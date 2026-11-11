"""Associação do realizado ao orçamento: propostas, confirmação, rejeição, anulação e execução por linha.

- O realizado é o dos mapas de gestão (`cost_documents`): cada documento uma vez, repartido por rubrica, ao
  custo (regime normal: base + IVA não dedutível). Cada parcela (documento × rubrica) pode ser associada a uma
  ou várias ocorrências de despesas previstas da mesma rubrica, sem nunca ultrapassar o seu custo numa versão.
- As propostas são calculadas na leitura (nada é gravado ao consultar): rubrica, fornecedor, data e valor.
  Só a confirmação grava; a rejeição também fica gravada para a proposta não voltar.
- O orçamento aprovado não é alterado: as associações são execução. Ao aprovar uma revisão, as associações
  confirmadas da versão substituída passam para as linhas copiadas (`origin_line_id`).
- Estados por despesa prevista: orçamentado, faturado (associado), pago e por pagar (pelos pagamentos e
  reconciliação já registados), ainda previsto e previsto sem documento (ocorrência passada sem associação).
"""

import json
from collections import defaultdict
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .invoice_evidence import nif_key
from .management_maps import cost_documents, deductible
from .models import (
    BankAccount,
    BankBookEntry,
    Budget,
    BudgetLine,
    BudgetLink,
    BusinessEntity,
    Category,
    ERPDocument,
    Purchase,
    now,
)
from .schemas import Strict

router = APIRouter()
ROLES = ["administrador", "gestor"]
STRONG, LIKELY = 85, 65
WINDOW = {"semanal": 4, "mensal": 20, "bimestral": 35, "trimestral": 45, "semestral": 60, "anual": 75, "personalizada": 45, "unica": 60}


def fail(message, status=422):
    raise HTTPException(status, message)


def today():
    from .budget import today as budget_today
    return budget_today()


class LinkInput(Strict):
    source_kind: Literal["purchase", "erp", "bank"]
    source_id: str = Field(min_length=1, max_length=36)
    category_id: str = Field(min_length=1, max_length=36)
    line_id: str = Field(min_length=1, max_length=36)
    rule_date: date
    amount_cents: int | None = Field(default=None, strict=True, gt=0, le=10**13)
    confidence: int = Field(default=0, ge=0, le=100)
    reasons: list[str] = Field(default_factory=list, max_length=10)


class BatchInput(Strict):
    items: list[LinkInput] = Field(min_length=1, max_length=200)


class UndoInput(Strict):
    reason: str = Field(min_length=3, max_length=2000)


# --- Fontes (realizado) ----------------------------------------------------------------------------

def source_key(identifier):
    if identifier.startswith("erp:"):
        return "erp", identifier[4:]
    if identifier.startswith("bank:"):
        return "bank", identifier[5:]
    return "purchase", identifier


def budget_stores(db, user, budget):
    stores = scope(user, db)
    if budget.store_id:
        if budget.store_id not in stores:
            fail("Estabelecimento sem acesso.", 403)
        return [budget.store_id]
    return stores


def shares(db, stores, year):
    """{(kind, id, rubrica): {date, cost}} das parcelas com custo positivo e rubrica, e detalhes por documento."""
    from .settings import vat_settings
    regime = vat_settings(db)["vat_regime"]
    percents = {c.id: c.vat_deduction_percent for c in db.scalars(select(Category))}
    from .rubric_nature import filter_shares, flags
    result, credits = {}, []
    for stamp, identifier, parts in filter_shares(cost_documents(db, stores, date(year, 1, 1), date(year, 12, 31)), flags(db), "in_payment_budget"):
        kind, sid = source_key(identifier)
        for category, base, tax in parts:
            cost = base + (tax - deductible(tax, category, percents) if regime == "normal" else tax)
            if not category:
                continue
            if cost <= 0:
                credits.append((kind, sid, category, stamp, cost))
                continue
            key = (kind, sid, category)
            if key in result:
                result[key]["cost"] += cost
            else:
                result[key] = {"date": stamp, "cost": cost}
    return result, credits


