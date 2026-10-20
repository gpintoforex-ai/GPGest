"""Digitalização rápida por PIN: acessos emparelhados, sessões curtas e origem das digitalizações."""
import importlib

import sqlalchemy as sa
from alembic import op
from app.models import QuickScanAccess, QuickScanSession

revision = "0055"
down_revision = "0054"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)
    try:
        for schema in schemas:
            connection.execution_options(schema_translate_map={None: schema})
            QuickScanAccess.__table__.create(connection, checkfirst=True)
            QuickScanSession.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
    for schema in schemas:
        inspector = sa.inspect(connection)
        if not inspector.has_table("document_scans", schema=schema):
            continue
        columns = {c["name"] for c in inspector.get_columns("document_scans", schema=schema)}
        if "origin" not in columns:
            op.add_column("document_scans", sa.Column("origin", sa.String(40), nullable=False, server_default="digitalizacao"), schema=schema)
        if "quick_access_id" not in columns:
            op.add_column("document_scans", sa.Column("quick_access_id", sa.String(36), nullable=True), schema=schema)
            op.create_foreign_key("document_scans_quick_access_id_fkey", "document_scans", "quick_scan_accesses", ["quick_access_id"], ["id"], source_schema=schema, referent_schema=schema)
            op.create_index("ix_document_scans_quick_access_id", "document_scans", ["quick_access_id"], schema=schema)


def downgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)
    for schema in schemas:
        inspector = sa.inspect(connection)
        if not inspector.has_table("document_scans", schema=schema):
            continue
        columns = {c["name"] for c in inspector.get_columns("document_scans", schema=schema)}
        if "quick_access_id" in columns:
            op.drop_index("ix_document_scans_quick_access_id", "document_scans", schema=schema)
            op.drop_constraint("document_scans_quick_access_id_fkey", "document_scans", schema=schema, type_="foreignkey")
            op.drop_column("document_scans", "quick_access_id", schema=schema)
        if "origin" in columns:
            op.drop_column("document_scans", "origin", schema=schema)
    try:
        for schema in schemas:
            connection.execution_options(schema_translate_map={None: schema})
            QuickScanSession.__table__.drop(connection, checkfirst=True)
            QuickScanAccess.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
