"""Sequential ERP rubric code prefix."""
from alembic import op
import sqlalchemy as sa
revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("platform_companies", sa.Column("rubric_code_prefix", sa.String(30), nullable=False, server_default="RUB"), schema="public")

def downgrade():
    op.drop_column("platform_companies", "rubric_code_prefix", schema="public")
