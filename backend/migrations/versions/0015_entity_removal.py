"""Logical removal of local and imported entities."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
        connection
    ):
        op.add_column(
            "business_entities",
            sa.Column("removed", sa.Boolean(), nullable=False, server_default=sa.false()),
            schema=schema,
        )


def downgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
        connection
    ):
        op.drop_column("business_entities", "removed", schema=schema)
