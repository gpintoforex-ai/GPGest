"""Rastreabilidade das operações: do documento original à liquidação, só de leitura.

Junta as ligações que já existem em cada módulo, sem as duplicar nem criar registos:
- e-Fatura: compras sincronizadas da AT ou importadas de um ficheiro e-Fatura;
- PDF: anexos da compra (próprios, Drive, digitalizações) e documentos do arquivo ligados;
- ERP: a mesma regra de identidade e divergências da Conferência documental (`documents.projection`);
- rubricas: a mesma repartição dos mapas de gestão (`management_maps.purchase_documents`);
- vencimento: movimento previsto da compra e respetiva liquidação;
- pagamentos e banco: os estados calculados pela Conferência documental.

Cada ligação é classificada em: confirmada, provável, várias possibilidades, em falta, divergência
(ou não aplicável). Nada aqui confirma, cria ou altera registos.
"""

from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, scope
from .cash_sheet import day
from .db import db_session
from .invoice_evidence import nif_key
from .management_maps import purchase_documents
from .models import (
    ArchiveDocument,
    ArchiveLink,
    Category,
    DocumentERPLink,
    DocumentImport,
    ERPDocument,
    ERPDocumentRubric,
    InvoiceEvidenceValidation,
    PlannedMovement,
    PlannedSettlement,
    ProcurementDocument,
    Purchase,
    PurchaseRubric,
    Store,
    now,
)
from .rubric_expenses import erp_shares, supplier_rubrics

router = APIRouter()
ROLES = ["administrador", "gestor"]
STATES = {"confirmada": "Confirmada", "provavel": "Provável", "varias": "Várias possibilidades", "em_falta": "Em falta",
          "divergencia": "Divergência", "nao_aplicavel": "Não aplicável"}
DIMENSIONS = {"efatura": "e-Fatura", "pdf": "PDF", "erp": "ERP", "rubrica": "Rubrica", "pagamento": "Pagamento", "banco": "Banco"}
EFATURA_ORIGINS = {"efatura", "ficheiro"}
ORIGINS = {"efatura": "e-Fatura (AT)", "ficheiro": "Ficheiro e-Fatura", "manual": "Registo manual", "digitalizacao": "Digitalização",
           "rececao": "Receção de mercadoria"}


def today():
    return date.fromisoformat(day(now()))


def fail(message, status=422):
    raise HTTPException(status, message)


def integrated(db):
    from .operating_modes import policy
    return policy(db)["mode"] == "integrated"


def stores_for(user, db, store_id):
    stores = scope(user, db)
    if store_id:
        if store_id not in stores:
            fail("Estabelecimento sem acesso.", 403)
        return [store_id]
    return stores


def dim(state, label, **extra):
    return {"state": state, "state_label": STATES[state], "label": label, **extra}


# --- Classificação de cada ligação -----------------------------------------------------------------

def classify_efatura(purchase, efatura_import):
    if purchase.origin in EFATURA_ORIGINS or efatura_import:
        return dim("confirmada", ORIGINS.get(purchase.origin, "Ficheiro e-Fatura") if purchase.origin in EFATURA_ORIGINS else "Visto num ficheiro e-Fatura")
    return dim("em_falta", "Ainda não visto no e-Fatura", note="Registado por " + ORIGINS.get(purchase.origin, purchase.origin).lower())


def classify_pdf(attachments, validated, links, documents, total):
    """Anexo associado pelo utilizador ou relação confirmada no arquivo: confirmada; propostas: prováveis."""
    confirmed = [link for link in links if link.status == "confirmada"]
    proposed = [link for link in links if link.status == "proposta"]
    for link in confirmed:
        doc = documents.get(link.document_id)
        if doc and doc.total_cents is not None and total is not None and abs(doc.total_cents) != abs(total) and link.amount_cents is None:
            return dim("divergencia", "Total do PDF diferente do documento", document_id=doc.id)
    if attachments or confirmed or validated:
        count = len(attachments) + len(confirmed)
        return dim("confirmada", f"{count} ficheiro(s) associado(s)" + (" · validado" if validated else ""))
    if len(proposed) == 1:
        return dim("provavel", "Proposta do arquivo por confirmar", document_id=proposed[0].document_id)
    if proposed:
        return dim("varias", f"{len(proposed)} propostas do arquivo")
    return dim("em_falta", "Sem PDF associado")


