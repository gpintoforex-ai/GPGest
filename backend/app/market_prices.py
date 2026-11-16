"""Preços de mercado de um artigo: o que custa no KuantoKusta ou noutras lojas.

O KuantoKusta não tem API pública de consulta de preços (a API oficial é só para vendedores do
marketplace) e bloqueia leituras automáticas. A ficha abre a pesquisa no site e o utilizador
regista o que vê: loja, preço com IVA, data e ligação. Os valores ficam em cêntimos.

Com a ligação da página da própria loja, o preço lê-se automaticamente dos dados de produto que a
loja publica (schema.org, product:price): «Ler preço da ligação» preenche o registo e «Atualizar
preços das lojas» regista uma observação nova por loja (o histórico mantém-se). Só a pedido, e só se o
robots.txt do site o permitir (agente «IAZELA»).
"""

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import current, record
from .db import db_session
from .inventory import ArticleAttachment, article, fail, permissions, require, settings
from .models import ArticleMarketPrice, User, WintouchProduct
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


def own_price_cents(db, row):
    """PVP com IVA: linha de preço 1 da Wintouch ou preço do artigo local."""
    if row.erp_id:
        product = db.get(WintouchProduct, row.erp_id)
        if product is None:
            return None
        if product.sale_price_vat is not None:
            return cents(product.sale_price_vat) if product.sale_price_vat > 0 else None
        if product.sale_price is None or product.sale_price <= 0 or product.vat_rate is None:
            return None
        return cents(product.sale_price * (1 + product.vat_rate / 100))
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
        "summary": summary(rows, own_price_cents(db, product)),
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


# --- Leitura automática do preço na página da loja ------------------------------------------------------------
# As lojas publicam o preço para o Google em dados estruturados (schema.org Product/Offer em JSON-LD) ou em
# etiquetas meta (product:price:amount, og:price:amount, itemprop="price"). O KuantoKusta bloqueia leituras
# automáticas: para ele a ligação abre-se no navegador e o preço regista-se à mão.

BLOCKED_HOSTS = ("kuantokusta.pt",)
AUTO_NOTE = "Lido automaticamente da página da loja"


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.meta, self.itemprops, self._ld, self._buffer = [], {}, [], False, []

    def handle_starttag(self, tag, attrs):
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "script" and values.get("type", "").strip().lower() == "application/ld+json":
            self._ld, self._buffer = True, []
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or values.get("itemprop") or "").strip().lower()
            if key and values.get("content"):
                self.meta.setdefault(key, values["content"])
        elif values.get("itemprop") == "price" and values.get("content"):
            self.itemprops.append(values["content"])

    def handle_data(self, data):
        if self._ld:
            self._buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._ld:
            self.blocks.append("".join(self._buffer))
            self._ld = False


def amount(value):
    """Preço de texto («23.48», «23,48», «1.234,50», 23.48) em Decimal, ou None."""
    text = str(value if value is not None else "").strip().replace("\u00a0", "").replace(" ", "").replace("€", "")
    if not text:
        return None
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal == "," else ",", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except ArithmeticError:
        return None
    return number if 0 < number < 10**6 else None


def _offers(node):
    """Ofertas de produtos em JSON-LD (Product.offers, @graph, listas, AggregateOffer)."""
    if isinstance(node, list):
        for item in node:
            yield from _offers(item)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _offers(node["@graph"])
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "offers" in node and ("Product" in types or not node.get("@type")):
            offers = node["offers"]
            for offer in offers if isinstance(offers, list) else [offers]:
                if isinstance(offer, dict):
                    yield offer
        elif "Offer" in types or "AggregateOffer" in types:
            yield node


def page_price(html):
    """(preço com IVA, nome da loja) lidos da página, ou (None, nome)."""
    parser = _Page()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - HTML inválido: usa-se o que foi lido até aí
        pass
    site = parser.meta.get("og:site_name", "").strip()
    for block in parser.blocks:
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for offer in _offers(data):
            spec = offer.get("priceSpecification")
            spec = spec[0] if isinstance(spec, list) and spec else spec
            spec = spec if isinstance(spec, dict) else {}
            value = offer.get("price", offer.get("lowPrice", spec.get("price")))
            currency = str(offer.get("priceCurrency") or spec.get("priceCurrency") or "EUR")
            price = amount(value)
            if price is not None and currency.upper() == "EUR":
                return price, site
    currency = (parser.meta.get("product:price:currency") or parser.meta.get("og:price:currency") or "EUR").upper()
    for key in ("product:price:amount", "og:price:amount", "price"):
        price = amount(parser.meta.get(key))
        if price is not None and currency == "EUR":
            return price, site
    for value in parser.itemprops:
        price = amount(value)
        if price is not None:
            return price, site
    return None, site


