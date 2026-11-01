"""Customer demand and supply allocation; purchasing and stock keep their existing ledgers."""

import hashlib
import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import inventory as stock
from . import procurement as buying
from .access import authorize, current, record, scope
from .db import db_session
from .email_delivery import SendInput, parts_for
from .entities import effective_flags, supplier_missing
from .models import (
    BusinessEntity,
    EmailDispatch,
    StockArticle,
    StockLot,
    Store,
    User,
    WintouchEntity,
    now,
)
from .models import (
    CustomerOrder as Order,
)
from .models import (
    CustomerOrderEvent as Event,
)
from .models import (
    CustomerOrderLine as Line,
)
from .models import (
    CustomerOrderReservation as Reservation,
)
from .models import (
    CustomerPurchaseSimulation as Simulation,
)
from .models import (
    CustomerSupplyLink as Supply,
)
from .models import (
    ProcurementDocument as Document,
)
from .models import (
    SupplierOrderTerms as TermsRow,
)
from .schemas import Strict

router = APIRouter(prefix="/api/customer-orders", tags=["Encomendas de clientes"])
ZERO = D(0)
WRITE = ["administrador", "gestor", "operador"]
STATES = {
    "registered": "Pedido registado",
    "waiting": "A aguardar encomenda",
    "simulated": "Incluído em simulação",
    "prepared": "Encomenda preparada",
    "sent": "Enviada ao fornecedor",
    "confirmed": "Confirmada pelo fornecedor",
    "partial": "Parcialmente recebida",
    "received": "Recebida",
    "notified": "Cliente notificado",
    "ready": "Pronta para entrega",
    "delivered": "Entregue",
    "cancelled": "Cancelada",
}


def fail(message, code=422):
    raise HTTPException(code, message)


def dump(value):
    return stock.dump(value)


class Change(Strict):
    request_key: str = Field(min_length=8, max_length=100)
    revision: int = Field(default=0, ge=0)


class RequestedLine(Strict):
    article_id: str | None = None
    supplier_id: str | None = None
    description: str = Field(min_length=1, max_length=500)
    brand: str = Field(default="", max_length=100)
    reference: str = Field(default="", max_length=100)
    ean: str = Field(default="", max_length=100)
    quantity: D = Field(gt=0, le=10**9, decimal_places=6)
    sale_price_cents: int | None = Field(default=None, ge=0, le=10**11)
    notes: str = Field(default="", max_length=2000)


class OrderInput(Change):
    store_id: str
    customer_id: str | None = None
    customer_name: str = Field(min_length=1, max_length=300)
    phone: str = Field(default="", max_length=60)
    email: str = Field(default="", max_length=254)
    preferred_channel: Literal["email", "whatsapp", "phone", "none"] = "none"
    contact_allowed: bool = False
    requested_at: str | None = Field(default=None, max_length=40)
    due_date: date | None = None
    priority: Literal["normal", "urgent"] = "normal"
    notes: str = Field(default="", max_length=5000)
    allow_duplicate: bool = False
    lines: list[RequestedLine] = Field(min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        if value:
            from .email_settings import email_address

            return email_address(value)
        return ""


class Tier(Strict):
    threshold_cents: int = Field(ge=0, le=10**12)
    discount: D = Field(ge=0, le=100, decimal_places=4)


class CommercialTerms(Strict):
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    minimum_cents: int = Field(default=0, ge=0, le=10**12)
    minimum_basis: Literal["gross", "net", "net_vat", "total"] = "net"
    discount: D = Field(default=ZERO, ge=0, le=100, decimal_places=4)
    tiers: list[Tier] = Field(default_factory=list, max_length=20)
    shipping_cents: int = Field(default=0, ge=0, le=10**10)
    free_shipping_cents: int | None = Field(default=None, ge=0, le=10**12)
    shipping_vat: D = Field(default=D(23), ge=0, le=100, decimal_places=3)
    vat: D = Field(default=D(23), ge=0, le=100, decimal_places=3)
    lead_days: int = Field(default=7, ge=0, le=3650)
    pending_days: int = Field(default=7, ge=1, le=3650)
    confirmation_days: int = Field(default=3, ge=1, le=3650)
    pickup_days: int = Field(default=7, ge=1, le=3650)
    # Vendas recentes consideradas na reposição automática do simulador (dias).
    sales_period_days: int = Field(default=90, ge=7, le=730)
    # Devolução de produtos a acabar a validade e aviso (meses antes da validade).
    accepts_expiry_returns: bool = False
    expiry_return_months: int = Field(default=3, ge=1, le=36)
    email: str = Field(default="", max_length=254)
    notes: str = Field(default="", max_length=3000)
    prices: dict[str, D] = Field(default_factory=dict, max_length=500)

    @field_validator("prices")
    @classmethod
    def prices_valid(cls, values):
        if any(
            not v.is_finite() or v < 0 or v > 10**9 or v.as_tuple().exponent < -6
            for v in values.values()
        ):
            raise ValueError("Preços de custo inválidos (até seis casas decimais).")
        return values

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        return OrderInput.email_valid(value)


class SaveTerms(Change):
    terms: CommercialTerms


class Allocation(Strict):
    line_id: str
    quantity: D = Field(gt=0, le=10**9, decimal_places=6)


class SimLine(Strict):
    article_id: str | None = None
    description: str = Field(min_length=1, max_length=500)
    quantity: D = Field(gt=0, le=10**9, decimal_places=6)
    unit_cost: D = Field(ge=0, le=10**9, decimal_places=6)
    discount: D | None = Field(default=None, ge=0, le=100, decimal_places=4)
    vat: D | None = Field(default=None, ge=0, le=100, decimal_places=3)
    allocations: list[Allocation] = Field(default_factory=list, max_length=200)


class SimInput(Change):
    store_id: str
    supplier_id: str
    terms: CommercialTerms | None = None
    notes: str = Field(default="", max_length=3000)
    lines: list[SimLine] = Field(min_length=1, max_length=200)


class QuantityChange(Change):
    quantity: D = Field(gt=0, le=10**9, decimal_places=6)
    warehouse_id: str | None = None
    reason: str = Field(default="", max_length=2000)


class OrderDetails(Change):
    customer_name: str = Field(min_length=1, max_length=300)
    phone: str = Field(default="", max_length=60)
    email: str = Field(default="", max_length=254)
    preferred_channel: Literal["email", "whatsapp", "phone", "none"]
    contact_allowed: bool
    due_date: date | None = None
    priority: Literal["normal", "urgent"] = "normal"
    notes: str = Field(default="", max_length=5000)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return OrderInput.email_valid(value)


class EditedLine(RequestedLine):
    id: str | None = None


class OrderEdit(Change):
    customer_id: str | None = None
    customer_name: str = Field(min_length=1, max_length=300)
    phone: str = Field(default="", max_length=60)
    email: str = Field(default="", max_length=254)
    preferred_channel: Literal["email", "whatsapp", "phone", "none"] = "none"
    contact_allowed: bool = False
    requested_at: str | None = Field(default=None, max_length=40)
    due_date: date | None = None
    priority: Literal["normal", "urgent"] = "normal"
    notes: str = Field(default="", max_length=5000)
    reason: str = Field(default="", max_length=2000)
    lines: list[EditedLine] = Field(default_factory=list, max_length=100)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return OrderInput.email_valid(value)


class Associate(Change):
    article_id: str
    supplier_id: str | None = None


class Prepare(Change):
    warehouse_id: str
    confirmed: Literal[True]
    allow_below_minimum: bool = False


class ConfirmSupplier(Change):
    quantities: list[D] = Field(min_length=1, max_length=200)
    note: str = Field(min_length=3, max_length=2000)


class Receipt(Change):
    receipt_id: str | None = None
    document: str = Field(default="", max_length=100)
    quantities: list[D] = Field(default_factory=list, max_length=200)
    lots: list[str] = Field(default_factory=list, max_length=200)
    expiries: list[date | None] = Field(default_factory=list, max_length=200)
    confirmed: Literal[True]


class Contact(Change):
    channel: Literal["email", "whatsapp", "phone"]
    message: str = Field(min_length=1, max_length=10000)
    subject: str = Field(default="O seu pedido está disponível", min_length=1, max_length=500)
    result: Literal["prepared", "confirmed", "failed"] = "prepared"
    prepared_event_id: str | None = None

    @field_validator("subject")
    @classmethod
    def valid_subject(cls, value):
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Indique um assunto sem quebras de linha.")
        return value.strip()


def get_order(db, user, identifier, write=False):
    row = db.get(Order, identifier)
    if not row:
        fail("Pedido não encontrado nesta empresa.", 404)
    authorize(user, db, row.store_id, WRITE if write else None)
    return row


def get_line(db, user, identifier):
    line = db.get(Line, identifier)
    if not line:
        fail("Produto do pedido não encontrado.", 404)
    return line, get_order(db, user, line.order_id, True)


def management(db, user, store_id=None):
    authorize(user, db, store_id, ["administrador", "gestor"])
    stock.require(user, stock.settings(db), "view_cost")


def get_sim(db, user, identifier):
    row = db.get(Simulation, identifier)
    if not row:
        fail("Simulação não encontrada nesta empresa.", 404)
    management(db, user, row.store_id)
    return row


def revision(row, data):
    if row.revision != data.revision:
        fail("O registo foi alterado. Atualize antes de repetir.", 409)


def replay(db, user, action, data, target=""):
    stock.lock(db)
    db.expire_all()  # Refresh objects read before waiting for the company-wide write lock.
    digest = hashlib.sha256(
        (action + target + dump(data.model_dump(mode="json"))).encode()
    ).hexdigest()
    prior = db.scalar(select(Event).where(Event.request_key == data.request_key))
    if prior:
        authorize(user, db, prior.store_id)
        if prior.action != action or prior.digest != digest:
            fail("Esta chave já foi usada para outra operação.", 409)
    return digest, prior


def event(
    db, user, action, data, digest, target, store, order_id=None, detail=None, dispatch_id=None
):
    row = Event(
        request_key=data.request_key,
        digest=digest,
        action=action,
        target_id=target,
        store_id=store,
        order_id=order_id,
        user_id=user.id,
        data=dump(detail or {}),
        dispatch_id=dispatch_id,
    )
    db.add(row)
    record(db, user, "pedido_cliente_" + action, target, {"event_id": row.id}, store)
    db.flush()
    return row


def entity_reference(db, identifier, kind, materialize=False):
    """A local reference to an existing ERP entity is never an ERP creation."""
    if not identifier:
        return None
    if identifier.startswith("local:"):
        identifier = identifier.removeprefix("local:")
    if not identifier.startswith("wt:"):
        entity = db.get(BusinessEntity, identifier)
    else:
        from .db import request_company

        company = request_company(db)
        imported = db.get(WintouchEntity, identifier.removeprefix("wt:"))
        if not company or not imported or imported.enterprise_id != company.api_enterprise:
            fail("Entidade ERP indisponível nesta empresa.", 404)
        entity = db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == imported.id))
        flags = effective_flags(imported, entity)
        if not flags["active"] or not flags["is_" + kind]:
            fail("Entidade ERP inativa ou de outro tipo.")
        if not entity and materialize:
            entity = BusinessEntity(
                wintouch_id=imported.id,
                name=imported.name,
                tax_number=imported.tax_number or "",
                phone=imported.phone or "",
                email=imported.email or "",
                is_customer=bool(imported.is_customer),
                is_supplier=bool(imported.is_supplier),
            )
            db.add(entity)
            db.flush()
        elif not entity:
            return imported
    if not entity or entity.removed or not entity.active or not getattr(entity, "is_" + kind):
        fail("Entidade inválida ou inativa nesta empresa.")
    return entity


