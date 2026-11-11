"""Automatizações do Centro de Conferência (configuração por empresa; por omissão nada é automático)."""

import importlib

from alembic import op
from app.models import AutomationSettings

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

TABLES = (AutomationSettings,)


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            for model in TABLES:
                model.__table__.create(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            for model in reversed(TABLES):
                model.__table__.drop(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
