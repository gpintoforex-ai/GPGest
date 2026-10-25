"""Prazo e meio de pagamento das entidades importados da Wintouch."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import WintouchPaymentTerm

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

COLUMNS = [
    "payment_term_external_id",
    "payment_method_external_id",
    "supplier_payment_term_external_id",
    "supplier_payment_method_external_id",
]


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            inspector = sa.inspect(connection)
            if inspector.has_table("wt_entities", schema=schema):
                existing = {c["name"] for c in inspector.get_columns("wt_entities", schema=schema)}
                for column in COLUMNS:
                    if column not in existing:
                        op.add_column(
                            "wt_entities", sa.Column(column, sa.String(200), nullable=True), schema=schema
                        )
            WintouchPaymentTerm.__table__.create(
                connection.execution_options(schema_translate_map={None: schema}), checkfirst=True
            )
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            WintouchPaymentTerm.__table__.drop(
                connection.execution_options(schema_translate_map={None: schema}), checkfirst=True
            )
            inspector = sa.inspect(connection)
            if inspector.has_table("wt_entities", schema=schema):
                existing = {c["name"] for c in inspector.get_columns("wt_entities", schema=schema)}
                for column in COLUMNS:
                    if column in existing:
                        op.drop_column("wt_entities", column, schema=schema)
    finally:
        connection.execution_options(schema_translate_map=None)
