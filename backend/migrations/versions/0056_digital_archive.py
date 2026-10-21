"""Arquivo digital: índice central de documentos, originais cifrados, relações, histórico e fila."""
import importlib

from alembic import op
from app.models import (
    ArchiveBlob,
    ArchiveDocument,
    ArchiveEvent,
    ArchiveJob,
    ArchiveLink,
    ArchiveSettings,
)

revision = "0056"
down_revision = "0055"
branch_labels = depends_on = None

TABLES = [ArchiveDocument, ArchiveBlob, ArchiveLink, ArchiveEvent, ArchiveJob, ArchiveSettings]


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            for model in TABLES:
                model.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            for model in reversed(TABLES):
                model.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
