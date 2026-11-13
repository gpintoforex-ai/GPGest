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

PROPOSAL = {"description": "Spray de própolis em solução sem álcool, em frasco de 30 mL.\n\nAgitar antes de usar.",
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
    request = fake.calls[0]
    assert request["model"] == "claude-opus-5-5" and request["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in request["betas"]
    assert request["output_config"]["format"]["type"] == "json_schema"
    content = request["messages"][0]["content"]
    assert content[0] == {"type": "image", "source": {"type": "url", "url": "https://example.com/rotulo.jpg"}}
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
