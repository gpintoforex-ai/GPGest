"""Retenções na fonte e descontos financeiros registados nas reconciliações: retenções por entregar ao Estado e
retidas por clientes; descontos de pronto pagamento obtidos (pagamentos) e concedidos (recebimentos).

Cada reconciliação confirmada com retenção (`reconciliations.withholding_cents`) conta no mês do movimento bancário:
- pagamento a fornecedor com retenção: valor a entregar ao Estado até ao dia 20 do mês seguinte (DMR / guia);
- recebimento de cliente com retenção: imposto retido pelo cliente, a deduzir no IRS/IRC.

O total a entregar pode ser usado como valor previsto da ocorrência da obrigação «Retenções na fonte» desse prazo
(Obrigações e Prazos), com auditoria; nada é pago nem lançado automaticamente.
"""

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .models import (
    BankTransaction,
    Obligation,
    ObligationOccurrence,
    Purchase,
    Reconciliation,
    ReconciliationLine,
)
from .schemas import Strict

router = APIRouter()
ROLES = ["administrador", "gestor"]
KIND = "Retenções na fonte"


def due_date(month):
    """Dia 20 do mês seguinte ao da retenção."""
    year, number = int(month[:4]), int(month[5:7])
    return date(year + (number == 12), number % 12 + 1, 20).isoformat()


def collect(db, stores, since=None):
    """{(tipo, mês): [linhas]} das reconciliações ativas com retenção."""
    groups = list(db.scalars(select(Reconciliation).where(
        (Reconciliation.withholding_cents > 0) | (Reconciliation.discount_cents > 0),
        Reconciliation.undone_at.is_(None), Reconciliation.store_id.in_(stores))))
    if not groups:
        return {}
    lines = defaultdict(list)
    for line in db.scalars(select(ReconciliationLine).where(ReconciliationLine.reconciliation_id.in_([g.id for g in groups]))):
        lines[line.reconciliation_id].append(line)
    result = defaultdict(list)
    for group in groups:
        banks = [db.get(BankTransaction, line.source_id) for line in lines[group.id] if line.source == "bank"]
        banks = [b for b in banks if b]
        if not banks:
            continue
        day = min(b.movement_date for b in banks)
        if since and day < since:
            continue
        paying = banks[0].amount_cents < 0
        documents = []
        for line in lines[group.id]:
            if line.source == "purchase" and (purchase := db.get(Purchase, line.source_id)):
                documents.append({"document": purchase.document, "entity": purchase.supplier_name, "nif": purchase.supplier_nif,
                                  "base_cents": abs(purchase.base_cents) if purchase.base_cents else None})
        item = {"reconciliation_id": group.id, "date": day, "bank": banks[0].description, "reason": group.reason, "documents": documents}
        if group.withholding_cents:
            result["a_entregar" if paying else "retida_por_clientes", day[:7]].append({**item, "withholding_cents": group.withholding_cents})
        if group.discount_cents:
            result["desconto_obtido" if paying else "desconto_concedido", day[:7]].append({**item, "withholding_cents": group.discount_cents})
    return result


def occurrences_by_due(db, stores):
    rows = db.execute(select(ObligationOccurrence, Obligation).join(Obligation, Obligation.id == ObligationOccurrence.obligation_id)
                      .where(Obligation.kind == KIND)).all()
    found = {}
    for occurrence, obligation in rows:
        if obligation.store_id and obligation.store_id not in stores:
            continue
        found.setdefault(occurrence.due_date, (occurrence, obligation))
    return found


@router.get("/api/withholdings")
def listing(since: date | None = None, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    stores = scope(user, db)
    data = collect(db, stores, since.isoformat() if since else None)
    occurrences = occurrences_by_due(db, stores)
    months = []
    for (kind, month), items in sorted(data.items(), key=lambda pair: (pair[0][1], pair[0][0]), reverse=True):
        total = sum(item["withholding_cents"] for item in items)
        entry = {"kind": kind, "month": month, "total_cents": total, "count": len(items),
                 "items": sorted(items, key=lambda item: item["date"])}
        if kind == "a_entregar":
            entry["due_date"] = due_date(month)
            match = occurrences.get(entry["due_date"])
            entry["occurrence"] = {
                "id": match[0].id, "title": match[1].title, "status": match[0].status, "expected_cents": match[0].expected_cents,
                "actual_cents": match[0].actual_cents, "revision": match[0].revision,
            } if match else None
        months.append(entry)
    return {"months": months, "has_obligation": bool(occurrences), "can_edit": True}


class Forecast(Strict):
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    occurrence_id: str = Field(min_length=1, max_length=36)
    revision: int = Field(ge=0)


@router.post("/api/withholdings/forecast")
def forecast(data: Forecast, user=Depends(current), db: Session = Depends(db_session)):
    """Usa o total retido do mês como valor previsto da ocorrência «Retenções na fonte» do prazo seguinte."""
    authorize(user, db, roles=ROLES)
    stores = scope(user, db)
    occurrence = db.get(ObligationOccurrence, data.occurrence_id, with_for_update=True)
    obligation = db.get(Obligation, occurrence.obligation_id) if occurrence else None
    if not occurrence or not obligation or obligation.kind != KIND or (obligation.store_id and obligation.store_id not in stores):
        raise HTTPException(404, "Ocorrência de retenções na fonte não encontrada.")
    if occurrence.due_date != due_date(data.month):
        raise HTTPException(422, "A ocorrência não corresponde ao prazo das retenções desse mês.")
    if occurrence.status != "pendente":
        raise HTTPException(409, "A ocorrência já não está pendente.")
    if occurrence.revision != data.revision:
        raise HTTPException(409, "A ocorrência mudou entretanto. Atualize a página.")
    total = sum(item["withholding_cents"] for item in collect(db, stores).get(("a_entregar", data.month), []))
    if not total:
        raise HTTPException(422, "Não há retenções registadas nesse mês.")
    before = occurrence.expected_cents
    occurrence.expected_cents, occurrence.revision = total, occurrence.revision + 1
    record(db, user, "retencoes_valor_previsto", occurrence.id, {"month": data.month, "before": before, "after": total},
           obligation.store_id)
    db.commit()
    return {"occurrence_id": occurrence.id, "expected_cents": total, "revision": occurrence.revision}