def blocked(url):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == item or host.endswith("." + item) for item in BLOCKED_HOSTS)


def read_page(url):
    """Lê o preço com IVA de uma página de produto. Devolve {price, store_name, url}."""
    from . import web_fetch

    url = url.strip()
    if blocked(url):
        fail("O KuantoKusta não permite leitura automática: abra a página e registe o preço, ou use a ligação da "
             "própria loja.")
    if not web_fetch.robots_allow(url):
        fail("O site da loja não permite leitura automática desta página (robots.txt). Registe o preço à mão.", 403)
    raw, final = web_fetch.get(url, accept="text/html,application/xhtml+xml", limit=3 * 1024 * 1024,
                               what="da loja", too_big="A página da loja é demasiado grande para ler.", timeout=15)
    price, site = page_price(raw.decode("utf-8", errors="replace"))
    if price is None:
        fail("A página não indica o preço de forma legível (dados de produto). Registe o preço à mão.")
    host = (urlsplit(final).hostname or "").lower().removeprefix("www.")
    return {"price": str(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), "store_name": (site or host)[:120],
            "url": final}


class PageInput(Strict):
    url: str = Field(min_length=1, max_length=2000)


@router.post("/api/stock/articles/{identifier}/market-prices/read")
def read_market_price(identifier: str, data: PageInput, user=Depends(current), db: Session = Depends(db_session)):
    """Lê o preço da ligação para preencher o registo (não grava nada)."""
    require(user, settings(db), "change_price")
    article(db, identifier)
    return read_page(data.url)


@router.post("/api/stock/articles/{identifier}/market-prices/refresh")
def refresh_market_prices(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    """Atualiza o preço de cada loja com ligação: lê a página e regista uma observação nova (o histórico mantém-se)."""
    require(user, settings(db), "change_price")
    product = article(db, identifier)
    latest = {}
    for row in db.scalars(select(ArticleMarketPrice).where(ArticleMarketPrice.article_id == product.id).order_by(
            ArticleMarketPrice.observed_on.desc(), ArticleMarketPrice.created_at.desc(), ArticleMarketPrice.id)):
        latest.setdefault(row.store_name.casefold(), row)
    today = datetime.now(LISBON).date().isoformat()
    results = []
    for row in latest.values():
        item = {"store_name": row.store_name, "previous_cents": row.price_cents, "price_cents": None, "error": ""}
        if not row.url:
            results.append({**item, "state": "skipped", "error": "Sem ligação para a página da loja."})
            continue
        if blocked(row.url):
            results.append({**item, "state": "skipped", "error": "O KuantoKusta não permite leitura automática."})
            continue
        try:
            found = read_page(row.url)
        except HTTPException as exc:
            # 403: o site não permite leitura automática (robots.txt) — fica por registar à mão, não é um erro.
            results.append({**item, "state": "skipped" if exc.status_code == 403 else "failed", "error": str(exc.detail)})
            continue
        price = cents(Decimal(found["price"]))
        item["price_cents"] = price
        if price == row.price_cents and row.observed_on == today:
            results.append({**item, "state": "unchanged"})
            continue
        db.add(ArticleMarketPrice(article_id=product.id, source=row.source, store_name=row.store_name,
                                  price_cents=price, observed_on=today, url=row.url, notes=AUTO_NOTE, user_id=user.id))
        results.append({**item, "state": "updated" if price != row.price_cents else "confirmed"})
    if any(r["state"] in ("updated", "confirmed") for r in results):
        db.flush()
        record(db, user, "stock_article_market_price_refresh", product.id,
               {"lojas": [{k: r[k] for k in ("store_name", "state", "previous_cents", "price_cents")} for r in results]})
    db.commit()
    return {**listing(db, user, product), "refresh": results}
