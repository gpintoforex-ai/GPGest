"""Mapas de gestão indicativos: custos e proveitos, e mapa de IVA por período.

Proveitos e IVA liquidado: SAF-T de vendas (faturação líquida de IVA; notas de crédito subtraem) ou,
no modo integrado, as vendas Wintouch sincronizadas (devoluções subtraem; anulados excluídos).
Custos e IVA suportado: documentos de compra (e-Fatura, Compras e registos manuais), uma vez por
documento, mais as compras do ERP sem documento correspondente. A rubrica vem das linhas da compra
lançada, da rubrica definida no documento ERP, da repartição confirmada na classificação de rubricas ou da
rubrica do fornecedor. Os custos sem documento
(impostos, vencimentos, encargos) entram pelos registos bancários aprovados com rubrica e marcados
como custo, sem IVA.

No regime normal, o custo é a base tributável mais o IVA não dedutível estimado (percentagem de
dedução da rubrica); nos restantes regimes, o custo é o total. Não substitui a contabilidade nem a
declaração periódica de IVA.
"""

import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal as D

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .invoice_evidence import invoice_key, nif_key
from .models import (
    BankAccount,
    BankBookEntry,
    Category,
    DocumentERPLink,
    ERPDocument,
    ERPDocumentRubric,
    MapAlertSettings,
    ProcurementDocument,
    Purchase,
    PurchaseRubric,
    now,
)
from .rubric_expenses import supplier_rubrics
from .schemas import Strict

router = APIRouter()
UNCLASSIFIED = ""


def period_key(day, group):
    year, month = int(day[:4]), int(day[5:7])
    return f"{year}-{month:02d}" if group == "month" else f"{year}-T{(month - 1) // 3 + 1}"


def period_label(key):
    if "-T" in key:
        year, quarter = key.split("-T")
        return f"{quarter}.º trimestre {year}"
    months = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    return f"{months[int(key[5:7]) - 1].capitalize()} {key[:4]}"


def line_parts(line):
    from .procurement import money, net_price
    net = D(line["quantity"]) * net_price(line)
    return money(net), money(net * D(line["vat"]) / 100)


