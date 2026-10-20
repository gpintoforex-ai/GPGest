"""Repartição por rubrica dos movimentos previstos das compras (rubrica por linha)."""
import importlib

from alembic import op
from app.models import PlannedAllocation

revision = "0051"
down_revision = "0050"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            PlannedAllocation.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            PlannedAllocation.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
