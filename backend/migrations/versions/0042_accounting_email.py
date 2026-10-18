"""Tenant-scoped SMTP settings, frozen accounting dossiers and email history."""

import importlib

from alembic import op
from app.models import CompanyEmailSettings, EmailDispatch, EmailDispatchPart

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            connection.execution_options(schema_translate_map={None: schema})
            for model in (CompanyEmailSettings, EmailDispatch, EmailDispatchPart):
                model.__table__.create(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            connection.execution_options(schema_translate_map={None: schema})
            for model in (EmailDispatchPart, EmailDispatch, CompanyEmailSettings):
                model.__table__.drop(connection, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