def classify_erp(erp_status, candidates, is_integrated):
    available = [c for c in candidates if c.get("linked") or not c.get("unavailable")]
    if erp_status == "confirmada":
        return dim("confirmada", "Ligado ao documento do ERP")
    if erp_status == "divergencias":
        linked = next((c for c in candidates if c.get("linked")), None) or next((c for c in candidates if c.get("differences")), {})
        return dim("divergencia", "Diferenças: " + ", ".join(linked.get("differences", [])) if linked else "Diferenças com o ERP")
    if erp_status == "sugerida":
        return dim("varias" if len(available) > 1 else "provavel", f"{len(available)} documento(s) do ERP possível(eis)" if len(available) > 1 else "Documento do ERP proposto")
    if not is_integrated:
        return dim("nao_aplicavel", "Modo local: sem ERP")
    return dim("em_falta", "Sem documento no ERP")


def classify_rubric(source, shares, names):
    unclassified = sum(abs(base) for category, base, _ in shares if not category)
    labels = ", ".join(dict.fromkeys(names.get(category, "Por classificar") for category, _, _ in shares if category))
    if not shares or unclassified and unclassified == sum(abs(base) for _, base, _ in shares):
        return dim("em_falta", "Por classificar")
    if unclassified:
        return dim("em_falta", f"Parcialmente classificada ({labels})")
    if source == "fornecedor":
        return dim("provavel", f"{labels} · rubrica do fornecedor")
    return dim("confirmada", labels + (" · linhas da compra" if source == "linhas" else " · definida no documento"))


def classify_payment(projection, planned, reference):
    status = projection["payment_status"]
    if status == "pago":
        return dim("confirmada", "Pago")
    overdue = [p for p in planned if not p["settled"] and p["due_date"] and p["due_date"] < reference.isoformat()]
    label = ("Parcialmente pago · " if status == "parcial" else "Por pagar") + (f" · vencido desde {overdue[0]['due_date']}" if overdue else "")
    return dim("provavel" if status == "parcial" else "em_falta", label, overdue=bool(overdue))


def classify_bank(projection):
    if projection["payment_status"] == "por_pagar" and not projection["payments"]:
        return dim("nao_aplicavel", "Sem pagamento a reconciliar")
    status = projection["bank_status"]
    if status == "reconciliado":
        return dim("confirmada", "Reconciliado com o banco")
    if status == "parcial":
        return dim("provavel", "Parcialmente reconciliado")
    return dim("em_falta", "Pagamento por reconciliar")


def attention(dims):
    """Só o que precisa de intervenção: divergências, várias possibilidades, faltas relevantes e vencidos."""
    items = []
    for key, value in dims.items():
        state = value["state"]
        if state in ("divergencia", "varias") or state == "provavel" and key in ("erp", "pdf", "rubrica"):
            items.append(f"{DIMENSIONS[key]}: {value['label']}")
        elif state == "em_falta" and key in ("pdf", "erp", "rubrica", "banco"):
            items.append(f"{DIMENSIONS[key]}: {value['label']}")
        elif key == "pagamento" and value.get("overdue"):
            items.append(f"Pagamento: {value['label']}")
    return items


# --- Dados agregados (uma leitura por tipo, sem N+1) -----------------------------------------------

