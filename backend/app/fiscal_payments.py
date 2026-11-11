"""Pagamentos ao Estado (IVA) classificados noutras rubricas: deteção, rubrica fiscal e reclassificação auditada.

Um registo bancário ou uma saída de caixa confirmados nunca mudam (valor, data, rubrica original). A reclassificação
grava, à parte (`rubric_reclassifications`), a rubrica de análise que os custos, a tesouraria, as obrigações fiscais
e o orçamento passam a usar. É pré-visualizada (antes/depois por vista), aplicada só por um administrador com
motivo, fica na auditoria e pode ser anulada. Os candidatos são só sugestões pela descrição do movimento.
"""

import hashlib
import json
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .models import (
    BankAccount,
    BankBookEntry,
    Category,
    Movement,
    Register,
    RubricReclassification,
    User,
    now,
)
from .rubric_nature import Reason
from .schemas import Strict

router = APIRouter()
MONTHS = 24
OUTFLOW_KINDS = ("fornecedor", "despesa", "saida_caixa")
STRONG = re.compile(r"\biva\b")
STATE = re.compile(r"\b(autoridade tributaria|at|estado|financas|tributaria|impostos?)\b")
# Pagamentos ao Estado que não são IVA (encargos com pessoal e outros impostos): nunca são propostos.
OTHER = re.compile(r"\b(tsu|seguranca social|seg social|segsocial|ss|iss|contribuicoes|iuc|irs|irc|retenc\w*|dmr|selo|imi|imt|derrama)\b")


def reclassified(db):
    """{"bank:<id>" | "move:<id>": rubrica de análise} das reclassificações ativas."""
    return {f"{row.source}:{row.source_id}": row.to_category_id
            for row in db.scalars(select(RubricReclassification).where(RubricReclassification.undone_at.is_(None)))}


def fail(message, status=422):
    raise HTTPException(status, message)


def months_back(end, months):
    first = end.replace(day=1)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)
    return first


def text_of(item):
    if isinstance(item, BankBookEntry):
        return " ".join(filter(None, (item.description, item.reference)))
    return " ".join(filter(None, (item.notes, item.entity, item.document, item.reference)))


def assess(text, category_name):
    """(confiança, motivo) de um pagamento parecer IVA ao Estado, ou None."""
    from .rubric_nature import fold
    words = fold(text)
    if STRONG.search(words):
        return 90, "A descrição menciona IVA."
    if OTHER.search(words):
        return None
    if STATE.search(words):
        return 55, "Pagamento ao Estado sem indicar o imposto: confirme no comprovativo se é IVA (pode ser IRS retido, IRC ou IUC)."
    if re.search(r"\bimpostos?\b", fold(category_name)):
        return 30, f"Está na rubrica «{category_name}» e a descrição não indica TSU, Segurança Social nem retenções: confirme no comprovativo."
    return None


def rule_pattern(text):
    """Texto proposto para uma regra bancária: a descrição sem números (referências mudam todos os meses)."""
    from .reconciliation import normalized
    return " ".join(word for word in normalized(text).split() if not any(c.isdigit() for c in word))[:80]


def load(db, stores, key, registers, active=True):
    """O registo (bancário ou de caixa) de um identificador "bank:<id>" / "move:<id>", com as verificações de acesso
    (e, com `active`, de ser um pagamento ativo)."""
    from .billing_conference import movement_store
    source, _, identifier = key.partition(":")
    if source == "bank":
        entry = db.get(BankBookEntry, identifier)
        account = db.get(BankAccount, entry.account_id) if entry else None
        if not entry or account is None or account.store_id not in stores:
            fail("Registo bancário não encontrado.", 404)
        reversed_ = db.scalar(select(BankBookEntry.id).where(BankBookEntry.original_id == entry.id).limit(1))
        if active and (entry.kind not in ("payment", "fee") or entry.amount_cents >= 0 or reversed_ or entry.transfer_id):
            fail("Só pagamentos e encargos bancários ativos (não estornados) podem ser reclassificados.")
        return entry, entry.movement_date
    if source == "move":
        from .billing_conference import local_day
        move = db.get(Movement, identifier)
        if not move or movement_store(db, move, registers)[0] not in stores:
            fail("Movimento de caixa não encontrado.", 404)
        if active and (move.direction != -1 or move.kind not in OUTFLOW_KINDS or move.reversed_by or move.reversal_of or move.transfer_id):
            fail("Só saídas de caixa ativas (não estornadas) podem ser reclassificadas.")
        return move, local_day(move.occurred_at)
    fail("Identificador de registo inválido.")


