# ruff: noqa: F811
"""Imagens carregadas na ficha do artigo: normalização (orientação, dimensão, JPEG sem metadados), substituição,
permissões e auditoria; fotografia na loja online por endereço público com hash; rótulo nunca público e lido
pela IA. Imagens geradas no teste; a loja e a IA são simuladas."""

import io
import json
from contextlib import contextmanager

import anthropic
import pytest
from app import article_images
from app.db import db_session
from app.main import app
from app.models import ArticleImage, Audit
from PIL import Image
from sqlalchemy import func, select
from test_article_content import PROPOSAL, Fake
from test_inventory import login, product, stock_case, view  # noqa: F401
from test_webshop import connection
from test_webshop_catalog import BASE, feed, ok, publish, report


@pytest.fixture
def shop_case(stock_case, monkeypatch):
    """Empresa identificada na sessão (como em produção), para o endereço público da fotografia."""
    factory = stock_case[2]

    def override():
        with factory() as db:
            db.info["company"] = {"id": "empresa-teste"}
            yield db

    @contextmanager
    def company_session(company_id):
        if company_id != "empresa-teste":
            article_images.fail("Imagem não encontrada.", 404)
        with factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, db_session, override)
    monkeypatch.setattr(article_images, "company_session", company_session)
    return stock_case


def picture(size=(3000, 1000), color=(200, 30, 30), fmt="PNG", mode="RGB", **save):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, fmt, **save)
    return out.getvalue()


def upload(client, article, role, raw, name="foto.png", mime="image/png"):
    return client.put(f"/api/stock/articles/{article}/images/{role}", files={"file": (name, raw, mime)})


def opened(raw):
    image = Image.open(io.BytesIO(raw))
    image.load()
    return image


def test_upload_normalizes_replaces_and_is_audited(stock_case):
    c, _, factory = stock_case[0], stock_case[1], stock_case[2]
    article = product(stock_case)
    assert c.get(f"/api/stock/articles/{article}/images").json() == {"photo": None, "label": None}
    # PNG transparente e grande: fica JPEG com fundo branco e lado maior de 2000 px.
    first = upload(c, article, "photo", picture(mode="RGBA", color=(0, 0, 0, 0)))
    assert first.status_code == 200, first.text
    photo = first.json()["photo"]
    assert (photo["width"], photo["height"]) == (2000, 667) and first.json()["label"] is None
    served = c.get(photo["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"
    assert opened(served.content).getpixel((10, 10))[0] > 240  # fundo branco
    assert view(stock_case, article)["image_photo"] == photo["url"]
    # Orientação da câmara aplicada e metadados (EXIF/GPS) removidos.
    exif = Image.Exif()
    exif[0x0112] = 6  # rodar 90°
    exif[0x010F] = "Telemovel Ficticio"
    rotated = upload(c, article, "photo", picture((400, 200), fmt="JPEG", exif=exif.tobytes()), "camera.jpg", "image/jpeg")
    assert rotated.status_code == 200, rotated.text
    replaced = rotated.json()["photo"]
    assert (replaced["width"], replaced["height"]) == (200, 400) and replaced["url"] != photo["url"]
    stored = c.get(replaced["url"]).content
    assert not dict(opened(stored).getexif()) and b"Telemovel" not in stored
    assert c.get(photo["url"]).status_code == 404  # a versão antiga deixa de existir
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ArticleImage)) == 1
        actions = [a.action for a in db.scalars(select(Audit).where(Audit.target_id == article))]
        assert actions.count("artigo_imagem_carregada") == 2
    # Ficheiros inválidos e permissões.
    bad = upload(c, article, "photo", b"isto nao e uma imagem", "x.jpg", "image/jpeg")
    assert bad.status_code == 422 and "imagem válida" in bad.json()["detail"]
    assert upload(c, article, "outra", picture()).status_code == 404
    assert upload(c, "inexistente", "photo", picture()).status_code == 404
    login(c, "operator")
    assert upload(c, article, "label", picture()).status_code == 403
    assert c.delete(f"/api/stock/articles/{article}/images/photo").status_code == 403
    login(c, "admin")
    removed = c.delete(f"/api/stock/articles/{article}/images/photo")
    assert removed.status_code == 200 and removed.json()["photo"] is None
    assert c.delete(f"/api/stock/articles/{article}/images/photo").status_code == 404
    assert view(stock_case, article)["image_photo"] == ""


