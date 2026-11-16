"""Publicação do catálogo IAZELA na loja online (Raizpura/WooCommerce).

O IAZELA é a origem do catálogo publicado: o operador escolhe os artigos, o SKU e o preço do canal; a loja
recebe só as publicações cujo conteúdo ainda não confirmou (sincronização incremental) e devolve o
resultado. Uma falha fica registada com nova tentativa agendada; repetir o mesmo conteúdo não cria outro
produto. Correspondências com produtos já existentes na loja (SKU/EAN) são apenas sugestões: só o operador
as associa, e as ambíguas não se resolvem automaticamente.

A informação alimentar (rótulo, ingredientes, dose, revisão) é gerida e verificada na loja: o IAZELA não a
sobrescreve e mostra o que a loja indica estar em falta. Não escreve no ERP nem movimenta stock ou dinheiro.
"""

import hashlib
import json
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import article_images
from . import inventory as stock
from .access import authorize, current, record, scope
from .db import db_session
from .models import (
    ArticleBrand,
    ArticleFamily,
    ArticleSubfamily,
    StockArticle,
    User,
    WebshopConnection,
    WebshopPublication,
    WebshopPublicationEvent,
    WebshopRemoteProduct,
    WintouchProduct,
    now,
)
from .schemas import Strict
from .webshop import availability, connector

router = APIRouter(prefix="/api/webshop", tags=["Loja online"])

SCHEMA = 2
BACKOFF_MINUTES = (1, 5, 15, 60, 180, 360)
MANAGERS = ["administrador", "gestor"]


def fail(message, code=422):
    raise HTTPException(code, message)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def gtin_valid(code):
    """EAN-8, UPC-A, EAN-13 ou GTIN-14 com dígito de controlo correto."""
    if not re.fullmatch(r"\d{8}|\d{12,14}", code or ""):
        return False
    digits = [int(c) for c in code]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits[:-1])))
    return (10 - total % 10) % 10 == digits[-1]


def source(db, row):
    """Dados do artigo para o canal: nome, código e preço base do ERP quando o artigo está ligado (como no
    catálogo existente); o resto da ficha IAZELA."""
    data = json.loads(row.data)
    erp = db.get(WintouchProduct, row.erp_id) if row.erp_id else None
    net = erp.sale_price if erp else D(str(data.get("sale_net", 0)))
    vat = erp.vat_rate if erp else D(str(data.get("vat_rate", 23)))
    return {
        "row": row,
        "data": data,
        "erp": erp,
        "code": row.code,
        "erp_code": erp.code if erp else "",
        "name": erp.name if erp else row.name,
        "active": bool(row.active and (erp is None or erp.active)),
        "base_net": net,
        "vat": vat,
    }


def price_of(pub, src):
    net = pub.price_net if pub.price_net is not None else src["base_net"]
    vat = src["vat"]
    gross = (
        (net * (1 + vat / 100)).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        if net is not None and vat is not None
        else None
    )
    return net, vat, gross


def blockers(src, net, vat):
    """Motivos que impedem a publicação (o artigo fica retirado da loja enquanto existirem)."""
    data, reasons = src["data"], []
    if not src["active"]:
        reasons.append("Artigo inativo.")
    if data.get("service"):
        reasons.append("Serviços não se vendem na loja online.")
    if int(data.get("precision", 0)) != 0:
        reasons.append("Só artigos vendidos à unidade (sem casas decimais).")
    if net is None or net <= 0:
        reasons.append("Preço de venda desconhecido ou zero: defina o preço do artigo ou do canal.")
    if vat is None:
        reasons.append("Taxa de IVA desconhecida.")
    return reasons


UNSET = object()


def taxonomy(db):
    """Famílias e subfamílias da IAZELA, lidas uma vez por pedido: (por nome, por (família, nome))."""
    cached = db.info.get("webshop_taxonomy")
    if cached is None:
        families = {r.name.casefold(): r for r in db.scalars(select(ArticleFamily))}
        subfamilies = {(r.family_id, r.name.casefold()): r for r in db.scalars(select(ArticleSubfamily))}
        cached = db.info["webshop_taxonomy"] = (families, subfamilies)
    return cached


def classification(db, data):
    """(família, subfamília) da ficha nas tabelas da IAZELA; a loja usa os identificadores como categorias."""
    families, subfamilies = taxonomy(db)
    family = families.get(str(data.get("family") or "").strip().casefold())
    sub = subfamilies.get((family.id, str(data.get("category") or "").strip().casefold())) if family else None
    return family, sub