class Context:
    def __init__(self, db, purchases, erp_documents=()):
        ids = [p.id for p in purchases]
        erp_ids = [d.id for d in erp_documents]
        self.names = {c.id: c.name for c in db.scalars(select(Category))}
        self.stores = {s.id: s.name for s in db.scalars(select(Store))}
        self.efatura_imports = set(db.scalars(select(DocumentImport.id).where(DocumentImport.source == "ficheiro_efatura")))
        self.validated = set(db.scalars(select(InvoiceEvidenceValidation.purchase_id).where(InvoiceEvidenceValidation.purchase_id.in_(ids)))) if ids else set()
        self.links = defaultdict(list)
        targets = [("purchase", i) for i in ids] + [("erp_document", i) for i in erp_ids]
        if targets:
            for link in db.scalars(select(ArchiveLink).where(ArchiveLink.target_kind.in_(("purchase", "erp_document")),
                                                             ArchiveLink.target_id.in_([t for _, t in targets]),
                                                             ArchiveLink.status.in_(("confirmada", "proposta")))):
                self.links[(link.target_kind, link.target_id)].append(link)
        doc_ids = {link.document_id for links in self.links.values() for link in links}
        self.documents = {d.id: d for d in db.scalars(select(ArchiveDocument).where(ArchiveDocument.id.in_(doc_ids)))} if doc_ids else {}
        self.planned = defaultdict(list)
        if ids:
            rows = list(db.scalars(select(PlannedMovement).where(PlannedMovement.purchase_id.in_(ids))))
            settled = set(db.scalars(select(PlannedSettlement.planned_id).where(PlannedSettlement.planned_id.in_([r.id for r in rows])))) if rows else set()
            for row in rows:
                self.planned[row.purchase_id].append({"id": row.id, "due_date": row.due_date, "amount_cents": row.amount_cents, "settled": row.id in settled})
        self.procurement = set(db.scalars(select(ProcurementDocument.purchase_id).where(
            ProcurementDocument.purchase_id.in_(ids), ProcurementDocument.kind.in_(("invoice", "credit")),
            ProcurementDocument.confirmed_at.is_not(None), ProcurementDocument.status != "cancelled"))) if ids else set()
        self.erp_link = {link.purchase_id: link.erp_id for link in db.scalars(select(DocumentERPLink).where(
            DocumentERPLink.purchase_id.in_(ids), DocumentERPLink.undone_at.is_(None)))} if ids else {}
        linked_erp = set(self.erp_link.values())
        self.erp_rubrics = set(db.scalars(select(ERPDocumentRubric.erp_document_id).where(ERPDocumentRubric.erp_document_id.in_(linked_erp)))) if linked_erp else set()
        self.by_supplier = supplier_rubrics(db)
        self.classified = set(db.scalars(select(PurchaseRubric.purchase_id).where(PurchaseRubric.purchase_id.in_(ids)))) if ids else set()

    def rubric_source(self, purchase):
        if purchase.id in self.procurement:
            return "linhas"
        if self.erp_link.get(purchase.id) in self.erp_rubrics or purchase.id in self.classified:
            return "documento"
        return "fornecedor" if nif_key(purchase.supplier_nif) in self.by_supplier else ""


def shares_of(db, stores, start, end):
    """{id: [(rubrica, base, IVA)]} com a mesma repartição dos mapas de gestão (compras e ERP sem compra)."""
    return {identifier: shares for _, identifier, shares in purchase_documents(db, stores, start, end)}


