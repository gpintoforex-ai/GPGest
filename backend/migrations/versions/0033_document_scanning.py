"""Protected scans and reusable cloud folders in each company."""
import importlib
from alembic import op
from app.models import DocumentScanSettings, DocumentScan, ScanCloudFolder
revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            for model in (DocumentScanSettings, ScanCloudFolder, DocumentScan):
                model.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map={None: "public"})

def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            for model in (DocumentScan, ScanCloudFolder, DocumentScanSettings):
                model.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map={None: "public"})