def supplier(db, identifier):
    return entity_reference(db, identifier, "supplier", True) if identifier else None


def prefetched(db, kind, key):
    """Dados já carregados em bloco pela listagem (ver `prefetch`), ou `None` fora dela."""
    cache = db.info.get("customer_orders_cache")
    return cache[kind].get(key, []) if cache and key in cache["known"] else None


def prefetch(db, orders):
    """
    Carrega de uma vez as linhas, reservas, ligações a compras, contactos, artigos, lotes e emails das
    encomendas listadas. Sem isto a lista fazia ~7 consultas por encomenda, o que com a base na cloud
    (Neon) tornava lento abrir o ecrã e gravar (a gravação recarrega a lista).
    """
    ids = [o.id for o in orders]
    cache = {"known": set(), "lines": {}, "reserved": {}, "supplies": {}, "contacts": {}, "keep": []}
    db.info["customer_orders_cache"] = cache
    if not ids:
        return
    group = lambda kind, key, item: cache[kind].setdefault(key, []).append(item)  # noqa: E731
    lines = list(db.scalars(select(Line).where(Line.order_id.in_(ids)).order_by(Line.id)))
    for line in lines:
        group("lines", line.order_id, line)
    line_ids = [line.id for line in lines]
    reservations, supplies = [], []
    if line_ids:
        reservations = list(db.scalars(select(Reservation).where(Reservation.line_id.in_(line_ids), Reservation.quantity > 0).order_by(Reservation.id)))
        supplies = list(db.scalars(select(Supply).where(Supply.line_id.in_(line_ids)).order_by(Supply.id)))
    for hold in reservations:
        group("reserved", hold.line_id, hold)
    for link in supplies:
        group("supplies", link.line_id, link)
    contacts = list(db.scalars(select(Event).where(Event.order_id.in_(ids), Event.action == "contact").order_by(Event.created_at.desc())))
    for item in contacts:
        group("contacts", item.order_id, item)
    # Objetos lidos depois com db.get: ficam no mapa de identidade da sessão (referências mantidas em "keep").
    keep = cache["keep"]
    keep += lines + reservations + supplies + contacts
    simulation_ids = {link.simulation_id for link in supplies}
    simulations = list(db.scalars(select(Simulation).where(Simulation.id.in_(simulation_ids)))) if simulation_ids else []
    document_ids = {sim.procurement_id for sim in simulations if sim.procurement_id}
    documents = list(db.scalars(select(Document).where(Document.id.in_(document_ids)))) if document_ids else []
    article_ids = {line.article_id for line in lines if line.article_id}
    lot_ids = {hold.lot_id for hold in reservations if hold.lot_id}
    dispatch_ids = {item.dispatch_id for item in contacts if item.dispatch_id}
    for doc in documents:
        dispatch_ids.update(json.loads(doc.data).get("email_ids", []))
    keep += simulations + documents
    if article_ids:
        keep += list(db.scalars(select(StockArticle).where(StockArticle.id.in_(article_ids))))
    if lot_ids:
        keep += list(db.scalars(select(StockLot).where(StockLot.id.in_(lot_ids))))
    if dispatch_ids:
        keep += list(db.scalars(select(EmailDispatch).where(EmailDispatch.id.in_(dispatch_ids))))
    cache["known"].update(ids)
    cache["known"].update(line_ids)


def active_supplies(db, line_id):
    result = []
    links = prefetched(db, "supplies", line_id)
    if links is None:
        links = db.scalars(select(Supply).where(Supply.line_id == line_id).order_by(Supply.id))
    for link in links:
        sim = db.get(Simulation, link.simulation_id)
        doc = db.get(Document, sim.procurement_id) if sim.procurement_id else None
        if sim.status != "cancelled" and not (doc and doc.status == "cancelled"):
            result.append((link, sim, doc))
    return result


def reserved(db, line_id):
    cached = prefetched(db, "reserved", line_id)
    if cached is not None:
        return list(cached)
    return list(
        db.scalars(
            select(Reservation)
            .where(Reservation.line_id == line_id, Reservation.quantity > 0)
            .order_by(Reservation.id)
        )
    )


def usable_reservations(db, line):
    article = db.get(StockArticle, line.article_id) if line.article_id else None
    if not article or not article.active:
        return []
    result = []
    for hold in reserved(db, line.id):
        lot = db.get(StockLot, hold.lot_id) if hold.lot_id else None
        if lot and lot.expiry and lot.expiry < date.today().isoformat():
            continue
        result.append(hold)
    return result


def contact_status(db, order_id):
    events = prefetched(db, "contacts", order_id)
    if events is None:
        events = list(
            db.scalars(
                select(Event)
                .where(Event.order_id == order_id, Event.action == "contact")
                .order_by(Event.created_at.desc())
            )
        )
    covered = {}
    for item in events:
        data = json.loads(item.data)
        mail = db.get(EmailDispatch, item.dispatch_id) if item.dispatch_id else None
        if data.get("result") == "confirmed" or mail and mail.status in ("accepted", "received"):
            for identifier, quantity in data.get("availability", {}).items():
                covered[identifier] = max(covered.get(identifier, ZERO), D(quantity))
    ready = []
    lines = prefetched(db, "lines", order_id)
    for line in lines if lines is not None else db.scalars(select(Line).where(Line.order_id == order_id)):
        held = sum((r.quantity for r in usable_reservations(db, line)), ZERO)
        if held:
            ready.append(covered.get(line.id, ZERO) >= held + line.delivered)
    return bool(ready) and all(ready)


def quantities(db, line):
    links = active_supplies(db, line.id)
    held = sum((r.quantity for r in reserved(db, line.id)), ZERO)
    available = sum((r.quantity for r in usable_reservations(db, line)), ZERO)
    pending = sum(
        (
            max(ZERO, line_item.quantity - line_item.released - line_item.received)
            for line_item, _, _ in links
        ),
        ZERO,
    )
    prepared = sum(
        (line_item.quantity - line_item.released for line_item, s, d in links if d), ZERO
    )
    simulated = sum(
        (
            max(ZERO, line_item.quantity - line_item.released - line_item.received)
            for line_item, s, d in links
            if not d
        ),
        ZERO,
    )
    received = sum((line_item.received for line_item, _, _ in links), ZERO)
    confirmed = sum(
        (max(ZERO, line_item.confirmed - line_item.released) for line_item, _, _ in links), ZERO
    )
    sent = ZERO
    for link, sim, doc in links:
        if doc and any(
            (
                db.get(EmailDispatch, i)
                and db.get(EmailDispatch, i).status in ("accepted", "received")
            )
            for i in json.loads(doc.data).get("email_ids", [])
        ):
            sent += link.quantity - link.released
    return {
        "requested": line.quantity,
        "available": available,
        "reserved": held,
        "to_order": max(ZERO, line.quantity - line.cancelled - line.delivered - held - pending),
        "simulated": simulated,
        "ordered": prepared,
        "sent": sent,
        "confirmed": confirmed,
        "received": received,
        "delivered": line.delivered,
        "cancelled": line.cancelled,
    }