def documents(db, keys):
    """Dados de identificação de cada documento: fornecedor, NIF, número, loja."""
    ids = defaultdict(set)
    for kind, sid, _ in keys:
        ids[kind].add(sid)
    info = {}
    for row in db.scalars(select(Purchase).where(Purchase.id.in_(ids["purchase"]))) if ids["purchase"] else []:
        info[("purchase", row.id)] = {"supplier_nif": row.supplier_nif, "supplier_name": row.supplier_name, "label": f"{row.document_type} {row.document}",
                                      "store_id": row.store_id, "total_cents": row.amount_cents, "origin": "Compra"}
    for row in db.scalars(select(ERPDocument).where(ERPDocument.id.in_(ids["erp"]))) if ids["erp"] else []:
        info[("erp", row.id)] = {"supplier_nif": row.supplier_nif, "supplier_name": row.supplier_name, "label": f"{row.document_type} {row.document or row.internal_document}",
                                 "store_id": row.store_id, "total_cents": row.amount_cents, "origin": "ERP"}
    if ids["bank"]:
        accounts = {a.id: a.store_id for a in db.scalars(select(BankAccount))}
        for row in db.scalars(select(BankBookEntry).where(BankBookEntry.id.in_(ids["bank"]))):
            info[("bank", row.id)] = {"supplier_nif": "", "supplier_name": row.description, "label": row.reference or "Movimento bancário",
                                      "store_id": accounts.get(row.account_id), "total_cents": -row.amount_cents, "origin": "Banco"}
    return info


def paid_ratio(db, kind, sid, total):
    """Parte paga do documento: compras pelos pagamentos/reconciliação já registados; banco é pagamento; ERP: desconhecido."""
    if kind == "bank":
        return 1.0
    if kind == "erp" or not total:
        return None
    from .reconciliation import used
    return min(1.0, used(db, "purchase", sid) / abs(total))


# --- Ocorrências e execução ------------------------------------------------------------------------

def line_occurrences(db, budget, lines):
    """{line_id: [ocorrência]} com o custo previsto de cada data (motor de recorrências do orçamento)."""
    from .budget import occurrences
    from .settings import vat_settings
    regime = vat_settings(db)["vat_regime"]
    percents = {c.id: c.vat_deduction_percent for c in db.scalars(select(Category))}
    return {line.id: occurrences(line, budget.year, percents.get(line.category_id), regime) for line in lines}


def active_links(db, budget_id):
    return list(db.scalars(select(BudgetLink).where(BudgetLink.budget_id == budget_id, BudgetLink.status == "confirmada")))


def execution(db, budget, lines, reference=None):
    """Por linha: realizado por mês e por ocorrência, pago, por pagar, ainda previsto e previstos sem documento."""
    reference = reference or today()
    links = active_links(db, budget.id)
    by_line = defaultdict(list)
    for link in links:
        by_line[link.budget_line_id].append(link)
    # Rascunho de revisão: mostra o realizado das linhas de origem (passa para a versão nova ao aprovar).
    if budget.status == "rascunho":
        origins = {line.origin_line_id: line.id for line in lines if line.origin_line_id and not by_line.get(line.id)}
        if origins:
            for link in db.scalars(select(BudgetLink).where(BudgetLink.budget_line_id.in_(origins), BudgetLink.status == "confirmada")):
                by_line[origins[link.budget_line_id]].append(link)
    totals = {}
    for (kind, sid), item in documents(db, {(lk.source_kind, lk.source_id, lk.category_id) for lk in links}).items():
        totals[(kind, sid)] = item["total_cents"]
    ratios = {}
    occurrences = line_occurrences(db, budget, lines)
    result = {}
    for line in lines:
        months, by_date, paid, unknown = [0] * 12, defaultdict(int), 0, 0
        for link in by_line.get(line.id, []):
            months[int(link.rule_date[5:7]) - 1] += link.amount_cents
            by_date[link.rule_date] += link.amount_cents
            key = (link.source_kind, link.source_id)
            if key not in ratios:
                ratios[key] = paid_ratio(db, link.source_kind, link.source_id, totals.get(key, 0))
            if ratios[key] is None:
                unknown += link.amount_cents
            else:
                paid += round(link.amount_cents * ratios[key])
        items = []
        remaining = missing = 0
        for occ in occurrences[line.id]:
            linked = by_date.get(occ["date"], 0)
            left = max(0, occ["cost"] - linked)
            past = occ["date"] < reference.isoformat()
            state = "realizado" if linked >= occ["cost"] else "parcial" if linked else "sem_documento" if past else "previsto"
            if linked > occ["cost"]:
                state = "acima"
            items.append({**occ, "linked_cents": linked, "remaining_cents": left, "state": state})
            remaining += left
            missing += 1 if state == "sem_documento" else 0
        realized = sum(months)
        result[line.id] = {"realized_months": months, "realized_cents": realized, "paid_cents": paid, "unknown_payment_cents": unknown,
                           "unpaid_cents": max(0, realized - paid - unknown), "remaining_cents": remaining, "missing_documents": missing,
                           "links": len(by_line.get(line.id, [])), "occurrence_execution": items}
    return result


