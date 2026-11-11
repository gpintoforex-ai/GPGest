"""Orçamento de despesas: centros de custo, orçamentos (versões) e despesas previstas."""

import importlib

from alembic import op
from app.models import Budget, BudgetLine, CostCenter

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

TABLES = (CostCenter, Budget, BudgetLine)


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
