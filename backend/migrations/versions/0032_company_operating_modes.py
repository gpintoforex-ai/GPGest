"""Explicit operating modes and common integration identities per company."""
import importlib
from alembic import op
import sqlalchemy as sa
from app.models import IntegrationBinding, AccountBalanceReference

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    for name, typ, default in [("operating_mode", sa.String(20), "local"), ("connector_writes", sa.Integer(), "0"), ("sync_resources", sa.Text(), '["products","entities","cash_accounts","sales","purchase_documents","rubrics"]'), ("data_authority", sa.Text(), '{}'), ("integration_revision", sa.Integer(), "0"), ("sync_interval_minutes", sa.Integer(), "0")]:
        op.add_column("platform_companies", sa.Column(name, typ, nullable=False, server_default=default), schema="public")
    op.add_column("platform_companies", sa.Column("last_scheduled_at", sa.DateTime(timezone=True), nullable=True), schema="public")
    conn.execute(sa.text("UPDATE public.platform_companies SET operating_mode='integrated', connector_writes=1 WHERE api_active=1 AND erp_provider <> 'none'"))
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
            conn.execution_options(schema_translate_map={None: schema})
            for model in (IntegrationBinding, AccountBalanceReference):
                model.__table__.create(conn, checkfirst=True)
    finally:
        conn.execution_options(schema_translate_map={None: "public"})


def downgrade():
    conn = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
            conn.execution_options(schema_translate_map={None: schema})
            for model in (AccountBalanceReference, IntegrationBinding):
                model.__table__.drop(conn, checkfirst=True)
    finally:
        conn.execution_options(schema_translate_map={None: "public"})
    for name in ("last_scheduled_at", "sync_interval_minutes", "integration_revision", "data_authority", "sync_resources", "connector_writes", "operating_mode"):
        op.drop_column("platform_companies", name, schema="public")
