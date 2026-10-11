"""Read-only Wintouch sales snapshots in each registered company."""

import re

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def schemas(connection):
    result = {"public"} | set(
        connection.execute(sa.text("SELECT schema_name FROM public.platform_companies")).scalars()
    )
    if any(s != "public" and not re.fullmatch(r"gpgest_c_[a-f0-9]{32}", s) for s in result):
        raise RuntimeError("Unexpected company schema; migration cancelled.")
    return sorted(result)


def upgrade():
    connection = op.get_bind()
    targets = schemas(connection)
    try:
        for schema in targets:
            connection.execution_options(schema_translate_map={None: schema})
            op.create_table(
                "wt_sale_records",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("enterprise_id", sa.String(100), nullable=False),
                sa.Column("external_id", sa.String(200), nullable=False),
                sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
                sa.Column("source_version", sa.String(200), nullable=True),
                sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column(
                    "sync_run_id", sa.String(36), sa.ForeignKey("wt_sync_runs.id"), nullable=True
                ),
                sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id"), nullable=False),
                sa.Column("kind", sa.String(20), nullable=False),
                sa.Column("document_date", sa.String(10), nullable=False),
                sa.Column("active", sa.Boolean(), nullable=False),
                sa.Column("data", sa.Text(), nullable=False),
                sa.UniqueConstraint("enterprise_id", "external_id"),
            )
            op.create_index("ix_wt_sale_records_store_id", "wt_sale_records", ["store_id"])
            op.create_index(
                "ix_wt_sale_records_document_date", "wt_sale_records", ["document_date"]
            )
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    targets = schemas(connection)
    try:
        for schema in targets:
            connection.execution_options(schema_translate_map={None: schema})
            op.drop_table("wt_sale_records")
    finally:
        connection.execution_options(schema_translate_map=None)
