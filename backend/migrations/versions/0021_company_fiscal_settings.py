"""Company VAT reporting frequency."""
import importlib
from alembic import op
from app.models import CompanyFiscalSettings

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            CompanyFiscalSettings.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map={None: "public"})


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            CompanyFiscalSettings.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map={None: "public"})
