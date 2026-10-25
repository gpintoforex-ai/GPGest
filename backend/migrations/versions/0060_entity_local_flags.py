"""Cliente, fornecedor e ativo definidos localmente em entidades do ERP."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
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
        if "flags_local" not in existing:
            op.add_column(
                "business_entities",
                sa.Column("flags_local", sa.Boolean(), nullable=False, server_default="false"),
                schema=schema,
            )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("business_entities", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("business_entities", schema=schema)}
        if "flags_local" in existing:
            op.drop_column("business_entities", "flags_local", schema=schema)
