"""Sub-rubricas (categories.parent_id) e classificação de gestão por registo (record_annotations).

Só acrescenta: sem rubrica-mãe nem anotações, nada muda."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import RecordAnnotation

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        columns = {c["name"] for c in sa.inspect(connection).get_columns("categories", schema=schema)}
        if "parent_id" not in columns:
            op.add_column("categories", sa.Column("parent_id", sa.String(36), nullable=True), schema=schema)
            op.create_index("ix_categories_parent_id", "categories", ["parent_id"], schema=schema)
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            RecordAnnotation.__table__.create(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            RecordAnnotation.__table__.drop(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
    for schema in schemas(connection):
        columns = {c["name"] for c in sa.inspect(connection).get_columns("categories", schema=schema)}
        if "parent_id" in columns:
            op.drop_index("ix_categories_parent_id", table_name="categories", schema=schema)
            op.drop_column("categories", "parent_id", schema=schema)
