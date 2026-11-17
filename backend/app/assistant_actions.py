"""Ações do assistente IAZELA para preparar e publicar um artigo na loja online, a partir de uma fotografia.

Tudo aqui valida e PROPÕE; nada altera a ficha, as imagens nem a loja. Cada proposta segue para o navegador
num cartão e, se o utilizador confirmar, o navegador faz o mesmo pedido que o ecrã correspondente (ficha do
artigo, descritivos do site, catálogo da loja online), com as permissões, os controlos de concorrência e a
auditoria desse endpoint:

- Fotografia ou rótulo: a fotografia anexada na conversa (o navegador envia-a ao confirmar) ou uma imagem de
  um endereço HTTPS (descarregada e validada aqui para a pré-visualização; guardada pela importação por URL).
- Imagens de uma página: lê a imagem de produto que a página declara (og:image, schema.org Product), com o
  mesmo leitor seguro da app (endereços públicos, robots.txt, limites de tamanho).
- Descritivo do site: gerado pelo serviço dos descritivos (proposta pendente, que nunca muda a ficha) e aprovado
  no cartão, com edição, pelo endpoint de aprovação.
- Publicação: pré-validada como no catálogo da loja online; criada ou reposta pelo endpoint do catálogo.
"""

import base64
import io
import json
from urllib.parse import urljoin, urlsplit

from fastapi import HTTPException
from sqlalchemy import select

from .models import ArticleContentProposal, StockArticle, WebshopConnection, WebshopPublication

ROLES = {"fotografia": "photo", "rotulo": "label"}
ROLE_LABELS = {"photo": "Fotografia do produto", "label": "Fotografia do rótulo"}
MAX_ATTACHMENT = 12 * 1024 * 1024
PREVIEW_SIDE = 360
PAGE_LIMIT = 3 * 1024 * 1024
PAGE_IMAGES = 6


class ActionError(Exception):
    """Pedido que não pode ser proposto; a mensagem segue para o modelo."""


def detail(exc):
    return str(exc.detail) if isinstance(exc, HTTPException) else str(exc)


def article_for(db, article_id):
    row = db.get(StockArticle, article_id or "")
    if not row:
        raise ActionError("Artigo não encontrado. Use procurar_artigos e indique o id devolvido.")
    if not row.active:
        raise ActionError(f"O artigo «{row.name}» está inativo.")
    return row


def can_edit_article(db, user):
    from . import inventory as stock

    try:
        stock.require(user, stock.settings(db), "adjust_stock")
    except HTTPException as exc:
        raise ActionError(detail(exc)) from None


# --- Fotografia anexada na conversa --------------------------------------------------------------------------

def decode_attachment(text):
    """Fotografia anexada (base64, sem prefixo data:) normalizada como na ficha: JPEG, até 2000 px, sem
    metadados. Devolve (bytes, largura, altura) ou levanta HTTPException 422 com o motivo."""
    from .article_images import normalize

    try:
        raw = base64.b64decode(text, validate=True)
    except ValueError:
        raise HTTPException(422, "A fotografia anexada não é válida. Volte a anexá-la.") from None
    if not raw or len(raw) > MAX_ATTACHMENT:
        raise HTTPException(422, "A fotografia anexada está vazia ou excede 12 MB.")
    return normalize(raw)


def barcodes_in(content):
    """Códigos de barras de produto (EAN/UPC) legíveis na fotografia, para encontrar o artigo."""
    import zxingcpp
    from PIL import Image

    try:
        with Image.open(io.BytesIO(content)) as image:
            found = zxingcpp.read_barcodes(image.convert("L"))
    except Exception:  # noqa: BLE001 - leitura opcional: sem código, o modelo procura pelo nome
        return []
    linear = {"EAN13", "EAN8", "UPCA", "UPCE"}
    return sorted({code.text for code in found if code.text and code.text.isdigit()
                   and str(code.format).split(".")[-1].replace("-", "").replace("_", "") in linear})


def preview(content):
    """Miniatura JPEG (data URL) para o cartão, sem pedir nada ao site de origem a partir do navegador."""
    from PIL import Image

    with Image.open(io.BytesIO(content)) as image:
        image.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


