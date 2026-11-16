"""Imagens carregadas na ficha do artigo: fotografia principal e fotografia do rótulo.

- Guardadas na base da empresa (`article_images`), uma por papel e artigo; carregar outra substitui a anterior.
- Normalizadas ao carregar: orientação da câmara aplicada, lado maior até 2000 px, JPEG sem metadados (a
  localização GPS e o modelo do telemóvel não ficam guardados).
- Fotografia (`photo`): substitui o endereço HTTPS da ficha como fotografia principal, na aplicação e na loja
  online. A loja descarrega-a por um endereço público que inclui o hash do conteúdo (muda quando a fotografia
  muda; sem o hash certo não se obtém nada).
- Rótulo (`label`): interno por omissão (só a IA o lê para propor o descritivo). Publicado na ficha (legível e em
  português), vai para a loja como rótulo da informação alimentar e, se pedido, como última imagem da galeria; só
  então tem endereço público. Substituir a imagem volta a pedir a confirmação.
"""

import base64
import hashlib
import io
import os
from contextlib import contextmanager

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import current, record
from .db import company_db, db_session, next_platform_session
from .models import ArticleImage, now
from .platform_models import Company
from .schemas import Strict

router = APIRouter()

ROLES = {"photo": "fotografia", "label": "rótulo"}
MAX_UPLOAD = 20 * 1024 * 1024
MAX_PIXELS = 80_000_000
MAX_SIDE = 2000
PUBLIC_PREFIX = "/api/public/article-images"


def fail(message, status=422):
    raise HTTPException(status, message)


