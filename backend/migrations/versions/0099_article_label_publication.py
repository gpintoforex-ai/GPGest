"""Rótulo do artigo no site (article_images.public e .gallery).

A fotografia do rótulo continua interna por omissão; só vai para a loja quando alguém confirma na ficha que está
legível e em português. As imagens existentes ficam como estavam (não públicas)."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0099"
down_revision = "0098"
branch_labels = None
depends_on = None

COLUMNS = ("public", "gallery")


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("article_images", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("article_images", schema=schema)}
        for name in COLUMNS:
            if name not in existing:
                op.add_column("article_images", sa.Column(name, sa.Boolean(), nullable=False,
                                                          server_default=sa.text("false")), schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("article_images", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("article_images", schema=schema)}
        for name in COLUMNS:
            if name in existing:
                op.drop_column("article_images", name, schema=schema)
