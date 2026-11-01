"""Bonificação nas condições por fornecedor do artigo: na compra de X, oferta de Y."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

COLUMNS = ("bonus_buy_quantity", "bonus_free_quantity")


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("article_supplier_terms", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("article_supplier_terms", schema=schema)}
        for name in COLUMNS:
            if name not in existing:
                op.add_column(
                    "article_supplier_terms",
                    sa.Column(name, sa.Numeric(24, 6), nullable=True),
                    schema=schema,
                )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("article_supplier_terms", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("article_supplier_terms", schema=schema)}
        for name in COLUMNS:
            if name in existing:
                op.drop_column("article_supplier_terms", name, schema=schema)
