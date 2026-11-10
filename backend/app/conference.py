"""Centro de Conferência: o que precisa de intervenção, de todos os módulos, e as automatizações autorizadas.

- Só agrega o que os módulos já calculam (rastreabilidade, classificação, associação ao orçamento,
  reconciliação, arquivo, obrigações): nenhuma regra é repetida e consultar não grava nada.
- «Aprovar as seguras» usa as rotas de lote existentes (classificação e associação ao orçamento), que voltam a
  validar cada item; o centro só indica quais são seguras pelos limites configurados.
- Automatizações (por omissão desligadas): classificação por regra aprovada e associação ao orçamento com
  confiança muito alta e sem alternativa. Nunca pagamentos, reconciliação, lançamentos nem escritas no ERP.
  Correm a pedido («Executar agora») ou uma vez por dia pelo agendador, em nome de quem as autorizou, com
  auditoria marcada como automática.
"""

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .models import (
    ArchiveLink,
    AutomationSettings,
    BankAccount,
    BankTransaction,
    Obligation,
    ObligationOccurrence,
    Purchase,
    User,
    now,
)
from .schemas import Strict

router = APIRouter()
ROLES = ["administrador", "gestor"]
DEFAULTS = {"frequency": "manual", "classification": {"enabled": False, "min_confidence": 95},
            "budget_links": {"enabled": False, "min_confidence": 99}, "batch_classification": 85, "batch_budget_links": 85}


class Toggle(Strict):
    enabled: bool = False
    min_confidence: int = Field(ge=85, le=99)


class SettingsInput(Strict):
    revision: int = Field(ge=0)
    frequency: str = Field(pattern="^(manual|diaria)$")
    classification: Toggle
    budget_links: Toggle
    batch_classification: int = Field(default=85, ge=70, le=99)
    batch_budget_links: int = Field(default=85, ge=70, le=99)


def today():
    from .budget import today as budget_today
    return budget_today()


def settings_row(db):
    return db.get(AutomationSettings, "default")


def settings(db):
    row = settings_row(db)
    data = {**DEFAULTS, **json.loads(row.data)} if row else dict(DEFAULTS)
    return data, row


def period(start, end):
    end = end or today()
    start = start or end - timedelta(days=89)
    if start > end or (end - start).days > 400:
        raise HTTPException(422, "Selecione um período de até um ano.")
    return start, end


def stores_for(user, db, store_id):
    stores = scope(user, db)
    if store_id:
        if store_id not in stores:
            raise HTTPException(403, "Estabelecimento sem acesso.")
        return [store_id]
    return stores


# --- Secções ---------------------------------------------------------------------------------------

def section(key, title, count, tone, page, items=(), amount=None, params=None, note="", safe=None):
    return {"key": key, "title": title, "count": count, "amount_cents": amount, "tone": "success" if not count else tone, "page": page,
            "params": params or {}, "items": list(items)[:5], "note": note, "safe": safe}


def trace_sections(request, db, user, start, end, store_id):
    from .operation_trace import operations
    data = operations(request, start, end, store_id, False, user, db)
    rows = data["rows"]

    def having(dim, states):
        return [r for r in rows if r["dimensions"][dim]["state"] in states]

    def item(r, dim):
        return {"label": f"{r['supplier_name']} · {r['document_type']} {r['document']}", "detail": r["dimensions"][dim]["label"], "date": r["document_date"],
                "amount_cents": r["amount_cents"]}
    missing = having("pdf", ("em_falta",)) + [r for r in having("erp", ("em_falta",)) if r not in having("pdf", ("em_falta",))]
    differences = having("erp", ("divergencia",)) + [r for r in having("pdf", ("divergencia",)) if r not in having("erp", ("divergencia",))]
    proposed = [r for r in rows if r["dimensions"]["erp"]["state"] in ("provavel", "varias") or r["dimensions"]["pdf"]["state"] in ("provavel", "varias")]
    return [
        section("faltas", "Documentos em falta", len(missing), "warning", "Rastreabilidade",
                [item(r, "pdf" if r["dimensions"]["pdf"]["state"] == "em_falta" else "erp") for r in missing], note="PDF ou documento do ERP por associar"),
        section("diferencas", "Diferenças de valores", len(differences), "danger", "Rastreabilidade",
                [item(r, "erp" if r["dimensions"]["erp"]["state"] == "divergencia" else "pdf") for r in differences], note="Documento, ERP e PDF com valores diferentes"),
        section("propostas_documentos", "Associações documentais propostas", len(proposed), "info", "Conferência documental",
                [item(r, "erp" if r["dimensions"]["erp"]["state"] in ("provavel", "varias") else "pdf") for r in proposed], note="e-Fatura ↔ ERP ↔ PDF por confirmar"),
    ], data


