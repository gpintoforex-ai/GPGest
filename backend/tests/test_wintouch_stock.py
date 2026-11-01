"""Saídas de stock das vendas Wintouch importadas: só API simulada e esquema temporário."""

import json
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

import pytest
from app.access import authorize
from app.db import db_session
from app.main import app
from app.models import (
    Audit,
    Register,
    StockArticle,
    StockEntry,
    User,
    WintouchProduct,
    WintouchSaleRecord,
)
from app.wintouch_sales import Reader, synchronize_sales
from app.wintouch_stock import apply_sales_stock
from sqlalchemy import select
from test_sales import fixtures, reader

START, END = date(2026, 10, 1), date(2026, 10, 31)


def line(dimension, product, quantity="2", gross="10.00", row=0, source=None, batch=None):
    return {
        "Row": row, "RowType": 0, "ProductID": product, "WharehouseID": "wt-armazem", "StockUnitFactor": 1,
        "ProductDocumentDetailsDimensions": [{
            "ID": dimension, "Quantity": quantity, "StockQuantity": quantity, "GrossValueWithoutVAT": gross,
            "RowDiscountValueWithoutVAT": 0, "HeaderDiscountValueWithoutVAT": 0, "CanceledQuantity": 0,
            "ProductBatchID": batch,
            "Sources": [{"SourceProductDocumentDetailsDimensionID": source}] if source else [],
        }],
    }


def text_line():
    return {"Row": 9, "RowType": 3, "ProductID": None, "ProductDocumentDetailsDimensions": [{"ID": "txt", "Quantity": 0}]}


@pytest.fixture
def case(setup, monkeypatch):
    client, ids, factory = setup
    monkeypatch.setattr("app.inventory.policy", lambda _: {"mode": "local", "provider": "none"})
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    warehouse = client.post("/api/stock/warehouses", json={"store_id": store, "name": "Principal"}).json()["id"]
    articles = {}
    for external in ("prod-a", "prod-lotes"):
        response = client.post("/api/stock/articles", json={"code": "P-" + uuid4().hex[:6], "name": "Artigo " + external, "sale_net": "5", **({"lots": True} if external == "prod-lotes" else {})})
        assert response.status_code == 200, response.text
        articles[external] = response.json()["id"]
    with factory.begin() as db:
        for external, article_id in articles.items():
            product = WintouchProduct(enterprise_id="tenant", external_id=external, code=external, name=external)
            db.add(product)
            db.flush()
            db.get(StockArticle, article_id).erp_id = product.id
    purchase = client.post("/api/stock/movements", json={
        "article_id": articles["prod-a"], "warehouse_id": warehouse, "kind": "purchase", "quantity": "10",
        "unit_cost": "3", "reason": "Stock inicial de teste", "request_key": str(uuid4()), "occurred_on": "2026-09-30",
    })
    assert purchase.status_code == 200, purchase.text
    settings = client.get("/api/stock/catalog").json()["settings"]
    assert client.post("/api/stock/settings", json={**settings, "control_start": "2026-10-01"}).status_code == 200

    data = fixtures()
    data["document_types"] += [{"ID": "nc", "Code": "NC", "Class": 6}]
    sale = data["product_documents"][0]
    sale["Version"] = "v1"
    data["product_documents/sale/withsources"] = [{"ID": "sale", "ProductDocumentDetails": [
        line("dim-a", "prod-a"), line("dim-x", "prod-sem-ficha", row=1), line("dim-l", "prod-lotes", row=2), text_line(),
    ]}]
    data["product_documents/credit-sale/withsources"] = [{"ID": "credit-sale", "ProductDocumentDetails": []}]
    calls = []
    original_get = Reader.get

    def counting_get(self, resource, params=None):
        calls.append(resource)
        return original_get(self, resource, params)

    monkeypatch.setattr(Reader, "get", counting_get)
    monkeypatch.setattr("app.wintouch_sales.Reader", lambda *args: reader(data))
    return client, factory, store, warehouse, articles, data, calls


def run(case, lines_from=date(2026, 10, 1), warehouse=None):
    client, factory, store, *_ = case
    result = synchronize_sales(factory.kw["bind"], "tenant", store, START, END, "https://api.wintouchcloud.com/api/v1", "key", lines_from)
    assert result["status"] == "concluida", result
    with factory() as db:
        user = db.scalar(select(User).where(User.email == "admin@test.pt"))
        stock = apply_sales_stock(db, user, authorize, "tenant", store, START, END, warehouse)
        db.commit()
    return stock


def entries(factory, article_id):
    with factory() as db:
        return list(db.scalars(select(StockEntry).where(StockEntry.article_id == article_id).order_by(StockEntry.created_at)))


