"""Previsão de liquidez: prioridades das origens, compromissos e estimativas (orçamento e obrigações).

Prioridade, da mais certa para a menos certa (cada valor conta uma vez, na origem mais certa):
1. Real: saldos e movimentos já registados (e depósitos em trânsito).
2. Documentos por liquidar: movimentos previstos das compras e valores em aberto do ERP.
3. Compromissos ainda não faturados: encomendas a fornecedores por receber e receções por faturar.
4. Estimativas: previsões manuais, prazos de obrigações com valor e despesas orçamentadas ainda não realizadas.

Regras contra a duplicação (a estimativa sai, com o motivo, para a lista de excluídos):
- Despesa orçamentada: conta só o que ainda não foi associado a documentos (orçamento → Associações), com IVA,
  na data prevista de pagamento. Sai quando há um documento por associar que a proposta do orçamento já liga a
  essa ocorrência (provavelmente substituída) ou quando uma obrigação pendente da mesma rubrica e mês a cobre.
- Obrigação: sai quando um documento por liquidar do mesmo fornecedor tem valor e data próximos.
- Compromisso: só a parte por receber/faturar; o que já tem documento financeiro é prioridade 2.
Só leitura: nada aqui cria, altera ou liquida registos.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal as D

from sqlalchemy import select

from .models import (
    Budget,
    BudgetLine,
    BusinessEntity,
    Obligation,
    ObligationOccurrence,
    ProcurementDocument,
)

PRIORITIES = {1: "Real", 2: "Documentos por liquidar", 3: "Compromissos", 4: "Estimativas"}
SOURCE_PRIORITY = {"deposit": 1, "planned": 2, "wintouch": 2, "commitment": 3, "manual": 4, "obligation": 4, "budget": 4, "simulation": 4}


def priority(source):
    level = SOURCE_PRIORITY.get(source, 4)
    return level, PRIORITIES[level]


def tokens(text):
    """Palavras que identificam a entidade (as mesmas da reconciliação: sem formas jurídicas, com marcas curtas)."""
    from .reconciliation import entity_tokens
    return set(entity_tokens(text or ""))


# --- Compromissos (prioridade 3) -------------------------------------------------------------------

def commitments(db, stores, reference, end):
    """Encomendas confirmadas por receber e receções sem documento por faturar: valor com IVA ainda em falta."""
    from .procurement import document_view, money, net_price
    entities = {e.id: e for e in db.scalars(select(BusinessEntity))}
    result = []
    rows = db.scalars(select(ProcurementDocument).where(
        ProcurementDocument.kind.in_(("order", "receipt")), ProcurementDocument.store_id.in_(stores),
        ProcurementDocument.confirmed_at.is_not(None), ProcurementDocument.status.notin_(("cancelled", "received", "draft"))))
    for row in rows:
        payload = document_view(db, row)
        if row.kind == "receipt" and (payload.get("document_type_id") or row.purchase_id):
            continue  # Receção com documento de compra: já é um documento por liquidar.
        amount = 0
        for line in payload.get("lines", []):
            pending = D(line.get("pending") or 0)
            if pending > 0:
                net = pending * net_price(line)
                amount += money(net) + money(net * D(line.get("vat") or 0) / 100)  # Como `procurement.line_total`.
        if amount <= 0:
            continue
        supplier = entities.get(row.supplier_id)
        anchor = payload.get("expected_date") or payload.get("document_date") or reference.isoformat()
        due = date.fromisoformat(anchor[:10]) + timedelta(days=(supplier.payment_days if supplier and supplier.payment_days is not None else 30))
        expected = max(due, reference)
        if expected > end:
            continue
        label = "Encomenda" if row.kind == "order" else "Receção por faturar"
        result.append({"key": "commitment:" + row.id, "store_id": row.store_id, "title": f"{supplier.name if supplier else 'Fornecedor'} · {label} {payload.get('number') or payload.get('document') or ''}".strip(),
                       "amount_cents": amount, "direction": -1, "due_date": due.isoformat(), "expected_date": expected.isoformat(), "target": "",
                       "currency": "EUR", "confidence": "provavel", "entity": supplier.name if supplier else "", "document": payload.get("number") or "",
                       "source_url": "?page=Encomendas%20a%20fornecedores", "estimate_note": "Valor por receber/faturar com IVA; vencimento estimado pelo prazo do fornecedor"})
    return result


# --- Obrigações (prioridade 4) ---------------------------------------------------------------------

def obligation_events(db, stores, reference, end, documents):
    """Ocorrências pendentes com valor (as gravadas pelo módulo; nada é gerado aqui), sem duplicar documentos."""
    events, excluded = [], []
    rows = {o.id: o for o in db.scalars(select(Obligation).where(Obligation.status == "ativa"))
            if o.store_id is None or o.store_id in stores}
    if not rows:
        return events, excluded
    for occ in db.scalars(select(ObligationOccurrence).where(
            ObligationOccurrence.obligation_id.in_(rows), ObligationOccurrence.status == "pendente",
            ObligationOccurrence.expected_cents.is_not(None), ObligationOccurrence.due_date <= end.isoformat())):
        ob = rows[occ.obligation_id]
        if not occ.expected_cents:
            continue
        expected = max(date.fromisoformat(occ.due_date), reference)
        item = {"key": "obligation:" + occ.id, "store_id": ob.store_id or (stores[0] if stores else None), "title": f"{ob.title} · prazo {occ.due_date}",
                "amount_cents": occ.expected_cents, "direction": -1, "due_date": occ.due_date, "expected_date": expected.isoformat(), "target": "",
                "currency": "EUR", "confidence": "provavel", "entity": ob.entity_name, "document": ob.kind, "category_id": ob.category_id,
                "source_url": "?page=Obriga%C3%A7%C3%B5es%20e%20Prazos", "estimate_note": "Valor previsto do prazo; conclui-se só por confirmação"}
        match = next((d for d in documents if d["direction"] == -1 and ob.entity_name and tokens(ob.entity_name) & tokens(d.get("entity") or d.get("title"))
                      and d.get("due_date") and abs((date.fromisoformat(d["due_date"]) - date.fromisoformat(occ.due_date)).days) <= 15
                      and abs(d["amount_cents"] - occ.expected_cents) <= max(100, occ.expected_cents // 10)), None)
        if match:
            excluded.append({**item, "exclusion": f"Substituída pelo documento «{match['title']}»"})
        else:
            events.append(item)
    return events, excluded


# --- Orçamento (prioridade 4) ----------------------------------------------------------------------

def budgets_in_force(db, stores, store_id, years):
    """Versões em vigor: do estabelecimento filtrado; sem filtro, a da empresa ou, sem ela, as dos estabelecimentos."""
    from .budget import current_version, versions_of
    result = []
    for year in years:
        if store_id:
            current = current_version(versions_of(db, year, store_id)) or current_version(versions_of(db, year, ""))
            result += [current] if current else []
            continue
        company = current_version(versions_of(db, year, ""))
        if company:
            result.append(company)
            continue
        keys = {b.scope_key for b in db.scalars(select(Budget).where(Budget.year == year, Budget.scope_key.in_(stores)))}
        result += [b for b in (current_version(versions_of(db, year, key)) for key in keys) if b]
    return result


def budget_events(db, user, stores, store_id, reference, end, obligations):
    from .budget_links import execution, proposals
    events, excluded = [], []
    for budget in budgets_in_force(db, stores, store_id, sorted({reference.year, end.year})):
        lines = list(db.scalars(select(BudgetLine).where(BudgetLine.budget_id == budget.id, BudgetLine.status == "ativa")))
        if not lines:
            continue
        run = execution(db, budget, lines, reference)
        budget_stores = [budget.store_id] if budget.store_id else stores
        likely = {}
        for item in proposals(db, user, budget, budget_stores)["pending"]:
            if item["strength"] in ("forte", "provavel"):
                best = item["candidates"][0]
                likely.setdefault((best["line_id"], best["rule_date"]), item)
        for line in lines:
            account = "bank:" + line.payment_account.removeprefix("banco:") if line.payment_account.startswith("banco:") else ""
            for occ in run[line.id]["occurrence_execution"]:
                if occ["remaining_cents"] <= 0 or not occ["cost"]:
                    continue
                payment = max(date.fromisoformat(occ["payment_date"]), reference)
                if occ["payment_date"] < reference.isoformat() or payment > end:
                    continue
                gross = round(occ["gross"] * occ["remaining_cents"] / occ["cost"])
                item = {"key": f"budget:{line.id}:{occ['date']}", "store_id": budget.store_id or (stores[0] if stores else None),
                        "title": f"{line.description} · orçamento {budget.year}", "amount_cents": gross, "direction": -1, "due_date": occ["payment_date"],
                        "expected_date": payment.isoformat(), "target": account, "currency": "EUR",
                        "confidence": "provavel" if budget.status == "aprovado" else "incerto", "entity": line.entity_name, "document": "",
                        "source_url": "?page=Or%C3%A7amento", "estimate_note": "Parte ainda não realizada da despesa orçamentada, com IVA, na data prevista de pagamento"}
                proposal = likely.get((line.id, occ["date"]))
                covering = next((o for o in obligations if o.get("category_id") == line.category_id and o["due_date"][:7] == occ["payment_date"][:7]
                                 and (not line.entity_name or tokens(line.entity_name) & tokens(o.get("entity") or o["title"]))), None)
                if proposal:
                    excluded.append({**item, "exclusion": f"Provavelmente substituída por {proposal['label']} ({proposal['supplier_name']}); associe-o no orçamento"})
                elif covering:
                    excluded.append({**item, "exclusion": f"Coberta pela obrigação «{covering['title']}»"})
                else:
                    events.append(item)
    return events, excluded


def extra_events(db, user, stores, store_id, reference, end, documents):
    """(eventos, excluídos por duplicação) de compromissos, obrigações e orçamento, por esta ordem de prioridade."""
    items = commitments(db, stores, reference, end)
    obligations, excluded = obligation_events(db, stores, reference, end, documents)
    budget, budget_excluded = budget_events(db, user, stores, store_id, reference, end, obligations)
    return items + obligations + budget, excluded + budget_excluded


def summarize(scheduled, pending):
    """Totais por prioridade do que entra na previsão e do que ficou de fora por duplicação."""
    totals = defaultdict(lambda: {"incoming_cents": 0, "outgoing_cents": 0, "count": 0})
    for event in scheduled:
        level, _ = priority(event.get("source", ""))
        totals[level]["incoming_cents" if event["direction"] == 1 else "outgoing_cents"] += event["amount_cents"]
        totals[level]["count"] += 1
    duplicates = [p for p in pending if p.get("duplicate")]
    return {"levels": [{"priority": level, "label": label, **totals[level]} for level, label in PRIORITIES.items()],
            "excluded_duplicates": len(duplicates), "excluded_duplicates_cents": sum(p["amount_cents"] for p in duplicates)}

