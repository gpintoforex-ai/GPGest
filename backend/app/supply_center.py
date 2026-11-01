"""Read-only supply planning over existing demand, reservations and purchase ledgers."""

import json
from datetime import date, timedelta
from decimal import Decimal as D

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import customer_orders as orders
from . import order_cycle as cycle
from . import procurement as buying
from .access import current
from .db import db_session
from .models import (
    BusinessEntity,
    CustomerOrder,
    CustomerOrderLine,
    CustomerOrderReservation,
    CustomerPurchaseSimulation,
    CustomerSupplyLink,
    ProcurementDocument,
    StockArticle,
)
from .supply_terms import article_terms, purchase_quantity

router = APIRouter(prefix="/api/order-cycle", tags=["Centro de encomendas"])
ZERO = D(0)


@router.get("/supply-center")
def supply_center(store_id: str, user=Depends(current), db: Session = Depends(db_session)):
    orders.management(db, user, store_id)
    free, incoming, _, _, prices, entries = cycle.stock_context(db, user, store_id)
    known = not buying.policy(db).get("authority", {}).get("stock") == "erp"
    articles = {a.id: a for a in db.scalars(select(StockArticle))}
    negotiated = article_terms(db)
    physical, held, committed = {}, {}, {}
    for entry in entries:
        physical[entry.article_id] = physical.get(entry.article_id, ZERO) + entry.quantity
    for aid, quantity in db.execute(
        select(CustomerOrderLine.article_id, CustomerOrderReservation.quantity)
        .select_from(CustomerOrderReservation)
        .join(CustomerOrderLine, CustomerOrderLine.id == CustomerOrderReservation.line_id)
        .join(CustomerOrder, CustomerOrder.id == CustomerOrderLine.order_id)
        .where(CustomerOrder.store_id == store_id)
    ):
        held[aid] = held.get(aid, ZERO) + quantity
    for link, line, sim, doc in db.execute(
        select(
            CustomerSupplyLink, CustomerOrderLine, CustomerPurchaseSimulation, ProcurementDocument
        )
        .select_from(CustomerSupplyLink)
        .join(CustomerOrderLine, CustomerOrderLine.id == CustomerSupplyLink.line_id)
        .join(
            CustomerPurchaseSimulation,
            CustomerPurchaseSimulation.id == CustomerSupplyLink.simulation_id,
        )
        .join(
            ProcurementDocument, ProcurementDocument.id == CustomerPurchaseSimulation.procurement_id
        )
        .where(
            CustomerPurchaseSimulation.store_id == store_id,
            CustomerPurchaseSimulation.status != "cancelled",
            ProcurementDocument.status != "cancelled",
        )
    ):
        if doc.confirmed_at and line.article_id:
            committed[line.article_id] = committed.get(line.article_id, ZERO) + max(
                ZERO, link.quantity - link.released - link.received
            )
    # Incoming already assigned to a customer cannot cover a second customer.
    available = dict(free) if known else {}
    unassigned = {aid: max(ZERO, q - committed.get(aid, ZERO)) for aid, q in incoming.items()}
    rows = list(db.scalars(select(CustomerOrder).where(CustomerOrder.store_id == store_id)))
    orders.prefetch(db, rows)
    try:
        views = [orders.order_view(db, row) for row in rows]
    finally:
        db.info.pop("customer_orders_cache", None)
    views.sort(
        key=lambda o: (
            o.get("priority") != "urgent",
            o.get("due_date") or "9999-12-31",
            str(o["created_at"]),
            o["id"],
        )
    )
    consolidated, supplier_groups = {}, {}
    pending_orders, urgent_orders, ready_orders = set(), set(), set()
    for order in views:
        if order["closed"]:
            continue
        pending_orders.add(order["id"])
        urgent = order.get("priority") == "urgent" or order["late"]
        if urgent:
            urgent_orders.add(order["id"])
        for line in order["lines"]:
            q = {key: D(value) for key, value in line["quantities"].items()}
            outstanding = q["requested"] - q["delivered"] - q["cancelled"]
            if outstanding <= 0:
                continue
            aid = line["article_id"]
            article = articles.get(aid)
            meta = json.loads(article.data) if article else {}
            sid = line["supplier_id"] or meta.get("supplier_id")
            # Allocation is a planning view, not a physical reservation.
            uncovered = q["to_order"]
            stock_cover = min(uncovered, available.get(aid, ZERO)) if aid and known else ZERO
            if aid:
                available[aid] = max(ZERO, available.get(aid, ZERO) - stock_cover)
            uncovered -= stock_cover
            transit_cover = min(uncovered, unassigned.get(aid, ZERO)) if aid else ZERO
            if aid:
                unassigned[aid] = max(ZERO, unassigned.get(aid, ZERO) - transit_cover)
            needed = max(ZERO, uncovered - transit_cover)
            if q["available"] > 0:
                ready_orders.add(order["id"])
            key = aid or "provisional:" + line["id"]
            item = consolidated.setdefault(
                key,
                {
                    "id": key,
                    "article_id": aid,
                    "code": line["article_code"],
                    "description": article.name if article else line["description"],
                    "physical": physical.get(aid, ZERO) if known else None,
                    "reserved": held.get(aid, ZERO) if known else None,
                    "available": free.get(aid, ZERO) if known else None,
                    "incoming": incoming.get(aid, ZERO),
                    "needed": ZERO,
                    "requested": ZERO,
                    "delivered": ZERO,
                    "urgent": False,
                    "customers": [],
                    "stock_known": known,
                    "provisional": not bool(article),
                    "physical_shortage": False,
                    "reservation_warning": False,
                },
            )
            own_pending = max(
                ZERO,
                q["requested"]
                - q["cancelled"]
                - q["delivered"]
                - q["reserved"]
                - q["to_order"]
                - q["simulated"],
            )
            customer = {
                "line_id": line["id"],
                "order_id": order["id"],
                "number": order["number"],
                "customer": order["customer_name"],
                "customer_id": order["customer_id"],
                "requested_at": order.get("requested_at"),
                "due_date": order.get("due_date"),
                "requested": q["requested"],
                "delivered": q["delivered"],
                "reserved": q["reserved"],
                "ready_quantity": q["available"],
                "stock_cover": stock_cover,
                "incoming_cover": transit_cover,
                "ordered_pending": own_pending,
                "simulated": q["simulated"],
                "needed": needed if known else None,
                "supplier_id": sid,
                "state": "Reserva por rever"
                if q["reserved"] > q["available"]
                else line["state_label"],
                "urgent": urgent,
            }
            item["customers"].append(customer)
            item["needed"] += needed
            item["requested"] += q["requested"]
            item["delivered"] += q["delivered"]
            item["urgent"] |= urgent
            item["physical_shortage"] |= outstanding > q["available"] + stock_cover
            item["reservation_warning"] |= q["reserved"] > q["available"]
            if needed <= 0:
                continue
            supplier = db.get(BusinessEntity, sid) if sid else None
            group = supplier_groups.setdefault(
                sid or "",
                {
                    "supplier_id": sid,
                    "name": supplier.name if supplier else "Sem fornecedor",
                    "store_id": store_id,
                    "articles": {},
                    "clients": set(),
                    "urgent": False,
                    "unpriced": 0,
                    "blocked": not known,
                    "reasons": [],
                },
            )
            group["clients"].add(order.get("customer_id") or order["customer_name"].casefold())
            group["urgent"] |= urgent
            group["blocked"] |= (
                not bool(article) or not article.active or not sid or line["supplier_id"] != sid
            )
            config = orders.terms(db, sid)
            agreement = negotiated.get((aid, sid))
            group["lead_days"] = max(
                group.get("lead_days", 0),
                agreement.lead_days
                if agreement and agreement.lead_days is not None
                else config.lead_days,
            )
            cost = (
                agreement.agreed_cost
                if agreement and agreement.agreed_cost is not None
                else config.prices.get(aid, prices.get(aid))
            )
            if cost is None:
                cost = next(
                    (
                        e.unit_cost
                        for e in reversed(entries)
                        if e.article_id == aid and e.unit_cost is not None and e.quantity > 0
                    ),
                    None,
                )
            cost = D(str(cost or 0))
            purchase = group["articles"].setdefault(
                key,
                {
                    "article_id": aid,
                    "description": item["description"],
                    "quantity": ZERO,
                    "unit_cost": cost,
                    "discount": None,
                    "vat": meta.get("vat_rate", config.vat),
                    "allocations": [],
                    "minimum_quantity": agreement.min_quantity if agreement else None,
                    "pack_quantity": agreement.pack_quantity if agreement else None,
                    "precision": meta.get("precision", 0),
                    "unpriced": not bool(cost),
                },
            )
            purchase["quantity"] += needed
            purchase["allocations"].append({"line_id": line["id"], "quantity": needed})
    groups = []
    for group in supplier_groups.values():
        config = orders.terms(db, group["supplier_id"])
        lines = []
        for item in group.pop("articles").values():
            item["quantity"] = purchase_quantity(
                item["quantity"],
                item.pop("precision"),
                item.pop("minimum_quantity"),
                item.pop("pack_quantity"),
            )
            group["unpriced"] += int(item.pop("unpriced"))
            lines.append(orders.SimLine(**item))
        group["lines"] = [line.model_dump(mode="json") for line in lines]
        group["article_count"] = len(lines)
        group["client_count"] = len(group.pop("clients"))
        group["totals"] = orders.calculate(lines, config)
        group["terms"] = config.model_dump(mode="json")
        group["expected_date"] = (date.today() + timedelta(days=group["lead_days"])).isoformat()
        group["blocked"] |= bool(group["unpriced"])
        if not known:
            group["reasons"].append("Stock controlado pelo ERP: quantidades por confirmar.")
        if group["unpriced"]:
            group["reasons"].append("Confirme os custos antes de simular.")
        if any(not line.article_id for line in lines):
            group["reasons"].append("Associe os artigos provisórios nas encomendas dos clientes.")
        if group["blocked"] and not group["reasons"]:
            group["reasons"].append(
                "Confirme o fornecedor e o estado dos artigos nos pedidos antes de simular."
            )
        groups.append(group)
    documents = list(
        db.scalars(
            select(ProcurementDocument).where(
                ProcurementDocument.store_id == store_id,
                ProcurementDocument.kind == "order",
                ProcurementDocument.confirmed_at.is_not(None),
                ProcurementDocument.status != "cancelled",
            )
        )
    )
    sent, partial = 0, 0
    for doc in documents:
        data = buying.document_view(db, doc)
        sent += int(
            any(e["status"] in ("accepted", "received") for e in data.get("email_history", []))
        )
        quantities = [
            (buying.used(db, doc.id, i, "receipt"), buying.accepted(line))
            for i, line in enumerate(json.loads(doc.data)["lines"])
        ]
        partial += int(
            any(received > 0 for received, _ in quantities)
            and any(received < accepted for received, accepted in quantities)
        )
    items = list(consolidated.values())
    supplier_ids = {
        c["supplier_id"] for item in items for c in item["customers"] if c["supplier_id"]
    }
    supplier_catalog = [
        {"supplier_id": row.id, "name": row.name}
        for row in db.scalars(select(BusinessEntity).where(BusinessEntity.id.in_(supplier_ids)))
    ]
    return {
        "store_id": store_id,
        "read_only": True,
        "stock_known": known,
        "metrics": {
            "pending_orders": len(pending_orders),
            "short_articles": sum(i["physical_shortage"] for i in items) if known else None,
            "needs_to_buy": sum(i["needed"] > 0 for i in items) if known else None,
            "sent_orders": sent,
            "partial_orders": partial,
            "ready_orders": len(ready_orders),
            "urgent_orders": len(urgent_orders),
        },
        "articles": items,
        "suppliers": sorted(groups, key=lambda g: (not g["urgent"], g["name"])),
        "supplier_catalog": supplier_catalog,
        "note": "Planeamento por loja: o stock livre e o trânsito sem atribuição são considerados uma única vez. Não cria reservas, compras ou envios.",
    }
