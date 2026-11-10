"""Margem bruta histórica das vendas e cobertura de custos (dashboard).

Custo de cada linha vendida = custo das mercadorias vendidas pelo método de valorização do stock
(custo médio ponderado móvel da empresa):

1. «stock»: o custo gravado na saída de stock dessa linha (venda Wintouch ou SAF-T integrada no stock).
   É imutável, como o próprio movimento;
2. «estimado»: sem essa saída, o custo médio móvel reconstituído com os movimentos do artigo até à data
   da venda (entradas valorizadas, regularizações de valor), com a mesma regra do stock.

Nunca se usa o último custo de compra. Os serviços ficam fora da margem e da cobertura (apresentados à
parte). Custo zero é um custo conhecido (por exemplo, oferta), apenas assinalado para revisão.
A margem comercial de referência e a margem efetiva de receção pertencem à revisão de preços.
"""

import json
from collections import defaultdict
from decimal import ROUND_HALF_UP
from decimal import Decimal as D

from sqlalchemy import select

from . import inventory as stock
from .models import StockArticle, StockEntry, WintouchProduct

REASONS = {
    "no_cost": "Artigo sem custo registado",
    "zero_cost": "Custo igual a zero",
    "no_history": "Custo histórico indisponível à data da venda",
    "no_match": "Artigo sem correspondência com a ficha local",
    "unidentified": "Artigo não identificado",
    "inactive": "Artigo inativo sem custo",
    "incomplete": "Dados incompletos: entrada sem custo no stock",
}
SOURCES = {
    "stock": "Saída de stock (custo médio à data da venda)",
    "estimated": "Custo médio reconstituído à data da venda",
}
# Cobertura: completa (verde), parcial (âmbar) e crítica (vermelho) abaixo de 80 %.
PARTIAL_FROM = 80


def cents(value):
    return int(D(value).quantize(D(1), rounding=ROUND_HALF_UP))


def sale_lines(db, stores, period, source):
    """Linhas por artigo do período, com o artigo local e a chave da saída de stock."""
    from .dashboard import erp_sale_line_records, saft_sale_line_records
    from .wintouch_stock import line_key
    if source == "saft":
        records = saft_sale_line_records(db, stores, period)
        mapping = stock.settings(db)["saft_mapping"]
        codes = {r["code"] for r in records if r["code"]}
        by_code = {a.code: a for a in db.scalars(select(StockArticle).where(StockArticle.code.in_(codes)))} if codes else {}
        mapped = {mapping[code] for code in codes if code in mapping}
        by_id = {a.id: a for a in db.scalars(select(StockArticle).where(StockArticle.id.in_(mapped)))} if mapped else {}
        result = []
        for record in records:
            article = by_id.get(mapping.get(record["code"])) or by_code.get(record["code"])
            result.append({**record, "key": article.id if article else "saft:" + (record["code"] or record["description"]),
                           "article": article, "product": None, "reason": None if article else "no_match"})
        return result, 0
    records, missing = erp_sale_line_records(db, stores, period)
    keys = {(r["enterprise"], r["product_id"]) for r in records}
    products = {(p.enterprise_id, p.external_id): p for p in db.scalars(select(WintouchProduct).where(
        WintouchProduct.enterprise_id.in_({enterprise for enterprise, _ in keys}),
        WintouchProduct.external_id.in_({product for _, product in keys}),
    ))} if keys else {}
    ids = {p.id for p in products.values()}
    articles = {a.erp_id: a for a in db.scalars(select(StockArticle).where(StockArticle.erp_id.in_(ids)))} if ids else {}
    result = []
    for record in records:
        product = products.get((record["enterprise"], record["product_id"]))
        article = articles.get(product.id) if product else None
        result.append({
            "date": record["date"], "net": cents(record["net"]), "quantity": record["quantity"],
            "code": article.code if article else product.code if product else f"ERP:{record['product_id']}",
            "description": article.name if article else product.name if product else f"Artigo ERP {record['product_id']}",
            "stock_key": line_key(record["enterprise"], record["line_id"]) if record["line_id"] else None,
            "key": article.id if article else f"erp:{record['enterprise']}:{record['product_id']}",
            "article": article, "product": product,
            "reason": None if article else "no_match" if product else "unidentified",
        })
    return result, missing


