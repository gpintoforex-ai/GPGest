"""Mapas de gestão indicativos: custos e proveitos, e mapa de IVA por período.

Proveitos e IVA liquidado: SAF-T de vendas (faturação líquida de IVA; notas de crédito subtraem).
Custos e IVA suportado: documentos de compra (e-Fatura, Compras e registos manuais), uma vez por
documento, mais as compras do ERP sem documento correspondente. A rubrica vem das linhas da compra
lançada, da rubrica definida no documento ERP ou da rubrica do fornecedor.

No regime normal, o custo é a base tributável mais o IVA não dedutível estimado (percentagem de
dedução da rubrica); nos restantes regimes, o custo é o total. Não substitui a contabilidade nem a
declaração periódica de IVA.
"""

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal as D

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, scope
from .db import db_session
from .invoice_evidence import invoice_key, nif_key
from .models import (
    Category,
    DocumentERPLink,
    ERPDocument,
    ERPDocumentRubric,
    ProcurementDocument,
    Purchase,
)
from .rubric_expenses import supplier_rubrics

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


@router.get("/api/management/costs-revenues")
def costs_revenues(start: date, end: date, group: str = "month", store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    from .billing_conference import documents
    from .settings import vat_settings
    if group not in ("month", "quarter"):
        raise HTTPException(422, "Agrupe por mês ou por trimestre.")
    stores = context(user, db, start, end, store_id)
    regime = vat_settings(db)["vat_regime"]
    categories = {c.id: c for c in db.scalars(select(Category))}
    percents = {c.id: c.vat_deduction_percent for c in categories.values()}
    periods = set()
    revenue = defaultdict(int)
    for doc in documents(db, stores, str(start), str(end)):
        if doc["eligible"]:
            key = period_key(doc["date"], group)
            periods.add(key)
            revenue[key] += doc["net_cents"] * doc["direction"]
    costs = defaultdict(lambda: defaultdict(int))
    for day, _, shares in purchase_documents(db, stores, start, end):
        key = period_key(day, group)
        periods.add(key)
        for category, base, tax in shares:
            # Regime normal: base + IVA não dedutível; restantes regimes: o IVA também é custo.
            cost = base + (tax - deductible(tax, category, percents) if regime == "normal" else tax)
            costs[category][key] += cost
    ordered = sorted(periods)
    rubrics = sorted(costs, key=lambda c: (c == UNCLASSIFIED, -sum(costs[c].values())))
    total_costs = {key: sum(costs[c][key] for c in costs) for key in ordered}
    return {
        "regime": regime, "group": group,
        "periods": [{"key": key, "label": period_label(key)} for key in ordered],
        "revenue": {key: revenue[key] for key in ordered},
        "costs": [{"category_id": c or None, "category_name": categories[c].name if c in categories else "Por classificar",
                   "values": {key: costs[c][key] for key in ordered}, "total_cents": sum(costs[c].values())} for c in rubrics],
        "total_costs": total_costs,
        "margin": {key: revenue[key] - total_costs[key] for key in ordered},
        "totals": {"revenue_cents": sum(revenue.values()), "costs_cents": sum(total_costs.values()),
                   "margin_cents": sum(revenue.values()) - sum(total_costs.values())},
        "notes": [
            "Proveitos: faturação SAF-T líquida de IVA, pela data do documento. Custos: todos os documentos de compra pela data do documento, incluindo faturas do e-Fatura ainda não lançadas (com a rubrica do fornecedor).",
            "Custo no regime normal = base tributável + IVA não dedutível estimado; nos restantes regimes, o IVA é custo." if regime else "Configure o regime de IVA para calcular os custos corretamente.",
            "Mapa de gestão indicativo: não inclui amortizações, acréscimos e diferimentos nem salários não registados como compras.",
        ],
    }


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
    for doc in documents(db, stores, str(start), str(end)):
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
            "IVA liquidado: SAF-T de vendas (documentos normais; notas de crédito subtraem). IVA suportado: documentos de compra, incluindo os ainda não lançados.",
            "Mapa indicativo: a dedutibilidade depende da natureza da despesa e é confirmada pelo contabilista. Não substitui a declaração periódica.",
            "Pequenos retalhistas: o imposto devido segue regras próprias (art. 60.º do CIVA); o saldo apresentado é apenas uma referência." if fiscal["vat_regime"] == "pequenos_retalhistas" else "",
        ],
    }