def classification_section(db, user, start, end, store_id, threshold):
    from .classification import pending
    data = pending(start, end, store_id, False, user, db)
    rows = [r for r in data["rows"] if r["state"] in ("por_classificar", "fornecedor")]
    safe = [r for r in rows if r["suggestions"] and r["suggestions"][0]["confidence"] >= threshold]
    payload = [{"kind": r["kind"], "id": r["id"], "mode": "percent", "rule_id": r["suggestions"][0]["rule_id"],
                "shares": [{"category_id": s["category_id"], "value": s["percent"]} for s in r["suggestions"][0]["shares"]]} for r in safe]
    return section("rubricas", "Rubricas por classificar", len(rows), "warning", "Classificação de rubricas",
                   [{"label": f"{r['supplier_name']} · {r['document_type']} {r['document']}", "date": r["document_date"], "amount_cents": r["amount_cents"],
                     "detail": (f"Sugestão: {', '.join(s['category_name'] for s in r['suggestions'][0]['shares'])} ({r['suggestions'][0]['confidence']} %)"
                                if r["suggestions"] else "Sem sugestão")} for r in rows],
                   note="Por classificar ou só com a rubrica do fornecedor",
                   safe={"count": len(payload), "endpoint": "/classification/batch", "body": {"items": payload}, "threshold": threshold} if payload else None)


def budget_sections(db, user, stores, store_id, year, threshold):
    from .budget_links import proposals
    from .liquidity_estimates import budgets_in_force
    budgets = budgets_in_force(db, stores, store_id, [year])
    pending, unbudgeted, safe_by_budget = [], [], []
    for budget in budgets:
        data = proposals(db, user, budget, [budget.store_id] if budget.store_id else stores)
        pending += data["pending"]
        unbudgeted += data["unbudgeted"]
        safe = [p for p in data["pending"] if p["strength"] == "forte" and p["candidates"][0]["confidence"] >= threshold]
        if safe:
            safe_by_budget.append({"budget_id": budget.id, "items": [{"source_kind": p["source_kind"], "source_id": p["source_id"], "category_id": p["category_id"],
                                                                      "line_id": p["candidates"][0]["line_id"], "rule_date": p["candidates"][0]["rule_date"],
                                                                      "confidence": p["candidates"][0]["confidence"], "reasons": p["candidates"][0]["reasons"]} for p in safe]})
    one = safe_by_budget[0] if len(safe_by_budget) == 1 else None
    note = "" if budgets else f"Sem orçamento em vigor para {year}"
    return [
        section("orcamento", "Despesas por associar ao orçamento", len(pending), "info", "Orçamento", params={"area.orcamento": "associacoes"},
                items=[{"label": f"{p['supplier_name']} · {p['label']}", "date": p["date"], "amount_cents": p["remaining_cents"],
                        "detail": f"{p['candidates'][0]['description']} ({p['candidates'][0]['confidence']} %)"} for p in pending], note=note,
                safe={"count": len(one["items"]), "endpoint": f"/budgets/{one['budget_id']}/links/batch", "body": {"items": one["items"]}, "threshold": threshold} if one else None),
        section("sem_orcamento", "Despesas sem orçamento", len(unbudgeted), "warning", "Orçamento", params={"area.orcamento": "associacoes"},
                amount=sum(u["remaining_cents"] for u in unbudgeted),
                items=[{"label": f"{u['supplier_name']} · {u['label']}", "date": u["date"], "amount_cents": u["remaining_cents"], "detail": u["reason"]} for u in unbudgeted],
                note=note),
    ]


def reconciliation_section(db, stores, start, end):
    from .reconciliation import used
    rows = list(db.scalars(select(BankTransaction).join(BankAccount, BankAccount.id == BankTransaction.account_id).where(
        BankAccount.store_id.in_(stores), BankTransaction.movement_date >= start.isoformat(), BankTransaction.movement_date <= end.isoformat())
        .order_by(BankTransaction.movement_date.desc())))
    open_rows = []
    for row in rows:
        left = abs(row.amount_cents) - abs(used(db, "bank", row.id))
        if left > 0:
            open_rows.append((row, left))
    return section("reconciliacao", "Movimentos bancários por reconciliar", len(open_rows), "warning", "Reconciliação automática",
                   [{"label": row.description, "date": row.movement_date, "amount_cents": row.amount_cents, "detail": f"Por reconciliar {left / 100:.2f} €".replace(".", ",")}
                    for row, left in open_rows], amount=sum(left for _, left in open_rows),
                   note="Nunca são reconciliados automaticamente")


