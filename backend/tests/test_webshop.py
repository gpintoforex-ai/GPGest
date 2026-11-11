# ruff: noqa: F811
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from app.models import (
    CustomerOrder,
    CustomerOrderReservation,
    Movement,
    StockEntry,
    WebshopConnection,
)
from sqlalchemy import func, select
from test_inventory import login, move, product, stock_case  # noqa: F401, F811

BASE = "/api/webshop"


def connection(case, **extra):
    c, _, _, warehouses, store = case
    response = c.post(
        BASE + "/connections",
        json={"name": "Raizpura", "store_id": store, "warehouse_id": warehouses[0], **extra},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    return result, {
        "Authorization": "Bearer " + result["token"],
        "X-Raizpura-Connection": result["id"],
    }


def payload(article, **extra):
    return {
        "external_id": "site:" + str(uuid4()),
        "sequence": 1,
        "status": "processing",
        "customer_name": "Cliente de teste",
        "email": "teste@example.test",
        "total_cents": 1230,
        "lines": [{"article_id": article, "quantity": 2, "total_net_cents": 1000}],
        **extra,
    }


def test_credentials_scope_rotation_and_revocation(stock_case):
    c, _, factory, warehouses, _ = stock_case
    row, headers = connection(stock_case)
    assert c.get(BASE + "/health", headers=headers).status_code == 200
    assert c.get(BASE + "/catalog").status_code == 401
    assert (
        c.get(BASE + "/catalog", headers=headers | {"Authorization": "Bearer invalid"}).status_code
        == 401
    )
    with factory() as db:
        saved = db.get(WebshopConnection, row["id"])
        assert saved.token_hash != row["token"]
    assert "token_hash" not in c.get(BASE + "/connections").text
    assert (
        c.post(
            BASE + "/connections", json={"store_id": stock_case[4], "warehouse_id": warehouses[2]}
        ).status_code
        == 422
    )
    rotated = c.post(BASE + f"/connections/{row['id']}/rotate", json={}).json()
    assert c.get(BASE + "/health", headers=headers).status_code == 401
    headers["Authorization"] = "Bearer " + rotated["token"]
    assert c.get(BASE + "/health", headers=headers).status_code == 200
    login(c, "operator")
    assert (
        c.post(
            BASE + "/connections", json={"store_id": stock_case[4], "warehouse_id": warehouses[0]}
        ).status_code
        == 403
    )
    login(c, "admin")
    assert c.post(BASE + f"/connections/{row['id']}/revoke", json={}).status_code == 200
    assert c.get(BASE + "/health", headers=headers).status_code == 401


def test_catalog_and_order_idempotence_reservations_and_cancel(stock_case):
    c, _, factory, _, _ = stock_case
    article = product(
        stock_case, family="Vitaminas", brand="Marca de teste", internal_notes="NÃO EXPORTAR"
    )
    move(stock_case, article, quantity="10")
    _, headers = connection(stock_case)
    catalog = c.get(BASE + "/catalog", headers=headers).json()
    assert catalog["products"][0]["available"] == 10
    assert catalog["products"][0]["family"] == "Vitaminas"
    assert "NÃO EXPORTAR" not in str(catalog)
    data = payload(article)
    first = c.post(BASE + "/orders", json=data, headers=headers)
    assert first.status_code == 200, first.text
    again = c.post(BASE + "/orders", json=data, headers=headers)
    assert again.json()["order_id"] == first.json()["order_id"]
    assert c.get(BASE + "/catalog", headers=headers).json()["products"][0]["available"] == 8
    changed = data | {"total_cents": 1500}
    assert c.post(BASE + "/orders", json=changed, headers=headers).status_code == 409
    changed["sequence"] = 2
    assert c.post(BASE + "/orders", json=changed, headers=headers).status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(CustomerOrder)) == 1
        assert db.scalar(select(func.sum(CustomerOrderReservation.quantity))) == 2
        assert db.scalar(select(func.count()).select_from(StockEntry)) == 1
        assert db.scalar(select(func.count()).select_from(Movement)) == 0
    cancel = c.post(
        BASE + "/orders", json=data | {"status": "cancelled", "sequence": 2}, headers=headers
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["state"] == "cancelled"
    assert c.post(BASE + "/orders", json=data, headers=headers).json()["state"] == "cancelled"
    assert c.get(BASE + "/catalog", headers=headers).json()["products"][0]["available"] == 10
    assert c.post(BASE + "/orders", json=data | {"sequence": 3}, headers=headers).status_code == 409


def test_shortage_rollback_and_competing_orders(stock_case):
    c, _, factory, _, _ = stock_case
    article = product(stock_case)
    move(stock_case, article, quantity="2")
    _, headers = connection(stock_case)
    bad = payload(article, lines=[{"article_id": article, "quantity": 3, "total_net_cents": 1500}])
    assert c.post(BASE + "/orders", json=bad, headers=headers).status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(CustomerOrder)) == 0
    data = [payload(article), payload(article)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda d: c.post(BASE + "/orders", json=d, headers=headers).status_code, data)
        )
    assert sorted(results) == [200, 409]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(CustomerOrder)) == 1
        assert db.scalar(select(func.sum(CustomerOrderReservation.quantity))) == 2


