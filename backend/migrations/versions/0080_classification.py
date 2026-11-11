"""Classificação de rubricas: regras aprovadas e repartição de compras sem linhas nem documento ERP."""

import importlib

from alembic import op
from app.models import ClassificationRule, PurchaseRubric

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None

TABLES = (ClassificationRule, PurchaseRubric)


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
