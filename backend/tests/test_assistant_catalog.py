# ruff: noqa: F811
"""Assistente IAZELA a preparar um artigo para o site a partir de uma fotografia: fotografia anexada (código de
barras lido), imagem de um endereço, imagens declaradas numa página, descritivo gerado e aprovado, publicação na
loja online. Tudo é só proposto: nada muda antes do pedido de confirmação (o mesmo do ecrã). A Claude API, os
sites e a loja são simulados: nenhum pedido sai da máquina."""

import base64
import io
import json

import pytest
import zxingcpp
from app import article_images
from app.assistant_actions import _page_images
from app.models import ArticleImage, StockArticle, WebshopPublication
from PIL import Image
from sqlalchemy import select
from test_assistant import FakeClient, ask, assistant, confirm  # noqa: F401
from test_inventory import product, stock_case  # noqa: F401
from test_webshop import connection

EAN = "5601234567892"
PROPOSAL = {"focus_keyword": "óleo de coco biológico", "description": "Óleo de coco biológico em frasco de vidro de 500 ml.",
            "summary": "Óleo de coco biológico, 500 ml.", "highlights": ["Biológico", "Frasco de vidro"],
            "seo_title": "Óleo de coco biológico 500 ml", "seo_description": "Óleo de coco biológico em frasco de 500 ml.",
            "needs_review": ["«Rico em ácido láurico» (rótulo)"], "missing_information": ["Modo de conservação"],
            "sources_used": ["ficha", "imagem"]}


def photo(code=EAN):
    """Fotografia de teste com o código de barras da embalagem (PNG)."""
    bars = zxingcpp.create_barcode(code, zxingcpp.BarcodeFormat.EAN13).to_image(scale=4)
    picture = Image.frombuffer("L", (bars.shape[1], bars.shape[0]), bytes(memoryview(bars)), "raw", "L", 0, 1)
    canvas = Image.new("RGB", (picture.width + 80, picture.height + 80), "white")
    canvas.paste(picture.convert("RGB"), (40, 40))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def ask_with_photo(case, plan, question, raw):
    FakeClient.plan = plan
    response = case[0].post("/api/assistente/mensagens", json={
        "messages": [{"role": "user", "content": question}], "store_id": case[4],
        "image": base64.b64encode(raw).decode()})
    assert response.status_code == 200, response.text
    return response.json()


def images_of(case, article):
    with case[2]() as db:
        return {i.role: i for i in db.scalars(select(ArticleImage).where(ArticleImage.article_id == article))}


def test_attached_photo_is_read_and_only_saved_after_confirmation(assistant):
    article = product(assistant, name="Óleo de coco biológico 500 ml", barcodes=[EAN])
    raw = photo()
    result = ask_with_photo(assistant, [("procurar_artigos", {"texto": EAN}),
                                        ("propor_imagem", {"artigo_id": article, "papel": "fotografia", "origem": "anexo"})],
                            "Cria este artigo para o site", raw)
    params = FakeClient.calls[0]
    # O código de barras da fotografia segue no contexto e a fotografia segue para o modelo (JPEG normalizado).
    assert EAN in params["system"][1]["text"] and "anexou uma fotografia" in params["system"][1]["text"]
    image_block, text_block = params["messages"][-1]["content"]
    assert image_block["type"] == "image" and image_block["source"]["media_type"] == "image/jpeg"
    assert text_block == {"type": "text", "text": "Cria este artigo para o site"}
    assert [a["id"] for a in FakeClient.results["procurar_artigos"]["artigos"]] == [article]
    [action] = result["actions"]
    assert action["type"] == "image" and action["source"] == "anexo" and action["role"] == "photo"
    assert action["preview"].startswith("data:image/jpeg;base64,") and action["width"] == 532
    assert action["request"] == {"path": f"/stock/articles/{article}/images/photo", "upload": "anexo"}
    assert images_of(assistant, article) == {}  # nada guardado antes de confirmar
    # Confirmar: o navegador envia a fotografia anexada pelo mesmo pedido da ficha.
    saved = assistant[0].put("/api" + action["request"]["path"], files={"file": ("foto.png", raw, "image/png")})
    assert saved.status_code == 200, saved.text
    assert images_of(assistant, article)["photo"].width == 532


def test_photo_proposals_need_an_attachment_and_the_edit_permission(assistant, monkeypatch):
    from app import inventory

    article = product(assistant, name="Chá verde 100 g")
    result = ask(assistant, [("propor_imagem", {"artigo_id": article, "papel": "rotulo", "origem": "anexo"})])
    assert "Não há fotografia anexada" in FakeClient.results["propor_imagem"]["erro"] and result["actions"] == []
    bad = assistant[0].post("/api/assistente/mensagens", json={"messages": [{"role": "user", "content": "Olá"}], "image": "!!"})
    assert bad.status_code == 422
    monkeypatch.setattr(inventory, "permissions", lambda user, config: {})
    ask_with_photo(assistant, [("propor_imagem", {"artigo_id": article, "papel": "rotulo", "origem": "anexo"})], "Rótulo", photo())
    assert "não permite" in FakeClient.results["propor_imagem"]["erro"]


