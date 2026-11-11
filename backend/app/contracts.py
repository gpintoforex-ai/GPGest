"""Contratos: ficha, prazo de denúncia, fidelização, renovação e pagamentos, sobre o motor de obrigações.

- Cada contrato gere duas obrigações (`Obligation.data.contract_id` + `role`):
  * «notice»: o prazo de denúncia (data de renovação − pré-aviso), recorrente na renovação automática, ou o fim do
    contrato quando não renova sozinho. Tem os alertas (90, 60, 30, 15, 7 dias, no dia) e conclui-se quando a
    decisão é tomada.
  * «payment»: os pagamentos com valor e periodicidade, que alimentam a previsão de liquidez (como estimativa).
- As obrigações geridas por um contrato só se alteram pelo contrato (sem estruturas paralelas a dessincronizar).
- Nada é renovado, denunciado ou terminado automaticamente: só por decisão registada, com motivo e auditoria.
  Denunciar antes do fim da fidelização exige confirmação explícita (pode haver penalização).
"""

import json
from datetime import date, timedelta
from decimal import Decimal as D
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import obligations, recurrence
from .access import authorize, current, record, scope
from .db import db_session
from .models import BusinessEntity, Category, Contract, Obligation, ObligationOccurrence, User
from .obligations import ROLES, check_scope, fail, state, sync
from .schemas import Strict

router = APIRouter()
STATUSES = {"ativo": "Ativo", "denunciado": "Denunciado (termina no fim do período)", "terminado": "Terminado"}
TYPES = ["Telecomunicações", "Renda", "Software", "Energia", "Água", "Manutenção", "Seguro", "Leasing / renting", "Limpeza", "Segurança", "Outro"]
PER_YEAR = {"semanal": 52, "mensal": 12, "bimestral": 6, "trimestral": 4, "semestral": 2, "anual": 1, "unica": 0}


class ContractInput(Strict):
    revision: int = Field(default=0, ge=0)
    store_id: str | None = None
    entity_id: str | None = None
    entity_name: str = Field(default="", max_length=200)
    contract_type: str = Field(default="", max_length=60)
    description: str = Field(default="", max_length=4000)
    number: str = Field(default="", max_length=80)
    start_date: date
    end_date: date | None = None
    fidelization_end: date | None = None
    auto_renewal: bool = False
    renewal_months: int = Field(default=12, ge=1, le=120)
    notice_days: int = Field(default=30, ge=0, le=365)
    amount_cents: int | None = Field(default=None, strict=True, ge=0, le=10**13)
    vat_rate: D | None = Field(default=None, ge=0, le=100, decimal_places=2)
    payment_periodicity: Literal["unica", "semanal", "mensal", "bimestral", "trimestral", "semestral", "anual", "personalizada"] = "mensal"
    payment_every_months: int | None = Field(default=None, ge=1, le=120)
    first_payment_date: date | None = None
    category_id: str | None = None
    responsible_id: str | None = None
    alert_days: list[int] = Field(default_factory=lambda: [90, 60, 30, 15, 7, 0], max_length=10)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def valid(self):
        self.entity_name = self.entity_name.strip()
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("A data de fim não pode ser anterior à data de início.")
        if self.fidelization_end and self.fidelization_end < self.start_date:
            raise ValueError("O fim da fidelização não pode ser anterior ao início.")
        if self.auto_renewal and not self.end_date:
            raise ValueError("Na renovação automática indique a data da próxima renovação (fim do período em curso).")
        if self.payment_periodicity == "personalizada" and not self.payment_every_months:
            raise ValueError("Indique de quantos em quantos meses é o pagamento.")
        if any(not 0 <= d <= 365 for d in self.alert_days):
            raise ValueError("Os alertas vão de 0 a 365 dias antes.")
        self.alert_days = sorted(set(self.alert_days), reverse=True)
        return self


class DecisionInput(Strict):
    revision: int = Field(ge=0)
    action: Literal["denunciar", "renovar", "terminar", "reativar"]
    reason: str = Field(min_length=3, max_length=2000)
    # Renovar: nova data de fim (por omissão, mais um período de renovação). Terminar: data efetiva (por omissão, hoje).
    effective_date: date | None = None
    accept_fidelization_penalty: bool = False


def renewal_periodicity(months):
    return {1: ("mensal", None), 2: ("bimestral", None), 3: ("trimestral", None), 6: ("semestral", None), 12: ("anual", None)}.get(months, ("personalizada", months))