def cost_at(rows, day):
    """Custo médio móvel do artigo à data, com as regras do stock; e se já havia entradas até essa data.

    Uma entrada sem custo contamina o custo até o stock esgotar; ao esgotar, a entrada seguinte recomeça
    o custo. As saídas não alteram o custo médio."""
    quantity, cost, seen = D(0), None, False
    for row in rows:
        if row.occurred_on > day or row.kind in ("transfer_in", "transfer_out"):
            continue
        if row.kind == "valuation":
            if quantity > 0 and cost is not None:
                cost += D(json.loads(row.data).get("inventory_value_delta", "0")) / quantity
            continue
        if row.quantity > 0:
            seen = True
            if quantity <= 0:
                cost = row.unit_cost
            elif cost is not None and row.unit_cost is not None:
                cost = (quantity * cost + row.quantity * row.unit_cost) / (quantity + row.quantity)
            else:
                cost = None
        quantity += row.quantity
    return cost, seen


def attach_costs(db, lines):
    """Custo por linha (`cost`, `cost_source`) e, sem custo, o motivo (`reason`)."""
    ids = {line["article"].id for line in lines if line["article"]}
    history = defaultdict(list)
    for entry in db.scalars(select(StockEntry).where(StockEntry.article_id.in_(ids)).order_by(StockEntry.created_at, StockEntry.id)) if ids else []:
        history[entry.article_id].append(entry)
    posted = defaultdict(list)
    for rows in history.values():
        for entry in rows:
            if entry.kind in ("sale", "customer_return") and entry.request_key.startswith(("wt-stock:", "saft-stock:")):
                posted[entry.request_key.split("~")[0]].append(entry)
    for line in lines:
        line.update(cost=None, cost_source=None, service=False)
        article = line["article"]
        if not article:
            continue
        if json.loads(article.data).get("service"):
            line["service"] = True
            continue
        snapshot = posted.get(line["stock_key"]) if line["stock_key"] else None
        if snapshot and all(entry.unit_cost is not None for entry in snapshot):
            units = sum(abs(entry.quantity) for entry in snapshot)
            if units > 0:
                line.update(cost=sum(abs(entry.quantity) * entry.unit_cost for entry in snapshot) / units, cost_source="stock")
                continue
        cost, seen = cost_at(history[article.id], line["date"])
        if cost is not None:
            line.update(cost=cost, cost_source="estimated")
        elif not article.active:
            line["reason"] = "inactive"
        elif seen:
            line["reason"] = "incomplete"
        else:
            later = any(entry.quantity > 0 for entry in history[article.id])
            line["reason"] = "no_history" if later else "no_cost"


def erp_state(article, product, erp_products):
    """Estado de sincronização ERP do artigo, para a grelha."""
    product = product or (erp_products.get(article.erp_id) if article and article.erp_id else None)
    if product:
        if not article:
            return "Sem ficha local"
        if not product.active:
            return "Inativo na Wintouch"
        return f"Sincronizado {product.synced_at.strftime('%d/%m/%Y')}" if product.synced_at else "Sincronizado"
    return "Artigo local" if article else "Sem correspondência"


