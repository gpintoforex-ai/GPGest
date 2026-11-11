"""Ligações de loja online e referência idempotente de encomendas por empresa."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import WebshopConnection, WebshopOrder

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            bind = connection.execution_options(schema_translate_map={None: schema})
            if sa.inspect(bind).has_table("stock_articles", schema=schema):
                for model in (WebshopConnection, WebshopOrder):
                    model.__table__.create(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            bind = connection.execution_options(schema_translate_map={None: schema})
            for model in (WebshopOrder, WebshopConnection):
                model.__table__.drop(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
