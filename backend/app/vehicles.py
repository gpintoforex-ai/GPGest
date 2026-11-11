"""Viaturas: ficha, IUC, inspeção periódica pela categoria e idade, seguro e revisão, sobre o motor de obrigações.

Regras legais pré-configuradas como modelos «a validar» (nunca como verdade fixa): cada viatura indica se a data
foi validada e qualquer ocorrência pode ser reagendada com a origem (por exemplo, a data do IMT ou do Portal das
Finanças). Não se assume a mesma periodicidade para todas as viaturas; motociclos, reboques e casos especiais
usam a regra manual (próxima data e intervalo em anos).

- IUC: anual, até ao último dia do mês da matrícula (modelo; confirme no Portal das Finanças).
- Inspeção: na data de aniversário da matrícula nos anos em que é devida (modelo; confirme no IMT/centro de
  inspeção). Ligeiro de passageiros: 4, 6 e 8 anos e depois anual; ligeiro de mercadorias: 2 anos e depois anual;
  pesados, táxis, ambulâncias, transporte escolar e instrução: anual a partir do 1.º ano.
- Concluir a inspeção (no prazo) atualiza a última inspeção e cria a seguinte; o histórico fica.
"""

import calendar
import json
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import obligations, recurrence
from .access import authorize, current, record, scope
from .db import db_session
from .models import Category, Contract, Obligation, ObligationOccurrence, User, Vehicle
from .obligations import ROLES, check_scope, fail, sync
from .schemas import Strict

router = APIRouter()
CATEGORIES = {
    "ligeiro_passageiros": {"label": "Ligeiro de passageiros", "ages": (4, 6, 8), "after": 1},
    "ligeiro_mercadorias": {"label": "Ligeiro de mercadorias", "ages": (2,), "after": 1},
    "pesado": {"label": "Pesado (passageiros ou mercadorias)", "ages": (1,), "after": 1},
    "servico_publico": {"label": "Táxi, ambulância, transporte escolar ou instrução", "ages": (1,), "after": 1},
    "motociclo": {"label": "Motociclo, triciclo ou quadriciclo", "ages": None, "after": None},
    "reboque": {"label": "Reboque ou semirreboque", "ages": None, "after": None},
    "outro": {"label": "Outro", "ages": None, "after": None},
}
FUELS = ["Gasóleo", "Gasolina", "Elétrico", "Híbrido", "Híbrido plug-in", "GPL", "GNV", "Outro"]
STATUSES = {"ativa": "Ativa", "vendida": "Vendida", "abatida": "Abatida"}
IUC_NOTE = "Modelo: IUC anual até ao último dia do mês da matrícula; confirme no Portal das Finanças (regime e categoria do IUC)."
INSPECTION_NOTE = "Modelo: inspeção na data de aniversário da matrícula conforme a categoria e a idade; confirme no IMT ou no centro de inspeção."


def normalize_plate(value):
    """«aa 00 bb», «AA00BB» e «AA-00-BB» são a mesma matrícula (formato português com hífenes)."""
    plain = "".join(ch for ch in value.upper() if ch.isalnum())
    return "-".join(plain[i:i + 2] for i in range(0, 6, 2)) if len(plain) == 6 else plain


