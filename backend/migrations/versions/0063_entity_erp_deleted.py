"""Entidades eliminadas no ERP a partir da IAZELA: o espelho mantém-se para o histórico."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("wt_entities", schema=schema):
            continue
        if "erp_deleted_at" not in {c["name"] for c in inspector.get_columns("wt_entities", schema=schema)}:
            op.add_column(
                "wt_entities", sa.Column("erp_deleted_at", sa.DateTime(timezone=True), nullable=True), schema=schema
            )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if inspector.has_table("wt_entities", schema=schema) and "erp_deleted_at" in {
            c["name"] for c in inspector.get_columns("wt_entities", schema=schema)
        }:
            op.drop_column("wt_entities", "erp_deleted_at", schema=schema)