def normalize(raw):
    """Valida e converte a imagem: (bytes JPEG, largura, altura). Recusa ficheiros que não sejam imagens."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "MPO"}:
                fail("Formato de imagem não suportado. Use JPG, PNG ou WebP.")
            if img.width * img.height > MAX_PIXELS:
                fail("A imagem tem demasiados píxeis. Use uma fotografia até 80 megapíxeis.")
            if img.format in {"JPEG", "MPO"}:
                img.draft("RGB", (MAX_SIDE * 2, MAX_SIDE * 2))
            img.seek(0)
            picture = ImageOps.exif_transpose(img)
            if picture.mode in {"RGBA", "LA", "P"}:
                # Transparência (PNG) sobre fundo branco, como nas fotografias de produto.
                rgba = picture.convert("RGBA")
                picture = Image.new("RGB", rgba.size, (255, 255, 255))
                picture.paste(rgba, mask=rgba.getchannel("A"))
            else:
                picture = picture.convert("RGB")
            picture.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            picture.save(out, "JPEG", quality=86, optimize=True, progressive=True)
            return out.getvalue(), picture.width, picture.height
    except HTTPException:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        fail("O ficheiro não é uma imagem válida. Use uma fotografia JPG, PNG ou WebP.")


def role_of(role):
    if role not in ROLES:
        fail("Tipo de imagem inválido.", 404)
    return role


def editable(db, user, identifier):
    """Artigo e permissão de editar a ficha (a mesma da gravação de artigos). Vale também para artigos do ERP:
    as imagens são dados locais."""
    from . import inventory as stock

    stock.require(user, stock.settings(db), "adjust_stock")
    return stock.article(db, identifier)


def uploaded(db):
    """{artigo: {papel: (id, sha256, público, galeria)}} das imagens carregadas, lido uma vez por pedido."""
    cached = db.info.get("article_images")
    if cached is None:
        cached = {}
        for article_id, role, image_id, sha, public, gallery in db.execute(
                select(ArticleImage.article_id, ArticleImage.role, ArticleImage.id, ArticleImage.sha256,
                       ArticleImage.public, ArticleImage.gallery)):
            cached.setdefault(article_id, {})[role] = (image_id, sha, bool(public), bool(gallery))
        db.info["article_images"] = cached
    return cached


def app_url(article_id, role, sha):
    """Endereço da imagem na aplicação (sessão iniciada); o hash evita mostrar uma versão antiga."""
    return f"/api/stock/articles/{article_id}/images/{role}/{sha[:32]}.jpg"


def photo_url(db, article_id):
    """Fotografia carregada para mostrar na aplicação, ou ''."""
    item = uploaded(db).get(article_id, {}).get("photo")
    return app_url(article_id, "photo", item[1]) if item else ""


def public_path(db, article_id, role="photo"):
    """Caminho público da fotografia (ou do rótulo publicado) para a loja online, ou ''. Relativo à origem da API."""
    item = uploaded(db).get(article_id, {}).get(role)
    company = db.info.get("company", {}).get("id")
    if not item or not company or (role == "label" and not item[2]):
        return ""
    return f"{PUBLIC_PREFIX}/{company}/{item[0]}/{item[1][:32]}.jpg"


def label_in_gallery(db, article_id):
    item = uploaded(db).get(article_id, {}).get("label")
    return bool(item and item[2] and item[3])


def public_origin(request: Request):
    """Origem pública da API, como a loja a vê (atrás do Cloudflare Worker: cabeçalhos X-Forwarded-*)."""
    configured = os.getenv("PUBLIC_API_ORIGIN", "").strip().rstrip("/")
    if configured:
        return configured
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if host and proto in {"http", "https"}:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


def ai_images(db, article_id):
    """Imagens carregadas para a IA, rótulo primeiro: [(papel, conteúdo em base64)]."""
    rows = db.scalars(select(ArticleImage).where(ArticleImage.article_id == article_id))
    order = {"label": 0, "photo": 1}
    return [(row.role, base64.standard_b64encode(row.data).decode())
            for row in sorted(rows, key=lambda r: order[r.role])]


def meta(row):
    return {"id": row.id, "role": row.role, "url": app_url(row.article_id, row.role, row.sha256), "width": row.width,
            "height": row.height, "size": row.size, "created_at": row.created_at,
            "public": bool(row.public), "gallery": bool(row.gallery)}


def listing(db, article_id):
    rows = {row.role: row for row in db.scalars(select(ArticleImage).where(ArticleImage.article_id == article_id))}
    return {role: meta(rows[role]) if role in rows else None for role in ROLES}


@router.get("/api/stock/articles/{identifier}/images")
def images(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    from . import inventory as stock

    row = stock.article(db, identifier)
    return listing(db, row.id)


@router.put("/api/stock/articles/{identifier}/images/{role}")
def upload(identifier: str, role: str, file: UploadFile = File(...), user=Depends(current),
           db: Session = Depends(db_session)):

    role = role_of(role)
    editable(db, user, identifier)
    raw = file.file.read(MAX_UPLOAD + 1)
    if not raw:
        fail("O ficheiro está vazio.")
    if len(raw) > MAX_UPLOAD:
        fail("A imagem excede 20 MB.")
    return store(db, user, identifier, role, raw)


def store(db, user, identifier, role, raw, origin=None):
    """Normaliza e grava a imagem do papel indicado (substitui a anterior), com auditoria."""
    from . import inventory as stock

    content, width, height = normalize(raw)
    stock.lock(db)
    row = editable(db, user, identifier)
    image = db.scalar(select(ArticleImage).where(ArticleImage.article_id == row.id, ArticleImage.role == role))
    before = image.sha256 if image else None
    image = image or ArticleImage(article_id=row.id, role=role)
    image.mime, image.data, image.width, image.height, image.size = "image/jpeg", content, width, height, len(content)
    image.sha256 = hashlib.sha256(content).hexdigest()
    image.created_by, image.created_at = user.id, now()
    # Imagem nova: o rótulo volta a ser interno até alguém confirmar que está legível e em português.
    image.public = image.gallery = False
    db.add(image)
    db.flush()
    record(db, user, "artigo_imagem_carregada", row.id, {"papel": ROLES[role], "sha256": image.sha256,
                                                          "anterior": before, "largura": width, "altura": height,
                                                          "bytes": len(content),
                                                          **({"origem": origin} if origin else {})})
    db.commit()
    db.info.pop("article_images", None)
    return listing(db, row.id)


def fetch(url):
    """Descarrega a imagem (até 20 MB, 20 s, três redirecionamentos, cada um verificado de novo)."""
    from . import web_fetch

    raw, _ = web_fetch.get(url, accept="image/*", limit=MAX_UPLOAD, what="da imagem", too_big="A imagem excede 20 MB.",
                           hint=" Guarde a imagem no computador e use «Escolher ficheiro».")
    return raw


class FromUrl(Strict):
    url: str = Field(min_length=1, max_length=2000)


@router.post("/api/stock/articles/{identifier}/images/{role}/from-url")
def import_url(identifier: str, role: str, data: FromUrl, user=Depends(current),
               db: Session = Depends(db_session)):
    """Guarda na IAZELA a imagem de um endereço HTTPS (a loja e a IA deixam de depender do site de origem)."""
    role = role_of(role)
    editable(db, user, identifier)
    raw = fetch(data.url.strip())
    return store(db, user, identifier, role, raw, origin=data.url.strip()[:500])


@router.delete("/api/stock/articles/{identifier}/images/{role}")
def remove(identifier: str, role: str, user=Depends(current), db: Session = Depends(db_session)):
    from . import inventory as stock

    role = role_of(role)
    stock.lock(db)
    row = editable(db, user, identifier)
    image = db.scalar(select(ArticleImage).where(ArticleImage.article_id == row.id, ArticleImage.role == role))
    if not image:
        fail(f"Este artigo não tem {ROLES[role]} carregada.", 404)
    record(db, user, "artigo_imagem_retirada", row.id, {"papel": ROLES[role], "sha256": image.sha256})
    db.delete(image)
    db.commit()
    db.info.pop("article_images", None)
    return listing(db, row.id)


def jpeg(data):
    return Response(data, media_type="image/jpeg", headers={"Content-Security-Policy": "default-src 'none'"})


@router.get("/api/stock/articles/{identifier}/images/{role}/{name}")
def view_image(identifier: str, role: str, name: str, user=Depends(current), db: Session = Depends(db_session)):
    role = role_of(role)
    image = db.scalar(select(ArticleImage).where(ArticleImage.article_id == identifier, ArticleImage.role == role))
    if not image or name != f"{image.sha256[:32]}.jpg":
        fail("Imagem não encontrada.", 404)
    return jpeg(image.data)


@contextmanager
def company_session(company_id):
    """Sessão da empresa indicada no endereço público (só empresas ativas)."""
    with next_platform_session() as core:
        company = core.get(Company, company_id)
        if not company or not company.active or getattr(company, "removed_at", None):
            fail("Imagem não encontrada.", 404)
        core.expunge(company)
    with company_db(company) as db:
        yield db


@router.get(PUBLIC_PREFIX + "/{company_id}/{image_id}/{name}")
def public_image(company_id: str, image_id: str, name: str):
    """Fotografia principal (e rótulo publicado) para a loja online, sem sessão. Exige o identificador e o hash
    certos; um rótulo não publicado nunca é servido aqui."""
    with company_session(company_id) as db:
        image = db.get(ArticleImage, image_id)
        if not image or (image.role != "photo" and not image.public) or name != f"{image.sha256[:32]}.jpg":
            fail("Imagem não encontrada.", 404)
        return jpeg(image.data)


class LabelPublication(Strict):
    public: bool
    gallery: bool = False


@router.put("/api/stock/articles/{identifier}/images/label/publication")
def publish_label(identifier: str, data: LabelPublication, user=Depends(current), db: Session = Depends(db_session)):
    """Publicar o rótulo no site (confirmado: legível e em português) e, opcionalmente, na galeria do produto."""
    from . import inventory as stock

    stock.lock(db)
    row = editable(db, user, identifier)
    image = db.scalar(select(ArticleImage).where(ArticleImage.article_id == row.id, ArticleImage.role == "label"))
    if not image:
        fail("Carregue primeiro a fotografia do rótulo.", 404)
    before = {"publico": bool(image.public), "galeria": bool(image.gallery)}
    image.public, image.gallery = data.public, data.public and data.gallery
    after = {"publico": image.public, "galeria": image.gallery}
    if before != after:
        record(db, user, "artigo_rotulo_publicacao", row.id, {"antes": before, "depois": after, "sha256": image.sha256})
    db.commit()
    db.info.pop("article_images", None)
    return listing(db, row.id)