def next_renewal(row, reference):
    """Próxima data de renovação/fim a partir de hoje (inclusive); None sem termo."""
    if not row.end_date:
        return None
    end = date.fromisoformat(row.end_date)
    if not row.auto_renewal or row.status != "ativo" or end >= reference:
        return end
    periodicity, every = renewal_periodicity(row.renewal_months)
    upcoming = recurrence.dates(end, periodicity, reference + timedelta(days=31 * (row.renewal_months + 1)), every_months=every, since=reference)
    return upcoming[0] if upcoming else end


def obligation_for(db, row, role):
    identifier = row.notice_obligation_id if role == "notice" else row.payment_obligation_id
    return db.get(Obligation, identifier) if identifier else None


def sync_obligations(db, row, user):
    """Cria, atualiza ou termina as obrigações geridas pelo contrato (sem tocar no histórico)."""
    reference = obligations.today()
    label = f"{row.entity_name}{' · ' + row.contract_type if row.contract_type else ''}"
    common = {"store_id": row.store_id, "category": "contrato", "entity_id": row.entity_id, "entity_name": row.entity_name,
              "responsible_id": row.responsible_id, "alert_days": row.alert_days, "date_source": "contrato", "date_validated": True}
    # Prazo de denúncia / fim do contrato.
    notice = obligation_for(db, row, "notice")
    if row.end_date:
        end = date.fromisoformat(row.end_date)
        periodicity, every = renewal_periodicity(row.renewal_months) if row.auto_renewal else ("unica", None)
        values = {**common, "kind": "Prazo de denúncia" if row.auto_renewal else "Fim do contrato",
                  "title": f"{'Prazo de denúncia' if row.auto_renewal else 'Fim do contrato'} — {label}",
                  "description": f"Contrato {row.number}".strip() if row.number else "", "category_id": None, "periodicity": periodicity, "every_months": every,
                  "start_date": (end - timedelta(days=row.notice_days)).isoformat(), "end_date": None, "amount_cents": None, "vat_rate": None,
                  "date_note": (f"Renovação a cada {row.renewal_months} mês(es); pré-aviso de {row.notice_days} dias antes da renovação"
                                if row.auto_renewal else f"Fim do contrato a {row.end_date}; decidir com {row.notice_days} dias de antecedência"),
                  "data": json.dumps({"contract_id": row.id, "role": "notice", "notice_days": row.notice_days}),
                  "status": "ativa" if row.status == "ativo" else "terminada"}
        notice = upsert(db, notice, values, user)
        row.notice_obligation_id = notice.id
    elif notice:
        notice.status, notice.revision = "terminada", notice.revision + 1
        sync(db, notice, reference)
    # Pagamentos.
    payment = obligation_for(db, row, "payment")
    if row.amount_cents:
        last = None
        if row.status == "denunciado" and row.end_date:
            last = (next_renewal(row, reference) or date.fromisoformat(row.end_date)) - timedelta(days=1)
        elif not row.auto_renewal and row.end_date:
            last = date.fromisoformat(row.end_date)
        values = {**common, "kind": "Pagamento do contrato", "title": f"Pagamento — {label}", "description": "", "category_id": row.category_id,
                  "periodicity": row.payment_periodicity, "every_months": row.payment_every_months if row.payment_periodicity == "personalizada" else None,
                  "start_date": row.first_payment_date or row.start_date, "end_date": last.isoformat() if last else None,
                  "amount_cents": row.amount_cents, "vat_rate": row.vat_rate, "date_note": "Pagamentos previstos pelo contrato",
                  "data": json.dumps({"contract_id": row.id, "role": "payment"}), "status": "terminada" if row.status == "terminado" else "ativa"}
        payment = upsert(db, payment, values, user)
        row.payment_obligation_id = payment.id
    elif payment:
        payment.status, payment.revision = "terminada", payment.revision + 1
        sync(db, payment, reference)


def upsert(db, obligation, values, user):
    if obligation is None:
        obligation = Obligation(created_by=user.id, revision=0)
        db.add(obligation)
    else:
        obligation.revision += 1
    for key, value in values.items():
        setattr(obligation, key, json.dumps(value) if key == "alert_days" and not isinstance(value, str) else value)
    db.flush()
    sync(db, obligation, obligations.today())
    return obligation