def purchase_row(request, db, purchase, ctx, shares, is_integrated, reference, projection=None, context=None):
    projection = projection or request.app.state.document_projection(db, purchase, context=context)
    planned = ctx.planned.get(purchase.id, [])
    links = ctx.links.get(("purchase", purchase.id), [])
    dims = {
        "efatura": classify_efatura(purchase, purchase.import_id in ctx.efatura_imports),
        "pdf": classify_pdf(projection["attachments"], purchase.id in ctx.validated, links, ctx.documents, purchase.amount_cents),
        "erp": classify_erp(projection["erp_status"], projection["erp_candidates"], is_integrated),
        "rubrica": classify_rubric(ctx.rubric_source(purchase), shares.get(purchase.id, []), ctx.names),
        "pagamento": classify_payment(projection, planned, reference),
        "banco": classify_bank(projection),
    }
    return {
        "kind": "purchase", "id": purchase.id, "store_id": purchase.store_id, "store": ctx.stores.get(purchase.store_id, ""),
        "supplier_nif": purchase.supplier_nif, "supplier_name": purchase.supplier_name, "document_type": purchase.document_type,
        "document": purchase.document, "document_date": purchase.document_date, "amount_cents": purchase.amount_cents,
        "base_cents": purchase.base_cents, "tax_cents": purchase.tax_cents, "origin": purchase.origin, "origin_label": ORIGINS.get(purchase.origin, purchase.origin),
        "dimensions": dims, "attention": attention(dims), "paid_cents": projection["paid_cents"], "remaining_cents": projection["remaining_cents"],
    }, projection


def erp_row(db, erp, ctx, shares, reference):
    split, source = erp_shares(db, [erp], ctx.by_supplier)[erp.id]
    links = ctx.links.get(("erp_document", erp.id), [])
    dims = {
        "efatura": dim("em_falta", "Sem registo e-Fatura correspondente", note="Documento só no ERP"),
        "pdf": classify_pdf([], False, links, ctx.documents, erp.amount_cents),
        "erp": dim("confirmada", "Documento do ERP" + (" (anulado)" if erp.cancelled else "")),
        "rubrica": classify_rubric("documento" if source == "documento" else source, shares.get("erp:" + erp.id, [(c, a, 0) for c, a in split]), ctx.names),
        "pagamento": dim("nao_aplicavel", "Valores em aberto no ERP"),
        "banco": dim("nao_aplicavel", "Ver reconciliação"),
    }
    return {
        "kind": "erp", "id": erp.id, "store_id": erp.store_id, "store": ctx.stores.get(erp.store_id, ""), "supplier_nif": erp.supplier_nif,
        "supplier_name": erp.supplier_name, "document_type": erp.document_type, "document": erp.document, "document_date": erp.document_date,
        "amount_cents": erp.amount_cents, "base_cents": erp.base_cents, "tax_cents": erp.tax_cents, "origin": "erp", "origin_label": "ERP",
        "dimensions": dims, "attention": attention(dims), "paid_cents": None, "remaining_cents": None,
    }


# --- Rotas -----------------------------------------------------------------------------------------

@router.get("/api/operations")
def operations(request: Request, start: date | None = None, end: date | None = None, store_id: str = "", attention_only: bool = False,
               user=Depends(current), db: Session = Depends(db_session)):
    """Operações de compra do período com o estado de cada ligação; as que precisam de intervenção primeiro."""
    from .document_projection import DocumentContext
    authorize(user, db, roles=ROLES)
    reference = today()
    end = end or reference
    start = start or end - timedelta(days=89)
    if start > end or (end - start).days > 400:
        fail("Selecione um período de até um ano.")
    stores = stores_for(user, db, store_id)
    purchases = list(db.scalars(select(Purchase).where(Purchase.store_id.in_(stores), Purchase.document_date >= start.isoformat(),
                                                       Purchase.document_date <= end.isoformat(), Purchase.origin != "efatura_testes")
                                .order_by(Purchase.document_date.desc())))
    shares = shares_of(db, stores, start, end)
    erp_ids = [key.removeprefix("erp:") for key in shares if key.startswith("erp:")]
    erp_documents = list(db.scalars(select(ERPDocument).where(ERPDocument.id.in_(erp_ids)))) if erp_ids else []
    ctx = Context(db, purchases, erp_documents)
    is_integrated = integrated(db)
    context = DocumentContext(db, purchases)
    rows = [purchase_row(request, db, p, ctx, shares, is_integrated, reference, context=context)[0] for p in purchases]
    rows += [erp_row(db, e, ctx, shares, reference) for e in erp_documents]
    summary = {key: {state: sum(1 for r in rows if r["dimensions"][key]["state"] == state) for state in STATES} for key in DIMENSIONS}
    rows.sort(key=lambda r: r["document_date"], reverse=True)
    rows.sort(key=lambda r: not r["attention"])  # Estável: as que precisam de intervenção primeiro, mais recentes primeiro.
    needing = sum(1 for r in rows if r["attention"])
    if attention_only:
        rows = [r for r in rows if r["attention"]]
    return {
        "start": start.isoformat(), "end": end.isoformat(), "mode": "integrated" if is_integrated else "local", "total": len(purchases) + len(erp_documents),
        "attention": needing, "summary": summary, "rows": rows, "states": STATES, "dimensions": DIMENSIONS,
        "notes": ["Só leitura: as ligações confirmam-se nos módulos de origem (Conferência documental, Arquivo, Reconciliação).",
                  "Rubricas com a mesma repartição dos mapas de gestão; ERP com a mesma regra de identidade da Conferência documental."],
    }


