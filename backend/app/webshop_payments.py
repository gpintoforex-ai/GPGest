"""Encomendas online: pagamentos, prazos e reservas (Raizpura/WooCommerce ↔ IAZELA).

O WooCommerce faz o checkout; o IAZELA valida e reserva atomicamente (bloqueio da empresa) ao receber a
encomenda. A reserva é temporária até ao prazo do meio de pagamento; só um pagamento comunicado a partir do
callback autenticado do prestador (ou da confirmação de uma transferência por um administrador da loja) a torna
definitiva. Nunca o regresso do navegador.

Uma reserva vencida é libertada automaticamente (agendador e cada pedido da loja). Um pagamento que chegue
depois tenta reservar de novo; sem stock, fica para revisão (nunca se reembolsa ou se vende stock de outra
encomenda sozinho). Eventos repetidos têm um só efeito. Não emite faturas nem cria movimentos financeiros.
"""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal as D
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import customer_orders as orders
from . import inventory as stock
from .access import authorize, current, record, scope
from .db import db_session
from .models import (
    CustomerOrder,
    CustomerOrderLine,
    CustomerOrderReservation,
    CustomerSupplyLink,
    StockArticle,
    User,
    WebshopConnection,
    WebshopOrder,
    WebshopOrderEvent,
    WebshopPaymentEvent,
    WintouchProduct,
    now,
)
from .schemas import Strict
from .webshop import availability as free_stock
from .webshop import connector, order_state

router = APIRouter(prefix="/api/webshop", tags=["Loja online"])

LISBON = ZoneInfo("Europe/Lisbon")
MANAGERS = ["administrador", "gestor"]
VIEWERS = ["administrador", "gestor", "operador"]
# Prazos iniciais (minutos) para pagar antes de a reserva expirar; configuráveis por ligação.
DEFAULT_DEADLINES = {"mbway": 15, "card": 15, "multibanco": 2880, "payshop": 2880, "transfer": 4320, "other": 60}
KIND_LABELS = {"mbway": "MB WAY", "card": "Cartão", "multibanco": "Multibanco", "payshop": "Payshop",
               "transfer": "Transferência", "other": "Outro meio"}
CLOSED = {"cancelled", "failed", "refunded"}


def fail(message, code=422):
    raise HTTPException(code, message)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def kind_of(method):
    """Meio de pagamento a partir do identificador do método WooCommerce (ifthenpay e métodos nativos)."""
    value = (method or "").lower()
    for key, kind in (("mbway", "mbway"), ("creditcard", "card"), ("card", "card"), ("stripe", "card"),
                      ("vivacom", "card"), ("ppcp", "card"), ("paypal", "card"), ("woocommerce_payments", "card"),
                      ("multibanco", "multibanco"), ("payshop", "payshop"), ("bacs", "transfer"),
                      ("transfer", "transfer"), ("gateway_ifthen", "card")):
        if key in value:
            return kind
    return "other"


def deadlines(connection):
    saved = json.loads(connection.order_settings or "{}").get("deadlines", {})
    return {k: int(saved.get(k, v)) for k, v in DEFAULT_DEADLINES.items()}


def expiry_for(connection, kind, moment=None):
    return (moment or now()) + timedelta(minutes=deadlines(connection)[kind])


def log(db, mapping, action, user=None, **detail):
    db.add(WebshopOrderEvent(webshop_order_id=mapping.id, action=action, user_id=user.id if user else None,
                             data=dump(detail)))


def lines_of(db, mapping):
    return list(db.scalars(select(CustomerOrderLine).where(CustomerOrderLine.order_id == mapping.order_id)
                           .order_by(CustomerOrderLine.id)))


def committed(db, line):
    """Linha com efeitos que a libertação automática não pode desfazer (entrega, fatura ou compra atribuída)."""
    return bool(line.delivered or line.invoiced or db.scalar(select(CustomerSupplyLink.id).where(
        CustomerSupplyLink.line_id == line.id, CustomerSupplyLink.quantity > CustomerSupplyLink.released)))


