"""Purchases, documents, reconciliation and e-Fatura configuration."""
import importlib
from alembic import op
from app.models import Purchase, PurchaseAttachment, PurchaseAllocation, EfaturaConfig, DocumentImport

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)
    try:
        for schema in schemas:
            connection.execution_options(schema_translate_map={None: schema})
            for model in (DocumentImport, Purchase, PurchaseAttachment, PurchaseAllocation, EfaturaConfig):
                model.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    raise RuntimeError("A remoção de compras e documentos exige uma decisão explícita.")
