import importlib
import re

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import SaftImport, SaftImportItem
from saft_fixtures import saft
from sqlalchemy import inspect, text
from test_platform import create, enter
from test_platform import platform as platform


def test_saft_company_isolation_and_nif_validation(platform):
    client, _ = platform
    a, b = create(client, "saft-tenant-a"), create(client, "saft-tenant-b")
    client.cookies.clear()
    boot = enter(client, a["slug"])
    nif = str(500000000 + sum((i + 1) * ord(ch) for i, ch in enumerate(a["slug"])))
    raw = saft(nif=nif)
    response = client.post(
        "/api/saft/imports",
        data={"store_id": boot["stores"][0]["id"]},
        files={"file": ("empresa-a.xml", raw)},
    )
    assert response.status_code == 200, response.text
    identifier = response.json()["id"]
    row = client.get(f"/api/saft/imports/{identifier}").json()
    assert row["status"] == "ready", row
    item = client.get(f"/api/saft/imports/{identifier}/items").json()["items"][0]["id"]
    confirmed = client.post(
        f"/api/saft/imports/{identifier}/confirm",
        json={"confirmed": True, "revision": row["revision"], "conflicts": {}},
    )
    assert confirmed.status_code == 200
    assert client.get(f"/api/saft/imports/{identifier}").json()["status"] == "completed"
    assert (
        client.get("/api/saft/analysis?start=2026-01-01&end=2026-01-31").json()[
            "billing_total_cents"
        ]
        == 1230
    )
    boot_b = enter(client, b["slug"])
    assert client.get("/api/saft/imports").json()["imports"] == []
    assert (
        client.get("/api/saft/analysis?start=2026-01-01&end=2026-01-31").json()[
            "billing_total_cents"
        ]
        == 0
    )
    assert client.get(f"/api/saft/imports/{identifier}").status_code == 404
    assert client.get(f"/api/saft/imports/{identifier}/items/{item}").status_code == 404
    assert (
        client.post(
            f"/api/saft/imports/{identifier}/confirm",
            json={"confirmed": True, "revision": row["revision"], "conflicts": {}},
        ).status_code
        == 404
    )
    rejected = client.post(
        "/api/saft/imports",
        data={"store_id": boot_b["stores"][0]["id"]},
        files={"file": ("outra-empresa.xml", raw)},
    )
    result = client.get("/api/saft/imports/" + rejected.json()["id"]).json()
    assert result["status"] == "failed" and "NIF" in result["error"]


def test_saft_migration_preserves_existing_data_and_is_repeatable(setup, monkeypatch):
    _, _, factory = setup
    migration = importlib.import_module("migrations.versions.0043_saft_sales")
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    with factory.kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_[0-9a-f]{32}", schema)
        before = connection.scalar(text("SELECT count(*) FROM users"))
        SaftImportItem.__table__.drop(connection)
        SaftImport.__table__.drop(connection)
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert {"saft_imports", "saft_import_items"} <= set(
            inspect(connection).get_table_names(schema=schema)
        )
        assert connection.scalar(text(f"SELECT count(*) FROM {schema}.users")) == before
