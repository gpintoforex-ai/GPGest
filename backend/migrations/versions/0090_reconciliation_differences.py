"""Natureza da diferença nas reconciliações: retenção na fonte e desconto financeiro
(reconciliations.withholding_cents e reconciliations.discount_cents).

Só acrescenta as colunas com valor 0: as reconciliações existentes não mudam."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None

COLUMNS = ("withholding_cents", "discount_cents")


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        existing = {c["name"] for c in sa.inspect(connection).get_columns("reconciliations", schema=schema)}
        for name in COLUMNS:
            if name not in existing:
                op.add_column("reconciliations", sa.Column(name, sa.BigInteger(), nullable=False, server_default="0"), schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        existing = {c["name"] for c in sa.inspect(connection).get_columns("reconciliations", schema=schema)}
        for name in reversed(COLUMNS):
            if name in existing:
                op.drop_column("reconciliations", name, schema=schema)
