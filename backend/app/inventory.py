"""Stock is an append-only ledger. FEFO allocation and moving average are independent."""

import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from unicodedata import combining, normalize
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query, Response
from fastapi.encoders import decimal_encoder
from pydantic import Field, field_validator
from sqlalchemy import case, cast, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.orm import Session

from .db import db_session
from .models import (
    Audit,
    BusinessEntity,
    CustomerOrderLine,
    CustomerOrderReservation,
    CustomerPurchaseSimulation,
    OrderReplenishmentPolicy,
    ProcurementDocument,
    StockArticle,
    StockEntry,
    StockInventory,
    StockLot,
    StockSettings,
    StockWarehouse,
    Store,
    User,
    WintouchProduct,
    WintouchSyncRun,
    now,
)
from .operating_modes import policy
from .schemas import Strict

D = Decimal
ZERO = D(0)
PERMISSIONS = (
    "view_cost",
    "change_price",
    "adjust_stock",
    "confirm_inventory",
    "manage_lots",
    "negative_stock",
    "override_fefo",
)
DEFAULTS = {
    "allow_negative": False,
    "recoverable_vat": True,
    "alerts": [90, 60, 30, 7],
    "permissions": {},
    "saft_mapping": {},
    "control_start": None,
}
INCOMING = {"purchase", "opening", "customer_return"}
OUTGOING = {"sale", "supplier_return", "loss", "expired", "transfer"}


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def fast_json(content):
    """O mesmo JSON que o FastAPI gera, sem o jsonable_encoder (lento no catálogo completo)."""

    def default(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return decimal_encoder(value)
        if isinstance(value, (set, frozenset)):
            return list(value)
        raise TypeError(f"Valor sem conversão para JSON: {type(value).__name__}")

    return Response(
        json.dumps(
            content, ensure_ascii=False, allow_nan=False, default=default, separators=(",", ":")
        ),
        media_type="application/json",
    )


def document_key(value):
    return " ".join(value.split()).casefold()


def fail(message, status=422):
    raise HTTPException(status, message)


def field_fail(field, message, status=422):
    raise HTTPException(status, [{"loc": ["body", field], "msg": message, "type": "value_error"}])


def lock(db):
    # Shared by all inventory writes: settings, CSV, inventory and manual/source postings.
    company = db.info.get("company", {}).get("id")
    db.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtext(coalesce(:company, current_schema()) || :suffix))"
        ),
        {"company": company, "suffix": ":stock-write"},
    )


def settings(db):
    row = db.get(StockSettings, "default")
    return {
        **DEFAULTS,
        **(json.loads(row.data) if row else {}),
        "revision": row.revision if row else 0,
    }


def permissions(user, config):
    base = {key: user.role in ("administrador", "gestor") for key in PERMISSIONS}
    base["negative_stock"] = user.role == "administrador"
    if user.role != "administrador":
        base.update(config["permissions"].get(user.id, {}))
    return base


def require(user, config, key):
    if not permissions(user, config).get(key):
        fail("O seu perfil não permite esta operação de artigos e stock.", 403)


def stock_from_erp(db):
    """O stock só é do ERP se a empresa o declarar; por omissão é registado na IAZELA.

    O conector Wintouch não lê quantidades (e a Wintouch pode nem controlar stock:
    GET /products/control_stocks), por isso os artigos do ERP têm stock local.
    """
    return policy(db).get("authority", {}).get("stock") == "erp"


def article(db, identifier, writable=False, *, stock=False):
    """Artigo da empresa. writable: a ficha vai ser alterada; stock=True: só o stock/gestão local."""
    row = db.get(StockArticle, identifier)
    if not row:
        fail("Artigo não encontrado nesta empresa.", 404)
    if writable and row.erp_id:
        if not stock:
            fail(
                "Artigo do ERP: o código, o nome, o estado e o preço vêm do ERP e só se alteram na origem.",
                409,
            )
        if stock_from_erp(db):
            fail(
                "O stock desta empresa é controlado pelo ERP e o conector não permite escrevê-lo.",
                409,
            )
    return row


def warehouse(db, user, identifier, authorize):
    row = db.get(StockWarehouse, identifier)
    if not row:
        fail("Armazém não encontrado nesta empresa.", 404)
    authorize(user, db, row.store_id)
    return row


def warehouse_dependencies(db, row):
    """Registos que impedem remover um armazém: (descrição, quantidade)."""
    documents = sum(
        1
        for document in db.scalars(
            select(ProcurementDocument).where(ProcurementDocument.store_id == row.store_id)
        )
        if any(
            line.get("warehouse_id") == row.id
            for line in json.loads(document.data).get("lines", [])
        )
    )
    return [
        (
            "movimentos de stock no histórico",
            db.scalar(
                select(func.count())
                .select_from(StockEntry)
                .where(StockEntry.warehouse_id == row.id)
            ),
        ),
        (
            "inventários associados",
            db.scalar(
                select(func.count())
                .select_from(StockInventory)
                .where(StockInventory.warehouse_id == row.id)
            ),
        ),
        ("documentos de compra associados", documents),
    ]


def entries(db, article_id, warehouse_id=None):
    query = select(StockEntry).where(StockEntry.article_id == article_id)
    if warehouse_id:
        query = query.where(StockEntry.warehouse_id == warehouse_id)
    return list(db.scalars(query.order_by(StockEntry.created_at, StockEntry.id)))


def balances(rows):
    values = defaultdict(Decimal)
    for row in rows:
        values[(row.warehouse_id, row.lot_id)] += row.quantity
    return values


def availability(history, allowed, lots, holds, active_warehouses=None):
    physical = balances([e for e in history if e.warehouse_id in allowed])
    reserved = ZERO
    for hold in holds:
        if hold.warehouse_id in allowed:
            physical[(hold.warehouse_id, hold.lot_id)] -= hold.quantity
            reserved += hold.quantity
    available = ZERO
    for (warehouse_id, lot_id), quantity in physical.items():
        if active_warehouses is not None and warehouse_id not in active_warehouses:
            continue
        lot = lots.get(lot_id)
        if not (lot and lot.expiry and lot.expiry < date.today().isoformat()):
            available += max(ZERO, quantity)
    return {"reserved_quantity": str(reserved), "available_quantity": str(available)}


def average(rows):
    """Unknown inbound cost taints the pool until exhausted. Cost snapshots never change."""
    quantity, cost, last = ZERO, None, None
    for row in rows:
        if row.kind in ("transfer_in", "transfer_out"):
            continue
        if row.kind == "valuation":
            if quantity > 0 and cost is not None:
                cost += D(json.loads(row.data).get("inventory_value_delta", "0")) / quantity
            continue
        if row.quantity > 0:
            if row.kind == "purchase":
                last = row.unit_cost
            cost = (
                row.unit_cost
                if quantity == 0
                else (quantity * cost + row.quantity * row.unit_cost) / (quantity + row.quantity)
                if quantity > 0 and cost is not None and row.unit_cost is not None
                else None
            )
        quantity += row.quantity
        if quantity <= 0:
            cost = None
    return cost, last


class ArticleAttachment(Strict):
    name: str = Field(default="", max_length=200)
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def https_url(cls, value):
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Indique um endereço HTTPS válido, sem credenciais.")
        return value