def payload(db, pub, src=None, woo=UNSET):
    """Conteúdo pretendido na loja. O `hash` muda quando algo publicado muda (não inclui a disponibilidade).
    `woo` calcula o conteúdo com outra associação ao produto da loja, sem alterar a publicação."""
    src = src or source(db, db.get(StockArticle, pub.article_id))
    data = src["data"]
    net, vat, gross = price_of(pub, src)
    blocked = blockers(src, net, vat)
    barcodes = [b for b in data.get("barcodes", []) if isinstance(b, str)]
    family, sub = classification(db, data)
    # Fotografia carregada no IAZELA (caminho público relativo, completado no envio) ou o endereço da ficha.
    main = article_images.public_path(db, pub.article_id) or data.get("photo", "")
    label = article_images.public_path(db, pub.article_id, "label")
    images = [u for u in [main, *data.get("additional_photos", [])] if u]
    if label and article_images.label_in_gallery(db, pub.article_id):
        images.append(label)  # Rótulo publicado também na galeria: sempre a última imagem.
    content = {
        "schema": SCHEMA,
        "article_id": pub.article_id,
        "state": "published" if pub.state == "published" and not blocked else "withdrawn",
        "woo_product_id": pub.woo_product_id if woo is UNSET else woo,
        "sku": pub.sku,
        "code": src["code"],
        "erp_code": src["erp_code"],
        "name": src["name"],
        "description": data.get("description", ""),
        "summary": data.get("web_summary", ""),
        "highlights": data.get("web_highlights", []),
        "seo_keyword": data.get("seo_keyword", ""),
        "seo_title": data.get("seo_title", ""),
        "seo_description": data.get("seo_description", ""),
        "family": data.get("family", ""),
        "category": data.get("category", ""),
        # Identidade estável da categoria na loja: muda o nome na IAZELA, muda o nome da categoria no site.
        "family_id": family.id if family else "",
        "category_id": sub.id if sub else "",
        "brand": data.get("brand", ""),
        "barcodes": barcodes,
        "gtin": next((b for b in barcodes if gtin_valid(b)), ""),
        "unit": data.get("unit", "un"),
        "price_net": str(net.quantize(D("0.000001"))) if net is not None else None,
        "vat_rate": str(vat) if vat is not None else None,
        "price_gross": str(gross) if gross is not None else None,
        "images": images,
        # Rótulo publicado (legível e em português): campo do rótulo na informação alimentar da loja.
        "label_image": label,
    }
    content["hash"] = hashlib.sha256(dump(content).encode()).hexdigest()
    if json.loads(pub.shop_data or "{}").get("override"):
        # Pedido do operador: substituir o texto preservado na loja. Fora do hash; desaparece com a confirmação.
        content["override"] = True
    return content, blocked


def event(db, pub, action, user=None, **detail):
    db.add(WebshopPublicationEvent(publication_id=pub.id, action=action, user_id=user.id if user else None,
                                   data=dump(detail)))


def get_connection(db, user, identifier):
    row = db.get(WebshopConnection, identifier)
    if not row:
        fail("Ligação não encontrada.", 404)
    authorize(user, db, row.store_id, MANAGERS)
    return row


def get_publication(db, user, identifier, revision=None):
    pub = db.get(WebshopPublication, identifier)
    if not pub:
        fail("Publicação não encontrada.", 404)
    get_connection(db, user, pub.connection_id)
    if revision is not None and pub.revision != revision:
        fail("A publicação foi alterada entretanto. Atualize a lista.", 409)
    return pub


def default_sku(src):
    return (src["erp_code"] or src["code"])[:100]


def sku_owner(db, connection_id, sku, exclude=None):
    query = select(WebshopPublication).where(
        WebshopPublication.connection_id == connection_id, func.lower(WebshopPublication.sku) == sku.lower()
    )
    if exclude:
        query = query.where(WebshopPublication.id != exclude)
    return db.scalar(query)


def remote_conflicts(db, connection_id, sku, barcodes, exclude_woo=None):
    """Produtos já na loja, sem associação, com o mesmo SKU (bloqueia) ou EAN (pede decisão)."""
    linked = set(db.scalars(select(WebshopPublication.woo_product_id).where(
        WebshopPublication.connection_id == connection_id, WebshopPublication.woo_product_id.is_not(None))))
    rows = db.scalars(select(WebshopRemoteProduct).where(WebshopRemoteProduct.connection_id == connection_id))
    same_sku, same_ean = [], []
    for remote in rows:
        if remote.woo_product_id in linked or remote.woo_product_id == exclude_woo:
            continue
        if remote.sku and remote.sku.lower() == sku.lower():
            same_sku.append(remote)
        elif remote.gtin and remote.gtin in barcodes:
            same_ean.append(remote)
    return same_sku, same_ean


def margin(db, net, cost_visible, article_id):
    if not cost_visible or net is None or net <= 0:
        return None, None
    cost, _ = stock.average(stock.entries(db, article_id))
    if cost is None:
        return None, None
    return cost, ((net - cost) / net * 100).quantize(D("0.1"))


def status_of(pub, content, blocked):
    if pub.state == "published" and blocked:
        return "blocked"
    if pub.error:
        return "error"
    if pub.synced_hash != content["hash"]:
        return "pending"
    return "synced"


STATUS_LABELS = {
    "synced": "Sincronizado",
    "pending": "A aguardar a loja",
    "error": "Erro",
    "blocked": "Bloqueado",
}


