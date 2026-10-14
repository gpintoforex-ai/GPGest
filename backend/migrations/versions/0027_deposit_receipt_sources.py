"""Reserve imported cheque receipts as well as local receipts."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
        if "source_receipt" not in {
            c["name"] for c in sa.inspect(conn).get_columns("deposit_cheques", schema=schema)
        }:
            op.add_column(
                "deposit_cheques", sa.Column("source_receipt", sa.String(200)), schema=schema
            )
            op.create_index(
                "deposit_cheque_active_source",
                "deposit_cheques",
                ["source_receipt"],
                unique=True,
                schema=schema,
                postgresql_where=sa.text("active AND source_receipt IS NOT NULL"),
            )


def downgrade():
    conn = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
        op.drop_index("deposit_cheque_active_source", table_name="deposit_cheques", schema=schema)
        op.drop_column("deposit_cheques", "source_receipt", schema=schema)
