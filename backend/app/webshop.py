"""API restrita para lojas online. Não escreve no ERP nem cria movimentos financeiros."""

import hashlib
import hmac
import json
import secrets
from datetime import timedelta, timezone
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import customer_orders as orders
from . import inventory as stock
from .access import authorize, current, record
from .db import db_session
from .models import (
    CustomerOrder,
    CustomerOrderLine,
    CustomerOrderReservation,
    CustomerSupplyLink,
    StockArticle,
    StockLot,
    StockWarehouse,
    User,
    WebshopConnection,
    WebshopOrder,
    WintouchProduct,
    now,
)
from .schemas import Strict

router = APIRouter(prefix="/api/webshop", tags=["Loja online"])


def fail(message, code=422):
    raise HTTPException(code, message)


class ConnectionInput(Strict):
    name: str = Field(default="Raizpura", min_length=1, max_length=100)
    store_id: str
    warehouse_id: str


@router.post("/connections")
def create_connection(
    data: ConnectionInput, user=Depends(current), db: Session = Depends(db_session)
):
    authorize(user, db, data.store_id, ["administrador", "gestor"])
    wh = db.get(StockWarehouse, data.warehouse_id)
    if not wh or not wh.active or wh.store_id != data.store_id:
        fail("Selecione um armazém ativo da loja escolhida.")
    token = secrets.token_urlsafe(48)
    row = WebshopConnection(
        name=data.name,
        store_id=data.store_id,
        warehouse_id=wh.id,
        user_id=user.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=now() + timedelta(days=90),
    )
    db.add(row)
    db.flush()
    record(db, user, "webshop_connection_created", row.id, {"warehouse_id": wh.id}, row.store_id)
    db.commit()
    return {"id": row.id, "token": token, "expires_at": row.expires_at}


