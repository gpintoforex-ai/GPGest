"""Explicit approved bank book entries; statement import remains independent."""
import importlib
from alembic import op
from app.models import BankBookEntry

revision = '0047'
down_revision = '0046'
branch_labels = depends_on = None

def upgrade():
    connection=op.get_bind()
    try:
        for schema in importlib.import_module('migrations.versions.0006_cash_sheet').schemas(connection):
            connection.execution_options(schema_translate_map={None:schema})
            BankBookEntry.__table__.create(connection,checkfirst=True)
    finally:connection.execution_options(schema_translate_map=None)

def downgrade():
    connection=op.get_bind()
    try:
        for schema in importlib.import_module('migrations.versions.0006_cash_sheet').schemas(connection):
            connection.execution_options(schema_translate_map={None:schema})
            BankBookEntry.__table__.drop(connection,checkfirst=True)
    finally:connection.execution_options(schema_translate_map=None)
