"""Credit obligations and links to actual payments in all companies."""

import re

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
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
                "planned_movements",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("store_id", sa.String(36), sa.ForeignKey("stores.id"), nullable=False),
                sa.Column(
                    "category_id", sa.String(36), sa.ForeignKey("categories.id"), nullable=False
                ),
                sa.Column("entity", sa.String(200), nullable=False),
                sa.Column("document", sa.String(200), nullable=False),
                sa.Column("due_date", sa.String(10), nullable=False),
                sa.Column("amount_cents", sa.BigInteger(), nullable=False),
                sa.Column("direction", sa.Integer(), nullable=False),
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
                sa.CheckConstraint("amount_cents > 0"),
                sa.CheckConstraint("direction IN (-1, 1)"),
            )
            op.create_index("ix_planned_movements_store_id", "planned_movements", ["store_id"])
            op.create_table(
                "planned_settlements",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column(
                    "planned_id",
                    sa.String(36),
                    sa.ForeignKey("planned_movements.id"),
                    nullable=False,
                ),
                sa.Column(
                    "movement_id",
                    sa.String(36),
                    sa.ForeignKey("movements.id"),
                    nullable=False,
                    unique=True,
                ),
            )
            op.create_index(
                "ix_planned_settlements_planned_id", "planned_settlements", ["planned_id"]
            )
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    targets = schemas(connection)
    try:
        for schema in targets:
            connection.execution_options(schema_translate_map={None: schema})
            op.drop_table("planned_settlements")
            op.drop_table("planned_movements")
    finally:
        connection.execution_options(schema_translate_map=None)
