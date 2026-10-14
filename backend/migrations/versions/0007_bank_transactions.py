"""Imported bank statements in every company schema."""
import importlib
from alembic import op
from app.models import BankTransaction, DocumentImport

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)
    try:
        for schema in schemas:
            connection.execution_options(schema_translate_map={None: schema})
            DocumentImport.__table__.create(connection, checkfirst=True)
            BankTransaction.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    raise RuntimeError("A remoção dos extratos importados requer uma decisão explícita.")