def copy_links(db, user, old_budget_ids, new_lines):
    """Ao aprovar uma revisão: as associações confirmadas das versões substituídas passam para as linhas copiadas."""
    origins = {line.origin_line_id: line for line in new_lines if line.origin_line_id}
    copied = 0
    if not origins:
        return copied
    for link in db.scalars(select(BudgetLink).where(BudgetLink.budget_id.in_(old_budget_ids), BudgetLink.budget_line_id.in_(origins),
                                                    BudgetLink.status == "confirmada")):
        line = origins[link.budget_line_id]
        db.add(BudgetLink(budget_id=line.budget_id, budget_line_id=line.id, rule_date=link.rule_date, source_kind=link.source_kind,
                          source_id=link.source_id, category_id=link.category_id, amount_cents=link.amount_cents, status="confirmada",
                          confidence=link.confidence, method="revisao", reasons=link.reasons, copied_from_id=link.id, decided_by=user.id))
        copied += 1
    return copied


# --- Propostas -------------------------------------------------------------------------------------

def tokens(text):
    return {t for t in "".join(ch.lower() if ch.isalnum() else " " for ch in text or "").split() if len(t) > 2}


def score(doc, share, line, occ, entity_nif, linked_occ):
    """(confiança, motivos) de uma ocorrência para uma parcela, ou None se incompatível."""
    days = abs((date.fromisoformat(share["date"]) - date.fromisoformat(occ["date"])).days)
    if days > WINDOW.get(line.periodicity, 45):
        return None
    reasons, points = ["Mesma rubrica"], 40
    doc_nif = nif_key(doc.get("supplier_nif") or "")
    if entity_nif:
        if doc_nif and doc_nif != entity_nif:
            return None
        if doc_nif == entity_nif:
            points += 30
            reasons.append("Mesmo fornecedor (NIF)")
    elif line.entity_name and tokens(line.entity_name) & tokens(doc.get("supplier_name")):
        points += 20
        reasons.append("Fornecedor com o mesmo nome")
    if days <= 7:
        points += 15
        reasons.append("Data próxima da prevista" if days else "Mesma data")
    elif share["date"][:7] == occ["date"][:7]:
        points += 10
        reasons.append("Mesmo mês")
    else:
        points += 3
        reasons.append(f"{days} dias da data prevista")
    expected = max(0, occ["cost"] - linked_occ) or occ["cost"]
    gap = abs(share["remaining"] - expected) / expected if expected else 1
    if gap <= 0.05:
        points += 15
        reasons.append("Valor igual ao previsto" if gap == 0 else "Valor dentro de 5 % do previsto")
    elif gap <= 0.2:
        points += 8
        reasons.append("Valor dentro de 20 % do previsto")
    else:
        reasons.append("Valor diferente do previsto")
    if linked_occ >= occ["cost"]:
        points -= 15
        reasons.append("Ocorrência já realizada por outro documento")
    return max(0, min(99, points)), reasons


def current_for(db, budget):
    from .budget import current_version, versions_of
    return current_version(versions_of(db, budget.year, budget.scope_key))


def load(db, user, identifier):
    budget = db.get(Budget, identifier)
    if not budget:
        fail("Orçamento inexistente.", 404)
    return budget, budget_stores(db, user, budget)