def release(db, mapping, state, reason, user=None):
    """Liberta as reservas e cancela as quantidades da encomenda online. Com entregas, faturas ou compras
    atribuídas não liberta nada e fica para revisão."""
    rows = lines_of(db, mapping)
    if any(committed(db, line) for line in rows):
        mapping.reservation_state = "review"
        mapping.review = "Reserva não libertada automaticamente: existem entregas, faturas ou compras atribuídas."
        log(db, mapping, "release_blocked", user, reason=reason)
        return False
    for line in rows:
        for hold in db.scalars(select(CustomerOrderReservation).where(CustomerOrderReservation.line_id == line.id)):
            hold.quantity = D(0)
        line.cancelled = line.quantity
    mapping.reservation_state = state
    mapping.reservation_expires_at = None
    log(db, mapping, "reservation_" + state, user, reason=reason)
    return True


def reserve_again(db, connection, mapping, user=None):
    """Volta a reservar todas as linhas (pagamento depois de expirar, ou nova tentativa). Tudo ou nada."""
    owner = db.get(User, connection.user_id)
    rows = lines_of(db, mapping)
    missing = []
    for line in rows:
        free = free_stock(db, line.article_id, connection.warehouse_id)
        if free is None or free < line.quantity:
            missing.append(json.loads(line.data).get("description") or line.article_id)
    if missing:
        return missing
    for line in rows:
        line.cancelled = D(0)
        db.flush()
        orders.reserve(db, owner, line, connection.warehouse_id, line.quantity,
                       as_of=now().astimezone(LISBON).date())
    return []


def expire_due(db, connection_id=None, moment=None):
    """Liberta as reservas temporárias vencidas e sem pagamento. Chamar com o bloqueio da empresa."""
    moment = moment or now()
    query = select(WebshopOrder).where(WebshopOrder.reservation_state == "temporary",
                                       WebshopOrder.reservation_expires_at.is_not(None),
                                       WebshopOrder.reservation_expires_at <= moment,
                                       WebshopOrder.payment_state != "paid")
    if connection_id:
        query = query.where(WebshopOrder.connection_id == connection_id)
    count = 0
    for mapping in db.scalars(query.with_for_update()):
        deadline = mapping.reservation_expires_at
        if release(db, mapping, "expired", "Prazo de pagamento ultrapassado sem confirmação do prestador."):
            count += 1
            row = db.get(CustomerOrder, mapping.order_id)
            connection = db.get(WebshopConnection, mapping.connection_id)
            owner = db.get(User, connection.user_id)
            record(db, owner, "webshop_reservation_expired", row.id,
                   {"external_id": mapping.external_id, "deadline": deadline.isoformat()}, connection.store_id)
    return count


def state_view(mapping):
    return {
        "payment_state": mapping.payment_state,
        "payment_kind": mapping.payment_kind,
        "reservation_state": mapping.reservation_state,
        "reservation_expires_at": mapping.reservation_expires_at,
        "paid_at": mapping.paid_at,
        "review": mapping.review,
        "ready_for_preparation": mapping.payment_state in ("paid", "legacy")
        and mapping.reservation_state in ("confirmed", "legacy"),
    }


# --- Loja online (credencial da ligação) ------------------------------------------------------------------


class AvailabilityLine(Strict):
    article_id: str
    quantity: int = Field(gt=0, le=1000000)


class AvailabilityInput(Strict):
    lines: list[AvailabilityLine] = Field(min_length=1, max_length=100)


