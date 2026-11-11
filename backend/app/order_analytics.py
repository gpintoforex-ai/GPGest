"""Read-only order indicators with explicit populations and traceable drilldowns."""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import customer_orders as orders
from .access import current, scope
from .db import db_session
from .models import (
    BusinessEntity,
    CustomerOrder,
    CustomerOrderEvent,
    CustomerPurchaseSimulation,
    StockArticle,
    StockEntry,
    StockWarehouse,
)

router = APIRouter(prefix="/api/order-cycle", tags=["Indicadores de encomendas"])
LISBON = ZoneInfo("Europe/Lisbon")
STATES = {
    "waiting": "A aguardar",
    "ordered": "Em compra",
    "partial": "Disponível parcialmente",
    "ready": "Disponível por completo",
    "delivered": "Entregue",
    "cancelled": "Cancelada",
    "closed_with_cancel": "Concluída com cancelamento",
}


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def state_of(view):
    if view["closed"]:
        cancelled = sum(D(line["quantities"]["cancelled"]) for line in view["lines"])
        delivered = sum(D(line["quantities"]["delivered"]) for line in view["lines"])
        return "cancelled" if not delivered else "closed_with_cancel" if cancelled else "delivered"
    if view["fully_ready"]:
        return "ready"
    if view["ready"]:
        return "partial"
    if any(
        D(line["quantities"]["ordered"]) > 0 or D(line["quantities"]["simulated"]) > 0
        for line in view["lines"]
    ):
        return "ordered"
    return "waiting"


