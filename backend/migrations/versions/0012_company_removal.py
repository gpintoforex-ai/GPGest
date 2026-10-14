"""Preserve data for removed companies."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "platform_companies",
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )


def downgrade():
    op.drop_column("platform_companies", "removed_at", schema="public")