def proposals(db, user, budget, stores, month=0):
    lines = list(db.scalars(select(BudgetLine).where(BudgetLine.budget_id == budget.id, BudgetLine.status == "ativa")))
    found, credits = shares(db, stores, budget.year)
    links = list(db.scalars(select(BudgetLink).where(BudgetLink.budget_id == budget.id, BudgetLink.status != "anulada")))
    used = defaultdict(int)
    occupied = defaultdict(int)
    rejected = set()
    for link in links:
        if link.status == "confirmada":
            used[(link.source_kind, link.source_id, link.category_id)] += link.amount_cents
            occupied[(link.budget_line_id, link.rule_date)] += link.amount_cents
        else:
            rejected.add((link.source_kind, link.source_id, link.category_id, link.budget_line_id, link.rule_date))
    info = documents(db, found.keys())
    occurrences = line_occurrences(db, budget, lines)
    entity_nifs = {e.id: nif_key(e.tax_number or "") for e in db.scalars(select(BusinessEntity).where(BusinessEntity.id.in_([ln.entity_id for ln in lines if ln.entity_id])))}
    by_category = defaultdict(list)
    for line in lines:
        by_category[line.category_id].append(line)
    names = {c.id: c.name for c in db.scalars(select(Category))}
    pending, unbudgeted, linked = [], [], []
    for key, share in sorted(found.items(), key=lambda item: item[1]["date"], reverse=True):
        kind, sid, category = key
        if month and int(share["date"][5:7]) != month:
            continue
        doc = info.get((kind, sid), {})
        remaining = share["cost"] - used[key]
        view = {"source_kind": kind, "source_id": sid, "category_id": category, "category_name": names.get(category, "—"), "date": share["date"],
                "cost_cents": share["cost"], "linked_cents": used[key], "remaining_cents": remaining, **doc}
        if remaining <= 0:
            linked.append(view)
            continue
        candidates = []
        for line in by_category.get(category, []):
            for occ in occurrences[line.id]:
                if (kind, sid, category, line.id, occ["date"]) in rejected:
                    continue
                result = score(doc, {**share, "remaining": remaining}, line, occ, entity_nifs.get(line.entity_id), occupied[(line.id, occ["date"])])
                if result:
                    confidence, reasons = result
                    candidates.append({"line_id": line.id, "description": line.description, "entity_name": line.entity_name, "rule_date": occ["date"],
                                       "expected_cents": occ["cost"], "occurrence_linked_cents": occupied[(line.id, occ["date"])],
                                       "confidence": confidence, "reasons": reasons})
        candidates.sort(key=lambda c: (-c["confidence"], c["rule_date"]))
        candidates = candidates[:5]
        if not candidates:
            unbudgeted.append({**view, "reason": "Sem despesa prevista desta rubrica" if category not in by_category else "Sem ocorrência compatível (fornecedor ou data)"})
            continue
        best = candidates[0]
        ambiguous = len(candidates) > 1 and candidates[1]["confidence"] >= best["confidence"] - 5
        strength = "varias" if ambiguous else "forte" if best["confidence"] >= STRONG else "provavel" if best["confidence"] >= LIKELY else "possivel"
        pending.append({**view, "candidates": candidates, "strength": strength, "proposed_cents": remaining})
    return {"pending": pending, "unbudgeted": unbudgeted, "linked": linked,
            "credits": [{"source_kind": k, "source_id": s, "category_id": c, "category_name": names.get(c, "—"), "date": d, "cost_cents": v} for k, s, c, d, v in credits]}


# --- Rotas -----------------------------------------------------------------------------------------

@router.get("/api/budgets/{identifier}/matching")
def matching(identifier: str, month: int = 0, user=Depends(current), db: Session = Depends(db_session)):
    """Propostas de associação do realizado do ano às despesas previstas da versão em vigor (só leitura)."""
    authorize(user, db, roles=ROLES)
    budget, stores = load(db, user, identifier)
    if not 0 <= month <= 12:
        fail("Mês inválido.")
    data = proposals(db, user, budget, stores, month)
    in_force = current_for(db, budget)
    return {
        "budget_id": budget.id, "year": budget.year, "editable": bool(in_force and in_force.id == budget.id), **data,
        "summary": {"pending": len(data["pending"]), "strong": sum(p["strength"] == "forte" for p in data["pending"]),
                    "ambiguous": sum(p["strength"] == "varias" for p in data["pending"]), "unbudgeted": len(data["unbudgeted"]),
                    "unbudgeted_cents": sum(u["remaining_cents"] for u in data["unbudgeted"]), "linked": len(data["linked"])},
        "thresholds": {"strong": STRONG, "likely": LIKELY},
        "notes": ["As propostas comparam rubrica, fornecedor, data e valor. Nada é associado sem confirmação; o orçamento aprovado não é alterado.",
                  "Cada parcela (documento × rubrica) conta uma vez: o valor associado nunca ultrapassa o seu custo.",
                  "Notas de crédito e parcelas sem rubrica não são associadas; contam na comparação por rubrica."],
    }