def comparison_rows(purchase, erp, pdf):
    """Campos lado a lado (documento, ERP, PDF) com a indicação das diferenças."""
    def norm_number(value):
        from .document_identity import number_key
        return number_key(value or "")
    fields = [
        ("supplier_nif", "NIF do fornecedor", lambda d: nif_key(d.get("supplier_nif") or "")),
        ("supplier_name", "Fornecedor", None),
        ("document_type", "Tipo", lambda d: (d.get("document_type") or "").upper()),
        ("document", "Número", lambda d: norm_number(d.get("document"))),
        ("document_date", "Data", lambda d: d.get("document_date") or ""),
        ("base_cents", "Base (sem IVA)", lambda d: abs(d["base_cents"]) if d.get("base_cents") is not None else None),
        ("tax_cents", "IVA", lambda d: abs(d["tax_cents"]) if d.get("tax_cents") is not None else None),
        ("amount_cents", "Total", lambda d: abs(d["amount_cents"]) if d.get("amount_cents") is not None else None),
    ]
    sources = {"documento": purchase, "erp": erp, "pdf": pdf}
    rows = []
    for key, label, normal in fields:
        values = {name: (source or {}).get(key) for name, source in sources.items()}
        compared = {normal(source) for source in sources.values() if source and normal and normal(source) not in (None, "")}
        rows.append({"key": key, "label": label, "values": values, "different": bool(normal) and len(compared) > 1})
    return rows


