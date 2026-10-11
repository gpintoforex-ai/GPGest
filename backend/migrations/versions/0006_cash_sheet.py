"""Daily cash sheets and durable payment counts in every company schema."""

import re

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def schemas(connection):
    targets = {"public"} | set(
        connection.execute(sa.text("SELECT schema_name FROM public.platform_companies")).scalars()
    )
    if any(s != "public" and not re.fullmatch(r"gpgest_c_[a-f0-9]{32}", s) for s in targets):
        raise RuntimeError("Unexpected company schema; migration cancelled.")
    return sorted(targets)


def upgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            op.add_column("cash_sessions", sa.Column("sheet_day", sa.String(10)), schema=schema)
            op.add_column(
                "cash_sessions", sa.Column("wintouch_scope", sa.String(50)), schema=schema
            )
            op.add_column(
                "cash_sessions", sa.Column("wintouch_enterprise", sa.String(100)), schema=schema
            )
            op.add_column(
                "cash_sessions",
                sa.Column("count_data", sa.Text(), nullable=False, server_default="{}"),
                schema=schema,
            )
            op.create_unique_constraint(
                "uq_cash_sheet_wintouch_day", "cash_sessions", ["wintouch_scope"], schema=schema
            )
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            op.drop_constraint(
                "uq_cash_sheet_wintouch_day", "cash_sessions", type_="unique", schema=schema
            )
            for column in ("count_data", "wintouch_enterprise", "wintouch_scope", "sheet_day"):
                op.drop_column("cash_sessions", column, schema=schema)
    finally:
        connection.execution_options(schema_translate_map=None)
