"""Natureza financeira das rubricas: custos operacionais, compras, tesouraria, obrigações fiscais, orçamento.

Só acrescenta colunas; os valores por omissão reproduzem o comportamento anterior (nada muda até ser aprovado)."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None

COLUMNS = (
    ("nature", lambda: sa.Column("nature", sa.String(20), nullable=False, server_default="")),
    ("in_operating_costs", lambda: sa.Column("in_operating_costs", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
    ("in_purchases", lambda: sa.Column("in_purchases", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
    ("in_treasury", lambda: sa.Column("in_treasury", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
    ("in_fiscal", lambda: sa.Column("in_fiscal", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
    ("in_payment_budget", lambda: sa.Column("in_payment_budget", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
)


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        existing = {c["name"] for c in sa.inspect(connection).get_columns("categories", schema=schema)}
        for name, column in COLUMNS:
            if name not in existing:
                op.add_column("categories", column(), schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        existing = {c["name"] for c in sa.inspect(connection).get_columns("categories", schema=schema)}
        for name, _ in reversed(COLUMNS):
            if name in existing:
                op.drop_column("categories", name, schema=schema)
