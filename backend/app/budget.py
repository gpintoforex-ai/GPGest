"""Orçamento e previsão de despesas: centros de custo, orçamentos com versões e despesas previstas.

- Só planeamento: nada aqui cria pagamentos, lançamentos ou movimentos de tesouraria.
- Valores das linhas sem IVA, por ocorrência; o custo orçamental usa o critério dos mapas de gestão
  (regime normal: base + IVA não dedutível estimado pela % da rubrica; restantes regimes: base + IVA),
  para comparar com o realizado sem distorções. O IVA e o valor a pagar com IVA ficam para a tesouraria.
- As ocorrências vêm do motor de recorrências comum (`recurrence.py`), com aumentos previstos e exceções.
- O realizado é o mesmo dos mapas de gestão (`management_maps.cost_documents`): cada documento uma vez.
- Versões: rascunho (editável) → aprovado (bloqueado) → revisto (substituído por uma revisão aprovada).
- Um desvio abaixo do orçamento só é «poupança» depois do fecho do período contabilístico.
"""

import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from . import recurrence
from .access import authorize, current, record, scope
from .cash_sheet import day
from .db import db_session
from .management_maps import UNCLASSIFIED, cost_documents, deductible
from .models import (
    AccountingPeriod,
    BankAccount,
    Budget,
    BudgetLine,
    BusinessEntity,
    Category,
    CostCenter,
    Store,
    User,
    now,
)
from .schemas import Strict

router = APIRouter()
ROLES = ["administrador", "gestor"]
STATUSES = {"rascunho": "Rascunho", "aprovado": "Aprovado", "revisto": "Revisto"}
LINE_STATUSES = {"ativa": "Ativa", "suspensa": "Suspensa"}
MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def today():
    return date.fromisoformat(day(now()))


def fail(message, status=422):
    raise HTTPException(status, message)


def cents(value) -> int:
    return int(D(value).quantize(D(1), rounding=ROUND_HALF_UP))


class Increase(Strict):
    start: date = Field(alias="from")
    percent: D = Field(ge=-100, le=1000, decimal_places=2)

    model_config = {"extra": "forbid", "populate_by_name": True}


class Exception_(Strict):
    date: date
    skip: bool = False
    amount_cents: int | None = Field(default=None, strict=True, ge=0, le=10**12)

    @model_validator(mode="after")
    def valid(self):
        if not self.skip and self.amount_cents is None:
            raise ValueError("Numa exceção indique um valor ou que a ocorrência não se realiza.")
        return self


class LineInput(Strict):
    revision: int = Field(default=0, ge=0)
    category_id: str = Field(min_length=1)
    description: str = Field(min_length=2, max_length=200)
    entity_id: str | None = None
    entity_name: str = Field(default="", max_length=200)
    cost_center_id: str | None = None
    amount_cents: int = Field(strict=True, ge=0, le=10**12)
    # Verdadeiro: o valor indicado inclui IVA e é convertido para a base (sem IVA) com a taxa.
    amount_includes_vat: bool = False
    vat_rate: D = Field(default=D(0), ge=0, le=100, decimal_places=2)
    periodicity: Literal["unica", "semanal", "mensal", "bimestral", "trimestral", "semestral", "anual", "personalizada"]
    every_months: int | None = Field(default=None, ge=1, le=120)
    start_date: date
    end_date: date | None = None
    payment_days: int = Field(default=0, ge=0, le=365)
    payment_account: str = Field(default="", max_length=50)
    increases: list[Increase] = Field(default_factory=list, max_length=24)
    exceptions: list[Exception_] = Field(default_factory=list, max_length=120)
    note: str = Field(default="", max_length=2000)
    status: Literal["ativa", "suspensa"] = "ativa"

    @model_validator(mode="after")
    def valid(self):
        self.description = self.description.strip()
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("A data de fim não pode ser anterior à data de início.")
        if self.periodicity == "personalizada" and not self.every_months:
            raise ValueError("Na periodicidade personalizada indique de quantos em quantos meses.")
        if len({e.date for e in self.exceptions}) != len(self.exceptions):
            raise ValueError("Há exceções repetidas para a mesma data.")
        return self


