"""Company staff records without application access."""
import importlib

from alembic import op
from app.models import Employee, EmployeeStore

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            Employee.__table__.create(connection, checkfirst=True)
            EmployeeStore.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map={None: "public"})


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection):
            connection.execution_options(schema_translate_map={None: schema})
            EmployeeStore.__table__.drop(connection, checkfirst=True)
            Employee.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map={None: "public"})
