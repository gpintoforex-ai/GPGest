"""Associação do realizado ao orçamento (budget_links) e origem das linhas copiadas numa revisão."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import BudgetLink

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            columns = {c["name"] for c in sa.inspect(connection).get_columns("budget_lines", schema=schema)}
            if "origin_line_id" not in columns:
                op.add_column("budget_lines", sa.Column("origin_line_id", sa.String(36), nullable=True), schema=schema)
            bind = connection.execution_options(schema_translate_map={None: schema})
            BudgetLink.__table__.create(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            BudgetLink.__table__.drop(bind, checkfirst=True)
            columns = {c["name"] for c in sa.inspect(connection).get_columns("budget_lines", schema=schema)}
            if "origin_line_id" in columns:
                op.drop_column("budget_lines", "origin_line_id", schema=schema)
    finally:
        connection.execution_options(schema_translate_map=None)
