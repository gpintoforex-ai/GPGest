import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import WebshopConnection, WebshopOrder
from sqlalchemy import inspect, text


@pytest.mark.fresh_schema
def test_migration_creates_tables_in_selected_schema(setup, monkeypatch):
    _, _, factory = setup
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert schema.startswith("test_")
        WebshopOrder.__table__.drop(connection)
        WebshopConnection.__table__.drop(connection)
        monkeypatch.setattr(importlib.import_module("migrations.versions.0006_cash_sheet"), "schemas", lambda _: [schema])
        module = importlib.import_module("migrations.versions.0074_webshop")
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()  # Repetir é seguro após uma instalação parcial.
        inspector = inspect(connection)
        assert inspector.has_table("webshop_connections", schema=schema)
        assert inspector.has_table("webshop_orders", schema=schema)
        assert next(c for c in inspector.get_columns("webshop_orders", schema=schema) if c["name"] == "sequence")["type"].__class__.__name__ == "BIGINT"