class BudgetInput(Strict):
    year: int = Field(ge=2000, le=2100)
    store_id: str | None = None
    name: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=2000)
    # Copiar as linhas de outro orçamento (normalmente do ano anterior), com uma % de atualização.
    copy_from_id: str | None = None
    update_percent: D = Field(default=D(0), ge=-100, le=1000, decimal_places=2)


class BudgetEdit(Strict):
    revision: int = Field(ge=0)
    name: str = Field(min_length=2, max_length=120)
    note: str = Field(default="", max_length=2000)


class VersionAction(Strict):
    revision: int = Field(ge=0)
    reason: str = Field(default="", max_length=2000)


class CostCenterInput(Strict):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=2, max_length=100)
    active: bool = True


# --- Âmbito e acesso -------------------------------------------------------------------------------

def stores_for(user, db, store_id):
    stores = scope(user, db)
    if store_id:
        if store_id not in stores:
            fail("Estabelecimento sem acesso.", 403)
        return [store_id]
    return stores


def load_budget(db, user, identifier, lock=False):
    row = db.get(Budget, identifier, with_for_update=lock)
    if not row:
        fail("Orçamento inexistente.", 404)
    stores_for(user, db, row.store_id)
    return row


def lock(db, year, scope_key):
    """Serializa a criação de versões do mesmo ano e âmbito (por empresa: o esquema entra na chave)."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))"), {"key": f"budget:{year}:{scope_key}"})


def editable(row):
    if row.status != "rascunho":
        fail("Orçamento aprovado: crie uma revisão para o alterar.", 409)


# --- Cálculo das ocorrências -----------------------------------------------------------------------

def factor(increases, when: str):
    result = D(1)
    for item in increases:
        if item["from"] <= when:
            result *= 1 + D(str(item["percent"])) / 100
    return result


def occurrences(line, year, percent_deductible, regime):
    """[{date, payment_date, base, vat, deductible, gross, cost}] da linha no ano, já com aumentos e exceções."""
    if line.status != "ativa":
        return []
    start = date.fromisoformat(line.start_date)
    end = date.fromisoformat(line.end_date) if line.end_date else None
    first, last = date(year, 1, 1), date(year, 12, 31)
    if start > last or (end and end < first):
        return []
    increases = sorted(json.loads(line.increases), key=lambda i: i["from"])
    exceptions = {e["date"]: e for e in json.loads(line.exceptions)}
    rate = D(str(line.vat_rate or 0))
    result = []
    for when in recurrence.dates(start, line.periodicity, last, end=end, every_months=line.every_months, since=max(start, first)):
        stamp = when.isoformat()
        exception = exceptions.get(stamp)
        if exception and exception.get("skip"):
            continue
        base = exception["amount_cents"] if exception and exception.get("amount_cents") is not None else cents(D(line.amount_cents) * factor(increases, stamp))
        vat = cents(D(base) * rate / 100)
        allowed = deductible(vat, line.category_id, {line.category_id: percent_deductible}) if regime == "normal" else 0
        cost = base + vat - allowed
        result.append({"date": stamp, "payment_date": (when + timedelta(days=line.payment_days)).isoformat(), "base": base, "vat": vat,
                       "deductible": allowed, "gross": base + vat, "cost": cost, "exception": bool(exception)})
    return result


def line_view(line, year, categories, centers, regime, month):
    category = categories.get(line.category_id)
    items = occurrences(line, year, category.vat_deduction_percent if category else None, regime)
    months = [0] * 12
    for item in items:
        months[int(item["date"][5:7]) - 1] += item["cost"]
    return {
        "id": line.id, "budget_id": line.budget_id, "category_id": line.category_id, "category_name": category.name if category else "—",
        "description": line.description, "entity_id": line.entity_id, "entity_name": line.entity_name,
        "cost_center_id": line.cost_center_id, "cost_center": centers[line.cost_center_id].name if line.cost_center_id in centers else "",
        "amount_cents": line.amount_cents, "vat_rate": str(line.vat_rate), "periodicity": line.periodicity,
        "periodicity_label": recurrence.describe(line.periodicity, line.every_months), "every_months": line.every_months,
        "start_date": line.start_date, "end_date": line.end_date, "payment_days": line.payment_days, "payment_account": line.payment_account,
        "increases": json.loads(line.increases), "exceptions": json.loads(line.exceptions), "note": line.note, "status": line.status,
        "revision": line.revision, "months": months, "month_cents": months[month - 1], "annual_cents": sum(months),
        "annual_base_cents": sum(i["base"] for i in items), "annual_vat_cents": sum(i["vat"] for i in items),
        "annual_deductible_cents": sum(i["deductible"] for i in items), "annual_gross_cents": sum(i["gross"] for i in items),
        "occurrences": items,
    }


def actual_costs(db, stores, year, regime, percents):
    """{rubrica: [12 meses]} do realizado (mesmo critério e documentos dos mapas de gestão)."""
    result = defaultdict(lambda: [0] * 12)
    for stamp, _, shares in cost_documents(db, stores, date(year, 1, 1), date(year, 12, 31)):
        month = int(stamp[5:7]) - 1
        for category, base, tax in shares:
            result[category or UNCLASSIFIED][month] += base + (tax - deductible(tax, category, percents) if regime == "normal" else tax)
    return result


def closed_months(db, year):
    """Meses do ano inteiramente dentro de períodos contabilísticos fechados."""
    periods = [(p.start, p.end) for p in db.scalars(select(AccountingPeriod).where(AccountingPeriod.closed_at.is_not(None)))]
    closed = set()
    for month in range(1, 13):
        first = date(year, month, 1)
        last = recurrence.add_months(first, 1) - timedelta(days=1)
        if any(start <= first.isoformat() and last.isoformat() <= end for start, end in periods):
            closed.add(month)
    return closed


def deviation(actual, budget):
    """(desvio €, desvio % ou None quando o orçamento é zero)."""
    diff = actual - budget
    return diff, (round(diff * 100 / budget, 1) if budget else None)


def budget_view(row, users=None):
    return {"id": row.id, "year": row.year, "store_id": row.store_id, "name": row.name, "version": row.version, "status": row.status,
            "status_label": STATUSES[row.status], "previous_id": row.previous_id, "note": row.note, "revision": row.revision,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "approved_by": (users or {}).get(row.approved_by), "created_at": row.created_at.isoformat() if row.created_at else None}


def versions_of(db, year, scope_key):
    return list(db.scalars(select(Budget).where(Budget.year == year, Budget.scope_key == scope_key).order_by(Budget.version)))


def current_version(versions):
    approved = [v for v in versions if v.status == "aprovado"]
    return (approved or versions or [None])[-1]


# --- Painel ----------------------------------------------------------------------------------------

@router.get("/api/budgets/overview")
def overview(year: int = 0, store_id: str = "", month: int = 0, budget_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    """Orçamento em vigor (último aprovado; senão o rascunho mais recente) comparado com o realizado."""
    from .settings import vat_settings
    authorize(user, db, roles=ROLES)
    reference = today()
    year = year or reference.year
    if not 2000 <= year <= 2100:
        fail("Ano inválido.")
    month = month or (reference.month if year == reference.year else 12)
    if not 1 <= month <= 12:
        fail("Mês inválido.")
    stores = stores_for(user, db, store_id)
    versions = versions_of(db, year, store_id)
    selected = next((v for v in versions if v.id == budget_id), None) if budget_id else current_version(versions)
    if budget_id and not selected:
        fail("Orçamento inexistente para este ano e âmbito.", 404)
    regime = vat_settings(db)["vat_regime"]
    categories = {c.id: c for c in db.scalars(select(Category))}
    percents = {c.id: c.vat_deduction_percent for c in categories.values()}
    centers = {c.id: c for c in db.scalars(select(CostCenter))}
    users = {u.id: u.name for u in db.scalars(select(User))}
    lines = []
    if selected:
        from .budget_links import execution
        rows = list(db.scalars(select(BudgetLine).where(BudgetLine.budget_id == selected.id).order_by(BudgetLine.position, BudgetLine.created_at)))
        executed = execution(db, selected, rows, reference)
        lines = [{**line_view(r, year, categories, centers, regime, month), **executed[r.id]} for r in rows]
        for line in lines:
            line["month_realized_cents"] = line["realized_months"][month - 1]
    actual = actual_costs(db, stores, year, regime, percents)
    closed = closed_months(db, year)
    planned = defaultdict(lambda: [0] * 12)
    for line in lines:
        for index, value in enumerate(line["months"]):
            planned[line["category_id"]][index] += value
    keys = set(planned) | {k for k, v in actual.items() if any(v)}

    def name(key):
        return categories[key].name if key in categories else "Por classificar"
    rubrics = []
    for key in keys:
        b, a = planned.get(key, [0] * 12), actual.get(key, [0] * 12)
        ytd_b, ytd_a = sum(b[:month]), sum(a[:month])
        diff, pct = deviation(ytd_a, ytd_b)
        month_diff, month_pct = deviation(a[month - 1], b[month - 1])
        rubrics.append({
            "category_id": key or None, "category_name": name(key), "budgeted": key in planned and any(b),
            "budget_months": b, "actual_months": a, "month_budget_cents": b[month - 1], "month_actual_cents": a[month - 1],
            "month_deviation_cents": month_diff, "month_deviation_pct": month_pct,
            "ytd_budget_cents": ytd_b, "ytd_actual_cents": ytd_a, "ytd_deviation_cents": diff, "ytd_deviation_pct": pct,
            "annual_budget_cents": sum(b), "annual_actual_cents": sum(a),
            "state": "nao_orcamentado" if not any(b) and ytd_a else "acima" if diff > 0 else "abaixo" if diff < 0 else "em_linha",
        })
    rubrics.sort(key=lambda r: (-max(r["annual_budget_cents"], r["annual_actual_cents"]), r["category_name"]))
    total_b = [sum(planned[k][i] for k in planned) for i in range(12)]
    total_a = [sum(actual[k][i] for k in actual) for i in range(12)]
    series, running_b, running_a = [], 0, 0
    for i in range(12):
        running_b += total_b[i]
        running_a += total_a[i]
        diff, pct = deviation(total_a[i], total_b[i])
        series.append({"month": i + 1, "label": MONTHS[i], "budget_cents": total_b[i], "actual_cents": total_a[i] if i < month else None,
                       "deviation_cents": diff if i < month else None, "deviation_pct": pct if i < month else None,
                       "cumulative_budget_cents": running_b, "cumulative_actual_cents": running_a if i < month else None, "closed": i + 1 in closed})
    ytd_b, ytd_a = sum(total_b[:month]), sum(total_a[:month])
    diff, pct = deviation(ytd_a, ytd_b)
    unbudgeted = sum(sum(r["actual_months"][:month]) for r in rubrics if r["state"] == "nao_orcamentado")
    suppliers = defaultdict(int)
    for line in lines:
        suppliers[line["entity_name"] or "Sem fornecedor indicado"] += line["annual_cents"]
    previous = [budget_view(v) for v in db.scalars(select(Budget).where(Budget.scope_key == store_id, Budget.year < year).order_by(Budget.year.desc(), Budget.version.desc()))]
    accounts = [{"id": "caixa", "name": "Caixa"}] + [{"id": "banco:" + a.id, "name": f"{a.name} ({a.bank} …{a.iban_last4})"}
                                                       for a in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores)))]
    store_names = {s.id: s.name for s in db.scalars(select(Store).where(Store.id.in_(scope(user, db))))}
    return {
        "today": reference.isoformat(), "year": year, "month": month, "store_id": store_id or None, "regime": regime,
        "budget": budget_view(selected, users) if selected else None,
        "versions": [budget_view(v, users) for v in versions],
        "copy_sources": previous[:12],
        "kpis": {
            "annual_budget_cents": sum(total_b), "period_budget_cents": ytd_b, "period_actual_cents": ytd_a,
            "deviation_cents": diff, "deviation_pct": pct, "month_budget_cents": total_b[month - 1], "month_actual_cents": total_a[month - 1],
            "unbudgeted_cents": unbudgeted, "lines": len(lines), "period_closed": month in closed,
            "annual_gross_cents": sum(line["annual_gross_cents"] for line in lines),
            "annual_vat_cents": sum(line["annual_vat_cents"] for line in lines),
            "annual_deductible_cents": sum(line["annual_deductible_cents"] for line in lines),
            "linked_cents": sum(line["realized_cents"] for line in lines), "paid_cents": sum(line["paid_cents"] for line in lines),
            "unpaid_cents": sum(line["unpaid_cents"] for line in lines), "unknown_payment_cents": sum(line["unknown_payment_cents"] for line in lines),
            "remaining_cents": sum(line["remaining_cents"] for line in lines), "missing_documents": sum(line["missing_documents"] for line in lines),
        },
        "months": series, "rubrics": rubrics,
        "suppliers": [{"name": k, "annual_cents": v} for k, v in sorted(suppliers.items(), key=lambda kv: -kv[1]) if v][:10],
        "lines": lines,
        "options": {
            "categories": [{"id": c.id, "name": c.name, "vat_deduction_percent": c.vat_deduction_percent}
                           for c in sorted(categories.values(), key=lambda c: c.name) if c.active and not c.cash_difference],
            "cost_centers": [{"id": c.id, "code": c.code, "name": c.name, "active": c.active} for c in sorted(centers.values(), key=lambda c: c.code)],
            "accounts": accounts, "stores": [{"id": k, "name": v} for k, v in sorted(store_names.items(), key=lambda kv: kv[1])],
            "periodicities": recurrence.PERIODICITIES, "statuses": STATUSES,
        },
        "notes": [
            "Orçamento sem IVA: o custo previsto usa o critério dos mapas de gestão (no regime normal, base mais IVA não dedutível estimado pela % da rubrica).",
            "Realizado: os mesmos documentos dos mapas de gestão (compras, documentos do ERP sem compra e custos bancários com rubrica), cada um uma vez, pela data do documento.",
            "Um desvio abaixo do orçamento só é poupança depois do fecho do período contabilístico.",
        ],
    }


# --- Orçamentos e versões --------------------------------------------------------------------------

def shift(stamp: str | None, years: int):
    return recurrence.add_months(date.fromisoformat(stamp), 12 * years).isoformat() if stamp else stamp


def copy_lines(db, source_id, target, years=0, percent=D(0)):
    """Copia as linhas; numa revisão (mesmo ano) guarda a linha de origem, para passar as associações."""
    scale = 1 + percent / 100
    for position, line in enumerate(db.scalars(select(BudgetLine).where(BudgetLine.budget_id == source_id).order_by(BudgetLine.position, BudgetLine.created_at))):
        exceptions = [{**e, "date": shift(e["date"], years), **({"amount_cents": cents(D(e["amount_cents"]) * scale)} if e.get("amount_cents") is not None else {})}
                      for e in json.loads(line.exceptions)]
        increases = [{**i, "from": shift(i["from"], years)} for i in json.loads(line.increases)]
        db.add(BudgetLine(budget_id=target.id, category_id=line.category_id, description=line.description, entity_id=line.entity_id,
                          entity_name=line.entity_name, cost_center_id=line.cost_center_id, amount_cents=cents(D(line.amount_cents) * scale),
                          vat_rate=line.vat_rate, periodicity=line.periodicity, every_months=line.every_months,
                          start_date=shift(line.start_date, years), end_date=shift(line.end_date, years), payment_days=line.payment_days,
                          payment_account=line.payment_account, increases=json.dumps(increases), exceptions=json.dumps(exceptions),
                          note=line.note, status=line.status, position=position, origin_line_id=line.id if not years else None))


@router.post("/api/budgets")
def create_budget(data: BudgetInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    stores_for(user, db, data.store_id)
    scope_key = data.store_id or ""
    lock(db, data.year, scope_key)
    if versions_of(db, data.year, scope_key):
        fail("Já existe um orçamento deste ano para este âmbito. Para o alterar, crie uma revisão.", 409)
    source = load_budget(db, user, data.copy_from_id) if data.copy_from_id else None
    store = db.get(Store, data.store_id) if data.store_id else None
    row = Budget(year=data.year, store_id=data.store_id, scope_key=scope_key, version=1, status="rascunho", created_by=user.id, note=data.note,
                 name=data.name.strip() or f"Orçamento {data.year}" + (f" — {store.name}" if store else ""))
    db.add(row)
    db.flush()
    if source:
        copy_lines(db, source.id, row, data.year - source.year, data.update_percent)
    record(db, user, "orcamento_criado", row.id, {**data.model_dump(mode="json"), "versao": 1}, row.store_id)
    db.commit()
    return budget_view(row)


@router.put("/api/budgets/{identifier}")
def edit_budget(identifier: str, data: BudgetEdit, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = load_budget(db, user, identifier, lock=True)
    if row.revision != data.revision:
        fail("O orçamento foi alterado entretanto. Atualize a página.", 409)
    record(db, user, "orcamento_alterado", row.id, {"antes": {"name": row.name, "note": row.note}, "depois": data.model_dump()}, row.store_id)
    row.name, row.note, row.revision = data.name.strip(), data.note, row.revision + 1
    db.commit()
    return budget_view(row)


@router.post("/api/budgets/{identifier}/approve")
def approve(identifier: str, data: VersionAction, user=Depends(current), db: Session = Depends(db_session)):
    """Aprova o rascunho; a versão aprovada anterior do mesmo ano e âmbito passa a «revisto»."""
    authorize(user, db, roles=ROLES)
    row = load_budget(db, user, identifier, lock=True)
    if row.revision != data.revision:
        fail("O orçamento foi alterado entretanto. Atualize a página.", 409)
    editable(row)
    if not db.scalar(select(func.count()).select_from(BudgetLine).where(BudgetLine.budget_id == row.id)):
        fail("Acrescente pelo menos uma despesa prevista antes de aprovar.")
    replaced = [v for v in versions_of(db, row.year, row.scope_key) if v.status == "aprovado" and v.id != row.id]
    for old in replaced:
        old.status, old.revision = "revisto", old.revision + 1
    row.status, row.approved_by, row.approved_at, row.revision = "aprovado", user.id, now(), row.revision + 1
    from .budget_links import copy_links
    copied = copy_links(db, user, [v.id for v in replaced], list(db.scalars(select(BudgetLine).where(BudgetLine.budget_id == row.id))))
    record(db, user, "orcamento_aprovado", row.id, {"versao": row.version, "substitui": [v.id for v in replaced], "nota": data.reason,
                                                    "associacoes_passadas": copied}, row.store_id)
    db.commit()
    return budget_view(row)


@router.post("/api/budgets/{identifier}/revise")
def revise(identifier: str, data: VersionAction, user=Depends(current), db: Session = Depends(db_session)):
    """Cria uma nova versão em rascunho com as linhas copiadas; a aprovada continua em vigor até aprovar a nova."""
    authorize(user, db, roles=ROLES)
    row = load_budget(db, user, identifier, lock=True)
    if row.revision != data.revision:
        fail("O orçamento foi alterado entretanto. Atualize a página.", 409)
    if row.status != "aprovado":
        fail("Só um orçamento aprovado pode ser revisto.", 409)
    if len(data.reason.strip()) < 3:
        fail("Indique o motivo da revisão.")
    lock(db, row.year, row.scope_key)
    versions = versions_of(db, row.year, row.scope_key)
    if any(v.status == "rascunho" for v in versions):
        fail("Já existe uma revisão em rascunho deste orçamento.", 409)
    new = Budget(year=row.year, store_id=row.store_id, scope_key=row.scope_key, name=row.name, note=data.reason.strip(), status="rascunho",
                 version=max(v.version for v in versions) + 1, previous_id=row.id, created_by=user.id)
    db.add(new)
    db.flush()
    copy_lines(db, row.id, new)
    record(db, user, "orcamento_revisao", new.id, {"origem": row.id, "versao": new.version, "motivo": data.reason}, row.store_id)
    db.commit()
    return budget_view(new)


# --- Despesas previstas (linhas) -------------------------------------------------------------------

def validate_line(db, user, budget, data):
    category = db.get(Category, data.category_id)
    if not category:
        fail("Rubrica inexistente.")
    if data.entity_id:
        entity = db.get(BusinessEntity, data.entity_id)
        if not entity:
            fail("Fornecedor inexistente.")
        data.entity_name = data.entity_name.strip() or entity.name
    if data.cost_center_id and not db.get(CostCenter, data.cost_center_id):
        fail("Centro de custo inexistente.")
    if data.payment_account and data.payment_account != "caixa":
        account = db.get(BankAccount, data.payment_account.removeprefix("banco:")) if data.payment_account.startswith("banco:") else None
        if not account or account.store_id not in scope(user, db):
            fail("Conta de pagamento inexistente.")
    if data.start_date > date(budget.year, 12, 31) or (data.end_date and data.end_date < date(budget.year, 1, 1)):
        fail(f"A despesa tem de ter ocorrências em {budget.year}.")


def apply_line(line, data):
    rate = data.vat_rate
    base = cents(D(data.amount_cents) * 100 / (100 + rate)) if data.amount_includes_vat else data.amount_cents
    for key in ("category_id", "description", "entity_id", "entity_name", "cost_center_id", "periodicity", "payment_days", "payment_account", "note", "status"):
        setattr(line, key, getattr(data, key))
    line.amount_cents, line.vat_rate = base, rate
    line.every_months = data.every_months if data.periodicity == "personalizada" else None
    line.start_date, line.end_date = data.start_date.isoformat(), data.end_date.isoformat() if data.end_date else None
    line.increases = json.dumps([{"from": i.start.isoformat(), "percent": str(i.percent)} for i in sorted(data.increases, key=lambda i: i.start)])
    line.exceptions = json.dumps([e.model_dump(mode="json", exclude_none=True) for e in sorted(data.exceptions, key=lambda e: e.date)])


def line_snapshot(line):
    return {k: getattr(line, k) for k in ("category_id", "description", "entity_name", "cost_center_id", "amount_cents", "periodicity",
                                          "every_months", "start_date", "end_date", "payment_days", "payment_account", "increases",
                                          "exceptions", "status")} | {"vat_rate": str(line.vat_rate)}


@router.post("/api/budgets/{identifier}/lines")
def create_line(identifier: str, data: LineInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    budget = load_budget(db, user, identifier, lock=True)
    editable(budget)
    validate_line(db, user, budget, data)
    position = db.scalar(select(func.coalesce(func.max(BudgetLine.position), -1)).where(BudgetLine.budget_id == budget.id)) + 1
    line = BudgetLine(budget_id=budget.id, position=position)
    apply_line(line, data)
    db.add(line)
    db.flush()
    record(db, user, "orcamento_linha_criada", line.id, {"orcamento": budget.id, **line_snapshot(line)}, budget.store_id)
    db.commit()
    return {"id": line.id}


def load_line(db, user, identifier):
    line = db.get(BudgetLine, identifier, with_for_update=True)
    if not line:
        fail("Despesa prevista inexistente.", 404)
    budget = load_budget(db, user, line.budget_id, lock=True)
    editable(budget)
    return line, budget


@router.put("/api/budget-lines/{identifier}")
def update_line(identifier: str, data: LineInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    line, budget = load_line(db, user, identifier)
    if line.revision != data.revision:
        fail("A despesa foi alterada entretanto. Volte a abri-la.", 409)
    validate_line(db, user, budget, data)
    before = line_snapshot(line)
    apply_line(line, data)
    line.revision += 1
    record(db, user, "orcamento_linha_alterada", line.id, {"orcamento": budget.id, "antes": before, "depois": line_snapshot(line)}, budget.store_id)
    db.commit()
    return {"id": line.id}


@router.delete("/api/budget-lines/{identifier}")
def delete_line(identifier: str, revision: int, user=Depends(current), db: Session = Depends(db_session)):
    """Só em rascunho (planeamento, sem movimentos); fica na auditoria com os dados da linha."""
    authorize(user, db, roles=ROLES)
    line, budget = load_line(db, user, identifier)
    if line.revision != revision:
        fail("A despesa foi alterada entretanto. Volte a abri-la.", 409)
    from .models import BudgetLink
    links = list(db.scalars(select(BudgetLink).where(BudgetLink.budget_line_id == line.id)))
    record(db, user, "orcamento_linha_removida", line.id, {"orcamento": budget.id, **line_snapshot(line),
                                                          "associacoes": [{"documento": lk.source_id, "valor": lk.amount_cents, "estado": lk.status} for lk in links]},
           budget.store_id)
    for link in links:
        db.delete(link)
    db.flush()
    db.delete(line)
    db.commit()
    return {"ok": True}


# --- Centros de custo ------------------------------------------------------------------------------

@router.get("/api/cost-centers")
def cost_centers(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    return [{"id": c.id, "code": c.code, "name": c.name, "active": c.active} for c in db.scalars(select(CostCenter).order_by(CostCenter.code))]


def unique_code(db, code, exclude=None):
    clash = db.scalar(select(CostCenter).where(func.lower(CostCenter.code) == code.lower()))
    if clash and clash.id != exclude:
        fail("Já existe um centro de custo com esse código.", 409)


@router.post("/api/cost-centers")
def create_cost_center(data: CostCenterInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    code = data.code.strip().upper()
    unique_code(db, code)
    row = CostCenter(code=code, name=data.name.strip(), active=data.active)
    db.add(row)
    db.flush()
    record(db, user, "centro_custo_criado", row.id, {"code": code, "name": row.name})
    db.commit()
    return {"id": row.id, "code": row.code, "name": row.name, "active": row.active}


@router.put("/api/cost-centers/{identifier}")
def update_cost_center(identifier: str, data: CostCenterInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = db.get(CostCenter, identifier, with_for_update=True)
    if not row:
        fail("Centro de custo inexistente.", 404)
    code = data.code.strip().upper()
    unique_code(db, code, row.id)
    record(db, user, "centro_custo_alterado", row.id, {"antes": {"code": row.code, "name": row.name, "active": row.active},
                                                       "depois": {"code": code, "name": data.name.strip(), "active": data.active}})
    row.code, row.name, row.active = code, data.name.strip(), data.active
    db.commit()
    return {"id": row.id, "code": row.code, "name": row.name, "active": row.active}
