"""Obrigações e prazos: regras, ocorrências (histórico independente) e confirmações de leitura."""

import importlib

from alembic import op
from app.models import Obligation, ObligationOccurrence, ObligationRead

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

TABLES = (Obligation, ObligationOccurrence, ObligationRead)


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