def contract_view(db, row, users, reference, detail=False):
    renewal = next_renewal(row, reference)
    notice_date = renewal - timedelta(days=row.notice_days) if renewal else None
    days = (notice_date - reference).days if notice_date else None
    fake = type("O", (), {"status": "pendente", "due_date": notice_date.isoformat() if notice_date else ""})
    notice_state = state(fake, reference) if notice_date and row.status == "ativo" else ("concluido" if row.status != "ativo" else None)
    per_year = PER_YEAR.get(row.payment_periodicity, 12 // (row.payment_every_months or 12) if row.payment_periodicity == "personalizada" else 0)
    fidelized = bool(row.fidelization_end and row.fidelization_end >= reference.isoformat())
    view = {
        "id": row.id, "store_id": row.store_id, "entity_id": row.entity_id, "entity_name": row.entity_name, "contract_type": row.contract_type,
        "description": row.description, "number": row.number, "start_date": row.start_date, "end_date": row.end_date, "fidelization_end": row.fidelization_end,
        "auto_renewal": row.auto_renewal, "renewal_months": row.renewal_months, "notice_days": row.notice_days, "amount_cents": row.amount_cents,
        "vat_rate": str(row.vat_rate) if row.vat_rate is not None else None, "payment_periodicity": row.payment_periodicity,
        "payment_periodicity_label": recurrence.describe(row.payment_periodicity, row.payment_every_months), "payment_every_months": row.payment_every_months,
        "first_payment_date": row.first_payment_date, "category_id": row.category_id, "responsible_id": row.responsible_id,
        "responsible": users.get(row.responsible_id), "alert_days": json.loads(row.alert_days), "status": row.status, "status_label": STATUSES[row.status],
        "notes": row.notes, "revision": row.revision, "next_renewal": renewal.isoformat() if renewal else None,
        "notice_deadline": notice_date.isoformat() if notice_date else None, "days_to_notice": days, "notice_state": notice_state,
        "fidelized": fidelized, "annual_cents": (row.amount_cents or 0) * per_year,
        "monthly_cents": round((row.amount_cents or 0) * per_year / 12) if per_year else None,
        "notice_obligation_id": row.notice_obligation_id, "payment_obligation_id": row.payment_obligation_id,
    }
    if detail:
        from .obligations import occurrence_view
        history = []
        for role in ("notice", "payment"):
            ob = obligation_for(db, row, role)
            if ob:
                rows = db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id == ob.id).order_by(ObligationOccurrence.due_date.desc()))
                history += [{**occurrence_view(o, ob, reference, users, set()), "role": role} for o in rows]
        view["occurrences"] = sorted(history, key=lambda o: o["due_date"], reverse=True)[:48]
    return view


def load(db, user, identifier, lock=False):
    row = db.get(Contract, identifier, with_for_update=lock)
    if not row:
        fail("Contrato inexistente.", 404)
    check_scope(user, db, row.store_id)
    return row


def validate(db, data):
    if data.entity_id:
        entity = db.get(BusinessEntity, data.entity_id)
        if not entity:
            fail("Entidade inexistente.")
        data.entity_name = data.entity_name or entity.name
    if len(data.entity_name) < 2:
        fail("Indique o fornecedor ou entidade.")
    if data.category_id and not db.get(Category, data.category_id):
        fail("Rubrica inexistente.")
    if data.responsible_id:
        person = db.get(User, data.responsible_id)
        if not person or not person.active:
            fail("Responsável inexistente ou inativo.")


def apply(row, data):
    for key in ("store_id", "entity_id", "entity_name", "contract_type", "description", "number", "auto_renewal", "renewal_months", "notice_days",
                "amount_cents", "vat_rate", "payment_periodicity", "category_id", "responsible_id", "notes"):
        setattr(row, key, getattr(data, key))
    row.payment_every_months = data.payment_every_months if data.payment_periodicity == "personalizada" else None
    for key in ("start_date", "end_date", "fidelization_end", "first_payment_date"):
        value = getattr(data, key)
        setattr(row, key, value.isoformat() if value else None)
    row.alert_days = json.dumps(data.alert_days)


