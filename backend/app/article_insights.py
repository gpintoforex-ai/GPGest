"""Read-only article summaries over the existing authorized stock ledger."""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .models import ProcurementDocument


def article_insights(db, product, visible, warehouses, can_view_cost, can_view_sales, unavailable):
    from .procurement import accepted, used

    today = datetime.now(ZoneInfo("Europe/Lisbon")).date()
    start = today - timedelta(days=29)
    data = json.loads(product.data)
    available = not unavailable and not data.get("service", False)
    sales = [e for e in visible if e.kind in ("sale", "customer_return")]
    recent = [e for e in sales if start.isoformat() <= e.occurred_on <= today.isoformat()]
    incoming = Decimal(0)
    # Same accepted-minus-received rule as the supply centre; draft/cancelled orders are excluded.
    if can_view_cost:
        stores = {w.store_id for w in warehouses}
        for document in db.scalars(select(ProcurementDocument).where(
                ProcurementDocument.store_id.in_(stores), ProcurementDocument.kind == "order",
                ProcurementDocument.confirmed_at.is_not(None), ProcurementDocument.status != "cancelled")):
            for index, line in enumerate(json.loads(document.data).get("lines", [])):
                if line.get("article_id") == product.id:
                    incoming += max(Decimal(0), accepted(line) - used(db, document.id, index, "receipt"))

    first_month = today.replace(day=1)
    for _ in range(5):
        first_month = (first_month - timedelta(days=1)).replace(day=1)
    monthly = defaultdict(lambda: {"sold": Decimal(0), "purchased": Decimal(0)})
    documents = []
    replaced = {json.loads(e.data or "{}").get("reversal_of") for e in visible} - {None}
    latest_sale = None
    latest_purchase = None
    for entry in visible:
        if entry.occurred_on > today.isoformat():
            continue
        detail = json.loads(entry.data or "{}")
        if entry.kind == "sale" and entry.id not in replaced:
            latest_sale = max(latest_sale or "", entry.occurred_on)
        if entry.kind == "purchase" and entry.id not in replaced:
            latest_purchase = max(latest_purchase or "", entry.occurred_on)
        if first_month.isoformat() <= entry.occurred_on:
            if entry.kind in ("sale", "customer_return"):
                monthly[entry.occurred_on[:7]]["sold"] -= entry.quantity
            elif entry.kind == "purchase" and entry.id not in replaced:
                monthly[entry.occurred_on[:7]]["purchased"] += entry.quantity
        if entry.kind not in ("sale", "customer_return", "purchase", "supplier_return"):
            continue
        if entry.kind in ("purchase", "supplier_return") and not can_view_cost:
            continue
        documents.append({"id": entry.id, "date": entry.occurred_on, "kind": entry.kind,
                          "document": detail.get("document") or None, "quantity": str(entry.quantity),
                          "net_sales": str(entry.net_sales) if can_view_sales and entry.kind in ("sale", "customer_return") else None})
    documents.sort(key=lambda row: (row["date"], row["id"]), reverse=True)
    months = []
    cursor = first_month
    while cursor <= today:
        key = cursor.isoformat()[:7]
        months.append({"month": key, "sold": str(monthly[key]["sold"]) if available else None,
                       "purchased": str(monthly[key]["purchased"]) if available and can_view_cost else None})
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {
        "source": "Movimentos de stock nos armazéns autorizados; vendas líquidas de devoluções. Não inclui linhas importadas ainda sem movimento aplicado.",
        "available": available, "from": start.isoformat(), "to": today.isoformat(),
        "sold_30_days": str(-sum((e.quantity for e in recent), Decimal(0))) if available else None,
        "sales_30_days": str(sum((e.net_sales for e in recent), Decimal(0))) if available and can_view_sales else None,
        "incoming_quantity": str(incoming) if can_view_cost else None,
        "last_sale": latest_sale if available else None,
        "last_purchase": latest_purchase if available and can_view_cost else None,
        "monthly": months, "documents": documents[:20] if available else [],
        "documents_total": len(documents) if available else 0,
        "rotation": None,  # No historical average-stock series exists: never invent a rotation ratio.
    }