def purchase_documents(db, stores, start, end):
    """[(data, id, [(rubrica, base, IVA)])] com sinal; cada documento de compra uma vez."""
    purchases = list(db.scalars(select(Purchase).where(
        Purchase.store_id.in_(stores), Purchase.document_date >= str(start), Purchase.document_date <= str(end),
        Purchase.origin != "efatura_testes")))
    ids = [p.id for p in purchases]
    procurement = {}
    for row in db.scalars(select(ProcurementDocument).where(ProcurementDocument.kind.in_(("invoice", "credit")), ProcurementDocument.confirmed_at.is_not(None),
                                                            ProcurementDocument.status != "cancelled", ProcurementDocument.purchase_id.in_(ids))) if ids else []:
        procurement[row.purchase_id] = row
    links = {link.purchase_id: link.erp_id for link in db.scalars(select(DocumentERPLink).where(DocumentERPLink.undone_at.is_(None), DocumentERPLink.purchase_id.in_(ids)))} if ids else {}
    erp_rubrics = defaultdict(list)
    for row in db.scalars(select(ERPDocumentRubric)):
        erp_rubrics[row.erp_document_id].append(row)
    by_supplier = supplier_rubrics(db)
    own_rubrics = defaultdict(list)
    for row in db.scalars(select(PurchaseRubric).where(PurchaseRubric.purchase_id.in_(ids))) if ids else []:
        own_rubrics[row.purchase_id].append(row)
    result = []

    def split(total_base, total_tax, weights):
        """Reparte base e IVA pelas rubricas na proporção indicada, sem perder cêntimos."""
        weight_sum = sum(w for _, w in weights) or 1
        parts, base_left, tax_left = [], total_base, total_tax
        for index, (category, weight) in enumerate(weights):
            last = index == len(weights) - 1
            base = base_left if last else round(total_base * weight / weight_sum)
            tax = tax_left if last else round(total_tax * weight / weight_sum)
            base_left, tax_left = base_left - base, tax_left - tax
            parts.append((category, base, tax))
        return parts

    for purchase in purchases:
        sign = -1 if purchase.amount_cents < 0 else 1
        tax = abs(purchase.tax_cents or 0)
        base = abs(purchase.base_cents) if purchase.base_cents is not None else abs(purchase.amount_cents) - tax
        doc = procurement.get(purchase.id)
        if doc:
            payload = json.loads(doc.data)
            default = payload.get("category_id")
            parts = defaultdict(lambda: [0, 0])
            for line in payload["lines"]:
                line_base, line_tax = line_parts(line)
                category = line.get("category_id") or default or UNCLASSIFIED
                parts[category][0] += line_base
                parts[category][1] += line_tax
            shares = [(category, sign * values[0], sign * values[1]) for category, values in parts.items()]
        elif purchase.id in links and erp_rubrics.get(links[purchase.id]):
            shares = [(c, sign * b, sign * t) for c, b, t in split(base, tax, [(r.category_id, r.amount_cents) for r in erp_rubrics[links[purchase.id]]])]
        elif own_rubrics.get(purchase.id):
            # Repartição confirmada na classificação de rubricas (compra sem linhas nem documento ERP com rubrica).
            shares = [(c, sign * b, sign * t) for c, b, t in split(base, tax, [(r.category_id, r.amount_cents) for r in own_rubrics[purchase.id]])]
        else:
            shares = [(by_supplier.get(nif_key(purchase.supplier_nif), UNCLASSIFIED), sign * base, sign * tax)]
        result.append((purchase.document_date, purchase.id, shares))
    known = {invoice_key(p.supplier_nif, p.document_type, p.document) for p in db.scalars(select(Purchase))}
    linked = set(db.scalars(select(DocumentERPLink.erp_id).where(DocumentERPLink.undone_at.is_(None))))
    for erp in db.scalars(select(ERPDocument).where(ERPDocument.store_id.in_(stores), ERPDocument.document_date >= str(start),
                                                    ERPDocument.document_date <= str(end), ERPDocument.cancelled.is_(False))):
        if erp.id in linked or invoice_key(erp.supplier_nif, erp.document_type, erp.document) in known:
            continue
        sign = -1 if erp.amount_cents < 0 else 1
        tax = abs(erp.tax_cents or 0)
        base = abs(erp.base_cents) if erp.base_cents is not None else abs(erp.amount_cents) - tax
        weights = [(r.category_id, r.amount_cents) for r in erp_rubrics.get(erp.id, [])] or [(by_supplier.get(nif_key(erp.supplier_nif), UNCLASSIFIED), 1)]
        result.append((erp.document_date, "erp:" + erp.id, [(c, sign * b, sign * t) for c, b, t in split(base, tax, weights)]))
    return result


def bank_costs(db, stores, start, end):
    """[(data, id, [(rubrica, valor, 0)])]: pagamentos e comissões do banco sem documento, com rubrica e
    marcados como custo. Registos corrigidos (com correção posterior) e correções ficam de fora."""
    corrected = select(BankBookEntry.original_id).where(BankBookEntry.original_id.is_not(None))
    rows = db.scalars(select(BankBookEntry).join(BankAccount, BankAccount.id == BankBookEntry.account_id).where(
        BankAccount.store_id.in_(stores), BankBookEntry.counts_as_cost.is_(True), BankBookEntry.category_id.is_not(None),
        BankBookEntry.amount_cents < 0, BankBookEntry.original_id.is_(None), BankBookEntry.id.not_in(corrected),
        BankBookEntry.movement_date >= str(start), BankBookEntry.movement_date <= str(end)))
    # Rubrica de análise reclassificada (fiscal_payments): o registo bancário não muda.
    from .fiscal_payments import reclassified
    moved = reclassified(db)
    return [(row.movement_date, "bank:" + row.id, [(moved.get("bank:" + row.id, row.category_id), -row.amount_cents, 0)]) for row in rows]


def cost_documents(db, stores, start, end):
    """Documentos de compra e custos sem documento registados no banco."""
    return [*purchase_documents(db, stores, start, end), *bank_costs(db, stores, start, end)]


def deductible(tax, category, percents):
    percent = percents.get(category)
    return tax if percent is None else round(tax * percent / 100)