def origin_label(db, item, accounts):
    if isinstance(item, BankBookEntry):
        account = accounts.get(item.account_id)
        return f"Banco · {account.bank} {account.name}".strip() if account else "Banco"
    return "Caixa"


# -------------------------------------------------------------------------- consulta


@router.get("/api/rubric-nature/fiscal-payments")
def overview(user=Depends(current), db: Session = Depends(db_session)):
    from .dashboard import today
    from .rubric_nature import flag, flags, outflow_records
    authorize(user, db, roles=["administrador", "gestor"])
    stores = scope(user, db)
    end = today()
    start = months_back(end, MONTHS)
    moved, table = reclassified(db), flags(db)
    categories = {c.id: c for c in db.scalars(select(Category))}
    accounts = {a.id: a for a in db.scalars(select(BankAccount))}
    candidates = []
    for day, key, category, amount, item in outflow_records(db, stores, start, end, moved):
        if flag(table, category, "in_fiscal"):
            continue
        name = categories[category].name if category in categories else ""
        text = text_of(item)
        found = assess(text, name)
        if not found:
            continue
        candidates.append({
            "id": key, "date": day, "description": text or "(sem descrição)", "origin": origin_label(db, item, accounts),
            "amount_cents": amount, "category_id": category or None, "category_name": name or "Sem rubrica",
            "confidence": found[0], "reason": found[1], "reclassified": key in moved,
            "rule": {"pattern": rule_pattern(text), "account_id": item.account_id,
                     "counts_as_cost": bool(item.counts_as_cost)} if isinstance(item, BankBookEntry) and rule_pattern(text) else None,
        })
    candidates.sort(key=lambda row: row["date"], reverse=True)
    candidates.sort(key=lambda row: row["confidence"], reverse=True)
    users = {u.id: u.name for u in db.scalars(select(User))}
    from .models import BankRule
    rules = set(db.scalars(select(BankRule.pattern).where(BankRule.active.is_(True))))
    history = []
    for row in db.scalars(select(RubricReclassification).order_by(RubricReclassification.created_at.desc()).limit(100)):
        item = db.get(BankBookEntry if row.source == "bank" else Movement, row.source_id)
        if item is None:
            continue
        history.append({
            "id": row.id, "record": f"{row.source}:{row.source_id}", "origin": origin_label(db, item, accounts),
            "date": item.movement_date if row.source == "bank" else item.occurred_at.date().isoformat(),
            "description": text_of(item), "amount_cents": abs(item.amount_cents),
            "from_name": categories[row.from_category_id].name if row.from_category_id in categories else "Sem rubrica",
            "to_name": categories[row.to_category_id].name if row.to_category_id in categories else "",
            "reason": row.reason, "user": users.get(row.user_id, ""), "created_at": row.created_at.isoformat(),
            "undone_at": row.undone_at.isoformat() if row.undone_at else None,
            # Proposta de regra bancária para os pagamentos seguintes com a mesma descrição (só sugerida).
            "rule": {"pattern": rule_pattern(text_of(item)), "category_id": row.to_category_id, "counts_as_cost": bool(item.counts_as_cost),
                     "exists": rule_pattern(text_of(item)) in rules}
                    if row.source == "bank" and not row.undone_at and rule_pattern(text_of(item)) else None,
        })
    return {
        "start": str(start), "end": str(end), "can_apply": user.role == "administrador",
        "fiscal_rubrics": [{"id": c.id, "name": c.name} for c in sorted(categories.values(), key=lambda c: c.name) if c.active and c.in_fiscal],
        "candidates": candidates, "history": history,
    }


