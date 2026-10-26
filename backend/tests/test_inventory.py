import importlib
import json
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import (
    Audit,
    Register,
    StockEntry,
    StockInventory,
    StockWarehouse,
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


def test_warehouse_remove_requires_confirmation_and_records_audit(stock_case):
    c, _, factory, warehouses, store = stock_case
    url = f"/api/stock/warehouses/{warehouses[0]}/delete"
    assert c.post(url, json={}).status_code == 422
    assert c.post(url, json={"confirmed": False}).status_code == 422
    response = c.post(url, json={"confirmed": True})
    assert response.status_code == 200, response.text
    assert warehouses[0] not in [w["id"] for w in c.get("/api/stock/catalog").json()["warehouses"]]
    with factory() as db:
        assert db.get(StockWarehouse, warehouses[0]) is None
        audit = db.scalar(select(Audit).where(Audit.action == "stock_warehouse_deleted"))
        assert audit is not None
        assert audit.store_id == store
    assert c.post(url, json={"confirmed": True}).status_code == 404


@pytest.mark.parametrize("linked", ["movements", "inventory"])
def test_warehouse_remove_preserves_history(stock_case, linked):
    c, _, factory, warehouses, _ = stock_case
    pid = product(stock_case)
    if linked == "movements":
        move(stock_case, pid)
        move(stock_case, pid, "sale", "10")  # Zero stock still has protected history.
    else:
        result = c.post(
            "/api/stock/inventories",
            json={"warehouse_id": warehouses[0], "article_ids": [pid]},
        )
        assert result.status_code == 200, result.text
    response = c.post(
        f"/api/stock/warehouses/{warehouses[0]}/delete", json={"confirmed": True}
    )
    assert response.status_code == 409, response.text
    with factory() as db:
        assert db.get(StockWarehouse, warehouses[0]) is not None
        model = StockEntry if linked == "movements" else StockInventory
        assert db.scalar(select(model.id).where(model.warehouse_id == warehouses[0]))


def test_warehouse_remove_checks_permission_and_store_scope(stock_case):
    c, ids, _, warehouses, _ = stock_case
    login(c, "operator")
    url = f"/api/stock/warehouses/{warehouses[0]}/delete"
    assert c.post(url, json={"confirmed": True}).status_code == 403
    login(c, "admin")
    configure(stock_case, permissions={ids["operator"]: {"adjust_stock": True}})
    login(c, "operator")
    assert c.post(
        f"/api/stock/warehouses/{warehouses[2]}/delete", json={"confirmed": True}
    ).status_code == 403
    assert c.post(url, json={"confirmed": True}).status_code == 200


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


def test_article_available_stock_deducts_reservations_expiry_and_respects_scope():
    from app.inventory import availability

    history = [
        SimpleNamespace(warehouse_id="allowed", lot_id=None, quantity=D(10)),
        SimpleNamespace(warehouse_id="allowed", lot_id="expired", quantity=D(5)),
        SimpleNamespace(warehouse_id="other", lot_id=None, quantity=D(100)),
        SimpleNamespace(warehouse_id="negative", lot_id=None, quantity=D(-2)),
    ]
    holds = [
        SimpleNamespace(warehouse_id="allowed", lot_id=None, quantity=D(3)),
        SimpleNamespace(warehouse_id="other", lot_id=None, quantity=D(20)),
    ]
    lots = {"expired": SimpleNamespace(expiry=(date.today() - timedelta(days=1)).isoformat())}
    result = availability(history, ["allowed", "negative"], lots, holds)
    assert result == {"reserved_quantity": "3", "available_quantity": "7"}
    assert availability(history, ["allowed", "negative"], lots, holds, set())["available_quantity"] == "0"
    assert availability([], ["allowed"], {}, []) == {"reserved_quantity": "0", "available_quantity": "0"}


def test_article_details_ledger_warehouses_cost_permissions_and_history(stock_case):
    c, _, _, warehouses, _ = stock_case
    pid = product(stock_case)
    move(stock_case, pid, quantity="10", unit_cost="2", document="FT TESTE/1")
    move(stock_case, pid, "transfer", "3", target_id=warehouses[1])
    details = c.get(f"/api/stock/articles/{pid}/details")
    assert details.status_code == 200, details.text
    body = details.json()
    quantities = {w["id"]: w["quantity"] for w in body["warehouses"]}
    assert D(quantities[warehouses[0]]) == 7
    assert D(quantities[warehouses[1]]) == 3
    assert body["last_purchase"]["document"] == "FT TESTE/1"
    assert D(body["last_purchase"]["unit_cost"]) == 2
    assert body["history"][0]["label"] == "Artigo criado"
    assert body["history"][0]["user_name"]
    assert D(body["available_quantity"]) == 10
    assert body["reserved_quantity"] == "0"
    assert "detail" not in body["history"][0]
    login(c, "operator")
    restricted = c.get(f"/api/stock/articles/{pid}/details").json()
    assert warehouses[2] not in [w["id"] for w in restricted["warehouses"]]
    assert restricted["last_purchase"]["unit_cost"] is None
    assert c.get("/api/stock/articles/nonexistent/details").status_code == 404


def test_article_validation_identifies_conflicting_fields(stock_case):
    c = stock_case[0]
    product(stock_case, code="DUPLICADO", barcodes=["001234"])
    for data, field, status in [
        ({"code": "duplicado", "name": "Outro"}, "code", 409),
        ({"code": "OUTRO", "name": "Outro", "barcodes": ["001234"]}, "barcodes", 409),
        ({"code": "MIN", "name": "Outro", "minimum": "5", "reorder": "2"}, "reorder", 422),
        ({"code": "LOT", "name": "Outro", "expiry": True}, "expiry", 422),
    ]:
        result = c.post("/api/stock/articles", json=data)
        assert result.status_code == status, result.text
        assert result.json()["detail"][0]["loc"] == ["body", field]


def test_article_notes_and_https_documents_round_trip(stock_case):
    c = stock_case[0]
    pid = product(stock_case, description="Descrição do artigo", internal_notes="Nota da equipa",
                  additional_photos=["https://example.com/photo.png"],
                  attachments=[{"name": "Ficha técnica", "url": "https://example.com/sheet.pdf"}])
    data = view(stock_case, pid)
    assert data["description"] == "Descrição do artigo"
    assert data["internal_notes"] == "Nota da equipa"
    assert data["attachments"][0]["name"] == "Ficha técnica"
    assert data["additional_photos"] == ["https://example.com/photo.png"]
    for field, value in [("photo", "https://"), ("additional_photos", ["http://example.com/photo.png"]),
                         ("attachments", [{"name": "Inválido", "url": "javascript:alert(1)"}])]:
        response = c.post("/api/stock/articles", json={"code": uuid4().hex, "name": "Inválido", field: value})
        assert response.status_code == 422


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


def erp_product(case, code, name="ERP produto", **extra):
    with case[2].begin() as db:
        row = WintouchProduct(
            enterprise_id="ERP",
            external_id="X" + uuid4().hex[:8],
            code=code,
            name=name,
            sale_price=D(5),
            last_cost=D(2),
            **extra,
        )
        db.add(row)
        db.flush()
        return row.id


def test_erp_articles_keep_erp_catalogue_and_local_stock(stock_case, monkeypatch):
    c, _, _, warehouses, _ = stock_case
    erp_id = erp_product(stock_case, "ERP1")
    result = c.post("/api/stock/erp-products", json={"erp_id": erp_id})
    assert result.status_code == 200, result.text
    pid = result.json()["id"]
    detail = view(stock_case, pid)
    # O conector não lê quantidades: o stock dos artigos do ERP é registado na IAZELA.
    assert detail["quantity"] == "0" and detail["sale_net"] is None and detail["origin"] == "ERP"
    assert detail["stock_state"] == "movimentos locais"
    movement = {
        "article_id": pid,
        "warehouse_id": warehouses[0],
        "kind": "purchase",
        "quantity": "3",
        "unit_cost": "2",
        "reason": "Receção",
        "request_key": str(uuid4()),
    }
    assert c.post("/api/stock/movements", json=movement).status_code == 200
    assert D(view(stock_case, pid)["quantity"]) == 3
    assert report(stock_case)["coverage"]["erp_stock_unavailable"] == 0

    # Ficha: só os dados de gestão local mudam; código, nome, estado e preço vêm do ERP.
    current = view(stock_case, pid)
    edit = {
        "code": "OUTRO", "name": "Outro nome", "active": False, "sale_net": "99",
        "category": "Chás", "minimum": "1", "reorder": "4", "location": "A1",
        "revision": current["revision"],
    }
    assert c.put(f"/api/stock/articles/{pid}", json=edit).status_code == 200, "edição local"
    after = view(stock_case, pid)
    assert after["code"] == "ERP1" and after["name"] == "ERP produto" and after["active"] is True
    assert after["category"] == "Chás" and after["location"] == "A1" and after["sale_net"] is None
    price = c.post(f"/api/stock/articles/{pid}/price", json={"revision": after["revision"], "sale_net": "7"})
    assert price.status_code == 409 and "vêm do ERP" in price.json()["detail"]

    # Se a empresa declarar o stock como do ERP, os movimentos locais voltam a ser recusados.
    monkeypatch.setattr("app.inventory.stock_from_erp", lambda db: True)
    blocked = c.post("/api/stock/movements", json={**movement, "request_key": str(uuid4())})
    assert blocked.status_code == 409
    assert view(stock_case, pid)["quantity"] is None


def test_link_erp_articles_by_code_with_preview(stock_case):
    c, _, factory, warehouses, _ = stock_case
    local = product(stock_case, code="A100", name="Nome local")
    c.post("/api/stock/movements", json={
        "article_id": local, "warehouse_id": warehouses[0], "kind": "opening", "quantity": "2",
        "unit_cost": "1", "reason": "Abertura", "request_key": str(uuid4()),
    })
    product(stock_case, code="SO-LOCAL")
    erp_product(stock_case, "a100", name="Nome ERP")
    erp_product(stock_case, "B200", name="Só no ERP")
    erp_product(stock_case, "DUP", name="Duplicado 1")
    erp_product(stock_case, "dup", name="Duplicado 2")
    erp_product(stock_case, "OFF", name="Inativo", active=False)

    preview = c.post("/api/stock/erp-products/link-by-code", json={})
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["link"] == 1 and plan["create"] == 1 and plan["ambiguous"] == 1
    assert plan["ambiguous_codes"] == ["DUP"] or plan["ambiguous_codes"] == ["dup"]
    assert view(stock_case, local)["origin"] == "Local"  # A pré-visualização não altera nada.

    done = c.post("/api/stock/erp-products/link-by-code", json={"confirmed": True, "create_missing": True})
    assert done.status_code == 200, done.text
    assert done.json()["linked"] == 1 and done.json()["created"] == 1
    linked = view(stock_case, local)
    # Mantém a ficha e o histórico; nome e código passam a vir do ERP.
    assert linked["origin"] == "ERP" and linked["name"] == "Nome ERP" and D(linked["quantity"]) == 2
    codes = {a["code"]: a for a in c.get("/api/stock/catalog").json()["articles"]}
    assert codes["B200"]["origin"] == "ERP" and "OFF" not in codes and codes["SO-LOCAL"]["origin"] == "Local"
    # Repetir não volta a ligar nem a criar.
    again = c.post("/api/stock/erp-products/link-by-code", json={"confirmed": True, "create_missing": True}).json()
    assert again["linked"] == 0 and again["created"] == 0


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


def test_service_articles_never_move_stock(stock_case):
    c, _, _, warehouses, _ = stock_case
    service = product(stock_case, code="PORTES", name="Portes de envio", service=True)
    assert view(stock_case, service)["stock_state"] == "serviço (sem stock)"
    moved = c.post("/api/stock/movements", json={
        "article_id": service, "warehouse_id": warehouses[0], "kind": "purchase", "quantity": "1",
        "unit_cost": "3.99", "reason": "Portes", "request_key": str(uuid4())})
    assert moved.status_code == 409 and "serviço" in moved.json()["detail"]
    lots = c.post("/api/stock/articles", json={"code": "SRV2", "name": "Serviço com lotes", "service": True, "lots": True})
    assert lots.status_code == 422
    # Um artigo com movimentos não passa a serviço.
    goods = product(stock_case, code="GOODS1")
    c.post("/api/stock/movements", json={"article_id": goods, "warehouse_id": warehouses[0], "kind": "opening", "quantity": "2",
                                         "unit_cost": "1", "reason": "Abertura", "request_key": str(uuid4())})
    current = view(stock_case, goods)
    changed = c.put(f"/api/stock/articles/{goods}", json={**{k: current[k] for k in ("code", "name", "revision")}, "service": True})
    assert changed.status_code == 409
