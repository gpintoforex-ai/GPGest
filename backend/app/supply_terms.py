"""Shared purchasing constraints; these helpers never create stock or ERP records."""

import json
from datetime import date
from decimal import Decimal as D
from math import gcd

from sqlalchemy import select

from .models import ArticleSupplierTerms, CustomerPurchaseSimulation, CustomerSupplyLink


def article_terms(db):
    today = date.today().isoformat()
    return {
        (row.article_id, row.supplier_id): row
        for row in db.scalars(select(ArticleSupplierTerms))
        if not row.valid_until or row.valid_until >= today
    }


def draft_replenishment(db, store_id):
    """Unallocated draft quantities are planning only, never physical availability."""
    drafts = list(
        db.scalars(
            select(CustomerPurchaseSimulation).where(
                CustomerPurchaseSimulation.store_id == store_id,
                CustomerPurchaseSimulation.status == "draft",
                CustomerPurchaseSimulation.procurement_id.is_(None),
            )
        )
    )
    allocated = {}
    for link in db.scalars(
        select(CustomerSupplyLink).where(
            CustomerSupplyLink.simulation_id.in_([row.id for row in drafts])
        )
    ):
        key = (link.simulation_id, link.simulation_line)
        allocated[key] = allocated.get(key, D(0)) + max(
            D(0), link.quantity - link.released - link.received
        )
    result = {}
    for row in drafts:
        for index, line in enumerate(json.loads(row.data)["lines"]):
            aid = line.get("article_id")
            if aid:
                result[aid] = result.get(aid, D(0)) + max(
                    D(0), D(line["quantity"]) - allocated.get((row.id, index), D(0))
                )
    return result


def purchase_quantity(wanted, precision=0, minimum=None, multiple=None, ceiling=None):
    """Round upward to a purchase pack, without silently exceeding a risk ceiling."""
    quantum = D(1).scaleb(-precision)
    step = max(quantum, D(str(multiple or quantum)))
    scale = 10 ** max(precision, -step.as_tuple().exponent)
    units, pack_units = int(quantum * scale), int(step * scale)
    step = D(units * pack_units // gcd(units, pack_units)) / scale
    if wanted <= 0:
        return D(0)
    amount = max(wanted, D(str(minimum or 0)))
    amount = (amount / step).to_integral_value(rounding="ROUND_UP") * step
    amount = amount.quantize(quantum, rounding="ROUND_UP")
    if ceiling is not None and amount > ceiling:
        return D(0)
    return amount
