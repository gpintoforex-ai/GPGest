"""Store addresses returned by the VIES NIF lookup."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None

COLUMNS = {
    "address": sa.String(500),
    "locality": sa.String(120),
    "postal_code": sa.String(20),
}


def upgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas
    for schema in schemas(connection):
        existing = {
            column["name"]
            for column in sa.inspect(connection).get_columns("business_entities", schema=schema)
        }
        for name, column_type in COLUMNS.items():
            if name not in existing:
                op.add_column(
                    "business_entities",
                    sa.Column(name, column_type, nullable=False, server_default=""),
                    schema=schema,
                )


def downgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas
    for schema in schemas(connection):
        existing = {
            column["name"]
            for column in sa.inspect(connection).get_columns("business_entities", schema=schema)
        }
        for name in reversed(tuple(COLUMNS)):
            if name in existing:
                op.drop_column("business_entities", name, schema=schema)
