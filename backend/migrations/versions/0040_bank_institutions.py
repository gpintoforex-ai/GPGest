"""Add a tenant-scoped financial institution catalogue."""
import importlib

from alembic import op
from app.models import BankInstitution

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            BankInstitution.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            BankInstitution.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
