"""Supplier orders, receipts and traceable links to existing purchases and stock."""
import importlib
from alembic import op
from app.models import ProcurementDocument, ProcurementLink
from sqlalchemy import inspect
import sqlalchemy as sa

revision = "0046"
down_revision = "0045"
branch_labels = depends_on = None

def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            for model in (ProcurementDocument, ProcurementLink):
                model.__table__.create(connection, checkfirst=True)
            if 'purchase_id' not in {c['name'] for c in inspect(connection).get_columns('planned_movements', schema=schema)}:
                op.add_column('planned_movements', sa.Column('purchase_id', sa.String(36), nullable=True), schema=schema)
                op.create_foreign_key('planned_purchase_fk', 'planned_movements', 'purchases', ['purchase_id'], ['id'], source_schema=schema, referent_schema=schema)
                op.create_unique_constraint('planned_purchase_unique', 'planned_movements', ['purchase_id'], schema=schema)
    finally:
        connection.execution_options(schema_translate_map=None)

def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            op.drop_column('planned_movements', 'purchase_id', schema=schema)
            for model in (ProcurementLink, ProcurementDocument):
                model.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
