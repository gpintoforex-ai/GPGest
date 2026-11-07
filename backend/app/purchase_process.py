"""Percurso de uma compra: encomenda → receção → documento de compra → conferência → anexo → pagamento → ERP,
e devoluções / notas de crédito quando existem.

A partir de qualquer documento de compras, junta os documentos ligados linha a linha (ProcurementLink, nos dois
sentidos, incluindo as outras receções da mesma encomenda) e o lado financeiro de cada um: compra, movimento
previsto, pagamentos conciliados e associação ao ERP. Só consulta; não altera documentos nem cria movimentos.
"""

import json

from fastapi import Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import inventory as stock
from .db import db_session
from .models import (
    DocumentERPLink,
    ERPDocument,
    Movement,
    PlannedMovement,
    PlannedSettlement,
    Purchase,
)
from .models import (
    ProcurementDocument as Doc,
)
from .models import (
    ProcurementLink as Link,
)
from .procurement import document_view, fail, paid_total

KINDS = {"order": "Encomenda", "receipt": "Receção", "invoice": "Fatura", "return": "Devolução", "credit": "Nota de crédito"}
ORDER = list(KINDS)
LIMIT = 200  # documentos por percurso; protege a consulta de cadeias anómalas


def connected(db: Session, start: str, stores: list[str]) -> list[Doc]:
    """Documentos ligados ao inicial (em qualquer sentido), só nas lojas autorizadas."""
    seen, frontier = {start}, {start}
    while frontier and len(seen) < LIMIT:
        pairs = db.execute(select(Link.document_id, Link.source_id)
                           .where(or_(Link.document_id.in_(frontier), Link.source_id.in_(frontier)))).all()
        frontier = {identifier for pair in pairs for identifier in pair} - seen
        seen |= frontier
    rows = db.scalars(select(Doc).where(Doc.id.in_(seen), Doc.store_id.in_(stores)))
    return sorted(rows, key=lambda d: (ORDER.index(d.kind) if d.kind in ORDER else 9,
                                       json.loads(d.data).get("document_date", ""), d.created_at))


def financial(db: Session, purchase: Purchase, cost_visible: bool) -> dict:
    planned = db.scalar(select(PlannedMovement).where(PlannedMovement.purchase_id == purchase.id))
    settled = None
    if planned:
        settlement = db.scalar(select(PlannedSettlement).where(PlannedSettlement.planned_id == planned.id).limit(1))
        movement = db.get(Movement, settlement.movement_id) if settlement else None
        settled = movement.occurred_at.date().isoformat() if movement else None
    link = db.scalar(select(DocumentERPLink).where(DocumentERPLink.purchase_id == purchase.id,
                                                   DocumentERPLink.undone_at.is_(None)))
    erp = db.get(ERPDocument, link.erp_id) if link else None
    paid = paid_total(db, purchase.id)
    total = abs(purchase.amount_cents)
    result = {
        "id": purchase.id, "document_type": purchase.document_type, "document": purchase.document,
        "document_date": purchase.document_date, "checked": purchase.validated,
        "payment_status": "paid" if paid >= total else "partial" if paid else "unpaid",
        "planned": {"due_date": planned.due_date, "settled_on": settled} if planned else None,
        "erp": {"document": erp.document, "internal_document": erp.internal_document, "document_date": erp.document_date,
                "cancelled": erp.cancelled} if erp else None,
    }
    if cost_visible:
        result.update(amount_cents=purchase.amount_cents, paid_cents=paid, remaining_cents=max(0, total - paid),
                      planned_amount_cents=planned.amount_cents if planned else None)
    return result


def step(key, label, state, detail):
    """state: done | partial | pending | missing (etapa sem documento) | none (não se aplica)."""
    return {"key": key, "label": label, "state": state, "detail": detail}


