"""Comportamento comum das tabelas: ligações ERP, regras de conflito e estado ativo/inativo."""

import importlib
import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from app.models import ErpLink, TableSyncSetting

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

ACTIVE = {"categories": "active", "registers": "active", "stock_warehouses": "active"}
TERM_TYPES = {"0": "apos", "3": "fim_mes", "4": "fim_mes_seguinte"}


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def columns(connection, table, schema):
    return {c["name"] for c in sa.inspect(connection).get_columns(table, schema=schema)}


def upgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            inspector = sa.inspect(connection)
            if not inspector.has_table("categories", schema=schema):
                continue
            translated = connection.execution_options(schema_translate_map={None: schema})
            for model in (ErpLink, TableSyncSetting):
                model.__table__.create(translated, checkfirst=True)
            connection.execution_options(schema_translate_map=None)
            for table, column in ACTIVE.items():
                if inspector.has_table(table, schema=schema) and column not in columns(connection, table, schema):
                    op.add_column(
                        table,
                        sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.text("true")),
                        schema=schema,
                    )
            if inspector.has_table("stock_warehouses", schema=schema) and "code" not in columns(
                connection, "stock_warehouses", schema
            ):
                op.add_column("stock_warehouses", sa.Column("code", sa.String(40), nullable=True), schema=schema)
                op.create_unique_constraint(
                    "stock_warehouses_code_key", "stock_warehouses", ["code"], schema=schema
                )
            if inspector.has_table("payment_terms", schema=schema) and "wintouch_id" in columns(
                connection, "payment_terms", schema
            ):
                # As ligações dos prazos à Wintouch passam para erp_links.
                linked = connection.execute(
                    sa.text(
                        f'SELECT t.id, t.code, t.name, t.term_type, t.days, t.discount_basis_points, '
                        f'w.enterprise_id, w.external_id, w.code AS remote_code, w.synced_at '
                        f'FROM "{schema}".payment_terms t JOIN "{schema}".wt_payment_terms w ON w.id = t.wintouch_id'
                    )
                ).mappings()
                for row in linked:
                    base = {
                        "code": row["code"],
                        "name": row["name"],
                        "term_type": row["term_type"],
                        "days": row["days"],
                        "discount_percent": f"{(row['discount_basis_points'] or 0) / 100:.2f}",
                    }
                    connection.execute(
                        sa.text(
                            f'INSERT INTO "{schema}".erp_links (id, table_key, local_id, provider, enterprise_id, '
                            "external_id, external_code, origin, state, base_values, synced_at, error, operation, "
                            "created_at, updated_at) VALUES (:id, 'payment_terms', :local, 'wintouch', :enterprise, "
                            ":external, :code, 'erp', 'sincronizado', :base, :synced, '', '', now(), now())"
                        ),
                        {
                            "id": str(uuid4()),
                            "local": row["id"],
                            "enterprise": row["enterprise_id"],
                            "external": row["external_id"],
                            "code": row["remote_code"] or "",
                            "base": json.dumps(base, ensure_ascii=False, sort_keys=True),
                            "synced": row["synced_at"],
                        },
                    )
                op.drop_column("payment_terms", "wintouch_id", schema=schema)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            inspector = sa.inspect(connection)
            if not inspector.has_table("categories", schema=schema):
                continue
            if inspector.has_table("payment_terms", schema=schema) and "wintouch_id" not in columns(
                connection, "payment_terms", schema
            ):
                op.add_column("payment_terms", sa.Column("wintouch_id", sa.String(36), nullable=True), schema=schema)
            if inspector.has_table("stock_warehouses", schema=schema) and "code" in columns(
                connection, "stock_warehouses", schema
            ):
                op.drop_constraint("stock_warehouses_code_key", "stock_warehouses", schema=schema)
                op.drop_column("stock_warehouses", "code", schema=schema)
            for table, column in ACTIVE.items():
                if inspector.has_table(table, schema=schema) and column in columns(connection, table, schema):
                    op.drop_column(table, column, schema=schema)
            translated = connection.execution_options(schema_translate_map={None: schema})
            for model in (TableSyncSetting, ErpLink):
                model.__table__.drop(translated, checkfirst=True)
            connection.execution_options(schema_translate_map=None)
    finally:
        connection.execution_options(schema_translate_map=None)
