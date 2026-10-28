"""Local customer and supplier records."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    # Tabela como era nesta revisão: as colunas seguintes (removed, category_id, morada,
    # prazo de pagamento, ...) são acrescentadas pelas migrações posteriores. Usar o modelo
    # atual aqui criaria chaves para tabelas que ainda não existem numa instalação nova.
    connection = op.get_bind()
    try:
        for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
            connection
        ):
            if sa.inspect(connection).has_table("business_entities", schema=schema):
                continue
            connection.execution_options(schema_translate_map={None: schema})
            op.create_table(
                "business_entities",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("wintouch_id", sa.String(36), unique=True, nullable=True),
                sa.Column("name", sa.String(300), nullable=False),
                sa.Column("tax_number", sa.String(40), nullable=False),
                sa.Column("email", sa.String(200), nullable=False),
                sa.Column("phone", sa.String(60), nullable=False),
                sa.Column("is_customer", sa.Boolean(), nullable=False),
                sa.Column("is_supplier", sa.Boolean(), nullable=False),
                sa.Column("active", sa.Boolean(), nullable=False),
            )
            connection.execution_options(schema_translate_map={None: "public"})
    finally:
        connection.execution_options(schema_translate_map={None: "public"})


def downgrade():
    connection = op.get_bind()
    for schema in importlib.import_module("migrations.versions.0006_cash_sheet").schemas(
        connection
    ):
        if sa.inspect(connection).has_table("business_entities", schema=schema):
            op.drop_table("business_entities", schema=schema)
