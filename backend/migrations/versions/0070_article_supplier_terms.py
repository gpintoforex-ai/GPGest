"""Condições por fornecedor de cada artigo (referência, preço acordado, prazo, quantidades)."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
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
        if not inspector.has_table("business_entities", schema=schema):
            continue
        if inspector.has_table("article_supplier_terms", schema=schema):
            continue
        op.create_table(
            "article_supplier_terms",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "article_id",
                sa.String(36),
                sa.ForeignKey(f"{schema}.stock_articles.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "supplier_id",
                sa.String(36),
                sa.ForeignKey(f"{schema}.business_entities.id"),
                nullable=False,
            ),
            sa.Column("supplier_reference", sa.String(100), nullable=False, server_default=""),
            sa.Column("agreed_cost", sa.Numeric(24, 6), nullable=True),
            sa.Column("valid_until", sa.String(10), nullable=True),
            sa.Column("lead_days", sa.Integer(), nullable=True),
            sa.Column("min_quantity", sa.Numeric(24, 6), nullable=True),
            sa.Column("pack_quantity", sa.Numeric(24, 6), nullable=True),
            sa.Column("notes", sa.String(300), nullable=False, server_default=""),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "updated_by", sa.String(36), sa.ForeignKey(f"{schema}.users.id"), nullable=False
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("article_id", "supplier_id", name="article_supplier_unique"),
            sa.CheckConstraint(
                "agreed_cost IS NULL OR agreed_cost >= 0", name="article_supplier_cost"
            ),
            sa.CheckConstraint("lead_days IS NULL OR lead_days >= 0", name="article_supplier_lead"),
            sa.CheckConstraint(
                "(min_quantity IS NULL OR min_quantity > 0) AND "
                "(pack_quantity IS NULL OR pack_quantity > 0)",
                name="article_supplier_quantities",
            ),
            schema=schema,
        )
        for column in ("article_id", "supplier_id"):
            op.create_index(
                f"ix_article_supplier_terms_{column}",
                "article_supplier_terms",
                [column],
                schema=schema,
            )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        if sa.inspect(connection).has_table("article_supplier_terms", schema=schema):
            op.drop_table("article_supplier_terms", schema=schema)
