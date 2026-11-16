"""Publicação de artigos na loja online (webshop_publications, webshop_publication_events,
webshop_remote_products).

Só acrescenta tabelas: sem publicações, a loja não recebe artigos pelo novo circuito."""

import importlib

from alembic import op
from app.models import WebshopPublication, WebshopPublicationEvent, WebshopRemoteProduct

revision = "0095"
down_revision = "0094"
branch_labels = None
depends_on = None

TABLES = (WebshopPublication, WebshopPublicationEvent, WebshopRemoteProduct)


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
