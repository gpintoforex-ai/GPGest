"""Protected scans and reusable cloud folders in each company."""
import importlib

import sqlalchemy as sa
from alembic import op
from app.models import DocumentScanSettings, ScanCloudFolder

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def create_document_scans():
    # Tabela como era nesta revisão: «origin» e «quick_access_id» (chave para
    # quick_scan_accesses) são acrescentadas pela 0055, que cria essa tabela.
    op.create_table(
        "document_scans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_key", sa.String(100), nullable=False, unique=True),
        sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purchase_id", sa.String(36), sa.ForeignKey("purchases.id"), nullable=True),
        sa.Column("movement_id", sa.String(36), sa.ForeignKey("movements.id"), nullable=True),
        sa.Column(
            "attachment_id", sa.String(36), sa.ForeignKey("drive_document_attachments.id"), nullable=True
        ),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("account", sa.String(200), nullable=False),
        sa.Column("root_folder", sa.String(200), nullable=False),
        sa.Column("drive_folder", sa.String(200), nullable=False),
        sa.Column("drive_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("document_date", sa.String(10), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("pdf_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("validation_status", sa.String(30), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("store_id", "digest", "document_date"):
        op.create_index(f"ix_document_scans_{column}", "document_scans", [column])


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            exists = sa.inspect(connection).has_table("document_scans", schema=schema)
            connection.execution_options(schema_translate_map={None: schema})
            for model in (DocumentScanSettings, ScanCloudFolder):
                model.__table__.create(connection, checkfirst=True)
            if not exists:
                create_document_scans()
            connection.execution_options(schema_translate_map={None: "public"})
    finally:
        connection.execution_options(schema_translate_map={None: "public"})

def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            if sa.inspect(connection).has_table("document_scans", schema=schema):
                op.drop_table("document_scans", schema=schema)
            connection.execution_options(schema_translate_map={None: schema})
            for model in (ScanCloudFolder, DocumentScanSettings):
                model.__table__.drop(connection, checkfirst=True)
            connection.execution_options(schema_translate_map={None: "public"})
    finally:
        connection.execution_options(schema_translate_map={None: "public"})
