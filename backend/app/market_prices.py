"""Preços de mercado de um artigo: o que custa no KuantoKusta ou noutras lojas.

O KuantoKusta não tem API pública de consulta de preços (a API oficial é só para vendedores do
marketplace) e bloqueia leituras automáticas. A ficha abre a pesquisa no site e o utilizador
regista o que vê: loja, preço com IVA, data e ligação. Os valores ficam em cêntimos.
"""

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import current, record
from .db import db_session
from .inventory import ArticleAttachment, article, fail, permissions, require, settings
from .models import ArticleMarketPrice, User
from .schemas import Strict

router = APIRouter()
LISBON = ZoneInfo("Europe/Lisbon")
SEARCH_URL = "https://www.kuantokusta.pt/search?q="


class MarketPriceInput(Strict):
    source: Literal["kuantokusta", "outro"] = "kuantokusta"
    store_name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(gt=0, le=10**7, max_digits=12, decimal_places=2)
    observed_on: date
    url: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=300)

    @field_validator("store_name")
    @classmethod
    def store(cls, value):
        if not value.strip():
            raise ValueError("Indique a loja.")
        return " ".join(value.split())

    @field_validator("url")
    @classmethod
    def link(cls, value):
        value = value.strip()
        if value:
            ArticleAttachment.https_url(value)
        return value

    @field_validator("observed_on")
    @classmethod
    def not_future(cls, value):
        if value > datetime.now(LISBON).date():
            raise ValueError("A data não pode ser futura.")
        return value


def cents(value):
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def own_price_cents(row):
    """Preço de venda com IVA do artigo; None nos artigos do ERP (IVA do preço não identificado)."""
    if row.erp_id:
        return None
    data = json.loads(row.data or "{}")
    net = Decimal(str(data.get("sale_net") or 0))
    if net <= 0:
        return None
    return cents(net * (1 + Decimal(str(data.get("vat_rate", 23))) / 100))


def search_url(row):
    data = json.loads(row.data or "{}")
    term = next((b for b in data.get("barcodes", []) if b), "") or row.name
    return SEARCH_URL + quote(term)


def summary(rows, own):
    """Compara com o preço mais recente de cada loja (as observações antigas ficam no histórico)."""
    latest = {}
    for row in rows:
        latest.setdefault(row.store_name.casefold(), row)
    current_prices = sorted(r.price_cents for r in latest.values())
    if not current_prices:
        return {"stores": 0, "lowest_cents": None, "highest_cents": None, "average_cents": None,
                "own_cents": own, "cheaper_stores": None, "lowest_store": None}
    lowest = min(latest.values(), key=lambda r: (r.price_cents, r.store_name.casefold()))
    return {
        "stores": len(current_prices),
        "lowest_cents": current_prices[0],
        "highest_cents": current_prices[-1],
        "average_cents": cents(Decimal(sum(current_prices)) / 100 / len(current_prices)),
        "own_cents": own,
        # Lojas com preço abaixo do da loja: o «posicionamento» do artigo.
        "cheaper_stores": None if own is None else sum(p < own for p in current_prices),
        "lowest_store": lowest.store_name,
    }


def view(row, names, latest_ids):
    return {
        "id": row.id,
        "source": row.source,
        "store_name": row.store_name,
        "price_cents": row.price_cents,
        "observed_on": row.observed_on,
        "url": row.url,
        "notes": row.notes,
        "user_name": names.get(row.user_id, ""),
        "created_at": row.created_at,
        "current": row.id in latest_ids,
    }


def listing(db, user, product):
    rows = list(
        db.scalars(
            select(ArticleMarketPrice)
            .where(ArticleMarketPrice.article_id == product.id)
            .order_by(
                ArticleMarketPrice.observed_on.desc(),
                ArticleMarketPrice.created_at.desc(),
                ArticleMarketPrice.id,
            )
        )
    )
    latest_ids = {}
    for row in rows:
        latest_ids.setdefault(row.store_name.casefold(), row.id)
    names = {u.id: u.name for u in db.scalars(select(User))}
    return {
        "prices": [view(r, names, set(latest_ids.values())) for r in rows],
        "summary": summary(rows, own_price_cents(product)),
        "search_url": search_url(product),
        "can_edit": permissions(user, settings(db))["change_price"],
    }


@router.get("/api/stock/articles/{identifier}/market-prices")
def market_prices(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    return listing(db, user, article(db, identifier))


@router.post("/api/stock/articles/{identifier}/market-prices")
def add_market_price(
    identifier: str,
    data: MarketPriceInput,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    require(user, settings(db), "change_price")
    product = article(db, identifier)
    row = ArticleMarketPrice(
        article_id=product.id,
        source=data.source,
        store_name=data.store_name,
        price_cents=cents(data.price),
        observed_on=data.observed_on.isoformat(),
        url=data.url,
        notes=data.notes.strip(),
        user_id=user.id,
    )
    db.add(row)
    db.flush()
    record(
        db,
        user,
        "stock_article_market_price",
        product.id,
        {"loja": row.store_name, "preco_cents": row.price_cents, "data": row.observed_on},
    )
    db.commit()
    return listing(db, user, product)


@router.delete("/api/stock/articles/{identifier}/market-prices/{price_id}")
def remove_market_price(
    identifier: str, price_id: str, user=Depends(current), db: Session = Depends(db_session)
):
    require(user, settings(db), "change_price")
    product = article(db, identifier)
    row = db.get(ArticleMarketPrice, price_id)
    if not row or row.article_id != product.id:
        fail("Preço de mercado não encontrado neste artigo.", 404)
    record(
        db,
        user,
        "stock_article_market_price_removed",
        product.id,
        {"loja": row.store_name, "preco_cents": row.price_cents, "data": row.observed_on},
    )
    db.delete(row)
    db.commit()
    return listing(db, user, product)