class VehicleInput(Strict):
    revision: int = Field(default=0, ge=0)
    store_id: str | None = None
    plate: str = Field(min_length=4, max_length=20)
    make: str = Field(default="", max_length=60)
    model: str = Field(default="", max_length=80)
    first_registration: date
    category: Literal["ligeiro_passageiros", "ligeiro_mercadorias", "pesado", "servico_publico", "motociclo", "reboque", "outro"]
    fuel: str = Field(default="", max_length=30)
    responsible_id: str | None = None
    iuc_amount_cents: int | None = Field(default=None, strict=True, ge=0, le=10**10)
    iuc_validated: bool = False
    last_inspection_date: date | None = None
    inspection_manual_next: date | None = None
    inspection_manual_years: int | None = Field(default=None, ge=1, le=10)
    inspection_cost_cents: int | None = Field(default=None, strict=True, ge=0, le=10**10)
    inspection_validated: bool = False
    insurance_entity: str = Field(default="", max_length=200)
    insurance_policy: str = Field(default="", max_length=80)
    insurance_renewal: date | None = None
    insurance_amount_cents: int | None = Field(default=None, strict=True, ge=0, le=10**10)
    insurance_periodicity: Literal["mensal", "trimestral", "semestral", "anual"] = "anual"
    next_service_date: date | None = None
    service_cost_cents: int | None = Field(default=None, strict=True, ge=0, le=10**10)
    contract_id: str | None = None
    category_id: str | None = None
    alert_days: list[int] = Field(default_factory=lambda: [60, 30, 15, 7, 0], max_length=10)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def valid(self):
        self.plate = normalize_plate(self.plate)
        if len(self.plate) < 4:
            raise ValueError("Matrícula inválida.")
        if CATEGORIES[self.category]["ages"] is None and not self.inspection_manual_next:
            raise ValueError("Nesta categoria indique a data da próxima inspeção (regra manual).")
        if any(not 0 <= d <= 365 for d in self.alert_days):
            raise ValueError("Os alertas vão de 0 a 365 dias antes.")
        self.alert_days = sorted(set(self.alert_days), reverse=True)
        return self


class StatusInput(Strict):
    revision: int = Field(ge=0)
    status: Literal["ativa", "vendida", "abatida"]
    reason: str = Field(min_length=3, max_length=2000)


# --- Regras (modelos a validar) --------------------------------------------------------------------

def anniversary(first: date, years: int) -> date:
    return recurrence.add_months(first, 12 * years)


def inspection_ages(category):
    rule = CATEGORIES[category]
    if rule["ages"] is None:
        return
    yield from rule["ages"]
    age = rule["ages"][-1]
    while age < 80:
        age += rule["after"]
        yield age


def next_inspection(row, reference):
    """Data-limite da próxima inspeção pela regra da categoria: a primeira depois da última inspeção registada
    (pode estar vencida) ou, sem registo, a primeira a partir de hoje."""
    first = date.fromisoformat(row.first_registration)
    last = date.fromisoformat(row.last_inspection_date) if row.last_inspection_date else None
    for age in inspection_ages(row.category):
        due = anniversary(first, age)
        # A inspeção pode fazer-se até 3 meses antes da data-limite: uma inspeção nessa janela conta para esse prazo.
        if (last and recurrence.add_months(due, -3) > last) or (not last and due >= reference):
            return due
    return None


def iuc_deadline(first: date, year: int) -> date:
    return date(year, first.month, calendar.monthrange(year, first.month)[1])


def rule_explanation(row):
    rule = CATEGORIES[row.category]
    if rule["ages"] is None:
        return f"Regra manual: a partir de {row.inspection_manual_next}" + (f", de {row.inspection_manual_years} em {row.inspection_manual_years} anos" if row.inspection_manual_years else ", data única")
    ages = ", ".join(str(a) for a in rule["ages"])
    after = "anual" if rule["after"] == 1 else f"de {rule['after']} em {rule['after']} anos"
    return f"{rule['label']}: inspeção aos {ages} ano(s) e depois {after}, na data de aniversário da matrícula"


# --- Obrigações geridas ----------------------------------------------------------------------------

def owned(row):
    return json.loads(row.obligations or "{}")


def upsert(db, row, role, values, user):
    ids = owned(row)
    obligation = db.get(Obligation, ids[role]) if ids.get(role) else None
    if obligation is None:
        obligation = Obligation(created_by=user.id, revision=0)
        db.add(obligation)
    else:
        obligation.revision += 1
    base = {"store_id": row.store_id, "category": "veiculo", "entity_id": None, "entity_name": row.plate, "responsible_id": row.responsible_id,
            "alert_days": json.dumps(json.loads(row.alert_days)), "description": "", "end_date": None, "vat_rate": None, "every_months": None,
            "data": json.dumps({"vehicle_id": row.id, "role": role}), "status": "ativa" if row.status == "ativa" else "terminada"}
    for key, value in {**base, **values}.items():
        setattr(obligation, key, value)
    db.flush()
    sync(db, obligation, obligations.today())
    ids[role] = obligation.id
    row.obligations = json.dumps(ids)