def line_view(db, line, notified=False):
    q = quantities(db, line)
    state = "registered"
    if line.cancelled == line.quantity:
        state = "cancelled"
    elif line.delivered + line.cancelled >= line.quantity:
        state = "delivered"
    elif notified and q["reserved"] > 0:
        state = "notified"
    elif q["received"] >= line.quantity - line.cancelled:
        state = "received"
    elif q["received"] > 0:
        state = "partial"
    elif q["reserved"] > 0:
        state = "ready"
    elif q["confirmed"] > 0:
        state = "confirmed"
    elif q["sent"] > 0:
        state = "sent"
    elif q["ordered"] > 0:
        state = "prepared"
    elif q["simulated"] > 0:
        state = "simulated"
    elif line.supplier_id:
        state = "waiting"
    article = db.get(StockArticle, line.article_id) if line.article_id else None
    return {
        **json.loads(line.data),
        "id": line.id,
        "article_id": line.article_id,
        "article_code": article.code if article else "",
        "supplier_id": line.supplier_id,
        "quantities": {k: str(v) for k, v in q.items()},
        "state": state,
        "state_label": STATES[state],
        "provisional": not bool(article),
        "reservations": [
            {
                "id": r.id,
                "warehouse_id": r.warehouse_id,
                "lot_id": r.lot_id,
                "quantity": str(r.quantity),
            }
            for r in reserved(db, line.id)
        ],
    }


def order_label(sequence):
    return f"PC-{sequence:04d}"


def next_order_sequence(db):
    """Próximo número sequencial de pedido da empresa.

    Chamado ao criar, sob o bloqueio de escrita da empresa (`replay`): dois pedidos em simultâneo
    nunca recebem o mesmo número.
    """
    return max((json.loads(data).get("sequence") or 0 for data in db.scalars(select(Order.data))), default=0) + 1


def order_number(row):
    return json.loads(row.data).get("number") or "PC-" + row.id[:8].upper()


def order_view(db, row):
    notified = contact_status(db, row.id)
    rows = prefetched(db, "lines", row.id)
    if rows is None:
        rows = db.scalars(select(Line).where(Line.order_id == row.id).order_by(Line.id))
    lines = [line_view(db, line_item, notified) for line_item in rows]
    data = json.loads(row.data)
    return {
        **data,
        "id": row.id,
        "number": order_number(row),
        "store_id": row.store_id,
        "customer_id": row.customer_id,
        "created_at": row.created_at,
        "revision": row.revision,
        "notified": notified,
        "lines": lines,
        "ready": any(D(line_item["quantities"]["available"]) > 0 for line_item in lines),
        "fully_ready": any(line_item["state"] not in ("delivered", "cancelled") for line_item in lines) and all(
            line_item["state"] in ("delivered", "cancelled") or D(line_item["quantities"]["available"]) >=
            D(line_item["quantities"]["requested"]) - D(line_item["quantities"]["delivered"]) - D(line_item["quantities"]["cancelled"])
            for line_item in lines),
        "closed": all(line_item["state"] in ("delivered", "cancelled") for line_item in lines),
        "late": bool(
            data.get("due_date")
            and data["due_date"] < date.today().isoformat()
            and any(line_item["state"] not in ("delivered", "cancelled") for line_item in lines)
        ),
    }


def terms(db, supplier_id):
    row = db.get(TermsRow, supplier_id)
    return CommercialTerms(**json.loads(row.data)) if row else CommercialTerms()


def calculate(lines, config):
    gross = sum(buying.money(line_item.quantity * line_item.unit_cost) for line_item in lines)
    before_tier = sum(
        buying.money(
            line_item.quantity
            * line_item.unit_cost
            * (
                1
                - (line_item.discount if line_item.discount is not None else config.discount) / 100
            )
        )
        for line_item in lines
    )
    tier = max((t.discount for t in config.tiers if t.threshold_cents <= before_tier), default=ZERO)
    result = []
    for line in lines:
        discount = line.discount if line.discount is not None else config.discount
        rate = line.vat if line.vat is not None else config.vat
        effective = (100 - (100 - discount) * (100 - tier) / 100).quantize(
            D(".0001"), rounding="ROUND_HALF_UP"
        )
        unrounded = line.quantity * line.unit_cost * (1 - effective / 100)
        base = buying.money(unrounded)
        tax = buying.money(unrounded * rate / 100)
        result.append(
            {"base_cents": base, "tax_cents": tax, "discount": str(effective), "vat": str(rate)}
        )
    net = sum(line_item["base_cents"] for line_item in result)
    vat = sum(line_item["tax_cents"] for line_item in result)
    shipping = (
        0
        if config.free_shipping_cents is not None and net >= config.free_shipping_cents
        else config.shipping_cents
    )
    shipping_tax = int(
        (D(shipping) * config.shipping_vat / 100).quantize(D(1), rounding="ROUND_HALF_UP")
    )
    total = net + vat + shipping + shipping_tax
    eligible = {"gross": gross, "net": net, "net_vat": net + vat, "total": total}[
        config.minimum_basis
    ]
    return {
        "gross_cents": gross,
        "discount_cents": gross - net,
        "net_cents": net,
        "vat_cents": vat,
        "shipping_cents": shipping,
        "shipping_vat_cents": shipping_tax,
        "total_cents": total,
        "eligible_cents": eligible,
        "missing_cents": max(0, config.minimum_cents - eligible),
        "free_shipping_cents": config.free_shipping_cents,
        "shipping_missing_cents": max(0, config.free_shipping_cents - net) if config.free_shipping_cents is not None else None,
        "free_shipping_reached": config.free_shipping_cents is not None and net >= config.free_shipping_cents,
        "currency": config.currency,
        "tier_discount": str(tier),
        "lines": result,
    }


def sim_view(db, row):
    data = json.loads(row.data)
    doc = db.get(Document, row.procurement_id) if row.procurement_id else None
    result = {
        **data,
        "id": row.id,
        "store_id": row.store_id,
        "supplier_id": row.supplier_id,
        "supplier_name": db.get(BusinessEntity, row.supplier_id).name,
        "revision": row.revision,
        "status": row.status,
        "created_at": row.created_at,
        "procurement_id": row.procurement_id,
        "procurement": buying.document_view(db, doc) if doc else None,
    }
    return result


def free_balances(db, article_id, warehouse_id):
    available = stock.balances(stock.entries(db, article_id, warehouse_id))
    holds = db.scalars(
        select(Reservation)
        .join(Line, Line.id == Reservation.line_id)
        .where(Line.article_id == article_id, Reservation.warehouse_id == warehouse_id)
    )
    for hold in holds:
        available[(warehouse_id, hold.lot_id)] -= hold.quantity
    return available


def reserve(db, user, line, warehouse_id, quantity, *, lot_id=None):
    article = stock.article(db, line.article_id, True, stock=True)
    if buying.policy(db).get("authority", {}).get("stock") == "erp":
        fail(
            "O stock é controlado pelo ERP e o conector não disponibiliza reservas/receções por artigo.",
            409,
        )
    if quantity != quantity.quantize(D(1).scaleb(-json.loads(article.data).get("precision", 0))):
        fail("A quantidade não respeita a precisão do artigo.")
    wh = stock.warehouse(db, user, warehouse_id, authorize)
    order = db.get(Order, line.order_id)
    if wh.store_id != order.store_id:
        fail("O armazém pertence a outra loja.")
    if not article.active:
        fail("O artigo está inativo.")
    available = free_balances(db, article.id, warehouse_id)
    candidates = []
    for (_, batch), q in available.items():
        lot = db.get(StockLot, batch) if batch else None
        if q <= 0 or lot and lot.expiry and lot.expiry < date.today().isoformat():
            continue
        if lot_id is not None and lot_id != batch:
            continue
        candidates.append((lot.expiry if lot and lot.expiry else "9999-12-31", batch, q))
    remaining = quantity
    for _, batch, q in sorted(candidates, key=lambda x: (x[0], x[1] or "")):
        used = min(q, remaining)
        if used:
            db.add(
                Reservation(line_id=line.id, warehouse_id=warehouse_id, lot_id=batch, quantity=used)
            )
            remaining -= used
        if not remaining:
            break
    if remaining:
        fail("Stock livre insuficiente ou lotes expirados. Não foi criada a reserva.", 409)
    db.flush()


