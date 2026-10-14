"""Audited reconciliation groups and partial allocations in every company."""

import importlib

from alembic import op
from app.models import Reconciliation, ReconciliationIgnore, ReconciliationLine

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            connection.execution_options(schema_translate_map={None: schema})
            for model in (Reconciliation, ReconciliationLine, ReconciliationIgnore):
                model.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    raise RuntimeError("A remoção do histórico de reconciliação exige uma decisão explícita.")
