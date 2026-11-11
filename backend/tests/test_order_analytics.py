"""Verify source populations, historical deadlines and independent commercial goals."""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from uuid import uuid4

import pytest
from app.customer_orders import CommercialTerms, SimLine, calculate
from app.models import CustomerOrder, CustomerOrderEvent, ProcurementDocument, StockEntry
from app.order_cycle import automated_notifications
from sqlalchemy import func, select
from test_customer_orders import action, simulation, smtp
from test_customer_orders import case as customer_case
from test_customer_orders import request as customer_request
from test_inventory import login, move, product, stock_case  # noqa: F401


@pytest.fixture
def case(request, monkeypatch):
    monkeypatch.setattr("app.order_cycle.policy", lambda _: {"mode": "local", "provider": "none"})
    return customer_case.__wrapped__(request, monkeypatch)


def report(case, **params):
    today = date.today()
    response = case[0][0].get(
        "/api/order-cycle/analytics",
        params={
            "start": today - timedelta(days=30),
            "end": today,
            "store_id": case[0][4],
            **params,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "net,minimum_gap,shipping_gap,shipping",
    [(200, 5000, 10000, 1200), (250, 0, 5000, 1200), (300, 0, 0, 0)],
)
def test_minimum_and_shipping_are_independent(net, minimum_gap, shipping_gap, shipping):
    config = CommercialTerms(minimum_cents=25000, free_shipping_cents=30000, shipping_cents=1200)
    totals = calculate([SimLine(description="Produto", quantity=D(1), unit_cost=D(net))], config)
    assert totals["missing_cents"] == minimum_gap
    assert totals["shipping_missing_cents"] == shipping_gap
    assert totals["shipping_cents"] == shipping


def test_shipping_uses_discounted_net_and_distinguishes_unconfigured_and_zero():
    lines = [SimLine(description="Produto", quantity=D(1), unit_cost=D(300))]
    totals = calculate(lines, CommercialTerms(discount=D(10), free_shipping_cents=30000))
    assert totals["shipping_missing_cents"] == 3000
    assert calculate(lines, CommercialTerms())["shipping_missing_cents"] is None
    assert calculate(lines, CommercialTerms(free_shipping_cents=0))["free_shipping_reached"]


def test_partial_stock_does_not_trigger_automatic_complete_order_email(case, monkeypatch):
    calls = smtp(monkeypatch)
    aid, second = product(case[0]), product(case[0])
    move(case[0], aid)
    row, _ = customer_request(
        case,
        preferred_channel="email",
        lines=[
            {"article_id": aid, "description": "Disponível", "quantity": "2"},
            {"article_id": second, "description": "Em falta", "quantity": "1"},
        ],
    )
    # Select the actual first article; row order is a storage concern.
    first = next(line for line in row["lines"] if line["article_id"] == aid)
    response = case[0][0].post(
        f"/api/customer-orders/lines/{first['id']}/reserve",
        json={
            "revision": row["revision"],
            "request_key": str(uuid4()),
            "quantity": "2",
            "warehouse_id": case[0][3][0],
        },
    )
    assert response.status_code == 200, response.text
    partial = response.json()
    assert partial["ready"] and not partial["fully_ready"]
    assert report(case)["states"][2]["count"] == 1
    enabled = case[0][0].post(
        f"/api/order-cycle/automation/{case[0][4]}",
        json={"enabled": True, "request_key": str(uuid4())},
    )
    assert enabled.status_code == 200, enabled.text
    with case[0][2]() as db:
        assert automated_notifications(db)["sent"] == 0
    assert not calls


def test_real_delivery_deadline_snapshot_and_side_effect_free_report(case):
    aid = product(case[0])
    move(case[0], aid)
    row, _ = customer_request(case, aid, quantity="2", due_date=date.today().isoformat())
    row = action(case, row, "reserve", 2, warehouse_id=case[0][3][0])
    assert row["fully_ready"]
    row = action(case, row, "deliver", 2, reason="Entregue ao cliente")
    with case[0][2].begin() as db:
        db.get(CustomerOrder, row["id"]).created_at = datetime.now(timezone.utc) - timedelta(days=2)
        event = db.scalar(
            select(CustomerOrderEvent).where(
                CustomerOrderEvent.order_id == row["id"], CustomerOrderEvent.action == "deliver"
            )
        )
        assert json.loads(event.data)["due_date_at_delivery"] == date.today().isoformat()

    def counts():
        with case[0][2]() as db:
            return [
                db.scalar(select(func.count()).select_from(model))
                for model in (StockEntry, ProcurementDocument, CustomerOrderEvent)
            ]

    before = counts()
    result = report(case)
    assert counts() == before
    assert result["summary"]["created"] == result["summary"]["completed"] == 1
    assert result["summary"]["on_time_percent"] == 100
    assert result["summary"]["average_days"] == 2
    assert sum(point["completed"] for point in result["series"]) == 1
    assert result["completed_orders"][0]["id"] == row["id"]
    # Historical rows without an immutable promise cannot inherit the current deadline.
    with case[0][2].begin() as db:
        event = db.scalar(
            select(CustomerOrderEvent).where(
                CustomerOrderEvent.order_id == row["id"], CustomerOrderEvent.action == "deliver"
            )
        )
        detail = json.loads(event.data)
        detail.pop("due_date_at_delivery")
        event.data = json.dumps(detail)
    historical = report(case)["summary"]
    assert historical["on_time_percent"] is None
    assert historical["unknown_deadlines"] == 1 and historical["dated_deliveries"] == 0


def test_sales_drilldown_excludes_transfers_and_drafts_do_not_create_stock(case):
    aid = product(case[0], supplier_id=case[1])
    move(case[0], aid, quantity="10")
    move(case[0], aid, kind="sale", quantity="3")
    sim, _ = simulation(
        case, [], aid, quantity="2", terms={"minimum_cents": 10000, "free_shipping_cents": 20000}
    )
    result = report(case)
    assert result["rotation"][0]["quantity"] == "3.000000"
    assert result["rotation"][0]["movement_count"] == 1
    assert result["rotation"][0]["movements"][0]["quantity"]
    assert result["drafts"][0]["id"] == sim["id"]
    assert result["summary"]["drafts_below_minimum"] == 1
    outside = report(case, start="2020-01-01", end="2020-01-31")
    assert not outside["rotation"] and outside["drafts"][0]["id"] == sim["id"]


def test_cancelled_orders_are_not_successful_deliveries_and_roles_are_enforced(case):
    row, _ = customer_request(case, product(case[0]), quantity="2")
    action(case, row, "cancel", 2, reason="Cliente cancelou")
    result = report(case)
    assert result["summary"]["completed"] == 0
    assert next(state for state in result["states"] if state["id"] == "cancelled")["count"] == 1
    login(case[0][0], "operator")
    assert (
        case[0][0]
        .get("/api/order-cycle/analytics", params={"start": date.today(), "end": date.today()})
        .status_code
        == 403
    )


def test_invalid_range_is_rejected(case):
    assert (
        case[0][0].get("/api/order-cycle/analytics?start=2026-02-01&end=2026-01-01").status_code
        == 422
    )


def test_manager_scope_does_not_mix_other_store_orders_or_sales(case):
    from app.models import StockWarehouse, User

    aid = product(case[0])
    own, _ = customer_request(case, aid, quantity="2")
    with case[0][2].begin() as db:
        other_store = db.get(StockWarehouse, case[0][3][2]).store_id
        db.get(User, case[0][1]["operator"]).role = "gestor"
    customer_request(case, aid, quantity="3", store_id=other_store)
    login(case[0][0], "operator")
    result = report(case, store_id="")
    assert [row["id"] for row in result["orders"]] == [own["id"]]
    assert (
        case[0][0]
        .get(
            "/api/order-cycle/analytics",
            params={"start": date.today(), "end": date.today(), "store_id": other_store},
        )
        .status_code
        == 403
    )


def test_replenishment_uses_supplier_period_then_article_override(case):
    from test_customer_orders import post

    aid = product(case[0], supplier_id=case[1], minimum="3")
    post(
        case[0][0],
        f"/suppliers/{case[1]}/terms",
        {"request_key": str(uuid4()), "terms": {"sales_period_days": 15}},
    )
    path = f"/api/order-cycle/recommendations?store_id={case[0][4]}&supplier_id={case[1]}"
    assert case[0][0].get(path).json()["items"][0]["period_days"] == 15
    saved = case[0][0].post(
        "/api/order-cycle/replenishment",
        json={
            "request_key": str(uuid4()),
            "store_id": case[0][4],
            "article_id": aid,
            "history_days": 60,
        },
    )
    assert saved.status_code == 200, saved.text
    assert case[0][0].get(path).json()["items"][0]["period_days"] == 60
    assert case[0][0].get(path + "&period_days=7").json()["items"][0]["period_days"] == 7