@router.get("")
def listing(store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        stores = [store_id]
    rows = list(
        db.scalars(
            select(Order).where(Order.store_id.in_(stores)).order_by(Order.created_at.desc())
        )
    )
    prefetch(db, rows)
    try:
        orders = [order_view(db, row) for row in rows]
    finally:
        db.info.pop("customer_orders_cache", None)
    simulations = list(
        db.scalars(
            select(Simulation)
            .where(Simulation.store_id.in_(stores))
            .order_by(Simulation.created_at.desc())
        )
    )
    can_buy = (
        user.role in ("administrador", "gestor")
        and stock.permissions(user, stock.settings(db))["view_cost"]
    )
    return {
        "orders": orders,
        "simulations": [sim_view(db, r) for r in simulations] if can_buy else [],
        "can_write": user.role in WRITE,
        "can_buy": can_buy,
        "states": STATES,
        "integration_note": "Receções e entregas usam stock local. O conector ERP atual não suporta receções detalhadas nem escrita de stock; não são simuladas sincronizações.",
    }


@router.get("/catalog")
def catalog(user=Depends(current), db: Session = Depends(db_session)):
    from .entities import list_entities

    # Fichas locais carregadas de uma vez (uma consulta por entidade tornava o ecrã lento com a base
    # na cloud: centenas de clientes do ERP = centenas de idas à base de dados).
    items = [item for item in list_entities(user, db) if item["active"]]
    local_ids = [i["id"].removeprefix("local:") for i in items if i["id"].startswith("local:")]
    erp_ids = [i["id"].removeprefix("wt:") for i in items if not i["id"].startswith("local:")]
    by_id = {e.id: e for e in db.scalars(select(BusinessEntity).where(BusinessEntity.id.in_(local_ids)))} if local_ids else {}
    by_erp = (
        {e.wintouch_id: e for e in db.scalars(select(BusinessEntity).where(BusinessEntity.wintouch_id.in_(erp_ids)))}
        if erp_ids
        else {}
    )
    entities = []
    for item in items:
        key = item["id"]
        local = by_id.get(key.removeprefix("local:")) if key.startswith("local:") else by_erp.get(key.removeprefix("wt:"))
        # `record_id` é o identificador da ficha nas Tabelas (wt:… para entidades do ERP, mesmo
        # quando já têm referência local), para abrir a edição a partir das encomendas.
        entities.append({**item, "id": local.id if local else key, "record_id": key})
    can_buy = (
        user.role in ("administrador", "gestor")
        and stock.permissions(user, stock.settings(db))["view_cost"]
    )
    from .supply_terms import article_terms

    bonuses = {}
    for (article_id, supplier_id), agreement in article_terms(db).items():
        if agreement.bonus_buy_quantity and agreement.bonus_free_quantity:
            bonuses.setdefault(supplier_id, {})[article_id] = {
                "buy": str(agreement.bonus_buy_quantity),
                "free": str(agreement.bonus_free_quantity),
            }
    return {
        "customers": [
            {"id": e["id"], "name": e["name"], "phone": e["phone"], "email": e["email"]}
            for e in entities
            if e["is_customer"]
        ],
        "suppliers": [
            {
                "id": e["id"],
                "name": e["name"],
                "tax_number": e.get("tax_number") or "",
                "record_id": e["record_id"],
                "missing": supplier_missing(e),
                "bonuses": bonuses.get(e["id"], {}),
                "email": e["email"] if can_buy else "",
                "terms": terms(db, e["id"]).model_dump(mode="json") if can_buy else None,
                "revision": db.get(TermsRow, e["id"]).revision
                if can_buy and db.get(TermsRow, e["id"])
                else 0,
            }
            for e in entities
            if e["is_supplier"]
        ],
    }


@router.post("")
def create(data: OrderInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, data.store_id, WRITE)
    if not db.get(Store, data.store_id):
        fail("Loja não encontrada.")
    digest, prior = replay(db, user, "create", data)
    if prior:
        return order_view(db, get_order(db, user, prior.target_id))
    if not data.customer_name.strip():
        fail("Indique o nome do cliente.")
    customer = (
        entity_reference(db, data.customer_id, "customer", True) if data.customer_id else None
    )
    raw = data.model_dump(mode="json")
    raw.pop("lines")
    raw.pop("request_key")
    raw.pop("revision")
    raw.pop("allow_duplicate")
    raw["requested_at"] = requested_time(data.requested_at)
    fingerprint = order_fingerprint(data.model_dump(mode="json"))
    if not data.allow_duplicate:
        for existing in db.scalars(select(Order).where(Order.store_id == data.store_id)):
            if (
                json.loads(existing.data).get("fingerprint") == fingerprint
                and not order_view(db, existing)["closed"]
            ):
                fail(
                    "Já existe um pedido igual em aberto. Consulte-o ou confirme expressamente a duplicação.",
                    409,
                )
    raw["fingerprint"] = fingerprint
    row = Order(
        store_id=data.store_id,
        customer_id=customer.id if customer else None,
        user_id=user.id,
        data=dump(raw),
    )
    db.add(row)
    db.flush()
    raw["sequence"] = next_order_sequence(db)
    raw["number"] = order_label(raw["sequence"])
    row.data = dump(raw)
    for item in data.lines:
        preferred_supplier = checked_line(db, item)
        db.add(
            Line(
                order_id=row.id,
                article_id=item.article_id,
                supplier_id=preferred_supplier.id if preferred_supplier else None,
                quantity=item.quantity,
                data=dump(item.model_dump(mode="json")),
            )
        )
    event(db, user, "create", data, digest, row.id, row.store_id, row.id)
    db.commit()
    return order_view(db, row)


def requested_time(value):
    if not value:
        return now().isoformat()
    from datetime import datetime

    try:
        requested = datetime.fromisoformat(value)
    except ValueError:
        fail("Data/hora do pedido inválida.")
    if requested.tzinfo is None or requested > now() + timedelta(minutes=5):
        fail("A data/hora deve incluir fuso horário e não pode ser futura.")
    return value


def order_fingerprint(values):
    """Identifica pedidos iguais em aberto (mesmo cliente, produtos e quantidades)."""
    ignored = ("request_key", "revision", "allow_duplicate", "requested_at", "reason")
    # Mesma ordem de campos do registo, para que um pedido editado continue comparável.
    ordered = {k: values[k] for k in OrderInput.model_fields if k in values and k not in ignored}
    return hashlib.sha256(dump(ordered).encode()).hexdigest()


def checked_line(db, item, *, article_changed=True, quantity_changed=True, ean_changed=True):
    """Valida um produto pedido e devolve o fornecedor preferencial (ou `None`)."""
    if not item.description.strip():
        fail("Indique a descrição de cada produto.")
    preferred_supplier = supplier(db, item.supplier_id)
    if item.article_id:
        article = stock.article(db, item.article_id)
        meta = json.loads(article.data)
        if article_changed and not article.active:
            fail("Artigo inativo.")
        if (article_changed or quantity_changed) and item.quantity != item.quantity.quantize(
            D(1).scaleb(-meta.get("precision", 0))
        ):
            fail("Quantidade incompatível com a precisão do artigo.")
    elif item.ean and ean_changed:
        for article in db.scalars(select(StockArticle)):
            meta = json.loads(article.data)
            if item.ean in meta.get("barcodes", []) or item.ean == article.code:
                fail(
                    "Este EAN/referência já corresponde a um artigo. Selecione-o no catálogo.",
                    409,
                )
    return preferred_supplier


@router.put("/{identifier}")
def edit_order(
    identifier: str, data: OrderEdit, user=Depends(current), db: Session = Depends(db_session)
):
    """Corrige o pedido como na janela de registo, sem tocar no que já está em curso.

    Produtos reservados, em compra ou entregues mantêm o artigo e não descem abaixo do que já
    está comprometido. Retirar um produto cancela a quantidade em falta (fica no histórico).
    """
    row = get_order(db, user, identifier, True)
    digest, prior = replay(db, user, "edit", data, identifier)
    if prior:
        return order_view(db, row)
    revision(row, data)
    if not data.customer_name.strip():
        fail("Indique o nome do cliente.")
    customer = (
        entity_reference(db, data.customer_id, "customer", True) if data.customer_id else None
    )
    existing = {line.id: line for line in db.scalars(select(Line).where(Line.order_id == row.id))}
    seen, added, changed, removed = set(), 0, 0, 0
    for item in data.lines:
        values = item.model_dump(mode="json", exclude={"id"})
        if not item.id:
            preferred_supplier = checked_line(db, item)
            db.add(
                Line(
                    order_id=row.id,
                    article_id=item.article_id,
                    supplier_id=preferred_supplier.id if preferred_supplier else None,
                    quantity=item.quantity,
                    data=dump(values),
                )
            )
            added += 1
            continue
        line = existing.get(item.id)
        if not line or item.id in seen:
            fail("Produto do pedido não encontrado.", 404)
        seen.add(item.id)
        before = json.loads(line.data)
        if line.delivered + line.cancelled >= line.quantity:
            if (
                item.article_id != line.article_id
                or item.quantity != line.quantity
                or item.description != before.get("description")
            ):
                fail(f"«{before.get('description')}» já está entregue ou cancelado.", 409)
        committed = line.quantity - quantities(db, line)["to_order"]
        if item.article_id != line.article_id and committed > 0:
            fail(
                f"«{before.get('description')}» já está reservado, em compra, entregue ou "
                "cancelado: o artigo não pode mudar.",
                409,
            )
        if item.quantity < committed:
            fail(
                f"A quantidade de «{before.get('description')}» não pode ser inferior a "
                f"{committed.normalize():f} (já reservada, em compra, entregue ou cancelada).",
                409,
            )
        preferred_supplier = checked_line(
            db,
            item,
            article_changed=item.article_id != line.article_id,
            quantity_changed=item.quantity != line.quantity,
            ean_changed=item.ean != before.get("ean", ""),
        )
        new_supplier = preferred_supplier.id if preferred_supplier else None
        if (
            item.article_id != line.article_id
            or item.quantity != line.quantity
            or new_supplier != line.supplier_id
            or values != {k: before.get(k) for k in values}
        ):
            changed += 1
        line.article_id = item.article_id
        line.supplier_id = new_supplier
        line.quantity = item.quantity
        line.data = dump({**before, **values})
    for line in existing.values():
        if line.id in seen or line.delivered + line.cancelled >= line.quantity:
            continue
        free = quantities(db, line)["to_order"]
        if free < line.quantity - line.cancelled - line.delivered:
            fail(
                f"«{json.loads(line.data).get('description')}» tem reservas ou compras em curso. "
                "Use «Cancelar quantidade» no detalhe do pedido.",
                409,
            )
        if len(data.reason.strip()) < 3:
            fail("Indique o motivo para retirar produtos do pedido.")
        line.cancelled += free
        removed += 1
    original = json.loads(row.data)
    header = data.model_dump(mode="json", exclude={"lines", "request_key", "revision", "reason"})
    header["requested_at"] = (
        requested_time(data.requested_at)
        if data.requested_at and data.requested_at != original.get("requested_at")
        else original.get("requested_at")
    )
    original.update(header)
    original["fingerprint"] = order_fingerprint(
        {
            **header,
            "store_id": row.store_id,
            "lines": [
                {k: v for k, v in item.model_dump(mode="json").items() if k != "id"}
                for item in data.lines
            ],
        }
    )
    row.customer_id = customer.id if customer else None
    row.data = dump(original)
    row.revision += 1
    event(
        db,
        user,
        "edit",
        data,
        digest,
        row.id,
        row.store_id,
        row.id,
        {"reason": data.reason, "added": added, "changed": changed, "removed": removed},
    )
    db.commit()
    return order_view(db, row)


@router.put("/{identifier}/details")
def edit_details(
    identifier: str, data: OrderDetails, user=Depends(current), db: Session = Depends(db_session)
):
    row = get_order(db, user, identifier, True)
    digest, prior = replay(db, user, "details", data, identifier)
    if prior:
        return order_view(db, row)
    revision(row, data)
    if not data.customer_name.strip():
        fail("Indique o nome do cliente.")
    original = json.loads(row.data)
    original.update(
        {
            k: v
            for k, v in data.model_dump(mode="json").items()
            if k not in ("request_key", "revision")
        }
    )
    row.data = dump(original)
    row.revision += 1
    event(db, user, "details", data, digest, row.id, row.store_id, row.id)
    db.commit()
    return order_view(db, row)


@router.post("/lines/{identifier}/associate")
def associate(
    identifier: str, data: Associate, user=Depends(current), db: Session = Depends(db_session)
):
    line, row = get_line(db, user, identifier)
    digest, prior = replay(db, user, "associate", data, identifier)
    if prior:
        return order_view(db, row)
    revision(row, data)
    if reserved(db, line.id) or active_supplies(db, line.id) or line.delivered:
        fail("Associe o artigo antes de reservar ou incluir numa simulação.", 409)
    article = stock.article(db, data.article_id)
    precision = json.loads(article.data).get("precision", 0)
    if not article.active or line.quantity != line.quantity.quantize(D(1).scaleb(-precision)):
        fail("Artigo inativo ou precisão incompatível.")
    preferred_supplier = supplier(db, data.supplier_id)
    line.article_id = article.id
    line.supplier_id = preferred_supplier.id if preferred_supplier else line.supplier_id
    row.revision += 1
    event(
        db,
        user,
        "associate",
        data,
        digest,
        line.id,
        row.store_id,
        row.id,
        {"article_id": article.id},
    )
    db.commit()
    return order_view(db, row)


@router.post("/lines/{identifier}/{action}")
def line_action(
    identifier: str,
    action: Literal["reserve", "deliver", "cancel", "adjust", "release"],
    data: QuantityChange,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    line, row = get_line(db, user, identifier)
    digest, prior = replay(db, user, action, data, identifier)
    if prior:
        return order_view(db, row)
    revision(row, data)
    q = quantities(db, line)
    if action == "reserve":
        if data.quantity > q["to_order"]:
            fail("A quantidade já está reservada ou atribuída a uma compra.", 409)
        reserve(db, user, line, data.warehouse_id, data.quantity)
    elif action == "cancel":
        if len(data.reason.strip()) < 3:
            fail("Indique o motivo do cancelamento.")
        if data.quantity > line.quantity - line.cancelled - line.delivered:
            fail("Não pode cancelar produtos já entregues.", 409)
        remaining = data.quantity
        # Cancel unassigned demand first, then unreceived supply, then physical reservations.
        remaining -= min(remaining, q["to_order"])
        for link, _, _ in active_supplies(db, line.id):
            released = min(remaining, max(ZERO, link.quantity - link.released - link.received))
            link.released += released
            remaining -= released
        for hold in reserved(db, line.id):
            released = min(remaining, hold.quantity)
            hold.quantity -= released
            remaining -= released
        if remaining:
            fail("A distribuição mudou. Atualize o pedido.", 409)
        line.cancelled += data.quantity
    elif action == "release":
        if len(data.reason.strip()) < 3:
            fail("Justifique a libertação da reserva.")
        if data.quantity > q["reserved"]:
            fail("A quantidade excede as reservas existentes.", 409)
        remaining = data.quantity
        for hold in reserved(db, line.id):
            amount = min(hold.quantity, remaining)
            hold.quantity -= amount
            remaining -= amount
    elif action == "deliver":
        if buying.policy(db).get("authority", {}).get("stock") == "erp":
            fail("O stock é controlado pelo ERP. O conector não suporta esta saída.", 409)
        if data.quantity > q["available"]:
            fail("Só pode entregar produtos reservados ativos e com validade adequada.", 409)
        if len(data.reason.strip()) < 3:
            fail("Indique a referência/observação da entrega.")
        remaining = data.quantity
        for hold in usable_reservations(db, line):
            amount = min(hold.quantity, remaining)
            if not amount:
                continue
            hold.quantity -= amount
            db.flush()
            price = json.loads(line.data).get("sale_price_cents")
            stock.post(
                db,
                user,
                stock.MovementInput(
                    article_id=line.article_id,
                    warehouse_id=hold.warehouse_id,
                    lot_id=hold.lot_id,
                    kind="sale",
                    quantity=amount,
                    net_sales=amount * D(price or 0) / 100,
                    reason=data.reason,
                    document=order_number(row),
                    request_key=data.request_key + ":" + hold.id,
                ),
                authorize,
                source="customer_order",
                bypass=True,
            )
            remaining -= amount
            if not remaining:
                break
        line.delivered += data.quantity
    else:
        if len(data.reason.strip()) < 3:
            fail("Justifique a alteração da quantidade pedida.")
        committed = line.quantity - q["to_order"]
        if data.quantity < committed:
            fail(
                "A quantidade já está entregue, cancelada, reservada ou atribuída. Cancele/liberte a atribuição antes de a reduzir.",
                409,
            )
        if line.article_id:
            precision = json.loads(stock.article(db, line.article_id).data).get("precision", 0)
            if data.quantity != data.quantity.quantize(D(1).scaleb(-precision)):
                fail("Quantidade incompatível com a precisão do artigo.")
        line.quantity = data.quantity
    row.revision += 1
    event(
        db,
        user,
        action,
        data,
        digest,
        line.id,
        row.store_id,
        row.id,
        {"quantity": str(data.quantity), "reason": data.reason,
         **({"due_date_at_delivery": json.loads(row.data).get("due_date")} if action == "deliver" else {})},
    )
    db.commit()
    return order_view(db, row)


@router.post("/suppliers/{identifier}/terms")
def save_terms(
    identifier: str, data: SaveTerms, user=Depends(current), db: Session = Depends(db_session)
):
    management(db, user)
    digest, prior = replay(db, user, "terms", data, identifier)
    if prior:
        return {"ok": True}
    identifier = supplier(db, identifier).id
    row = db.get(TermsRow, identifier)
    if (row.revision if row else 0) != data.revision:
        fail("As condições foram alteradas. Atualize.", 409)
    for article_id in data.terms.prices:
        stock.article(db, article_id)
    if not row:
        row = TermsRow(supplier_id=identifier)
        db.add(row)
    row.data = dump(data.terms.model_dump(mode="json"))
    row.revision = (row.revision or 0) + 1
    # Company-wide conditions can be edited by management, but events retain an accessible store.
    stores = scope(user, db)
    if not stores:
        fail("Sem lojas autorizadas.", 403)
    event(db, user, "terms", data, digest, identifier, stores[0])
    db.commit()
    return {"ok": True}


def validate_sim(db, user, data, editing=None):
    management(db, user, data.store_id)
    data.supplier_id = supplier(db, data.supplier_id).id
    seen = set()
    for item in data.lines:
        article = stock.article(db, item.article_id) if item.article_id else None
        if article and not article.active:
            fail("Artigo inativo.")
        if sum((a.quantity for a in item.allocations), ZERO) > item.quantity:
            fail("As atribuições excedem a quantidade de compra.")
        for alloc in item.allocations:
            if alloc.line_id in seen:
                fail("Cada linha de cliente só pode aparecer uma vez na simulação.")
            seen.add(alloc.line_id)
            line, row = get_line(db, user, alloc.line_id)
            if (
                row.store_id != data.store_id
                or line.supplier_id != data.supplier_id
                or line.article_id != item.article_id
            ):
                fail("Atribuições devem ter o mesmo artigo, fornecedor e loja.")
            extra = sum(
                (
                    line_item.quantity - line_item.released - line_item.received
                    for line_item, s, _ in active_supplies(db, line.id)
                    if s.id == editing
                ),
                ZERO,
            )
            if alloc.quantity > quantities(db, line)["to_order"] + extra:
                fail("Quantidade já atribuída, reservada ou satisfeita noutro pedido.", 409)
    return data.terms or terms(db, data.supplier_id)


@router.post("/simulations/preview")
def preview_sim(data: SimInput, user=Depends(current), db: Session = Depends(db_session)):
    management(db, user, data.store_id)
    entity = entity_reference(db, data.supplier_id, "supplier")
    return calculate(data.lines, data.terms or terms(db, entity.id))


@router.post("/simulations")
def save_sim(data: SimInput, user=Depends(current), db: Session = Depends(db_session)):
    management(db, user, data.store_id)
    digest, prior = replay(db, user, "simulation", data)
    if prior:
        return sim_view(db, get_sim(db, user, prior.target_id))
    config = validate_sim(db, user, data)
    row = Simulation(store_id=data.store_id, supplier_id=data.supplier_id, user_id=user.id)
    row.data = dump(
        {
            "lines": [line_item.model_dump(mode="json") for line_item in data.lines],
            "terms": config.model_dump(mode="json"),
            "totals": calculate(data.lines, config),
            "notes": data.notes,
        }
    )
    db.add(row)
    db.flush()
    for index, item in enumerate(data.lines):
        for alloc in item.allocations:
            db.add(
                Supply(
                    simulation_id=row.id,
                    simulation_line=index,
                    line_id=alloc.line_id,
                    quantity=alloc.quantity,
                )
            )
    event(db, user, "simulation", data, digest, row.id, row.store_id)
    db.commit()
    return sim_view(db, row)


@router.put("/simulations/{identifier}")
def edit_sim(
    identifier: str, data: SimInput, user=Depends(current), db: Session = Depends(db_session)
):
    row = get_sim(db, user, identifier)
    digest, prior = replay(db, user, "simulation_edit", data, identifier)
    if prior:
        return sim_view(db, row)
    revision(row, data)
    data.supplier_id = supplier(db, data.supplier_id).id
    if (
        row.status not in ("draft", "prepared")
        or row.store_id != data.store_id
        or row.supplier_id != data.supplier_id
    ):
        fail("Só pode editar simulações ou encomendas ativas da mesma loja e fornecedor.", 409)
    if row.status == "prepared":
        issued_order(db, row)
    config = validate_sim(db, user, data, row.id)
    for link in db.scalars(select(Supply).where(Supply.simulation_id == row.id)):
        db.delete(link)
    db.flush()
    for index, item in enumerate(data.lines):
        for alloc in item.allocations:
            db.add(
                Supply(
                    simulation_id=row.id,
                    simulation_line=index,
                    line_id=alloc.line_id,
                    quantity=alloc.quantity,
                )
            )
    previous = json.loads(row.data)
    raw = {
        **{k: v for k, v in previous.items() if k in ("supplier_confirmation", "previous_confirmations")},
        "lines": [line_item.model_dump(mode="json") for line_item in data.lines],
        "terms": config.model_dump(mode="json"),
        "totals": calculate(data.lines, config),
        "notes": data.notes,
    }
    resend = revise_order(db, user, row, raw, config) if row.status == "prepared" else False
    row.data = dump(raw)
    row.revision += 1
    event(
        db,
        user,
        "simulation_edit",
        data,
        digest,
        row.id,
        row.store_id,
        detail={"issued": row.status == "prepared", "needs_resend": resend},
    )
    db.commit()
    return sim_view(db, row)


@router.post("/simulations/{identifier}/cancel")
def cancel_sim(
    identifier: str, data: Change, user=Depends(current), db: Session = Depends(db_session)
):
    row = get_sim(db, user, identifier)
    digest, prior = replay(db, user, "simulation_cancel", data, identifier)
    if prior:
        return sim_view(db, row)
    revision(row, data)
    if row.status != "draft":
        fail(
            "Uma encomenda preparada deve ser tratada no circuito de compras; não é cancelada silenciosamente.",
            409,
        )
    row.status = "cancelled"
    row.revision += 1
    event(db, user, "simulation_cancel", data, digest, row.id, row.store_id)
    db.commit()
    return sim_view(db, row)


@router.post("/simulations/{identifier}/prepare")
def prepare(
    identifier: str, data: Prepare, user=Depends(current), db: Session = Depends(db_session)
):
    row = get_sim(db, user, identifier)
    digest, prior = replay(db, user, "prepare", data, identifier)
    if prior:
        return sim_view(db, row)
    revision(row, data)
    if row.status != "draft":
        fail("A simulação já foi preparada ou cancelada.", 409)
    raw = json.loads(row.data)
    config = CommercialTerms(**raw["terms"])
    if config.currency != "EUR":
        fail(
            "O circuito de compras atual só suporta EUR. Não é feita conversão cambial automática."
        )
    if raw["totals"]["missing_cents"] and not data.allow_below_minimum:
        fail("O mínimo não foi atingido. Reveja ou autorize explicitamente a exceção.", 409)
    wh = stock.warehouse(db, user, data.warehouse_id, authorize)
    if wh.store_id != row.store_id:
        fail("Armazém de outra loja.")
    lines = order_lines(db, raw, config, wh.id, row.supplier_id)
    payload = buying.Draft(
        kind="order",
        store_id=row.store_id,
        supplier_id=row.supplier_id,
        document=buying.order_label(sequence := buying.next_order_sequence(db)),
        expected_date=date.today() + timedelta(days=config.lead_days),
        contact=config.email or supplier(db, row.supplier_id).email,
        notes=raw.get("notes", ""),
        payment_terms=config.notes,
        request_key=data.request_key,
        lines=lines,
    )
    buying.validate(db, user, payload, authorize)
    saved = payload.model_dump(mode="json")
    saved.update(buying.totals(saved["lines"]))
    saved["order_sequence"] = sequence
    doc = Document(
        kind="order",
        store_id=row.store_id,
        supplier_id=row.supplier_id,
        user_id=user.id,
        request_key=data.request_key,
        data=dump(saved),
    )
    db.add(doc)
    db.flush()
    buying.confirm_document(db, user, doc, buying.Confirm(revision=0), authorize, record)
    row.procurement_id = doc.id
    row.status = "prepared"
    row.revision += 1
    event(
        db, user, "prepare", data, digest, row.id, row.store_id, detail={"procurement_id": doc.id}
    )
    db.commit()
    return sim_view(db, row)


def bonus_quantity(quantity, agreement, precision=0):
    """Unidades de oferta pela bonificação «na compra de X, oferta de Y» (por cada X completo)."""
    if not agreement or not agreement.bonus_buy_quantity or not agreement.bonus_free_quantity:
        return ZERO
    blocks = (D(quantity) / agreement.bonus_buy_quantity).to_integral_value(rounding="ROUND_FLOOR")
    return (blocks * agreement.bonus_free_quantity).quantize(
        D(1).scaleb(-precision), rounding="ROUND_DOWN"
    )


def order_lines(db, raw, config, warehouse_id, supplier_id=None):
    """Linhas da encomenda real (mercadorias, portes e ofertas) a partir de uma simulação.

    As ofertas da bonificação ficam no fim, a preço 0: a receção espera-as e o custo médio do
    stock reflete a oferta. Não deslocam os índices das linhas, que ligam as receções aos clientes.
    """
    from .supply_terms import article_terms

    agreements = article_terms(db) if supplier_id else {}
    bonuses = []
    lines = []
    for index, item in enumerate(raw["lines"]):
        if not item["article_id"]:
            fail("Associe os produtos provisórios a artigos reais antes de preparar a compra.")
        article = stock.article(db, item["article_id"])
        meta = json.loads(article.data)
        values = raw["totals"]["lines"][index]
        lines.append(
            buying.Line(
                article_id=article.id,
                description=item["description"],
                quantity=item["quantity"],
                price=item["unit_cost"],
                discount=values["discount"],
                vat=values["vat"],
                warehouse_id=warehouse_id,
                unit=meta.get("unit", "un"),
                supplier_reference=meta.get("supplier_reference", ""),
            )
        )
        agreement = agreements.get((article.id, supplier_id))
        free = bonus_quantity(item["quantity"], agreement, meta.get("precision", 0))
        if free > 0:
            bonuses.append(
                buying.Line(
                    article_id=article.id,
                    description=(
                        f"{item['description']} · oferta (na compra de "
                        f"{agreement.bonus_buy_quantity.normalize():f}, oferta de "
                        f"{agreement.bonus_free_quantity.normalize():f})"
                    ),
                    quantity=free,
                    price=ZERO,
                    vat=values["vat"],
                    warehouse_id=warehouse_id,
                    unit=meta.get("unit", "un"),
                    supplier_reference=meta.get("supplier_reference", ""),
                )
            )
    if raw["totals"]["shipping_cents"]:
        lines.append(
            buying.Line(
                nature="service",
                description="Portes de transporte",
                quantity=1,
                price=D(raw["totals"]["shipping_cents"]) / 100,
                vat=config.shipping_vat,
            )
        )
    return lines + bonuses


def issued_order(db, row):
    """Encomenda real ainda editável: ativa e sem receções nem faturas."""
    doc = db.get(Document, row.procurement_id)
    if not doc or doc.status == "cancelled":
        fail("A encomenda real foi cancelada; crie uma nova simulação.", 409)
    payload = json.loads(doc.data)
    if any(
        buying.used(db, doc.id, index, kind)
        for index in range(len(payload["lines"]))
        for kind in ("receipt", "invoice")
    ):
        fail(
            "A encomenda já tem receções ou faturas: receba o que chegou e faça outra encomenda "
            "para o resto.",
            409,
        )
    return doc, payload


def revise_order(db, user, row, raw, config):
    """Atualiza a encomenda real já emitida com a simulação editada (mesmo número, nova versão).

    Só antes de receções ou faturas: depois disso as linhas estão ligadas a movimentos e
    corrigem-se com a receção do que chegou e uma nova encomenda.
    """
    doc, payload = issued_order(db, row)
    warehouse_id = next(
        (line["warehouse_id"] for line in payload["lines"] if line.get("warehouse_id")), None
    )
    lines = order_lines(db, raw, config, warehouse_id, row.supplier_id)
    draft = buying.Draft(
        **{
            **{k: v for k, v in payload.items() if k in buying.Draft.model_fields},
            "lines": lines,
            "notes": raw.get("notes", ""),
            "request_key": payload.get("request_key") or doc.request_key,
            "revision": 0,
        }
    )
    buying.validate(db, user, draft, authorize)
    previous_total = payload.get("total_cents")
    updated = draft.model_dump(mode="json")
    updated.update(buying.totals(updated["lines"]))
    sent = any(
        (e := db.get(EmailDispatch, i)) and e.status in ("accepted", "received")
        for i in payload.get("email_ids", [])
    )
    payload.update({k: updated[k] for k in ("lines", "notes", "base_cents", "tax_cents", "total_cents") if k in updated})
    payload.setdefault("versions", []).append(
        {"at": now().isoformat(), "user_id": user.id, "previous_total_cents": previous_total}
    )
    payload["needs_resend"] = bool(sent or payload.get("needs_resend"))
    doc.data = dump(payload)
    doc.revision += 1
    # A confirmação do fornecedor referia-se à versão anterior: fica no histórico.
    if raw.get("supplier_confirmation"):
        raw.setdefault("previous_confirmations", []).append(raw.pop("supplier_confirmation"))
    return sent


@router.post("/simulations/{identifier}/supplier-confirm")
def confirm_supplier(
    identifier: str, data: ConfirmSupplier, user=Depends(current), db: Session = Depends(db_session)
):
    row = get_sim(db, user, identifier)
    digest, prior = replay(db, user, "supplier_confirm", data, identifier)
    if prior:
        return sim_view(db, row)
    revision(row, data)
    if not row.procurement_id or db.get(Document, row.procurement_id).status == "cancelled":
        fail("Prepare uma encomenda ativa antes de registar a confirmação.")
    raw = json.loads(row.data)
    if len(data.quantities) != len(raw["lines"]):
        fail("Indique a quantidade confirmada de cada produto.")
    for index, value in enumerate(data.quantities):
        if (
            not value.is_finite()
            or value < 0
            or value > D(raw["lines"][index]["quantity"])
            or value.as_tuple().exponent < -6
        ):
            fail("Quantidade confirmada inválida.")
        remaining = value
        for link in db.scalars(
            select(Supply)
            .where(Supply.simulation_id == row.id, Supply.simulation_line == index)
            .order_by(Supply.id)
        ):
            link.confirmed = min(link.quantity, remaining)
            remaining -= link.confirmed
    raw["supplier_confirmation"] = {
        "quantities": [str(q) for q in data.quantities],
        "at": now().isoformat(),
        "note": data.note,
    }
    row.data = dump(raw)
    row.revision += 1
    event(
        db, user, "supplier_confirm", data, digest, row.id, row.store_id, detail={"note": data.note}
    )
    db.commit()
    return sim_view(db, row)


def distributed(db, receipt_id, simulation_id=None):
    """A receção já foi distribuída pelos pedidos (desta simulação, ou de qualquer uma)."""
    query = select(Event).where(Event.action == "receive")
    if simulation_id:
        query = query.where(Event.target_id == simulation_id)
    return any(json.loads(e.data).get("receipt_id") == receipt_id for e in db.scalars(query))


def allocate_receipt(db, user, sim, order, receipt, *, tolerant=False):
    """Reserva aos clientes as quantidades aceites da receção que pertencem à encomenda `order`.

    Devolve (havia linhas da encomenda, reservas feitas, avisos). Com `tolerant`, uma reserva
    impossível (ex.: stock já consumido) fica como aviso em vez de anular a receção.
    """
    raw = json.loads(sim.data)
    matching, done, warnings = False, [], []
    for received in json.loads(receipt.data)["lines"]:
        if received.get("source_id") != order.id:
            continue
        index = received["source_line"]
        if index >= len(raw["lines"]):
            continue  # transport/service lines do not satisfy goods
        matching = True
        remaining = buying.accepted(received)
        for link in db.scalars(
            select(Supply)
            .where(Supply.simulation_id == sim.id, Supply.simulation_line == index)
            .order_by(Supply.id)
        ):
            amount = min(remaining, max(ZERO, link.quantity - link.released - link.received))
            if not amount:
                continue
            line = db.get(Line, link.line_id)
            customer = db.get(Order, line.order_id)
            label = json.loads(line.data).get("description", "")
            try:
                with db.begin_nested():
                    reserve(
                        db, user, line, received["warehouse_id"], amount, lot_id=received.get("lot_id")
                    )
            except HTTPException as exc:
                if not tolerant:
                    raise
                warnings.append(f"{order_number(customer)} · {label}: {exc.detail}")
                continue
            link.received += amount
            customer.revision += 1
            remaining -= amount
            done.append(
                {
                    "order_id": customer.id,
                    "number": order_number(customer),
                    "customer": json.loads(customer.data).get("customer_name", ""),
                    "description": label,
                    "quantity": str(amount),
                }
            )
    return matching, done, warnings


def order_customers(db, procurement_id):
    """Pedidos de clientes servidos por uma encomenda ao fornecedor, com o que ainda falta receber."""
    result = []
    for sim in db.scalars(
        select(Simulation).where(
            Simulation.procurement_id == procurement_id, Simulation.status != "cancelled"
        )
    ):
        for link in db.scalars(
            select(Supply).where(Supply.simulation_id == sim.id).order_by(Supply.id)
        ):
            line = db.get(Line, link.line_id)
            customer = db.get(Order, line.order_id)
            data = json.loads(customer.data)
            result.append(
                {
                    "order_id": customer.id,
                    "number": order_number(customer),
                    "customer": data.get("customer_name", ""),
                    "description": json.loads(line.data).get("description", ""),
                    "waiting": str(max(ZERO, link.quantity - link.released - link.received)),
                    "urgent": data.get("priority") == "urgent",
                    "due_date": data.get("due_date"),
                }
            )
    return result


def distribute_receipt(db, user, receipt):
    """Ao confirmar uma receção nas Compras: reserva logo aos clientes o que vem das encomendas deles.

    Guarda o resumo na própria receção. A receção pela simulação continua a usar
    `allocate_receipt` diretamente, por isso nada é distribuído duas vezes.
    """
    sources = {
        line.get("source_id") for line in json.loads(receipt.data)["lines"] if line.get("source_id")
    }
    if receipt.kind != "receipt" or not sources:
        return
    done, warnings = [], []
    for sim in db.scalars(
        select(Simulation).where(
            Simulation.procurement_id.in_(sources), Simulation.status != "cancelled"
        )
    ):
        if distributed(db, receipt.id, sim.id):
            continue
        order = db.get(Document, sim.procurement_id)
        _, reserved_now, problems = allocate_receipt(db, user, sim, order, receipt, tolerant=True)
        done += reserved_now
        warnings += problems
        sim.revision += 1
        key = SimpleNamespace(request_key=f"auto-receive:{receipt.id}:{sim.id}")
        event(
            db,
            user,
            "receive",
            key,
            "automatico",
            sim.id,
            sim.store_id,
            detail={
                "receipt_id": receipt.id,
                "document": json.loads(receipt.data)["document"],
                "automatic": True,
            },
        )
    if done or warnings:
        payload = json.loads(receipt.data)
        payload["customer_reservations"] = done
        payload["customer_warnings"] = warnings
        receipt.data = dump(payload)


@router.post("/simulations/{identifier}/receive")
def receive(
    identifier: str, data: Receipt, user=Depends(current), db: Session = Depends(db_session)
):
    row = get_sim(db, user, identifier)
    digest, prior = replay(db, user, "receive", data, identifier)
    if prior:
        return sim_view(db, row)
    revision(row, data)
    order = db.get(Document, row.procurement_id) if row.procurement_id else None
    if not order or order.status == "cancelled":
        fail("Prepare uma encomenda ativa antes da receção.")
    raw = json.loads(row.data)
    source = json.loads(order.data)
    if data.receipt_id:
        receipt = db.get(Document, data.receipt_id)
        if (
            not receipt
            or receipt.kind != "receipt"
            or not receipt.confirmed_at
            or receipt.store_id != row.store_id
            or receipt.supplier_id != row.supplier_id
        ):
            fail("Associe uma receção confirmada deste fornecedor e desta loja.")
    else:
        if not data.document.strip() or len(data.quantities) != len(raw["lines"]):
            fail("Indique o documento e as quantidades efetivamente recebidas.")
        lines = []
        for index, q in enumerate(data.quantities):
            if not q.is_finite() or q < 0 or q.as_tuple().exponent < -6:
                fail("Quantidade recebida inválida.")
            if q == 0:
                continue
            original = source["lines"][index]
            line = {k: v for k, v in original.items() if k in buying.Line.model_fields}
            line.update(
                source_id=order.id,
                source_line=index,
                quantity=str(q),
                lot_number=data.lots[index] if index < len(data.lots) else "",
                expiry=data.expiries[index] if index < len(data.expiries) else None,
            )
            lines.append(buying.Line(**line))
        if not lines:
            fail("Indique pelo menos um produto recebido.")
        payload = buying.Draft(
            kind="receipt",
            store_id=row.store_id,
            supplier_id=row.supplier_id,
            document=data.document,
            request_key=data.request_key,
            lines=lines,
        )
        buying.validate(db, user, payload, authorize)
        saved = payload.model_dump(mode="json")
        saved.update(buying.totals(saved["lines"]))
        receipt = Document(
            kind="receipt",
            store_id=row.store_id,
            supplier_id=row.supplier_id,
            user_id=user.id,
            request_key=data.request_key,
            data=dump(saved),
        )
        db.add(receipt)
        db.flush()
        buying.confirm_document(db, user, receipt, buying.Confirm(revision=0), authorize, record)
    # A confirmed receipt is attached only once, including across distinct request keys.
    if distributed(db, receipt.id, row.id):
        fail("Esta receção já foi distribuída pelos pedidos.", 409)
    matching, _, _ = allocate_receipt(db, user, row, order, receipt)
    if not matching:
        fail("A receção não contém linhas desta encomenda.")
    row.revision += 1
    event(
        db,
        user,
        "receive",
        data,
        digest,
        row.id,
        row.store_id,
        detail={"receipt_id": receipt.id, "document": json.loads(receipt.data)["document"]},
    )
    db.commit()
    return sim_view(db, row)


def contact_pdf(order, message):
    from xml.sax.saxutils import escape

    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    out = io.BytesIO()
    styles = getSampleStyleSheet()
    SimpleDocTemplate(out).build(
        [
            Paragraph(escape("Pedido " + order_number(order)), styles["Title"]),
            Spacer(1, 12),
            Paragraph(escape(message).replace("\n", "<br/>"), styles["Normal"]),
            Spacer(1, 12),
            Paragraph("Informação de levantamento. Não é documento fiscal.", styles["Normal"]),
        ]
    )
    return out.getvalue()


@router.post("/{identifier}/contact")
def prepare_contact(
    identifier: str, data: Contact, user=Depends(current), db: Session = Depends(db_session)
):
    row = get_order(db, user, identifier, True)
    digest, prior = replay(db, user, "contact", data, identifier)
    if prior:
        mail = db.get(EmailDispatch, prior.dispatch_id) if prior.dispatch_id else None
        from .email_delivery import dispatch_view

        return {
            **json.loads(prior.data),
            "event_id": prior.id,
            "email": dispatch_view(db, mail) if mail else None,
        }
    revision(row, data)
    raw = json.loads(row.data)
    if not raw["contact_allowed"] or raw["preferred_channel"] not in (data.channel,):
        fail("Este canal não está autorizado no pedido.", 409)
    if not order_view(db, row)["ready"]:
        fail("Ainda não há produtos reservados para levantamento.", 409)
    availability = {
        line.id: str(
            sum((r.quantity for r in usable_reservations(db, line)), ZERO) + line.delivered
        )
        for line in db.scalars(select(Line).where(Line.order_id == row.id))
        if usable_reservations(db, line)
    }
    detail = {
        "channel": data.channel,
        "message": data.message,
        "result": data.result,
        "availability": availability,
    }
    mail = None
    if data.prepared_event_id:
        original = db.get(Event, data.prepared_event_id)
        if not original or original.order_id != row.id or original.action != "contact":
            fail("Contacto preparado não encontrado.")
        original_data = json.loads(original.data)
        if (
            original_data.get("channel") != data.channel
            or original_data.get("message") != data.message
            or original_data.get("result") != "prepared"
        ):
            fail("Confirme a mensagem e o canal preparados.")
        detail["availability"] = original_data.get("availability", {})
    if data.channel == "email":
        if data.result != "prepared":
            fail("O resultado do email é registado pelo transporte SMTP.")
        from .dossier_package import zip_bytes
        from .email_delivery import dispatch_view, preview_plan
        from .email_settings import configuration, email_address
        from .platform import cipher

        config_row, config = configuration(db)
        if not config_row or not config["enabled"]:
            fail("Ative o SMTP nas configurações de Email.")
        recipient = email_address(raw["email"])
        key = "pedido-" + row.id[:8] + ".pdf"
        pdf = contact_pdf(row, data.message)
        for old in db.scalars(
            select(Event).where(
                Event.order_id == row.id, Event.action == "contact", Event.dispatch_id.is_not(None)
            )
        ):
            dispatch = db.get(EmailDispatch, old.dispatch_id)
            if dispatch and (dispatch.status in ("prepared", "sending") or dispatch.uncertain):
                fail(
                    "Existe um email preparado, em curso ou de resultado incerto. Consulte o histórico.",
                    409,
                )
        mail = EmailDispatch(
            id=str(uuid4()),
            kind="customer_order",
            request_key=data.request_key,
            created_by=user.id,
            start_date=date.today().isoformat(),
            end_date=date.today().isoformat(),
            store_ids=dump([row.store_id]),
            config_revision=config_row.revision,
            payload_ciphertext=cipher().encrypt(zip_bytes({key: pdf})),
            draft=dump(
                {
                    "to": [recipient],
                    "cc": [],
                    "subject": data.subject,
                    "message": data.message,
                    "selected_keys": [key],
                    "format": "individual",
                    "allow_split": False,
                }
            ),
            manifest=dump(
                {
                    "files": [
                        {
                            "key": key,
                            "sha256": hashlib.sha256(pdf).hexdigest(),
                            "original_name": key,
                            "document": order_number(row),
                            "supplier": raw["customer_name"],
                            "amount_cents": 0,
                            "date": date.today().isoformat(),
                        }
                    ]
                }
            ),
        )
        db.add(mail)
        db.flush()
        preview_plan(db, mail, json.loads(mail.draft), config)
        detail["recipient"] = recipient
    elif data.channel == "whatsapp":
        phone = re.sub(r"[\s()+.-]", "", raw["phone"])
        if phone.startswith("00"):
            phone = phone[2:]
        if not re.fullmatch(r"[1-9][0-9]{7,14}", phone):
            fail("Indique o telefone em formato internacional, incluindo o indicativo do país.")
        detail["url"] = "https://wa.me/" + phone + "?text=" + quote(data.message, safe="")
    if data.channel != "email" and data.result == "confirmed" and not data.prepared_event_id:
        fail("Prepare o contacto antes de confirmar o seu resultado.")
    item = event(
        db,
        user,
        "contact",
        data,
        digest,
        row.id,
        row.store_id,
        row.id,
        detail,
        mail.id if mail else None,
    )
    row.revision += 1
    db.commit()
    return {**detail, "event_id": item.id, "email": dispatch_view(db, mail) if mail else None}


@router.post("/{identifier}/email/{dispatch_id}/send")
def send_contact(
    identifier: str,
    dispatch_id: str,
    data: SendInput,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    from .email_delivery import get_dispatch, submit_dispatch

    row = get_order(db, user, identifier, True)
    raw = json.loads(row.data)
    if not raw["contact_allowed"] or raw["preferred_channel"] != "email":
        fail("Email não autorizado neste pedido.", 409)
    if not order_view(db, row)["ready"]:
        fail("Os produtos já não estão disponíveis. Reveja o pedido.", 409)
    item = db.scalar(
        select(Event).where(Event.order_id == row.id, Event.dispatch_id == dispatch_id)
    )
    if not item:
        fail("Este email não pertence ao pedido.", 404)
    for line_id, quantity in json.loads(item.data).get("availability", {}).items():
        line = db.get(Line, line_id)
        if not line or sum(
            (r.quantity for r in usable_reservations(db, line)), ZERO
        ) + line.delivered < D(quantity):
            fail(
                "A disponibilidade mudou desde a pré-visualização. Reveja o pedido antes de contactar.",
                409,
            )
    return submit_dispatch(db, get_dispatch(db, dispatch_id, True), data, user, record)


@router.get("/{identifier}/email/{dispatch_id}")
def customer_email(
    identifier: str, dispatch_id: str, user=Depends(current), db: Session = Depends(db_session)
):
    from .email_delivery import dispatch_view, get_dispatch

    row = get_order(db, user, identifier)
    if not db.scalar(
        select(Event.id).where(Event.order_id == row.id, Event.dispatch_id == dispatch_id)
    ):
        fail("Email não encontrado neste pedido.", 404)
    return dispatch_view(db, get_dispatch(db, dispatch_id))


@router.get("/simulations/{identifier}/email/{dispatch_id}")
def supplier_email(
    identifier: str, dispatch_id: str, user=Depends(current), db: Session = Depends(db_session)
):
    from .email_delivery import dispatch_view, get_dispatch

    row = get_sim(db, user, identifier)
    doc = db.get(Document, row.procurement_id) if row.procurement_id else None
    if not doc or dispatch_id not in json.loads(doc.data).get("email_ids", []):
        fail("Email não encontrado nesta encomenda.", 404)
    return dispatch_view(db, get_dispatch(db, dispatch_id))


@router.get("/history")
def history(store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        stores = [store_id]
    rows = list(
        db.scalars(
            select(Event)
            .where(Event.store_id.in_(stores))
            .order_by(Event.created_at.desc())
            .limit(1000)
        )
    )
    can_buy = (
        user.role in ("administrador", "gestor")
        and stock.permissions(user, stock.settings(db))["view_cost"]
    )
    result = []
    for item in rows:
        if not item.order_id and not can_buy:
            continue
        mail = db.get(EmailDispatch, item.dispatch_id) if item.dispatch_id else None
        result.append(
            {
                "id": item.id,
                "at": item.created_at,
                "action": item.action,
                "order_id": item.order_id,
                "target_id": item.target_id,
                "user": db.get(User, item.user_id).name,
                "detail": json.loads(item.data),
                "dispatch_id": item.dispatch_id,
                "status": mail.status if mail else json.loads(item.data).get("result", "recorded"),
                "message_ids": [p.message_id for p in parts_for(db, mail.id)] if mail else [],
            }
        )
    return result