def test_shop_receives_uploaded_photo_by_public_hashed_url_and_never_the_label(shop_case):
    stock_case = shop_case
    c = stock_case[0]
    article = product(stock_case, photo="https://example.test/a.jpg")
    conn, headers = connection(stock_case)
    publish(stock_case, conn, article)
    entry = feed(stock_case, headers)[0]
    assert entry["images"] == ["https://example.test/a.jpg"]
    report(stock_case, headers, ok(entry, 701))
    assert feed(stock_case, headers) == []
    # O rótulo não muda o que vai para a loja.
    assert upload(c, article, "label", picture((800, 1200))).status_code == 200
    assert feed(stock_case, headers) == []
    # A fotografia carregada prevalece sobre o endereço e volta a enviar a publicação.
    photo = upload(c, article, "photo", picture()).json()["photo"]
    changed = feed(stock_case, headers)
    assert len(changed) == 1 and changed[0]["hash"] != entry["hash"]
    url = changed[0]["images"][0]
    assert url.startswith("http://testserver/api/public/article-images/") and url.endswith(".jpg")
    assert len(changed[0]["images"]) == 1
    # Origem pública atrás do Cloudflare Worker.
    behind = c.get(BASE + "/publications-feed", headers={**headers, "X-Forwarded-Host": "loja.example.pt",
                                                         "X-Forwarded-Proto": "https"}).json()["items"][0]
    assert behind["images"][0].startswith("https://loja.example.pt/api/public/article-images/")
    assert behind["hash"] == changed[0]["hash"]  # o hash não depende da origem
    # Endereço público sem sessão: só com o hash certo, e nunca para o rótulo.
    path = url.removeprefix("http://testserver")
    c.cookies.clear()
    public = c.get(path)
    assert public.status_code == 200 and public.headers["content-type"] == "image/jpeg"
    company, image_id, name = path.rsplit("/", 3)[1:]
    assert c.get(f"/api/public/article-images/{company}/{image_id}/{'0' * 32}.jpg").status_code == 404
    assert c.get(f"/api/public/article-images/outra/{image_id}/{name}").status_code == 404
    assert c.get(photo["url"]).status_code == 401  # o endereço da aplicação exige sessão
    login(c, "admin")
    label = c.get(f"/api/stock/articles/{article}/images").json()["label"]
    label_name = label["url"].rsplit("/", 1)[1]
    c.cookies.clear()
    assert c.get(f"/api/public/article-images/{company}/{label['id']}/{label_name}").status_code == 404
    login(c, "admin")
    # Confirmado o envio, nada fica pendente; retirar a fotografia volta ao endereço da ficha.
    report(stock_case, headers, ok(changed[0], 701))
    assert feed(stock_case, headers) == []
    c.delete(f"/api/stock/articles/{article}/images/photo")
    assert feed(stock_case, headers)[0]["images"] == ["https://example.test/a.jpg"]


def test_ai_reads_label_and_uploaded_photo_before_the_sheet(stock_case, monkeypatch):
    c = stock_case[0]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-ficticia")
    article = product(stock_case, name="Magnésio Fictício", photo="https://example.com/foto.jpg")
    before = c.get(f"/api/stock/articles/{article}/web-content").json()
    assert before["has_label"] is False and before["has_images"] is True
    upload(c, article, "label", picture((900, 1400), color=(240, 240, 240)))
    upload(c, article, "photo", picture((900, 900)))
    fake = Fake([("end_turn", json.dumps(PROPOSAL))])
    monkeypatch.setattr(anthropic, "Anthropic", lambda **_: fake)
    response = c.post(f"/api/stock/articles/{article}/web-content")
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["images_used"] is True
    content = fake.calls[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Fotografia do rótulo do artigo:"}
    assert content[1]["type"] == "image" and content[1]["source"]["type"] == "base64"
    assert content[1]["source"]["media_type"] == "image/jpeg" and len(content[1]["source"]["data"]) > 100
    assert content[2]["text"] == "Fotografia do produto:" and content[3]["source"]["type"] == "base64"
    # Com fotografia carregada, o endereço da fotografia já não é enviado.
    assert not any(b.get("source", {}).get("type") == "url" for b in content)
    assert "rótulo" in fake.calls[0]["system"][0]["text"].lower()
    after = c.get(f"/api/stock/articles/{article}/web-content").json()
    assert after["has_label"] is True
    queue = c.get("/api/stock/web-content", params={"state": "por_rever"}).json()["rows"]
    row = next(r for r in queue if r["id"] == article)
    assert row["has_label"] is True and row["photo"].startswith(f"/api/stock/articles/{article}/images/photo/")


def test_photo_imported_from_https_address_with_network_safeguards(stock_case, monkeypatch):
    """«Guardar esta imagem na IAZELA»: o servidor descarrega o endereço (rede simulada) e guarda-o como fotografia."""
    import socket

    import httpx

    c, factory = stock_case[0], stock_case[2]
    article = product(stock_case, photo="https://loja.exemplo.pt/produto.jpg")
    hosts = {"loja.exemplo.pt": "93.184.216.34", "cdn.exemplo.pt": "93.184.216.35", "interno.exemplo.pt": "10.0.0.5"}
    real = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        if host in hosts:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (hosts[host], 443))]
        return real(host, *args, **kwargs)

    def site(request):
        path = request.url.path
        if path == "/produto.jpg":
            return httpx.Response(302, headers={"location": "https://cdn.exemplo.pt/imagens/produto.jpg"})
        if path == "/imagens/produto.jpg":
            return httpx.Response(200, content=picture((1200, 900)), headers={"content-type": "image/png"})
        if path == "/para-dentro.jpg":
            return httpx.Response(301, headers={"location": "https://interno.exemplo.pt/segredo.jpg"})
        if path == "/texto.jpg":
            return httpx.Response(200, content=b"<html>nao e imagem</html>")
        return httpx.Response(403)

    original = httpx.Client
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(site), **kw))
    path = f"/api/stock/articles/{article}/images/photo/from-url"
    saved = c.post(path, json={"url": "https://loja.exemplo.pt/produto.jpg"})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["photo"]["width"], saved.json()["photo"]["height"]) == (1200, 900)
    assert view(stock_case, article)["image_photo"] == saved.json()["photo"]["url"]
    with factory() as db:
        audit = db.scalar(select(Audit).where(Audit.target_id == article, Audit.action == "artigo_imagem_carregada"))
        assert json.loads(audit.detail)["origem"] == "https://loja.exemplo.pt/produto.jpg"
    # Proteções: só HTTPS na porta normal, nunca a rede interna (também depois de um redirecionamento).
    for url, code, text in (("http://loja.exemplo.pt/produto.jpg", 422, "HTTPS"),
                            ("https://loja.exemplo.pt:8443/produto.jpg", 422, "porta"),
                            ("https://127.0.0.1/produto.jpg", 422, "rede interna"),
                            ("https://interno.exemplo.pt/produto.jpg", 422, "rede interna"),
                            ("https://loja.exemplo.pt/para-dentro.jpg", 422, "rede interna"),
                            ("https://loja.exemplo.pt/bloqueado.jpg", 502, "HTTP 403"),
                            ("https://loja.exemplo.pt/texto.jpg", 422, "imagem válida")):
        refused = c.post(path, json={"url": url})
        assert refused.status_code == code and text in refused.json()["detail"], (url, refused.text)
    login(c, "operator")
    assert c.post(path, json={"url": "https://loja.exemplo.pt/produto.jpg"}).status_code == 403


