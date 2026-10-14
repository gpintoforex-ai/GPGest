"""Local payments linked to forecast documents without writing to an ERP."""
import importlib
from alembic import op
from app.models import LiquiditySettlement

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
            conn.execution_options(schema_translate_map={None: schema})
            LiquiditySettlement.__table__.create(conn, checkfirst=True)
    finally:
        conn.execution_options(schema_translate_map={None: "public"})


def downgrade():
    conn = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
            conn.execution_options(schema_translate_map={None: schema})
            LiquiditySettlement.__table__.drop(conn, checkfirst=True)
    finally:
        conn.execution_options(schema_translate_map={None: "public"})