def view(db, pub, cost_visible, src=None):
    src = src or source(db, db.get(StockArticle, pub.article_id))
    content, blocked = payload(db, pub, src)
    net, vat, gross = price_of(pub, src)
    cost, percent = margin(db, net, cost_visible, pub.article_id)
    shop = json.loads(pub.shop_data or "{}")
    status = status_of(pub, content, blocked)
    return {
        "id": pub.id,
        "connection_id": pub.connection_id,
        "article_id": pub.article_id,
        "code": src["code"],
        "erp_code": src["erp_code"],
        "name": src["name"],
        "sku": pub.sku,
        "barcodes": content["barcodes"],
        "gtin": content["gtin"],
        "base_price_net": str(src["base_net"]) if src["base_net"] is not None else None,
        "channel_price_net": str(pub.price_net) if pub.price_net is not None else None,
        "price_net": str(net) if net is not None else None,
        "price_gross": str(gross) if gross is not None else None,
        "vat_rate": str(vat) if vat is not None else None,
        "average_cost": str(cost.quantize(D("0.0001"))) if cost is not None else None,
        "margin_percent": str(percent) if percent is not None else None,
        "state": pub.state,
        "shop_state": content["state"],
        "status": status,
        "status_label": STATUS_LABELS[status],
        "blockers": blocked,
        "woo_product_id": pub.woo_product_id,
        "woo_status": pub.woo_status,
        "synced_at": pub.synced_at,
        "retry_at": pub.retry_at,
        "attempts": pub.attempts,
        "error": pub.error,
        "product_missing": product_missing(pub),
        "food_missing": shop.get("food_missing", []),
        "preserved": shop.get("preserved", []),
        "override_pending": bool(shop.get("override")),
        "revision": pub.revision,
        "updated_at": pub.updated_at,
    }


def product_missing(pub):
    """A loja indicou que o produto associado foi apagado: o operador pode pedir um produto novo."""
    return bool(pub.woo_product_id and "já não existe na loja" in (pub.error or ""))


def can_see_cost(db, user):
    return bool(stock.permissions(user, stock.settings(db)).get("view_cost"))


# --- Gestão (sessão IAZELA) -------------------------------------------------------------------------------


@router.get("/publications")
def publications(connection_id: str, user=Depends(current), db: Session = Depends(db_session)):
    connection = get_connection(db, user, connection_id)
    visible = can_see_cost(db, user)
    rows = list(db.scalars(select(WebshopPublication).where(WebshopPublication.connection_id == connection.id)))
    items = sorted((view(db, pub, visible) for pub in rows), key=lambda item: item["name"].lower())
    counts = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "connection": {"id": connection.id, "name": connection.name, "active": connection.active},
        "cost_visible": visible,
        "counts": counts,
        "items": items,
    }


@router.get("/publications/articles")
def candidates(
    connection_id: str,
    q: str = Query(default="", max_length=100),
    user=Depends(current),
    db: Session = Depends(db_session),
):
    """Artigos ainda não publicados nesta ligação, para escolher o que vai para a loja."""
    connection = get_connection(db, user, connection_id)
    taken = select(WebshopPublication.article_id).where(WebshopPublication.connection_id == connection.id)
    query = select(StockArticle).where(StockArticle.active.is_(True), StockArticle.id.not_in(taken))
    term = q.strip()
    if term:
        like = f"%{term}%"
        query = query.where(or_(StockArticle.code.ilike(like), StockArticle.name.ilike(like),
                                StockArticle.data.ilike(f'%"{term}"%')))
    result = []
    for row in db.scalars(query.order_by(StockArticle.name).limit(30)):
        src = source(db, row)
        net, vat, _ = (src["base_net"], src["vat"], None)
        result.append({
            "article_id": row.id,
            "code": src["code"],
            "erp_code": src["erp_code"],
            "name": src["name"],
            "barcodes": src["data"].get("barcodes", []),
            "price_net": str(net) if net is not None else None,
            "blockers": blockers(src, net, vat),
        })
    return result


class PublishInput(Strict):
    connection_id: str
    article_id: str
    sku: str = Field(default="", max_length=100)
    price_net: D | None = Field(default=None, gt=0, le=10**9, max_digits=18, decimal_places=6)
    # Existe na loja um produto com o mesmo EAN: criar mesmo assim um produto novo (decisão do operador).
    allow_new: bool = False

    @field_validator("sku")
    @classmethod
    def clean_sku(cls, value):
        return value.strip()


def validate_sku(db, connection_id, sku, exclude=None):
    if not re.fullmatch(r"[\w.\-/]{1,100}", sku):
        fail("SKU inválido: use letras, algarismos, ponto, hífen, barra ou sublinhado, sem espaços.")
    owner = sku_owner(db, connection_id, sku, exclude)
    if owner:
        other = db.get(StockArticle, owner.article_id)
        fail(f"O SKU «{sku}» já está na publicação de «{other.name if other else owner.article_id}».", 409)