# -------------------------------------------------------------------------- rubrica fiscal


class FiscalRubric(Strict):
    name: str = Field(default="IVA", min_length=2, max_length=100)


@router.post("/api/rubric-nature/fiscal-rubric")
def create_fiscal_rubric(data: FiscalRubric, user=Depends(current), db: Session = Depends(db_session)):
    from .rubric_nature import PRESETS
    authorize(user, db, roles=["administrador"])
    name = data.name.strip()
    if db.scalar(select(Category.id).where(func.lower(Category.name) == name.lower())):
        fail(f"Já existe uma rubrica «{name}». Defina a natureza dela como «Obrigação fiscal» na tabela abaixo.", 409)
    row = Category(name=name, icon="landmark", nature="fiscal", **PRESETS["fiscal"])
    db.add(row)
    db.flush()
    record(db, user, "rubrica_fiscal_criada", row.id, {"name": name, "nature": "fiscal", **PRESETS["fiscal"]})
    db.commit()
    return {"id": row.id, "name": row.name}


# -------------------------------------------------------------------------- reclassificação


class Selection(Strict):
    items: list[str] = Field(min_length=1, max_length=200)
    category_id: str = Field(min_length=1, max_length=36)


class Confirmation(Selection):
    fingerprint: str = Field(min_length=64, max_length=64)
    reason: Reason
    request_id: str | None = Field(default=None, max_length=36)


def prepared(db, user, data):
    """{id: (registo, data, rubrica atual)} e a rubrica de destino, validados."""
    stores = scope(user, db)
    target = db.get(Category, data.category_id)
    if not target or not target.active:
        fail("Escolha uma rubrica ativa de destino.")
    if len(set(data.items)) != len(data.items):
        fail("Há registos repetidos na seleção.")
    moved = reclassified(db)
    registers = {row.id: row for row in db.scalars(select(Register))}
    result = {}
    for key in data.items:
        item, day = load(db, stores, key, registers)
        category = moved.get(key, item.category_id or "")
        if category == target.id:
            fail(f"O registo de {day} já está na rubrica «{target.name}».")
        result[key] = (item, day, category)
    return result, target, stores


def fingerprint(selected, target):
    payload = sorted((key, category) for key, (_, _, category) in selected.items())
    return hashlib.sha256(json.dumps([payload, target.id], sort_keys=True).encode()).hexdigest()


def preview(db, user, data):
    from .dashboard import today
    from .rubric_nature import VIEWS, collect, flags, view_totals
    from .settings import vat_settings
    selected, target, stores = prepared(db, user, data)
    end = today()
    start = min(months_back(end, MONTHS), *(day_of(day) for _, day, _ in selected.values()))
    regime = vat_settings(db)["vat_regime"]
    categories = {c.id: c for c in db.scalars(select(Category))}
    percents = {c.id: c.vat_deduction_percent for c in categories.values()}
    table = flags(db)
    before_data = collect(db, stores, start, end)
    after_data = {
        "documents": [(d, i, target.id if i in selected else c, b, t) for d, i, c, b, t in before_data["documents"]],
        "treasury": [(d, i, target.id if i in selected else c, a) for d, i, c, a in before_data["treasury"]],
    }
    before = view_totals(before_data, regime, percents, table, before_data)
    after = view_totals(after_data, regime, percents, table, after_data)
    labels = {**{key: label for key, (label, _) in VIEWS.items()}, "costs": "Mapa de custos e proveitos (custos)",
              "budget": "Orçamento de pagamentos (realizado)"}
    accounts = {a.id: a for a in db.scalars(select(BankAccount))}
    rows = [{"id": key, "date": day, "description": text_of(item) or "(sem descrição)", "origin": origin_label(db, item, accounts),
             "amount_cents": abs(item.amount_cents),
             "from_name": categories[category].name if category in categories else "Sem rubrica", "to_name": target.name}
            for key, (item, day, category) in sorted(selected.items(), key=lambda pair: pair[1][1], reverse=True)]
    return {
        "fingerprint": fingerprint(selected, target), "start": str(start), "end": str(end), "target": target.name,
        "total_cents": sum(row["amount_cents"] for row in rows), "rows": rows,
        "views": [{"key": key, "label": labels[key], "before_cents": before[key], "after_cents": after[key],
                   "difference_cents": after[key] - before[key]} for key in (*VIEWS, "costs", "budget")],
        "preserved": ["Valor, data, descrição e rubrica original de cada registo (nada é alterado no banco nem na caixa)",
                      "Extratos, livro bancário e reconciliação", "Previsão de liquidez", "Mapa de IVA e previsão de IVA"],
    }


