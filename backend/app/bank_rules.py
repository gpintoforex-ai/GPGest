"""Regras por descrição do extrato para movimentos sem documento (impostos, vencimentos, encargos).

Uma regra só sugere o tratamento na reconciliação. O registo é criado pela resolução explícita do
movimento (`bank_balances.resolve`: pré-visualização, procura de duplicações, confirmação e auditoria).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .models import BankAccount, BankRule, Category, Employee
from .reconciliation import normalized
from .schemas import Strict
from .table_rules import ensure_usable

router = APIRouter()
KINDS = ("receipt", "payment", "fee", "interest", "transfer")
KIND_LABELS = {"receipt": "Recebimento", "payment": "Pagamento", "fee": "Comissão bancária", "interest": "Juros",
               "transfer": "Transferência entre contas próprias"}


def fail(message, status=422):
    raise HTTPException(status, message)


def classification(db, kind, sign, category_id, employee_id, counts_as_cost, current=(None, None)):
    """Valida rubrica, funcionário e custo de um registo ou regra; devolve os nomes para apresentar."""
    if kind == "transfer" and (category_id or employee_id or counts_as_cost):
        fail("Uma transferência entre contas próprias não tem rubrica nem custo.")
    category = ensure_usable(db, Category, category_id, current[0])
    if category_id and category is None:
        fail("Rubrica não encontrada.")
    employee = ensure_usable(db, Employee, employee_id, current[1])
    if employee_id and employee is None:
        fail("Funcionário não encontrado.")
    if counts_as_cost and (not category or sign > 0 or kind not in ("payment", "fee")):
        fail("Só um pagamento ou comissão com rubrica pode contar como custo.")
    return {"category_name": category.name if category else "", "employee_name": employee.name if employee else ""}


def clean_pattern(value):
    pattern = normalized(value or "")
    if len(pattern) < 3:
        fail("Indique o texto da descrição do extrato com pelo menos 3 letras ou algarismos.")
    return pattern


class RuleInput(Strict):
    name: str = Field(min_length=3, max_length=100)
    pattern: str = Field(min_length=3, max_length=200)
    account_id: str | None = None
    direction: Literal[-1, 0, 1] = 0
    kind: Literal["receipt", "payment", "fee", "interest", "transfer"]
    category_id: str | None = None
    employee_id: str | None = None
    target_account_id: str | None = None
    description: str = Field(default="", max_length=300)
    counts_as_cost: bool = False
    active: bool = True

    @field_validator("name", "description")
    @classmethod
    def stripped(cls, value):
        return value.strip()


def account_of(db, user, identifier):
    account = db.get(BankAccount, identifier) if identifier else None
    if identifier and account is None:
        fail("Conta não encontrada nesta empresa.", 404)
    if account:
        authorize(user, db, account.store_id, ["administrador", "gestor"])
    return account


def validated(db, user, data, row=None):
    account_of(db, user, data.account_id)
    sign = data.direction or (1 if data.kind in ("receipt", "interest") else -1)
    if data.kind == "receipt" and data.direction < 0 or data.kind in ("payment", "fee") and data.direction > 0:
        fail("O tratamento não corresponde ao sentido do movimento (entradas ou saídas).")
    if data.kind == "transfer":
        target = account_of(db, user, data.target_account_id)
        if target is None:
            fail("Escolha a outra conta própria da transferência.")
        if target.id == data.account_id:
            fail("A conta de destino tem de ser diferente da conta da regra.")
    elif data.target_account_id:
        fail("A conta de destino só se aplica a transferências próprias.")
    classification(db, data.kind, sign, data.category_id, data.employee_id, data.counts_as_cost,
                   (row.category_id, row.employee_id) if row else (None, None))
    return {**data.model_dump(), "pattern": clean_pattern(data.pattern)}


def visible_accounts(db, user):
    stores = set(scope(user, db))
    return {a.id: a for a in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores)))}


def view(rule, accounts, categories, employees):
    account = accounts.get(rule.account_id)
    target = accounts.get(rule.target_account_id)
    return {
        "id": rule.id, "name": rule.name, "pattern": rule.pattern, "account_id": rule.account_id,
        "account_name": f"{account.bank} · {account.name}" if account else "Todas as contas",
        "direction": rule.direction, "kind": rule.kind, "kind_label": KIND_LABELS[rule.kind],
        "category_id": rule.category_id, "category_name": categories.get(rule.category_id, ""),
        "employee_id": rule.employee_id, "employee_name": employees.get(rule.employee_id, ""),
        "target_account_id": rule.target_account_id,
        "target_account_name": f"{target.bank} · {target.name}" if target else "",
        "description": rule.description, "counts_as_cost": rule.counts_as_cost, "active": rule.active,
    }


def names(db):
    return ({c.id: c.name for c in db.scalars(select(Category))}, {e.id: e.name for e in db.scalars(select(Employee))})


def rules_for(db, user):
    accounts = visible_accounts(db, user)
    # Regras de uma conta sem acesso ficam fora; regras de todas as contas aplicam-se às visíveis.
    return [r for r in db.scalars(select(BankRule).order_by(BankRule.name)) if not r.account_id or r.account_id in accounts], accounts


def suggestions(db, user, banks):
    """Para cada movimento por reconciliar, a regra ativa que melhor corresponde (texto mais longo)."""
    rules, accounts = rules_for(db, user)
    rules = [r for r in rules if r.active]
    if not rules:
        return []
    categories, employees = names(db)
    result = []
    for bank in banks:
        if not bank["available_cents"] or bank["status"] == "ignorado":
            continue
        sign = 1 if bank["amount_cents"] > 0 else -1
        text = normalized(bank["description"] + " " + (bank.get("reference") or ""))
        matching = [r for r in rules if (not r.account_id or r.account_id == bank.get("account_id"))
                    and r.direction in (0, sign) and r.pattern in text
                    and not (r.kind == "receipt" and sign < 0 or r.kind in ("payment", "fee") and sign > 0)
                    and not (r.kind == "transfer" and r.target_account_id == bank.get("account_id"))]
        if not matching:
            continue
        rule = max(matching, key=lambda r: (len(r.pattern), bool(r.account_id)))
        result.append({"bank_id": bank["id"], "rule": view(rule, accounts, categories, employees),
                       "alternatives": len(matching) - 1})
    return result


@router.get("/api/bank-rules")
def list_rules(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    rules, accounts = rules_for(db, user)
    categories, employees = names(db)
    return [view(r, accounts, categories, employees) for r in rules]


@router.post("/api/bank-rules")
def create_rule(data: RuleInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    values = validated(db, user, data)
    rule = BankRule(**values)
    db.add(rule)
    db.flush()
    record(db, user, "banco_regra_criada", rule.id, values)
    db.commit()
    return {"id": rule.id}


@router.post("/api/bank-rules/{rule_id}")
def update_rule(rule_id: str, data: RuleInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    rule = db.get(BankRule, rule_id)
    rules, _ = rules_for(db, user)
    if rule is None or rule not in rules:
        fail("Regra não encontrada.", 404)
    before = {c.name: getattr(rule, c.name) for c in BankRule.__table__.columns if c.name not in ("created_at", "updated_at")}
    values = validated(db, user, data, rule)
    for name, value in values.items():
        setattr(rule, name, value)
    record(db, user, "banco_regra_alterada", rule.id, {"antes": before, "depois": values})
    db.commit()
    return {"id": rule.id}


@router.post("/api/bank-rules/{rule_id}/remove")
def remove_rule(rule_id: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    rule = db.get(BankRule, rule_id)
    rules, _ = rules_for(db, user)
    if rule is None or rule not in rules:
        fail("Regra não encontrada.", 404)
    if rule.account_id:
        account_of(db, user, rule.account_id)
    record(db, user, "banco_regra_removida", rule.id, {"nome": rule.name, "texto": rule.pattern})
    db.delete(rule)
    db.commit()
    return {"ok": True}
