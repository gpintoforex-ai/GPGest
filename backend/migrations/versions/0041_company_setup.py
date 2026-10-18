"""Resumable company setup, register preferences and payment terminals."""
import importlib

import sqlalchemy as sa
from alembic import op
from app.models import CompanySetup, PaymentTerminal

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            checks = sa.inspect(connection).get_check_constraints("wt_payment_methods", schema=schema)
            mapping = next((c for c in checks if c["name"] == "wt_payment_mapping"), None)
            if mapping and "pm_%" not in mapping["sqltext"]:
                op.drop_constraint("wt_payment_mapping", "wt_payment_methods", type_="check", schema=schema)
                op.create_check_constraint("wt_payment_mapping", "wt_payment_methods", "local_payment IS NULL OR local_payment IN ('numerario','cartao','mbway','transferencia','outro') OR local_payment LIKE 'pm_%'", schema=schema)
            for table in ("registers", "bank_accounts", "local_payment_methods"):
                columns = {c["name"] for c in sa.inspect(connection).get_columns(table, schema=schema)}
                if "settings" not in columns:
                    op.add_column(table, sa.Column("settings", sa.Text(), nullable=False, server_default="{}"), schema=schema)
                if table == "bank_accounts" and "iban_ciphertext" not in columns:
                    op.add_column(table, sa.Column("iban_ciphertext", sa.Text(), nullable=False, server_default=""), schema=schema)
            connection.execution_options(schema_translate_map={None: schema})
            CompanySetup.__table__.create(connection, checkfirst=True)
            PaymentTerminal.__table__.create(connection, checkfirst=True)
            for table, name, kind, target in (
                ("cash_sessions", "retained_fund_cents", sa.BigInteger(), None),
                ("movements", "terminal_id", sa.String(36), "payment_terminals"),
                ("movements", "payment_method_id", sa.String(20), "local_payment_methods"),
            ):
                if name not in {c["name"] for c in sa.inspect(connection).get_columns(table, schema=schema)}:
                    op.add_column(table, sa.Column(name, kind, nullable=True), schema=schema)
                    if target:
                        op.create_foreign_key("fk_movement_" + name, table, target, [name], ["id"], source_schema=schema, referent_schema=schema)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            op.drop_column("movements", "terminal_id", schema=schema)
            op.drop_column("movements", "payment_method_id", schema=schema)
            op.drop_column("cash_sessions", "retained_fund_cents", schema=schema)
            connection.execution_options(schema_translate_map={None: schema})
            PaymentTerminal.__table__.drop(connection, checkfirst=True)
            CompanySetup.__table__.drop(connection, checkfirst=True)
            for table in ("registers", "bank_accounts", "local_payment_methods"):
                op.drop_column(table, "settings", schema=schema)
            op.drop_column("bank_accounts", "iban_ciphertext", schema=schema)
    finally:
        connection.execution_options(schema_translate_map=None)
