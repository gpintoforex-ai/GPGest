from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest
from app.models import CustomerPurchaseSimulation, StockEntry, StockLot
from app.supply_terms import purchase_quantity
from sqlalchemy import func, select
from test_customer_orders import action, post, prepared, receive, simulation
from test_customer_orders import case as customer_case
from test_customer_orders import request as customer_request
from test_inventory import login, move, product, stock_case  # noqa: F401

URL = "/api/order-cycle/supply-center"


@pytest.fixture
def case(request, monkeypatch):
    return customer_case.__wrapped__(request, monkeypatch)


def center(case):
    result = case[0][0].get(URL, params={"store_id": case[0][4]})
    assert result.status_code == 200, result.text
    return result.json()


def test_shared_stock_is_allocated_once_and_read_is_side_effect_free(case):
    aid = product(case[0], supplier_id=case[1])
    move(case[0], aid, quantity="6", unit_cost="10")
    customer_request(case, aid, "5")
    second, _ = customer_request(case, aid, "5", priority="urgent")
    for _ in range(2):
        data = center(case)
        item = data["articles"][0]
        assert D(str(item["needed"])) == 4
        assert len(item["customers"]) == 2
        assert item["customers"][0]["order_id"] == second["id"]
        assert D(str(item["customers"][0]["stock_cover"])) == 5
        assert data["suppliers"][0]["lines"][0]["quantity"] == "4"
        assert data["metrics"]["pending_orders"] == 2
    with case[0][2]() as db:
        assert db.scalar(select(func.count()).select_from(StockEntry)) == 1
        assert db.scalar(select(func.count()).select_from(CustomerPurchaseSimulation)) == 0


def test_stock_reserved_for_another_customer_is_not_reused(case):
    aid = product(case[0], supplier_id=case[1])
    move(case[0], aid, quantity="5", unit_cost="10")
    first, _ = customer_request(case, aid, "3")
    action(case, first, "reserve", 3, warehouse_id=case[0][3][0])
    second, _ = customer_request(case, aid, "4")
    data = center(case)
    item = data["articles"][0]
    assert D(str(item["reserved"])) == 3
    assert D(str(item["available"])) == 2
    assert D(str(item["needed"])) == 2
    assert data["metrics"]["ready_orders"] == 1
    assert data["suppliers"][0]["lines"][0]["allocations"] == [
        {"line_id": second["lines"][0]["id"], "quantity": "2.000000"}
    ]


def test_assigned_incoming_and_partial_receipt_are_not_counted_twice(case):
    aid = product(case[0], supplier_id=case[1])
    first, _ = customer_request(case, aid, "5")
    sim, _ = simulation(case, [first], aid, quantity="7")
    sim = prepared(case, sim)
    customer_request(case, aid, "4")
    assert D(str(center(case)["articles"][0]["needed"])) == 2
    receive(case, sim, "2")
    data = center(case)
    assert D(str(data["articles"][0]["needed"])) == 2
    assert data["metrics"]["partial_orders"] == 1
    assert data["metrics"]["ready_orders"] == 1


def test_minimum_uses_net_goods_and_shipping_has_independent_rule(case):
    aid = product(case[0], supplier_id=case[1])
    post(
        case[0][0],
        f"/suppliers/{case[1]}/terms",
        {
            "request_key": str(uuid4()),
            "terms": {
                "minimum_cents": 9000,
                "discount": "20",
                "shipping_cents": 1500,
                "prices": {aid: "10"},
            },
        },
    )
    customer_request(case, aid, "10")
    group = center(case)["suppliers"][0]
    assert group["totals"]["gross_cents"] == 10000
    assert group["totals"]["eligible_cents"] == 8000
    assert group["totals"]["missing_cents"] == 1000
    assert group["totals"]["shipping_cents"] == 1500
    assert group["totals"]["total_cents"] > 9000


def test_negotiated_cost_and_buying_multiples_apply_without_duplicate_allocation(case):
    aid = product(case[0], supplier_id=case[1])
    result = case[0][0].put(
        f"/api/stock/articles/{aid}/supplier-terms/{case[1]}",
        json={
            "agreed_cost": "3.25",
            "min_quantity": "5",
            "pack_quantity": "4",
        },
    )
    assert result.status_code == 200, result.text
    customer_request(case, aid, "3")
    group = center(case)["suppliers"][0]
    assert not group["blocked"]
    line = group["lines"][0]
    assert D(line["quantity"]) == 8 and D(line["unit_cost"]) == D("3.25")
    assert sum(D(a["quantity"]) for a in line["allocations"]) == 3
    saved = post(
        case[0][0],
        "/simulations",
        {
            "store_id": case[0][4],
            "supplier_id": case[1],
            "request_key": str(uuid4()),
            "lines": group["lines"],
            "terms": group["terms"],
        },
    )
    assert saved["status"] == "draft"
    assert center(case)["suppliers"] == []


