"""Dashboard de gestão: agregações no servidor, reutilizando os cálculos dos módulos existentes.

Fontes (sem duplicar regras):
- Faturação: SAF-T importado (`billing_conference.documents`) ou, sem SAF-T, vendas do ERP (com IVA).
- Recebimentos e pagamentos: `billing_conference.receipts` / `payment_list` (sem transferências internas
  nem estornos; liquidação de TPA não é novo recebimento).
- Saldos, compromissos e previsão: previsão de liquidez (`app.state.liquidity`).
- Custos por rubrica: `management_maps.purchase_documents`.
- Margem por artigo: linhas do SAF-T × custo médio das entradas de stock (só quando o custo é conhecido).

Cada bloco é calculado de forma independente: um erro num bloco não impede os restantes.
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .access import authorize, current, scope
from .db import db_session
from .models import (
    BankAccount,
    BankTransaction,
    CashSession,
    DocumentImport,
    DriveDocumentAttachment,
    ProcurementDocument,
    Purchase,
    PurchaseAttachment,
    Register,
    SaftImport,
    StockArticle,
    StockEntry,
    StockLot,
    StockWarehouse,
    WintouchSaleRecord,
    WintouchSyncRun,
)

router = APIRouter()
log = logging.getLogger(__name__)
LISBON = ZoneInfo("Europe/Lisbon")
FINANCE_ROLES = ("administrador", "gestor", "consulta")
COST_ROLES = ("administrador", "gestor")
MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def today():
    return datetime.now(LISBON).date()


def shift_year(day, years=-1):
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 de fevereiro
        return day.replace(year=day.year + years, day=28)


def comparison_range(start, end, mode):
    """Intervalo comparável e a metodologia usada, explicada ao utilizador."""
    if mode == "yoy":
        return shift_year(start), shift_year(end), "Mesmo período do ano anterior"
    if start.day == 1 and (start.year, start.month) == (end.year, end.month):
        previous_end = start - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
        month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if end == month_end:
            return previous_start, previous_end, "Mês anterior completo"
        # Mês em curso (ou parcial): os mesmos dias decorridos do mês anterior.
        last = min(previous_start + timedelta(days=(end - start).days), previous_end)
        return previous_start, last, f"Mesmos dias do mês anterior (1 a {end.day})"
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1), f"Período anterior com o mesmo número de dias ({days})"


def granularity(start, end):
    days = (end - start).days + 1
    return "day" if days <= 31 else "week" if days <= 120 else "month"


def bucket_start(day, unit):
    if unit == "day":
        return day
    if unit == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def buckets(start, end, unit):
    result, cursor = [], bucket_start(start, unit)
    while cursor <= end:
        if unit == "day":
            following = cursor + timedelta(days=1)
        elif unit == "week":
            following = cursor + timedelta(days=7)
        else:
            following = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        result.append((max(cursor, start), min(following - timedelta(days=1), end)))
        cursor = following
    return result


def bucket_label(first, last, unit):
    if unit == "day":
        return f"{first.day:02d}/{first.month:02d}"
    if unit == "week":
        return f"{first.day:02d}/{first.month:02d}–{last.day:02d}/{last.month:02d}"
    return f"{MONTHS[first.month - 1]} {first.year}"


def series(values_by_day, start, end, unit):
    return [sum(values_by_day.get((first + timedelta(days=i)).isoformat(), 0) for i in range((last - first).days + 1)) for first, last in buckets(start, end, unit)]


def change(current_value, previous_value):
    if previous_value in (None, 0) or current_value is None:
        return None
    return round((current_value - previous_value) / abs(previous_value) * 100, 1)


def meta(definition, formula, source, updated_at=None, limitations=""):
    return {"definition": definition, "formula": formula, "source": source, "updated_at": updated_at, "limitations": limitations}


def stamp(value):
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------------- vendas


def sales_source(db):
    from .billing_conference import saft_source
    source = saft_source(db)
    if source and db.scalar(select(SaftImport.id).where(SaftImport.status == "completed").limit(1)):
        last = db.scalar(select(func.max(SaftImport.created_at)).where(SaftImport.status == "completed"))
        return "saft", stamp(last)
    if db.scalar(select(WintouchSaleRecord.id).where(WintouchSaleRecord.kind == "documento").limit(1)):
        last = db.scalar(select(func.max(WintouchSyncRun.finished_at)).where(WintouchSyncRun.resource == "sales", WintouchSyncRun.status == "concluida"))
        return "erp", stamp(last)
    return None, None


def sales_by_day(db, stores, start, end, source):
    """{data: cêntimos sem IVA}, {data: cêntimos com IVA}, documentos e documentos sem base (ERP)."""
    net, gross, documents, missing = defaultdict(int), defaultdict(int), 0, 0
    if source == "saft":
        from .billing_conference import documents as saft_documents
        for doc in saft_documents(db, stores, str(start), str(end)):
            if doc["eligible"]:
                net[doc["date"]] += doc["net_cents"] * doc["direction"]
                gross[doc["date"]] += doc["gross_cents"] * doc["direction"]
                documents += 1
    elif source == "erp":
        for row in db.scalars(select(WintouchSaleRecord).where(
                WintouchSaleRecord.kind == "documento", WintouchSaleRecord.active.is_(True), WintouchSaleRecord.store_id.in_(stores),
                WintouchSaleRecord.document_date >= str(start), WintouchSaleRecord.document_date <= str(end))):
            data = json.loads(row.data)
            if data.get("document_group") != "vendas" or data.get("cancelled"):
                continue
            sign = data.get("direction") or 1
            gross[row.document_date] += int(data.get("amount_cents") or 0) * sign
            if data.get("net_cents") is None:
                missing += 1
            else:
                net[row.document_date] += int(data["net_cents"]) * sign
            documents += 1
    return net, gross, documents, missing


def sales_block(db, stores, period, compare):
    source, updated = sales_source(db)
    if not source:
        return {"status": "empty", "message": "Sem dados de faturação: importe o SAF-T de vendas ou ative a sincronização de vendas do ERP.",
                "meta": meta("Faturação do período", "—", "SAF-T de vendas ou ERP")}
    start, end = period
    current_net, current_gross, current_docs, current_missing = sales_by_day(db, stores, start, end, source)
    previous_net, previous_gross, previous_docs, previous_missing = sales_by_day(db, stores, compare[0], compare[1], source)
    # Sem IVA só quando todos os documentos dos dois períodos têm base; nunca se misturam bases.
    without_vat = not current_missing and not previous_missing
    current_values, previous_values = (current_net, previous_net) if without_vat else (current_gross, previous_gross)
    unit = granularity(start, end)
    current_series = series(current_values, start, end, unit)
    previous_series = series(previous_values, compare[0], compare[1], unit)
    labels = [bucket_label(first, last, unit) for first, last in buckets(start, end, unit)]
    total, previous_total = sum(current_values.values()), sum(previous_values.values())
    vat = "sem IVA" if without_vat else "com IVA"
    return {
        "status": "ok", "source": source, "vat_basis": vat, "unit": unit,
        "total_cents": total, "documents": current_docs, "previous_cents": previous_total if previous_docs else None,
        "previous_documents": previous_docs, "change_percent": change(total, previous_total) if previous_docs else None,
        "points": [{"label": label, "current_cents": value, "previous_cents": previous_series[i] if i < len(previous_series) else None}
                   for i, (label, value) in enumerate(zip(labels, current_series))],
        "meta": meta(
            f"Faturação do período ({vat}).",
            "Soma dos documentos FT, FS, FR, ND e NC em estado normal, pela data do documento; notas de crédito subtraem; anulados excluídos."
            if source == "saft" else "Soma dos documentos de venda do ERP não anulados, pela data do documento; devoluções subtraem. Sem IVA = soma das bases tributáveis indicadas pelo ERP (BaseValue1–4), só quando batem com o total.",
            "SAF-T de vendas importado" if source == "saft" else "Sincronização de vendas Wintouch", updated,
            "" if without_vat else f"{current_missing + previous_missing} documento(s) do ERP sincronizado(s) sem base tributável (sincronização anterior à atualização ou valores incoerentes): "
            "valores com IVA. Volte a sincronizar as vendas do período para obter a faturação líquida.",
        ),
        "missing_base_documents": current_missing + previous_missing,
    }


# --------------------------------------------------------------------- margem por artigo


def article_costs(db, article_ids):
    """Custo médio ponderado das entradas de stock com custo conhecido (None se desconhecido)."""
    totals = defaultdict(lambda: [Decimal(0), Decimal(0)])
    if article_ids:
        for entry in db.scalars(select(StockEntry).where(StockEntry.article_id.in_(article_ids), StockEntry.quantity > 0, StockEntry.unit_cost.is_not(None),
                                                         StockEntry.kind.in_(("purchase", "inventory", "opening")))):
            totals[entry.article_id][0] += entry.quantity
            totals[entry.article_id][1] += entry.quantity * entry.unit_cost
    return {key: value[1] / value[0] for key, value in totals.items() if value[0] > 0}


def margin_block(db, stores, period, can_view_cost):
    source, updated = sales_source(db)
    if source != "saft":
        return {"status": "empty", "message": "A análise por artigo exige o SAF-T de vendas (linhas por artigo).", "meta": meta("Vendas e margem por família de artigo", "—", "SAF-T")}
    from .billing_conference import saft_source
    start, end = period
    lines = []
    for row in db.scalars(select(WintouchSaleRecord).where(
            WintouchSaleRecord.enterprise_id == saft_source(db), WintouchSaleRecord.kind == "saft_invoice", WintouchSaleRecord.active.is_(True),
            WintouchSaleRecord.store_id.in_(stores), WintouchSaleRecord.document_date >= str(start), WintouchSaleRecord.document_date <= str(end))):
        data = json.loads(row.data)
        if data.get("status") != "N" or data.get("type") not in ("FT", "FS", "FR", "ND", "NC"):
            continue
        raw_lines = (data.get("raw") or {}).get("Line") or []
        for line in raw_lines if isinstance(raw_lines, list) else [raw_lines]:
            amount = line.get("CreditAmount") or line.get("DebitAmount") or "0"
            sign = -1 if line.get("DebitAmount") else 1
            try:
                net = int(Decimal(str(amount)) * 100) * sign
                quantity = Decimal(str(line.get("Quantity") or "0")) * sign
            except ArithmeticError:
                continue
            lines.append((str(line.get("ProductCode") or ""), str(line.get("ProductDescription") or ""), net, quantity))
    articles = {a.code: a for a in db.scalars(select(StockArticle).where(StockArticle.code.in_({code for code, *_ in lines})))} if lines else {}
    costs = article_costs(db, [a.id for a in articles.values()]) if can_view_cost else {}
    groups = defaultdict(lambda: {"sales_cents": 0, "known_cents": 0, "cost_cents": 0, "articles": set()})
    for code, description, net, quantity in lines:
        article = articles.get(code)
        detail = json.loads(article.data) if article else {}
        name = detail.get("family") or detail.get("category") or ("Sem família/categoria" if article else "Artigo sem ficha")
        group = groups[name]
        group["sales_cents"] += net
        group["articles"].add(code)
        cost = costs.get(article.id) if article else None
        if cost is not None:
            group["known_cents"] += net
            group["cost_cents"] += int(quantity * cost * 100)
    total = sum(g["sales_cents"] for g in groups.values())
    known = sum(g["known_cents"] for g in groups.values())
    ordered = sorted(groups.items(), key=lambda item: -item[1]["sales_cents"])
    top, rest = ordered[:8], ordered[8:]
    rows = [{"name": name, "sales_cents": g["sales_cents"], "articles": len(g["articles"]),
             "cost_coverage_percent": round(g["known_cents"] / g["sales_cents"] * 100) if g["sales_cents"] else None,
             "margin_cents": (g["known_cents"] - g["cost_cents"]) if can_view_cost and g["known_cents"] else None} for name, g in top]
    if rest:
        rows.append({"name": f"Outras ({len(rest)})", "sales_cents": sum(g["sales_cents"] for _, g in rest), "articles": sum(len(g["articles"]) for _, g in rest),
                     "cost_coverage_percent": None, "margin_cents": None})
    coverage = round(known / total * 100, 1) if total else None
    margin = sum(g["known_cents"] - g["cost_cents"] for g in groups.values()) if can_view_cost else None
    return {
        "status": "ok" if lines else "empty", "message": "" if lines else "Sem linhas de venda SAF-T neste período.",
        "rows": rows, "sales_cents": total, "cost_coverage_percent": coverage if can_view_cost else None,
        "gross_margin_cents": margin if can_view_cost and coverage and coverage >= 80 else None,
        "gross_margin_percent": round(margin / known * 100, 1) if can_view_cost and coverage and coverage >= 80 and known else None,
        "cost_restricted": not can_view_cost,
        "meta": meta(
            "Vendas sem IVA por família (ou categoria) do artigo e margem bruta quando o custo é conhecido.",
            "Margem bruta = vendas das linhas com custo conhecido − quantidade × custo médio ponderado das entradas de stock com custo. "
            "Só é apresentada com custo conhecido em pelo menos 80% das vendas.",
            "Linhas do SAF-T de vendas e entradas de stock (custo)", updated,
            "Margem bruta não é lucro: exclui despesas gerais, pessoal, impostos e outros custos. Compras de mercadoria não são tratadas como custo das vendas.",
        ),
    }


# ----------------------------------------------------------------------- tesouraria


def treasury_block(request, db, user, store_id, period, compare):
    from .billing_conference import payment_list, pending_settlement, receipts
    liquidity = request.app.state.liquidity
    stores = [store_id] if store_id else scope(user, db)
    start, end = period
    horizon = today() + timedelta(days=29)
    # Saldos, compromissos e previsão dependem da liquidez; os fluxos do período não.
    liquidity_error = ""
    try:
        accounts, events, warnings, _ = liquidity["source_data"](db, user, horizon, store_id)
        forecast = liquidity["forecast"](liquidity["input"](start=today(), end=horizon, store_id=store_id), user, db)
    except HTTPException as exc:
        db.rollback()
        accounts, events, warnings, forecast = [], [], [], {"groups": {}, "warnings": []}
        liquidity_error = f"Saldos e compromissos indisponíveis: {exc.detail}"
    eur = [a for a in accounts if a["currency"] == "EUR"]
    other = sorted({a["currency"] for a in accounts if a["currency"] != "EUR"})
    known = [a for a in eur if a["balance_cents"] is not None]
    receipts_rows = receipts(db, stores, str(start), str(end))
    payments_rows = payment_list(db, stores, str(start), str(end))
    previous_receipts = receipts(db, stores, str(compare[0]), str(compare[1]))
    previous_payments = payment_list(db, stores, str(compare[0]), str(compare[1]))
    received = sum(r["amount_cents"] * r["direction"] for r in receipts_rows)
    paid = sum(p["amount_cents"] for p in payments_rows)
    unit = granularity(start, end)
    by_day_in, by_day_out = defaultdict(int), defaultdict(int)
    for r in receipts_rows:
        by_day_in[r["date"]] += r["amount_cents"] * r["direction"]
    for p in payments_rows:
        by_day_out[p["date"]] += p["amount_cents"]
    labels = [bucket_label(first, last, unit) for first, last in buckets(start, end, unit)]
    flows = [{"label": label, "in_cents": i, "out_cents": o} for label, i, o in zip(labels, series(by_day_in, start, end, unit), series(by_day_out, start, end, unit))]
    # Compromissos: eventos da previsão (saldo por liquidar), sem transferências internas.
    stamp_today = today()
    due = {"payable": defaultdict(int), "receivable": defaultdict(int)}
    counts = {"payable": defaultdict(int), "receivable": defaultdict(int)}
    for event in events:
        if event.get("transfer") or event.get("currency", "EUR") != "EUR" or event["amount_cents"] <= 0:
            continue
        side = "payable" if event["direction"] == -1 else "receivable"
        if not event.get("due_date"):
            key = "sem_data"
        else:
            days = (date.fromisoformat(event["due_date"]) - stamp_today).days
            key = "vencido" if days < 0 else "7_dias" if days <= 7 else "30_dias" if days <= 30 else "mais_30"
        due[side][key] += event["amount_cents"]
        counts[side][key] += 1
    group = forecast["groups"].get("EUR")
    pending = pending_settlement(db, stores, str(stamp_today))
    last_statement = db.scalar(select(func.max(BankTransaction.created_at)).join(BankAccount).where(BankAccount.store_id.in_(stores)))
    return {
        "status": "ok", "liquidity_error": liquidity_error,
        "balance_cents": sum(a["balance_cents"] for a in known) if known else None,
        "balance_complete": len(known) == len(eur),
        "accounts": [{"name": a["name"], "kind": a["kind"], "balance_cents": a["balance_cents"], "balance_date": a["balance_date"], "stale": a["stale"], "source": a["source"]} for a in eur],
        "other_currencies": other,
        "card_pending_cents": pending["amount_cents"], "card_pending_count": pending["count"],
        "received_cents": received, "paid_cents": paid,
        "previous_received_cents": sum(r["amount_cents"] * r["direction"] for r in previous_receipts),
        "previous_paid_cents": sum(p["amount_cents"] for p in previous_payments),
        "flows": flows, "unit": unit,
        "due": {side: {key: {"amount_cents": due[side][key], "count": counts[side][key]} for key in ("vencido", "7_dias", "30_dias", "mais_30", "sem_data")} for side in due},
        "forecast": {
            "available": bool(group and group["daily"]),
            "points": [{"date": row["date"], "closing_cents": row["closing_cents"], "in_cents": row["incoming_cents"], "out_cents": row["outgoing_cents"]} for row in (group or {}).get("daily", [])],
            "initial_cents": (group or {}).get("initial_cents"), "minimum_cents": (group or {}).get("minimum_cents"), "minimum_date": (group or {}).get("minimum_date"),
            "reserve_cents": (group or {}).get("reserve_cents"), "first_below": (group or {}).get("first_below"), "incomplete": (group or {}).get("incomplete", True),
        },
        "warnings": list(dict.fromkeys(([liquidity_error] if liquidity_error else []) + warnings + forecast.get("warnings", [])))[:8],
        "meta": {
            "balance": meta("Saldo disponível conhecido em caixas e contas (EUR).", "Por conta: último saldo do extrato importado ou saldo de referência local; caixas: fundo + movimentos. Um valor por conta (nunca extrato + livro da mesma conta).",
                            "Previsão de liquidez (contas e caixas)", stamp(last_statement), "Saldos estimados ou desatualizados são assinalados. Outras moedas não são convertidas nem somadas."),
            "flows": meta("Recebimentos e pagamentos externos efetivamente registados.", "Recebimentos (caixas, fechos, registos bancários aprovados) menos reembolsos; pagamentos a fornecedores, despesas, saídas de caixa e encargos bancários, pela data do movimento.",
                          "Movimentos reais e livro bancário", None, "Exclui transferências entre contas próprias e estornos. A liquidação bancária do TPA não é um novo recebimento. Valores com IVA."),
            "due": meta("Valores por receber e por pagar, por vencimento.", "Saldos por liquidar dos movimentos previstos, documentos do ERP em aberto e previsões manuais.", "Previsão de liquidez", None,
                        "Uma fatura registada não está paga por estar registada: conta o saldo por liquidar. Transferências internas excluídas."),
            "forecast": meta("Saldo previsto para os próximos 30 dias (cenário base).", "Saldo inicial conhecido + entradas − saídas previstas por data esperada.", "Previsão de liquidez", None,
                             "Valores vencidos sem nova data não entram na projeção; ver Previsão de liquidez."),
        },
    }


# -------------------------------------------------------------------------- despesas


def expenses_block(db, stores, period, compare):
    from .management_maps import UNCLASSIFIED, deductible, purchase_documents
    from .models import Category
    from .settings import vat_settings
    regime = vat_settings(db)["vat_regime"]
    categories = {c.id: c for c in db.scalars(select(Category))}
    percents = {c.id: c.vat_deduction_percent for c in categories.values()}

    def totals(first, last):
        result = defaultdict(int)
        for _, _, shares in purchase_documents(db, stores, first, last):
            for category, base, tax in shares:
                result[category] += base + (tax - deductible(tax, category, percents) if regime == "normal" else tax)
        return result
    current_values, previous_values = totals(*period), totals(*compare)
    ordered = sorted(current_values, key=lambda key: -current_values[key])
    rows = [{"category_id": key or None, "name": categories[key].name if key in categories else "Por classificar",
             "amount_cents": current_values[key], "previous_cents": previous_values.get(key, 0)} for key in ordered if current_values[key]]
    top, rest = rows[:8], rows[8:]
    if rest:
        top.append({"category_id": "outras", "name": f"Outras ({len(rest)})", "amount_cents": sum(r["amount_cents"] for r in rest), "previous_cents": sum(r["previous_cents"] for r in rest)})
    total, previous = sum(current_values.values()), sum(previous_values.values())
    return {
        "status": "ok" if rows else "empty", "message": "" if rows else "Sem documentos de compra neste período.",
        "rows": top, "total_cents": total, "previous_cents": previous, "change_percent": change(total, previous),
        "unclassified_cents": current_values.get(UNCLASSIFIED, 0), "vat_basis": "sem IVA dedutível" if regime == "normal" else "com IVA",
        "meta": meta("Despesas e compras por rubrica, pela data do documento.",
                     "Regime normal: base tributável + IVA não dedutível estimado; restantes regimes: total com IVA. Rubrica das linhas da compra, do documento ERP ou do fornecedor.",
                     "Documentos de compra (e-Fatura, Compras, ERP)", None,
                     "Inclui faturas do e-Fatura ainda não lançadas. Compras de mercadoria contam como compras, não como custo das vendas." + ("" if regime else " Configure o regime de IVA.")),
    }


# -------------------------------------------------------------------------- atenção


def operations_block(db, stores, period, costs_allowed):
    start, end = period
    stamp_today = today()
    items = {}
    registers = {r.id: r for r in db.scalars(select(Register).where(Register.store_id.in_(stores)))}
    sessions = list(db.scalars(select(CashSession).where(CashSession.register_id.in_(registers))))
    stale_open = [s for s in sessions if s.closed_at is None and (s.sheet_day or s.opened_at.astimezone(LISBON).date().isoformat()) < stamp_today.isoformat()]
    differences = [s for s in sessions if s.closed_at and s.counted_cents is not None and s.expected_cents is not None and s.counted_cents != s.expected_cents
                   and str(start) <= (s.sheet_day or s.closed_at.astimezone(LISBON).date().isoformat()) <= str(end)]
    items["cash_open"] = {"count": len(stale_open), "registers": sorted({registers[s.register_id].name for s in stale_open})}
    items["cash_differences"] = {"count": len(differences), "amount_cents": sum(abs(s.counted_cents - s.expected_cents) for s in differences)}
    # Movimentos bancários com mais de 7 dias ainda por reconciliar (últimos 180 dias).
    from .models import ReconciliationIgnore
    from .reconciliation import used
    ignored = set(db.scalars(select(ReconciliationIgnore.source_id).where(ReconciliationIgnore.source == "bank")))
    unreconciled, amount = 0, 0
    for tx in db.scalars(select(BankTransaction).join(BankAccount).where(
            BankAccount.store_id.in_(stores), BankTransaction.movement_date <= (stamp_today - timedelta(days=7)).isoformat(),
            BankTransaction.movement_date >= (stamp_today - timedelta(days=180)).isoformat())):
        if tx.id in ignored:
            continue
        missing = abs(tx.amount_cents) - used(db, "bank", tx.id)
        if missing > 0:
            unreconciled += 1
            amount += missing
    items["bank_unreconciled"] = {"count": unreconciled, "amount_cents": amount}
    if costs_allowed:
        purchases = list(db.scalars(select(Purchase).where(Purchase.store_id.in_(stores), Purchase.document_date >= str(start), Purchase.document_date <= str(end),
                                                           Purchase.origin != "efatura_testes", Purchase.amount_cents != 0)))
        ids = [p.id for p in purchases]
        with_attachment = set(db.scalars(select(PurchaseAttachment.purchase_id).where(PurchaseAttachment.purchase_id.in_(ids)))) | set(
            db.scalars(select(DriveDocumentAttachment.purchase_id).where(DriveDocumentAttachment.purchase_id.in_(ids)))) if ids else set()
        items["documents_without_attachment"] = {"count": sum(p.id not in with_attachment for p in purchases), "total": len(purchases)}
        efatura = set(db.scalars(select(DocumentImport.id).where(DocumentImport.source == "ficheiro_efatura")))
        launched = {d.purchase_id or json.loads(d.data).get("purchase_id") for d in db.scalars(select(ProcurementDocument).where(ProcurementDocument.status != "cancelled"))}
        from .models import DocumentERPLink
        linked = set(db.scalars(select(DocumentERPLink.purchase_id).where(DocumentERPLink.undone_at.is_(None))))
        pending = [p for p in purchases if (p.origin in ("ficheiro", "efatura") or p.import_id in efatura) and p.id not in launched and p.id not in linked]
        items["efatura_unlaunched"] = {"count": len(pending), "amount_cents": sum(p.amount_cents for p in pending)}
    # Stock: abaixo do mínimo e lotes com validade próxima (quantidade em armazéns do âmbito).
    warehouses = list(db.scalars(select(StockWarehouse.id).where(StockWarehouse.store_id.in_(stores))))
    below, expiring = 0, []
    if warehouses:
        quantities = dict(db.execute(select(StockEntry.article_id, func.sum(StockEntry.quantity)).where(StockEntry.warehouse_id.in_(warehouses)).group_by(StockEntry.article_id)).all())
        for article in db.scalars(select(StockArticle).where(StockArticle.active.is_(True))):
            minimum = Decimal(str(json.loads(article.data).get("minimum") or 0))
            if minimum > 0 and Decimal(quantities.get(article.id) or 0) < minimum:
                below += 1
        lots = dict(db.execute(select(StockEntry.lot_id, func.sum(StockEntry.quantity)).where(StockEntry.warehouse_id.in_(warehouses), StockEntry.lot_id.is_not(None)).group_by(StockEntry.lot_id)).all())
        limit = (stamp_today + timedelta(days=30)).isoformat()
        for lot in db.scalars(select(StockLot).where(StockLot.id.in_([k for k, v in lots.items() if v and v > 0]), StockLot.expiry.is_not(None), StockLot.expiry <= limit)) if lots else []:
            expiring.append({"expiry": lot.expiry, "quantity": str(lots[lot.id])})
    items["stock_below_minimum"] = {"count": below}
    items["stock_expiring"] = {"count": len(expiring), "expired": sum(e["expiry"] < stamp_today.isoformat() for e in expiring)}
    return {"status": "ok", "items": items}


def attention(sales, treasury, expenses, operations, mode, period_label):
    """Observações com regras verificáveis: facto, valores, impacto, ação e destino."""
    notes = []

    def add(severity, key, fact, values, impact, action, page, filters=None):
        notes.append({"severity": severity, "key": key, "fact": fact, "values": values, "impact": impact, "action": action, "page": page, "filters": filters or {}})

    def euros(cents):
        return f"{cents / 100:,.2f} €".replace(",", " ").replace(".", ",")

    if treasury.get("status") == "ok":
        due = treasury["due"]
        if due["payable"]["vencido"]["count"]:
            add("critical", "overdue_payables", f"{due['payable']['vencido']['count']} pagamento(s) vencido(s) por liquidar.", euros(due["payable"]["vencido"]["amount_cents"]),
                "Atrasos podem gerar juros, cortes de fornecimento ou perda de condições.", "Confirme se já foram pagos e registe o pagamento ou defina nova data.", "Movimentos previstos")
        if due["payable"]["7_dias"]["count"]:
            add("warning", "due_7", f"{due['payable']['7_dias']['count']} pagamento(s) vencem nos próximos 7 dias.", euros(due["payable"]["7_dias"]["amount_cents"]),
                "Saída de tesouraria a curto prazo.", "Confirme a disponibilidade e planeie os pagamentos.", "Movimentos previstos")
        if due["receivable"]["vencido"]["count"]:
            add("warning", "overdue_receivables", f"{due['receivable']['vencido']['count']} recebimento(s) vencido(s) por receber.", euros(due["receivable"]["vencido"]["amount_cents"]),
                "Dinheiro esperado que ainda não entrou.", "Contacte os clientes ou atualize a data esperada.", "Previsão de liquidez")
        below = treasury["forecast"].get("first_below")
        if below:
            add("critical", "reserve", f"O saldo previsto fica abaixo da reserva mínima em {below['date'][8:10]}/{below['date'][5:7]}.", euros(below["shortfall_cents"]) + " em falta",
                "Risco de não cumprir pagamentos previstos.", "Reveja datas de pagamento, recebimentos esperados ou financiamento.", "Previsão de liquidez")
        stale = [a["name"] for a in treasury["accounts"] if a["stale"] and not a["name"].startswith("Caixa")]
        if stale:
            add("warning", "stale_banks", f"{len(stale)} conta(s) bancária(s) sem extrato recente.", ", ".join(stale[:3]),
                "Os saldos e a previsão podem não refletir a realidade.", "Importe o extrato mais recente.", "Extratos")
        if treasury["card_pending_count"]:
            add("info", "card_pending", f"{treasury['card_pending_count']} recebimento(s) TPA/MB WAY a aguardar liquidação bancária.", euros(treasury["card_pending_cents"]),
                "Valor já recebido do cliente, ainda não creditado no banco (não é um segundo recebimento).", "Reconcilie com os créditos do extrato.", "Reconciliação automática")
    if sales.get("status") == "ok" and sales.get("change_percent") is not None and sales["change_percent"] <= -15 and (sales["previous_cents"] or 0) > 0:
        add("warning", "sales_drop", f"Faturação {abs(sales['change_percent']):.1f}% abaixo do período comparável.", f"{euros(sales['total_cents'])} vs {euros(sales['previous_cents'])} ({sales['vat_basis']}, {period_label})",
            "Menos receita para cobrir custos fixos.", "Analise a evolução por dia e por família de artigos.", "Faturação e recebimentos" if mode == "local" else "Movimentos reais")
    if expenses and expenses.get("status") == "ok" and expenses.get("change_percent") is not None and expenses["change_percent"] >= 20 and expenses["total_cents"] - expenses["previous_cents"] >= 10000:
        grown = sorted(expenses["rows"], key=lambda r: -(r["amount_cents"] - r["previous_cents"]))[0]
        add("warning", "expenses_up", f"Despesas {expenses['change_percent']:.1f}% acima do período comparável.", f"{euros(expenses['total_cents'])} vs {euros(expenses['previous_cents'])}; maior aumento: {grown['name']}",
            "Pressão sobre a margem.", "Reveja as compras da rubrica com maior aumento.", "Despesas por rubrica")
    if expenses and expenses.get("unclassified_cents"):
        add("info", "unclassified", "Há compras sem rubrica.", euros(expenses["unclassified_cents"]), "A análise de despesas fica incompleta.", "Indique a rubrica do fornecedor ou do documento.", "Despesas por rubrica")
    items = operations.get("items", {}) if operations.get("status") == "ok" else {}
    if items.get("cash_open", {}).get("count"):
        add("warning", "cash_open", f"{items['cash_open']['count']} caixa(s) de dias anteriores por fechar.", ", ".join(items["cash_open"]["registers"][:3]),
            "Saldos de numerário e conferências desatualizados.", "Conte e feche as caixas pendentes.", "Folha de caixa")
    if items.get("cash_differences", {}).get("count"):
        add("warning", "cash_differences", f"{items['cash_differences']['count']} fecho(s) de caixa com diferenças no período.", euros(items["cash_differences"]["amount_cents"]) + " (soma das diferenças)",
            "Possíveis erros de registo ou faltas de numerário.", "Reveja as justificações dos fechos.", "Folha de caixa")
    if items.get("bank_unreconciled", {}).get("count"):
        add("info", "unreconciled", f"{items['bank_unreconciled']['count']} movimento(s) bancário(s) com mais de 7 dias por reconciliar.", euros(items["bank_unreconciled"]["amount_cents"]),
            "Pagamentos e recebimentos podem não estar identificados.", "Reconcilie os movimentos.", "Reconciliação automática")
    if items.get("documents_without_attachment", {}).get("count"):
        info = items["documents_without_attachment"]
        add("info", "no_attachment", f"{info['count']} de {info['total']} documento(s) de compra do período sem anexo.", period_label,
            "Documentos em falta para a contabilidade.", "Procure os anexos ou peça-os aos fornecedores.", "Conferência documental")
    if items.get("efatura_unlaunched", {}).get("count"):
        add("info", "unlaunched", f"{items['efatura_unlaunched']['count']} fatura(s) do e-Fatura por lançar.", euros(items["efatura_unlaunched"]["amount_cents"]),
            "Compras e vencimentos em falta no plano de pagamentos.", "Lance-as a partir das propostas da conferência.", "Conferência documental")
    if items.get("stock_expiring", {}).get("count"):
        info = items["stock_expiring"]
        add("warning", "expiring", f"{info['count']} lote(s) com stock e validade nos próximos 30 dias" + (f" ({info['expired']} já expirados)." if info["expired"] else "."), "",
            "Risco de quebra por validade.", "Priorize a venda ou devolva ao fornecedor.", "Compras e Stock — Visão geral")
    if items.get("stock_below_minimum", {}).get("count"):
        add("info", "below_minimum", f"{items['stock_below_minimum']['count']} artigo(s) abaixo do stock mínimo.", "", "Risco de rutura.", "Prepare encomendas.", "Compras e Stock — Visão geral")
    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(notes, key=lambda n: order[n["severity"]])


def run(db, label, function, *args):
    try:
        result = function(*args)
        db.commit()
        return result
    except HTTPException as exc:
        db.rollback()
        return {"status": "error", "message": exc.detail}
    except Exception:
        db.rollback()
        log.exception("Falha no bloco %s do dashboard", label)
        return {"status": "error", "message": "Não foi possível calcular este bloco. Atualize para tentar novamente."}


@router.get("/api/dashboard/overview")
def overview(request: Request, start: date | None = None, end: date | None = None, store_id: str = "", compare: str = "previous",
             user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db)
    if compare not in ("previous", "yoy"):
        raise HTTPException(422, "Compare com o período anterior ou homólogo.")
    end = end or today()
    start = start or end.replace(day=1)
    if start > end or (end - start).days > 400:
        raise HTTPException(422, "Selecione um período até 400 dias.")
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        stores = [store_id] if store_id in stores else []
    from .operating_modes import policy
    mode = policy(db).get("mode", "local")
    previous_start, previous_end, method = comparison_range(start, end, compare)
    period, compared = (start, end), (previous_start, previous_end)
    finance = user.role in FINANCE_ROLES
    costs_allowed = user.role in COST_ROLES
    from .inventory import permissions
    from .inventory import settings as stock_settings
    can_view_cost = costs_allowed and permissions(user, stock_settings(db)).get("view_cost", False)
    restricted = {"status": "restricted", "message": "O seu perfil não tem acesso a estes dados."}
    sales = run(db, "sales", sales_block, db, stores, period, compared) if finance else restricted
    treasury = run(db, "treasury", treasury_block, request, db, user, store_id, period, compared) if finance else restricted
    expenses = run(db, "expenses", expenses_block, db, stores, period, compared) if costs_allowed else restricted
    margin = run(db, "margin", margin_block, db, stores, period, can_view_cost) if costs_allowed else restricted
    operations = run(db, "operations", operations_block, db, stores, period, costs_allowed)
    label = f"{start.strftime('%d/%m/%Y')} a {end.strftime('%d/%m/%Y')}"
    return {
        "generated_at": datetime.now(LISBON).isoformat(), "mode": mode,
        "period": {"start": str(start), "end": str(end), "label": label, "in_progress": end >= today()},
        "comparison": {"start": str(previous_start), "end": str(previous_end), "method": method, "mode": compare},
        "permissions": {"finance": finance, "costs": costs_allowed, "article_costs": can_view_cost},
        "sales": sales, "treasury": treasury, "expenses": expenses, "margin": margin, "operations": operations,
        "attention": attention(sales, treasury, expenses if costs_allowed else None, operations, mode, label),
    }