@router.get("/analytics")
def analytics(
    start: date,
    end: date,
    store_id: str = "",
    user=Depends(current),
    db: Session = Depends(db_session),
):
    orders.management(db, user, store_id or None)
    if start > end or (end - start).days > 1096:
        orders.fail("Escolha um período válido até três anos.")
    stores = [store_id] if store_id else scope(user, db)
    rows = list(db.scalars(select(CustomerOrder).where(CustomerOrder.store_id.in_(stores))))
    orders.prefetch(db, rows)
    try:
        views = {row.id: orders.order_view(db, row) for row in rows}
    finally:
        db.info.pop("customer_orders_cache", None)
    event_rows = list(
        db.scalars(
            select(CustomerOrderEvent)
            .where(CustomerOrderEvent.store_id.in_(stores), CustomerOrderEvent.action == "deliver")
            .order_by(CustomerOrderEvent.created_at, CustomerOrderEvent.id)
        )
    )
    latest = {event.order_id: event for event in event_rows if event.order_id in views}
    distribution = {key: 0 for key in STATES}
    created_by_day, completed_by_day = defaultdict(int), defaultdict(int)
    listings, articles = [], {}
    completed, durations, on_time, dated = [], [], 0, 0
    for row in rows:
        view = views[row.id]
        state = state_of(view)
        created = timestamp(row.created_at)
        created_day = created.astimezone(LISBON).date()
        in_period = start <= created_day <= end
        if in_period:
            created_by_day[created_day.isoformat()] += 1
            distribution[state] += 1
            listings.append(
                {
                    "id": row.id,
                    "number": view["number"],
                    "customer": view["customer_name"],
                    "created_at": row.created_at,
                    "due_date": view.get("due_date"),
                    "state": state,
                    "state_label": STATES[state],
                    "late": view["late"],
                    "article_ids": [
                        line["article_id"] for line in view["lines"] if line["article_id"]
                    ],
                }
            )
            for line in view["lines"]:
                q = line["quantities"]
                amount = D(q["requested"]) - D(q["cancelled"])
                if amount <= 0:
                    continue
                key = line["article_id"] or "provisional:" + line["id"]
                item = articles.setdefault(
                    key,
                    {
                        "article_id": line["article_id"],
                        "description": line["description"],
                        "quantity": D(0),
                        "order_ids": [],
                        "provisional": not bool(line["article_id"]),
                    },
                )
                item["quantity"] += amount
                if row.id not in item["order_ids"]:
                    item["order_ids"].append(row.id)
        event = latest.get(row.id)
        if state != "delivered" or not event:
            continue
        delivered = timestamp(event.created_at)
        delivery_day = delivered.astimezone(LISBON).date()
        if not start <= delivery_day <= end:
            continue
        completed_by_day[delivery_day.isoformat()] += 1
        detail = json.loads(event.data)
        deadline = detail.get("due_date_at_delivery")
        on_time_value = None
        if deadline:
            try:
                promised = date.fromisoformat(deadline)
                dated += 1
                on_time_value = delivery_day <= promised
                on_time += int(on_time_value)
            except (ValueError, TypeError):
                pass
        elapsed = (delivered - created).total_seconds() / 86400
        if elapsed >= 0:
            durations.append(elapsed)
        completed.append(
            {
                "id": row.id,
                "number": view["number"],
                "customer": view["customer_name"],
                "delivered_at": event.created_at,
                "deadline": deadline,
                "on_time": on_time_value,
                "elapsed_days": round(elapsed, 2) if elapsed >= 0 else None,
            }
        )
    unit = "day" if (end - start).days <= 45 else "week" if (end - start).days <= 180 else "month"

    def bucket(day):
        return (
            day.isoformat()
            if unit == "day"
            else (day - timedelta(days=day.weekday())).isoformat()
            if unit == "week"
            else day.replace(day=1).isoformat()
        )

    grouped = defaultdict(lambda: {"created": 0, "completed": 0})
    for source, field in [(created_by_day, "created"), (completed_by_day, "completed")]:
        for key, value in source.items():
            grouped[bucket(date.fromisoformat(key))][field] += value
    cursor = start
    while cursor <= end:
        grouped[bucket(cursor)]
        cursor += timedelta(days=1)
    warehouse_rows = list(
        db.scalars(select(StockWarehouse).where(StockWarehouse.store_id.in_(stores)))
    )
    warehouse_ids = [row.id for row in warehouse_rows]
    warehouse_names = {row.id: row.name for row in warehouse_rows}
    products = {article.id: article for article in db.scalars(select(StockArticle))}
    rotation = [
        {
            "article_id": aid,
            "description": products[aid].name if aid in products else aid,
            "quantity": str(quantity),
            "movement_count": count,
            "daily": str((quantity / D((end - start).days + 1)).quantize(D(".000001"))),
        }
        for aid, quantity, count in db.execute(
            select(StockEntry.article_id, func.sum(-StockEntry.quantity), func.count(StockEntry.id))
            .where(
                StockEntry.warehouse_id.in_(warehouse_ids),
                StockEntry.kind == "sale",
                StockEntry.quantity < 0,
                StockEntry.occurred_on >= start.isoformat(),
                StockEntry.occurred_on <= end.isoformat(),
            )
            .group_by(StockEntry.article_id)
            .order_by(func.sum(-StockEntry.quantity).desc())
            .limit(12)
        )
    ]
    for item in rotation:
        entries = db.scalars(
            select(StockEntry)
            .where(
                StockEntry.article_id == item["article_id"],
                StockEntry.warehouse_id.in_(warehouse_ids),
                StockEntry.kind == "sale",
                StockEntry.quantity < 0,
                StockEntry.occurred_on >= start.isoformat(),
                StockEntry.occurred_on <= end.isoformat(),
            )
            .order_by(StockEntry.occurred_on.desc(), StockEntry.id)
            .limit(50)
        )
        item["movements"] = [
            {
                "id": entry.id,
                "date": entry.occurred_on,
                "warehouse": warehouse_names[entry.warehouse_id],
                "quantity": str(-entry.quantity),
            }
            for entry in entries
        ]
        item["movements_truncated"] = item["movement_count"] > len(item["movements"])
    suppliers = {row.id: row.name for row in db.scalars(select(BusinessEntity))}
    drafts, currencies = [], {}
    for sim in db.scalars(
        select(CustomerPurchaseSimulation).where(
            CustomerPurchaseSimulation.store_id.in_(stores),
            CustomerPurchaseSimulation.status == "draft",
            CustomerPurchaseSimulation.procurement_id.is_(None),
        )
    ):
        raw = json.loads(sim.data)
        terms = orders.CommercialTerms(**raw["terms"])
        totals = orders.calculate([orders.SimLine(**line) for line in raw["lines"]], terms)
        currencies[terms.currency] = currencies.get(terms.currency, 0) + totals["total_cents"]
        drafts.append(
            {
                "id": sim.id,
                "supplier": suppliers.get(sim.supplier_id, "Fornecedor"),
                "store_id": sim.store_id,
                "currency": terms.currency,
                "total_cents": totals["total_cents"],
                "net_cents": totals["net_cents"],
                "minimum_missing_cents": totals["missing_cents"],
                "shipping_missing_cents": totals["shipping_missing_cents"],
            }
        )
    return {
        "read_only": True,
        "start": start,
        "end": end,
        "unit": unit,
        "definitions": {
            "population": "Estado atual dos pedidos criados no período, pela data de registo.",
            "completed": "Entregas físicas completas no período; pedidos com cancelamentos são separados.",
            "on_time": "Entrega completa até ao prazo guardado no evento de entrega. Sem prazo histórico fica fora do denominador.",
            "rotation": "Saídas físicas locais de venda. Não se soma faturação ou transferências.",
            "drafts": "Rascunhos atuais, fora do filtro de datas; sem compromisso de stock, separados por moeda.",
        },
        "summary": {
            "created": len(listings),
            "pending": sum(
                v
                for k, v in distribution.items()
                if k not in ("delivered", "cancelled", "closed_with_cancel")
            ),
            "late": sum(row["late"] for row in listings),
            "completed": len(completed),
            "average_days": round(sum(durations) / len(durations), 2) if durations else None,
            "on_time_percent": round(on_time * 100 / dated, 1) if dated else None,
            "on_time_count": on_time,
            "dated_deliveries": dated,
            "unknown_deadlines": len(completed) - dated,
            "drafts_below_minimum": sum(row["minimum_missing_cents"] > 0 for row in drafts),
        },
        "states": [
            {"id": key, "label": label, "count": distribution[key]} for key, label in STATES.items()
        ],
        "series": [{"key": key, **value} for key, value in sorted(grouped.items())],
        "articles": sorted(articles.values(), key=lambda item: -item["quantity"])[:12],
        "rotation": rotation,
        "drafts": drafts,
        "forecast_by_currency": currencies,
        "orders": sorted(listings, key=lambda item: str(item["created_at"]), reverse=True),
        "completed_orders": completed,
    }