@router.post("/publications")
def publish_article(data: PublishInput, user=Depends(current), db: Session = Depends(db_session)):
    connection = get_connection(db, user, data.connection_id)
    if not connection.active:
        fail("A ligação está revogada.", 409)
    stock.lock(db)
    row = db.get(StockArticle, data.article_id)
    if not row:
        fail("Artigo não encontrado.", 404)
    existing = db.scalar(select(WebshopPublication).where(
        WebshopPublication.connection_id == connection.id, WebshopPublication.article_id == row.id))
    if existing:
        fail("Este artigo já tem publicação nesta loja: use Publicar ou Atualizar na lista.", 409)
    src = source(db, row)
    sku = data.sku or default_sku(src)
    validate_sku(db, connection.id, sku)
    pub = WebshopPublication(connection_id=connection.id, article_id=row.id, sku=sku, state="published",
                             price_net=data.price_net, user_id=user.id)
    _, blocked = payload(db, pub, src)
    if blocked:
        fail("Não é possível publicar: " + " ".join(blocked))
    own = db.scalar(select(WebshopRemoteProduct).where(
        WebshopRemoteProduct.connection_id == connection.id, WebshopRemoteProduct.article_id == row.id))
    if own and not db.scalar(select(WebshopPublication.id).where(
            WebshopPublication.connection_id == connection.id, WebshopPublication.woo_product_id == own.woo_product_id)):
        # Produto criado antes por este conector para o mesmo artigo (identidade exata): reutiliza-o.
        pub.woo_product_id = own.woo_product_id
    same_sku, same_ean = remote_conflicts(db, connection.id, sku, src["data"].get("barcodes", []),
                                          exclude_woo=pub.woo_product_id)
    if same_sku:
        fail(f"Já existe na loja o produto «{same_sku[0].name}» (ID {same_sku[0].woo_product_id}) com o SKU "
             f"«{sku}», sem associação: associe-o em vez de criar outro produto.", 409)
    if same_ean and not data.allow_new:
        names = ", ".join(f"«{r.name}» (ID {r.woo_product_id})" for r in same_ean[:3])
        fail(f"Já existe na loja um produto com o mesmo EAN: {names}. Associe-o, ou confirme que pretende "
             "criar um produto novo.", 409)
    db.add(pub)
    db.flush()
    event(db, pub, "published", user, sku=sku, price_net=str(data.price_net) if data.price_net else None,
          allow_new=data.allow_new)
    record(db, user, "webshop_publication_created", pub.id, {"article_id": row.id, "sku": sku}, connection.store_id)
    commit(db)
    return view(db, pub, can_see_cost(db, user), src)


class AssociateInput(Strict):
    connection_id: str
    article_id: str
    woo_product_id: int = Field(gt=0)


@router.post("/publications/associate")
def associate(data: AssociateInput, user=Depends(current), db: Session = Depends(db_session)):
    """Associa um produto já existente na loja a um artigo IAZELA (decisão explícita do operador)."""
    connection = get_connection(db, user, data.connection_id)
    stock.lock(db)
    row = db.get(StockArticle, data.article_id)
    if not row:
        fail("Artigo não encontrado.", 404)
    remote = db.scalar(select(WebshopRemoteProduct).where(
        WebshopRemoteProduct.connection_id == connection.id, WebshopRemoteProduct.woo_product_id == data.woo_product_id))
    if not remote:
        fail("Produto da loja desconhecido: aguarde a próxima comunicação dos produtos pela loja.", 404)
    if remote.article_id and remote.article_id != row.id:
        fail("Esse produto da loja já pertence a outro artigo IAZELA.", 409)
    taken = db.scalar(select(WebshopPublication).where(
        WebshopPublication.connection_id == connection.id, WebshopPublication.woo_product_id == data.woo_product_id))
    if taken and taken.article_id != row.id:
        fail("Esse produto da loja já está associado a outra publicação.", 409)
    pub = db.scalar(select(WebshopPublication).where(
        WebshopPublication.connection_id == connection.id, WebshopPublication.article_id == row.id))
    if pub and pub.woo_product_id and pub.woo_product_id != data.woo_product_id:
        fail("O artigo já está publicado noutro produto da loja. Retire-o antes de mudar a associação.", 409)
    src = source(db, row)
    if not pub:
        sku = remote.sku or default_sku(src)
        validate_sku(db, connection.id, sku)
        pub = WebshopPublication(connection_id=connection.id, article_id=row.id, sku=sku,
                                 state="published" if remote.status == "publish" else "withdrawn", user_id=user.id)
        db.add(pub)
        db.flush()
    pub.woo_product_id = data.woo_product_id
    pub.revision += 1
    pub.updated_at = now()
    event(db, pub, "associated", user, woo_product_id=data.woo_product_id, remote_sku=remote.sku,
          remote_gtin=remote.gtin, remote_name=remote.name)
    record(db, user, "webshop_publication_associated", pub.id,
           {"article_id": row.id, "woo_product_id": data.woo_product_id}, connection.store_id)
    commit(db)
    return view(db, pub, can_see_cost(db, user), src)


class EditInput(Strict):
    revision: int
    sku: str = Field(min_length=1, max_length=100)
    price_net: D | None = Field(default=None, gt=0, le=10**9, max_digits=18, decimal_places=6)

    @field_validator("sku")
    @classmethod
    def clean_sku(cls, value):
        return value.strip()


@router.put("/publications/{identifier}")
def edit(identifier: str, data: EditInput, user=Depends(current), db: Session = Depends(db_session)):
    stock.lock(db)
    pub = get_publication(db, user, identifier, data.revision)
    validate_sku(db, pub.connection_id, data.sku, exclude=pub.id)
    before = {"sku": pub.sku, "price_net": str(pub.price_net) if pub.price_net is not None else None}
    after = {"sku": data.sku, "price_net": str(data.price_net) if data.price_net is not None else None}
    if before == after:
        return view(db, pub, can_see_cost(db, user))
    pub.sku, pub.price_net = data.sku, data.price_net
    pub.revision += 1
    pub.updated_at = now()
    event(db, pub, "edited", user, before=before, after=after)
    connection = db.get(WebshopConnection, pub.connection_id)
    record(db, user, "webshop_publication_edited", pub.id, {"before": before, "after": after}, connection.store_id)
    commit(db)
    return view(db, pub, can_see_cost(db, user))