@router.post("/availability")
def check_availability(data: AvailabilityInput, identity=Depends(connector), db: Session = Depends(db_session)):
    """Disponibilidade atual (stock livre, sem reservas nem lotes expirados) para o carrinho/checkout.
    Não reserva: a reserva só acontece ao registar a encomenda."""
    conn, _ = identity
    stock.lock(db)
    expire_due(db, conn.id)
    db.commit()
    result = []
    for item in data.lines:
        article = db.get(StockArticle, item.article_id)
        meta = json.loads(article.data) if article else {}
        usable = bool(article and article.active and not meta.get("service") and not meta.get("precision", 0)
                      and not (article.erp_id and not db.get(WintouchProduct, article.erp_id).active))
        available = free_stock(db, item.article_id, conn.warehouse_id) if usable else 0
        result.append({"article_id": item.article_id, "requested": item.quantity, "available": available,
                       "ok": available is not None and available >= item.quantity})
    return {"connection_id": conn.id, "ok": all(r["ok"] for r in result), "lines": result}


class PaymentInput(Strict):
    event_key: str = Field(min_length=8, max_length=100)
    provider: str = Field(min_length=1, max_length=40)
    method: str = Field(default="", max_length=80)
    status: Literal["paid", "failed", "refunded"]
    transaction_id: str = Field(default="", max_length=200)
    amount_cents: int = Field(ge=0, le=10**12)
    currency: Literal["EUR"] = "EUR"
    # provider_callback: callback do prestador validado pelo seu plugin (chave anti-phishing/HMAC);
    # admin_confirmation: transferência confirmada por um administrador da loja.
    verification: Literal["provider_callback", "admin_confirmation"]
    occurred_at: datetime | None = None
    evidence: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("evidence")
    @classmethod
    def short_evidence(cls, value):
        return {str(k)[:40]: str(v)[:200] for k, v in value.items()}


@router.post("/orders/{external_id}/payments")
def payment(external_id: str, data: PaymentInput, identity=Depends(connector), db: Session = Depends(db_session)):
    """Pagamento autenticado do prestador. A mesma chave de evento repetida devolve o resultado original;
    com conteúdo diferente é recusada. Um segundo pagamento, um montante divergente ou um pagamento depois de
    expirar sem stock ficam para revisão."""
    conn, owner = identity
    stock.lock(db)
    db.expire_all()
    expire_due(db, conn.id)
    mapping = db.scalar(select(WebshopOrder).where(WebshopOrder.connection_id == conn.id,
                                                   WebshopOrder.external_id == external_id))
    if not mapping:
        fail("Encomenda não encontrada nesta ligação.", 404)
    body = data.model_dump(mode="json")
    signature = digest(body)
    previous = db.scalar(select(WebshopPaymentEvent).where(WebshopPaymentEvent.connection_id == conn.id,
                                                           WebshopPaymentEvent.event_key == data.event_key))
    if previous:
        if previous.digest != signature:
            fail("Evento de pagamento repetido com conteúdo diferente.", 409)
        db.commit()
        return {**order_state(db, mapping), "duplicate": True, "effect": previous.effect}
    effect = apply_payment(db, conn, mapping, data)
    db.add(WebshopPaymentEvent(connection_id=conn.id, webshop_order_id=mapping.id, event_key=data.event_key,
                               digest=signature, provider=data.provider, method=data.method, status=data.status,
                               transaction_id=data.transaction_id, amount_cents=data.amount_cents,
                               currency=data.currency, verification=data.verification,
                               occurred_at=data.occurred_at, effect=effect, data=dump(data.evidence)))
    log(db, mapping, "payment_" + effect, provider=data.provider, method=data.method, status=data.status,
        amount_cents=data.amount_cents, transaction_id=data.transaction_id, verification=data.verification)
    record(db, owner, "webshop_payment_" + effect, mapping.order_id,
           {"external_id": external_id, "amount_cents": data.amount_cents, "provider": data.provider},
           conn.store_id)
    mapping.updated_at = now()
    db.commit()
    return {**order_state(db, mapping), "duplicate": False, "effect": effect}


