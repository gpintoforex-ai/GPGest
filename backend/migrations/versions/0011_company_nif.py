"""Unique NIF for SaaS company identification; preserve existing companies."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "platform_companies", sa.Column("nif", sa.String(9), nullable=True), schema="public"
    )
    op.create_unique_constraint(
        "platform_companies_nif_key", "platform_companies", ["nif"], schema="public"
    )


def downgrade():
    op.drop_constraint(
        "platform_companies_nif_key", "platform_companies", schema="public", type_="unique"
    )
    op.drop_column("platform_companies", "nif", schema="public")
