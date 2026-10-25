"""Tabela de prazos de pagamento (local e Wintouch) e prazo escolhido em cada entidade."""

import importlib
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from app.models import PaymentTerm

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            inspector = sa.inspect(connection)
            if not inspector.has_table("business_entities", schema=schema):
                continue
            PaymentTerm.__table__.create(
                connection.execution_options(schema_translate_map={None: schema}), checkfirst=True
            )
            connection.execution_options(schema_translate_map=None)
            existing = {c["name"] for c in inspector.get_columns("business_entities", schema=schema)}
            if "payment_term_id" not in existing:
                op.add_column(
                    "business_entities",
                    sa.Column("payment_term_id", sa.String(36), nullable=True),
                    schema=schema,
                )
                op.create_foreign_key(
                    "business_entities_payment_term_id_fkey",
                    "business_entities",
                    "payment_terms",
                    ["payment_term_id"],
                    ["id"],
                    source_schema=schema,
                    referent_schema=schema,
                )
            # Os dias já indicados nas entidades passam a ser prazos «N Dias» da tabela.
            days_in_use = connection.execute(
                sa.text(
                    f'SELECT DISTINCT payment_days FROM "{schema}".business_entities '
                    "WHERE payment_days IS NOT NULL AND payment_term_id IS NULL"
                )
            ).scalars()
            for days in days_in_use:
                code = f"{days}DIAS"
                term_id = connection.execute(
                    sa.text(f'SELECT id FROM "{schema}".payment_terms WHERE upper(code) = :code'),
                    {"code": code},
                ).scalar()
                if term_id is None:
                    term_id = str(uuid4())
                    connection.execute(
                        sa.text(
                            f'INSERT INTO "{schema}".payment_terms '
                            "(id, code, name, term_type, days, discount_basis_points, active, updated_at) "
                            "VALUES (:id, :code, :name, 'apos', :days, 0, true, now())"
                        ),
                        {"id": term_id, "code": code, "name": f"{days} Dias", "days": days},
                    )
                connection.execute(
                    sa.text(
                        f'UPDATE "{schema}".business_entities SET payment_term_id = :term '
                        "WHERE payment_days = :days AND payment_term_id IS NULL"
                    ),
                    {"term": term_id, "days": days},
                )
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            inspector = sa.inspect(connection)
            if not inspector.has_table("business_entities", schema=schema):
                continue
            existing = {c["name"] for c in inspector.get_columns("business_entities", schema=schema)}
            if "payment_term_id" in existing:
                op.drop_column("business_entities", "payment_term_id", schema=schema)
            PaymentTerm.__table__.drop(
                connection.execution_options(schema_translate_map={None: schema}), checkfirst=True
            )
    finally:
        connection.execution_options(schema_translate_map=None)