def archive_section(db, user):
    count = db.scalar(select(func.count()).select_from(ArchiveLink).where(ArchiveLink.status == "proposta")) or 0
    return section("arquivo", "Relações do arquivo por confirmar", count, "info", "Associações", note="PDF e digitalizações ↔ compras, ERP e pagamentos")


def obligations_section(db, stores, reference):
    rows = {o.id: o for o in db.scalars(select(Obligation).where(Obligation.status == "ativa")) if o.store_id is None or o.store_id in stores}
    limit = (reference + timedelta(days=7)).isoformat()
    due = list(db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id.in_(rows), ObligationOccurrence.status == "pendente",
                                                             ObligationOccurrence.due_date <= limit).order_by(ObligationOccurrence.due_date))) if rows else []
    overdue = sum(1 for o in due if o.due_date < reference.isoformat())
    return section("prazos", "Prazos vencidos ou a 7 dias", len(due), "danger" if overdue else "warning", "Obrigações e Prazos",
                   [{"label": rows[o.obligation_id].title, "date": o.due_date, "amount_cents": o.expected_cents,
                     "detail": "Vencido" if o.due_date < reference.isoformat() else "A vencer"} for o in due],
                   note=f"{overdue} vencido(s)" if overdue else "")


def new_documents_section(db, stores, reference):
    since = now() - timedelta(days=7)
    rows = list(db.scalars(select(Purchase).where(Purchase.store_id.in_(stores), Purchase.created_at >= since, Purchase.origin != "efatura_testes")
                           .order_by(Purchase.created_at.desc())))
    return section("novos", "Documentos novos (7 dias)", len(rows), "info", "Rastreabilidade",
                   [{"label": f"{r.supplier_name} · {r.document_type} {r.document}", "date": r.document_date, "amount_cents": r.amount_cents,
                     "detail": {"efatura": "e-Fatura (AT)", "ficheiro": "Ficheiro e-Fatura", "rececao": "Receção", "digitalizacao": "Digitalização"}.get(r.origin, "Registo")}
                    for r in rows], note="Confira a rastreabilidade de cada um")