def lock(db, budget):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))"), {"key": "budget-links:" + budget.id})


def confirm_one(db, user, budget, stores, data, found=None):
    in_force = current_for(db, budget)
    if not in_force or in_force.id != budget.id:
        fail("Só a versão em vigor do orçamento recebe associações.", 409)
    line = db.get(BudgetLine, data.line_id)
    if not line or line.budget_id != budget.id:
        fail("Despesa prevista inexistente nesta versão.", 404)
    if line.category_id != data.category_id:
        fail("A despesa prevista é de outra rubrica.")
    stamp = data.rule_date.isoformat()
    if stamp not in {o["date"] for o in line_occurrences(db, budget, [line])[line.id]}:
        fail("Essa data não é uma ocorrência da despesa prevista.")
    found = found if found is not None else shares(db, stores, budget.year)[0]
    key = (data.source_kind, data.source_id, data.category_id)
    if key not in found:
        fail("Documento sem custo nesta rubrica no ano do orçamento (ou fora do âmbito).", 404)
    used = sum(lk.amount_cents for lk in db.scalars(select(BudgetLink).where(
        BudgetLink.budget_id == budget.id, BudgetLink.status == "confirmada", BudgetLink.source_kind == data.source_kind,
        BudgetLink.source_id == data.source_id, BudgetLink.category_id == data.category_id)))
    remaining = found[key]["cost"] - used
    amount = data.amount_cents or remaining
    if remaining <= 0 or amount > remaining:
        fail(f"O documento já está associado ({used / 100:.2f} € de {found[key]['cost'] / 100:.2f} €): a mesma despesa não pode contar duas vezes.", 409)
    if db.scalar(select(BudgetLink.id).where(BudgetLink.budget_id == budget.id, BudgetLink.status != "anulada", BudgetLink.source_kind == data.source_kind,
                                             BudgetLink.source_id == data.source_id, BudgetLink.category_id == data.category_id,
                                             BudgetLink.budget_line_id == line.id, BudgetLink.rule_date == stamp)):
        fail("Esta associação já existe ou foi rejeitada. Anule-a primeiro.", 409)
    link = BudgetLink(budget_id=budget.id, budget_line_id=line.id, rule_date=stamp, source_kind=data.source_kind, source_id=data.source_id,
                      category_id=data.category_id, amount_cents=amount, status="confirmada", confidence=data.confidence,
                      method="sugestao" if data.reasons else "manual", reasons=json.dumps(data.reasons, ensure_ascii=False), decided_by=user.id)
    db.add(link)
    db.flush()
    record(db, user, "orcamento_associacao", link.id, {"orcamento": budget.id, "linha": line.id, "data": stamp, "origem": data.source_kind,
                                                      "documento": data.source_id, "rubrica": data.category_id, "valor": amount}, budget.store_id)
    return link