def day_of(value):
    from datetime import date
    return date.fromisoformat(value).replace(day=1)


@router.post("/api/rubric-nature/reclassify/preview")
def reclassify_preview(data: Selection, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    return preview(db, user, data)


@router.post("/api/rubric-nature/reclassify")
def reclassify(data: Confirmation, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    # Serializa reclassificações concorrentes dos mesmos registos.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))"), {"key": "rubric-reclassification"})
    result = preview(db, user, data)
    if result["fingerprint"] != data.fingerprint:
        fail("Os registos ou as rubricas mudaram desde a pré-visualização. Pré-visualize novamente antes de aplicar.", 409)
    selected, target, _ = prepared(db, user, data)
    from .rubric_requests import settle
    settle(db, user, data.request_id, "reclassification", {"items": data.items, "category_id": data.category_id}, data.reason)
    stamp = now()
    summary = {view["key"]: [view["before_cents"], view["after_cents"]] for view in result["views"]}
    for key, (item, day, category) in selected.items():
        source, _, identifier = key.partition(":")
        active = db.scalar(select(RubricReclassification).where(
            RubricReclassification.source == source, RubricReclassification.source_id == identifier,
            RubricReclassification.undone_at.is_(None)).with_for_update())
        if active:
            active.undone_at, active.undone_by = stamp, user.id
        db.flush()
        row = RubricReclassification(source=source, source_id=identifier, from_category_id=category or None,
                                     to_category_id=target.id, reason=data.reason, user_id=user.id)
        db.add(row)
        db.flush()
        record(db, user, "pagamento_rubrica_reclassificada", key, {
            "date": day, "amount_cents": abs(item.amount_cents), "original_category_id": item.category_id,
            "before_category_id": category or None, "after_category_id": target.id, "reason": data.reason,
            "reclassification_id": row.id, "simulation": summary, "request_id": data.request_id})
    db.commit()
    return {"applied": len(selected), "views": result["views"]}


class Undo(Strict):
    reason: Reason


@router.post("/api/rubric-nature/reclassifications/{identifier}/undo")
def undo(identifier: str, data: Undo, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    row = db.scalar(select(RubricReclassification).where(RubricReclassification.id == identifier).with_for_update())
    if not row:
        fail("Reclassificação não encontrada.", 404)
    if row.undone_at:
        fail("Esta reclassificação já foi anulada ou substituída.", 409)
    registers = {r.id: r for r in db.scalars(select(Register))}
    load(db, scope(user, db), f"{row.source}:{row.source_id}", registers, active=False)
    row.undone_at, row.undone_by = now(), user.id
    record(db, user, "pagamento_rubrica_reposta", f"{row.source}:{row.source_id}", {
        "reclassification_id": row.id, "from_category_id": row.to_category_id, "to_category_id": row.from_category_id,
        "reason": data.reason})
    db.commit()
    return {"undone": row.id}