class ArticleInput(Strict):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    reference: str = Field(default="", max_length=100)
    barcodes: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default="", max_length=4000)
    internal_notes: str = Field(default="", max_length=4000)
    additional_photos: list[str] = Field(default_factory=list, max_length=10)
    attachments: list[ArticleAttachment] = Field(default_factory=list, max_length=20)
    photo: str = Field(default="", max_length=2000)
    family: str = Field(default="", max_length=100)
    category: str = Field(default="", max_length=100)
    brand: str = Field(default="", max_length=100)
    supplier_id: str | None = None
    supplier_reference: str = Field(default="", max_length=100)
    unit: str = Field(default="un", min_length=1, max_length=20)
    precision: int = Field(default=0, ge=0, le=6)
    vat_rate: Decimal = Field(default=D(23), ge=0, le=100, max_digits=6, decimal_places=3)
    sale_net: Decimal = Field(default=ZERO, ge=0, le=10**9, max_digits=18, decimal_places=6)
    minimum: Decimal = Field(default=ZERO, ge=0, le=10**12, max_digits=18, decimal_places=6)
    reorder: Decimal = Field(default=ZERO, ge=0, le=10**12, max_digits=18, decimal_places=6)
    # Stock máximo (0 = sem máximo); só informativo: assinala excesso de stock na grelha.
    maximum: Decimal = Field(default=ZERO, ge=0, le=10**12, max_digits=18, decimal_places=6)
    location: str = Field(default="", max_length=200)
    lots: bool = False
    expiry: bool = False
    # Serviço (portes, mão de obra, assinaturas…): conta nas compras/vendas, mas não movimenta stock.
    service: bool = False
    active: bool = True
    revision: int = 0

    @field_validator("code", "name", "unit")
    @classmethod
    def strip_required(cls, value):
        if not value.strip():
            raise ValueError("Preencha este campo.")
        return value.strip()

    @field_validator("photo")
    @classmethod
    def photo_url(cls, value):
        if value:
            ArticleAttachment.https_url(value)
        return value

    @field_validator("additional_photos")
    @classmethod
    def image_urls(cls, value):
        for url in value:
            if len(url) > 2000:
                raise ValueError("O endereço da imagem é demasiado longo.")
            ArticleAttachment.https_url(url)
        return value

    @field_validator("barcodes")
    @classmethod
    def barcode_list(cls, value):
        result = [v.strip() for v in value if v.strip()]
        if any(len(v) > 100 for v in result) or len(set(result)) != len(result):
            raise ValueError("Códigos de barras repetidos ou demasiado longos.")
        return result