@router.get("/api/conference/overview")
def overview(request: Request, start: date | None = None, end: date | None = None, store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    start, end = period(start, end)
    stores = stores_for(user, db, store_id)
    config, row = settings(db)
    reference = today()
    trace, _ = trace_sections(request, db, user, start, end, store_id)
    sections = [new_documents_section(db, stores, reference), *trace, classification_section(db, user, start, end, store_id, config["batch_classification"]),
                *budget_sections(db, user, stores, store_id, end.year, config["batch_budget_links"]), reconciliation_section(db, stores, start, end),
                archive_section(db, user), obligations_section(db, stores, reference)]
    return {"start": start.isoformat(), "end": end.isoformat(), "sections": sections, "total": sum(s["count"] for s in sections),
            "automation": automation_view(db, config, row),
            "notes": ["Só leitura: as correções e confirmações fazem-se nos módulos de origem ou pelas aprovações seguras, que voltam a validar cada item.",
                      "Pagamentos, reconciliação bancária, lançamentos e escritas no ERP nunca são automáticos."]}


# --- Automatizações --------------------------------------------------------------------------------

def automation_view(db, config, row):
    names = {u.id: u.name for u in db.scalars(select(User))}
    return {**config, "revision": row.revision if row else 0, "authorized_by": names.get(row.authorized_by) if row else None,
            "last_run_at": row.last_run_at.isoformat() if row and row.last_run_at else None,
            "last_result": json.loads(row.last_result) if row else {}}


@router.get("/api/conference/automation")
def get_automation(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    config, row = settings(db)
    return automation_view(db, config, row)


@router.put("/api/conference/automation")
def save_automation(data: SettingsInput, user=Depends(current), db: Session = Depends(db_session)):
    """Só o administrador autoriza automatizações; ficam em nome de quem as ativou."""
    authorize(user, db, roles=["administrador"])
    row = db.get(AutomationSettings, "default", with_for_update=True)
    if (row.revision if row else 0) != data.revision:
        raise HTTPException(409, "As automatizações foram alteradas entretanto. Atualize a página.")
    before = json.loads(row.data) if row else {}
    values = data.model_dump(exclude={"revision"})
    if not row:
        row = AutomationSettings(id="default", revision=0)
        db.add(row)
    row.data, row.revision = json.dumps(values), (row.revision or 0) + 1
    enabled = values["classification"]["enabled"] or values["budget_links"]["enabled"]
    row.authorized_by = user.id if enabled else None
    record(db, user, "automatizacoes_configuradas", "automation", {"antes": before, "depois": values})
    db.commit()
    return automation_view(db, {**DEFAULTS, **values}, row)


def run_automation(db, user, reference=None):
    """Aplica só o que está autorizado; cada operação passa pelas validações e auditoria do módulo de origem."""
    from .budget_links import LinkInput, confirm_one, lock, proposals
    from .budget_links import shares as budget_shares
    from .classification import ApplyInput, apply_one, pending
    from .liquidity_estimates import budgets_in_force
    config, row = settings(db)
    reference = reference or today()
    start, end = reference - timedelta(days=89), reference
    result = {"classified": 0, "linked": 0, "errors": []}
    if config["classification"]["enabled"]:
        for item in pending(start, end, "", False, user, db)["rows"]:
            top = item["suggestions"][0] if item["suggestions"] else None
            if not top or top["source"] != "regra" or top["confidence"] < config["classification"]["min_confidence"] or item["state"] not in ("por_classificar", "fornecedor"):
                continue
            try:
                apply_one(db, user, ApplyInput(kind=item["kind"], id=item["id"], mode="percent", rule_id=top["rule_id"],
                                               shares=[{"category_id": s["category_id"], "value": s["percent"]} for s in top["shares"]]))
                db.commit()
                result["classified"] += 1
            except HTTPException as exc:
                db.rollback()
                result["errors"].append(f"Classificação {item['document']}: {exc.detail}")
    if config["budget_links"]["enabled"]:
        stores = scope(user, db)
        for budget in budgets_in_force(db, stores, "", [reference.year]):
            budget_stores = [budget.store_id] if budget.store_id else stores
            for item in proposals(db, user, budget, budget_stores)["pending"]:
                best = item["candidates"][0]
                if item["strength"] != "forte" or best["confidence"] < config["budget_links"]["min_confidence"]:
                    continue
                try:
                    lock(db, budget)
                    confirm_one(db, user, budget, budget_stores, LinkInput(
                        source_kind=item["source_kind"], source_id=item["source_id"], category_id=item["category_id"], line_id=best["line_id"],
                        rule_date=date.fromisoformat(best["rule_date"]), confidence=best["confidence"],
                        reasons=[*best["reasons"], "Automatização autorizada"][:10]), budget_shares(db, budget_stores, budget.year)[0])
                    db.commit()
                    result["linked"] += 1
                except HTTPException as exc:
                    db.rollback()
                    result["errors"].append(f"Orçamento {item['label']}: {exc.detail}")
    row = db.get(AutomationSettings, "default", with_for_update=True)
    if row:
        row.last_run_at, row.last_result = now(), json.dumps({**result, "errors": result["errors"][:20]}, ensure_ascii=False)
    record(db, user, "automatizacoes_executadas", "automation", {**result, "automatico": True})
    db.commit()
    return result


@router.post("/api/conference/automation/run")
def run_now(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    config, row = settings(db)
    if not (config["classification"]["enabled"] or config["budget_links"]["enabled"]):
        raise HTTPException(409, "Não há automatizações ativas.")
    return run_automation(db, user)


def scheduled_tick():
    """Agendador: uma execução por dia por empresa com automatizações diárias, em nome de quem as autorizou."""
    from . import db as database
    from .db import company_db
    from .platform_models import Company
    with database.next_platform_session() as core:
        companies = list(core.scalars(select(Company).where(Company.active == 1)))
        for company in companies:
            core.expunge(company)
    for company in companies:
        try:
            with company_db(company) as db:
                config, row = settings(db)
                if not row or config["frequency"] != "diaria" or not row.authorized_by:
                    continue
                if row.last_run_at and now() - row.last_run_at < timedelta(hours=23):
                    continue
                user = db.get(User, row.authorized_by)
                if not user or not user.active or user.role != "administrador":
                    continue
                run_automation(db, user)
        except Exception:
            continue
