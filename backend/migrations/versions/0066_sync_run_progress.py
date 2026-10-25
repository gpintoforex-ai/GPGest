"""Progresso das sincronizações de tabelas: total, feitos, registo atual e resultados por registo."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None

COLUMNS = [
    ("total_count", lambda: sa.Column("total_count", sa.Integer(), nullable=False, server_default="0")),
    ("done_count", lambda: sa.Column("done_count", sa.Integer(), nullable=False, server_default="0")),
    ("current_label", lambda: sa.Column("current_label", sa.String(300), nullable=False, server_default="")),
    ("results", lambda: sa.Column("results", sa.Text(), nullable=False, server_default="[]")),
    ("heartbeat_at", lambda: sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)),
]


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("wt_sync_runs", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("wt_sync_runs", schema=schema)}
        for name, column in COLUMNS:
            if name not in existing:
                op.add_column("wt_sync_runs", column(), schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("wt_sync_runs", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("wt_sync_runs", schema=schema)}
        for name, _ in reversed(COLUMNS):
            if name in existing:
                op.drop_column("wt_sync_runs", name, schema=schema)
