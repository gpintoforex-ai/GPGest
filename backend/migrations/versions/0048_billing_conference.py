"""Conferência faturação ↔ recebimentos: tabelas novas, sem alterar dados existentes."""
import importlib

from alembic import op
from app.models import BillingConference, BillingConferenceLine

revision = "0048"
down_revision = "0047"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            BillingConference.__table__.create(connection, checkfirst=True)
            BillingConferenceLine.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            BillingConferenceLine.__table__.drop(connection, checkfirst=True)
            BillingConference.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
