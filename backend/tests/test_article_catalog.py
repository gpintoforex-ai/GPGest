"""Synthetic catalogue data only; the shared fixture creates an isolated test schema."""

import json

import pytest
from app.models import StockArticle
from sqlalchemy import event
from test_inventory import stock_case as stock_case


@pytest.mark.parametrize("count", [1000, 10000])
def test_catalogue_pages_and_filters_without_loading_other_history(setup, monkeypatch, count):
    client, _, factory = setup
    monkeypatch.setattr("app.inventory.policy", lambda _: {"mode": "local"})
    with factory.begin() as db:
        db.add_all(
            StockArticle(
                code=f"A{i:05}",
                name=f"Artigo {i:05}",
                data=json.dumps(
                    {
                        "barcodes": [f"EAN{i:05}"],
                        "family": "Família A" if i % 2 else "Família B",
                        "category": "Categoria",
                        "reference": f"REF{i:05}",
                        "minimum": "5",
                    }
                ),
            )
            for i in range(count)
        )
    statements = []
    bind = factory.kw["bind"]

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement.lower(), parameters))

    event.listen(bind, "before_cursor_execute", capture)
    try:
        meta = client.get("/api/stock/catalog?metadata_only=true").json()
        assert meta["articles"] == [] and meta["total_all"] == count
        assert not any("from stock_entries" in sql for sql, _ in statements)
        statements.clear()
        result = client.get("/api/stock/catalog?page=2&size=25&sort=code").json()
        assert result["total"] == count and result["pages"] == count // 25
        assert len(result["articles"]) == 25
        assert result["articles"][0]["code"] == "A00025"
        ledger_queries = [
            (sql, params) for sql, params in statements if "from stock_entries" in sql
        ]
        assert ledger_queries and all(
            "stock_entries.article_id in" in sql for sql, _ in ledger_queries
        )
        assert client.get("/api/stock/catalog?page=1&q=EAN00003").json()["total"] == 1
        assert (
            client.get("/api/stock/catalog?page=1&q=REF00003").json()["articles"][0]["code"]
            == "A00003"
        )
        filtered = client.get(
            "/api/stock/catalog?page=1&family=Família A&category=Categoria&active=true"
        ).json()
        assert filtered["total"] == count // 2
        assert client.get("/api/stock/catalog?page=99999&size=100").json()["page"] == count // 100
        assert (
            client.get("/api/stock/catalog?page=1&q=%25").json()["total"] == 0
        )  # literal %, not SQL wildcard
        assert client.get("/api/stock/catalog?page=1&sort=unsupported").status_code == 422
        assert client.get("/api/stock/catalog?page=1&stock=unsupported").status_code == 422
    finally:
        event.remove(bind, "before_cursor_execute", capture)


def test_catalogue_cost_permissions_and_stock_filters(setup, monkeypatch):
    client, _, factory = setup
    monkeypatch.setattr("app.inventory.policy", lambda _: {"mode": "local"})
    with factory.begin() as db:
        db.add(StockArticle(code="LOCAL", name="Água sem movimentos", data='{"minimum":"5"}'))
    assert client.get("/api/stock/catalog?page=1&q=agua").json()["total"] == 1
    assert client.get("/api/stock/catalog?page=1&q=ÁGUA").json()["total"] == 1
    low = client.get("/api/stock/catalog?page=1&stock=low").json()
    assert low["total"] == 1
    assert client.get("/api/stock/catalog?page=1&stock=positive").json()["total"] == 0
    assert client.get("/api/stock/catalog?page=1&expiry=missing").json()["total"] == 1
    assert (
        client.post(
            "/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}
        ).status_code
        == 200
    )
    result = client.get("/api/stock/catalog?page=1").json()
    assert not result["permissions"]["view_cost"]
    assert result["articles"][0]["average_cost"] is None
    assert result["articles"][0]["last_cost"] is None
    assert (
        client.put(
            "/api/stock/articles/" + result["articles"][0]["id"],
            json={"code": "LOCAL", "name": "Tentativa"},
        ).status_code
        == 403
    )


def test_catalogue_expiry_filters_respect_store_access(stock_case):
    from datetime import date, timedelta

    from test_inventory import login, move, product

    client = stock_case[0]
    identifier = product(stock_case, lots=True, expiry=True)
    move(
        stock_case,
        identifier,
        lot_number="NEAR",
        expiry=(date.today() + timedelta(days=2)).isoformat(),
        unit_cost="2",
    )
    move(
        stock_case,
        identifier,
        warehouse_id=stock_case[3][2],
        lot_number="EXPIRED",
        expiry=(date.today() - timedelta(days=2)).isoformat(),
        unit_cost="2",
    )
    assert client.get("/api/stock/catalog?page=1&expiry=expired").json()["total"] == 1
    login(client, "reader")
    assert client.get("/api/stock/catalog?page=1&expiry=expired").json()["total"] == 0
    result = client.get("/api/stock/catalog?page=1&expiry=near").json()
    assert result["total"] == 1
    assert float(result["articles"][0]["available_quantity"]) == 10
