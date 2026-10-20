"""Prazo de pagamento habitual do fornecedor e dedução de IVA estimada por rubrica."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

COLUMNS = {"business_entities": "payment_days", "categories": "vat_deduction_percent"}


def upgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
        inspector = sa.inspect(connection)
        for table, column in COLUMNS.items():
            if inspector.has_table(table, schema=schema) and column not in {c["name"] for c in inspector.get_columns(table, schema=schema)}:
                op.add_column(table, sa.Column(column, sa.Integer(), nullable=True), schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
        inspector = sa.inspect(connection)
        for table, column in COLUMNS.items():
            if inspector.has_table(table, schema=schema) and column in {c["name"] for c in inspector.get_columns(table, schema=schema)}:
                op.drop_column(table, column, schema=schema)
