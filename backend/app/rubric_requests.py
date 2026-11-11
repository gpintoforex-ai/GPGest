"""Pedidos de aprovação de alterações às rubricas: um gestor simula e pede; o administrador revê, volta a simular e
aprova (aplicando) ou recusa, sempre com motivo.

O pedido guarda as alterações pedidas e o resumo da simulação no momento do pedido. Nada muda até a aprovação: a
aprovação é a aplicação normal (`/api/rubric-nature/apply` ou `/api/rubric-nature/reclassify`) com `request_id`,
que volta a simular e verifica a impressão digital. Pedidos, recusas e cancelamentos ficam na auditoria.
"""

import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, record
from .db import db_session
from .models import Category, RubricRequest, User, now
from .rubric_nature import NATURES, Change, Reason
from .schemas import Strict

router = APIRouter()
STATUS_LABELS = {"pendente": "Por aprovar", "aprovado": "Aprovado", "recusado": "Recusado", "cancelado": "Cancelado"}


def fail(message, status=422):
    raise HTTPException(status, message)


def normal(kind, payload):
    """Forma comparável do pedido (ordem das rubricas e dos registos não interessa)."""
    if kind == "nature":
        return sorted((dict(change) for change in payload), key=lambda change: change["category_id"])
    return {"items": sorted(payload["items"]), "category_id": payload["category_id"]}


def settle(db, user, request_id, kind, payload, reason):
    """Marca o pedido como aprovado na mesma transação da aplicação (sem pedido, não faz nada)."""
    if not request_id:
        return None
    row = db.scalar(select(RubricRequest).where(RubricRequest.id == request_id).with_for_update())
    if not row or row.kind != kind:
        fail("Pedido de aprovação não encontrado.", 404)
    if row.status != "pendente":
        fail(f"Este pedido já está {STATUS_LABELS[row.status].lower()}.", 409)
    same = normal(kind, json.loads(row.payload)) == normal(kind, payload)
    row.status, row.decided_by, row.decided_at = "aprovado", user.id, now()
    row.decision_reason = reason if same else f"{reason} (aprovado com alterações ao pedido)"
    record(db, user, "rubrica_pedido_aprovado", row.id, {"kind": kind, "reason": reason, "as_requested": same})
    return row


class RequestInput(Strict):
    kind: Literal["nature", "reclassification"]
    changes: list[Change] = Field(default_factory=list, max_length=200)
    items: list[str] = Field(default_factory=list, max_length=200)
    category_id: str = Field(default="", max_length=36)
    reason: Reason

    @model_validator(mode="after")
    def complete(self):
        if self.kind == "nature" and not self.changes:
            raise ValueError("Indique as alterações à natureza das rubricas.")
        if self.kind == "reclassification" and (not self.items or not self.category_id):
            raise ValueError("Indique os pagamentos e a rubrica de destino.")
        return self


class Decision(Strict):
    reason: Reason


def describe(db, row, users, names):
    payload, summary = json.loads(row.payload), json.loads(row.summary or "{}")
    if row.kind == "nature":
        title = "Natureza: " + "; ".join(
            f"{names.get(c['category_id'], '?')} → {NATURES.get(c['nature'], c['nature'])}" for c in payload)
    else:
        title = (f"Reclassificar {len(payload['items'])} pagamento(s)"
                 f"{' (' + format(summary.get('total_cents', 0) / 100, '.2f').replace('.', ',') + ' €)' if summary.get('total_cents') else ''}"
                 f" para «{names.get(payload['category_id'], '?')}»")
    return {
        "id": row.id, "kind": row.kind, "status": row.status, "status_label": STATUS_LABELS[row.status], "title": title,
        "payload": payload, "summary": summary, "reason": row.reason, "requested_by": users.get(row.requested_by, ""),
        "mine": False, "created_at": row.created_at.isoformat(), "decided_by": users.get(row.decided_by or "", ""),
        "decided_at": row.decided_at.isoformat() if row.decided_at else None, "decision_reason": row.decision_reason,
    }


