"""Bank statement provenance and audited reconciliation period snapshots."""
import importlib
import sqlalchemy as sa
from alembic import op
from app.models import BankReconciliationPeriod

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            inspector = sa.inspect(connection)
            if not inspector.has_table("bank_transactions", schema=schema):
                continue
            columns = {c["name"] for c in inspector.get_columns("bank_transactions", schema=schema)}
            if "reference" not in columns:
                op.add_column("bank_transactions", sa.Column("reference", sa.String(200), nullable=False, server_default=""), schema=schema)
            op.alter_column("bank_transactions", "balance_cents", existing_type=sa.BigInteger(), nullable=True, schema=schema)
            if "file_ciphertext" not in {c["name"] for c in inspector.get_columns("document_imports", schema=schema)}:
                op.add_column("document_imports", sa.Column("file_ciphertext", sa.LargeBinary(), nullable=True), schema=schema)
            BankReconciliationPeriod.__table__.create(connection.execution_options(schema_translate_map={None: schema}), checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)

def downgrade():
    # Missing historical statement balances cannot safely be replaced with zero.
    raise RuntimeError("Esta migração preserva saldos desconhecidos; a reversão exige revisão dos dados.")