# --- Proposta de imagem ---------------------------------------------------------------------------------------

def image_proposal(db, user, article_id, role_name, origin, url, attachment, pending):
    """Fotografia ou rótulo para a ficha: a anexada na conversa ou a de um endereço HTTPS. Não grava."""
    from .article_images import fetch, normalize

    can_edit_article(db, user)
    row = article_for(db, article_id)
    role = ROLES.get(role_name)
    if not role:
        raise ActionError("Papel inválido: use fotografia ou rotulo.")
    base = {"type": "image", "article_id": row.id, "code": row.code, "name": row.name, "role": role,
            "role_label": ROLE_LABELS[role]}
    if origin == "anexo":
        if not attachment:
            raise ActionError("Não há fotografia anexada nesta mensagem. Peça ao utilizador para a anexar.")
        content, width, height = attachment
        if any(a["type"] == "image" and a.get("source") == "anexo" and a["article_id"] == row.id and a["role"] == role
               for a in pending):
            raise ActionError("Esta fotografia já foi proposta para este artigo.")
        return {**base, "source": "anexo", "width": width, "height": height, "preview": preview(content),
                "request": {"path": f"/stock/articles/{row.id}/images/{role}", "upload": "anexo"}}
    if origin != "url":
        raise ActionError("Origem inválida: use anexo ou url.")
    url = (url or "").strip()
    if not url.startswith("https://") or len(url) > 2000:
        raise ActionError("Indique o endereço HTTPS da imagem (não o da página).")
    try:
        content, width, height = normalize(fetch(url))
    except HTTPException as exc:
        raise ActionError(f"Não foi possível usar essa imagem: {detail(exc)}") from None
    return {**base, "source": "url", "url": url, "site": (urlsplit(url).hostname or "").removeprefix("www."),
            "width": width, "height": height, "preview": preview(content),
            "request": {"path": f"/stock/articles/{row.id}/images/{role}/from-url", "body": {"url": url}}}


def _page_images(html, base_url):
    from .market_prices import _Page

    parser = _Page()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - HTML inválido: usa-se o que foi lido até aí
        pass
    found = []

    def add(value):
        if isinstance(value, list):
            for item in value:
                add(item)
        elif isinstance(value, dict):
            add(value.get("url") or value.get("contentUrl"))
        elif isinstance(value, str) and value.strip():
            absolute = urljoin(base_url, value.strip())
            if absolute.startswith("https://") and absolute not in found:
                found.append(absolute)

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if "@graph" in node:
                walk(node["@graph"])
            types = node.get("@type")
            if "Product" in (types if isinstance(types, list) else [types]):
                add(node.get("image"))

    for block in parser.blocks:
        try:
            walk(json.loads(block.strip()))
        except ValueError:
            continue
    for key in ("og:image:secure_url", "og:image", "twitter:image"):
        add(parser.meta.get(key))
    return found[:PAGE_IMAGES], parser.meta.get("og:site_name", "").strip()


def page_images(url):
    """Imagens de produto declaradas numa página (respeita o robots.txt do site)."""
    from . import web_fetch

    url = (url or "").strip()
    if not url.startswith("https://"):
        raise ActionError("Indique o endereço HTTPS da página do produto.")
    try:
        if not web_fetch.robots_allow(url):
            raise ActionError("O site não permite leitura automática desta página (robots.txt).")
        raw, final = web_fetch.get(url, accept="text/html,application/xhtml+xml", limit=PAGE_LIMIT, what="da página",
                                   too_big="A página é demasiado grande para ler.", timeout=15)
    except HTTPException as exc:
        raise ActionError(detail(exc)) from None
    images, site = _page_images(raw.decode("utf-8", errors="replace"), final)
    host = (urlsplit(final).hostname or "").removeprefix("www.")
    return {"pagina": final, "site": site or host, "imagens": images,
            "nota": "" if images else "A página não declara imagens de produto (og:image ou schema.org)."}


# --- Descritivo do site ---------------------------------------------------------------------------------------

