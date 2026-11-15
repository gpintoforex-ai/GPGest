"""Encomendas online: estados de pagamento e reserva, prazos por meio de pagamento, eventos de pagamento e
histórico (webshop_orders, webshop_connections.order_settings, webshop_payment_events, webshop_order_events).

As encomendas já existentes ficam «legacy» em pagamento e reserva: sem prazo, nunca expiram automaticamente
(não se sabe se foram pagas); as canceladas ficam com a reserva libertada."""

import importlib

import sqlalchemy as sa
from alembic import op
from app.models import WebshopOrderEvent, WebshopPaymentEvent

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None

ORDER_COLUMNS = (
    ("created_at", lambda: sa.Column("created_at", sa.DateTime(timezone=True), nullable=True)),
    ("total_cents", lambda: sa.Column("total_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0"))),
    ("payment_method", lambda: sa.Column("payment_method", sa.String(80), nullable=False, server_default="")),
    ("payment_kind", lambda: sa.Column("payment_kind", sa.String(20), nullable=False, server_default="")),
    ("payment_state", lambda: sa.Column("payment_state", sa.String(20), nullable=False, server_default="pending")),
    ("paid_cents", lambda: sa.Column("paid_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0"))),
    ("paid_at", lambda: sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True)),
    ("reservation_state", lambda: sa.Column("reservation_state", sa.String(20), nullable=False, server_default="temporary")),
    ("reservation_expires_at", lambda: sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True)),
    ("review", lambda: sa.Column("review", sa.String(500), nullable=False, server_default="")),
)
TABLES = (WebshopPaymentEvent, WebshopOrderEvent)


def schemas(connection):
    return importlib.import_module("migrations.versions.0006_cash_sheet").schemas(connection)


def upgrade():
    connection = op.get_bind()
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("webshop_orders", schema=schema):
            continue
        settings = {c["name"] for c in inspector.get_columns("webshop_connections", schema=schema)}
        if "order_settings" not in settings:
            op.add_column("webshop_connections", sa.Column("order_settings", sa.Text(), nullable=False,
                                                           server_default="{}"), schema=schema)
        existing = {c["name"] for c in inspector.get_columns("webshop_orders", schema=schema)}
        added = [name for name, _ in ORDER_COLUMNS if name not in existing]
        for name, column in ORDER_COLUMNS:
            if name in added:
                op.add_column("webshop_orders", column(), schema=schema)
        if "payment_state" in added:
            # Encomendas anteriores: sem prazo nem expiração automática; total a partir da encomenda guardada.
            op.execute(sa.text(f"""
                UPDATE "{schema}".webshop_orders w SET
                    created_at = w.updated_at,
                    payment_state = 'legacy',
                    reservation_state = CASE WHEN w.status IN ('cancelled', 'failed', 'refunded')
                                             THEN 'released' ELSE 'legacy' END,
                    total_cents = COALESCE((c.data::jsonb -> 'webshop' ->> 'total_cents')::bigint, 0)
                FROM "{schema}".customer_orders c WHERE c.id = w.order_id"""))
        if "reservation_expires_at" in added:
            op.create_index("ix_webshop_orders_reservation_expires_at", "webshop_orders",
                            ["reservation_expires_at"], schema=schema)
    try:
        for schema in schemas(connection):
            if not sa.inspect(connection).has_table("webshop_orders", schema=schema):
                continue
            bind = connection.execution_options(schema_translate_map={None: schema})
            for model in TABLES:
                model.__table__.create(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)


def downgrade():
    connection = op.get_bind()
    try:
        for schema in schemas(connection):
            bind = connection.execution_options(schema_translate_map={None: schema})
            for model in reversed(TABLES):
                model.__table__.drop(bind, checkfirst=True)
    finally:
        connection.execution_options(schema_translate_map=None)
    for schema in schemas(connection):
        inspector = sa.inspect(connection)
        if not inspector.has_table("webshop_orders", schema=schema):
            continue
        existing = {c["name"] for c in inspector.get_columns("webshop_orders", schema=schema)}
        if "reservation_expires_at" in existing:
            op.drop_index("ix_webshop_orders_reservation_expires_at", "webshop_orders", schema=schema)
        for name, _ in reversed(ORDER_COLUMNS):
            if name in existing:
                op.drop_column("webshop_orders", name, schema=schema)
        if "order_settings" in {c["name"] for c in inspector.get_columns("webshop_connections", schema=schema)}:
            op.drop_column("webshop_connections", "order_settings", schema=schema)
