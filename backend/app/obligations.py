"""Obrigações e prazos: regras, ocorrências, estados e painel.

- Uma obrigação guarda a regra (categoria, periodicidade, valor previsto, responsável, origem da data).
- As ocorrências são gravadas uma vez por data da regra (chave única), até 400 dias à frente, e ficam
  como histórico: concluir, cancelar ou marcar «não aplicável» não altera a regra nem as seguintes.
- Mudar a regra só substitui as ocorrências futuras ainda intocadas; as alteradas ou concluídas ficam.
- Uma obrigação nunca é dada como cumprida por existir um movimento financeiro: só por confirmação.
- Datas legais não são fixadas no código: cada obrigação indica a origem da data e se foi validada.
"""

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal as D
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import recurrence
from .access import authorize, current, record, scope
from .cash_sheet import day
from .db import db_session
from .models import (
    BusinessEntity,
    Category,
    Obligation,
    ObligationOccurrence,
    ObligationRead,
    User,
    now,
)
from .schemas import Strict

router = APIRouter()
ROLES = ["administrador", "gestor"]
CATEGORIES = {"fiscal": "Fiscal", "contrato": "Contrato", "veiculo": "Veículo", "inspecao": "Inspeção",
              "seguro": "Seguro", "licenca": "Licença", "outro": "Outro"}
DATE_SOURCES = {"manual": "Indicada manualmente", "regra_legal": "Regra legal", "calendario_oficial": "Calendário oficial",
                "contrato": "Contrato ou apólice", "documento": "Documento"}
STATES = {"agendado": "Agendado", "a_aproximar": "A aproximar-se", "urgente": "Urgente", "vencido": "Vencido",
          "concluido": "Concluído", "cancelado": "Cancelado", "nao_aplicavel": "Não aplicável"}
STORED = {"concluida": "concluido", "cancelada": "cancelado", "nao_aplicavel": "nao_aplicavel"}
AHEAD, BEHIND = 400, 400  # Dias de ocorrências gravadas para a frente e de histórico gerado para trás.


def today():
    return date.fromisoformat(day(now()))


class ObligationInput(Strict):
    revision: int = Field(default=0, ge=0)
    store_id: str | None = None
    category: Literal["fiscal", "contrato", "veiculo", "inspecao", "seguro", "licenca", "outro"]
    kind: str = Field(default="", max_length=60)
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)
    entity_id: str | None = None
    entity_name: str = Field(default="", max_length=200)
    category_id: str | None = None
    responsible_id: str | None = None
    periodicity: Literal["unica", "semanal", "mensal", "bimestral", "trimestral", "semestral", "anual", "personalizada"]
    every_months: int | None = Field(default=None, ge=1, le=120)
    start_date: date
    end_date: date | None = None
    alert_days: list[int] = Field(default_factory=lambda: [90, 60, 30, 15, 7, 0], max_length=10)
    amount_cents: int | None = Field(default=None, strict=True, ge=0, le=10**13)
    vat_rate: D | None = Field(default=None, ge=0, le=100, decimal_places=2)
    date_source: Literal["manual", "regra_legal", "calendario_oficial", "contrato", "documento"] = "manual"
    date_note: str = Field(default="", max_length=2000)
    date_validated: bool = True
    data: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self):
        self.title = self.title.strip()
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("A data de fim não pode ser anterior à data de início.")
        if self.periodicity == "personalizada" and not self.every_months:
            raise ValueError("Na periodicidade personalizada indique de quantos em quantos meses.")
        if any(not 0 <= value <= 365 for value in self.alert_days):
            raise ValueError("Os alertas vão de 0 a 365 dias antes.")
        self.alert_days = sorted(set(self.alert_days), reverse=True)
        if len(json.dumps(self.data)) > 20_000:
            raise ValueError("Dados adicionais demasiado extensos.")
        return self


class StatusInput(Strict):
    revision: int = Field(ge=0)
    status: Literal["ativa", "suspensa", "terminada"]
    reason: str = Field(min_length=3, max_length=2000)


class OccurrenceAction(Strict):
    revision: int = Field(ge=0)
    reason: str = Field(default="", max_length=2000)
    actual_cents: int | None = Field(default=None, strict=True, ge=0, le=10**13)
    due_date: date | None = None


def fail(message, status=422):
    raise HTTPException(status, message)