def test_sale_lines_create_stock_exits_once_and_list_exclusions(case):
    client, factory, store, warehouse, articles, data, calls = case
    stock = run(case)
    assert stock["status"] == "aplicado" and stock["applied"] == 1
    reasons = {p["reason"] for p in stock["pending"]}
    assert "Artigo do ERP sem ficha local associada." in reasons
    assert "Artigo com lotes: registe a saída manualmente, com o lote." in reasons
    rows = entries(factory, articles["prod-a"])
    sale = next(r for r in rows if r.kind == "sale")
    assert sale.quantity == D(-2) and sale.net_sales == D(10) and sale.unit_cost == D(3)
    assert sale.warehouse_id == warehouse and sale.occurred_on == "2026-10-01"
    assert json.loads(sale.data)["origin"] == "Wintouch" and json.loads(sale.data)["document"] == "FR 2026 1"
    # Repetir não volta a baixar o stock nem a ler linhas de um documento com a mesma versão.
    reads = calls.count("product_documents/sale/withsources")
    again = run(case)
    assert again["applied"] == 0 and len([r for r in entries(factory, articles["prod-a"]) if r.kind == "sale"]) == 1
    assert calls.count("product_documents/sale/withsources") == reads
    # As linhas ficam só no documento, não nos pagamentos nem nos pendentes.
    with factory() as db:
        for row in db.scalars(select(WintouchSaleRecord).where(WintouchSaleRecord.kind != "documento")):
            assert "lines" not in json.loads(row.data)
    # Uma venda manual do mesmo documento e artigo é recusada depois de integrada.
    manual = client.post("/api/stock/movements", json={
        "article_id": articles["prod-a"], "warehouse_id": warehouse, "kind": "sale", "quantity": "1",
        "document": "FR 2026 1", "reason": "Duplicado manual", "request_key": str(uuid4()), "occurred_on": "2026-10-01",
    })
    assert manual.status_code == 409


def test_credit_note_returns_at_original_cost_and_cancellation_reverses_the_rest(case):
    client, factory, store, warehouse, articles, data, calls = case
    run(case)
    credit = {**data["product_documents"][0], "ID": "nc-1", "DocumentTypeID": "nc", "DocumentNumber": 7, "Version": "n1",
              "Date": "2026-10-02T10:00:00", "Payments": []}
    data["product_documents"].append(credit)
    data["product_documents/nc-1/withsources"] = [{"ID": "nc-1", "ProductDocumentDetails": [line("dim-nc", "prod-a", quantity="1", gross="5.00", source="dim-a")]}]
    stock = run(case)
    assert stock["returns"] == 1, stock
    back = next(r for r in entries(factory, articles["prod-a"]) if r.kind == "customer_return")
    assert back.quantity == D(1) and back.unit_cost == D(3) and back.net_sales == D(-5)
    # Anulada a venda na Wintouch: devolve-se só o que falta (estorno), sem apagar movimentos.
    data["product_documents"][0]["DeletedDate"] = "2026-10-03T09:00:00"
    data["product_documents"][0]["Version"] = "v2"
    stock = run(case)
    assert stock["reversed"] == 1, stock
    rows = entries(factory, articles["prod-a"])
    assert sum(r.quantity for r in rows) == D(10)
    assert len([r for r in rows if r.kind == "sale"]) == 1
    assert run(case)["reversed"] == 0


def test_changed_line_after_integration_is_a_conflict_without_new_movements(case):
    client, factory, store, warehouse, articles, data, calls = case
    run(case)
    data["product_documents"][0]["Version"] = "v2"
    data["product_documents/sale/withsources"][0]["ProductDocumentDetails"][0] = line("dim-a", "prod-a", quantity="3", gross="15.00")
    stock = run(case)
    assert stock["conflict_count"] == 1 and "alterada" in stock["conflicts"][0]["reason"]
    assert len(entries(factory, articles["prod-a"])) == 2


def test_insufficient_stock_is_listed_and_does_not_block_other_lines(case):
    client, factory, store, warehouse, articles, data, calls = case
    lines = data["product_documents/sale/withsources"][0]["ProductDocumentDetails"]
    lines[0] = line("dim-a", "prod-a", quantity="50", gross="250.00")
    stock = run(case)
    assert stock["applied"] == 0
    assert any("insuficiente" in p["reason"] for p in stock["pending"])
    assert [r.kind for r in entries(factory, articles["prod-a"])] == ["purchase"]


