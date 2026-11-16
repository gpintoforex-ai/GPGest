"""Preço de venda (PVP, linha de preço 1) de um artigo da Wintouch alterado na ficha do artigo.

- O preço é enviado à Wintouch pelo mesmo caminho da revisão de preços (`wintouch_price_write`): um artigo de
  cada vez, só dado como gravado depois de lido de volta na Wintouch. Sem confirmação, nada muda no IAZELA e
  pode repetir-se com segurança.
- Confirmado, o preço local do artigo ERP passa a ser o lido da Wintouch; a publicação na loja online (sem preço
  próprio do canal) fica com conteúdo novo e a loja recebe o preço na sincronização seguinte.
- Exige a permissão de alterar preços, administrador, ligação Wintouch ativa com escrita de preços autorizada e
  empresa real (nunca a demonstração). Tudo na auditoria.
"""

from decimal import ROUND_HALF_UP
from decimal import Decimal as D

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import current, record
from .db import db_session
from .models import WebshopConnection, WebshopPublication, WintouchProduct
from .schemas import Strict

router = APIRouter()


def fail(message, status=422):
    raise HTTPException(status, message)


def money(value):
    return str(value) if value is not None else None


def erp_article(db, user, identifier):
    from . import inventory as stock

    stock.require(user, stock.settings(db), "change_price")
    row = stock.article(db, identifier)
    if not row.erp_id:
        fail("Artigo local: o preço altera-se nos campos de venda da ficha.", 409)
    product = db.get(WintouchProduct, row.erp_id)
    if not product:
        fail("O artigo da Wintouch não está sincronizado. Sincronize os artigos antes de alterar o preço.", 409)
    return row, product


def channels(db, article_id):
    """Publicações na loja online e se usam o preço base (seguem o PVP) ou um preço próprio do canal."""
    rows = db.execute(select(WebshopPublication, WebshopConnection.name)
                      .join(WebshopConnection, WebshopConnection.id == WebshopPublication.connection_id)
                      .where(WebshopPublication.article_id == article_id))
    return [{"connection": name, "state": pub.state, "own_price": pub.price_net is not None,
             "price_net": money(pub.price_net)} for pub, name in rows]


def state(db, user, row, product):
    from .wintouch_price_write import availability

    available, reason = availability(db, user)
    if available and product.vat_rate is None:
        available, reason = False, "A Wintouch não indica a taxa de IVA deste artigo: não é possível calcular o preço."
    return {"available": available, "reason": reason, "net": money(product.sale_price),
            "gross": money(product.sale_price_vat), "vat_rate": money(product.vat_rate),
            "channels": channels(db, row.id)}


@router.get("/api/stock/articles/{identifier}/erp-price")
def get_price(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    row, product = erp_article(db, user, identifier)
    return state(db, user, row, product)


class ErpPrice(Strict):
    gross: D = Field(gt=0, le=10**7, max_digits=12, decimal_places=2)
    # Preço com IVA que a ficha mostrava: se a Wintouch mudou entretanto, não se sobrepõe sem rever.
    expected_gross: D | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)


@router.post("/api/stock/articles/{identifier}/erp-price")
def set_price(identifier: str, data: ErpPrice, user=Depends(current), db: Session = Depends(db_session)):
    from . import inventory as stock
    from .wintouch_price_write import push_prices

    stock.lock(db)
    row, product = erp_article(db, user, identifier)
    current_state = state(db, user, row, product)
    if not current_state["available"]:
        fail(current_state["reason"], 409)
    before_net, before_gross = product.sale_price, product.sale_price_vat
    if data.expected_gross is not None and (before_gross is None or abs(before_gross - data.expected_gross) > D("0.005")):
        fail("O preço na Wintouch mudou entretanto. Atualize a ficha e reveja o preço antes de gravar.", 409)
    net = (data.gross / (1 + product.vat_rate / 100)).quantize(D("0.0001"), rounding=ROUND_HALF_UP)
    outcome = push_prices(db, user, [{"product": product, "net": net, "gross": data.gross}])[product.id]
    record(db, user, "wintouch_price_write", row.id,
           {"erp_id": product.external_id, "before_net": money(before_net), "before_gross": money(before_gross),
            "after_net": str(outcome["net"]), "after_gross": str(outcome["gross"]), "state": outcome["state"],
            "error": outcome["error"], "origin": "ficha_do_artigo"})
    db.commit()
    return {"state": outcome["state"], "error": outcome["error"], **state(db, user, row, product)}
