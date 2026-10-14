"""Independent invoice evidence, checks and incremental caches per company."""
import importlib
from alembic import op
from app.models import InvoiceWorkflowSettings, InvoiceWorkflowCheck, InvoiceEvidenceCache, InvoiceEvidenceValidation, InvoiceWorkflowRun
revision="0034"
down_revision="0033"
branch_labels=None
depends_on=None
MODELS=(InvoiceWorkflowSettings,InvoiceWorkflowCheck,InvoiceEvidenceCache,InvoiceEvidenceValidation,InvoiceWorkflowRun)
def upgrade():
    connection=op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None:schema})
            for model in MODELS: model.__table__.create(connection,checkfirst=True)
    finally: connection.execution_options(schema_translate_map={None:"public"})
def downgrade():
    connection=op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None:schema})
            for model in reversed(MODELS): model.__table__.drop(connection,checkfirst=True)
    finally: connection.execution_options(schema_translate_map={None:"public"})
