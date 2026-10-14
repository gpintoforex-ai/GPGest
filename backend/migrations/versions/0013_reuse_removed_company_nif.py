"""Allow new companies to reuse a removed company's NIF."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "platform_companies_nif_key", "platform_companies", schema="public", type_="unique"
    )
    op.create_index(
        "platform_companies_current_nif_key",
        "platform_companies",
        ["nif"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("removed_at IS NULL"),
    )


def downgrade():
    # Refuse a downgrade if reused NIFs exist, rather than modifying history.
    op.create_unique_constraint(
        "platform_companies_nif_key", "platform_companies", ["nif"], schema="public"
    )
    op.drop_index(
        "platform_companies_current_nif_key", table_name="platform_companies", schema="public"
    )