def retire(db, row, role):
    ids = owned(row)
    obligation = db.get(Obligation, ids[role]) if ids.get(role) else None
    if obligation and obligation.status != "terminada":
        obligation.status, obligation.revision = "terminada", obligation.revision + 1
        sync(db, obligation, obligations.today())


def sync_obligations(db, row, user):
    reference = obligations.today()
    first = date.fromisoformat(row.first_registration)
    label = f"{row.plate}{' · ' + ' '.join(filter(None, [row.make, row.model])) if row.make or row.model else ''}"
    # IUC: a partir do próximo prazo (os anteriores, de antes do registo da viatura, supõem-se pagos).
    iuc_start = iuc_deadline(first, max(first.year + 1, reference.year))
    if iuc_start < reference:
        iuc_start = iuc_deadline(first, iuc_start.year + 1)
    upsert(db, row, "iuc", {"kind": "IUC", "title": f"IUC — {label}", "periodicity": "anual", "start_date": iuc_start.isoformat(),
                            "amount_cents": row.iuc_amount_cents, "category_id": row.category_id, "date_source": "regra_legal",
                            "date_note": IUC_NOTE, "date_validated": row.iuc_validated}, user)
    if CATEGORIES[row.category]["ages"] is None:
        values = {"periodicity": "personalizada" if row.inspection_manual_years else "unica", "every_months": 12 * row.inspection_manual_years if row.inspection_manual_years else None,
                  "start_date": row.inspection_manual_next, "date_source": "manual", "date_note": "Regra manual indicada na ficha da viatura"}
    else:
        due = next_inspection(row, reference)
        values = {"periodicity": "unica", "start_date": due.isoformat() if due else row.first_registration, "date_source": "regra_legal", "date_note": INSPECTION_NOTE}
    upsert(db, row, "inspection", {"kind": "Inspeção periódica", "title": f"Inspeção periódica — {label}", "amount_cents": row.inspection_cost_cents,
                                   "category_id": row.category_id, "date_validated": row.inspection_validated or values["date_source"] == "manual", **values}, user)
    if row.insurance_renewal:
        upsert(db, row, "insurance", {"kind": "Seguro automóvel", "title": f"Seguro — {label}{' · ' + row.insurance_entity if row.insurance_entity else ''}",
                                      "periodicity": row.insurance_periodicity, "start_date": row.insurance_renewal, "amount_cents": row.insurance_amount_cents,
                                      "category_id": row.category_id, "date_source": "contrato", "date_note": f"Apólice {row.insurance_policy}".strip(),
                                      "date_validated": True, "entity_name": row.insurance_entity or row.plate}, user)
    else:
        retire(db, row, "insurance")
    if row.next_service_date:
        upsert(db, row, "service", {"kind": "Revisão", "title": f"Revisão — {label}", "periodicity": "unica", "start_date": row.next_service_date,
                                    "amount_cents": row.service_cost_cents, "category_id": row.category_id, "date_source": "manual",
                                    "date_note": "Indicada na ficha da viatura", "date_validated": True}, user)
    else:
        retire(db, row, "service")
    return reference


def after_inspection(db, user, vehicle_id, due_date):
    """Chamado ao concluir a inspeção (regra da categoria): regista a última inspeção e cria a seguinte."""
    row = db.get(Vehicle, vehicle_id, with_for_update=True)
    if not row or CATEGORIES[row.category]["ages"] is None or row.status != "ativa":
        return
    if not row.last_inspection_date or due_date > row.last_inspection_date:
        row.last_inspection_date = due_date
        row.revision += 1
        sync_obligations(db, row, user)
        record(db, user, "viatura_inspecao_registada", row.id, {"inspecao": due_date}, row.store_id)