def pending(db):
    return list(db.scalars(select(RubricRequest).where(RubricRequest.status == "pendente").order_by(RubricRequest.created_at)))


@router.get("/api/rubric-nature/requests")
def listing(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    users = {u.id: u.name for u in db.scalars(select(User))}
    names = {c.id: c.name for c in db.scalars(select(Category))}
    since = now() - timedelta(days=90)
    rows = pending(db) + list(db.scalars(select(RubricRequest).where(
        RubricRequest.status != "pendente", RubricRequest.created_at >= since).order_by(RubricRequest.created_at.desc()).limit(30)))
    result = []
    for row in rows:
        item = describe(db, row, users, names)
        item["mine"] = row.requested_by == user.id
        result.append(item)
    return {"can_decide": user.role == "administrador", "requests": result}


@router.post("/api/rubric-nature/requests")
def create(data: RequestInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    if data.kind == "nature":
        from .rubric_nature import simulate, validated
        payload = [change.model_dump() for change in data.changes]
        result = simulate(db, user, validated(db, data.changes))
        summary = {"start": result["start"], "end": result["end"], "affected": result["affected"]["count"],
                   "warnings": [w["text"] for w in result["warnings"]],
                   "views": [{k: v[k] for k in ("key", "label", "before_cents", "after_cents", "difference_cents")} for v in result["views"]]}
    else:
        from .fiscal_payments import Selection, preview
        payload = {"items": data.items, "category_id": data.category_id}
        result = preview(db, user, Selection(**payload))
        summary = {"start": result["start"], "end": result["end"], "total_cents": result["total_cents"], "target": result["target"],
                   "views": [{k: v[k] for k in ("key", "label", "before_cents", "after_cents", "difference_cents")} for v in result["views"]]}
    for row in pending(db):
        if row.kind == data.kind and normal(row.kind, json.loads(row.payload)) == normal(data.kind, payload):
            fail("Já existe um pedido igual por aprovar.", 409)
    row = RubricRequest(kind=data.kind, payload=json.dumps(payload, ensure_ascii=False), summary=json.dumps(summary, ensure_ascii=False),
                        reason=data.reason, requested_by=user.id)
    db.add(row)
    db.flush()
    record(db, user, "rubrica_pedido_criado", row.id, {"kind": data.kind, "payload": payload, "reason": data.reason, "summary": summary})
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/api/rubric-nature/requests/{identifier}/reject")
def reject(identifier: str, data: Decision, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    row = db.scalar(select(RubricRequest).where(RubricRequest.id == identifier).with_for_update())
    if not row:
        fail("Pedido de aprovação não encontrado.", 404)
    if row.status != "pendente":
        fail(f"Este pedido já está {STATUS_LABELS[row.status].lower()}.", 409)
    row.status, row.decided_by, row.decided_at, row.decision_reason = "recusado", user.id, now(), data.reason
    record(db, user, "rubrica_pedido_recusado", row.id, {"kind": row.kind, "reason": data.reason})
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/api/rubric-nature/requests/{identifier}/cancel")
def cancel(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    row = db.scalar(select(RubricRequest).where(RubricRequest.id == identifier).with_for_update())
    if not row:
        fail("Pedido de aprovação não encontrado.", 404)
    if row.requested_by != user.id:
        fail("Só quem fez o pedido o pode cancelar.", 403)
    if row.status != "pendente":
        fail(f"Este pedido já está {STATUS_LABELS[row.status].lower()}.", 409)
    row.status, row.decided_by, row.decided_at, row.decision_reason = "cancelado", user.id, now(), "Cancelado por quem pediu."
    record(db, user, "rubrica_pedido_cancelado", row.id, {"kind": row.kind})
    db.commit()
    return {"id": row.id, "status": row.status}
