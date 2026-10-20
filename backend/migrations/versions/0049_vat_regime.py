"""Regime de IVA da empresa, ao lado da periodicidade já existente."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas
    for schema in schemas(connection):
        existing = {
            column["name"]
            for column in sa.inspect(connection).get_columns("company_fiscal_settings", schema=schema)
        }
        if "vat_regime" not in existing:
            op.add_column(
                "company_fiscal_settings",
                sa.Column("vat_regime", sa.String(30), nullable=False, server_default=""),
                schema=schema,
            )
        # Até aqui só existia a periodicidade, que corresponde ao regime normal.
        table = sa.table("company_fiscal_settings", sa.column("vat_period"), sa.column("vat_regime"), schema=schema)
        op.execute(table.update().where(table.c.vat_period != "").values(vat_regime="normal"))


def downgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas
    for schema in schemas(connection):
        existing = {
            column["name"]
            for column in sa.inspect(connection).get_columns("company_fiscal_settings", schema=schema)
        }
        if "vat_regime" in existing:
            op.drop_column("company_fiscal_settings", "vat_regime", schema=schema)
