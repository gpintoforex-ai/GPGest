"""Pedidos de clientes com numerador sequencial (PC-0001, …); o número antigo fica em legacy_number."""

import importlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def orders(connection, schema):
    return connection.execute(
        sa.text(f'SELECT id, data FROM "{schema}".customer_orders ORDER BY created_at, id')
    ).all()


def save(connection, schema, identifier, data):
    connection.execute(
        sa.text(f'UPDATE "{schema}".customer_orders SET data = :data WHERE id = :id'),
        {"data": json.dumps(data, ensure_ascii=False, default=str), "id": identifier},
    )


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        if not sa.inspect(connection).has_table("customer_orders", schema=schema):
            continue
        rows = orders(connection, schema)
        # Repetível: pedidos já numerados mantêm o número; os restantes continuam a sequência.
        last = max((json.loads(d or "{}").get("sequence") or 0 for _, d in rows), default=0)
        for identifier, raw in rows:
            data = json.loads(raw or "{}")
            if data.get("sequence"):
                continue
            last += 1
            old = data.get("number") or "PC-" + identifier[:8].upper()
            data.update(sequence=last, number=f"PC-{last:04d}", legacy_number=old)
            save(connection, schema, identifier, data)


def downgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        if not sa.inspect(connection).has_table("customer_orders", schema=schema):
            continue
        for identifier, raw in orders(connection, schema):
            data = json.loads(raw or "{}")
            if data.get("legacy_number"):
                data["number"] = data.pop("legacy_number")
            data.pop("sequence", None)
            save(connection, schema, identifier, data)
