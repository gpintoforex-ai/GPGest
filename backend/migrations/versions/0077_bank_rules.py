"""Movimentos bancários sem documento: rubrica, funcionário e custo nos registos; regras por descrição."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import BankRule

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

# Nome, coluna e tabela referenciada (no mesmo esquema da empresa).
COLUMNS = (
    ("category_id", lambda: sa.Column("category_id", sa.String(36), nullable=True), "categories"),
    ("employee_id", lambda: sa.Column("employee_id", sa.String(36), nullable=True), "employees"),
    ("counts_as_cost", lambda: sa.Column("counts_as_cost", sa.Boolean(), nullable=False, server_default="false"), None),
)


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("bank_book_entries", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("bank_book_entries", schema=schema)}
        for name, column, target in COLUMNS:
            if name not in existing:
                op.add_column("bank_book_entries", column(), schema=schema)
                if target:
                    op.create_foreign_key("fk_bank_book_" + name, "bank_book_entries", target, [name], ["id"],
                                          source_schema=schema, referent_schema=schema)
        BankRule.__table__.create(connection.execution_options(schema_translate_map={None: schema}), checkfirst=True)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if inspector.has_table("bank_rules", schema=schema):
            op.drop_table("bank_rules", schema=schema)
        if not inspector.has_table("bank_book_entries", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("bank_book_entries", schema=schema)}
        for name, _, _ in reversed(COLUMNS):
            if name in existing:
                op.drop_column("bank_book_entries", name, schema=schema)
