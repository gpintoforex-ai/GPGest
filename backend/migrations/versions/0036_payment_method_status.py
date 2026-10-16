"""Payment methods can be inactivated without deleting company history."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
        columns = sa.inspect(connection).get_columns("local_payment_methods", schema=schema)
        # A fresh 0035 uses the current model, which already includes this column.
        if "active" not in {column["name"] for column in columns}:
            op.add_column(
                "local_payment_methods",
                sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
                schema=schema,
            )


def downgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
        op.drop_column("local_payment_methods", "active", schema=schema)
