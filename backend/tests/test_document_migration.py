"""Run actual migration operations in disposable schemas only."""

import importlib
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import Base
from sqlalchemy import inspect, text


@pytest.mark.parametrize("path", ["existing", "fresh"])
def test_document_migration_preserves_existing_schema_and_supports_new_install(
    setup, monkeypatch, path
):
    _, _, factory = setup
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    new_names = {
        "document_states",
        "erp_documents",
        "document_erp_links",
        "expense_drafts",
        "document_payments",
        "payment_document_lines",
        "document_imports",
    }
    with factory.kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_[0-9a-f]{32}", schema)
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        before = connection.scalar(text("SELECT count(*) FROM users"))
        for table in ("purchases", "bank_transactions"):
            connection.execute(text(f"ALTER TABLE {schema}.{table} DROP COLUMN import_id"))
        connection.execute(text(f"ALTER TABLE {schema}.purchases DROP COLUMN base_cents"))
        targets = set(new_names)
        if path == "fresh":
            targets.update(
                {
                    "bank_transactions",
                    "purchases",
                    "purchase_attachments",
                    "purchase_allocations",
                    "efatura_configs",
                    "reconciliations",
                    "reconciliation_lines",
                    "reconciliation_ignores",
                }
            )
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in targets:
                connection.execute(text(f"DROP TABLE {schema}.{table.name}"))
        with Operations.context(MigrationContext.configure(connection)):
            if path == "fresh":
                for name in ("0007_bank_transactions", "0008_purchases", "0009_reconciliation"):
                    importlib.import_module("migrations.versions." + name).upgrade()
            migration = importlib.import_module("migrations.versions.0010_document_control")
            migration.upgrade()
            migration.upgrade()
        assert new_names <= set(inspect(connection).get_table_names(schema=schema))
        assert {"base_cents", "import_id"} <= {
            column["name"] for column in inspect(connection).get_columns("purchases", schema=schema)
        }
        assert "import_id" in {
            column["name"]
            for column in inspect(connection).get_columns("bank_transactions", schema=schema)
        }
        assert connection.scalar(text(f"SELECT count(*) FROM {schema}.users")) == before