@router.get("/api/contracts")
def contracts(store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    check_scope(user, db, store_id)
    stores = set(scope(user, db))
    users = {u.id: u.name for u in db.scalars(select(User))}
    reference = obligations.today()
    rows = [r for r in db.scalars(select(Contract)) if (r.store_id is None or r.store_id in stores) and (not store_id or r.store_id in (None, store_id))]
    views = [contract_view(db, r, users, reference) for r in rows]
    views.sort(key=lambda v: (v["status"] != "ativo", v["notice_deadline"] or "9999-12-31", v["entity_name"]))
    return {"contracts": views, "types": TYPES, "statuses": STATUSES, "periodicities": recurrence.PERIODICITIES,
            "users": [{"id": u.id, "name": u.name} for u in db.scalars(select(User).where(User.active == 1))],
            "summary": {"active": sum(v["status"] == "ativo" for v in views), "annual_cents": sum(v["annual_cents"] for v in views if v["status"] == "ativo"),
                        "notice_30": sum(1 for v in views if v["days_to_notice"] is not None and 0 <= v["days_to_notice"] <= 30 and v["status"] == "ativo"),
                        "fidelized": sum(v["fidelized"] for v in views if v["status"] == "ativo")}}


@router.get("/api/contracts/{identifier}")
def contract(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = load(db, user, identifier)
    users = {u.id: u.name for u in db.scalars(select(User))}
    return contract_view(db, row, users, obligations.today(), detail=True)


@router.post("/api/contracts")
def create(data: ContractInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    check_scope(user, db, data.store_id)
    validate(db, data)
    row = Contract(created_by=user.id, status="ativo", revision=0)
    apply(row, data)
    db.add(row)
    db.flush()
    sync_obligations(db, row, user)
    record(db, user, "contrato_criado", row.id, data.model_dump(mode="json"), row.store_id)
    db.commit()
    return contract(row.id, user, db)


@router.put("/api/contracts/{identifier}")
def update(identifier: str, data: ContractInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = load(db, user, identifier, lock=True)
    check_scope(user, db, data.store_id)
    if row.revision != data.revision:
        fail("O contrato foi alterado entretanto. Volte a abri-lo.", 409)
    validate(db, data)
    before = contract_view(db, row, {}, obligations.today())
    apply(row, data)
    row.revision += 1
    sync_obligations(db, row, user)
    record(db, user, "contrato_alterado", row.id, {"antes": before, "depois": data.model_dump(mode="json")}, row.store_id)
    db.commit()
    return contract(row.id, user, db)


@router.post("/api/contracts/{identifier}/decision")
def decision(identifier: str, data: DecisionInput, user=Depends(current), db: Session = Depends(db_session)):
    """Decisões explícitas: denunciar (termina no fim do período), renovar (contratos sem renovação automática),
    terminar e reativar. Nunca acontecem sozinhas."""
    authorize(user, db, roles=ROLES)
    row = load(db, user, identifier, lock=True)
    if row.revision != data.revision:
        fail("O contrato foi alterado entretanto. Volte a abri-lo.", 409)
    reference = obligations.today()
    before = {"status": row.status, "end_date": row.end_date}
    if data.action == "denunciar":
        if row.status != "ativo":
            fail("Só um contrato ativo pode ser denunciado.", 409)
        if not row.end_date:
            fail("Sem data de fim ou renovação: indique-a no contrato antes de denunciar.")
        if row.fidelization_end and row.fidelization_end > (next_renewal(row, reference) or reference).isoformat() and not data.accept_fidelization_penalty:
            fail(f"O contrato está fidelizado até {row.fidelization_end}: confirme que aceita a eventual penalização.", 409)
        term = next_renewal(row, reference)
        row.status, row.end_date = "denunciado", term.isoformat()
    elif data.action == "renovar":
        if row.status != "ativo" or row.auto_renewal:
            fail("Só contratos ativos sem renovação automática se renovam manualmente.", 409)
        if not row.end_date and not data.effective_date:
            fail("Indique a nova data de fim.")
        new_end = data.effective_date or recurrence.add_months(date.fromisoformat(row.end_date), row.renewal_months)
        if row.end_date and new_end <= date.fromisoformat(row.end_date):
            fail("A nova data de fim tem de ser posterior à atual.")
        row.end_date = new_end.isoformat()
    elif data.action == "terminar":
        if row.status == "terminado":
            fail("O contrato já está terminado.", 409)
        row.status, row.end_date = "terminado", (data.effective_date or reference).isoformat()
    else:
        if row.status == "ativo":
            fail("O contrato já está ativo.", 409)
        row.status = "ativo"
    row.revision += 1
    sync_obligations(db, row, user)
    record(db, user, f"contrato_{data.action}", row.id, {"antes": before, "depois": {"status": row.status, "end_date": row.end_date},
                                                          "motivo": data.reason, "penalizacao_aceite": data.accept_fidelization_penalty}, row.store_id)
    db.commit()
    return contract(row.id, user, db)
