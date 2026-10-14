"""Deposit transfers can reference a register without opening a sales cash sheet."""

import importlib

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
        op.alter_column("movements", "session_id", nullable=True, schema=schema)


def downgrade():
    conn = op.get_bind()
    from sqlalchemy import text

    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
        if conn.execute(
            text(f'SELECT 1 FROM "{schema}".movements WHERE session_id IS NULL LIMIT 1')
        ).first():
            raise RuntimeError(
                "Deposit transfers without a cash sheet exist; migration cannot be reversed."
            )
        op.alter_column("movements", "session_id", nullable=False, schema=schema)
