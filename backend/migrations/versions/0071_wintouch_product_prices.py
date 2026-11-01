"""Preço de venda com IVA e taxa de IVA dos artigos importados da Wintouch."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None

COLUMNS = (("sale_price_vat", sa.Numeric(18, 4)), ("vat_rate", sa.Numeric(7, 4)))


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("wt_products", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("wt_products", schema=schema)}
        for name, kind in COLUMNS:
            if name not in existing:
                op.add_column("wt_products", sa.Column(name, kind, nullable=True), schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("wt_products", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("wt_products", schema=schema)}
        for name, _ in COLUMNS:
            if name in existing:
                op.drop_column("wt_products", name, schema=schema)