@router.get("/api/operations/purchase/{identifier}")
def purchase_detail(identifier: str, request: Request, user=Depends(current), db: Session = Depends(db_session)):
    """Ficha da operação: documento, PDF, ERP, rubricas, vencimento, pagamentos, banco e histórico."""
    authorize(user, db, roles=ROLES)
    purchase = db.get(Purchase, identifier)
    if not purchase:
        fail("Documento não encontrado.", 404)
    stores_for(user, db, purchase.store_id)
    projection = request.app.state.document_projection(db, purchase, True)
    stamp = date.fromisoformat(purchase.document_date)
    shares = shares_of(db, [purchase.store_id], stamp, stamp)
    ctx = Context(db, [purchase])
    row, _ = purchase_row(request, db, purchase, ctx, shares, integrated(db), today(), projection=projection)
    linked = next((c for c in projection["erp_candidates"] if c.get("linked")), None)
    best = linked or (projection["erp_candidates"][0] if len(projection["erp_candidates"]) == 1 else None)
    links = sorted(ctx.links.get(("purchase", purchase.id), []), key=lambda link: (link.status != "confirmada", -link.score))
    pdf_doc = ctx.documents.get(links[0].document_id) if links else None
    pdf = {"supplier_nif": pdf_doc.entity_nif, "supplier_name": pdf_doc.entity_name, "document_type": pdf_doc.document_code,
           "document": pdf_doc.document_number, "document_date": pdf_doc.issue_date, "base_cents": pdf_doc.base_cents,
           "tax_cents": pdf_doc.tax_cents, "amount_cents": pdf_doc.total_cents} if pdf_doc else None
    files = [{"id": a["id"], "name": a["name"], "mime": a.get("mime"), "source": a.get("source", "anexo"),
              "view_url": a.get("view_url") or f"/api/documents/attachments/{a['id']}/view"} for a in projection["attachments"]]
    files += [{"id": "archive:" + link.document_id, "name": ctx.documents[link.document_id].original_name, "mime": ctx.documents[link.document_id].mime,
               "source": "arquivo", "status": link.status, "score": link.score, "view_url": f"/api/archive/documents/{link.document_id}/file"}
              for link in links if link.document_id in ctx.documents]
    return {
        **row,
        "comparison": comparison_rows(row, best and {k: best.get(k) for k in ("supplier_nif", "supplier_name", "document_type", "document", "document_date", "base_cents", "tax_cents", "amount_cents")}, pdf),
        "erp_candidates": projection["erp_candidates"], "files": files,
        "rubrics": [{"category_id": c or None, "category_name": ctx.names.get(c, "Por classificar") if c else "Por classificar", "base_cents": b, "tax_cents": t}
                    for c, b, t in shares.get(purchase.id, [])],
        "rubric_source": ctx.rubric_source(purchase) or None,
        "planned": ctx.planned.get(purchase.id, []), "payments": projection["payments"], "procurement_documents": projection["procurement_documents"],
        "history": projection.get("history", [])[:40],
        "links": {"conference": "Conferência documental", "archive": "Arquivo — Visão geral", "reconciliation": "Reconciliação automática"},
    }


@router.get("/api/operations/erp/{identifier}")
def erp_detail(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    erp = db.get(ERPDocument, identifier)
    if not erp:
        fail("Documento do ERP não encontrado.", 404)
    stores_for(user, db, erp.store_id)
    ctx = Context(db, [], [erp])
    stamp = date.fromisoformat(erp.document_date)
    shares = shares_of(db, [erp.store_id], stamp, stamp)
    row = erp_row(db, erp, ctx, shares, today())
    links = sorted(ctx.links.get(("erp_document", erp.id), []), key=lambda link: (link.status != "confirmada", -link.score))
    pdf_doc = ctx.documents.get(links[0].document_id) if links else None
    pdf = {"supplier_nif": pdf_doc.entity_nif, "supplier_name": pdf_doc.entity_name, "document_type": pdf_doc.document_code, "document": pdf_doc.document_number,
           "document_date": pdf_doc.issue_date, "base_cents": pdf_doc.base_cents, "tax_cents": pdf_doc.tax_cents, "amount_cents": pdf_doc.total_cents} if pdf_doc else None
    split = shares.get("erp:" + erp.id) or [(c, a, 0) for c, a in erp_shares(db, [erp], ctx.by_supplier)[erp.id][0]]
    return {
        **row, "comparison": comparison_rows({}, row, pdf), "erp_candidates": [],
        "files": [{"id": "archive:" + link.document_id, "name": ctx.documents[link.document_id].original_name, "mime": ctx.documents[link.document_id].mime,
                   "source": "arquivo", "status": link.status, "score": link.score, "view_url": f"/api/archive/documents/{link.document_id}/file"}
                  for link in links if link.document_id in ctx.documents],
        "rubrics": [{"category_id": c or None, "category_name": ctx.names.get(c, "Por classificar") if c else "Por classificar", "base_cents": b, "tax_cents": t} for c, b, t in split],
        "rubric_source": erp_shares(db, [erp], ctx.by_supplier)[erp.id][1] or None, "planned": [], "payments": [], "procurement_documents": [], "history": [],
        "links": {"conference": "Conferência documental", "archive": "Arquivo — Visão geral"},
    }