def test_unknown_erp_stock_blocks_purchase_proposal(case, monkeypatch):
    aid = product(case[0], supplier_id=case[1])
    customer_request(case, aid, "3")
    monkeypatch.setattr("app.procurement.policy", lambda _: {"authority": {"stock": "erp"}})
    data = center(case)
    assert not data["stock_known"]
    assert data["articles"][0]["available"] is None
    assert data["suppliers"][0]["blocked"]


def test_supply_center_protects_costs_from_operator(case):
    login(case[0][0], "operator")
    assert case[0][0].get(URL, params={"store_id": case[0][4]}).status_code == 403


def test_expired_reservation_requires_review_and_is_not_ready(case):
    aid = product(case[0], supplier_id=case[1], lots=True, expiry=True)
    move(case[0], aid, quantity="2", unit_cost="1", lot_number="VALIDADE",
         expiry=(date.today() + timedelta(days=5)).isoformat())
    row, _ = customer_request(case, aid, "2")
    action(case, row, "reserve", 2, warehouse_id=case[0][3][0])
    with case[0][2].begin() as db:
        lot = db.scalar(select(StockLot).where(StockLot.article_id == aid))
        lot.expiry = (date.today() - timedelta(days=1)).isoformat()
    data = center(case)
    assert data["metrics"]["ready_orders"] == 0
    assert data["articles"][0]["reservation_warning"]
    assert data["articles"][0]["customers"][0]["state"] == "Reserva por rever"


def test_packs_do_not_override_expiry_budget_or_maximum_ceiling():
    assert purchase_quantity(D("3"), minimum=D("5"), multiple=D("4")) == 8
    assert purchase_quantity(D("3"), multiple=D("4"), ceiling=D("3")) == 0
    assert purchase_quantity(D("0"), minimum=D("5")) == 0
    assert purchase_quantity(D("1"), precision=0, multiple=D("1.5")) == 3


def test_recommendation_does_not_repeat_extra_stock_already_in_a_draft(case, monkeypatch):
    monkeypatch.setattr("app.order_cycle.policy", lambda _: {"mode": "local"})
    aid = product(case[0], supplier_id=case[1], minimum="0", reorder="10")
    row, _ = customer_request(case, aid, "3")
    simulation(case, [row], aid, quantity="8")
    response = case[0][0].get("/api/order-cycle/recommendations", params={"store_id": case[0][4]})
    assert response.status_code == 200, response.text
    item = next(r for r in response.json()["items"] if r["article_id"] == aid)
    assert D(item["draft_replenishment"]) == 5
    assert D(item["quantity"]) == 5
    assert D(item["free"]) == 0 and D(item["incoming"]) == 0


def test_recommendation_windows_and_high_stock_do_not_invent_demand(case, monkeypatch):
    monkeypatch.setattr("app.order_cycle.policy", lambda _: {"mode": "local"})
    aid = product(case[0], supplier_id=case[1], minimum="0", reorder="0")
    move(case[0], aid, quantity="100", unit_cost="1")
    for days, amount in [(10, "5"), (40, "10"), (80, "15")]:
        move(
            case[0],
            aid,
            kind="sale",
            quantity=amount,
            occurred_on=(date.today() - timedelta(days=days)).isoformat(),
        )
    result = case[0][0].get("/api/order-cycle/recommendations", params={"store_id": case[0][4]})
    assert result.status_code == 200, result.text
    item = next(r for r in result.json()["items"] if r["article_id"] == aid)
    assert {key: D(value) for key, value in item["sales_history"].items()} == {
        "30": D(5),
        "60": D(15),
        "90": D(30),
    }
    assert D(item["quantity"]) == 0
    assert D(item["coverage_days"]) > 90


def test_recommendation_pack_cannot_force_excess_above_maximum(case, monkeypatch):
    monkeypatch.setattr("app.order_cycle.policy", lambda _: {"mode": "local"})
    aid = product(case[0], supplier_id=case[1], minimum="0", reorder="20")
    response = case[0][0].put(
        f"/api/stock/articles/{aid}/supplier-terms/{case[1]}",
        json={
            "agreed_cost": "1",
            "min_quantity": "12",
            "pack_quantity": "6",
        },
    )
    assert response.status_code == 200, response.text
    response = case[0][0].post(
        "/api/order-cycle/replenishment",
        json={
            "request_key": str(uuid4()),
            "store_id": case[0][4],
            "article_id": aid,
            "maximum_stock": "10",
        },
    )
    assert response.status_code == 200, response.text
    response = case[0][0].get("/api/order-cycle/recommendations", params={"store_id": case[0][4]})
    assert response.status_code == 200, response.text
    item = next(r for r in response.json()["items"] if r["article_id"] == aid)
    assert D(item["quantity"]) == 0
    assert any("mínimos/múltiplos" in w for w in item["warnings"])
