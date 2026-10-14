"""Company-scoped bank deposits, cheque claims, credit allocations and proofs."""

import importlib

from alembic import op
from app.models import BankDeposit, DepositAttachment, DepositCheque, DepositCredit

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None
tables = [BankDeposit, DepositCheque, DepositCredit, DepositAttachment]


def upgrade():
    conn = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
            conn.execution_options(schema_translate_map={None: schema})
            for model in tables:
                model.__table__.create(conn, checkfirst=True)
    finally:
        conn.execution_options(schema_translate_map={None: "public"})


def downgrade():
    conn = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
            conn.execution_options(schema_translate_map={None: schema})
            for model in reversed(tables):
                model.__table__.drop(conn, checkfirst=True)
    finally:
        conn.execution_options(schema_translate_map={None: "public"})
