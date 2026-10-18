import importlib
import re
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import CompanyEmailSettings, EmailDispatch, EmailDispatchPart
from sqlalchemy import inspect, text
from test_platform import create, enter
from test_platform import platform as platform


def test_email_configuration_history_and_payload_are_tenant_scoped(platform):
    client, _ = platform
    a, b = create(client, "email-tenant-a"), create(client, "email-tenant-b")
    client.cookies.clear()
    boot = enter(client, a["slug"])
    assert (
        client.post("/api/settings/email", json={"accountant_email": "a@example.test"}).status_code
        == 200
    )
    row = client.post(
        "/api/accounting/email/prepare",
        json={
            "request_key": str(uuid4()),
            "start": "2026-01-01",
            "end": "2026-01-31",
            "store_ids": [boot["stores"][0]["id"]],
            "categories": ["pendencias"],
        },
    ).json()
    assert "id" in row, row
    enter(client, b["slug"])
    assert client.get("/api/settings/email").json()["accountant_email"] == ""
    assert client.get("/api/emails").json() == []
    assert client.get(f"/api/emails/{row['id']}").status_code == 404
    key = row["manifest"]["files"][0]["key"]
    assert client.get(f"/api/emails/{row['id']}/files/{key}").status_code == 404
    assert (
        client.post(
            f"/api/emails/{row['id']}/send",
            json={"confirmed": True, "revision": row["revision"], "request_key": str(uuid4())},
        ).status_code
        == 404
    )
    enter(client, a["slug"])
    assert client.get("/api/settings/email").json()["accountant_email"] == "a@example.test"
    assert client.get(f"/api/emails/{row['id']}/files/{key}").status_code == 200


def test_email_migration_is_additive_and_repeatable(setup, monkeypatch):
    _, _, factory = setup
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    migration = importlib.import_module("migrations.versions.0042_accounting_email")
    with factory.kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_[0-9a-f]{32}", schema)
        before = connection.scalar(text("SELECT count(*) FROM users"))
        for model in (EmailDispatchPart, EmailDispatch, CompanyEmailSettings):
            model.__table__.drop(connection)
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        inspector = inspect(connection)
        assert {"company_email_settings", "email_dispatches", "email_dispatch_parts"} <= set(
            inspector.get_table_names(schema=schema)
        )
        assert "message_digest" in {
            col["name"] for col in inspector.get_columns("email_dispatch_parts", schema=schema)
        }
        assert connection.scalar(text(f"SELECT count(*) FROM {schema}.users")) == before