def context(user, db, start, end, store_id):
    authorize(user, db, roles=["administrador", "gestor"])
    if start > end or (end - start).days > 400:
        raise HTTPException(422, "Selecione um período de até um ano.")
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        stores = [store_id] if store_id in stores else []
    return stores


def euro(cents):
    text = f"{abs(cents) / 100:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return ("-" if cents < 0 else "") + text + " €"


def period_keys(start, end, group):
    """Todos os períodos do intervalo (mês ou trimestre), mesmo os sem movimentos: as colunas ficam completas."""
    keys, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        key = period_key(f"{year}-{month:02d}-01", group)
        if key not in keys:
            keys.append(key)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


def previous_year(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 de fevereiro
        return day.replace(year=day.year - 1, day=28)


def next_key(key):
    """Chave do período do ano anterior → chave do período correspondente do ano atual."""
    return f"{int(key[:4]) + 1}{key[4:]}"


def compute(db, stores, start, end, group, regime, categories, percents, table):
    """Mapa de custos e proveitos de um intervalo. Os valores são os de sempre (mesmos documentos, mesmo critério);
    os campos novos (subtotais, fora dos custos, pagamentos ao Estado, registos) só desdobram o que já existia."""
    from . import rubric_nature
    from .dashboard import sales_by_day, sales_source
    from .rubric_nature import cost_value, flag
    revenue, gross_sales = defaultdict(int), defaultdict(int)
    # Proveitos: a mesma faturação sem IVA do dashboard (SAF-T ou, no modo integrado, vendas Wintouch).
    source = "erp" if sales_source(db)[0] == "erp" else "saft"
    net, gross, _, missing = sales_by_day(db, stores, start, end, source)
    for day, cents in net.items():
        revenue[period_key(day, group)] += cents
    for day, cents in gross.items():
        gross_sales[period_key(day, group)] += cents
    data = rubric_nature.collect(db, stores, start, end)
    costs = defaultdict(lambda: defaultdict(int))
    counted = defaultdict(lambda: defaultdict(set))
    excluded, outside = defaultdict(int), defaultdict(lambda: defaultdict(int))
    # IVA das compras por período (os mesmos documentos dos custos): suportado e dedutível estimado.
    vat_input, vat_deductible = defaultdict(int), defaultdict(int)
    records = {}
    for day, identifier, category, base, tax in data["documents"]:
        key = period_key(day, group)
        # Regime normal: base + IVA não dedutível; restantes regimes: o IVA também é custo.
        cost = cost_value(category, base, tax, regime, percents)
        # Custos: rubricas de custos operacionais ou de compras; pagamentos ao Estado e movimentos financeiros ficam de fora.
        if flag(table, category, "in_operating_costs") or flag(table, category, "in_purchases"):
            costs[category][key] += cost
            counted[category][key].add(identifier)
            record = records.setdefault(identifier, {"date": str(day)[:10], "amount": 0, "categories": []})
            record["amount"] += base + tax
            record["categories"].append(category)
        else:
            excluded[category] += cost
            outside[category][key] += cost
        vat_input[key] += tax
        vat_deductible[key] += deductible(tax, category, percents) if regime == "normal" else 0
    # Pagamentos ao Estado pelo valor pago (a mesma vista «Obrigações fiscais» do Dashboard): informativo, fora dos custos.
    state = defaultdict(int)
    for day, _, _, _, _, amount in rubric_nature.contributions(data, "fiscal", regime, percents, table):
        state[period_key(str(day), group)] += amount
    has_data = bool(revenue or gross_sales or data["documents"] or state)
    ordered = period_keys(start, end, group) if has_data else []
    rubrics = sorted(costs, key=lambda c: (c == UNCLASSIFIED, -sum(costs[c].values())))
    total_costs = {key: sum(costs[c][key] for c in costs) for key in ordered}

    def name(c):
        return categories[c].name if c in categories else "Por classificar"

    def info(c):
        item = categories.get(c)
        parent = categories.get(item.parent_id) if item and item.parent_id else None
        return {"group": "purchases" if flag(table, c, "in_purchases") else "operating", "nature": item.nature if item else "",
                "fixed": bool(item and item.fixed_cost), "color": item.color if item else None, "icon": item.icon if item else None,
                "parent_name": parent.name if parent else None}

    rows = [{"category_id": c or None, "category_name": name(c), "values": {key: costs[c][key] for key in ordered},
             "total_cents": sum(costs[c].values()), **info(c),
             "documents": {key: len(counted[c][key]) for key in ordered}, "records": len(set().union(*counted[c].values()))} for c in rubrics]
    subtotals = {group_key: {key: sum(r["values"][key] for r in rows if r["group"] == group_key) for key in ordered}
                 for group_key in ("purchases", "operating")}
    revenue_total, costs_total = sum(revenue.values()), sum(total_costs.values())
    margin_total = revenue_total - costs_total

    def kind(c):
        item = categories.get(c)
        if flag(table, c, "in_fiscal") or (item and item.nature == "fiscal"):
            return "fiscal"
        return "financeiro" if item and item.nature == "financeiro" else "outro"

    return {
        "group": group,
        "periods": [{"key": key, "label": period_label(key)} for key in ordered],
        "revenue": {key: revenue[key] for key in ordered},
        "costs": rows,
        "total_costs": total_costs,
        "margin": {key: revenue[key] - total_costs[key] for key in ordered},
        "subtotals": subtotals,
        "purchase_vat": {
            "input": {key: vat_input[key] for key in ordered}, "deductible": {key: vat_deductible[key] for key in ordered},
            "non_deductible": {key: vat_input[key] - vat_deductible[key] for key in ordered},
            "totals": {"input_cents": sum(vat_input.values()), "deductible_cents": sum(vat_deductible.values()),
                       "non_deductible_cents": sum(vat_input.values()) - sum(vat_deductible.values())},
        },
        # IVA liquidado nas vendas (faturação com IVA − sem IVA); indisponível se há vendas do ERP sem base.
        "sales_vat": None if missing else {key: gross_sales[key] - revenue[key] for key in ordered},
        "state_payments": {key: state[key] for key in ordered},
        "totals": {"revenue_cents": revenue_total, "costs_cents": costs_total, "margin_cents": margin_total},
        "kpis": {
            "revenue_cents": revenue_total, "costs_cents": costs_total, "margin_cents": margin_total,
            "margin_percent": round(margin_total * 100 / revenue_total, 1) if revenue_total > 0 else None,
            "merchandise_cents": sum(r["total_cents"] for r in rows if r["group"] == "purchases" and r["nature"] != "investimento"),
            "investment_cents": sum(r["total_cents"] for r in rows if r["group"] == "purchases" and r["nature"] == "investimento"),
            "operating_cents": sum(subtotals["operating"].values()),
            "fixed_cents": sum(r["total_cents"] for r in rows if r["fixed"]),
            "unclassified_cents": sum(r["total_cents"] for r in rows if r["category_id"] is None),
            "state_payments_cents": sum(state.values()),
        },
        "revenue_source": source, "revenue_missing_documents": missing,
        "excluded": [{"category_id": c or None, "category_name": name(c), "total_cents": v}
                     for c, v in sorted(excluded.items(), key=lambda item: -item[1]) if v],
        "outside": [{"category_id": c or None, "category_name": name(c), "kind": kind(c), "values": {key: outside[c][key] for key in ordered},
                     "total_cents": excluded[c], "color": categories[c].color if c in categories else None,
                     "icon": categories[c].icon if c in categories else None}
                    for c in sorted(outside, key=lambda c: -excluded[c]) if excluded[c]],
        "_records": records, "_names": {c: name(c) for c in rubrics},
    }


def previous_block(previous, rows):
    """O ano anterior com as chaves dos períodos atuais (mesmos meses), para comparar coluna a coluna."""
    shift = {p["key"]: next_key(p["key"]) for p in previous["periods"]}
    by_category = {r["category_id"]: r for r in previous["costs"]}
    current = {r["category_id"] for r in rows}
    return {
        "has_data": bool(previous["periods"]),
        "periods": [{"key": shift[p["key"]], "label": p["label"]} for p in previous["periods"]],
        "revenue": {shift[k]: v for k, v in previous["revenue"].items()},
        "total_costs": {shift[k]: v for k, v in previous["total_costs"].items()},
        "margin": {shift[k]: v for k, v in previous["margin"].items()},
        "subtotals": {g: {shift[k]: v for k, v in values.items()} for g, values in previous["subtotals"].items()},
        "costs": {(c or ""): {"values": {shift[k]: v for k, v in r["values"].items()}, "total_cents": r["total_cents"]} for c, r in by_category.items()},
        # Rubricas com custos no ano anterior e nenhum no período atual (para não desaparecerem da comparação).
        "only_previous": [{"category_id": r["category_id"], "category_name": r["category_name"], "total_cents": r["total_cents"]}
                          for c, r in by_category.items() if c not in current and r["total_cents"]],
        "kpis": previous["kpis"], "totals": previous["totals"],
    }


def budget_block(db, start, end, group, store_id, regime, categories):
    """Orçamento em vigor do ano (último aprovado; senão o rascunho mais recente), por rubrica e mês."""
    from .budget import STATUSES, current_version, line_view, versions_of
    from .models import BudgetLine
    if group != "month" or start.year != end.year:
        return {"available": False, "message": "A comparação com o orçamento é mensal e dentro do mesmo ano."}
    year = start.year
    selected = current_version(versions_of(db, year, store_id))
    if not selected:
        return {"available": False, "message": f"Sem orçamento de despesas para {year}{' neste estabelecimento' if store_id else ''} (Orçamento)."}
    keys = period_keys(start, end, group)
    planned = defaultdict(lambda: defaultdict(int))
    for line in db.scalars(select(BudgetLine).where(BudgetLine.budget_id == selected.id)):
        months = line_view(line, year, categories, {}, regime, 1)["months"]
        for index, value in enumerate(months):
            key = f"{year}-{index + 1:02d}"
            if key in keys and value:
                planned[line.category_id or UNCLASSIFIED][key] += value
    return {
        "available": True, "name": selected.name, "version": selected.version, "status_label": STATUSES[selected.status],
        "values": {c: {key: planned[c][key] for key in keys} for c in planned},
        "totals": {key: sum(planned[c][key] for c in planned) for key in keys},
        "partial": start.day != 1 or (end + timedelta(days=1)).day != 1,
    }


ALERT_DEFAULTS = {"above_average_percent": 50, "month_change_percent": 50, "increase_percent": 25, "minimum_cents": 10000, "duplicate_days": 5}


def alert_settings(db):
    row = db.get(MapAlertSettings, "default")
    return {name: getattr(row, name) for name in ALERT_DEFAULTS} | {"revision": row.revision} if row else {**ALERT_DEFAULTS, "revision": 0}


def detect_alerts(db, result, records, names, previous, settings):
    """Sinais para rever (nunca erros): valores acima da média, subidas face ao mês ou ao ano anterior, possíveis
    duplicados e despesas sem rubrica. Cada alerta indica os movimentos que o justificam."""
    from .dashboard import describe_sources
    from .rubric_nature import fold
    keys = [p["key"] for p in result["periods"]]
    labels = {p["key"]: p["label"] for p in result["periods"]}
    minimum = settings["minimum_cents"]
    alerts = []

    def add(kind, tone, row, key, title, message, amount, reference=None, ids=None):
        alerts.append({"id": f"{kind}:{row['category_id'] or ''}:{key or ''}:{len(alerts)}", "kind": kind, "tone": tone,
                       "category_id": row["category_id"], "category_name": row["category_name"], "period": key,
                       "period_label": labels.get(key) if key else None, "title": title, "message": message,
                       "amount_cents": amount, "reference_cents": reference, "ids": ids or []})

    for row in result["costs"]:
        values, label = row["values"], row["category_name"]
        if row["category_id"] is None:
            if row["total_cents"]:
                add("unclassified", "warning", row, None, "Despesas sem classificação",
                    f"{euro(row['total_cents'])} em {row['records']} registo(s) sem rubrica. Atribua a rubrica para entrarem na análise certa.",
                    row["total_cents"])
            continue
        flagged = set()
        for index, key in enumerate(keys):
            value = values.get(key, 0)
            others = [values.get(k, 0) for k in keys if k != key]
            if len(others) >= 3:
                average = sum(others) / len(others)
                if value - average >= minimum and value >= average * (1 + settings["above_average_percent"] / 100):
                    above = round((value - average) * 100 / average) if average > 0 else None
                    add("above_average", "info", row, key, "Acima da média",
                        f"{label}: {euro(value)} em {labels[key]}"
                        + (f", {above}% acima da média dos restantes períodos ({euro(round(average))})." if above is not None else ", sem valores nos restantes períodos.")
                        + " Pode ser normal (pagamento anual, compra pontual): confirme os movimentos.", value, round(average))
                    flagged.add(key)
            if index and key not in flagged:
                before = values.get(keys[index - 1], 0)
                if before > 0 and value - before >= minimum and value >= before * (1 + settings["month_change_percent"] / 100):
                    add("month_change", "info", row, key, "Subida face ao período anterior",
                        f"{label}: {euro(value)} em {labels[key]} contra {euro(before)} em {labels[keys[index - 1]]} "
                        f"(+{round((value - before) * 100 / before)}%). Confirme os movimentos.", value, before)
        if previous and previous["has_data"]:
            before = previous["costs"].get(row["category_id"] or "", {}).get("total_cents", 0)
            total = row["total_cents"]
            if before > 0 and total - before >= minimum and total >= before * (1 + settings["increase_percent"] / 100):
                add("increase", "info", row, None, "Aumento face ao ano anterior",
                    f"{label}: {euro(total)} contra {euro(before)} no mesmo período do ano anterior (+{round((total - before) * 100 / before)}%).",
                    total, before)
    # Possíveis duplicados: a mesma entidade e o mesmo valor em poucos dias (registos diferentes).
    window = settings["duplicate_days"]
    if window and records:
        rows = [{"id": identifier} for identifier in records]
        describe_sources(db, rows)
        groups = defaultdict(list)
        for row in rows:
            record = records[row["id"]]
            entity = fold(row.get("entity") or "")
            if record["amount"] and entity and entity != "—":
                groups[(entity, record["amount"])].append({**record, **row})
        from datetime import date as day_type
        for (_, amount), items in groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda item: item["date"])
            cluster = [items[0]]
            for item in [*items[1:], None]:
                if item and (day_type.fromisoformat(item["date"]) - day_type.fromisoformat(cluster[-1]["date"])).days <= window:
                    cluster.append(item)
                    continue
                if len(cluster) > 1:
                    category = next((c for c in cluster[0]["categories"]), UNCLASSIFIED)
                    first = cluster[0]
                    add("duplicate", "warning", {"category_id": category or None, "category_name": names.get(category, "Por classificar")},
                        period_key(first["date"], result["group"]), "Possível duplicado",
                        f"{len(cluster)} registos de {first.get('entity')} com o mesmo valor ({euro(amount)}) em {window} dia(s) ou menos: "
                        + ", ".join(f"{c.get('label')} ({c['date'][8:10]}/{c['date'][5:7]})" for c in cluster)
                        + ". Confirme se não é o mesmo documento lançado duas vezes.", amount, None, [c["id"] for c in cluster])
                cluster = [item] if item else []
    order = {"unclassified": 0, "duplicate": 1, "increase": 2, "above_average": 3, "month_change": 4}
    alerts.sort(key=lambda a: (order[a["kind"]], -abs(a["amount_cents"])))
    return alerts[:60]