def test_without_control_start_no_lines_are_read_and_stock_is_unchanged(case):
    client, factory, store, warehouse, articles, data, calls = case
    settings = client.get("/api/stock/catalog").json()["settings"]
    assert client.post("/api/stock/settings", json={**settings, "control_start": None}).status_code == 200
    stock = run(case, lines_from=None)
    assert stock["status"] == "inativo" and "início do controlo" in stock["message"]
    assert not any(c.endswith("/withsources") for c in calls)
    assert [r.kind for r in entries(factory, articles["prod-a"])] == ["purchase"]


def test_documents_before_control_start_are_ignored(case):
    client, factory, store, warehouse, articles, data, calls = case
    stock = run(case, lines_from=date(2026, 10, 2))
    assert stock["applied"] == 0
    assert not any(c.endswith("/withsources") for c in calls)


def test_warehouse_must_be_chosen_when_store_has_several(case):
    client, factory, store, warehouse, articles, data, calls = case
    second = client.post("/api/stock/warehouses", json={"store_id": store, "name": "Reserva"}).json()["id"]
    stock = run(case)
    assert stock["applied"] == 0 and any("Armazém por definir" in p["reason"] for p in stock["pending"])
    stock = run(case, warehouse=warehouse)
    assert stock["applied"] == 1
    with factory() as db:
        assert db.scalar(select(StockEntry).where(StockEntry.kind == "sale")).warehouse_id == warehouse
        assert second


@pytest.fixture
def endpoint(case, monkeypatch):
    """Ligação Wintouch simulada da empresa, como a configurada na plataforma."""
    client, factory, *_ = case
    from types import SimpleNamespace

    company = SimpleNamespace(active=True, api_active=True, erp_provider="wintouch", mode="demonstracao",
                              api_url="https://api.wintouchcloud.com/api/v1", api_key_ciphertext="x", api_enterprise="tenant")

    class Core:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args):
            return company

    monkeypatch.setattr("app.main.database.next_platform_session", lambda: Core())
    monkeypatch.setattr("app.platform.cipher", lambda: SimpleNamespace(decrypt=lambda value: b"key"))
    monkeypatch.setattr("app.platform.validate_url", lambda value: value)

    def session_with_company():
        # Como no servidor (company_db): a sessão conhece a empresa, alvo da auditoria.
        with factory() as db:
            db.info["company"] = {"id": "empresa-teste"}
            yield db

    monkeypatch.setitem(app.dependency_overrides, db_session, session_with_company)
    return case


def test_endpoint_reports_stock_and_records_audit(endpoint):
    client, factory, store, warehouse, articles, data, calls = endpoint
    response = client.post("/api/integrations/wintouch/sales", json={
        "store_id": store, "start_date": "2026-10-01", "end_date": "2026-10-31", "confirm_demo": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "concluida" and body["stock"]["applied"] == 1
    with factory() as db:
        audit = db.scalar(select(Audit).where(Audit.action == "vendas_wintouch_stock"))
        assert json.loads(audit.detail)["applied"] == 1


def test_background_sync_reports_progress_and_result(endpoint):
    import time

    client, factory, store, warehouse, articles, data, calls = endpoint
    body = {"store_id": store, "start_date": "2026-10-01", "end_date": "2026-10-31", "confirm_demo": True}
    started = client.post("/api/integrations/wintouch/sales/start", json=body)
    assert started.status_code == 200, started.text
    run = started.json()
    stages = set()
    deadline = time.monotonic() + 60
    while run["status"] == "em_curso" and time.monotonic() < deadline:
        time.sleep(0.2)
        run = client.get(f"/api/integrations/wintouch/sales/runs/{run['id']}").json()
        stages.add(run["stage"])
        assert run["result"] is None or run["status"] != "em_curso"
    assert run["status"] == "concluida", run
    assert run["result"]["stock"]["applied"] == 1 and run["done"] >= 1
    assert client.get("/api/integrations/wintouch/sales/running").json() is None
    with factory() as db:
        assert len([e for e in db.scalars(select(StockEntry).where(StockEntry.kind == "sale"))]) == 1


def test_background_sync_refuses_a_second_run_and_reader_cannot_follow(endpoint):
    from app.models import WintouchSyncRun

    client, factory, store, *_ = endpoint
    with factory.begin() as db:
        db.add(WintouchSyncRun(enterprise_id="tenant", resource="vendas:progresso", status="em_curso", results="null"))
    body = {"store_id": store, "start_date": "2026-10-01", "end_date": "2026-10-31", "confirm_demo": True}
    response = client.post("/api/integrations/wintouch/sales/start", json=body)
    assert response.status_code == 409 and "em curso" in response.json()["detail"]
    running = client.get("/api/integrations/wintouch/sales/running").json()
    assert running["status"] == "em_curso"
    client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"})
    assert client.get(f"/api/integrations/wintouch/sales/runs/{running['id']}").status_code == 403
