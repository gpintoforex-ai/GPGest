"""Custo fixo nas rubricas (categories.fixed_cost) e limites dos alertas do mapa de custos e proveitos
(map_alert_settings).

Só acrescenta: sem rubricas marcadas como fixas nem limites gravados, os mapas mostram o mesmo de antes."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import MapAlertSettings

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        columns = {c["name"] for c in sa.inspect(connection).get_columns("categories", schema=schema)}
        if "fixed_cost" not in columns:
            op.add_column("categories", sa.Column("fixed_cost", sa.Boolean(), nullable=False, server_default=sa.text("false")), schema=schema)
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            MapAlertSettings.__table__.create(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            MapAlertSettings.__table__.drop(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
    for schema in schemas(connection):
        columns = {c["name"] for c in sa.inspect(connection).get_columns("categories", schema=schema)}
        if "fixed_cost" in columns:
            op.drop_column("categories", "fixed_cost", schema=schema)
