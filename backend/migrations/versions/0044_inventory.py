"""Integrated articles, lots, stock ledger and physical inventories."""

import importlib

from alembic import op
from app.models import (
    StockArticle,
    StockEntry,
    StockImport,
    StockInventory,
    StockLot,
    StockSettings,
    StockWarehouse,
)

revision = "0044"
down_revision = "0043"
branch_labels = depends_on = None
TABLES = (
    StockArticle,
    StockWarehouse,
    StockLot,
    StockEntry,
    StockInventory,
    StockSettings,
    StockImport,
)


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            connection.execution_options(schema_translate_map={None: schema})
            for model in TABLES:
                model.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            connection.execution_options(schema_translate_map={None: schema})
            for model in reversed(TABLES):
                model.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
