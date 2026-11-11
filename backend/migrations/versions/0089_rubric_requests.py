"""Pedidos de aprovação de alterações às rubricas (rubric_requests).

Só cria a tabela; sem pedidos, nada muda."""

import importlib

from alembic import op
from app.models import RubricRequest

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None

TABLES = (RubricRequest,)


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