class MovementInput(Strict):
    article_id: str
    warehouse_id: str
    target_id: str | None = None
    kind: Literal[
        "purchase",
        "sale",
        "customer_return",
        "supplier_return",
        "transfer",
        "loss",
        "expired",
        "adjustment",
        "opening",
    ]
    quantity: Decimal = Field(gt=0, le=10**12, max_digits=18, decimal_places=6)
    adjustment_out: bool = False
    unit_cost: Decimal | None = Field(default=None, ge=0, le=10**9, max_digits=18, decimal_places=8)
    net_sales: Decimal = Field(default=ZERO, ge=0, le=10**12, max_digits=18, decimal_places=6)
    direct_cost: Decimal = Field(default=ZERO, ge=0, le=10**9, max_digits=18, decimal_places=6)
    occurred_on: date = Field(default_factory=date.today)
    document: str = Field(default="", max_length=200)
    reason: str = Field(min_length=3, max_length=2000)
    request_key: str = Field(min_length=8, max_length=200)
    lot_id: str | None = None
    lot_number: str = Field(default="", max_length=100)
    expiry: date | None = None
    deadline: Literal["validade", "consumo_preferencial"] = "validade"
    supplier_id: str | None = None
    original_id: str | None = None
    override_reason: str = Field(default="", max_length=2000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("Indique um motivo com pelo menos três caracteres.")
        return value.strip()


def save_article(
    db, user, data, existing=None, *, catalogue=None, config=None, preview=False, flush=True
):
    config = settings(db) if config is None else config
    require(user, config, "adjust_stock")
    if existing:
        article(db, existing.id, True, stock=True)
        if existing.revision != data.revision:
            fail("O artigo foi alterado. Atualize a ficha antes de guardar.", 409)
        old = json.loads(existing.data)
        if existing.erp_id:
            # Artigo do ERP: só os dados de gestão local mudam; código, nome, estado e preço vêm do ERP.
            data = data.model_copy(
                update={
                    "code": existing.code,
                    "name": existing.name,
                    "active": existing.active,
                    "sale_net": D(old.get("sale_net", 0)),
                }
            )
    payload = data.model_dump(mode="json", exclude={"code", "name", "active", "revision"})
    if existing:
        if (
            D(old.get("sale_net", 0)) != data.sale_net
            or D(old.get("vat_rate", 23)) != data.vat_rate
        ):
            require(user, config, "change_price")
        if (old.get("lots") != data.lots or old.get("expiry") != data.expiry) and entries(
            db, existing.id
        ):
            field_fail(
                "lots" if old.get("lots") != data.lots else "expiry",
                "O controlo de lotes/validades não pode mudar num artigo com histórico.",
                409,
            )
        if data.service and not old.get("service") and entries(db, existing.id):
            field_fail(
                "service", "Um artigo com movimentos de stock não pode passar a serviço.", 409
            )
        if old.get("unit", "un") != data.unit and entries(db, existing.id):
            field_fail("unit", "A unidade não pode mudar num artigo com histórico.", 409)
        if any(
            abs(entry.quantity) != abs(entry.quantity).quantize(D(1).scaleb(-data.precision))
            for entry in entries(db, existing.id)
        ):
            field_fail(
                "precision",
                "A precisão indicada não permite representar as quantidades do histórico.",
                409,
            )
    elif data.sale_net:
        require(user, config, "change_price")
    if data.expiry and not data.lots:
        field_fail("expiry", "O controlo de validade exige lotes.")
    if data.service and (data.lots or data.expiry):
        field_fail("service", "Um serviço não controla lotes nem validades.")
    if data.reorder < data.minimum:
        field_fail("reorder", "O nível de reposição deve ser igual ou superior ao mínimo.")
    if data.maximum and data.maximum < max(data.reorder, data.minimum):
        field_fail(
            "maximum", "O stock máximo deve ser igual ou superior ao nível de reposição e ao mínimo."
        )
    if data.supplier_id:
        supplier = db.get(BusinessEntity, data.supplier_id)
        if not supplier or not supplier.is_supplier or supplier.removed:
            field_fail("supplier_id", "Selecione um fornecedor desta empresa.")
    if catalogue is None:
        # Sem ler nem descodificar a ficha de todos os artigos: o código compara-se em Python
        # (casefold, independente do locale da base) e os códigos de barras no PostgreSQL.
        others = select(StockArticle.id, StockArticle.code, StockArticle.name)
        if existing:
            others = others.where(StockArticle.id != existing.id)
        for _, code, name in db.execute(others):
            if code.casefold() == data.code.casefold():
                field_fail("code", f"Código já utilizado pelo artigo «{name}».", 409)
        if data.barcodes:
            name = db.scalar(
                others.with_only_columns(StockArticle.name)
                .where(cast(StockArticle.data, JSONB)["barcodes"].has_any(array(data.barcodes)))
                .limit(1)
            )
            if name:
                field_fail(
                    "barcodes", f"Código de barras já utilizado pelo artigo «{name}».", 409
                )
    for other in catalogue or []:
        if existing and other.id == existing.id:
            continue
        if other.code.casefold() == data.code.casefold():
            field_fail("code", f"Código já utilizado pelo artigo «{other.name}».", 409)
        if set(json.loads(other.data).get("barcodes", [])) & set(data.barcodes):
            field_fail(
                "barcodes", f"Código de barras já utilizado pelo artigo «{other.name}».", 409
            )
    row = existing or StockArticle(code=data.code, name=data.name)
    row.code, row.name, row.active, row.data = data.code, data.name, data.active, dump(payload)
    row.revision = (row.revision or 0) + 1
    if not preview:
        db.add(row)
        if flush:
            db.flush()
    return row


def post(db, user, data, authorize, *, source="manual", bypass=False):
    config = settings(db)
    if not bypass:
        require(user, config, "adjust_stock")
    product = article(db, data.article_id, True, stock=True)
    if not product.active:
        fail("Ative o artigo antes de registar movimentos.", 409)
    if json.loads(product.data).get("service"):
        fail(f"«{product.name}» é um serviço: não movimenta stock.", 409)
    origin = warehouse(db, user, data.warehouse_id, authorize)
    target = warehouse(db, user, data.target_id, authorize) if data.kind == "transfer" else None
    for item in (origin, target):
        if item is not None and not item.active:
            fail(
                f"O armazém «{item.name}» está inativo e não aceita novos movimentos. Reative-o em Tabelas → Armazéns.",
                409,
            )
    if target and target.id == origin.id:
        fail("Selecione outro armazém de destino.")
    if data.occurred_on > date.today():
        fail("Não pode registar movimentos de stock no futuro.")
    meta = json.loads(product.data)
    if data.quantity != data.quantity.quantize(D(1).scaleb(-meta.get("precision", 0))):
        fail("A quantidade não respeita a precisão definida no artigo.")
    request = data.model_dump(mode="json")
    # A repeated key is accepted only for exactly the same request.
    prior = list(db.scalars(select(StockEntry).where(StockEntry.request_key == data.request_key)))
    if prior:
        if json.loads(prior[0].data).get("request") != request:
            fail("Esta referência já identifica outro movimento. Consulte o histórico.", 409)
        return prior
    if data.document and source == "manual":
        # Prevent accidental duplicate import/manual lines of the same document/article/kind.
        for previous in entries(db, product.id):
            detail = json.loads(previous.data)
            if (
                previous.kind == data.kind
                and document_key(detail.get("document", "")) == document_key(data.document)
                and detail.get("origin") != "manual"
            ):
                fail("Este documento/artigo já foi integrado. Consulte o movimento de origem.", 409)
    if data.unit_cost is not None:
        require(user, config, "view_cost")
    if data.supplier_id:
        entity = db.get(BusinessEntity, data.supplier_id)
        if not entity or not entity.is_supplier or entity.removed:
            fail("Fornecedor inválido nesta empresa.")
    history = entries(db, product.id)
    cost, _ = average(history)
    inbound = data.kind in INCOMING or data.kind == "adjustment" and not data.adjustment_out
    original = None
    if data.original_id:
        if data.kind not in ("customer_return", "supplier_return"):
            fail("A ligação ao movimento original destina-se às devoluções.")
        original = db.get(StockEntry, data.original_id)
        expected = "sale" if data.kind == "customer_return" else "purchase"
        if not original or original.article_id != product.id or original.kind != expected:
            fail("O movimento original não corresponde ao artigo e à devolução.")
        warehouse(db, user, original.warehouse_id, authorize)
        returned = sum(
            (
                abs(e.quantity)
                for e in history
                if e.original_id == original.id and e.kind == data.kind
            ),
            ZERO,
        )
        if returned + data.quantity > abs(original.quantity):
            fail("A devolução excede a quantidade da operação original.")
    if inbound:
        cost = (
            original.unit_cost
            if original
            else data.unit_cost
            if data.unit_cost is not None
            else cost
            if data.kind == "adjustment"
            else None
        )
        if (
            cost is not None
            and not config["recoverable_vat"]
            and data.kind in ("purchase", "opening")
        ):
            cost *= 1 + D(meta.get("vat_rate", 0)) / 100
    lot = None
    chosen_lot = data.lot_id or (original.lot_id if original else None)
    if chosen_lot:
        lot = db.get(StockLot, chosen_lot)
        if not lot or lot.article_id != product.id:
            fail("O lote não pertence ao artigo.")
        if original and original.lot_id and original.lot_id != lot.id:
            fail("Selecione o lote do movimento original para esta devolução.")
    if inbound and meta.get("lots"):
        if not bypass:
            require(user, config, "manage_lots")
        if not lot:
            if not data.lot_number.strip():
                fail("Indique o número do lote.")
            lot = db.scalar(
                select(StockLot).where(
                    StockLot.article_id == product.id, StockLot.number == data.lot_number.strip()
                )
            )
            if lot and data.expiry and lot.expiry != data.expiry.isoformat():
                fail("O lote já existe com outra validade. Reveja a ficha do lote.", 409)
            if not lot:
                lot = StockLot(
                    article_id=product.id,
                    number=data.lot_number.strip(),
                    expiry=data.expiry.isoformat() if data.expiry else None,
                    data=dump(
                        {
                            "deadline": data.deadline,
                            "supplier_id": data.supplier_id,
                            "document": data.document,
                            "entry_date": data.occurred_on,
                            "unit_cost": cost,
                        }
                    ),
                )
                db.add(lot)
                db.flush()
    if not meta.get("lots") and (lot or data.lot_number):
        fail("Este artigo não utiliza lotes.")
    allocations = [(lot.id if lot else None, data.quantity)]
    available = balances(history)
    if not inbound:
        # Customer reservations do not create stock, but protect physical units
        # against every other outgoing path (including manual purchases/returns).
        from .models import CustomerOrderLine, CustomerOrderReservation

        holds = list(
            db.scalars(
                select(CustomerOrderReservation)
                .join(CustomerOrderLine, CustomerOrderLine.id == CustomerOrderReservation.line_id)
                .where(
                    CustomerOrderLine.article_id == product.id,
                    CustomerOrderReservation.warehouse_id == origin.id,
                    CustomerOrderReservation.quantity > 0,
                )
            )
        )
        for hold in holds:
            available[(origin.id, hold.lot_id)] -= hold.quantity
        if holds and data.quantity > sum(
            (max(ZERO, q) for (wh, _), q in available.items() if wh == origin.id), ZERO
        ):
            fail(
                "Existem unidades reservadas para pedidos de clientes. Liberte as reservas antes desta saída.",
                409,
            )
        candidates = []
        for (wh, lot_id), quantity in available.items():
            if wh != origin.id or quantity <= 0:
                continue
            batch = db.get(StockLot, lot_id) if lot_id else None
            # Evaluate expiry against today as well as movement date: backdating cannot bypass it.
            expired = (
                batch
                and batch.expiry
                and batch.expiry < max(date.today().isoformat(), data.occurred_on.isoformat())
            )
            if expired and data.kind not in ("loss", "expired", "supplier_return", "adjustment"):
                continue
            candidates.append(
                (batch.expiry if batch and batch.expiry else "9999-12-31", lot_id, quantity)
            )
        candidates.sort(key=lambda value: (value[0], value[1] or ""))
        if lot:
            if candidates and candidates[0][1] != lot.id and not (bypass and source == "inventory"):
                require(user, config, "override_fefo")
                if len(data.override_reason.strip()) < 3:
                    fail("Justifique a alteração da proposta FEFO.")
            candidates = [c for c in candidates if c[1] == lot.id]
        allocations, remaining = [], data.quantity
        for _, lot_id, quantity in candidates:
            used = min(quantity, remaining)
            allocations.append((lot_id, used))
            remaining -= used
            if remaining == 0:
                break
        if remaining:
            if (
                meta.get("lots")
                or not config["allow_negative"]
                or not permissions(user, config)["negative_stock"]
            ):
                fail(
                    "Stock disponível insuficiente, ou lote expirado. Registe uma entrada ou utilize perda/devolução quando aplicável.",
                    409,
                )
            allocations.append((None, remaining))
    if data.kind == "customer_return" and original:
        sales = -original.net_sales * data.quantity / abs(original.quantity)
    else:
        sales = (
            data.net_sales * (-1 if data.kind == "customer_return" else 1)
            if data.kind in ("sale", "customer_return")
            else ZERO
        )
    result = []
    for lot_id, quantity in allocations:
        common = {
            "article_id": product.id,
            "user_id": user.id,
            "lot_id": lot_id,
            "unit_cost": cost,
            "occurred_on": data.occurred_on.isoformat(),
            "request_key": data.request_key,
            "original_id": data.original_id,
            "data": dump(
                {
                    "origin": source,
                    "document": data.document,
                    "reason": data.reason,
                    "unit": meta.get("unit", "un"),
                    "override_reason": data.override_reason,
                    "supplier_id": data.supplier_id,
                    "request": request,
                }
            ),
        }
        row = StockEntry(
            **common,
            sequence=len(result),
            warehouse_id=origin.id,
            kind="transfer_out" if target else data.kind,
            quantity=quantity * (1 if inbound else -1),
            net_sales=sales * quantity / data.quantity,
            direct_cost=data.direct_cost * quantity / data.quantity,
        )
        result.append(row)
        if target:
            result.append(
                StockEntry(
                    **common,
                    sequence=len(result),
                    warehouse_id=target.id,
                    kind="transfer_in",
                    quantity=quantity,
                    net_sales=ZERO,
                    direct_cost=ZERO,
                )
            )
    db.add_all(result)
    db.flush()
    return result


def entry_view(row, cost_visible):
    detail = json.loads(row.data)
    return {
        "id": row.id,
        "article_id": row.article_id,
        "warehouse_id": row.warehouse_id,
        "lot_id": row.lot_id,
        "kind": row.kind,
        "quantity": str(row.quantity),
        "occurred_on": row.occurred_on,
        "created_at": row.created_at,
        "user_id": row.user_id,
        "original_id": row.original_id,
        "operation_id": row.request_key,
        "unit_cost": str(row.unit_cost) if cost_visible and row.unit_cost is not None else None,
        "net_sales": str(row.net_sales),
        "direct_cost": str(row.direct_cost) if cost_visible else None,
        **{
            k: detail.get(k)
            for k in (
                "origin",
                "document",
                "reason",
                "unit",
                "override_reason",
                "supplier_id",
                "procurement_id",
            )
        },
        "value_delta": detail.get("value_delta") if cost_visible else None,
        "inventory_value_delta": detail.get("inventory_value_delta") if cost_visible else None,
        "consumed_value_delta": detail.get("consumed_value_delta") if cost_visible else None,
    }


def article_view(
    db,
    row,
    allowed_warehouses,
    cost_visible,
    history=None,
    erp=None,
    erp_stock=False,
    available=None,
):
    data = json.loads(row.data)
    history = entries(db, row.id) if history is None else history
    visible = [e for e in history if e.warehouse_id in allowed_warehouses]
    # The cost pool is company-wide, while quantities and sales respect store access.
    cost, last = average(history)
    erp = erp or (db.get(WintouchProduct, row.erp_id) if row.erp_id else None)
    return {
        **data,
        "id": row.id,
        "code": erp.code if erp else row.code,
        "name": erp.name if erp else row.name,
        "active": erp.active if erp else row.active,
        "revision": row.revision,
        "sale_net": None if erp else data.get("sale_net", "0"),
        "erp_price": str(erp.sale_price) if erp and erp.sale_price is not None else None,
        "erp_price_basis": "IVA não identificado pelo conector" if erp else None,
        "origin": "ERP" if erp else "Local",
        "erp_id": row.erp_id,
        "synced_at": erp.synced_at if erp else None,
        # Stock dos artigos do ERP registado na IAZELA (o conector não lê quantidades).
        "stock_state": "serviço (sem stock)"
        if data.get("service")
        else "indisponível no conector"
        if erp and erp_stock
        else "movimentos locais",
        "quantity": None if erp and erp_stock else str(sum((e.quantity for e in visible), ZERO)),
        "reserved_quantity": None
        if erp and erp_stock
        else (available or {}).get("reserved_quantity"),
        "available_quantity": None
        if erp and erp_stock
        else (available or {}).get("available_quantity"),
        "average_cost": str(cost)
        if cost_visible and cost is not None and not (erp and erp_stock)
        else None,
        "last_cost": str(last if last is not None or not erp else erp.last_cost)
        if cost_visible and (last is not None or erp is not None and erp.last_cost is not None)
        else None,
    }


class WarehouseInput(Strict):
    store_id: str
    name: str = Field(min_length=1, max_length=100)


class RemoveWarehouse(Strict):
    confirmed: Literal[True]


class PriceInput(Strict):
    revision: int
    sale_net: Decimal = Field(ge=0, le=10**9, max_digits=18, decimal_places=6)


class SettingsInput(Strict):
    revision: int
    allow_negative: bool = False
    recoverable_vat: bool = True
    alerts: list[int] = Field(default_factory=lambda: [90, 60, 30, 7], min_length=1, max_length=12)
    permissions: dict[str, dict[str, bool]] = Field(default_factory=dict)
    saft_mapping: dict[str, str] = Field(default_factory=dict)
    control_start: date | None = None


class InventoryCreate(Strict):
    warehouse_id: str
    article_ids: list[str] = Field(default_factory=list, max_length=2000)
    category: str = ""


class CountLine(Strict):
    article_id: str
    lot_id: str | None = None
    counted: Decimal = Field(ge=0, le=10**12, max_digits=18, decimal_places=6)
    reason: str = Field(default="", max_length=2000)


class InventoryCount(Strict):
    revision: int
    lines: list[CountLine] = Field(max_length=5000)
    confirm: bool = False


def inventory_view(row):
    return {
        "id": row.id,
        "warehouse_id": row.warehouse_id,
        "status": row.status,
        "revision": row.revision,
        "created_at": row.created_at,
        "confirmed_at": row.confirmed_at,
        "lines": json.loads(row.data),
    }


def register_routes(app, current, authorize, scope, audit):
    def wh_ids(db, user):
        return [
            w.id
            for w in db.scalars(
                select(StockWarehouse).where(StockWarehouse.store_id.in_(scope(user, db)))
            )
        ]

    @app.get("/api/stock/catalog")
    def catalogue(
        user=Depends(current),
        db: Session = Depends(db_session),
        metadata_only: bool = False,
        page: int | None = Query(None, ge=1),
        size: int = Query(25, ge=1, le=100),
        q: str = Query("", max_length=200),
        family: str = "",
        category: str = "",
        brand: str = "",
        supplier_id: str = "",
        active: Literal["", "true", "false"] = "",
        origin: Literal["", "Local", "ERP"] = "",
        stock: Literal["", "positive", "zero", "low", "over", "unknown"] = "",
        expiry: Literal["", "near", "expired", "valid", "missing"] = "",
        sort: str = "name",
        direction: Literal["asc", "desc"] = "asc",
    ):
        config = settings(db)
        rights = permissions(user, config)
        allowed = wh_ids(db, user)
        authorized_warehouses = list(
            db.scalars(select(StockWarehouse).where(StockWarehouse.id.in_(allowed)))
        )
        active_warehouses = {w.id for w in authorized_warehouses if w.active}
        # Keep the legacy unpaged contract for stock workflows. The grid filters,
        # counts and pages metadata in SQL before loading any movement history.
        payload = cast(StockArticle.data, JSONB)
        external = WintouchProduct
        expressions = {
            "code": func.coalesce(external.code, StockArticle.code),
            "name": func.coalesce(external.name, StockArticle.name),
            "family": payload["family"].astext,
            "category": payload["category"].astext,
            "active": func.coalesce(external.active, StockArticle.active),
            "origin": case((external.id.is_not(None), "ERP"), else_="Local"),
        }
        if sort not in expressions:
            fail("Ordenação de artigos inválida.")
        query = select(StockArticle).outerjoin(external, external.id == StockArticle.erp_id)
        for key, value in (
            ("family", family),
            ("category", category),
            ("brand", brand),
            ("supplier_id", supplier_id),
        ):
            if value:
                query = query.where(payload[key].astext == value)
        if origin:
            query = query.where(expressions["origin"] == origin)
        if active:
            query = query.where(expressions["active"] == (active == "true"))
        if q.strip():
            query = query.outerjoin(
                BusinessEntity, BusinessEntity.id == payload["supplier_id"].astext
            )
            searchable = func.lower(
                func.concat_ws(
                    " ",
                    expressions["code"],
                    expressions["name"],
                    payload["reference"].astext,
                    payload["barcodes"].astext,
                    payload["supplier_reference"].astext,
                    payload["brand"].astext,
                    BusinessEntity.name,
                )
            )
            # Portable Portuguese search, including databases created with locale C.
            accented = "ÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜÇÑÝŸ"
            plain = "".join(normalize("NFD", char)[0] for char in accented)
            searchable = func.lower(
                func.translate(searchable, accented + accented.lower(), plain + plain.lower())
            )
            term = "".join(
                char for char in normalize("NFD", q.strip()) if not combining(char)
            ).lower()
            query = query.where(searchable.contains(term, autoescape=True))
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        total_all = db.scalar(select(func.count()).select_from(StockArticle))
        facets = {}
        for key in ("family", "category", "brand", "unit"):
            field = payload[key].astext
            facets[key] = list(
                db.scalars(
                    select(field).where(field.is_not(None), field != "").distinct().order_by(field)
                )
            )
        ordered = expressions[sort]
        ordered = func.lower(ordered) if sort != "active" else ordered
        query = query.order_by(
            ordered.asc().nulls_last() if direction == "asc" else ordered.desc().nulls_last(),
            StockArticle.id,
        )
        derived = bool(stock or expiry)
        current_page = min(page or 1, max(1, (total + size - 1) // size))
        if page is not None and not derived:
            query = query.offset((current_page - 1) * size).limit(size)
        selected = [] if metadata_only else list(db.scalars(query))
        identifiers = [row.id for row in selected]
        erp = (
            {
                row.id: row
                for row in db.scalars(
                    select(external).where(
                        external.id.in_([row.erp_id for row in selected if row.erp_id])
                    )
                )
            }
            if selected
            else {}
        )
        grouped = defaultdict(list)
        if identifiers:
            # Só leitura: linhas simples (mesmos atributos) em vez de objetos ORM, que custam
            # caro no catálogo completo.
            table = StockEntry.__table__
            for entry in db.execute(
                select(table)
                .where(table.c.article_id.in_(identifiers))
                .order_by(table.c.created_at, table.c.id)
            ):
                grouped[entry.article_id].append(entry)
        erp_stock = stock_from_erp(db)
        lot_map = (
            {
                lot.id: lot
                for lot in db.scalars(select(StockLot).where(StockLot.article_id.in_(identifiers)))
            }
            if identifiers
            else {}
        )
        holds = defaultdict(list)
        if identifiers:
            for hold, article_id in db.execute(
                select(CustomerOrderReservation, CustomerOrderLine.article_id)
                .join(CustomerOrderLine, CustomerOrderLine.id == CustomerOrderReservation.line_id)
                .where(
                    CustomerOrderLine.article_id.in_(identifiers),
                    CustomerOrderReservation.warehouse_id.in_(allowed),
                    CustomerOrderReservation.quantity > 0,
                )
            ):
                holds[article_id].append(hold)
        rows = []
        for row in selected:
            value = article_view(
                db,
                row,
                allowed,
                rights["view_cost"],
                grouped[row.id],
                erp.get(row.erp_id),
                erp_stock,
                availability(grouped[row.id], allowed, lot_map, holds[row.id], active_warehouses),
            )
            quantities = defaultdict(lambda: ZERO)
            for entry in grouped[row.id]:
                if entry.warehouse_id in active_warehouses and entry.lot_id:
                    quantities[(entry.warehouse_id, entry.lot_id)] += entry.quantity
            dates = [
                date.fromisoformat(str(lot_map[lot_id].expiry)[:10])
                for (_, lot_id), quantity in quantities.items()
                if quantity > 0 and lot_id in lot_map and lot_map[lot_id].expiry
            ]
            nearest = min(dates) if dates else None
            value["expiry_state"] = (
                (
                    "expired"
                    if nearest < now().astimezone(ZoneInfo("Europe/Lisbon")).date()
                    else "near"
                    if (nearest - now().astimezone(ZoneInfo("Europe/Lisbon")).date()).days
                    <= max(config["alerts"], default=90)
                    else "valid"
                )
                if nearest
                else "missing"
            )
            value["nearest_expiry"] = nearest
            quantity = value["available_quantity"]
            # Serviços não movimentam stock: ficam fora dos filtros de situação de stock.
            if stock and value.get("service"):
                continue
            if stock and not (
                stock == "unknown"
                and quantity is None
                or quantity is not None
                and (
                    stock == "low"
                    and D(quantity) < D(str(value.get("minimum", 0)))
                    or stock == "over"
                    and D(str(value.get("maximum") or 0)) > 0
                    and D(quantity) > D(str(value.get("maximum")))
                    or stock == "zero"
                    and D(quantity) <= 0
                    or stock == "positive"
                    and D(quantity) > 0
                )
            ):
                continue
            if expiry and value["expiry_state"] != expiry:
                continue
            rows.append(value)
        if derived and not metadata_only:
            total = len(rows)
            current_page = min(page or 1, max(1, (total + size - 1) // size))
            if page is not None:
                rows = rows[(current_page - 1) * size : current_page * size]
        return fast_json({
            "articles": rows,
            "total": total,
            "total_all": total_all,
            "page": current_page,
            "pages": max(1, (total + size - 1) // size),
            "size": size,
            "facets": facets,
            "preference_scope": f"{db.info.get('company', {}).get('id', 'local')}.{user.id}",
            "warehouses": [
                {"id": w.id, "store_id": w.store_id, "name": w.name, "active": w.active}
                for w in authorized_warehouses
            ],
            "suppliers": [
                {"id": e.id, "name": e.name}
                for e in db.scalars(
                    select(BusinessEntity).where(
                        BusinessEntity.is_supplier.is_(True), BusinessEntity.removed.is_(False)
                    )
                )
            ],
            "permissions": rights,
            "policy": policy(db),
            "settings": config
            if user.role == "administrador"
            else {
                k: config[k]
                for k in ("alerts", "allow_negative", "recoverable_vat", "control_start")
            },
            "users": [{"id": u.id, "name": u.name} for u in db.scalars(select(User))]
            if user.role == "administrador"
            else [],
        })

    @app.get("/api/stock/articles/{identifier}/details")
    def article_details(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        product = article(db, identifier)
        allowed = wh_ids(db, user)
        authorized_warehouses = list(
            db.scalars(select(StockWarehouse).where(StockWarehouse.id.in_(allowed)))
        )
        config = settings(db)
        rights = permissions(user, config)
        visible = [e for e in entries(db, product.id) if e.warehouse_id in allowed]
        unavailable = bool(product.erp_id and stock_from_erp(db))
        quantities = defaultdict(Decimal)
        for entry in visible:
            quantities[entry.warehouse_id] += entry.quantity
        last_purchase = next((e for e in reversed(visible) if e.kind == "purchase"), None)
        lots = {
            lot.id: lot
            for lot in db.scalars(select(StockLot).where(StockLot.article_id == product.id))
        }
        holds = list(
            db.scalars(
                select(CustomerOrderReservation)
                .join(CustomerOrderLine, CustomerOrderLine.id == CustomerOrderReservation.line_id)
                .where(
                    CustomerOrderLine.article_id == product.id,
                    CustomerOrderReservation.warehouse_id.in_(allowed),
                    CustomerOrderReservation.quantity > 0,
                )
            )
        )
        available = availability(
            visible, allowed, lots, holds, {w.id for w in authorized_warehouses if w.active}
        )
        erp = db.get(WintouchProduct, product.erp_id) if product.erp_id else None
        labels = {
            "stock_article_create": "Artigo criado",
            "stock_article_edit": "Ficha atualizada",
            "stock_article_price": "Preço atualizado",
        }
        history = list(
            db.scalars(
                select(Audit)
                .where(
                    Audit.target_id == product.id,
                    Audit.action.in_(labels),
                    (Audit.store_id.is_(None)) | (Audit.store_id.in_(scope(user, db))),
                )
                .order_by(Audit.created_at.desc(), Audit.id.desc())
                .limit(30)
            )
        )
        created = db.scalar(
            select(Audit)
            .where(
                Audit.target_id == product.id,
                Audit.action == "stock_article_create",
                (Audit.store_id.is_(None)) | (Audit.store_id.in_(scope(user, db))),
            )
            .order_by(Audit.created_at, Audit.id)
            .limit(1)
        )
        latest_sync = (
            db.scalar(
                select(WintouchSyncRun)
                .where(
                    WintouchSyncRun.enterprise_id == erp.enterprise_id,
                    WintouchSyncRun.resource == "products",
                )
                .order_by(WintouchSyncRun.started_at.desc())
                .limit(1)
            )
            if erp
            else None
        )
        authors = [h.user_id for h in history] + ([created.user_id] if created else [])
        names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(authors)))}

        def history_view(item):
            return (
                {
                    "id": item.id,
                    "label": labels[item.action],
                    "created_at": item.created_at,
                    "user_name": names.get(item.user_id),
                }
                if item
                else None
            )

        # Histórico do preço de venda, reconstituído da auditoria (edições da ficha e do preço).
        # Só o preço e o IVA saem da auditoria; o resto do registo fica privado.
        price_history = []
        if not product.erp_id:
            known = None
            for item in db.scalars(
                select(Audit)
                .where(
                    Audit.target_id == product.id,
                    Audit.action.in_(
                        ("stock_article_create", "stock_article_edit", "stock_article_price")
                    ),
                    (Audit.store_id.is_(None)) | (Audit.store_id.in_(scope(user, db))),
                )
                .order_by(Audit.created_at, Audit.id)
            ):
                detail = json.loads(item.detail or "{}")
                net = detail.get("after") if item.action == "stock_article_price" else detail.get("sale_net")
                if net is None:
                    continue
                vat = detail.get("vat_rate", known[1] if known else json.loads(product.data).get("vat_rate", 23))
                value = (D(str(net)), D(str(vat)))
                if value != known:
                    price_history.append((item, value))
                    known = value
            price_names = {
                u.id: u.name
                for u in db.scalars(
                    select(User).where(User.id.in_({i.user_id for i, _ in price_history}))
                )
            }
            price_history = [
                {
                    "created_at": item.created_at,
                    "user_name": price_names.get(item.user_id),
                    "sale_net": str(net),
                    "vat_rate": str(vat),
                }
                for item, (net, vat) in reversed(price_history[-20:])
            ]

        # Compras por fornecedor, a partir das entradas de compra (receções e movimentos manuais);
        # entradas substituídas ou anuladas por estorno (reversal_of) ficam de fora.
        replaced = {json.loads(e.data or "{}").get("reversal_of") for e in visible} - {None}
        by_supplier = {}
        for entry in visible:
            if entry.kind != "purchase" or entry.quantity <= 0 or entry.id in replaced:
                continue
            detail = json.loads(entry.data or "{}")
            group = by_supplier.setdefault(
                detail.get("supplier_id") or "",
                {"purchases": 0, "quantity": ZERO, "costs": [], "last": None},
            )
            group["purchases"] += 1
            group["quantity"] += entry.quantity
            if entry.unit_cost is not None:
                group["costs"].append((entry.quantity, entry.unit_cost))
            if group["last"] is None or entry.occurred_on >= group["last"][0].occurred_on:
                group["last"] = (entry, detail.get("document"))
        supplier_names = {
            e.id: e.name
            for e in db.scalars(
                select(BusinessEntity).where(BusinessEntity.id.in_([k for k in by_supplier if k]))
            )
        }
        main_supplier = json.loads(product.data).get("supplier_id")

        def supplier_view(supplier_id, group):
            last, document = group["last"]
            costs = group["costs"] if rights["view_cost"] else []
            weighted = sum((q for q, _ in costs), ZERO)
            return {
                "supplier_id": supplier_id or None,
                "supplier_name": supplier_names.get(supplier_id) or ("Sem fornecedor" if not supplier_id else "Fornecedor removido"),
                "main": bool(supplier_id) and supplier_id == main_supplier,
                "purchases": group["purchases"],
                "quantity": str(group["quantity"]),
                "last_date": last.occurred_on,
                "last_document": document,
                "last_cost": str(last.unit_cost)
                if rights["view_cost"] and last.unit_cost is not None
                else None,
                "min_cost": str(min(c for _, c in costs)) if costs else None,
                "max_cost": str(max(c for _, c in costs)) if costs else None,
                "average_cost": str((sum(q * c for q, c in costs) / weighted).quantize(D("0.000001")))
                if costs and weighted
                else None,
            }

        purchase_suppliers = sorted(
            (supplier_view(k, g) for k, g in by_supplier.items()),
            key=lambda s: s["last_date"],
            reverse=True,
        )

        return {
            "company_name": db.info.get("company", {}).get("name"),
            "reserved_quantity": None if unavailable else available["reserved_quantity"],
            "available_quantity": None if unavailable else available["available_quantity"],
            "expiry_alerts": [
                {
                    "number": lots[lot_id].number,
                    "expiry": lots[lot_id].expiry,
                    "quantity": str(quantity),
                }
                for (_, lot_id), quantity in balances(visible).items()
                if quantity > 0
                and lot_id in lots
                and lots[lot_id].expiry
                and (date.fromisoformat(lots[lot_id].expiry) - date.today()).days
                <= max(config["alerts"], default=90)
            ]
            if not unavailable
            else [],
            "warehouses": [
                {
                    "id": w.id,
                    "name": w.name,
                    "quantity": None if unavailable else str(quantities[w.id]),
                }
                for w in authorized_warehouses
            ],
            "last_purchase": {
                "date": last_purchase.occurred_on,
                "document": json.loads(last_purchase.data).get("document"),
                "unit_cost": str(last_purchase.unit_cost)
                if rights["view_cost"] and last_purchase.unit_cost is not None
                else None,
            }
            if last_purchase
            else None,
            "erp": {"external_id": erp.external_id, "enterprise_id": erp.enterprise_id}
            if erp
            else None,
            "sync_state": latest_sync.status if latest_sync else None,
            "created": history_view(created),
            "updated": history_view(history[0]) if history else None,
            # Only labels and authors: audit payloads may include costs and private data.
            "history": [history_view(h) for h in history],
            "price_history": price_history,
            "purchase_suppliers": purchase_suppliers,
        }

    @app.post("/api/stock/settings")
    def configure(data: SettingsInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        lock(db)
        config = settings(db)
        if config["revision"] != data.revision:
            fail("Os parâmetros mudaram. Atualize antes de guardar.", 409)
        if any(n < 1 or n > 3650 for n in data.alerts):
            fail("Os alertas devem estar entre 1 e 3650 dias.")
        for identifier, rights in data.permissions.items():
            if not db.get(User, identifier) or any(k not in PERMISSIONS for k in rights):
                fail("Utilizador ou permissão inválida nesta empresa.")
        for source, identifier in data.saft_mapping.items():
            article(db, identifier, True, stock=True)
            if not source or len(source) > 200:
                fail("Código de artigo SAF-T inválido.")
        row = db.get(StockSettings, "default") or StockSettings(id="default")
        row.data = dump(data.model_dump(mode="json", exclude={"revision"}))
        row.revision = config["revision"] + 1
        db.add(row)
        audit(db, user, "stock_settings", "default", data.model_dump(mode="json"))
        db.commit()
        return {"saved": True}

    @app.post("/api/stock/warehouses")
    def create_warehouse(
        data: WarehouseInput, user=Depends(current), db: Session = Depends(db_session)
    ):
        require(user, settings(db), "adjust_stock")
        authorize(user, db, data.store_id)
        lock(db)
        if not db.get(Store, data.store_id):
            fail("Estabelecimento não encontrado.", 404)
        name = data.name.strip()
        if not name or db.scalar(
            select(StockWarehouse).where(
                StockWarehouse.store_id == data.store_id, StockWarehouse.name == name
            )
        ):
            fail("Preencha um nome de armazém ainda não utilizado.")
        row = StockWarehouse(store_id=data.store_id, name=name)
        db.add(row)
        db.flush()
        audit(db, user, "stock_warehouse", row.id, {"name": name}, data.store_id)
        db.commit()
        return {"id": row.id}

    @app.post("/api/stock/warehouses/{warehouse_id}")
    def edit_warehouse(
        warehouse_id: str,
        data: WarehouseInput,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        require(user, settings(db), "adjust_stock")
        lock(db)
        row = warehouse(db, user, warehouse_id, authorize)
        authorize(user, db, data.store_id)
        if row.store_id != data.store_id:
            fail(
                "O estabelecimento de um armazém existente não pode ser alterado. Crie um armazém no destino e transfira o stock."
            )
        name = data.name.strip()
        if not name or db.scalar(
            select(StockWarehouse.id).where(
                StockWarehouse.store_id == row.store_id,
                StockWarehouse.name == name,
                StockWarehouse.id != row.id,
            )
        ):
            fail("Preencha um nome de armazém ainda não utilizado.")
        before = row.name
        row.name = name
        audit(
            db,
            user,
            "stock_warehouse_updated",
            row.id,
            {"before": before, "name": name},
            row.store_id,
        )
        db.commit()
        return {"id": row.id}

    @app.post("/api/stock/warehouses/{warehouse_id}/delete")
    def remove_warehouse(
        warehouse_id: str,
        data: RemoveWarehouse,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        require(user, settings(db), "adjust_stock")
        lock(db)
        row = warehouse(db, user, warehouse_id, authorize)
        reason = next((label for label, count in warehouse_dependencies(db, row) if count), None)
        if reason:
            fail(
                f"Não é possível remover o armazém «{row.name}»: tem {reason}. "
                "O histórico foi preservado. Pode editar o nome do armazém.",
                409,
            )
        audit(
            db,
            user,
            "stock_warehouse_deleted",
            row.id,
            {"name": row.name, "store_id": row.store_id},
            row.store_id,
        )
        db.delete(row)
        db.commit()
        return {"ok": True, "id": warehouse_id}

    @app.post("/api/stock/articles")
    def create_article(
        data: ArticleInput, user=Depends(current), db: Session = Depends(db_session)
    ):
        lock(db)
        row = save_article(db, user, data)
        audit(
            db,
            user,
            "stock_article_create",
            row.id,
            {"code": row.code, "sale_net": str(data.sale_net), "vat_rate": str(data.vat_rate)},
        )
        db.commit()
        return {"id": row.id}

    @app.put("/api/stock/articles/{identifier}")
    def edit_article(
        identifier: str,
        data: ArticleInput,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        lock(db)
        row = save_article(db, user, data, article(db, identifier, True, stock=True))
        audit(db, user, "stock_article_edit", row.id, data.model_dump(mode="json"))
        db.commit()
        return {"id": row.id}

    @app.delete("/api/stock/articles/{identifier}")
    def delete_article(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        require(user, settings(db), "adjust_stock")
        lock(db)
        row = article(db, identifier, True)
        if (
            entries(db, row.id)
            or db.scalar(select(StockLot).where(StockLot.article_id == row.id))
            or any(identifier in inv.data for inv in db.scalars(select(StockInventory)))
            or identifier in dump(settings(db)["saft_mapping"])
            or db.scalar(
                select(CustomerOrderLine.id)
                .where(CustomerOrderLine.article_id == identifier)
                .limit(1)
            )
            or db.scalar(
                select(OrderReplenishmentPolicy.id)
                .where(OrderReplenishmentPolicy.article_id == identifier)
                .limit(1)
            )
            or any(
                identifier in simulation.data
                for simulation in db.scalars(select(CustomerPurchaseSimulation))
            )
        ):
            fail(
                "O artigo tem histórico ou associações. Desative-o para preservar a rastreabilidade.",
                409,
            )
        audit(db, user, "stock_article_delete", row.id, {"code": row.code, "name": row.name})
        db.delete(row)
        db.commit()
        return {"deleted": True}

    @app.post("/api/stock/articles/{identifier}/price")
    def edit_price(
        identifier: str, data: PriceInput, user=Depends(current), db: Session = Depends(db_session)
    ):
        require(user, settings(db), "change_price")
        lock(db)
        row = article(db, identifier, True)
        if row.revision != data.revision:
            fail("O artigo mudou. Atualize antes de guardar o preço.", 409)
        old = json.loads(row.data)
        audit(
            db,
            user,
            "stock_article_price",
            row.id,
            {"before": old.get("sale_net"), "after": str(data.sale_net)},
        )
        row.data, row.revision = dump({**old, "sale_net": str(data.sale_net)}), row.revision + 1
        db.commit()
        return {"saved": True}

    @app.post("/api/stock/movements")
    def movement(data: MovementInput, user=Depends(current), db: Session = Depends(db_session)):
        lock(db)
        rows = post(db, user, data, authorize)
        audit(
            db,
            user,
            "stock_movement",
            rows[0].id,
            {"kind": data.kind, "quantity": str(data.quantity)},
            db.get(StockWarehouse, data.warehouse_id).store_id,
        )
        db.commit()
        return {"ids": [r.id for r in rows]}

    @app.get("/api/stock/movements")
    def ledger(
        article_id: str = "",
        warehouse_id: str = "",
        start: date | None = None,
        end: date | None = None,
        page: int = Query(default=1, ge=1),
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        query = select(StockEntry).where(StockEntry.warehouse_id.in_(wh_ids(db, user)))
        if article_id:
            query = query.where(StockEntry.article_id == article_id)
        if warehouse_id:
            warehouse(db, user, warehouse_id, authorize)
            query = query.where(StockEntry.warehouse_id == warehouse_id)
        if start:
            query = query.where(StockEntry.occurred_on >= start.isoformat())
        if end:
            query = query.where(StockEntry.occurred_on <= end.isoformat())
        rows = list(
            db.scalars(
                query.order_by(StockEntry.created_at.desc(), StockEntry.id.desc())
                .offset((page - 1) * 50)
                .limit(51)
            )
        )
        return {
            "rows": [
                entry_view(r, permissions(user, settings(db))["view_cost"]) for r in rows[:50]
            ],
            "more": len(rows) > 50,
        }

    @app.get("/api/stock/lots")
    def lots(
        article_id: str = "",
        warehouse_id: str = "",
        state: str = "",
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        allowed = wh_ids(db, user)
        if warehouse_id:
            warehouse(db, user, warehouse_id, authorize)
            allowed = [warehouse_id]
        history = defaultdict(list)
        visible = []
        for entry in db.scalars(select(StockEntry).order_by(StockEntry.created_at, StockEntry.id)):
            history[entry.article_id].append(entry)
            if entry.warehouse_id in allowed:
                visible.append(entry)
        values = balances(visible)
        batches = {lot.id: lot for lot in db.scalars(select(StockLot))}
        config = settings(db)
        result = []
        for (wh, lot_id), quantity in values.items():
            if not lot_id or quantity == 0:
                continue
            lot = batches[lot_id]
            if article_id and lot.article_id != article_id:
                continue
            days = (date.fromisoformat(lot.expiry) - date.today()).days if lot.expiry else None
            status = (
                "missing"
                if days is None
                else "expired"
                if days < 0
                else "near"
                if days <= max(config["alerts"], default=90)
                else "valid"
            )
            if state and status != state:
                continue
            cost, _ = average(history[lot.article_id])
            detail = json.loads(lot.data)
            if not permissions(user, config)["view_cost"]:
                detail.pop("unit_cost", None)
            result.append(
                {
                    "id": lot.id,
                    "article_id": lot.article_id,
                    "warehouse_id": wh,
                    "number": lot.number,
                    "expiry": lot.expiry,
                    "quantity": str(quantity),
                    "days": days,
                    "state": status,
                    **detail,
                    "alert": min(
                        (n for n in config["alerts"] if days is not None and days <= n),
                        default=None,
                    ),
                    "cost_value": str(quantity * cost)
                    if permissions(user, config)["view_cost"] and cost is not None
                    else None,
                }
            )
        return {"rows": sorted(result, key=lambda r: r["expiry"] or "9999")}

    @app.post("/api/stock/inventories")
    def create_inventory(
        data: InventoryCreate, user=Depends(current), db: Session = Depends(db_session)
    ):
        require(user, settings(db), "adjust_stock")
        lock(db)
        warehouse(db, user, data.warehouse_id, authorize)
        lines = []
        # Artigos do ERP entram no inventário quando o stock é registado na IAZELA.
        candidates = select(StockArticle).where(StockArticle.active.is_(True))
        if stock_from_erp(db):
            candidates = candidates.where(StockArticle.erp_id.is_(None))
        for product in db.scalars(candidates):
            if json.loads(product.data).get("service"):
                continue  # Serviços não têm stock para contar.
            meta = json.loads(product.data)
            if (
                data.article_ids
                and product.id not in data.article_ids
                or data.category
                and meta.get("category") != data.category
            ):
                continue
            values = balances(entries(db, product.id, data.warehouse_id))
            if not meta.get("lots"):
                values.setdefault((data.warehouse_id, None), ZERO)
            for (_, lot_id), qty in values.items():
                lines.append(
                    {
                        "article_id": product.id,
                        "lot_id": lot_id,
                        "expected": str(qty),
                        "counted": None,
                        "reason": "",
                    }
                )
        if not lines:
            fail(
                "Não existem posições de stock para os artigos selecionados. Registe os lotes através de uma entrada inicial."
            )
        row = StockInventory(warehouse_id=data.warehouse_id, user_id=user.id, data=dump(lines))
        db.add(row)
        db.flush()
        audit(db, user, "stock_inventory_start", row.id, {"positions": len(lines)})
        db.commit()
        return inventory_view(row)

    @app.get("/api/stock/inventories")
    def inventories(user=Depends(current), db: Session = Depends(db_session)):
        return {
            "rows": [
                inventory_view(row)
                for row in db.scalars(
                    select(StockInventory)
                    .where(StockInventory.warehouse_id.in_(wh_ids(db, user)))
                    .order_by(StockInventory.created_at.desc())
                )
            ]
        }

    @app.post("/api/stock/inventories/{identifier}")
    def count_inventory(
        identifier: str,
        data: InventoryCount,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        lock(db)
        config = settings(db)
        require(user, config, "confirm_inventory" if data.confirm else "adjust_stock")
        row = db.get(StockInventory, identifier)
        if not row:
            fail("Inventário não encontrado.", 404)
        warehouse(db, user, row.warehouse_id, authorize)
        if row.status == "confirmed":
            if data.confirm:
                return inventory_view(row)
            fail("O inventário já foi confirmado.", 409)
        if data.revision != row.revision:
            fail("O inventário mudou. Atualize antes de guardar.", 409)
        baseline = json.loads(row.data)
        positions = {(v["article_id"], v["lot_id"]): v for v in baseline}
        seen = set()
        for counted in data.lines:
            key = (counted.article_id, counted.lot_id)
            if key not in positions or key in seen:
                fail("Posição de contagem desconhecida ou repetida.")
            seen.add(key)
            product = article(db, counted.article_id, True, stock=True)
            precision = json.loads(product.data).get("precision", 0)
            if counted.counted != counted.counted.quantize(D(1).scaleb(-precision)):
                fail("A contagem não respeita a precisão do artigo.")
            positions[key].update(counted=str(counted.counted), reason=counted.reason)
        if data.confirm:
            for index, line in enumerate(baseline):
                if line["counted"] is None:
                    fail("Preencha todas as quantidades antes de confirmar.")
                delta = D(line["counted"]) - D(line["expected"])
                if delta:
                    if len(line["reason"].strip()) < 3:
                        fail("Justifique cada diferença do inventário.")
                    # Apply only the snapshot difference; intervening receipts/exits remain intact.
                    post(
                        db,
                        user,
                        MovementInput(
                            article_id=line["article_id"],
                            warehouse_id=row.warehouse_id,
                            lot_id=line["lot_id"],
                            kind="adjustment",
                            adjustment_out=delta < 0,
                            quantity=abs(delta),
                            document="Inventário " + row.id,
                            reason=line["reason"],
                            request_key=f"inventory:{row.id}:{index}",
                        ),
                        authorize,
                        source="inventory",
                        bypass=True,
                    )
            row.status, row.confirmed_at = "confirmed", now()
        row.data, row.revision = dump(baseline), row.revision + 1
        audit(
            db,
            user,
            "stock_inventory_confirm" if data.confirm else "stock_inventory_save",
            row.id,
            {"positions": len(baseline)},
        )
        db.commit()
        return inventory_view(row)

    from .inventory_reports import register_reports

    register_reports(app, current, authorize, scope)
    from .inventory_imports import register_imports

    register_imports(app, current, authorize, scope, audit)
    from .saft_articles import register as register_saft_articles

    register_saft_articles(app, current, authorize, audit)