# --- Vistas e rotas --------------------------------------------------------------------------------

def vehicle_view(db, row, users, reference, detail=False):
    from .obligations import occurrence_view, state
    ids = owned(row)
    first = date.fromisoformat(row.first_registration)
    upcoming = {}
    for role, identifier in ids.items():
        ob = db.get(Obligation, identifier)
        if not ob or ob.status != "ativa":
            continue
        occ = db.scalar(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id == ob.id, ObligationOccurrence.status == "pendente")
                        .order_by(ObligationOccurrence.due_date).limit(1))
        if occ:
            upcoming[role] = {"due_date": occ.due_date, "days": (date.fromisoformat(occ.due_date) - reference).days, "state": state(occ, reference),
                              "expected_cents": occ.expected_cents, "validated": ob.date_validated}
    age = reference.year - first.year - ((reference.month, reference.day) < (first.month, first.day))
    view = {
        "id": row.id, "store_id": row.store_id, "plate": row.plate, "make": row.make, "model": row.model, "first_registration": row.first_registration, "age_years": age,
        "category": row.category, "category_label": CATEGORIES[row.category]["label"], "fuel": row.fuel, "status": row.status, "status_label": STATUSES[row.status],
        "responsible_id": row.responsible_id, "responsible": users.get(row.responsible_id), "iuc_amount_cents": row.iuc_amount_cents, "iuc_validated": row.iuc_validated,
        "last_inspection_date": row.last_inspection_date, "inspection_manual_next": row.inspection_manual_next, "inspection_manual_years": row.inspection_manual_years,
        "inspection_cost_cents": row.inspection_cost_cents, "inspection_validated": row.inspection_validated, "insurance_entity": row.insurance_entity,
        "insurance_policy": row.insurance_policy, "insurance_renewal": row.insurance_renewal, "insurance_amount_cents": row.insurance_amount_cents,
        "insurance_periodicity": row.insurance_periodicity, "next_service_date": row.next_service_date, "service_cost_cents": row.service_cost_cents,
        "contract_id": row.contract_id, "category_id": row.category_id, "alert_days": json.loads(row.alert_days), "notes": row.notes, "revision": row.revision,
        "inspection_rule": rule_explanation(row), "upcoming": upcoming,
    }
    if row.contract_id:
        contract = db.get(Contract, row.contract_id)
        view["contract"] = {"id": contract.id, "label": f"{contract.entity_name} · {contract.contract_type}".strip(" ·")} if contract else None
    if detail:
        history = []
        for role, identifier in ids.items():
            ob = db.get(Obligation, identifier)
            if ob:
                rows = db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id == ob.id).order_by(ObligationOccurrence.due_date.desc()))
                history += [{**occurrence_view(o, ob, reference, users, set()), "role": role} for o in rows]
        view["occurrences"] = sorted(history, key=lambda o: o["due_date"], reverse=True)[:40]
    return view


def load(db, user, identifier, lock=False):
    row = db.get(Vehicle, identifier, with_for_update=lock)
    if not row:
        fail("Viatura inexistente.", 404)
    check_scope(user, db, row.store_id)
    return row


def validate(db, data, current_id=None):
    clash = db.scalar(select(Vehicle).where(Vehicle.plate == data.plate))
    if clash and clash.id != current_id:
        fail("Já existe uma viatura com esta matrícula.", 409)
    if data.category_id and not db.get(Category, data.category_id):
        fail("Rubrica inexistente.")
    if data.contract_id and not db.get(Contract, data.contract_id):
        fail("Contrato inexistente.")
    if data.responsible_id:
        person = db.get(User, data.responsible_id)
        if not person or not person.active:
            fail("Responsável inexistente ou inativo.")


