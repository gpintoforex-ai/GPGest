"""e-Fatura via Google Drive: origem do ficheiro, pastas próprias, rejeições e estado do assistente."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

COLUMNS = {
    "document_imports": {
        "drive_id": (sa.String(200), ""),
        "drive_version": (sa.String(200), ""),
    },
    "invoice_workflow_settings": {
        "efatura_folders": (sa.Text(), "[]"),
        "include_subfolders": (sa.Boolean(), sa.true()),
        "assistant_state": (sa.Text(), "{}"),
    },
    "invoice_workflow_checks": {
        "rejected": (sa.Text(), "[]"),
    },
}


def upgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        for table, columns in COLUMNS.items():
            if not inspector.has_table(table, schema=schema):
                continue
            existing = {column["name"] for column in inspector.get_columns(table, schema=schema)}
            for name, (column_type, default) in columns.items():
                if name not in existing:
                    op.add_column(
                        table,
                        sa.Column(name, column_type, nullable=False, server_default=default),
                        schema=schema,
                    )


def downgrade():
    connection = op.get_bind()
    schemas = importlib.import_module("migrations.versions.0006_cash_sheet").schemas
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        for table, columns in COLUMNS.items():
            if not inspector.has_table(table, schema=schema):
                continue
            existing = {column["name"] for column in inspector.get_columns(table, schema=schema)}
            for name in reversed(tuple(columns)):
                if name in existing:
                    op.drop_column(table, name, schema=schema)