def apply_payment(db, conn, mapping, data):
    if data.status == "failed":
        if mapping.payment_state != "paid":
            mapping.payment_state = "failed"
        return "failed"
    if data.status == "refunded":
        if mapping.payment_state == "paid":
            mapping.payment_state = "refunded"
            if mapping.reservation_state in ("confirmed", "legacy") and mapping.status not in CLOSED:
                mapping.review = "Pagamento reembolsado com a reserva ativa: cancele a encomenda ou liberte a reserva."
        return "refunded"
    if data.amount_cents != mapping.total_cents:
        mapping.review = (f"Pagamento de {data.amount_cents / 100:.2f} € diferente do total da encomenda "
                          f"({mapping.total_cents / 100:.2f} €): confirme com o prestador.")
        return "amount_mismatch"
    if mapping.payment_state == "paid":
        mapping.review = "Segundo pagamento recebido para a mesma encomenda: confirme e reembolse se for duplicado."
        return "duplicate_payment"
    mapping.payment_state, mapping.paid_cents, mapping.paid_at = "paid", data.amount_cents, data.occurred_at or now()
    if data.method and not mapping.payment_method:
        mapping.payment_method, mapping.payment_kind = data.method, kind_of(data.method)
    if mapping.reservation_state in ("temporary", "legacy", "confirmed"):
        mapping.reservation_state, mapping.reservation_expires_at = "confirmed", None
        return "confirmed"
    if mapping.reservation_state in ("expired", "released"):
        missing = reserve_again(db, conn, mapping)
        if not missing:
            mapping.reservation_state = "confirmed"
            log(db, mapping, "reservation_confirmed_after_expiry")
            return "late_reserved"
        mapping.reservation_state = "review"
        mapping.review = ("Pagamento recebido depois de a reserva expirar e sem stock livre para: "
                          + ", ".join(missing)[:300] + ". Reponha o stock ou reembolse o cliente.")
        return "late_no_stock"
    return "recorded"


# --- Gestão (sessão IAZELA) -------------------------------------------------------------------------------


def visible_connections(db, user):
    stores = scope(user, db)
    return {c.id: c for c in db.scalars(select(WebshopConnection).where(WebshopConnection.store_id.in_(stores)))}


def order_view(db, mapping, connection, detail=False):
    row = db.get(CustomerOrder, mapping.order_id)
    data = json.loads(row.data)
    shop = data.get("webshop", {})
    rows = lines_of(db, mapping)
    articles = []
    for line in rows:
        q = orders.quantities(db, line)
        articles.append({"article_id": line.article_id, "description": json.loads(line.data).get("description", ""),
                         "quantity": str(line.quantity), "reserved": str(q["reserved"]),
                         "delivered": str(line.delivered), "invoiced": str(line.invoiced),
                         "cancelled": str(line.cancelled)})
    view = {
        "id": mapping.id,
        "connection_id": mapping.connection_id,
        "connection_name": connection.name,
        "external_id": mapping.external_id,
        "shop_order": mapping.external_id.rsplit(":", 1)[-1],
        "order_id": row.id,
        "number": orders.order_number(row),
        "created_at": mapping.created_at or mapping.updated_at,
        "updated_at": mapping.updated_at,
        "shop_status": mapping.status,
        "order_state": order_state(db, mapping)["state"],
        "customer": {"name": data.get("customer_name", ""), "email": data.get("email", ""),
                     "phone": data.get("phone", ""), "shipping_address": shop.get("shipping_address", "")},
        "total_cents": mapping.total_cents,
        "payment_method": mapping.payment_method,
        "payment_kind_label": KIND_LABELS.get(mapping.payment_kind, "—") if mapping.payment_kind else "—",
        "paid_cents": mapping.paid_cents,
        "lines": articles,
        **state_view(mapping),
    }
    if detail:
        names = {}

        def who(identifier):
            if identifier and identifier not in names:
                person = db.get(User, identifier)
                names[identifier] = person.name if person else ""
            return names.get(identifier, "Loja online / automático")

        view["history"] = [{"action": e.action, "at": e.created_at, "user": who(e.user_id), "data": json.loads(e.data)}
                           for e in db.scalars(select(WebshopOrderEvent).where(
                               WebshopOrderEvent.webshop_order_id == mapping.id)
                               .order_by(WebshopOrderEvent.created_at.desc(), WebshopOrderEvent.id))]
        view["payments"] = [{"at": p.received_at, "provider": p.provider, "method": p.method, "status": p.status,
                             "amount_cents": p.amount_cents, "transaction_id": p.transaction_id,
                             "verification": p.verification, "effect": p.effect}
                            for p in db.scalars(select(WebshopPaymentEvent).where(
                                WebshopPaymentEvent.webshop_order_id == mapping.id)
                                .order_by(WebshopPaymentEvent.received_at.desc()))]
    return view