def summary(views: list[dict], finances: list[dict]) -> list[dict]:
    by_kind = {k: [v for v in views if v["kind"] == k and v["status"] != "cancelled"] for k in KINDS}
    confirmed = {k: [v for v in rows if v.get("confirmed_at")] for k, rows in by_kind.items()}
    steps = []
    orders = confirmed["order"]
    steps.append(step("order", "Encomenda", "done" if orders else "partial" if by_kind["order"] else "missing",
                      f"{len(orders)} confirmada(s)" if orders else "Rascunho por confirmar" if by_kind["order"]
                      else "Receção sem encomenda"))
    receipts = confirmed["receipt"]  # uma fatura direta de mercadoria cria a sua receção explícita
    pending_goods = any(v["status"] != "received" for v in orders)
    steps.append(step("receipt", "Receção", "partial" if receipts and pending_goods else "done" if receipts else "pending",
                      ("Receção parcial: há quantidades por receber" if pending_goods else f"{len(receipts)} receção(ões)")
                      if receipts else "Mercadoria por receber"))
    to_invoice = [v for v in confirmed["receipt"] if any(float(line.get("pending") or 0) > 0 for line in v["lines"])]
    steps.append(step("invoice", "Documento de compra",
                      "partial" if finances and to_invoice else "done" if finances else "pending",
                      ("Há quantidades recebidas por faturar" if to_invoice else
                       ", ".join(f"{f['document_type']} {f['document']}" for f in finances[:3]))
                      if finances else "Sem fatura registada"))
    if finances:
        checked = [f for f in finances if f["checked"]]
        steps.append(step("check", "Conferência", "done" if len(checked) == len(finances) else "partial" if checked
                          else "pending", "Conferido" if len(checked) == len(finances) else "Por conferir"))
        missing = [v for v in views if v.get("purchase_id") and v.get("attachment_status") == "missing"]
        steps.append(step("attachment", "Anexo", "pending" if missing else "done",
                          "Falta o documento digital" if missing else "Documento digital associado"))
        states = {f["payment_status"] for f in finances}
        due = sorted(f["planned"]["due_date"] for f in finances if f["planned"] and f["payment_status"] != "paid")
        steps.append(step("payment", "Pagamento", "done" if states == {"paid"} else "partial" if "paid" in states
                          or "partial" in states else "pending",
                          "Pago" if states == {"paid"} else ("Por pagar" + (f" · vence a {due[0]}" if due else ""))))
        linked = [f for f in finances if f["erp"]]
        steps.append(step("erp", "ERP", "done" if len(linked) == len(finances) else "partial" if linked else "pending",
                          "Associado ao ERP" if len(linked) == len(finances) else "Sem associação ERP"))
    if confirmed["return"]:
        uncredited = [v for v in confirmed["return"] if any(float(line.get("pending") or 0) > 0 for line in v["lines"])]
        steps.append(step("return", "Devolução e nota de crédito", "pending" if uncredited else "done",
                          "Devolução sem nota de crédito" if uncredited else "Devolução creditada"))
    return steps


def process(db: Session, user, row: Doc, stores: list[str]) -> dict:
    cost_visible = stock.permissions(user, stock.settings(db))["view_cost"]
    rows = connected(db, row.id, stores)
    views, finances, seen_purchases = [], [], set()
    for doc in rows:
        view = document_view(db, doc, cost_visible)
        purchase_ids = [doc.purchase_id] if doc.purchase_id else []
        purchase_ids += json.loads(doc.data).get("credit_note_purchase_ids") or []
        doc_finances = []
        for purchase_id in purchase_ids:
            purchase = db.get(Purchase, purchase_id)
            if purchase and purchase.id not in seen_purchases and purchase.store_id in stores:
                seen_purchases.add(purchase.id)
                doc_finances.append(financial(db, purchase, cost_visible))
        finances += [f for f in doc_finances if f["document_type"] != "NC"]
        item = {"id": doc.id, "kind": doc.kind, "label": KINDS.get(doc.kind, doc.kind), "document": view.get("document") or "",
                "document_date": view.get("document_date"), "status": view["status"], "confirmed_at": view.get("confirmed_at"),
                "current": doc.id == row.id, "purchase_id": doc.purchase_id, "financial": doc_finances,
                "attachment_status": view["attachment_status"],
                "lines": [{"description": line.get("description", ""), "quantity": line.get("quantity"),
                           "received": line.get("received"), "invoiced": line.get("invoiced"),
                           "returned": line.get("returned"), "credited": line.get("credited"), "pending": line.get("pending"),
                           "nature": line.get("nature")} for line in view["lines"]]}
        if cost_visible:
            item["total_cents"] = view.get("total_cents")
        views.append(item)
    return {"document_id": row.id, "documents": views, "steps": summary(views, finances), "cost_visible": cost_visible,
            "truncated": len(rows) >= LIMIT}


def register_routes(app, current, authorize, scope):
    @app.get("/api/procurement/{identifier}/process")
    def purchase_process(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        row = db.get(Doc, identifier)
        if not row:
            fail("Documento não encontrado nesta empresa.", 404)
        authorize(user, db, row.store_id)
        return process(db, user, row, list(scope(user, db)))
