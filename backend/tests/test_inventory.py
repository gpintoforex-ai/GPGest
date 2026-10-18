import importlib
import json
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import (
    Register,
    StockEntry,
    StockInventory,
    WintouchProduct,
    WintouchSaleRecord,
)
from sqlalchemy import inspect, select, text


@pytest.fixture
def stock_case(setup, monkeypatch):
    client, ids, factory = setup
    monkeypatch.setattr("app.inventory.policy", lambda _: {"mode": "local", "provider": "none"})
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        other = db.get(Register, ids["registers"][2]).store_id
    warehouses = []
    for store_id, name in ((store, "Principal"), (store, "Reserva"), (other, "Outra loja")):
        result = client.post("/api/stock/warehouses", json={"store_id": store_id, "name": name})
        assert result.status_code == 200, result.text
        warehouses.append(result.json()["id"])
    return client, ids, factory, warehouses, store


def test_warehouse_edit_validates_name_and_preserves_store(stock_case):
    c, _, _, warehouses, store = stock_case
    response = c.post(f"/api/stock/warehouses/{warehouses[0]}", json={"store_id": store, "name": "Principal atualizado"})
    assert response.status_code == 200
    catalog = c.get("/api/stock/catalog").json()
    assert next(w for w in catalog["warehouses"] if w["id"] == warehouses[0])["name"] == "Principal atualizado"
    assert c.post(f"/api/stock/warehouses/{warehouses[0]}", json={"store_id": store, "name": "Reserva"}).status_code == 422
    assert c.post(f"/api/stock/warehouses/{warehouses[0]}", json={"store_id": store, "name": " "}).status_code == 422
    assert c.post("/api/stock/warehouses/missing", json={"store_id": store, "name": "Nome"}).status_code == 404


def product(case, **extra):
    data = {
        "code": "P-" + uuid4().hex[:8],
        "name": "Artigo de teste",
        "sale_net": "5",
        "minimum": "2",
        "reorder": "8",
        **extra,
    }
    response = case[0].post("/api/stock/articles", json=data)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def move(case, product_id, kind="purchase", quantity="10", **extra):
    data = {
        "article_id": product_id,
        "warehouse_id": case[3][0],
        "kind": kind,
        "quantity": quantity,
        "reason": "Teste isolado",
        "request_key": str(uuid4()),
        **extra,
    }
    result = case[0].post("/api/stock/movements", json=data)
    assert result.status_code == 200, result.text
    return result.json()["ids"]


def view(case, product_id):
    return next(
        r for r in case[0].get("/api/stock/catalog").json()["articles"] if r["id"] == product_id
    )