@router.get("/api/management/costs-revenues")
def costs_revenues(start: date, end: date, group: str = "month", store_id: str = "", compare: bool = False,
                   user=Depends(current), db: Session = Depends(db_session)):
    from .rubric_nature import flags
    from .settings import vat_settings
    if group not in ("month", "quarter"):
        raise HTTPException(422, "Agrupe por mês ou por trimestre.")
    stores = context(user, db, start, end, store_id)
    regime = vat_settings(db)["vat_regime"]
    categories = {c.id: c for c in db.scalars(select(Category))}
    percents = {c.id: c.vat_deduction_percent for c in categories.values()}
    table = flags(db)
    result = compute(db, stores, start, end, group, regime, categories, percents, table)
    records, names = result.pop("_records"), result.pop("_names")
    previous = None
    if compare:
        before = compute(db, stores, previous_year(start), previous_year(end), group, regime, categories, percents, table)
        before.pop("_records"), before.pop("_names")
        previous = previous_block(before, result["costs"])
    settings = alert_settings(db)
    shown = {r["category_id"] for r in result["costs"]}
    excluded = result["excluded"]
    source, missing = result["revenue_source"], result["revenue_missing_documents"]
    return {
        "regime": regime, **result,
        "previous": previous,
        "budget": budget_block(db, start, end, group, store_id, regime, categories),
        "alerts": detect_alerts(db, result, records, names, previous, settings),
        "alert_settings": settings,
        # Rubricas de custos sem movimentos no período (opção «Mostrar rubricas sem movimentos»).
        "empty_rubrics": [{"category_id": c.id, "category_name": c.name, "group": "purchases" if table.get(c.id, {}).get("in_purchases") else "operating",
                           "fixed": c.fixed_cost, "nature": c.nature, "color": c.color, "icon": c.icon,
                           "parent_name": categories[c.parent_id].name if c.parent_id in categories else None}
                          for c in sorted(categories.values(), key=lambda c: c.name)
                          if c.active and not c.cash_difference and c.id not in shown
                          and (table.get(c.id, {}).get("in_operating_costs") or table.get(c.id, {}).get("in_purchases"))],
        "notes": [
            *(["Fora dos custos pela natureza da rubrica (obrigações fiscais ou movimentos financeiros): "
               + ", ".join(e["category_name"] for e in excluded) + "."] if excluded else []),
            *([f"Atenção: {missing} documento(s) de venda do ERP sem base tributável não entram nos proveitos. Volte a sincronizar as vendas do período."] if missing else []),
            ("Proveitos: vendas Wintouch líquidas de IVA (anulados excluídos; devoluções subtraem)" if source == "erp"
             else "Proveitos: faturação SAF-T líquida de IVA") + ", pela data do documento. Custos: todos os documentos de compra pela data do documento, incluindo faturas do e-Fatura ainda não lançadas (com a rubrica do fornecedor), e os custos sem documento (impostos, vencimentos, encargos) registados na reconciliação bancária com rubrica, pela data do movimento.",
            "Custo no regime normal = base tributável + IVA não dedutível estimado; nos restantes regimes, o IVA é custo." if regime else "Configure o regime de IVA para calcular os custos corretamente.",
            "Margem de gestão estimada = proveitos sem IVA − custos deste mapa. As compras de mercadorias contam pela data da fatura (não são o custo das mercadorias vendidas): não é o resultado contabilístico nem uma margem de tesouraria.",
            "Uma fatura e o respetivo pagamento contam uma vez (pelo documento). Os pagamentos ao Estado (IVA, retenções, Segurança Social nas rubricas de obrigações fiscais) aparecem à parte, pelo valor pago, e não somam aos custos.",
            "Mapa de gestão indicativo: não inclui amortizações, acréscimos e diferimentos, nem custos pagos sem registo com rubrica.",
        ],
    }