def test_label_goes_to_the_shop_only_when_confirmed_legible_in_portuguese(shop_case):
    c, factory = shop_case[0], shop_case[2]
    article = product(shop_case, photo="https://example.test/a.jpg")
    path = f"/api/stock/articles/{article}/images/label/publication"
    assert c.put(path, json={"public": True}).status_code == 404  # ainda sem rótulo
    label = upload(c, article, "label", picture((800, 1200))).json()["label"]
    assert (label["public"], label["gallery"]) == (False, False)
    conn, headers = connection(shop_case)
    publish(shop_case, conn, article)
    entry = feed(shop_case, headers)[0]
    assert entry["label_image"] == "" and entry["images"] == ["https://example.test/a.jpg"]
    report(shop_case, headers, ok(entry, 721))
    # Publicado (sem galeria): campo do rótulo na loja, servido pelo endereço público.
    published = c.put(path, json={"public": True, "gallery": False})
    assert published.status_code == 200 and published.json()["label"]["public"] is True
    sent = feed(shop_case, headers)[0]
    assert sent["label_image"].startswith("http://testserver/api/public/article-images/")
    assert sent["images"] == ["https://example.test/a.jpg"]
    c.cookies.clear()
    assert c.get(sent["label_image"].removeprefix("http://testserver")).status_code == 200
    login(c, "admin")
    # Também na galeria: sempre a última imagem.
    c.put(path, json={"public": True, "gallery": True})
    sent = feed(shop_case, headers)[0]
    assert sent["images"] == ["https://example.test/a.jpg", sent["label_image"]]
    # Uma galeria sem publicação não existe.
    assert c.put(path, json={"public": False, "gallery": True}).json()["label"]["gallery"] is False
    c.put(path, json={"public": True, "gallery": True})
    # Rótulo novo: volta a ser interno até nova confirmação (e o endereço antigo deixa de servir).
    current = feed(shop_case, headers)[0]
    old = current["label_image"].removeprefix("http://testserver")
    report(shop_case, headers, ok(current, 721))  # a loja aplicou o rótulo publicado
    replaced = upload(c, article, "label", picture((900, 1300), color=(10, 120, 40))).json()["label"]
    assert (replaced["public"], replaced["gallery"]) == (False, False)
    sent = feed(shop_case, headers)[0]
    assert sent["label_image"] == "" and sent["images"] == ["https://example.test/a.jpg"]
    c.cookies.clear()
    assert c.get(old).status_code == 404
    login(c, "admin")
    with factory() as db:
        assert db.scalar(select(Audit).where(Audit.action == "artigo_rotulo_publicacao"))
    login(c, "operator")
    assert c.put(path, json={"public": True}).status_code == 403
