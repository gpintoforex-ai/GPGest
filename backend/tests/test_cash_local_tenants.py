import importlib
import json
import re

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import db as database
from app.models import LocalPaymentMethod, WintouchSaleRecord
from app.platform_models import Company
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from test_cash_local import declared
from test_platform import create, enter
from test_platform import platform as platform


def test_real_local_company_methods_and_declarations_are_tenant_scoped(platform):
    client, factory = platform
    a, b = create(client, "caixa-local-a"), create(client, "caixa-local-b")
    client.cookies.clear()
    boot = enter(client, a["slug"])
    method = client.post("/api/cash/payment-methods", json={"name": "Vale oferta"})
    assert method.status_code == 200
    key = method.json()["id"]
    register = boot["registers"][0]["id"]
    opened = client.post(
        "/api/sessions", json={"register_id": register, "opening": "100", "sheet_day": "2026-10-02"}
    )
    identifier = opened.json()["id"]
    with factory.begin() as core:
        company = core.get(Company, a["id"])
        company.api_enterprise = "previous-erp"
        schema = company.schema_name
    source = json.dumps(
        {
            "payment": "Numerário",
            "payment_code": "NU",
            "amount_cents": 30000,
            "direction": 1,
            "occurred_at": "2026-10-02T12:00:00+01:00",
            "source_kind": "sale",
        }
    )
    with Session(database.engine.execution_options(schema_translate_map={None: schema})) as db:
        record = WintouchSaleRecord(
            enterprise_id="previous-erp",
            external_id="receipt-test",
            store_id=boot["stores"][0]["id"],
            kind="real",
            active=True,
            document_date="2026-10-02",
            data=source,
        )
        db.add(record)
        db.commit()
        record_id = record.id
    payload = {**declared(numerario="300"), "cash_total": "400"}
    payload["realized"]["values"][key] = "0"
    response = client.post(f"/api/sessions/{identifier}/reconcile", json=payload)
    assert response.status_code == 200, response.text
    numerario = next(
        r for r in response.json()["sheet"]["reconciliation"]["rows"] if r["key"] == "numerario"
    )
    assert numerario["sources"] == ["ERP"] and numerario["declared_source"] == "Manual"
    with Session(database.engine.execution_options(schema_translate_map={None: schema})) as db:
        assert db.get(WintouchSaleRecord, record_id).data == source
    enter(client, b["slug"])
    assert key not in client.get("/api/bootstrap").json()["payment_methods"]
    assert client.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 404


def test_cash_method_migration_creates_table_without_changing_existing_sheets(setup, monkeypatch):
    _, _, factory = setup
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    migration = importlib.import_module("migrations.versions.0035_local_cash_methods")
    with factory.kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_[0-9a-f]{32}", schema)
        before = connection.scalar(text("SELECT count(*) FROM users"))
        LocalPaymentMethod.__table__.drop(connection)
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert "local_payment_methods" in inspect(connection).get_table_names(schema=schema)
        assert connection.scalar(text(f"SELECT count(*) FROM {schema}.users")) == before
