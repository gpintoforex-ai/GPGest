"""Descritivo dos artigos para o site com IA: proposta gerada a partir da ficha e das imagens, nunca publicada sem
revisão, aprovação com edição (auditoria antes/depois), gravação pela ficha, descarte, lista para o lote, erros do
serviço. O serviço de IA é simulado: nenhum pedido sai para a Anthropic. Dados fictícios."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from app import article_content
from app.models import ArticleContentProposal, Audit, StockArticle
from sqlalchemy import select
from test_inventory import product, stock_case  # noqa: F401

PROPOSAL = {"focus_keyword": "Própolis  Spray sem Álcool", "description": "Spray de própolis em solução sem álcool, em frasco de 30 mL.\n\nAgitar antes de usar.",
            "summary": "Spray de própolis sem álcool, 30 mL.", "highlights": ["Sem álcool", "Frasco de 30 mL"],
            "seo_title": "Própolis Spray sem álcool 30 mL | Marca Fictícia",
            "seo_description": "Spray de própolis sem álcool em frasco de 30 mL, da Marca Fictícia.",
            "needs_review": ["«Contribui para o normal funcionamento do sistema imunitário» (rótulo)"],
            "missing_information": ["Modo de utilização completo"], "sources_used": ["ficha", "imagem"]}


class Fake:
    """Cliente Anthropic simulado: regista os pedidos e devolve a resposta configurada."""

    def __init__(self, outcomes):
        self.calls, self.outcomes = [], list(outcomes)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        stop, text = outcome
        return SimpleNamespace(stop_reason=stop, model="claude-opus-5-5", content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=300))


@pytest.fixture
def case(stock_case, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-ficticia")
    article = product(stock_case, name="Própolis Spray Fictício", brand="Marca Fictícia", family="Própolis",
                      photo="https://example.com/rotulo.jpg", internal_notes="Margem negociada: não divulgar")

    def install(*outcomes):
        fake = Fake(outcomes)
        monkeypatch.setattr(anthropic, "Anthropic", lambda **_: fake)
        return fake
    return stock_case, article, install


def image_error():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError("Could not process image: unable to download the image URL", response=httpx2.Response(400, request=request),
                                     body={"error": {"message": "Could not process image"}})


def test_proposal_uses_sheet_and_images_and_never_changes_the_article(case):
    stock, article, install = case
    client = stock[0]
    fake = install(("end_turn", json.dumps(PROPOSAL)))
    response = client.post(f"/api/stock/articles/{article}/web-content")
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert proposal["status"] == "pendente" and proposal["fields"]["web_summary"] == PROPOSAL["summary"]
    assert proposal["needs_review"] and proposal["images_used"] is True
    assert proposal["fields"]["seo_keyword"] == "própolis spray sem álcool"  # palavra-chave principal normalizada
    request = fake.calls[0]
    assert request["model"] == "claude-opus-5-5" and request["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in request["betas"]
    assert request["output_config"]["format"]["type"] == "json_schema"
    content = request["messages"][0]["content"]
    assert [b for b in content if b["type"] == "image"] == [
        {"type": "image", "source": {"type": "url", "url": "https://example.com/rotulo.jpg"}}]
    sent = json.loads(content[-1]["text"].split("\n", 1)[1].split("\n\n")[0])
    assert sent["marca"] == "Marca Fictícia" and "Margem negociada" not in content[-1]["text"]  # notas internas não saem
    assert "não inventes" in request["system"][0]["text"].lower() or "nunca inventes" in request["system"][0]["text"].lower()
    with stock[2]() as db:
        assert json.loads(db.get(StockArticle, article).data)["description"] == ""  # a ficha não muda
        assert db.scalar(select(Audit).where(Audit.action == "artigo_descritivo_ia_gerado"))


def test_unreachable_image_retries_with_sheet_only_and_errors_are_explained(case):
    stock, article, install = case
    client = stock[0]
    fake = install(image_error(), ("end_turn", json.dumps(PROPOSAL)))
    response = client.post(f"/api/stock/articles/{article}/web-content")
    assert response.status_code == 200 and response.json()["proposal"]["images_used"] is False
    assert not any(block["type"] == "image" for block in fake.calls[1]["messages"][0]["content"])
    install(("refusal", ""))
    refused = client.post(f"/api/stock/articles/{article}/web-content")
    assert refused.status_code == 422 and "manualmente" in refused.json()["detail"]


def test_approve_with_edits_discard_queue_and_save_from_sheet(case, monkeypatch):
    stock, article, install = case
    client = stock[0]
    install(("end_turn", json.dumps(PROPOSAL)))
    generated = client.post(f"/api/stock/articles/{article}/web-content").json()
    queue = client.get("/api/stock/web-content", params={"state": "por_rever"}).json()
    assert [r["id"] for r in queue["rows"]] == [article]
    fields = {**generated["proposal"]["fields"], "web_summary": "Spray de própolis sem álcool (revisto)."}
    stale = client.post(f"/api/stock/articles/{article}/web-content/approve", json={**fields, "revision": generated["revision"] - 1})
    assert stale.status_code == 409
    approved = client.post(f"/api/stock/articles/{article}/web-content/approve", json={**fields, "revision": generated["revision"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["proposal"]["status"] == "aprovada"
    with stock[2]() as db:
        data = json.loads(db.get(StockArticle, article).data)
        assert (data["web_summary"], data["web_highlights"], data["seo_title"]) == ("Spray de própolis sem álcool (revisto).",
                                                                                   PROPOSAL["highlights"], PROPOSAL["seo_title"])
        assert data["description"] == PROPOSAL["description"] and data["brand"] == "Marca Fictícia"  # o resto da ficha mantém-se
        assert data["seo_keyword"] == "própolis spray sem álcool"
        audit = db.scalar(select(Audit).where(Audit.action == "artigo_descritivo_aprovado"))
        detail = json.loads(audit.detail)
        assert detail["editado"] is True and detail["antes"]["description"] == ""
    assert client.get("/api/stock/web-content", params={"state": "sem_descritivo"}).json()["rows"] == [] or \
        article not in [r["id"] for r in client.get("/api/stock/web-content", params={"state": "sem_descritivo"}).json()["rows"]]
    # Nova proposta descartada não muda nada; outra usada no formulário fica «aplicada» ao gravar a ficha.
    install(("end_turn", json.dumps({**PROPOSAL, "description": "Texto alternativo."})), ("end_turn", json.dumps({**PROPOSAL, "description": "Texto final."})))
    client.post(f"/api/stock/articles/{article}/web-content")
    assert client.post(f"/api/stock/articles/{article}/web-content/discard").json()["proposal"]["status"] == "descartada"
    client.post(f"/api/stock/articles/{article}/web-content")
    sheet = client.get(f"/api/stock/articles/{article}/web-content").json()
    with stock[2]() as db:
        row = db.get(StockArticle, article)
        body = {key: value for key, value in json.loads(row.data).items() if key not in ("price_history",)}
        body.update(code=row.code, name=row.name, active=row.active, revision=row.revision, description="Texto final.")
    saved = client.put(f"/api/stock/articles/{article}", json={k: v for k, v in body.items() if k in __import__("app.inventory", fromlist=["ArticleInput"]).ArticleInput.model_fields})
    assert saved.status_code == 200, saved.text
    with stock[2]() as db:
        assert db.scalar(select(ArticleContentProposal).where(ArticleContentProposal.article_id == article)).status == "aplicada"
    assert sheet["proposal"]["status"] == "pendente"


def test_without_api_key_nothing_is_sent(case, monkeypatch):
    stock, article, install = case
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    fake = install(("end_turn", json.dumps(PROPOSAL)))
    response = stock[0].post(f"/api/stock/articles/{article}/web-content")
    assert response.status_code == 409 and "ANTHROPIC_API_KEY" in response.json()["detail"] and fake.calls == []
    assert article_content.fit({"summary": "x" * 400})["web_summary"] == "x" * 300


def test_mandatory_supplement_notice_is_appended_verbatim_and_configurable(case, monkeypatch):
    """Avisos obrigatórios: texto fixo da empresa no fim da proposta das famílias escolhidas (não é escrito pela IA)."""
    from test_inventory import login, product

    stock, _, install = case
    client = stock[0]
    supplement = product(stock, name="Magnésio Fictício", family="Suplementos Alimentares")
    cosmetic = product(stock, name="Creme Fictício", family="Cosmética")
    settings = client.get("/api/stock/web-content/settings").json()
    assert settings["families"] == ["Suplementos Alimentares"] and settings["text"].startswith("Suplemento Alimentar.")
    assert settings["title"] == "Advertências"
    assert "Cosmética" in settings["family_options"]
    fake = install(("end_turn", json.dumps(PROPOSAL)), ("end_turn", json.dumps(PROPOSAL)))
    generated = client.post(f"/api/stock/articles/{supplement}/web-content").json()["proposal"]
    description = generated["fields"]["description"]
    assert description.startswith(PROPOSAL["description"]) and generated["notice"] is True
    assert description.endswith("\n\n## Advertências\n" + settings["text"])  # subtítulo (H2 no site)
    sent = json.loads(fake.calls[0]["messages"][0]["content"][-1]["text"].split("\n", 1)[1].split("\n\n")[0])
    assert sent["avisos_obrigatorios_automaticos"] is True
    assert "avisos_obrigatorios_automaticos" in fake.calls[0]["system"][0]["text"]
    other = client.post(f"/api/stock/articles/{cosmetic}/web-content").json()["proposal"]
    assert other["fields"]["description"] == PROPOSAL["description"] and other["notice"] is False
    # A IA já escreveu os avisos: não se repetem.
    install(("end_turn", json.dumps({**PROPOSAL, "description": "Texto.\n\nAvisos obrigatórios\n" + settings["text"]})))
    again = client.post(f"/api/stock/articles/{supplement}/web-content").json()["proposal"]["fields"]["description"]
    assert again.count("Manter fora do alcance das crianças") == 1
    # A IA escreveu a secção «## Advertências» com as do rótulo: os avisos gerais entram no fim dela (sem repetir).
    labelled = "Texto.\n\n## Advertências\nContém produtos da colmeia.\n\n## Apresentação\nFrasco de 30 mL."
    install(("end_turn", json.dumps({**PROPOSAL, "description": labelled})))
    merged = client.post(f"/api/stock/articles/{supplement}/web-content").json()["proposal"]["fields"]["description"]
    assert merged == ("Texto.\n\n## Advertências\nContém produtos da colmeia.\n\n" + settings["text"]
                      + "\n\n## Apresentação\nFrasco de 30 mL.")
    # Configuração: texto e famílias da empresa, com controlo de alterações simultâneas e auditoria.
    body = {"title": "Advertências", "text": "Suplemento alimentar.   Conservar ao abrigo da luz.",
            "families": ["Cosmética"], "revision": settings["revision"]}
    saved = client.put("/api/stock/web-content/settings", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["text"] == "Suplemento alimentar. Conservar ao abrigo da luz." and saved.json()["families"] == ["Cosmética"]
    assert client.put("/api/stock/web-content/settings", json=body).status_code == 409
    install(("end_turn", json.dumps(PROPOSAL)), ("end_turn", json.dumps(PROPOSAL)))
    changed = client.post(f"/api/stock/articles/{cosmetic}/web-content").json()["proposal"]["fields"]["description"]
    assert changed.endswith("Advertências\nSuplemento alimentar. Conservar ao abrigo da luz.")
    assert client.post(f"/api/stock/articles/{supplement}/web-content").json()["proposal"]["notice"] is False
    with stock[2]() as db:
        assert db.scalar(select(Audit).where(Audit.action == "descritivo_avisos_alterados"))
    login(client, "operator")
    assert client.put("/api/stock/web-content/settings", json={**body, "revision": 1}).status_code == 403