def analyse(db, stores, period, source, can_view_cost):
    """Margem, cobertura, motivos, famílias, artigos e evolução por período do intervalo."""
    from .dashboard import bucket_label, buckets, granularity, series
    lines, missing = sale_lines(db, stores, period, source)
    if can_view_cost:
        attach_costs(db, lines)
    else:
        for line in lines:
            line.update(cost=None, cost_source=None, service=bool(line["article"] and json.loads(line["article"].data).get("service")))
    erp_ids = {line["article"].erp_id for line in lines if line["article"] and line["article"].erp_id}
    erp_products = {p.id: p for p in db.scalars(select(WintouchProduct).where(WintouchProduct.id.in_(erp_ids)))} if erp_ids else {}
    articles, families = {}, {}
    sales_by_day, covered_by_day, profit_by_day = defaultdict(int), defaultdict(int), defaultdict(int)
    totals = defaultdict(int)
    for line in lines:
        article = line["article"]
        detail = json.loads(article.data) if article else {}
        family = detail.get("family") or detail.get("category") or ("Sem família/categoria" if article else "Artigo sem ficha")
        known = line["cost"] is not None
        cost_cents = cents(line["quantity"] * line["cost"] * 100) if known else 0
        if line["service"]:
            totals["services"] += line["net"]
            totals["service_lines"] += 1
            continue
        totals["sales"] += line["net"]
        sales_by_day[line["date"]] += line["net"]
        reason = None
        if known:
            totals["covered"] += line["net"]
            totals["cost"] += cost_cents
            covered_by_day[line["date"]] += line["net"]
            profit_by_day[line["date"]] += line["net"] - cost_cents
            if line["cost_source"] == "estimated":
                totals["estimated"] += line["net"]
            if line["cost"] == 0:
                reason = "zero_cost"
                totals["zero"] += line["net"]
        else:
            reason = line["reason"] or "no_cost"
        row = articles.setdefault(line["key"], {
            "key": line["key"], "article_id": article.id if article else None, "code": line["code"],
            "name": article.name if article else line["description"] or line["code"], "family": family,
            "active": article.active if article else None, "erp_status": erp_state(article, line["product"], erp_products),
            "quantity": D(0), "sales_cents": 0, "covered_cents": 0, "cost_cents": 0, "covered_quantity": D(0),
            "sources": set(), "reasons": set(),
        })
        row["quantity"] += line["quantity"]
        row["sales_cents"] += line["net"]
        if known:
            row["covered_cents"] += line["net"]
            row["cost_cents"] += cost_cents
            row["covered_quantity"] += line["quantity"]
            row["sources"].add(line["cost_source"])
        if reason:
            row["reasons"].add(reason)
        group = families.setdefault(family, {"name": family, "sales_cents": 0, "covered_cents": 0, "cost_cents": 0, "articles": set()})
        group["sales_cents"] += line["net"]
        group["articles"].add(line["key"])
        if known:
            group["covered_cents"] += line["net"]
            group["cost_cents"] += cost_cents
    rows = []
    for row in articles.values():
        covered = row["covered_quantity"] != 0
        margin = row["covered_cents"] - row["cost_cents"] if covered else None
        reasons = sorted(row.pop("reasons"))
        sources = row.pop("sources")
        rows.append({
            **row, "quantity": str(row["quantity"]), "covered_quantity": str(row["covered_quantity"]),
            "uncovered_cents": row["sales_cents"] - row["covered_cents"],
            "unit_cost": str((D(row["cost_cents"]) / 100 / row["covered_quantity"]).quantize(D("0.0001"))) if covered else None,
            "cost_source": None if not sources else "mixed" if len(sources) > 1 else next(iter(sources)),
            "margin_cents": margin, "margin_percent": round(margin / row["covered_cents"] * 100, 1) if margin is not None and row["covered_cents"] else None,
            "reasons": reasons, "needs_review": bool(reasons),
        })
    rows.sort(key=lambda r: (-r["sales_cents"], r["code"]))
    sales, covered = totals["sales"], totals["covered"]
    coverage = round(covered / sales * 100, 1) if sales > 0 else None
    profit = covered - totals["cost"] if covered else None
    level = None if coverage is None else "complete" if coverage >= 100 else "partial" if coverage >= PARTIAL_FROM else "critical"
    reason_totals = defaultdict(lambda: {"sales_cents": 0, "articles": 0})
    for row in rows:
        for reason in row["reasons"]:
            reason_totals[reason]["articles"] += 1
            reason_totals[reason]["sales_cents"] += row["covered_cents"] if reason == "zero_cost" else row["uncovered_cents"]
    start, end = period
    unit = granularity(start, end)
    labels = [bucket_label(first, last, unit) for first, last in buckets(start, end, unit)]
    points = []
    for label, value, known, gain in zip(labels, series(sales_by_day, start, end, unit), series(covered_by_day, start, end, unit), series(profit_by_day, start, end, unit)):
        points.append({"label": label, "sales_cents": value, "covered_cents": known,
                       "margin_cents": gain if known else None, "margin_percent": round(gain / known * 100, 1) if known else None,
                       "coverage_percent": round(known / value * 100, 1) if value > 0 else None})
    family_rows = sorted(families.values(), key=lambda g: -g["sales_cents"])
    return {
        "lines": len(lines), "incomplete_documents": missing, "unit": unit, "points": points,
        "sales_cents": sales, "covered_cents": covered, "uncovered_cents": sales - covered, "cost_cents": totals["cost"],
        "cost_coverage_percent": coverage, "gross_margin_cents": profit,
        "gross_margin_percent": round(profit / covered * 100, 1) if profit is not None and covered else None,
        "level": level, "partial": level != "complete",
        "estimated_cents": totals["estimated"], "zero_cost_cents": totals["zero"],
        "services_cents": totals["services"], "service_lines": totals["service_lines"],
        "review_articles": sum(1 for r in rows if r["needs_review"]),
        "negative_articles": sum(1 for r in rows if r["margin_cents"] is not None and r["margin_cents"] < 0),
        "reasons": [{"reason": key, "label": REASONS[key], **value} for key, value in sorted(reason_totals.items(), key=lambda item: -item[1]["sales_cents"])],
        "families": [{"name": g["name"], "sales_cents": g["sales_cents"], "articles": len(g["articles"]), "covered_cents": g["covered_cents"],
                      "cost_coverage_percent": round(g["covered_cents"] / g["sales_cents"] * 100) if g["sales_cents"] else None,
                      "margin_cents": g["covered_cents"] - g["cost_cents"] if g["covered_cents"] else None} for g in family_rows],
        "articles": rows,
    }
