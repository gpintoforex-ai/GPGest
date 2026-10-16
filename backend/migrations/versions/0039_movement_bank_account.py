"""Keep the bank destination chosen when a movement is recorded."""
import importlib

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
        if "bank_account_id" not in {c["name"] for c in sa.inspect(connection).get_columns("movements", schema=schema)}:
            op.add_column("movements", sa.Column("bank_account_id", sa.String(36), nullable=True), schema=schema)
            op.create_foreign_key("fk_movement_bank_account", "movements", "bank_accounts", ["bank_account_id"], ["id"], source_schema=schema, referent_schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
        op.drop_column("movements", "bank_account_id", schema=schema)
