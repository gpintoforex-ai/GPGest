import importlib.util
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db import engine
from app.models import (
    Base,
    WintouchCashAccount,
    WintouchEntity,
    WintouchMovement,
    WintouchProduct,
    WintouchSyncRun,
)
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError


def test_external_identity_precision_and_financial_separation(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        db.add(
            WintouchProduct(
                enterprise_id="api-a",
                external_id="product-1",
                code="P1",
                name="Artigo",
                sale_price=Decimal("12.3456"),
            )
        )
        entity = WintouchEntity(
            enterprise_id="api-a",
            external_id="entity-1",
            name="Cliente e fornecedor",
            is_customer=True,
            is_supplier=True,
        )
        db.add(entity)
        db.flush()
        account = WintouchCashAccount(
            enterprise_id="api-a",
            external_id="cash-1",
            name="Caixa Wintouch",
            register_id=ids["registers"][0],
        )
        db.add(account)
        db.flush()
        db.add(
            WintouchMovement(
                enterprise_id="api-a",
                external_id="receipt-1",
                resource="pos/receipts",
                amount_cents=12345,
                direction=1,
                cash_account_id=account.id,
                entity_id=entity.id,
            )
        )
    with factory() as db:
        assert db.scalar(select(WintouchProduct.sale_price)) == Decimal("12.3456")
        assert db.scalar(select(WintouchEntity)).is_supplier
    assert client.get("/api/movements").json() == []
    assert client.get("/api/sessions").json() == []
    with pytest.raises(IntegrityError), factory.begin() as db:
        db.add(
            WintouchProduct(
                enterprise_id="api-a", external_id="product-1", code="P2", name="Duplicado"
            )
        )
    with factory.begin() as db:
        db.add(
            WintouchProduct(
                enterprise_id="api-b", external_id="product-1", code="P1", name="Outra origem"
            )
        )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WintouchProduct)) == 2


def test_sync_lock_and_invalid_financial_values(setup):
    _, _, factory = setup
    with factory.begin() as db:
        db.add(WintouchSyncRun(enterprise_id="api-a", resource="products", status="em_curso"))
    with pytest.raises(IntegrityError), factory.begin() as db:
        db.add(WintouchSyncRun(enterprise_id="api-a", resource="products", status="em_curso"))
    with pytest.raises(IntegrityError), factory.begin() as db:
        db.add(
            WintouchMovement(
                enterprise_id="api-a",
                external_id="bad",
                resource="pos/receipts",
                amount_cents=-1,
                direction=1,
            )
        )


def test_migration_updates_existing_schemas_and_can_rollback(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "migrations/versions/0003_wintouch_tables.py"
    spec = importlib.util.spec_from_file_location("wintouch_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schemas = ["test_" + uuid4().hex for _ in range(2)]
    with engine.begin() as connection:
        for schema in schemas:
            connection.execute(text("CREATE SCHEMA " + schema))
            connection.execution_options(schema_translate_map={None: schema})
            Base.metadata.create_all(
                connection,
                tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith("wt_")],
            )
        connection.execution_options(schema_translate_map=None)
    monkeypatch.setattr(migration, "_schemas", lambda connection: schemas)
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            for schema in schemas:
                tables = inspect(connection).get_table_names(schema=schema)
                assert len([t for t in tables if t.startswith("wt_")]) == 11
                assert "movements" in tables
                connection.execute(
                    text(
                        'INSERT INTO "'
                        + schema
                        + '".wt_entities (id, enterprise_id, external_id, name, active, synced_at) VALUES (:id, :enterprise, :external, :name, true, CURRENT_TIMESTAMP)'
                    ),
                    dict(id=str(uuid4()), enterprise="api-a", external="same-id", name="Teste"),
                )
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            for schema in schemas:
                assert "wt_products" not in inspect(connection).get_table_names(schema=schema)
                assert "movements" in inspect(connection).get_table_names(schema=schema)
    finally:
        with engine.begin() as connection:
            for schema in schemas:
                connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