def test_expired_batches_pagination_and_connection_order_isolation(stock_case):
    from datetime import date

    c, _, _, _, _ = stock_case
    article = product(stock_case, lots=True, expiry=True)
    move(
        stock_case,
        article,
        quantity="5",
        lot_number="EXP",
        expiry=(date.today() - timedelta(days=1)).isoformat(),
    )
    product(stock_case)
    _, headers = connection(stock_case)
    one = c.get(BASE + "/catalog?limit=1", headers=headers).json()
    assert len(one["products"]) == 1 and one["next_cursor"]
    two = c.get(BASE + "/catalog?limit=1&cursor=" + one["next_cursor"], headers=headers).json()
    all_rows = one["products"] + two["products"]
    assert next(p for p in all_rows if p["id"] == article)["available"] == 0
    assert c.post(BASE + "/orders", json=payload(article), headers=headers).status_code == 409
    assert c.get(BASE + "/orders/other-site:1", headers=headers).status_code == 404


def test_delivery_and_erp_authority(stock_case, monkeypatch):
    from app.customer_orders import QuantityChange, line_action
    from app.models import CustomerOrderLine, User

    c, _, factory, warehouses, _ = stock_case
    article = product(stock_case)
    move(stock_case, article, quantity="5")
    conn, headers = connection(stock_case)
    data = payload(article)
    result = c.post(BASE + "/orders", json=data, headers=headers)
    assert result.status_code == 200, result.text
    with factory() as db:
        line = db.scalar(
            select(CustomerOrderLine).where(CustomerOrderLine.order_id == result.json()["order_id"])
        )
        user = db.get(User, db.get(WebshopConnection, conn["id"]).user_id)
        line_action(
            line.id,
            "deliver",
            QuantityChange(
                request_key=str(uuid4()),
                revision=1,
                quantity="2",
                warehouse_id=warehouses[0],
                reason="Entrega de teste",
            ),
            user,
            db,
        )
    assert (
        c.get(BASE + "/orders/" + data["external_id"], headers=headers).json()["state"]
        == "delivered"
    )
    _, other = connection(stock_case)
    assert c.get(BASE + "/orders/" + data["external_id"], headers=other).status_code == 404
    monkeypatch.setattr("app.inventory.stock_from_erp", lambda db: True)
    assert c.get(BASE + "/catalog", headers=headers).json()["products"][0]["available"] is None
    assert c.post(BASE + "/orders", json=payload(article), headers=headers).status_code == 409


def test_cancel_before_create_preserves_version(stock_case):
    c, _, factory, _, _ = stock_case
    article = product(stock_case)
    _, headers = connection(stock_case)
    data = payload(article)
    cancelled = c.post(
        BASE + "/orders", json=data | {"sequence": 3, "status": "cancelled"}, headers=headers
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    older = c.post(BASE + "/orders", json=data, headers=headers)
    assert older.json()["state"] == "cancelled"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(CustomerOrder)) == 1
        assert db.scalar(select(func.count()).select_from(CustomerOrderReservation)) == 0


def test_reservations_use_portuguese_expiry_day(stock_case, monkeypatch):
    from datetime import date
    from types import SimpleNamespace

    from app.models import StockLot

    c, _, factory, _, _ = stock_case
    article = product(stock_case, lots=True, expiry=True)
    move(
        stock_case,
        article,
        quantity="5",
        lot_number="EXPIRED",
        expiry=(date.today() - timedelta(days=1)).isoformat(),
    )
    move(
        stock_case,
        article,
        quantity="5",
        lot_number="VALID",
        expiry=(date.today() + timedelta(days=10)).isoformat(),
    )
    _, headers = connection(stock_case)
    # Simular o servidor ainda no dia anterior; a loja usa explicitamente Portugal.
    monkeypatch.setattr(
        "app.customer_orders.date", SimpleNamespace(today=lambda: date.today() - timedelta(days=1))
    )
    response = c.post(BASE + "/orders", json=payload(article), headers=headers)
    assert response.status_code == 200, response.text
    with factory() as db:
        lot_ids = list(db.scalars(select(CustomerOrderReservation.lot_id)))
        assert lot_ids
        assert all(db.get(StockLot, identifier).number == "VALID" for identifier in lot_ids)