@router.get("/online-orders")
def online_orders(
    connection_id: str = "",
    payment: str = "",
    reservation: str = "",
    attention: bool = False,
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=200, ge=1, le=500),
    user=Depends(current),
    db: Session = Depends(db_session),
):
    authorize(user, db, roles=VIEWERS)
    connections = visible_connections(db, user)
    if connection_id:
        if connection_id not in connections:
            fail("Ligação não encontrada.", 404)
        connections = {connection_id: connections[connection_id]}
    if not connections:
        return {"connections": [], "items": [], "counts": {}}
    query = select(WebshopOrder).where(WebshopOrder.connection_id.in_(list(connections)))
    if payment:
        query = query.where(WebshopOrder.payment_state == payment)
    if reservation:
        query = query.where(WebshopOrder.reservation_state == reservation)
    if attention:
        query = query.where(or_(WebshopOrder.review != "", WebshopOrder.reservation_state == "review"))
    items = []
    for mapping in db.scalars(query.order_by(WebshopOrder.updated_at.desc()).limit(limit)):
        view = order_view(db, mapping, connections[mapping.connection_id])
        if q.strip():
            needle = q.strip().lower()
            haystack = " ".join([view["number"], view["external_id"], view["customer"]["name"],
                                 view["customer"]["email"]] + [line["description"] for line in view["lines"]]).lower()
            if needle not in haystack:
                continue
        items.append(view)
    counts = {}
    for view in items:
        for key in ("payment_state", "reservation_state"):
            counts[f"{key}:{view[key]}"] = counts.get(f"{key}:{view[key]}", 0) + 1
    counts["attention"] = sum(1 for v in items if v["review"] or v["reservation_state"] == "review")
    return {"connections": [{"id": c.id, "name": c.name, "deadlines": deadlines(c)} for c in connections.values()],
            "items": items, "counts": counts}


def get_mapping(db, user, identifier, roles):
    mapping = db.get(WebshopOrder, identifier)
    if not mapping:
        fail("Encomenda online não encontrada.", 404)
    connection = db.get(WebshopConnection, mapping.connection_id)
    authorize(user, db, connection.store_id, roles)
    return mapping, connection