class AlertSettingsInput(Strict):
    above_average_percent: int = Field(ge=10, le=1000)
    month_change_percent: int = Field(ge=10, le=1000)
    increase_percent: int = Field(ge=5, le=1000)
    minimum_cents: int = Field(ge=0, le=10_000_000)
    duplicate_days: int = Field(ge=0, le=31)
    revision: int = Field(ge=0)


@router.get("/api/management/alert-settings")
def get_alert_settings(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    return {**alert_settings(db), "can_edit": user.role == "administrador"}


@router.put("/api/management/alert-settings")
def put_alert_settings(data: AlertSettingsInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador"])
    row = db.get(MapAlertSettings, "default", with_for_update=True)
    if not row:
        row = MapAlertSettings(id="default", **ALERT_DEFAULTS, revision=0)
        db.add(row)
        db.flush()
    if row.revision != data.revision:
        raise HTTPException(409, "Os limites foram alterados entretanto. Recarregue e tente de novo.")
    before = {name: getattr(row, name) for name in ALERT_DEFAULTS}
    after = data.model_dump(exclude={"revision"})
    for name, value in after.items():
        setattr(row, name, value)
    row.revision += 1
    row.updated_by, row.updated_at = user.id, now()
    record(db, user, "limites_alertas_mapas", "default", {"antes": before, "depois": after})
    db.commit()
    return {**alert_settings(db), "can_edit": True}


@router.get("/api/management/vat-map")
def vat_map(start: date, end: date, store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    from .billing_conference import documents
    from .settings import vat_settings
    stores = context(user, db, start, end, store_id)
    fiscal = vat_settings(db)
    if not fiscal["vat_regime"]:
        return {"available": False, "message": "Configure o regime de IVA da empresa (Configurações → Regime de IVA)."}
    if fiscal["vat_regime"] == "isento_53":
        return {"available": False, "message": "Empresa isenta ao abrigo do artigo 53.º do CIVA: não há IVA a liquidar nem a deduzir."}
    group = "month" if fiscal["vat_period"] == "mensal" else "quarter"
    categories = {c.id: c for c in db.scalars(select(Category))}
    percents = {c.id: c.vat_deduction_percent for c in categories.values()}
    rows = defaultdict(lambda: {"output_cents": 0, "input_cents": 0, "deductible_cents": 0, "sales_documents": 0, "purchase_documents": 0})
    from .dashboard import sales_source
    source, _ = sales_source(db)
    sales_documents = documents(db, stores, str(start), str(end))
    if source == "erp":
        from .models import WintouchSaleRecord
        sales_documents = []
        for sale in db.scalars(select(WintouchSaleRecord).where(
                WintouchSaleRecord.kind == "documento", WintouchSaleRecord.active.is_(True), WintouchSaleRecord.store_id.in_(stores),
                WintouchSaleRecord.document_date >= str(start), WintouchSaleRecord.document_date <= str(end))):
            data = json.loads(sale.data)
            if data.get("document_group") != "vendas" or data.get("cancelled"):
                continue
            if data.get("tax_cents") is None:
                return {"available": False, "message": "Há vendas do ERP sem IVA discriminado. Volte a sincronizar o período."}
            sales_documents.append({"eligible": True, "date": sale.document_date, "tax_cents": int(data["tax_cents"]), "direction": data.get("direction") or 1})
    for doc in sales_documents:
        if doc["eligible"]:
            row = rows[period_key(doc["date"], group)]
            row["output_cents"] += doc["tax_cents"] * doc["direction"]
            row["sales_documents"] += 1
    by_rubric = defaultdict(lambda: {"input_cents": 0, "deductible_cents": 0})
    for day, _, shares in purchase_documents(db, stores, start, end):
        row = rows[period_key(day, group)]
        row["purchase_documents"] += 1
        for category, _, tax in shares:
            value = deductible(tax, category, percents)
            row["input_cents"] += tax
            row["deductible_cents"] += value
            by_rubric[category]["input_cents"] += tax
            by_rubric[category]["deductible_cents"] += value
    ordered = sorted(rows)
    estimated = any(percent is not None for percent in percents.values())
    return {
        "available": True, "regime": fiscal["vat_regime"], "period": fiscal["vat_period"], "estimated_deduction": estimated,
        "periods": [{"key": key, "label": period_label(key), **rows[key], "balance_cents": rows[key]["output_cents"] - rows[key]["deductible_cents"]} for key in ordered],
        "rubrics": [{"category_id": c or None, "category_name": categories[c].name if c in categories else "Por classificar",
                     "vat_deduction_percent": percents.get(c), **values} for c, values in sorted(by_rubric.items(), key=lambda item: -item[1]["input_cents"])],
        "notes": [
            ("IVA liquidado: vendas Wintouch (anulados excluídos; devoluções subtraem)." if source == "erp" else "IVA liquidado: SAF-T de vendas (documentos normais; notas de crédito subtraem).") + " IVA suportado: documentos de compra, incluindo os ainda não lançados.",
            "Mapa indicativo: a dedutibilidade depende da natureza da despesa e é confirmada pelo contabilista. Não substitui a declaração periódica.",
            "Pequenos retalhistas: o imposto devido segue regras próprias (art. 60.º do CIVA); o saldo apresentado é apenas uma referência." if fiscal["vat_regime"] == "pequenos_retalhistas" else "",
        ],
    }
