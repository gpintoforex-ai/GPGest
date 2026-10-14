"""Category appearance and provider-neutral external identity."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import CategoryIntegrationLink

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            op.add_column(
                "categories",
                sa.Column("icon", sa.String(40), nullable=False, server_default="tag"),
                schema=schema,
            )
            op.add_column(
                "categories",
                sa.Column("color", sa.String(7), nullable=False, server_default="#245fc5"),
                schema=schema,
            )
            connection.execution_options(schema_translate_map={None: schema})
            CategoryIntegrationLink.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map={None: "public"})


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            connection.execution_options(schema_translate_map={None: schema})
            CategoryIntegrationLink.__table__.drop(connection, checkfirst=True)
            op.drop_column("categories", "color", schema=schema)
            op.drop_column("categories", "icon", schema=schema)
    finally:
        connection.execution_options(schema_translate_map={None: "public"})
