"""Meio de pagamento habitual de clientes e fornecedores (tabela «Meios de pagamento»)."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if inspector.has_table("business_entities", schema=schema) and "payment_method_id" not in {
            c["name"] for c in inspector.get_columns("business_entities", schema=schema)
        }:
            op.add_column(
                "business_entities", sa.Column("payment_method_id", sa.String(20), nullable=True), schema=schema
            )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if inspector.has_table("business_entities", schema=schema) and "payment_method_id" in {
            c["name"] for c in inspector.get_columns("business_entities", schema=schema)
        }:
            op.drop_column("business_entities", "payment_method_id", schema=schema)