@router.post("/api/budgets/{identifier}/links")
def confirm(identifier: str, data: LinkInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    budget, stores = load(db, user, identifier)
    lock(db, budget)
    link = confirm_one(db, user, budget, stores, data)
    db.commit()
    return {"id": link.id, "amount_cents": link.amount_cents}


@router.post("/api/budgets/{identifier}/links/batch")
def confirm_batch(identifier: str, data: BatchInput, user=Depends(current), db: Session = Depends(db_session)):
    """Confirma as propostas selecionadas; tudo ou nada."""
    authorize(user, db, roles=ROLES)
    budget, stores = load(db, user, identifier)
    lock(db, budget)
    found = shares(db, stores, budget.year)[0]
    for item in data.items:
        confirm_one(db, user, budget, stores, item, found)
    db.commit()
    return {"ok": True, "confirmed": len(data.items)}


@router.post("/api/budgets/{identifier}/links/reject")
def reject(identifier: str, data: LinkInput, user=Depends(current), db: Session = Depends(db_session)):
    """Regista que esta parcela não corresponde a esta ocorrência (deixa de ser proposta)."""
    authorize(user, db, roles=ROLES)
    budget, stores = load(db, user, identifier)
    lock(db, budget)
    line = db.get(BudgetLine, data.line_id)
    if not line or line.budget_id != budget.id:
        fail("Despesa prevista inexistente nesta versão.", 404)
    stamp = data.rule_date.isoformat()
    if db.scalar(select(BudgetLink.id).where(BudgetLink.budget_id == budget.id, BudgetLink.status != "anulada", BudgetLink.source_kind == data.source_kind,
                                             BudgetLink.source_id == data.source_id, BudgetLink.category_id == data.category_id,
                                             BudgetLink.budget_line_id == line.id, BudgetLink.rule_date == stamp)):
        fail("Já existe uma decisão para esta proposta.", 409)
    link = BudgetLink(budget_id=budget.id, budget_line_id=line.id, rule_date=stamp, source_kind=data.source_kind, source_id=data.source_id,
                      category_id=data.category_id, amount_cents=0, status="rejeitada", confidence=data.confidence, method="rejeicao",
                      reasons=json.dumps(data.reasons, ensure_ascii=False), decided_by=user.id)
    db.add(link)
    db.flush()
    record(db, user, "orcamento_associacao_rejeitada", link.id, {"orcamento": budget.id, "linha": line.id, "data": stamp, "documento": data.source_id}, budget.store_id)
    db.commit()
    return {"id": link.id}


@router.post("/api/budget-links/{identifier}/undo")
def undo(identifier: str, data: UndoInput, user=Depends(current), db: Session = Depends(db_session)):
    """Anula uma associação (ou rejeição): fica no histórico com quem, quando e porquê."""
    authorize(user, db, roles=ROLES)
    link = db.get(BudgetLink, identifier, with_for_update=True)
    if not link or link.status == "anulada":
        fail("Associação inexistente ou já anulada.", 404)
    budget, _ = load(db, user, link.budget_id)
    previous = link.status
    link.status, link.undone_by, link.undone_at, link.undo_reason = "anulada", user.id, now(), data.reason.strip()
    record(db, user, "orcamento_associacao_anulada", link.id, {"orcamento": budget.id, "estado_anterior": previous, "motivo": data.reason}, budget.store_id)
    db.commit()
    return {"ok": True}


@router.get("/api/budget-lines/{identifier}/links")
def line_links(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    """Documentos e movimentos associados a uma despesa prevista (por ocorrência), com o estado de pagamento."""
    authorize(user, db, roles=ROLES)
    line = db.get(BudgetLine, identifier)
    if not line:
        fail("Despesa prevista inexistente.", 404)
    budget, _ = load(db, user, line.budget_id)
    rows = list(db.scalars(select(BudgetLink).where(BudgetLink.budget_line_id == line.id).order_by(BudgetLink.rule_date, BudgetLink.decided_at)))
    info = documents(db, {(r.source_kind, r.source_id, r.category_id) for r in rows})
    from .models import User
    users = {u.id: u.name for u in db.scalars(select(User))}
    result = []
    for row in rows:
        doc = info.get((row.source_kind, row.source_id), {})
        ratio = paid_ratio(db, row.source_kind, row.source_id, doc.get("total_cents", 0)) if row.status == "confirmada" else None
        result.append({"id": row.id, "rule_date": row.rule_date, "source_kind": row.source_kind, "source_id": row.source_id, "amount_cents": row.amount_cents,
                       "status": row.status, "confidence": row.confidence, "method": row.method, "reasons": json.loads(row.reasons or "[]"),
                       "decided_by": users.get(row.decided_by), "decided_at": row.decided_at.isoformat() if row.decided_at else None,
                       "undo_reason": row.undo_reason, "payment": None if ratio is None else "pago" if ratio >= 1 else "parcial" if ratio > 0 else "por_pagar", **doc})
    return {"line_id": line.id, "budget_id": budget.id, "links": result}