def visible_stores(user, db):
    return set(scope(user, db))


def check_scope(user, db, store_id):
    if store_id and store_id not in visible_stores(user, db):
        fail("Estabelecimento sem acesso.", 403)


def sync(db, row, reference):
    """Grava as ocorrências em falta e substitui as futuras intocadas que a regra deixou de gerar."""
    start = date.fromisoformat(row.start_date)
    end = date.fromisoformat(row.end_date) if row.end_date else None
    wanted = []
    if row.status == "ativa":
        wanted = [d.isoformat() for d in recurrence.dates(start, row.periodicity, reference + timedelta(days=AHEAD), end=end,
                                                          every_months=row.every_months, since=max(start, reference - timedelta(days=BEHIND)))]
    existing = {o.rule_date: o for o in db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id == row.id))}
    for stamp in wanted:
        if stamp not in existing:
            db.add(ObligationOccurrence(obligation_id=row.id, rule_date=stamp, due_date=stamp, expected_cents=row.amount_cents,
                                        responsible_id=row.responsible_id))
    keep = set(wanted)
    for stamp, occurrence in existing.items():
        untouched = occurrence.status == "pendente" and occurrence.revision == 0
        if untouched and stamp >= reference.isoformat():
            if stamp not in keep:
                db.delete(occurrence)  # Futura e intocada: a regra mudou; nunca histórico nem alterações.
            else:
                occurrence.expected_cents, occurrence.responsible_id = row.amount_cents, row.responsible_id
    db.flush()


def state(occurrence, reference):
    if occurrence.status in STORED:
        return STORED[occurrence.status]
    days = (date.fromisoformat(occurrence.due_date) - reference).days
    return "vencido" if days < 0 else "urgente" if days <= 7 else "a_aproximar" if days <= 30 else "agendado"


def obligations_in_scope(db, user, store_id=""):
    stores = visible_stores(user, db)
    query = select(Obligation)
    rows = [r for r in db.scalars(query) if r.store_id is None or r.store_id in stores]
    return [r for r in rows if not store_id or r.store_id in (None, store_id)]


def occurrence_view(o, ob, reference, users, reads):
    days = (date.fromisoformat(o.due_date) - reference).days
    alerts = json.loads(ob.alert_days)
    return {
        "id": o.id, "obligation_id": ob.id, "title": ob.title, "category": ob.category, "kind": ob.kind,
        "entity_name": ob.entity_name, "store_id": ob.store_id, "rule_date": o.rule_date, "due_date": o.due_date,
        "days": days, "state": state(o, reference), "expected_cents": o.expected_cents, "actual_cents": o.actual_cents,
        "status": o.status, "note": o.note, "date_origin": o.date_origin, "revision": o.revision,
        "periodicity": recurrence.describe(ob.periodicity, ob.every_months),
        "date_source": ob.date_source, "date_validated": ob.date_validated,
        "responsible": users.get(o.responsible_id or ob.responsible_id), "responsible_id": o.responsible_id or ob.responsible_id,
        "completed_at": o.completed_at.isoformat() if o.completed_at else None, "completed_by": users.get(o.completed_by),
        "read": o.id in reads, "alert_due": o.status == "pendente" and (days in alerts or days < 0),
        **managed(ob),
    }


def managed(ob):
    """Obrigação gerida por um contrato (prazo de denúncia ou pagamentos): só se altera pelo contrato."""
    data = json.loads(ob.data or "{}")
    return {"contract_id": data.get("contract_id"), "role": data.get("role")} if data.get("contract_id") else {"contract_id": None, "role": None}


def obligation_view(ob, users, next_occurrence=None):
    return {
        "id": ob.id, "store_id": ob.store_id, "category": ob.category, "kind": ob.kind, "title": ob.title,
        "description": ob.description, "entity_id": ob.entity_id, "entity_name": ob.entity_name, "category_id": ob.category_id,
        "responsible_id": ob.responsible_id, "responsible": users.get(ob.responsible_id), "periodicity": ob.periodicity,
        "periodicity_label": recurrence.describe(ob.periodicity, ob.every_months), "every_months": ob.every_months,
        "start_date": ob.start_date, "end_date": ob.end_date, "alert_days": json.loads(ob.alert_days),
        "amount_cents": ob.amount_cents, "vat_rate": str(ob.vat_rate) if ob.vat_rate is not None else None,
        "date_source": ob.date_source, "date_note": ob.date_note, "date_validated": ob.date_validated, "status": ob.status,
        "data": json.loads(ob.data), "revision": ob.revision, "next": next_occurrence, **managed(ob),
    }


