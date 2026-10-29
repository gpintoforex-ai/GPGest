"""Synthetic catalogue data only; the shared fixture creates an isolated test schema."""

import json

import pytest
from app.models import StockArticle, StockEntry
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


def test_maximum_stock_is_validated_and_filtered(stock_case):
    from test_inventory import move, product

    client = stock_case[0]
    bad = client.post(
        "/api/stock/articles",
        json={"code": "MAX", "name": "Máximo", "minimum": "2", "reorder": "8", "maximum": "5"},
    )
    assert bad.status_code == 422 and bad.json()["detail"][0]["loc"] == ["body", "maximum"]
    over = product(stock_case, code="EXCESSO", maximum="8")
    move(stock_case, over, quantity="10", unit_cost="1")
    unlimited = product(stock_case, code="SEMMAX")
    move(stock_case, unlimited, quantity="10", unit_cost="1")
    rows = client.get("/api/stock/catalog?page=1&stock=over").json()["articles"]
    assert [r["code"] for r in rows] == ["EXCESSO"] and rows[0]["maximum"] == "8"


def test_article_details_show_price_history_and_purchases_by_supplier(stock_case):
    from app.models import BusinessEntity
    from test_inventory import login, move, product, view

    client, _, factory, _, _ = stock_case
    with factory.begin() as db:
        first = BusinessEntity(name="Fornecedor Alfa", is_supplier=True)
        second = BusinessEntity(name="Fornecedor Beta", is_supplier=True)
        db.add_all([first, second])
        db.flush()
        alpha, beta = first.id, second.id
    pid = product(stock_case, sale_net="5", supplier_id=alpha)
    row = view(stock_case, pid)
    assert client.post(
        f"/api/stock/articles/{pid}/price", json={"revision": row["revision"], "sale_net": "6"}
    ).status_code == 200
    row = view(stock_case, pid)
    edit = {
        k: row[k] for k in ("code", "name", "sale_net", "minimum", "reorder", "supplier_id")
    }
    # Ficha gravada sem mudar o preço não cria entrada; mudar o IVA cria.
    assert client.put(
        f"/api/stock/articles/{pid}", json={**edit, "revision": row["revision"]}
    ).status_code == 200
    row = view(stock_case, pid)
    assert client.put(
        f"/api/stock/articles/{pid}", json={**edit, "vat_rate": "6", "revision": row["revision"]}
    ).status_code == 200
    move(stock_case, pid, quantity="10", unit_cost="2", supplier_id=alpha, document="FT A/1")
    move(stock_case, pid, quantity="5", unit_cost="3", supplier_id=alpha, document="FT A/2")
    move(stock_case, pid, quantity="4", unit_cost="2.5", supplier_id=beta, document="FT B/1")
    # Uma compra substituída por estorno (edição/anulação de receção) não conta.
    replaced = move(stock_case, pid, quantity="1", unit_cost="9", supplier_id=beta, document="FT B/2")[0]
    with factory.begin() as db:
        original = db.get(StockEntry, replaced)
        db.add(StockEntry(
            article_id=pid, warehouse_id=original.warehouse_id, user_id=original.user_id,
            kind="adjustment", quantity=-original.quantity, unit_cost=original.unit_cost,
            occurred_on=original.occurred_on, request_key="estorno-teste",
            data=json.dumps({"reversal_of": replaced}),
        ))
    details = client.get(f"/api/stock/articles/{pid}/details").json()
    assert [(float(p["sale_net"]), float(p["vat_rate"])) for p in details["price_history"]] == [
        (6, 6), (6, 23), (5, 23)
    ]
    suppliers = {s["supplier_name"]: s for s in details["purchase_suppliers"]}
    a = suppliers["Fornecedor Alfa"]
    assert a["main"] and a["purchases"] == 2 and float(a["quantity"]) == 15
    assert (float(a["last_cost"]), float(a["min_cost"]), float(a["max_cost"])) == (3, 2, 3)
    assert abs(float(a["average_cost"]) - 35 / 15) < 1e-6 and a["last_document"] == "FT A/2"
    b = suppliers["Fornecedor Beta"]
    assert not b["main"] and b["purchases"] == 1 and float(b["max_cost"]) == 2.5
    assert b["last_document"] == "FT B/1"
    # Sem permissão de custos: fornecedores e quantidades sim, custos não.
    login(client, "reader")
    hidden = client.get(f"/api/stock/articles/{pid}/details").json()["purchase_suppliers"]
    assert {s["supplier_name"] for s in hidden} == {"Fornecedor Alfa", "Fornecedor Beta"}
    assert all(s["last_cost"] is None and s["average_cost"] is None for s in hidden)


def test_catalogue_json_matches_fastapi_encoding(stock_case, monkeypatch):
    """O catálogo responde com json.dumps direto: o resultado tem de ser o do FastAPI."""
    from datetime import date, timedelta
    from decimal import Decimal as D

    import app.inventory as inventory
    from app.models import WintouchProduct
    from fastapi.encoders import jsonable_encoder
    from test_inventory import move, product

    client, _, factory, _, _ = stock_case
    lots = product(stock_case, code="LOTES", name="Artigo com lotes ção", lots=True, expiry=True)
    move(stock_case, lots, lot_number="L1", unit_cost="2.3333",
         expiry=(date.today() + timedelta(days=5)).isoformat())
    erp_article = product(stock_case, code="ERPCOPIA")
    with factory.begin() as db:
        erp = WintouchProduct(external_id="x1", enterprise_id="E", code="W1", name="Do ERP",
                              active=True, sale_price=D("4.5"))
        db.add(erp)
        db.flush()
        db.get(StockArticle, erp_article).erp_id = erp.id
    captured = []
    original = inventory.fast_json
    monkeypatch.setattr(inventory, "fast_json", lambda c: captured.append(c) or original(c))
    for url in ("/api/stock/catalog", "/api/stock/catalog?page=1", "/api/stock/catalog?metadata_only=true"):
        response = client.get(url)
        assert response.status_code == 200 and response.headers["content-type"] == "application/json"
        assert response.json() == jsonable_encoder(captured[-1]), url
    article = next(a for a in captured[0]["articles"] if a["code"] == "LOTES")
    assert isinstance(article["nearest_expiry"], date)  # datas e Decimal passam pela conversão


def test_catalogue_stock_filters_leave_out_services(stock_case):
    from test_inventory import product

    client = stock_case[0]
    product(stock_case, code="PORTES", name="Portes de envio", service=True, minimum="0", reorder="0")
    goods = product(stock_case, code="SEMSTOCK", name="Artigo sem stock")
    for state in ("zero", "low", "positive", "unknown"):
        codes = [a["code"] for a in client.get(f"/api/stock/catalog?page=1&stock={state}").json()["articles"]]
        assert "PORTES" not in codes, state
    zero = client.get("/api/stock/catalog?page=1&stock=zero").json()["articles"]
    assert [a["id"] for a in zero] == [goods]
    assert client.get("/api/stock/catalog?page=1&q=PORTES").json()["total"] == 1