def content_proposal(db, user, article_id, pending):
    """Gera a proposta de descritivo pelo serviço dos descritivos (fica pendente; a ficha não muda)."""
    from . import article_content

    can_edit_article(db, user)
    row = article_for(db, article_id)
    if any(a["type"] == "image" and a["article_id"] == row.id for a in pending):
        raise ActionError("Há uma imagem proposta para este artigo ainda por confirmar. Peça ao utilizador para a "
                          "confirmar no cartão e, depois, gere o descritivo (a IA lê as imagens guardadas na ficha).")
    try:
        result = article_content.create_proposal(row.id, user=user, db=db)
    except HTTPException as exc:
        raise ActionError(detail(exc)) from None
    proposal = result["proposal"]
    return {"type": "web_content", "article_id": row.id, "code": row.code, "name": row.name,
            "proposal_id": proposal["id"], "fields": proposal["fields"], "needs_review": proposal["needs_review"],
            "missing_information": proposal["missing_information"], "images_used": proposal["images_used"],
            "request": {"path": f"/stock/articles/{row.id}/web-content/approve", "revision": result["revision"],
                        "discard": f"/stock/articles/{row.id}/web-content/discard"}}


def has_pending_content(db, article_id):
    item = db.scalar(select(ArticleContentProposal).where(ArticleContentProposal.article_id == article_id))
    return bool(item and item.status == "pendente")


# --- Publicação na loja online --------------------------------------------------------------------------------

def connections_for(db, user):
    from .access import authorize
    from .webshop_catalog import MANAGERS

    rows = []
    for row in db.scalars(select(WebshopConnection).where(WebshopConnection.active.is_(True)).order_by(WebshopConnection.name)):
        try:
            authorize(user, db, row.store_id, MANAGERS)
        except HTTPException:
            continue
        rows.append(row)
    return rows


def publish_proposal(db, user, article_id, shop, pending):
    """Publicar o artigo na loja online (nova publicação, ou repor uma retirada). Não grava."""
    from . import article_images
    from .webshop_catalog import blockers, default_sku, price_of, source

    row = article_for(db, article_id)
    connections = connections_for(db, user)
    if shop:
        connections = [c for c in connections if shop.lower() in c.name.lower()]
    if not connections:
        raise ActionError("Não há loja online ativa que possa gerir" + (f" com o nome «{shop}»." if shop else "."))
    if len(connections) > 1:
        raise ActionError("Indique a loja online: " + ", ".join(c.name for c in connections) + ".")
    connection = connections[0]
    src = source(db, row)
    pub = db.scalar(select(WebshopPublication).where(WebshopPublication.connection_id == connection.id,
                                                     WebshopPublication.article_id == row.id))
    if pub and pub.state == "published":
        raise ActionError(f"O artigo já está publicado na loja «{connection.name}».")
    net, vat, gross = price_of(pub or WebshopPublication(price_net=None), src)
    blocked = blockers(src, net, vat)
    if blocked:
        raise ActionError("Não é possível publicar: " + " ".join(blocked))
    data = src["data"]
    warnings = []
    if not (data.get("description") or "").strip():
        warnings.append("Sem descritivo para o site na ficha" + (" (há uma proposta por aprovar)." if
                                                                  has_pending_content(db, row.id) or any(
                                                                      a["type"] == "web_content" and a["article_id"] == row.id
                                                                      for a in pending) else "."))
    if not (article_images.photo_url(db, row.id) or data.get("photo")) and not any(
            a["type"] == "image" and a["role"] == "photo" and a["article_id"] == row.id for a in pending):
        warnings.append("Sem fotografia do produto.")
    base = {"type": "publish", "article_id": row.id, "code": row.code, "name": row.name, "shop": connection.name,
            "sku": pub.sku if pub else default_sku(src), "gross": str(gross), "warnings": warnings}
    if pub:
        return {**base, "mode": "repor", "request": {"path": f"/webshop/publications/{pub.id}/publish",
                                                    "body": {"revision": pub.revision}}}
    return {**base, "mode": "nova", "request": {"path": "/webshop/publications",
                                               "body": {"connection_id": connection.id, "article_id": row.id}}}