def apply(row, data):
    for key in ("store_id", "plate", "make", "model", "category", "fuel", "responsible_id", "iuc_amount_cents", "iuc_validated", "inspection_manual_years",
                "inspection_cost_cents", "inspection_validated", "insurance_entity", "insurance_policy", "insurance_amount_cents", "insurance_periodicity",
                "service_cost_cents", "contract_id", "category_id", "notes"):
        setattr(row, key, getattr(data, key))
    for key in ("first_registration", "last_inspection_date", "inspection_manual_next", "insurance_renewal", "next_service_date"):
        value = getattr(data, key)
        setattr(row, key, value.isoformat() if value else None)
    row.alert_days = json.dumps(data.alert_days)


@router.get("/api/vehicles")
def vehicles(store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    check_scope(user, db, store_id)
    stores = set(scope(user, db))
    users = {u.id: u.name for u in db.scalars(select(User))}
    reference = obligations.today()
    rows = [r for r in db.scalars(select(Vehicle).order_by(Vehicle.plate)) if (r.store_id is None or r.store_id in stores) and (not store_id or r.store_id in (None, store_id))]
    views = [vehicle_view(db, r, users, reference) for r in rows]
    contracts = [{"id": c.id, "label": f"{c.entity_name} · {c.contract_type}".strip(" ·")} for c in db.scalars(select(Contract).where(Contract.status != "terminado"))]
    return {"vehicles": views, "categories": {k: v["label"] for k, v in CATEGORIES.items()}, "fuels": FUELS, "statuses": STATUSES, "contracts": contracts,
            "users": [{"id": u.id, "name": u.name} for u in db.scalars(select(User).where(User.active == 1))],
            "notes": [IUC_NOTE, INSPECTION_NOTE, "Uma data errada corrige-se reagendando a ocorrência (com a origem) ou marcando a regra como validada na ficha."]}


@router.get("/api/vehicles/{identifier}")
def vehicle(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = load(db, user, identifier)
    users = {u.id: u.name for u in db.scalars(select(User))}
    return vehicle_view(db, row, users, obligations.today(), detail=True)


@router.post("/api/vehicles")
def create(data: VehicleInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    check_scope(user, db, data.store_id)
    validate(db, data)
    row = Vehicle(created_by=user.id, status="ativa", revision=0)
    apply(row, data)
    db.add(row)
    db.flush()
    sync_obligations(db, row, user)
    record(db, user, "viatura_criada", row.id, data.model_dump(mode="json"), row.store_id)
    db.commit()
    return vehicle(row.id, user, db)


@router.put("/api/vehicles/{identifier}")
def update(identifier: str, data: VehicleInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = load(db, user, identifier, lock=True)
    check_scope(user, db, data.store_id)
    if row.revision != data.revision:
        fail("A viatura foi alterada entretanto. Volte a abri-la.", 409)
    validate(db, data, row.id)
    before = {k: getattr(row, k) for k in ("plate", "category", "first_registration", "last_inspection_date", "iuc_amount_cents", "insurance_renewal")}
    apply(row, data)
    row.revision += 1
    sync_obligations(db, row, user)
    record(db, user, "viatura_alterada", row.id, {"antes": before, "depois": data.model_dump(mode="json")}, row.store_id)
    db.commit()
    return vehicle(row.id, user, db)


@router.post("/api/vehicles/{identifier}/status")
def change_status(identifier: str, data: StatusInput, user=Depends(current), db: Session = Depends(db_session)):
    """Vender ou abater termina as obrigações futuras (o histórico fica); reativar volta a gerá-las."""
    authorize(user, db, roles=ROLES)
    row = load(db, user, identifier, lock=True)
    if row.revision != data.revision:
        fail("A viatura foi alterada entretanto. Volte a abri-la.", 409)
    record(db, user, "viatura_estado", row.id, {"antes": row.status, "depois": data.status, "motivo": data.reason}, row.store_id)
    row.status, row.revision = data.status, row.revision + 1
    sync_obligations(db, row, user)
    db.commit()
    return vehicle(row.id, user, db)
