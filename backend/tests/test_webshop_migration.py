import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import (
    WebshopConnection,
    WebshopOrder,
    WebshopOrderEvent,
    WebshopPaymentEvent,
    WebshopPublication,
    WebshopPublicationEvent,
    WebshopRemoteProduct,
)
from sqlalchemy import inspect, text


@pytest.mark.fresh_schema
def test_migration_creates_tables_in_selected_schema(setup, monkeypatch):
    _, _, factory = setup
    engine = factory.kw["bind"]
    with engine.begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert schema.startswith("test_")
        for model in (WebshopPublicationEvent, WebshopPublication, WebshopRemoteProduct, WebshopPaymentEvent,
                      WebshopOrderEvent, WebshopOrder):
            model.__table__.drop(connection)
        WebshopConnection.__table__.drop(connection)
        monkeypatch.setattr(importlib.import_module("migrations.versions.0006_cash_sheet"), "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            for name in ("0074_webshop", "0095_webshop_publications", "0096_webshop_payments"):
                module = importlib.import_module("migrations.versions." + name)
                module.upgrade()
                module.upgrade()  # Repetir é seguro após uma instalação parcial.
        inspector = inspect(connection)
        assert inspector.has_table("webshop_connections", schema=schema)
        assert inspector.has_table("webshop_orders", schema=schema)
        for table in ("webshop_publications", "webshop_publication_events", "webshop_remote_products"):
            assert inspector.has_table(table, schema=schema)
        indexes = {i["name"] for i in inspector.get_indexes("webshop_publications", schema=schema)}
        assert "uq_webshop_publications_woo" in indexes
        columns = {c["name"] for c in inspector.get_columns("webshop_orders", schema=schema)}
        assert {"payment_state", "reservation_state", "reservation_expires_at", "total_cents"} <= columns
        assert inspector.has_table("webshop_payment_events", schema=schema)
        assert "order_settings" in {c["name"] for c in inspector.get_columns("webshop_connections", schema=schema)}
        assert next(c for c in inspector.get_columns("webshop_orders", schema=schema) if c["name"] == "sequence")["type"].__class__.__name__ == "BIGINT"
