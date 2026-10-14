"""Explicit ERP selection in company settings."""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_companies", sa.Column("erp_provider", sa.String(40), nullable=False, server_default="wintouch"), schema="public")


def downgrade():
    op.drop_column("platform_companies", "erp_provider", schema="public")