@router.get("/connections")
def connections(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    from .access import scope

    return [
        {
            "id": r.id,
            "name": r.name,
            "store_id": r.store_id,
            "warehouse_id": r.warehouse_id,
            "active": r.active,
            "expires_at": r.expires_at,
        }
        for r in db.scalars(
            select(WebshopConnection).where(WebshopConnection.store_id.in_(scope(user, db)))
        )
    ]


@router.post("/connections/{identifier}/revoke")
def revoke(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    row = db.get(WebshopConnection, identifier)
    if not row:
        fail("Ligação não encontrada.", 404)
    authorize(user, db, row.store_id, ["administrador", "gestor"])
    row.active = False
    record(db, user, "webshop_connection_revoked", row.id, {}, row.store_id)
    db.commit()
    return {"active": False}


def connector(request: Request, db: Session = Depends(db_session)):
    row = db.get(WebshopConnection, request.headers.get("x-raizpura-connection", ""))
    auth = request.headers.get("authorization", "")
    digest = hashlib.sha256(auth[7:].encode()).hexdigest() if auth.startswith("Bearer ") else ""
    if not row or not row.active or not hmac.compare_digest(row.token_hash, digest):
        fail("Credenciais da integração inválidas.", 401)
    expiry = (
        row.expires_at.replace(tzinfo=timezone.utc)
        if row.expires_at.tzinfo is None
        else row.expires_at
    )
    user = db.get(User, row.user_id)
    if expiry <= now() or not user or not user.active:
        fail("Ligação expirada ou utilizador indisponível.", 401)
    authorize(user, db, row.store_id, ["administrador", "gestor"])
    wh = db.get(StockWarehouse, row.warehouse_id)
    if not wh or not wh.active or wh.store_id != row.store_id:
        fail("O armazém da integração está indisponível.", 409)
    return row, user


@router.get("/health")
def health(identity=Depends(connector)):
    row, _ = identity
    return {
        "version": 1,
        "connection_id": row.id,
        "store_id": row.store_id,
        "warehouse_id": row.warehouse_id,
        "expires_at": row.expires_at,
    }


def availability(db, article_id, warehouse_id):
    if stock.stock_from_erp(db):
        return None
    total = D(0)
    for (_, lot_id), quantity in orders.free_balances(db, article_id, warehouse_id).items():
        lot = db.get(StockLot, lot_id) if lot_id else None
        if (
            lot
            and lot.expiry
            and lot.expiry < now().astimezone(ZoneInfo("Europe/Lisbon")).date().isoformat()
        ):
            continue
        total += max(D(0), quantity)
    return int(total.to_integral_value(rounding="ROUND_FLOOR"))


@router.get("/catalog")
def catalog(
    cursor: str = "",
    limit: int = Query(default=100, ge=1, le=100),
    identity=Depends(connector),
    db: Session = Depends(db_session),
):
    connection, _ = identity
    rows = list(
        db.scalars(
            select(StockArticle)
            .where(StockArticle.id > cursor)
            .order_by(StockArticle.id)
            .limit(limit + 1)
        )
    )
    result = []
    for row in rows[:limit]:
        data = json.loads(row.data)
        erp = db.get(WintouchProduct, row.erp_id) if row.erp_id else None
        active = bool(row.active and (erp is None or erp.active))
        supported = not data.get("service") and int(data.get("precision", 0)) == 0
        net = erp.sale_price if erp else D(str(data.get("sale_net", 0)))
        vat = erp.vat_rate if erp else D(str(data.get("vat_rate", 0)))
        gross = erp.sale_price_vat if erp else None
        if gross is None and net is not None and vat is not None:
            gross = (net * (1 + vat / 100)).quantize(D(".01"), rounding=ROUND_HALF_UP)
        result.append(
            {
                "id": row.id,
                "sku": erp.code if erp else row.code,
                "name": erp.name if erp else row.name,
                "active": active,
                "supported": supported,
                "description": data.get("description", ""),
                "family": data.get("family", ""),
                "category": data.get("category", ""),
                "brand": data.get("brand", ""),
                "barcodes": data.get("barcodes", []),
                "unit": data.get("unit", "un"),
                "price_net": str(net) if net is not None else None,
                "price_gross": str(gross) if gross is not None else None,
                "vat_rate": str(vat) if vat is not None else None,
                "available": availability(db, row.id, connection.warehouse_id)
                if active and supported
                else 0,
            }
        )
    return {
        "version": 1,
        "connection_id": connection.id,
        "products": result,
        "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
    }


class WebLine(Strict):
    article_id: str
    quantity: int = Field(gt=0, le=1000000)
    total_net_cents: int = Field(ge=0, le=10**12)


class WebOrderInput(Strict):
    external_id: str = Field(min_length=1, max_length=100)
    sequence: int = Field(ge=1)
    status: Literal[
        "pending", "on-hold", "processing", "completed", "cancelled", "failed", "refunded"
    ]
    currency: Literal["EUR"] = "EUR"
    total_cents: int = Field(ge=0, le=10**12)
    customer_name: str = Field(min_length=1, max_length=300)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=60)
    shipping_address: str = Field(default="", max_length=2000)
    lines: list[WebLine] = Field(min_length=1, max_length=100)


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def order_state(db, mapping):
    row = db.get(CustomerOrder, mapping.order_id)
    lines = list(db.scalars(select(CustomerOrderLine).where(CustomerOrderLine.order_id == row.id)))
    state = (
        "delivered"
        if all(line.delivered == line.quantity for line in lines)
        else "cancelled"
        if all(line.cancelled == line.quantity for line in lines)
        else "partial"
        if any(line.delivered for line in lines)
        else "registered"
    )
    return {
        "external_id": mapping.external_id,
        "order_id": row.id,
        "number": orders.order_number(row),
        "sequence": mapping.sequence,
        "state": state,
    }


@router.get("/orders/{external_id}")
def status(external_id: str, identity=Depends(connector), db: Session = Depends(db_session)):
    conn, _ = identity
    row = db.scalar(
        select(WebshopOrder).where(
            WebshopOrder.connection_id == conn.id, WebshopOrder.external_id == external_id
        )
    )
    if not row:
        fail("Encomenda não encontrada nesta ligação.", 404)
    return order_state(db, row)


@router.post("/orders")
def receive(data: WebOrderInput, identity=Depends(connector), db: Session = Depends(db_session)):
    conn, user = identity
    stock.lock(db)
    db.expire_all()
    if not conn.active:
        fail("Ligação revogada.", 401)
    mapping = db.scalar(
        select(WebshopOrder).where(
            WebshopOrder.connection_id == conn.id, WebshopOrder.external_id == data.external_id
        )
    )
    raw = data.model_dump(mode="json")
    digest = fingerprint(raw)
    content = fingerprint({k: v for k, v in raw.items() if k not in {"sequence", "status"}})
    if mapping:
        if data.sequence < mapping.sequence:
            return order_state(db, mapping)
        if data.sequence == mapping.sequence:
            if mapping.digest != digest:
                fail("Versão repetida com conteúdo diferente.", 409)
            return order_state(db, mapping)
        if mapping.content_digest != content:
            fail("A encomenda já integrada foi alterada. Rever manualmente sem duplicar.", 409)
        if mapping.status in {"cancelled", "failed", "refunded"} and data.status not in {
            "cancelled",
            "failed",
            "refunded",
        }:
            fail("Não é possível reabrir automaticamente esta encomenda.", 409)
        row = db.get(CustomerOrder, mapping.order_id)
    else:
        cancelled = data.status in {"cancelled", "failed", "refunded"}
        if not cancelled and stock.stock_from_erp(db):
            fail("O conector ERP não suporta reservas de stock. Reveja a autoridade do stock.", 409)
        if len({line.article_id for line in data.lines}) != len(data.lines):
            fail("Agrupe as quantidades do mesmo artigo numa única linha.")
        for item in data.lines:
            article = db.get(StockArticle, item.article_id)
            if not article or (
                not cancelled
                and (
                    not article.active
                    or json.loads(article.data).get("service")
                    or json.loads(article.data).get("precision", 0)
                    or (article.erp_id and not db.get(WintouchProduct, article.erp_id).active)
                )
            ):
                fail("Artigo indisponível ou incompatível com a loja online.", 409)
        if not cancelled:
            for item in data.lines:
                if availability(db, item.article_id, conn.warehouse_id) < item.quantity:
                    fail("Stock livre insuficiente ou lotes expirados.", 409)
        sequence = orders.next_order_sequence(db)
        row = CustomerOrder(
            store_id=conn.store_id,
            user_id=user.id,
            data=stock.dump(
                {
                    "customer_name": data.customer_name,
                    "email": data.email,
                    "phone": data.phone,
                    "preferred_channel": "none",
                    "contact_allowed": False,
                    "priority": "normal",
                    "notes": "Raizpura / encomenda online "
                    + data.external_id
                    + "\nEntrega: "
                    + data.shipping_address,
                    "requested_at": now().isoformat(),
                    "sequence": sequence,
                    "number": orders.order_label(sequence),
                    "webshop": raw,
                }
            ),
        )
        db.add(row)
        db.flush()
        for item in data.lines:
            article = db.get(StockArticle, item.article_id)
            line = CustomerOrderLine(
                order_id=row.id,
                article_id=article.id,
                quantity=D(item.quantity),
                data=stock.dump(
                    {
                        "description": article.name,
                        "reference": article.code,
                        "sale_price_cents": int(
                            (D(item.total_net_cents) / item.quantity).quantize(
                                D(1), rounding=ROUND_HALF_UP
                            )
                        ),
                        "webshop_total_net_cents": item.total_net_cents,
                    }
                ),
            )
            db.add(line)
            db.flush()
            if not cancelled:
                orders.reserve(
                    db,
                    user,
                    line,
                    conn.warehouse_id,
                    D(item.quantity),
                    as_of=now().astimezone(ZoneInfo("Europe/Lisbon")).date(),
                )
        mapping = WebshopOrder(
            connection_id=conn.id,
            external_id=data.external_id,
            order_id=row.id,
            sequence=data.sequence,
            digest=digest,
            content_digest=content,
            status=data.status,
        )
        db.add(mapping)
    if data.status in {"cancelled", "failed", "refunded"}:
        lines = list(
            db.scalars(select(CustomerOrderLine).where(CustomerOrderLine.order_id == row.id))
        )
        for line in lines:
            if line.delivered or db.scalar(
                select(CustomerSupplyLink.id).where(
                    CustomerSupplyLink.line_id == line.id,
                    CustomerSupplyLink.quantity > CustomerSupplyLink.released,
                )
            ):
                fail(
                    "Existem entregas ou compras atribuídas. Rever cancelamento/devolução no GPGest.",
                    409,
                )
        for line in lines:
            for hold in db.scalars(
                select(CustomerOrderReservation).where(CustomerOrderReservation.line_id == line.id)
            ):
                hold.quantity = D(0)
            line.cancelled = line.quantity
    mapping.sequence, mapping.digest, mapping.status = data.sequence, digest, data.status
    mapping.updated_at = now()
    details = json.loads(row.data)
    details["webshop"] = raw
    row.data = stock.dump(details)
    row.revision += 1
    record(
        db,
        user,
        "webshop_order_synced",
        row.id,
        {
            "connection_id": conn.id,
            "external_id": data.external_id,
            "sequence": data.sequence,
            "status": data.status,
        },
        conn.store_id,
    )
    db.commit()
    return order_state(db, mapping)


@router.get("/options")
def options(user=Depends(current), db: Session = Depends(db_session)):
    from .access import scope
    from .models import Store

    authorize(user, db, roles=["administrador", "gestor"])
    allowed = scope(user, db)
    return {
        "company": db.info.get("company", {}),
        "stores": [
            {"id": r.id, "name": r.name}
            for r in db.scalars(select(Store).where(Store.id.in_(allowed)))
        ],
        "warehouses": [
            {"id": w.id, "name": w.name, "store_id": w.store_id}
            for w in db.scalars(
                select(StockWarehouse).where(
                    StockWarehouse.store_id.in_(allowed), StockWarehouse.active
                )
            )
        ],
    }


@router.post("/connections/{identifier}/rotate")
def rotate(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    row = db.get(WebshopConnection, identifier)
    if not row:
        fail("Ligação não encontrada.", 404)
    authorize(user, db, row.store_id, ["administrador", "gestor"])
    if not row.active:
        fail("Crie uma nova ligação para substituir uma ligação revogada.", 409)
    token = secrets.token_urlsafe(48)
    row.token_hash = hashlib.sha256(token.encode()).hexdigest()
    row.user_id = user.id
    row.expires_at = now() + timedelta(days=90)
    record(db, user, "webshop_token_rotated", row.id, {}, row.store_id)
    db.commit()
    return {"id": row.id, "token": token, "expires_at": row.expires_at}