def context(db, user, store_id=""):
    """Sincroniza as obrigações ativas no âmbito e devolve (obrigações, ocorrências, utilizadores, lidas)."""
    reference = today()
    rows = obligations_in_scope(db, user, store_id)
    for row in rows:
        sync(db, row, reference)
    db.commit()
    by_id = {r.id: r for r in rows}
    occurrences = list(db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id.in_(by_id)))) if by_id else []
    users = {u.id: u.name for u in db.scalars(select(User))}
    reads = set(db.scalars(select(ObligationRead.occurrence_id).where(ObligationRead.user_id == user.id)))
    return reference, by_id, occurrences, users, reads


def validate_links(db, data):
    if data.entity_id:
        entity = db.get(BusinessEntity, data.entity_id)
        if not entity:
            fail("Entidade inexistente.")
        data.entity_name = data.entity_name or entity.name
    if data.category_id and not db.get(Category, data.category_id):
        fail("Rubrica inexistente.")
    if data.responsible_id:
        responsible = db.get(User, data.responsible_id)
        if not responsible or not responsible.active:
            fail("Responsável inexistente ou inativo.")


def apply(row, data):
    values = data.model_dump(exclude={"revision"})
    for key in ("store_id", "category", "kind", "title", "description", "entity_id", "entity_name", "category_id", "responsible_id",
                "periodicity", "every_months", "amount_cents", "vat_rate", "date_source", "date_note", "date_validated"):
        setattr(row, key, values[key])
    row.every_months = data.every_months if data.periodicity == "personalizada" else None
    row.start_date, row.end_date = data.start_date.isoformat(), data.end_date.isoformat() if data.end_date else None
    row.alert_days, row.data = json.dumps(data.alert_days), json.dumps(data.data, ensure_ascii=False)


