"""Quantidade faturada nas linhas dos pedidos de clientes (customer_order_lines.invoiced).

A saída de stock de uma venda passa a vir só da fatura Wintouch confirmada; a entrega fica um registo
logístico e a reserva mantém-se até à fatura. As entregas registadas antes desta regra já deram baixa de
stock (e consumiram a reserva), por isso contam como faturadas: `invoiced = delivered`."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None

CHECK = "ck_customer_order_lines_invoiced"


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("customer_order_lines", schema=schema):
            continue
        columns = {c["name"] for c in inspector.get_columns("customer_order_lines", schema=schema)}
        if "invoiced" in columns:
            continue
        op.add_column(
            "customer_order_lines",
            sa.Column("invoiced", sa.Numeric(24, 6), nullable=False, server_default=sa.text("0")),
            schema=schema,
        )
        op.execute(sa.text(f'UPDATE "{schema}".customer_order_lines SET invoiced = delivered'))
        op.create_check_constraint(
            CHECK, "customer_order_lines", "invoiced >= 0 AND cancelled + invoiced <= quantity", schema=schema
        )


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("customer_order_lines", schema=schema):
            continue
        columns = {c["name"] for c in inspector.get_columns("customer_order_lines", schema=schema)}
        if "invoiced" in columns:
            op.drop_constraint(CHECK, "customer_order_lines", schema=schema, type_="check")
            op.drop_column("customer_order_lines", "invoiced", schema=schema)
