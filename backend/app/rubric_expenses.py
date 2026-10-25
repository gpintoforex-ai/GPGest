"""Despesas por rubrica: compras do ERP e compras locais confirmadas, pela data do documento.

A rubrica de uma compra do ERP é, por esta ordem: a atribuída localmente ao documento
(`ERPDocumentRubric`, que pode repartir o valor) ou a do fornecedor (pelo NIF). O ERP não é
alterado. As compras locais usam a repartição das suas linhas. Cada documento conta uma vez:
uma compra local ligada a um documento ERP é contada pelo ERP.
"""

import json
from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .access import authorize, cents, current, record, scope
from .db import db_session
from .invoice_evidence import nif_key
from .models import (
    BusinessEntity,
    Category,
    DocumentERPLink,
    DocumentImport,
    ERPDocument,
    ERPDocumentRubric,
    ProcurementDocument,
    Purchase,
    Store,
)
from .schemas import Strict
from .table_rules import ensure_usable

router = APIRouter()


class Share(Strict):
    category_id: str = Field(max_length=36)
    amount: str = Field(max_length=30)


class RubricInput(Strict):
    # Vazio repõe a rubrica do fornecedor.
    allocations: list[Share] = Field(default_factory=list, max_length=20)


def supplier_rubrics(db):
    """Rubrica do fornecedor por NIF; um NIF com rubricas diferentes fica por classificar."""
    found = defaultdict(set)
    for entity in db.scalars(select(BusinessEntity).where(BusinessEntity.removed.is_(False), BusinessEntity.category_id.is_not(None))):
        if nif_key(entity.tax_number):
            found[nif_key(entity.tax_number)].add(entity.category_id)
    return {nif: next(iter(ids)) for nif, ids in found.items() if len(ids) == 1}


def erp_shares(db, documents, by_supplier=None):
    """[(rubrica, cêntimos com sinal)] e a origem da rubrica, por documento ERP."""
    by_supplier = supplier_rubrics(db) if by_supplier is None else by_supplier
    overrides = defaultdict(list)
    if documents:
        for row in db.scalars(select(ERPDocumentRubric).where(ERPDocumentRubric.erp_document_id.in_([d.id for d in documents]))):
            overrides[row.erp_document_id].append(row)
    result = {}
    sign = lambda doc: -1 if doc.amount_cents < 0 else 1  # noqa: E731
    for doc in documents:
        if overrides[doc.id]:
            result[doc.id] = ([(row.category_id, sign(doc) * row.amount_cents) for row in sorted(overrides[doc.id], key=lambda r: -r.amount_cents)], "documento")
        elif nif_key(doc.supplier_nif) in by_supplier:
            result[doc.id] = ([(by_supplier[nif_key(doc.supplier_nif)], doc.amount_cents)], "fornecedor")
        else:
            result[doc.id] = ([(None, doc.amount_cents)], "")
    return result


def erp_rubric_labels(db, enterprise, items):
    """Rubricas a mostrar nos saldos por pagar do ERP: [(id do documento ERP, NIF)] -> rótulos."""
    names = {c.id: c.name for c in db.scalars(select(Category))}
    by_supplier = supplier_rubrics(db)
    ids = [external for external, _ in items if external]
    docs = {d.external_id: d for d in db.scalars(select(ERPDocument).where(ERPDocument.enterprise_id == enterprise, ERPDocument.external_id.in_(ids)))} if ids else {}
    shares = erp_shares(db, list(docs.values()), by_supplier)
    labels = []
    for external, nif in items:
        if external in docs:
            split, _ = shares[docs[external].id]
            labels.append(" / ".join(names[c] for c, _ in split if c in names) or None)
        else:
            labels.append(names.get(by_supplier.get(nif_key(nif or ""))))
    return labels


