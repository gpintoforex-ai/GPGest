"""Tipos de documento de compra (FC, FCD, NCC, NCCD) e ligação da compra ao tipo."""

import importlib
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

DEFAULTS = [
    ("FC", "Fatura de Compra", "compra", 1, "credito", None, False, True, 30),
    ("FCD", "Fatura de Compra a Dinheiro", "compra", 1, "dinheiro", "numerario", True, False, None),
    ("NCC", "Nota de Crédito de Compra", "nota_credito_compra", -1, "credito", None, False, True, 30),
    ("NCCD", "Nota de Crédito Compra Dinheiro", "nota_credito_compra", -1, "dinheiro", "numerario", True, False, None),
]


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("purchases", schema=schema):
            continue
        if not inspector.has_table("purchase_document_types", schema=schema):
            op.create_table(
                "purchase_document_types",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("code", sa.String(20), nullable=False, unique=True),
                sa.Column("description", sa.String(100), nullable=False),
                sa.Column("nature", sa.String(30), nullable=False),
                sa.Column("sign", sa.Integer(), nullable=False),
                sa.Column("payment_condition", sa.String(10), nullable=False),
                sa.Column("default_payment_method", sa.String(20), nullable=True),
                sa.Column("creates_cash_movement", sa.Boolean(), nullable=False, server_default=sa.false()),
                sa.Column("creates_debt", sa.Boolean(), nullable=False, server_default=sa.false()),
                sa.Column("default_due_days", sa.Integer(), nullable=True),
                sa.Column("series", sa.String(40), nullable=False, server_default=""),
                sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                sa.CheckConstraint("nature IN ('compra', 'nota_credito_compra')", name="purchase_doc_type_nature"),
                sa.CheckConstraint("sign IN (-1, 1)", name="purchase_doc_type_sign"),
                sa.CheckConstraint("payment_condition IN ('dinheiro', 'credito')", name="purchase_doc_type_condition"),
                schema=schema,
            )
            table = sa.table(
                "purchase_document_types",
                *(sa.column(name) for name in (
                    "id", "code", "description", "nature", "sign", "payment_condition", "default_payment_method",
                    "creates_cash_movement", "creates_debt", "default_due_days", "series", "active",
                )),
                schema=schema,
            )
            op.bulk_insert(table, [
                {"id": str(uuid4()), "code": code, "description": description, "nature": nature, "sign": sign,
                 "payment_condition": condition, "default_payment_method": method, "creates_cash_movement": cash,
                 "creates_debt": debt, "default_due_days": days, "series": "", "active": True}
                for code, description, nature, sign, condition, method, cash, debt, days in DEFAULTS
            ])
        columns = {c["name"] for c in sa.inspect(connection).get_columns("purchases", schema=schema)}
        if "document_type_id" not in columns:
            op.add_column("purchases", sa.Column("document_type_id", sa.String(36), nullable=True), schema=schema)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if inspector.has_table("purchases", schema=schema) and "document_type_id" in {
            c["name"] for c in inspector.get_columns("purchases", schema=schema)
        }:
            op.drop_column("purchases", "document_type_id", schema=schema)
        if inspector.has_table("purchase_document_types", schema=schema):
            op.drop_table("purchase_document_types", schema=schema)
