"""Imagens carregadas na ficha do artigo (article_images): fotografia principal e rótulo.

Só acrescenta uma tabela: sem imagens carregadas, a ficha continua a usar o endereço HTTPS da fotografia."""

import importlib

from alembic import op
from app.models import ArticleImage

revision = "0098"
down_revision = "0097"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            ArticleImage.__table__.create(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            ArticleImage.__table__.drop(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