def report(case, **params):
    response = case[0].get(
        "/api/stock/report",
        params={"start": "2020-01-01", "end": date.today().isoformat(), **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


def configure(case, **changes):
    data = case[0].get("/api/stock/catalog").json()["settings"]
    data.update(changes)
    result = case[0].post("/api/stock/settings", json=data)
    assert result.status_code == 200, result.text


def login(client, role):
    response = client.post(
        "/api/auth/login", json={"email": f"{role}@test.pt", "password": "PasswordTest123!"}
    )
    assert response.status_code == 200, response.text


def test_moving_average_frozen_sales_return_loss_and_transfer(stock_case):
    c, _, factory, warehouses, _ = stock_case
    pid = product(stock_case)
    move(stock_case, pid, unit_cost="2")
    move(stock_case, pid, unit_cost="4")
    assert D(view(stock_case, pid)["average_cost"]) == 3
    sale = move(stock_case, pid, "sale", "5", net_sales="25", direct_cost="1")[0]
    with factory() as db:
        assert db.get(StockEntry, sale).unit_cost == 3
    # New price cannot rewrite historical sale costs or margin.
    row = view(stock_case, pid)
    assert (
        c.post(
            f"/api/stock/articles/{pid}/price", json={"revision": row["revision"], "sale_net": "99"}
        ).status_code
        == 200
    )
    move(stock_case, pid, "customer_return", "2", original_id=sale)
    move(stock_case, pid, "loss", "2")
    transfers = move(stock_case, pid, "transfer", "3", target_id=warehouses[1])
    assert len(transfers) == 2
    assert D(view(stock_case, pid)["quantity"]) == 15
    results = report(stock_case)
    assert D(results["totals"]["net_sales"]) == 15
    assert D(results["totals"]["gross_margin"]) == 6
    assert D(results["totals"]["loss_value"]) == 6
    assert D(results["rows"][0]["contribution"]) == -1
    assert D(report(stock_case, warehouse_id=warehouses[1])["rows"][0]["quantity"]) == 3
    assert c.delete(f"/api/stock/articles/{pid}").status_code == 409


def test_multilot_fefo_override_and_expired_stock(stock_case):
    c, _, factory, _, _ = stock_case
    pid = product(stock_case, lots=True, expiry=True)
    soon = (date.today() + timedelta(days=5)).isoformat()
    later = (date.today() + timedelta(days=40)).isoformat()
    move(stock_case, pid, quantity="5", unit_cost="2", lot_number="A", expiry=soon)
    move(stock_case, pid, quantity="5", unit_cost="4", lot_number="B", expiry=later)
    lots = c.get("/api/stock/lots").json()["rows"]
    later_id = next(lot["id"] for lot in lots if lot["number"] == "B")
    body = {
        "article_id": pid,
        "warehouse_id": stock_case[3][0],
        "kind": "sale",
        "quantity": "1",
        "reason": "Teste FEFO",
        "request_key": str(uuid4()),
        "lot_id": later_id,
    }
    assert c.post("/api/stock/movements", json=body).status_code == 422
    body["override_reason"] = "Pedido específico do cliente"
    assert c.post("/api/stock/movements", json=body).status_code == 200
    sale = move(stock_case, pid, "sale", "6", net_sales="30")
    with factory() as db:
        assert [db.get(StockEntry, i).quantity for i in sale] == [D(-5), D(-1)]
        assert all(db.get(StockEntry, i).unit_cost == 3 for i in sale)
    expired_pid = product(stock_case, lots=True, expiry=True)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    move(stock_case, expired_pid, quantity="2", lot_number="OLD", expiry=yesterday, unit_cost="7")
    blocked = {
        **body,
        "article_id": expired_pid,
        "lot_id": None,
        "request_key": str(uuid4()),
        "override_reason": "",
    }
    assert c.post("/api/stock/movements", json=blocked).status_code == 409
    blocked["occurred_on"] = (date.today() - timedelta(days=3)).isoformat()
    assert c.post("/api/stock/movements", json=blocked).status_code == 409
    assert D(view(stock_case, expired_pid)["quantity"]) == 2
    move(stock_case, expired_pid, "expired", "2")
    assert D(view(stock_case, expired_pid)["quantity"]) == 0


def test_unknown_cost_missing_expiry_zero_denominators_and_recoverable_vat(stock_case):
    pid = product(stock_case, lots=True, expiry=True)
    move(stock_case, pid, quantity="3", lot_number="UNKNOWN")
    move(stock_case, pid, "sale", "1", net_sales="10")
    batch = stock_case[0].get("/api/stock/lots?state=missing").json()["rows"][0]
    assert batch["state"] == "missing" and batch["cost_value"] is None
    data = report(stock_case)
    assert data["coverage"]["sales_unknown"] == 1
    assert data["rows"][0]["gross_margin"] is None
    assert D(data["totals"]["gross_margin"]) == 0 and D(data["totals"]["net_sales"]) == 10
    configure(stock_case, recoverable_vat=False)
    p2 = product(stock_case, vat_rate="23", sale_net="0")
    move(stock_case, p2, quantity="1", unit_cost="10")
    assert D(view(stock_case, p2)["average_cost"]) == D("12.3")
    from app.inventory_reports import profitability

    assert profitability(D(0), D(0))["margin_pct"] is None
    assert profitability(D(0), D(0))["markup_pct"] is None


def test_inventory_preserves_intervening_movements_and_is_idempotent(stock_case):
    c, _, factory, warehouses, _ = stock_case
    pid = product(stock_case)
    move(stock_case, pid, quantity="10", unit_cost="2")
    response = c.post(
        "/api/stock/inventories", json={"warehouse_id": warehouses[0], "article_ids": [pid]}
    )
    assert response.status_code == 200, response.text
    inv = response.json()
    move(stock_case, pid, quantity="5", unit_cost="2")
    body = {
        "revision": inv["revision"],
        "confirm": True,
        "lines": [{"article_id": pid, "counted": "8", "reason": "Diferença na contagem inicial"}],
    }
    response = c.post(f"/api/stock/inventories/{inv['id']}", json=body)
    assert response.status_code == 200, response.text
    assert D(view(stock_case, pid)["quantity"]) == 13
    assert c.post(f"/api/stock/inventories/{inv['id']}", json=body).status_code == 200
    assert D(view(stock_case, pid)["quantity"]) == 13
    with factory() as db:
        assert db.get(StockInventory, inv["id"]).status == "confirmed"
        assert len([r for r in db.scalars(select(StockEntry)) if r.kind == "adjustment"]) == 1


def test_permissions_cost_masking_warehouse_scope_and_negative_exception(stock_case):
    c, ids, _, warehouses, _ = stock_case
    pid = product(stock_case)
    move(stock_case, pid, unit_cost="2")
    configure(
        stock_case,
        allow_negative=True,
        permissions={ids["operator"]: {"change_price": True, "adjust_stock": True}},
    )
    login(c, "operator")
    catalogue = c.get("/api/stock/catalog").json()
    assert len(catalogue["warehouses"]) == 2
    assert catalogue["articles"][0]["average_cost"] is None
    assert c.get("/api/stock/movements").json()["rows"][0]["unit_cost"] is None
    assert c.get(f"/api/stock/lots?warehouse_id={warehouses[2]}").status_code == 403
    assert (
        c.post(
            f"/api/stock/articles/{pid}/price",
            json={"revision": catalogue["articles"][0]["revision"], "sale_net": "6"},
        ).status_code
        == 200
    )
    data = {
        "article_id": pid,
        "warehouse_id": warehouses[0],
        "kind": "sale",
        "quantity": "11",
        "reason": "Teste insuficiente",
        "request_key": str(uuid4()),
    }
    assert c.post("/api/stock/movements", json=data).status_code == 409
    data["warehouse_id"] = warehouses[2]
    assert c.post("/api/stock/movements", json=data).status_code == 403
    login(c, "admin")
    data["warehouse_id"] = warehouses[0]
    assert c.post("/api/stock/movements", json=data).status_code == 200
    assert D(view(stock_case, pid)["quantity"]) == -1
    login(c, "reader")
    assert (
        c.post("/api/stock/movements", json={**data, "request_key": str(uuid4())}).status_code
        == 403
    )


def test_csv_preview_errors_confirmation_idempotency_and_barcode_duplicates(stock_case):
    c = stock_case[0]
    payload = {
        "content": "Codigo;Nome;Preco;Barras\nA;Primeiro;5,20;123\nB;Segundo;3,10;456\n",
        "mapping": {"code": "Codigo", "name": "Nome", "sale_net": "Preco", "barcodes": "Barras"},
    }
    response = c.post("/api/stock/csv/preview", json=payload)
    assert response.status_code == 200, response.text
    row = response.json()
    assert len(row["rows"]) == 2 and row["can_confirm"]
    assert c.get("/api/stock/catalog").json()["articles"] == []
    for _ in range(2):
        assert (
            c.post(f"/api/stock/csv/{row['id']}/confirm", json={"confirmed": True}).status_code
            == 200
        )
    assert len(c.get("/api/stock/catalog").json()["articles"]) == 2
    repeated = c.post("/api/stock/csv/preview", json=payload).json()
    assert len(repeated["errors"]) == 2 and not repeated["can_confirm"]
    duplicate = c.post(
        "/api/stock/articles", json={"code": "C", "name": "Outro", "barcodes": ["123"]}
    )
    assert duplicate.status_code == 409
    assert c.post(
        "/api/stock/csv/preview",
        json={
            **payload,
            "content": "Codigo;Nome;Preco;Barras\nX;Valido;1;123123\nX;Repetido;2;999\n",
        },
    ).json()["errors"]


def test_saft_explicit_policy_deduplication_and_changed_document_conflict(stock_case):
    c, _, factory, warehouses, store = stock_case
    pid = product(stock_case)
    move(stock_case, pid, quantity="10", unit_cost="2")
    with factory.begin() as db:
        snapshot = WintouchSaleRecord(
            enterprise_id="saft:500000001",
            external_id="invoice:abc",
            store_id=store,
            kind="saft_invoice",
            document_date=date.today().isoformat(),
            data=json.dumps(
                {
                    "document": "FT TEST/1",
                    "type": "FT",
                    "status": "N",
                    "date": date.today().isoformat(),
                    "raw": {
                        "Line": {
                            "LineNumber": "1",
                            "ProductCode": "S1",
                            "Quantity": "2",
                            "CreditAmount": "10",
                        }
                    },
                }
            ),
        )
        db.add(snapshot)
        db.flush()
        sid = snapshot.id
    url = f"/api/stock/saft?start={date.today()}&end={date.today()}"
    assert c.get(url).json()["rows"][0]["state"] == "unmapped"
    body = {
        "warehouse_id": warehouses[0],
        "start": date.today().isoformat(),
        "end": date.today().isoformat(),
        "confirmed": True,
    }
    assert c.post("/api/stock/saft/apply", json=body).status_code == 422
    configure(stock_case, saft_mapping={"S1": pid}, control_start=date.today().isoformat())
    for _ in range(2):
        assert c.post("/api/stock/saft/apply", json=body).status_code == 200
    assert D(view(stock_case, pid)["quantity"]) == 8
    assert c.get(url).json()["rows"][0]["state"] == "posted"
    # A manual replay of an integrated document must not duplicate stock exits.
    response = c.post(
        "/api/stock/movements",
        json={
            "article_id": pid,
            "warehouse_id": warehouses[0],
            "kind": "sale",
            "quantity": "2",
            "document": "FT TEST/1",
            "reason": "Duplicado",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 409
    with factory.begin() as db:
        row = db.get(WintouchSaleRecord, sid)
        content = json.loads(row.data)
        content["raw"]["Line"]["Quantity"] = "3"
        row.data = json.dumps(content)
    assert c.get(url).json()["rows"][0]["state"] == "conflict"
    assert c.post("/api/stock/saft/apply", json=body).status_code == 409
    assert D(view(stock_case, pid)["quantity"]) == 8
    conflict = c.get(url).json()["rows"][0]
    resolved = c.post(
        "/api/stock/saft/resolve",
        json={
            "confirmed": True,
            "record_id": sid,
            "key": conflict["key"],
            "fingerprint": conflict["fingerprint"],
            "reason": "Revisto: manter o registo, diferença tratada separadamente",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert c.get(url).json()["rows"][0]["state"] == "reviewed"
    assert c.post("/api/stock/saft/apply", json=body).status_code == 200
    assert D(view(stock_case, pid)["quantity"]) == 8
    with factory.begin() as db:
        row = db.get(WintouchSaleRecord, sid)
        content = json.loads(row.data)
        content["status"] = "A"
        row.data = json.dumps(content)
    assert c.get(url).json()["rows"][0]["state"] == "conflict"


def test_concurrent_exits_cannot_create_negative_stock(stock_case):
    from concurrent.futures import ThreadPoolExecutor

    c = stock_case[0]
    pid = product(stock_case)
    move(stock_case, pid, quantity="10", unit_cost="2")

    def sale(_):
        return c.post(
            "/api/stock/movements",
            json={
                "article_id": pid,
                "warehouse_id": stock_case[3][0],
                "kind": "sale",
                "quantity": "6",
                "reason": "Venda simultânea",
                "request_key": str(uuid4()),
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(sale, range(2)))
    assert sorted(results) == [200, 409]
    assert D(view(stock_case, pid)["quantity"]) == 4


def test_lot_inventory_adjustment_is_not_a_fefo_sale(stock_case):
    c = stock_case[0]
    pid = product(stock_case, lots=True, expiry=True)
    move(
        stock_case,
        pid,
        quantity="5",
        unit_cost="2",
        lot_number="FIRST",
        expiry=(date.today() + timedelta(days=3)).isoformat(),
    )
    move(
        stock_case,
        pid,
        quantity="5",
        unit_cost="4",
        lot_number="LAST",
        expiry=(date.today() + timedelta(days=50)).isoformat(),
    )
    inv = c.post(
        "/api/stock/inventories", json={"warehouse_id": stock_case[3][0], "article_ids": [pid]}
    ).json()
    last = next(
        lot["id"] for lot in c.get("/api/stock/lots").json()["rows"] if lot["number"] == "LAST"
    )
    lines = [
        {
            "article_id": pid,
            "lot_id": line["lot_id"],
            "counted": "3" if line["lot_id"] == last else "5",
            "reason": "Contagem física do lote",
        }
        for line in inv["lines"]
    ]
    response = c.post(
        f"/api/stock/inventories/{inv['id']}",
        json={"revision": inv["revision"], "confirm": True, "lines": lines},
    )
    assert response.status_code == 200, response.text
    lots = c.get("/api/stock/lots").json()["rows"]
    assert D(next(lot["quantity"] for lot in lots if lot["number"] == "LAST")) == 3
    assert D(next(lot["quantity"] for lot in lots if lot["number"] == "FIRST")) == 5


def test_erp_articles_readonly_and_stock_unavailable(stock_case):
    c, _, factory, warehouses, _ = stock_case
    with factory.begin() as db:
        row = WintouchProduct(
            enterprise_id="ERP",
            external_id="P1",
            code="ERP1",
            name="ERP produto",
            sale_price=D(5),
            last_cost=D(2),
        )
        db.add(row)
        db.flush()
        erp_id = row.id
    result = c.post("/api/stock/erp-products", json={"erp_id": erp_id})
    assert result.status_code == 200, result.text
    pid = result.json()["id"]
    detail = view(stock_case, pid)
    assert detail["quantity"] is None and detail["sale_net"] is None
    assert "indisponível" in detail["stock_state"]
    assert (
        c.post(
            "/api/stock/movements",
            json={
                "article_id": pid,
                "warehouse_id": warehouses[0],
                "kind": "purchase",
                "quantity": "1",
                "reason": "Tentativa",
                "request_key": str(uuid4()),
            },
        ).status_code
        == 409
    )
    assert report(stock_case)["coverage"]["erp_stock_unavailable"] == 1


def test_movement_idempotency_atomicity_and_inventory_migration(stock_case, monkeypatch):
    c, _, factory, warehouses, _ = stock_case
    pid = product(stock_case)
    request = {
        "article_id": pid,
        "warehouse_id": warehouses[0],
        "kind": "opening",
        "quantity": "3",
        "unit_cost": "1",
        "reason": "Teste repetido",
        "request_key": str(uuid4()),
    }
    for _ in range(2):
        assert c.post("/api/stock/movements", json=request).status_code == 200
    assert D(view(stock_case, pid)["quantity"]) == 3
    assert c.post("/api/stock/movements", json={**request, "quantity": "4"}).status_code == 409
    invalid = {
        **request,
        "request_key": str(uuid4()),
        "kind": "transfer",
        "target_id": "another-tenant",
    }
    assert c.post("/api/stock/movements", json=invalid).status_code == 404
    assert D(view(stock_case, pid)["quantity"]) == 3
    migration = importlib.import_module("migrations.versions.0044_inventory")
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    with factory.kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        before = connection.scalar(text("SELECT count(*) FROM users"))
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert {"stock_articles", "stock_lots", "stock_entries", "stock_inventories"} <= set(
            inspect(connection).get_table_names(schema=schema)
        )
        assert connection.scalar(text("SELECT count(*) FROM users")) == before