class ActionInput(Strict):
    revision: int


@router.post("/publications/{identifier}/{action}")
def act(
    identifier: str,
    action: Literal["publish", "withdraw", "resync", "recreate", "override"],
    data: ActionInput,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    """Publicar, retirar (o produto fica em rascunho na loja, sem apagar), atualizar já (nova tentativa) ou criar
    um produto novo quando o produto associado foi apagado na loja (decisão do operador; nunca automático)."""
    stock.lock(db)
    pub = get_publication(db, user, identifier, data.revision)
    detail = {"article_id": pub.article_id}
    if action == "recreate":
        if not pub.woo_product_id:
            fail("A publicação não está associada a nenhum produto da loja.", 409)
        detail["previous_woo_product_id"] = pub.woo_product_id
        # O produto apagado deixa de contar como produto da loja; a loja cria outro em rascunho na sincronização.
        for remote in db.scalars(select(WebshopRemoteProduct).where(
                WebshopRemoteProduct.connection_id == pub.connection_id,
                WebshopRemoteProduct.woo_product_id == pub.woo_product_id)):
            db.delete(remote)
        pub.woo_product_id, pub.woo_status, pub.error, pub.synced_hash = None, "", "", ""
    elif action == "override":
        # O texto da loja foi preservado (editado lá): o operador escolhe o da IAZELA. Volta a enviar já.
        shop = json.loads(pub.shop_data or "{}")
        detail["preserved"] = shop.get("preserved", [])
        pub.shop_data, pub.synced_hash = dump({**shop, "override": True}), ""
    elif action == "publish":
        src = source(db, db.get(StockArticle, pub.article_id))
        net, vat, _ = price_of(pub, src)
        blocked = blockers(src, net, vat)
        if blocked:
            fail("Não é possível publicar: " + " ".join(blocked))
        pub.state = "published"
    elif action == "withdraw":
        pub.state = "withdrawn"
    else:
        # Volta a enviar o conteúdo atual na próxima sincronização, mesmo que já confirmado.
        pub.synced_hash = ""
    pub.retry_at = None
    pub.attempts = 0
    pub.revision += 1
    pub.updated_at = now()
    event(db, pub, {"publish": "published", "withdraw": "withdrawn", "resync": "resync", "recreate": "recreated",
                    "override": "override"}[action],
          user, **{k: v for k, v in detail.items() if k != "article_id"})
    connection = db.get(WebshopConnection, pub.connection_id)
    record(db, user, "webshop_publication_" + action, pub.id, detail, connection.store_id)
    commit(db)
    return view(db, pub, can_see_cost(db, user))


@router.get("/publications/{identifier}/history")
def history(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    pub = get_publication(db, user, identifier)
    names = {}
    result = []
    for item in db.scalars(select(WebshopPublicationEvent).where(WebshopPublicationEvent.publication_id == pub.id)
                           .order_by(WebshopPublicationEvent.created_at.desc(), WebshopPublicationEvent.id)
                           .limit(200)):
        if item.user_id and item.user_id not in names:
            person = db.get(User, item.user_id)
            names[item.user_id] = person.name if person else ""
        result.append({"action": item.action, "at": item.created_at, "user": names.get(item.user_id, "Loja online"),
                       "data": json.loads(item.data)})
    return result


# --- Catálogo de artigos e ficha: o artigo está (ou não) na lista da loja online -------------------------------


def managed_connections(db, user):
    """Ligações ativas que o utilizador pode gerir (administrador ou gestor da loja da ligação)."""
    if user.role not in MANAGERS:
        return []
    stores = set(scope(user, db))
    return [row for row in db.scalars(select(WebshopConnection).where(WebshopConnection.active.is_(True))
                                      .order_by(WebshopConnection.name)) if row.store_id in stores]


def summary(db, pub, src=None):
    """Estado resumido de uma publicação para a grelha e a ficha do artigo."""
    src = src or source(db, db.get(StockArticle, pub.article_id))
    content, blocked = payload(db, pub, src)
    status = "withdrawn" if pub.state == "withdrawn" and not pub.error and pub.synced_hash == content["hash"] \
        else status_of(pub, content, blocked)
    net, _, gross = price_of(pub, src)
    shop = json.loads(pub.shop_data or "{}")
    return {"id": pub.id, "connection_id": pub.connection_id, "state": pub.state, "status": status,
            "status_label": "Retirado da loja" if status == "withdrawn" else STATUS_LABELS[status],
            "sku": pub.sku, "price_gross": str(gross) if gross is not None else None,
            "channel_price": pub.price_net is not None, "blockers": blocked, "error": pub.error,
            "synced_at": pub.synced_at, "retry_at": pub.retry_at, "woo_product_id": pub.woo_product_id,
            "product_missing": product_missing(pub),
            "woo_status": pub.woo_status, "food_missing": shop.get("food_missing", []),
            "preserved": shop.get("preserved", []), "override_pending": bool(shop.get("override")),
            "revision": pub.revision}


def article_states(db, article_ids):
    """{artigo: [resumo por ligação]} para os artigos indicados (uma página da grelha)."""
    if not article_ids:
        return {}
    names = {row.id: row.name for row in db.scalars(select(WebshopConnection))}
    result = {}
    for pub in db.scalars(select(WebshopPublication).where(WebshopPublication.article_id.in_(article_ids))):
        result.setdefault(pub.article_id, []).append({**summary(db, pub), "connection": names.get(pub.connection_id, "")})
    return result


@router.get("/article-publications/{article_id}")
def article_publications(article_id: str, user=Depends(current), db: Session = Depends(db_session)):
    """A ficha do artigo: em que lojas online está e o que se pode fazer (só administrador/gestor altera)."""
    row = db.get(StockArticle, article_id)
    if not row:
        fail("Artigo não encontrado.", 404)
    stores = set(scope(user, db))
    managed = {c.id for c in managed_connections(db, user)}
    src = source(db, row)
    net, vat = src["base_net"], src["vat"]
    items = []
    for connection in db.scalars(select(WebshopConnection).order_by(WebshopConnection.name)):
        if connection.store_id not in stores:
            continue
        pub = db.scalar(select(WebshopPublication).where(WebshopPublication.connection_id == connection.id,
                                                         WebshopPublication.article_id == row.id))
        if not pub and not connection.active:
            continue
        items.append({"connection_id": connection.id, "connection": connection.name, "active": connection.active,
                      "can_manage": connection.id in managed and connection.active,
                      "publication": summary(db, pub, src) if pub else None,
                      "blockers": [] if pub else blockers(src, net, vat)})
    return {"article_id": row.id, "connections": items}


@router.get("/publications-suggestions")
def suggestions(connection_id: str, user=Depends(current), db: Session = Depends(db_session)):
    """Produtos da loja sem associação e artigos candidatos pelo SKU ou EAN. Nunca associa sozinho."""
    connection = get_connection(db, user, connection_id)
    pubs = list(db.scalars(select(WebshopPublication).where(WebshopPublication.connection_id == connection.id)))
    linked = {p.woo_product_id for p in pubs if p.woo_product_id}
    published = {p.article_id for p in pubs}
    remotes = [r for r in db.scalars(select(WebshopRemoteProduct).where(
        WebshopRemoteProduct.connection_id == connection.id).order_by(WebshopRemoteProduct.name))
        if r.woo_product_id not in linked and not r.article_id]
    if not remotes:
        return []
    by_code, by_ean, names = {}, {}, {}
    for row, erp_code in db.execute(select(StockArticle, WintouchProduct.code)
                                    .outerjoin(WintouchProduct, WintouchProduct.id == StockArticle.erp_id)):
        names[row.id] = (erp_code or row.code, row.name)
        for code in {row.code, erp_code} - {None, ""}:
            by_code.setdefault(code.lower(), set()).add(row.id)
        for barcode in json.loads(row.data).get("barcodes", []):
            by_ean.setdefault(barcode, set()).add(row.id)
    ean_remotes = {}
    for remote in remotes:
        if remote.gtin:
            ean_remotes.setdefault(remote.gtin, []).append(remote.woo_product_id)
    result = []
    for remote in remotes:
        found = {}
        for article_id in by_code.get(remote.sku.lower(), set()) if remote.sku else set():
            found.setdefault(article_id, set()).add("SKU")
        for article_id in by_ean.get(remote.gtin, set()) if remote.gtin else set():
            found.setdefault(article_id, set()).add("EAN")
        candidates = [{"article_id": a, "code": names[a][0], "name": names[a][1], "by": sorted(by),
                       "already_published": a in published} for a, by in found.items()]
        if not candidates:
            status, note = "none", "Sem artigo com o mesmo SKU ou EAN."
        elif len(candidates) > 1:
            status, note = "ambiguous", "Vários artigos correspondem: escolha manualmente."
        elif remote.gtin and len(ean_remotes.get(remote.gtin, [])) > 1:
            status, note = "ambiguous", "O mesmo EAN está em vários produtos da loja."
        elif candidates[0]["already_published"]:
            status, note = "conflict", "O artigo já está publicado noutro produto da loja."
        else:
            status, note = "suggested", "Correspondência única: confirme a associação."
        result.append({"woo_product_id": remote.woo_product_id, "sku": remote.sku, "gtin": remote.gtin,
                       "name": remote.name, "status": status, "note": note, "candidates": candidates,
                       "woo_status": remote.status})
    return result


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        fail("Duplicação recusada: o artigo, o SKU ou o produto da loja já estão noutra publicação.", 409)


# --- Loja online (credencial da ligação) ------------------------------------------------------------------


class RemoteBrand(Strict):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=300)


class RemoteBrandsInput(Strict):
    brands: list[RemoteBrand] = Field(max_length=200)


@router.post("/brand-catalog")
def import_brands(data: RemoteBrandsInput, identity=Depends(connector), db: Session = Depends(db_session)):
    """Importação aditiva das marcas do site. Não altera fichas, estados ou nomes locais."""
    connection, owner = identity
    stock.lock(db)
    known = {" ".join(r.name.split()).casefold(): r for r in db.scalars(select(ArticleBrand))}
    created, matched, skipped = [], [], []
    for item in data.brands:
        name = " ".join(item.name.split())
        if not name or len(name) > 100:
            skipped.append({"site_id": item.id, "reason": "A designação deve ter entre 1 e 100 caracteres."})
            continue
        key = name.casefold()
        row = known.get(key)
        if row is None:
            row = ArticleBrand(name=name, active=True)
            db.add(row)
            db.flush()
            known[key] = row
            created.append({"site_id": item.id, "id": row.id, "name": row.name})
        else:
            matched.append({"site_id": item.id, "id": row.id, "name": row.name})
    if created or skipped:
        record(db, owner, "webshop_brands_imported", connection.id,
               {"created": created, "matched": len(matched), "skipped": skipped}, connection.store_id)
    db.commit()
    return {"connection_id": connection.id, "created": created, "matched": matched, "skipped": skipped}


@router.get("/families")
def families(identity=Depends(connector), db: Session = Depends(db_session)):
    """Categorias da loja: as famílias (e subfamílias) da IAZELA usadas nos artigos desta ligação, com o
    identificador estável, o nome atual e a hierarquia. A loja cria, renomeia e organiza as categorias por elas."""
    connection, _ = identity
    used = {}
    for pub in db.scalars(select(WebshopPublication).where(WebshopPublication.connection_id == connection.id)):
        row = db.get(StockArticle, pub.article_id)
        family, sub = classification(db, json.loads(row.data or "{}")) if row else (None, None)
        if not family:
            continue
        entry = used.setdefault(family.id, {"id": family.id, "name": family.name, "subfamilies": {}})
        if sub:
            entry["subfamilies"][sub.id] = {"id": sub.id, "name": sub.name}
    items = [{**entry, "subfamilies": sorted(entry["subfamilies"].values(), key=lambda r: r["name"].casefold())}
             for entry in sorted(used.values(), key=lambda r: r["name"].casefold())]
    return {"version": SCHEMA, "connection_id": connection.id, "families": items}


@router.get("/publications-feed")
def feed(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    identity=Depends(connector),
    db: Session = Depends(db_session),
):
    """Publicações por enviar à loja: conteúdo ainda não confirmado e sem tentativa agendada para depois.
    As fotografias carregadas no IAZELA seguem com o endereço completo; o hash usa o caminho relativo, por
    isso não muda com a origem da API."""
    connection, _ = identity
    origin = article_images.public_origin(request)
    moment = now()
    items = []
    rows = db.scalars(select(WebshopPublication).where(WebshopPublication.connection_id == connection.id)
                      .order_by(WebshopPublication.updated_at, WebshopPublication.id))
    for pub in rows:
        if pub.retry_at and pub.retry_at > moment:
            continue
        content, _ = payload(db, pub)
        if content["hash"] == pub.synced_hash:
            continue
        content["images"] = [origin + url if url.startswith("/") else url for url in content["images"]]
        if content["label_image"].startswith("/"):
            content["label_image"] = origin + content["label_image"]
        items.append(content)
        if len(items) >= limit:
            break
    return {"version": SCHEMA, "connection_id": connection.id, "items": items}


class Result(Strict):
    article_id: str
    hash: str = Field(min_length=64, max_length=64)
    ok: bool
    woo_product_id: int | None = Field(default=None, gt=0)
    woo_status: str = Field(default="", max_length=20)
    error: str = Field(default="", max_length=1000)
    food_missing: list[str] = Field(default_factory=list, max_length=30)
    preserved: list[str] = Field(default_factory=list, max_length=10)


class ResultsInput(Strict):
    results: list[Result] = Field(max_length=100)


@router.post("/publications-results")
def results(data: ResultsInput, identity=Depends(connector), db: Session = Depends(db_session)):
    """Resultado de cada publicação processada pela loja. Confirma só o conteúdo que a loja recebeu."""
    connection, _ = identity
    stock.lock(db)
    answer = []
    for item in data.results:
        pub = db.scalar(select(WebshopPublication).where(
            WebshopPublication.connection_id == connection.id, WebshopPublication.article_id == item.article_id))
        if not pub:
            answer.append({"article_id": item.article_id, "accepted": False, "reason": "Sem publicação."})
            continue
        # Conteúdo atual com a associação anterior, sem associação (envio da criação) ou com a comunicada.
        known = {hash_with(db, pub, woo) for woo in {pub.woo_product_id, None, item.woo_product_id}}
        moment = now()
        shop = {"food_missing": item.food_missing[:30], "preserved": item.preserved[:10]}
        if item.ok:
            if item.woo_product_id and item.woo_product_id != pub.woo_product_id:
                taken = db.scalar(select(WebshopPublication).where(
                    WebshopPublication.connection_id == connection.id,
                    WebshopPublication.woo_product_id == item.woo_product_id,
                    WebshopPublication.id != pub.id))
                if taken:
                    fail_result(pub, moment, "O produto da loja já está associado a outro artigo: duplicação recusada.")
                    event(db, pub, "sync_error", error=pub.error, woo_product_id=item.woo_product_id)
                    answer.append({"article_id": item.article_id, "accepted": False, "reason": pub.error})
                    continue
                pub.woo_product_id = item.woo_product_id
            # Confirma o conteúdo atual só se foi esse que a loja recebeu (a associação agora conhecida não o
            # torna pendente). Um resultado de conteúdo antigo não faz regredir uma confirmação mais recente.
            if item.hash in known:
                pub.synced_hash = payload(db, pub)[0]["hash"]
            pub.synced_at, pub.error, pub.attempts, pub.retry_at = moment, "", 0, None
            pub.woo_status = item.woo_status
            pub.shop_data = dump(shop)
            event(db, pub, "synced", hash=item.hash, woo_product_id=pub.woo_product_id, woo_status=item.woo_status,
                  **shop)
        else:
            fail_result(pub, moment, item.error or "Erro sem descrição na loja.")
            if json.loads(pub.shop_data or "{}").get("override"):
                shop["override"] = True  # O pedido de substituição mantém-se até a loja o aplicar.
            pub.shop_data = dump(shop)
            event(db, pub, "sync_error", hash=item.hash, error=pub.error, attempts=pub.attempts)
        answer.append({"article_id": item.article_id, "accepted": True})
    db.commit()
    return {"results": answer}


def hash_with(db, pub, woo_product_id):
    return payload(db, pub, woo=woo_product_id)[0]["hash"]


def fail_result(pub, moment, message):
    pub.attempts += 1
    pub.error = message[:1000]
    pub.retry_at = moment + timedelta(minutes=BACKOFF_MINUTES[min(pub.attempts, len(BACKOFF_MINUTES)) - 1])


@router.get("/publications-stock")
def published_stock(
    cursor: str = "",
    limit: int = Query(default=500, ge=1, le=500),
    identity=Depends(connector),
    db: Session = Depends(db_session),
):
    """Disponível (stock livre, sem reservas nem lotes expirados) dos artigos publicados. `null` = desconhecido."""
    connection, _ = identity
    rows = list(db.scalars(select(WebshopPublication).where(
        WebshopPublication.connection_id == connection.id, WebshopPublication.state == "published",
        WebshopPublication.article_id > cursor).order_by(WebshopPublication.article_id).limit(limit + 1)))
    items = []
    for pub in rows[:limit]:
        content, _ = payload(db, pub)
        if content["state"] != "published":
            continue
        items.append({"article_id": pub.article_id, "available": availability(db, pub.article_id, connection.warehouse_id)})
    return {"connection_id": connection.id, "items": items,
            "next_cursor": rows[limit - 1].article_id if len(rows) > limit else None}


class RemoteProduct(Strict):
    woo_product_id: int = Field(gt=0)
    sku: str = Field(default="", max_length=100)
    gtin: str = Field(default="", max_length=50)
    name: str = Field(default="", max_length=300)
    status: str = Field(default="", max_length=20)
    article_id: str = Field(default="", max_length=36)


class RemoteInput(Strict):
    cycle: str = Field(min_length=8, max_length=36)
    final: bool = False
    products: list[RemoteProduct] = Field(max_length=500)


@router.post("/remote-products")
def remote_products(data: RemoteInput, identity=Depends(connector), db: Session = Depends(db_session)):
    """Lista dos produtos existentes na loja (por páginas). Um produto que já tem o identificador do artigo
    gravado por esta ligação fica associado (identidade exata); os restantes só geram sugestões."""
    connection, owner = identity
    stock.lock(db)
    existing = {r.woo_product_id: r for r in db.scalars(select(WebshopRemoteProduct).where(
        WebshopRemoteProduct.connection_id == connection.id))}
    linked = 0
    for item in data.products:
        row = existing.get(item.woo_product_id)
        if not row:
            row = WebshopRemoteProduct(connection_id=connection.id, woo_product_id=item.woo_product_id)
            db.add(row)
            existing[item.woo_product_id] = row
        article = db.get(StockArticle, item.article_id) if item.article_id else None
        row.sku, row.gtin, row.name, row.status = item.sku.strip(), item.gtin.strip(), item.name, item.status
        row.article_id = article.id if article else ""
        row.cycle, row.reported_at = data.cycle, now()
        if article:
            linked += link_known(db, connection, owner, article, item)
    if data.final:
        for row in existing.values():
            if row.cycle != data.cycle:
                db.delete(row)
    db.commit()
    return {"received": len(data.products), "linked": linked}


def link_known(db, connection, owner, article, item):
    """Produto criado por este conector (tem o ID do artigo): regista a associação que já existe na loja."""
    taken = db.scalar(select(WebshopPublication).where(
        WebshopPublication.connection_id == connection.id, WebshopPublication.woo_product_id == item.woo_product_id))
    if taken:
        return 0
    pub = db.scalar(select(WebshopPublication).where(
        WebshopPublication.connection_id == connection.id, WebshopPublication.article_id == article.id))
    if pub:
        if pub.woo_product_id:
            return 0
        pub.woo_product_id = item.woo_product_id
        event(db, pub, "linked", woo_product_id=item.woo_product_id)
        db.flush()
        return 1
    if item.status != "publish":
        return 0
    # Produto já à venda na loja, criado pelo conector anterior: passa a ser gerido como publicação.
    sku = item.sku.strip() or default_sku(source(db, article))
    if not re.fullmatch(r"[\w.\-/]{1,100}", sku) or sku_owner(db, connection.id, sku):
        return 0
    pub = WebshopPublication(connection_id=connection.id, article_id=article.id, sku=sku, state="published",
                             woo_product_id=item.woo_product_id, woo_status=item.status, user_id=owner.id)
    db.add(pub)
    db.flush()
    event(db, pub, "imported", woo_product_id=item.woo_product_id, sku=sku)
    return 1