@router.get("/api/obligations/overview")
def overview(store_id: str = "", month: str = "", user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    check_scope(user, db, store_id)
    reference, by_id, occurrences, users, reads = context(db, user, store_id)
    views = [occurrence_view(o, by_id[o.obligation_id], reference, users, reads) for o in occurrences]
    pending = [v for v in views if v["status"] == "pendente"]
    active = [r for r in by_id.values() if r.status == "ativa"]
    overdue_ids = {v["obligation_id"] for v in pending if v["days"] < 0}

    def within(days):
        return [v for v in pending if 0 <= v["days"] <= days]
    first = date.fromisoformat(month + "-01") if month else reference.replace(day=1)
    last = recurrence.add_months(first, 1) - timedelta(days=1)
    calendar = defaultdict(list)
    for v in views:
        if first.isoformat() <= v["due_date"] <= last.isoformat():
            calendar[v["due_date"]].append({k: v[k] for k in ("id", "title", "category", "state")})
    months = [recurrence.add_months(reference.replace(day=1), i) for i in range(6)]
    per_month = Counter(v["due_date"][:7] for v in views if v["status"] in ("pendente", "concluida"))
    upcoming = sorted((v for v in pending if v["days"] <= 90), key=lambda v: (v["due_date"], v["title"]))
    next_by_obligation = {}
    for v in sorted(pending, key=lambda v: v["due_date"]):
        next_by_obligation.setdefault(v["obligation_id"], v)
    return {
        "today": reference.isoformat(),
        "kpis": {
            "overdue": sum(1 for v in pending if v["days"] < 0), "next_7": len(within(7)), "next_30": len(within(30)), "next_90": len(within(90)),
            "up_to_date": sum(1 for r in active if r.id not in overdue_ids), "total": len(active),
            "done_year": sum(1 for v in views if v["status"] == "concluida" and v["due_date"][:4] == str(reference.year)),
            "commitments_30_cents": sum(v["expected_cents"] or 0 for v in within(30)),
            "commitments_90_cents": sum(v["expected_cents"] or 0 for v in within(90)),
        },
        "upcoming": [v for v in upcoming if v["days"] < 0] + [v for v in upcoming if v["days"] >= 0],
        "calendar": {"month": first.isoformat()[:7], "days": calendar},
        "categories": [{"key": k, "label": label, "count": sum(1 for r in active if r.category == k)} for k, label in CATEGORIES.items()],
        "per_month": [{"month": m.isoformat()[:7], "count": per_month.get(m.isoformat()[:7], 0)} for m in months],
        "fiscal": [obligation_view(r, users, next_by_obligation.get(r.id)) for r in sorted(active, key=lambda r: r.title) if r.category == "fiscal"],
        "contracts": [v for v in upcoming if v["category"] == "contrato" and v["days"] >= 0 and v.get("role") != "payment"],
        "labels": {"categories": CATEGORIES, "states": STATES, "date_sources": DATE_SOURCES, "periodicities": recurrence.PERIODICITIES},
        "users": [{"id": u.id, "name": u.name} for u in db.scalars(select(User).where(User.active == 1)).all()],
    }


@router.get("/api/obligations/occurrences")
def occurrences(store_id: str = "", category: str = "", state_filter: str = "", responsible_id: str = "", start: date | None = None,
                end: date | None = None, q: str = "", user=Depends(current), db: Session = Depends(db_session)):
    """Lista para o detalhe de cada indicador e a grelha (filtros por categoria, estado, responsável e datas)."""
    authorize(user, db, roles=ROLES)
    check_scope(user, db, store_id)
    reference, by_id, rows, users, reads = context(db, user, store_id)
    views = [occurrence_view(o, by_id[o.obligation_id], reference, users, reads) for o in rows]
    term = q.strip().lower()
    result = [v for v in views
              if (not category or v["category"] == category) and (not state_filter or v["state"] in state_filter.split(","))
              and (not responsible_id or v["responsible_id"] == responsible_id)
              and (not start or v["due_date"] >= start.isoformat()) and (not end or v["due_date"] <= end.isoformat())
              and (not term or term in f"{v['title']} {v['entity_name']} {v['kind']}".lower())]
    return sorted(result, key=lambda v: (v["due_date"], v["title"]))[:1000]


@router.get("/api/obligations")
def obligations(store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    check_scope(user, db, store_id)
    reference, by_id, rows, users, reads = context(db, user, store_id)
    upcoming = {}
    for o in sorted((o for o in rows if o.status == "pendente"), key=lambda o: o.due_date):
        upcoming.setdefault(o.obligation_id, occurrence_view(o, by_id[o.obligation_id], reference, users, reads))
    return [obligation_view(r, users, upcoming.get(r.id)) for r in sorted(by_id.values(), key=lambda r: (r.category, r.title))]


@router.get("/api/obligations/{identifier}")
def detail(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = db.get(Obligation, identifier)
    if not row:
        fail("Obrigação inexistente.", 404)
    check_scope(user, db, row.store_id)
    reference, by_id, rows, users, reads = context(db, user, row.store_id or "")
    history = sorted((o for o in rows if o.obligation_id == row.id), key=lambda o: o.rule_date, reverse=True)
    views = [occurrence_view(o, row, reference, users, reads) for o in history]
    return {**obligation_view(row, users, next((v for v in reversed(views) if v["status"] == "pendente"), None)), "occurrences": views}


@router.post("/api/obligations")
def create(data: ObligationInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    check_scope(user, db, data.store_id)
    validate_links(db, data)
    row = Obligation(created_by=user.id, status="ativa", revision=0)
    apply(row, data)
    db.add(row)
    db.flush()
    sync(db, row, today())
    record(db, user, "obrigacao_criada", row.id, data.model_dump(mode="json"), row.store_id)
    db.commit()
    return detail(row.id, user, db)


@router.put("/api/obligations/{identifier}")
def update(identifier: str, data: ObligationInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = db.get(Obligation, identifier, with_for_update=True)
    if not row:
        fail("Obrigação inexistente.", 404)
    check_scope(user, db, row.store_id)
    check_scope(user, db, data.store_id)
    if row.revision != data.revision:
        fail("A obrigação foi alterada entretanto. Volte a abri-la.", 409)
    if managed(row)["contract_id"]:
        fail("Esta obrigação é gerida por um contrato: altere-a na ficha do contrato.", 409)
    validate_links(db, data)
    before = obligation_view(row, {})
    apply(row, data)
    row.revision += 1
    sync(db, row, today())
    record(db, user, "obrigacao_alterada", row.id, {"antes": before, "depois": data.model_dump(mode="json")}, row.store_id)
    db.commit()
    return detail(row.id, user, db)


@router.post("/api/obligations/{identifier}/status")
def change_status(identifier: str, data: StatusInput, user=Depends(current), db: Session = Depends(db_session)):
    """Suspender ou terminar deixa de gerar ocorrências futuras; o histórico mantém-se."""
    authorize(user, db, roles=ROLES)
    row = db.get(Obligation, identifier, with_for_update=True)
    if not row:
        fail("Obrigação inexistente.", 404)
    check_scope(user, db, row.store_id)
    if row.revision != data.revision:
        fail("A obrigação foi alterada entretanto. Volte a abri-la.", 409)
    if managed(row)["contract_id"]:
        fail("Esta obrigação é gerida por um contrato: use as decisões do contrato (denunciar, renovar, terminar).", 409)
    record(db, user, "obrigacao_estado", row.id, {"antes": row.status, "depois": data.status, "motivo": data.reason}, row.store_id)
    row.status, row.revision = data.status, row.revision + 1
    sync(db, row, today())
    db.commit()
    return detail(row.id, user, db)


ACTIONS = {"complete", "reopen", "cancel", "not-applicable", "reschedule", "read"}


@router.post("/api/obligations/occurrences/{identifier}/{action}")
def occurrence_action(identifier: str, action: str, data: OccurrenceAction, user=Depends(current), db: Session = Depends(db_session)):
    """Concluir, reabrir, cancelar, «não aplicável», reagendar (com origem) e confirmar leitura."""
    authorize(user, db, roles=ROLES)
    if action not in ACTIONS:
        fail("Ação desconhecida.", 404)
    occurrence = db.get(ObligationOccurrence, identifier, with_for_update=True)
    row = db.get(Obligation, occurrence.obligation_id) if occurrence else None
    if not occurrence or not row:
        fail("Ocorrência inexistente.", 404)
    check_scope(user, db, row.store_id)
    if action == "read":
        if not db.scalar(select(ObligationRead).where(ObligationRead.occurrence_id == occurrence.id, ObligationRead.user_id == user.id)):
            db.add(ObligationRead(occurrence_id=occurrence.id, user_id=user.id))
            record(db, user, "obrigacao_lida", occurrence.id, {"obrigacao": row.id}, row.store_id)
        db.commit()
        return {"ok": True}
    if occurrence.revision != data.revision:
        fail("O prazo foi alterado entretanto. Atualize a lista.", 409)
    reason = data.reason.strip()
    before = {"status": occurrence.status, "due_date": occurrence.due_date, "actual_cents": occurrence.actual_cents}
    if action == "complete":
        if occurrence.status != "pendente":
            fail("Só um prazo pendente pode ser concluído.", 409)
        occurrence.status, occurrence.completed_by, occurrence.completed_at = "concluida", user.id, now()
        occurrence.actual_cents = data.actual_cents if data.actual_cents is not None else occurrence.actual_cents
        occurrence.note = reason or occurrence.note
    elif action == "reopen":
        if occurrence.status == "pendente" or len(reason) < 3:
            fail("Indique o motivo para reabrir um prazo concluído, cancelado ou não aplicável.")
        occurrence.status, occurrence.completed_by, occurrence.completed_at = "pendente", None, None
    elif action in ("cancel", "not-applicable"):
        if occurrence.status != "pendente" or len(reason) < 3:
            fail("Indique o motivo; só um prazo pendente pode ser cancelado ou marcado como não aplicável.")
        occurrence.status = "cancelada" if action == "cancel" else "nao_aplicavel"
        occurrence.note = reason
    elif action == "reschedule":
        if not data.due_date or len(reason) < 3:
            fail("Indique a nova data-limite e a origem da alteração (por exemplo, prorrogação publicada).")
        occurrence.due_date, occurrence.date_origin = data.due_date.isoformat(), reason
    occurrence.revision += 1
    record(db, user, "obrigacao_prazo_" + action.replace("-", "_"), occurrence.id,
           {"obrigacao": row.id, "antes": before, "motivo": reason, "valor_efetivo": occurrence.actual_cents, "data": occurrence.due_date}, row.store_id)
    db.commit()
    return {"ok": True, "status": occurrence.status, "revision": occurrence.revision}