@router.get("/online-orders/{identifier}")
def online_order(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    mapping, connection = get_mapping(db, user, identifier, VIEWERS)
    return order_view(db, mapping, connection, detail=True)


class ActionInput(Strict):
    note: str = Field(default="", max_length=500)


@router.post("/online-orders/{identifier}/{action}")
def online_order_action(
    identifier: str,
    action: Literal["retry_reservation", "extend", "release", "resolve"],
    data: ActionInput,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    """Decisões do operador: reservar de novo (pago sem reserva), prolongar o prazo, libertar ou dar a revisão
    por resolvida. Todas ficam no histórico e na auditoria."""
    stock.lock(db)
    mapping, connection = get_mapping(db, user, identifier, MANAGERS)
    if action != "retry_reservation" and len(data.note.strip()) < 3:
        fail("Indique o motivo.")
    if action == "retry_reservation":
        if mapping.reservation_state not in ("expired", "released", "review") or mapping.status in CLOSED:
            fail("Só se volta a reservar uma encomenda aberta sem reserva.", 409)
        missing = reserve_again(db, connection, mapping, user)
        if missing:
            fail("Stock livre insuficiente para: " + ", ".join(missing), 409)
        mapping.reservation_state = "confirmed" if mapping.payment_state == "paid" else "temporary"
        mapping.reservation_expires_at = (None if mapping.payment_state == "paid"
                                          else expiry_for(connection, mapping.payment_kind or "other"))
        if mapping.payment_state == "paid":
            mapping.review = ""
    elif action == "extend":
        if mapping.reservation_state != "temporary":
            fail("Só se prolonga uma reserva temporária.", 409)
        mapping.reservation_expires_at = expiry_for(connection, mapping.payment_kind or "other")
    elif action == "release":
        if mapping.reservation_state not in ("temporary", "review", "legacy", "confirmed"):
            fail("A reserva já não está ativa.", 409)
        if mapping.payment_state == "paid" and not mapping.review:
            fail("Encomenda paga: cancele-a na loja (com reembolso) em vez de libertar só a reserva.", 409)
        if not release(db, mapping, "released", data.note.strip(), user):
            fail(mapping.review, 409)
    else:
        if not mapping.review and mapping.reservation_state != "review":
            fail("Não há nada por rever nesta encomenda.", 409)
        mapping.review = ""
        if mapping.reservation_state == "review":
            mapping.reservation_state = "released"
    mapping.updated_at = now()
    log(db, mapping, action, user, note=data.note.strip())
    record(db, user, "webshop_order_" + action, mapping.order_id, {"note": data.note.strip()}, connection.store_id)
    db.commit()
    return order_view(db, mapping, connection, detail=True)


class SettingsInput(Strict):
    deadlines: dict[str, int]

    @field_validator("deadlines")
    @classmethod
    def valid(cls, value):
        unknown = set(value) - set(DEFAULT_DEADLINES)
        if unknown:
            raise ValueError("Meio de pagamento desconhecido: " + ", ".join(sorted(unknown)))
        if any(v < 5 or v > 30 * 24 * 60 for v in value.values()):
            raise ValueError("Cada prazo tem de estar entre 5 minutos e 30 dias.")
        return value


@router.put("/connections/{identifier}/order-settings")
def save_settings(identifier: str, data: SettingsInput, user=Depends(current), db: Session = Depends(db_session)):
    connection = db.get(WebshopConnection, identifier)
    if not connection:
        fail("Ligação não encontrada.", 404)
    authorize(user, db, connection.store_id, MANAGERS)
    before = deadlines(connection)
    saved = json.loads(connection.order_settings or "{}")
    saved["deadlines"] = {**before, **data.deadlines}
    connection.order_settings = dump(saved)
    record(db, user, "webshop_order_settings", connection.id, {"before": before, "after": saved["deadlines"]},
           connection.store_id)
    db.commit()
    return {"deadlines": deadlines(connection)}


def scheduled_tick():
    """Expira as reservas vencidas em todas as empresas (o agendador corre de 30 em 30 s)."""
    from . import db as database
    from .db import company_db
    from .platform_models import Company

    with database.next_platform_session() as core:
        companies = list(core.scalars(select(Company).where(Company.active == 1)))
        for company in companies:
            core.expunge(company)
    for company in companies:
        try:
            with company_db(company) as db:
                pending = db.scalar(select(WebshopOrder.id).where(
                    WebshopOrder.reservation_state == "temporary",
                    WebshopOrder.reservation_expires_at <= now()).limit(1))
                if not pending:
                    continue
                stock.lock(db)
                expire_due(db)
                db.commit()
        except Exception:
            continue
