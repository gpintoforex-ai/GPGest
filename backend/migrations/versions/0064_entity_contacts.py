"""Morada principal das entidades Wintouch e contactos editados na IAZELA."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None

MIRROR = {"address": 500, "locality": 120, "postal_code": 20}


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def columns(connection, table, schema):
    return {c["name"] for c in sa.inspect(connection).get_columns(table, schema=schema)}


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if inspector.has_table("wt_entities", schema=schema):
            existing = columns(connection, "wt_entities", schema)
            for name, size in MIRROR.items():
                if name not in existing:
                    op.add_column("wt_entities", sa.Column(name, sa.String(size), nullable=True), schema=schema)
        if inspector.has_table("business_entities", schema=schema) and "contact_local" not in columns(
            connection, "business_entities", schema
        ):
            op.add_column(
                "business_entities",
                sa.Column("contact_local", sa.Boolean(), nullable=False, server_default=sa.text("false")),
                schema=schema,
            )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if inspector.has_table("business_entities", schema=schema) and "contact_local" in columns(
            connection, "business_entities", schema
        ):
            op.drop_column("business_entities", "contact_local", schema=schema)
        if inspector.has_table("wt_entities", schema=schema):
            existing = columns(connection, "wt_entities", schema)
            for name in MIRROR:
                if name in existing:
                    op.drop_column("wt_entities", name, schema=schema)