def test_image_from_url_is_previewed_and_imported_on_confirmation(assistant, monkeypatch):
    article = product(assistant, name="Mel de rosmaninho 500 g")
    fetched = []
    monkeypatch.setattr(article_images, "fetch", lambda url: fetched.append(url) or photo())
    url = "https://marca.example/img/mel.png"
    result = ask(assistant, [("propor_imagem", {"artigo_id": article, "papel": "fotografia", "origem": "url", "url": url})])
    [action] = result["actions"]
    assert action["source"] == "url" and action["site"] == "marca.example" and action["preview"].startswith("data:image/jpeg")
    assert action["request"] == {"path": f"/stock/articles/{article}/images/photo/from-url", "body": {"url": url}}
    assert images_of(assistant, article) == {}
    assert confirm(assistant, action).status_code == 200
    assert "photo" in images_of(assistant, article) and fetched == [url, url]
    ask(assistant, [("propor_imagem", {"artigo_id": article, "papel": "fotografia", "origem": "url", "url": "http://x.example/a.jpg"})])
    assert "HTTPS" in FakeClient.results["propor_imagem"]["erro"]


def test_page_images_come_from_product_data_and_open_graph():
    html = """<html><head><meta property="og:site_name" content="Marca">
      <meta property="og:image" content="/media/frente.jpg">
      <script type="application/ld+json">{"@graph": [{"@type": "Product", "name": "Mel",
        "image": ["https://marca.example/media/grande.jpg", {"url": "https://marca.example/media/verso.jpg"}]}]}</script>
      </head><body></body></html>"""
    images, site = _page_images(html, "https://marca.example/produtos/mel")
    assert site == "Marca"
    assert images == ["https://marca.example/media/grande.jpg", "https://marca.example/media/verso.jpg",
                      "https://marca.example/media/frente.jpg"]


def test_description_is_generated_as_proposal_and_approved_from_the_card(assistant):
    article = product(assistant, name="Óleo de coco biológico 500 ml", brand="Marca Fictícia")
    FakeClient.proposal = PROPOSAL
    # Com uma imagem por confirmar nesta resposta, espera pela confirmação (a IA lê as imagens guardadas).
    ask_with_photo(assistant, [("propor_imagem", {"artigo_id": article, "papel": "rotulo", "origem": "anexo"}),
                               ("gerar_descritivo", {"artigo_id": article})], "Cria o descritivo", photo())
    assert "por confirmar" in FakeClient.results["gerar_descritivo"]["erro"] and FakeClient.generated == []
    result = ask(assistant, [("gerar_descritivo", {"artigo_id": article})])
    assert FakeClient.results["gerar_descritivo"]["confirmar_antes_de_publicar"] == PROPOSAL["needs_review"]
    [action] = result["actions"]
    assert action["type"] == "web_content" and action["fields"]["web_summary"] == PROPOSAL["summary"]
    with assistant[2]() as db:
        assert json.loads(db.get(StockArticle, article).data).get("description", "") == ""  # a ficha não muda
    # Aprovar no cartão (com edição): o endpoint de aprovação grava na ficha, com controlo de revisão.
    fields = {**action["fields"], "web_summary": "Óleo de coco biológico (revisto)."}
    approved = assistant[0].post("/api" + action["request"]["path"], json={**fields, "revision": action["request"]["revision"]})
    assert approved.status_code == 200, approved.text
    with assistant[2]() as db:
        data = json.loads(db.get(StockArticle, article).data)
        assert data["description"] == PROPOSAL["description"] and data["web_summary"] == "Óleo de coco biológico (revisto)."


def test_publication_is_validated_and_only_created_on_confirmation(assistant):
    article = product(assistant, name="Óleo de coco biológico 500 ml", sale_net="5")
    # Sem loja online ligada: o motivo segue para o modelo.
    ask(assistant, [("propor_publicacao", {"artigo_id": article})])
    assert "Não há loja online" in FakeClient.results["propor_publicacao"]["erro"]
    conn, _ = connection(assistant)
    [action] = ask(assistant, [("propor_publicacao", {"artigo_id": article})])["actions"]
    assert action["type"] == "publish" and action["mode"] == "nova" and action["shop"] == "Raizpura"
    assert action["gross"] == "6.15" and "Sem descritivo para o site na ficha." in action["warnings"]
    assert "Sem fotografia do produto." in action["warnings"]
    assert action["request"] == {"path": "/webshop/publications", "body": {"connection_id": conn["id"], "article_id": article}}
    with assistant[2]() as db:
        assert db.scalar(select(WebshopPublication).where(WebshopPublication.article_id == article)) is None
    assert confirm(assistant, action).status_code == 200
    with assistant[2]() as db:
        assert db.scalar(select(WebshopPublication).where(WebshopPublication.article_id == article)).state == "published"
    ask(assistant, [("propor_publicacao", {"artigo_id": article})])
    assert "já está publicado" in FakeClient.results["propor_publicacao"]["erro"]
    # Artigo sem preço: não é proposto.
    free = product(assistant, name="Amostra", sale_net="0")
    ask(assistant, [("propor_publicacao", {"artigo_id": free})])
    assert "Preço de venda" in FakeClient.results["propor_publicacao"]["erro"]


def test_web_search_can_be_turned_off(assistant, monkeypatch):
    monkeypatch.setenv("ASSISTENTE_PESQUISA_WEB", "0")
    ask(assistant, [])
    assert not [t for t in FakeClient.calls[0]["tools"] if isinstance(t, dict)]


@pytest.fixture(autouse=True)
def proposal():
    FakeClient.proposal = PROPOSAL