@router.get("/api/rubric-expenses")
def rubric_expenses(start: date, end: date, store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    if start > end or (end - start).days > 366:
        raise HTTPException(422, "Selecione um período de até um ano.")
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        stores = [store_id] if store_id in stores else []
    names = {c.id: c.name for c in db.scalars(select(Category))}
    store_names = {s.id: s.name for s in db.scalars(select(Store))}
    erp_docs = list(db.scalars(select(ERPDocument).where(
        ERPDocument.store_id.in_(stores), ERPDocument.document_date >= str(start), ERPDocument.document_date <= str(end),
        ERPDocument.cancelled.is_(False)).order_by(ERPDocument.document_date)))
    shares = erp_shares(db, erp_docs)
    documents = []
    for doc in erp_docs:
        split, source = shares[doc.id]
        documents.append({"id": "erp:" + doc.id, "source": "ERP", "store": store_names.get(doc.store_id, ""), "document_date": doc.document_date,
                          "supplier_name": doc.supplier_name, "supplier_nif": doc.supplier_nif, "document": doc.document or doc.internal_document,
                          "document_type": doc.document_type, "amount_cents": doc.amount_cents, "rubric_source": source,
                          "allocations": [{"category_id": c, "category_name": names.get(c) if c else None, "amount_cents": a} for c, a in split]})
    # Compras locais confirmadas que não estejam ligadas ao ERP (essas contam pelo documento ERP).
    linked = set(db.scalars(select(DocumentERPLink.purchase_id).where(DocumentERPLink.undone_at.is_(None))))
    for row in db.scalars(select(ProcurementDocument).where(
            ProcurementDocument.kind.in_(("invoice", "credit")), ProcurementDocument.confirmed_at.is_not(None),
            ProcurementDocument.status != "cancelled", ProcurementDocument.store_id.in_(stores))):
        payload = json.loads(row.data)
        if not (str(start) <= payload.get("document_date", "") <= str(end)) or payload.get("erp_id") or row.purchase_id in linked:
            continue
        sign = -1 if row.kind == "credit" else 1
        split = payload.get("allocations") or [{"category_id": payload.get("category_id"), "amount_cents": payload.get("total_cents", 0)}]
        snapshot = payload.get("supplier_snapshot", {})
        documents.append({"id": "proc:" + row.id, "source": "Compras", "store": store_names.get(row.store_id, ""), "document_date": payload["document_date"],
                          "supplier_name": snapshot.get("name", ""), "supplier_nif": snapshot.get("nif", ""), "document": payload.get("document", ""),
                          "document_type": "NC" if row.kind == "credit" else payload.get("document_type", "FT"),
                          "amount_cents": sign * int(payload.get("total_cents") or 0), "rubric_source": "linhas",
                          "allocations": [{"category_id": s["category_id"], "category_name": names.get(s["category_id"]) if s["category_id"] else None,
                                           "amount_cents": sign * int(s["amount_cents"])} for s in split]})
    totals = defaultdict(lambda: {"total_cents": 0, "documents": 0})
    for doc in documents:
        for share in doc["allocations"]:
            key = share["category_id"] or ""
            totals[key]["total_cents"] += share["amount_cents"]
            totals[key]["documents"] += 1
    rubrics = sorted(({"category_id": key or None, "category_name": names.get(key) if key else "Por classificar", **value} for key, value in totals.items()),
                     key=lambda item: (item["category_id"] is None, -item["total_cents"]))
    # Faturas comunicadas no e-Fatura ainda sem lançamento (Compras ou ERP): não entram nos totais acima.
    efatura_imports = set(db.scalars(select(DocumentImport.id).where(DocumentImport.source == "ficheiro_efatura")))
    launched = {doc.purchase_id or json.loads(doc.data).get("purchase_id") for doc in db.scalars(select(ProcurementDocument).where(ProcurementDocument.status != "cancelled"))}
    pending = [p for p in db.scalars(select(Purchase).where(Purchase.store_id.in_(stores), Purchase.document_date >= str(start), Purchase.document_date <= str(end), Purchase.amount_cents != 0))
               if (p.origin in ("ficheiro", "efatura") or p.import_id in efatura_imports) and p.id not in launched and p.id not in linked]
    return {"pending_efatura": {"count": len(pending), "total_cents": sum(p.amount_cents for p in pending)},
            "rubrics": rubrics, "documents": documents, "total_cents": sum(doc["amount_cents"] for doc in documents),
            "unclassified": sum(1 for doc in documents if any(share["category_id"] is None for share in doc["allocations"])),
            "erp_documents": len(erp_docs)}


@router.post("/api/rubric-expenses/erp/{erp_id}")
def set_erp_rubric(erp_id: str, data: RubricInput, user=Depends(current), db: Session = Depends(db_session)):
    """Atribui (ou repõe) a rubrica de uma compra do ERP; o ERP não é alterado."""
    doc = db.scalar(select(ERPDocument).where(ERPDocument.id == erp_id).with_for_update())
    if not doc:
        raise HTTPException(404, "Documento ERP indisponível.")
    authorize(user, db, doc.store_id, ["administrador", "gestor"])
    shares = {}
    for share in data.allocations:
        if not ensure_usable(db, Category, share.category_id):
            raise HTTPException(422, "Rubrica inválida.")
        shares[share.category_id] = shares.get(share.category_id, 0) + cents(share.amount)
    if any(value <= 0 for value in shares.values()):
        raise HTTPException(422, "Indique valores positivos em cada rubrica.")
    if shares and sum(shares.values()) != abs(doc.amount_cents):
        raise HTTPException(422, f"A repartição ({sum(shares.values()) / 100:.2f} €) tem de somar o total do documento ({abs(doc.amount_cents) / 100:.2f} €).")
    before = [{"category_id": r.category_id, "amount_cents": r.amount_cents} for r in db.scalars(select(ERPDocumentRubric).where(ERPDocumentRubric.erp_document_id == doc.id))]
    db.execute(delete(ERPDocumentRubric).where(ERPDocumentRubric.erp_document_id == doc.id))
    for category, amount in shares.items():
        db.add(ERPDocumentRubric(erp_document_id=doc.id, category_id=category, amount_cents=amount))
    record(db, user, "rubrica_compra_erp", doc.id, {"antes": before, "depois": [{"category_id": c, "amount_cents": a} for c, a in shares.items()]}, doc.store_id)
    db.commit()
    split, source = erp_shares(db, [doc])[doc.id]
    return {"rubric_source": source, "allocations": [{"category_id": c, "amount_cents": a} for c, a in split]}
