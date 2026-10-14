"""Independent documentary review, ERP matching, drafts and payments."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import (
    DocumentERPLink,
    DocumentImport,
    DocumentPayment,
    DocumentState,
    ERPDocument,
    ExpenseDraft,
    PaymentDocumentLine,
)

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            connection.execution_options(schema_translate_map={None: schema})
            DocumentImport.__table__.create(connection, checkfirst=True)
            bank_columns = {
                c["name"]
                for c in sa.inspect(connection).get_columns("bank_transactions", schema=schema)
            }
            if "import_id" not in bank_columns:
                op.add_column(
                    "bank_transactions",
                    sa.Column("import_id", sa.String(36), nullable=True),
                    schema=schema,
                )
                op.create_foreign_key(
                    "bank_import_fk",
                    "bank_transactions",
                    "document_imports",
                    ["import_id"],
                    ["id"],
                    source_schema=schema,
                    referent_schema=schema,
                )
            columns = {
                c["name"] for c in sa.inspect(connection).get_columns("purchases", schema=schema)
            }
            if "base_cents" not in columns:
                op.add_column(
                    "purchases",
                    sa.Column("base_cents", sa.BigInteger(), nullable=True),
                    schema=schema,
                )
            if "import_id" not in columns:
                op.add_column(
                    "purchases", sa.Column("import_id", sa.String(36), nullable=True), schema=schema
                )
                op.create_foreign_key(
                    "purchase_import_fk",
                    "purchases",
                    "document_imports",
                    ["import_id"],
                    ["id"],
                    source_schema=schema,
                    referent_schema=schema,
                )
            for model in (
                DocumentState,
                ERPDocument,
                DocumentERPLink,
                ExpenseDraft,
                DocumentPayment,
                PaymentDocumentLine,
            ):
                model.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    raise RuntimeError("A remoção do histórico documental exige uma decisão explícita.")
