"""Retenção na fonte na ficha da entidade: indicação e taxa (centésimas de ponto percentual)."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("business_entities", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("business_entities", schema=schema)}
        if "withholding" not in existing:
            op.add_column("business_entities", sa.Column("withholding", sa.Boolean(), nullable=False, server_default="false"),
                          schema=schema)
        if "withholding_basis_points" not in existing:
            op.add_column("business_entities", sa.Column("withholding_basis_points", sa.Integer(), nullable=True), schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("business_entities", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("business_entities", schema=schema)}
        for name in ("withholding_basis_points", "withholding"):
            if name in existing:
                op.drop_column("business_entities", name, schema=schema)
