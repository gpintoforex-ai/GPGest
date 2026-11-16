"""Propostas de descritivo para o site geradas por IA (article_content_proposals).

Só acrescenta uma tabela: sem propostas, nada muda. Os novos campos do site na ficha do artigo (resumo, pontos-chave
e SEO) vivem no JSON de `stock_articles.data` e não precisam de coluna."""

import importlib

from alembic import op
from app.models import ArticleContentProposal

revision = "0093"
down_revision = "0092"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            ArticleContentProposal.__table__.create(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            ArticleContentProposal.__table__.drop(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
