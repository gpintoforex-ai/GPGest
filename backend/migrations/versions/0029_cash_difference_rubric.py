"""Company-local rubric selection for cash closing differences."""
import importlib
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
        op.add_column("categories", sa.Column("cash_difference", sa.Boolean(), nullable=False, server_default=sa.text("false")), schema=schema)
        op.create_index("uq_category_cash_difference", "categories", ["cash_difference"], unique=True, schema=schema, postgresql_where=sa.text("cash_difference = true"))


def downgrade():
    conn = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(conn):
        op.drop_index("uq_category_cash_difference", table_name="categories", schema=schema)
        op.drop_column("categories", "cash_difference", schema=schema)
