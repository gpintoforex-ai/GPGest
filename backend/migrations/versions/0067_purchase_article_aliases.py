"""Correspondências aprendidas entre linhas de faturas de compra e artigos (referência, EAN, descrição)."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("stock_articles", schema=schema):
            continue
        if inspector.has_table("purchase_article_aliases", schema=schema):
            continue
        op.create_table(
            "purchase_article_aliases",
            sa.Column("id", sa.String(36), primary_key=True),
            # Vazio nos códigos de barras (valem para qualquer fornecedor).
            sa.Column("supplier_key", sa.String(36), nullable=False, server_default=""),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("key", sa.String(300), nullable=False),
            sa.Column(
                "article_id",
                sa.String(36),
                sa.ForeignKey(f"{schema}.stock_articles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("supplier_key", "kind", "key", name="purchase_alias_unique"),
            sa.CheckConstraint("kind IN ('ref', 'ean', 'desc')", name="purchase_alias_kind"),
            schema=schema,
        )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        if sa.inspect(connection).has_table("purchase_article_aliases", schema=schema):
            op.drop_table("purchase_article_aliases", schema=schema)
