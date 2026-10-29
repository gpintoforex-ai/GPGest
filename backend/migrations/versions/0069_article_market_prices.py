"""Preços de mercado dos artigos (KuantoKusta ou outras lojas), registados à mão."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
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
        if inspector.has_table("article_market_prices", schema=schema):
            continue
        op.create_table(
            "article_market_prices",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "article_id",
                sa.String(36),
                sa.ForeignKey(f"{schema}.stock_articles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("source", sa.String(20), nullable=False, server_default="kuantokusta"),
            sa.Column("store_name", sa.String(120), nullable=False),
            sa.Column("price_cents", sa.BigInteger(), nullable=False),
            sa.Column("observed_on", sa.String(10), nullable=False),
            sa.Column("url", sa.String(2000), nullable=False, server_default=""),
            sa.Column("notes", sa.String(300), nullable=False, server_default=""),
            sa.Column(
                "user_id", sa.String(36), sa.ForeignKey(f"{schema}.users.id"), nullable=False
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("price_cents > 0", name="market_price_positive"),
            sa.CheckConstraint("source IN ('kuantokusta', 'outro')", name="market_price_source"),
            schema=schema,
        )
        op.create_index(
            "ix_article_market_prices_article_id",
            "article_market_prices",
            ["article_id"],
            schema=schema,
        )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        if sa.inspect(connection).has_table("article_market_prices", schema=schema):
            op.drop_table("article_market_prices", schema=schema)
