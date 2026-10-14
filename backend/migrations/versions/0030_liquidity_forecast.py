"""Independent liquidity forecasts, adjustments and simulations."""
import importlib
from alembic import op
import sqlalchemy as sa
from app.models import LiquiditySettings, LiquidityManual, LiquidityAdjustment, LiquidityScenario

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None
tables = [LiquiditySettings, LiquidityManual, LiquidityAdjustment, LiquidityScenario]


def upgrade():
    conn = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
            op.add_column("bank_accounts", sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"), schema=schema)
            op.add_column("registers", sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"), schema=schema)
            conn.execution_options(schema_translate_map={None: schema})
            for model in tables:
                model.__table__.create(conn, checkfirst=True)
    finally:
        conn.execution_options(schema_translate_map={None: "public"})


def downgrade():
    conn = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
            conn.execution_options(schema_translate_map={None: schema})
            for model in reversed(tables):
                model.__table__.drop(conn, checkfirst=True)
            op.drop_column("bank_accounts", "currency", schema=schema)
            op.drop_column("registers", "currency", schema=schema)
    finally:
        conn.execution_options(schema_translate_map={None: "public"})
