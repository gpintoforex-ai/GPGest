"""Descritivo dos artigos para o site, gerado por IA e revisto antes de publicar.

- A IA (Claude, o mesmo serviço do assistente) escreve uma proposta a partir dos dados da ficha (nome, marca,
  família, unidade, códigos) e das imagens do artigo (foto e imagens adicionais por HTTPS: rótulo, embalagem).
- Só usa factos dessas fontes: não inventa composição, posologia, benefícios nem alegações de saúde; as frases que
  precisam de confirmação (alegações de saúde, advertências, dados incompletos) voltam assinaladas.
- A proposta fica pendente (`article_content_proposals`) e nunca é publicada sozinha: só passa para a ficha — e daí
  para a loja online, pelo catálogo — quando alguém a aprova (com edição) ou a grava na ficha. Tudo na auditoria.
- Cada geração gasta créditos da conta Anthropic configurada em ANTHROPIC_API_KEY.
"""

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import current, record
from .assistant import MODEL
from .db import db_session
from .models import ArticleContentProposal, StockArticle, now
from .schemas import Strict

router = APIRouter()
FIELDS = ("description", "web_summary", "web_highlights", "seo_title", "seo_description")

SYSTEM = """És redator de uma loja portuguesa de produtos naturais, suplementos e bem-estar. Escreves o descritivo de um
artigo para o site da loja, em português de Portugal (nunca português do Brasil), com tom informativo, claro e sóbrio.

Regras obrigatórias:
- Usa apenas factos que estejam nos dados do artigo ou que se leiam nas imagens (rótulo, embalagem). Se uma
  informação não estiver lá, não a escrevas: em vez disso, indica-a em «missing_information».
- Nunca inventes composição, ingredientes, quantidades, posologia, modo de utilização, contraindicações,
  certificações ou benefícios.
- Alegações de saúde ou de efeito (por exemplo, «reforça o sistema imunitário», «ajuda a dormir») só podem aparecer
  se estiverem escritas no rótulo, com a mesma formulação; copia cada uma dessas frases para «needs_review».
  Nunca prometas tratar, curar ou prevenir doenças.
- Se o rótulo tiver advertências (crianças, grávidas, dose diária recomendada, não substitui uma alimentação
  variada), inclui-as na descrição e em «needs_review».
- Não escrevas preços, stock, promoções, prazos de entrega nem comparações com outras marcas.
- «description»: 2 a 4 parágrafos curtos em texto simples (sem HTML nem markdown), até 1200 caracteres.
- «summary»: 1 a 2 frases, até 280 caracteres.
- «highlights»: 3 a 6 pontos curtos (até 120 caracteres cada), só com factos confirmados.
- «seo_title»: até 60 caracteres, com o nome do produto e a marca quando existir.
- «seo_description»: até 155 caracteres, sem alegações de saúde.
- «sources_used»: «ficha» e/ou «imagem», conforme o que usaste."""

SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "seo_title": {"type": "string"},
        "seo_description": {"type": "string"},
        "needs_review": {"type": "array", "items": {"type": "string"}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "sources_used": {"type": "array", "items": {"type": "string", "enum": ["ficha", "imagem"]}},
    },
    "required": ["description", "summary", "highlights", "seo_title", "seo_description", "needs_review", "missing_information",
                 "sources_used"],
    "additionalProperties": False,
}


def fail(message, status=422):
    raise HTTPException(status, message)


def access(db, user, identifier):
    """Artigo e permissão de editar a ficha (a mesma da edição de artigos)."""
    from . import inventory as stock
    stock.require(user, stock.settings(db), "adjust_stock")
    return stock.article(db, identifier)


def facts(row):
    """Dados da ficha enviados à IA (nada de custos, preços de compra, fornecedores nem notas internas)."""
    data = json.loads(row.data or "{}")
    return {
        "nome": row.name, "codigo": row.code, "marca": data.get("brand", ""), "familia": data.get("family", ""),
        "categoria": data.get("category", ""), "unidade": data.get("unit", ""), "codigos_de_barras": data.get("barcodes", []),
        "servico": bool(data.get("service")), "descricao_atual": data.get("description", ""),
    }


def images(row):
    data = json.loads(row.data or "{}")
    urls = [data.get("photo") or "", *(data.get("additional_photos") or [])]
    return [url for url in dict.fromkeys(urls) if url.startswith("https://")][:4]


def generate(row):
    """Chama a IA e devolve (proposta, modelo, tokens de entrada, tokens de saída, imagens usadas)."""
    import anthropic

    if not os.getenv("ANTHROPIC_API_KEY"):
        fail("A IA não está configurada: indique ANTHROPIC_API_KEY no ficheiro .env do servidor e reinicie a API.", 409)
    client = anthropic.Anthropic(max_retries=2, timeout=120)
    urls = images(row)

    def ask(with_images):
        content = [{"type": "image", "source": {"type": "url", "url": url}} for url in (urls if with_images else [])]
        content.append({"type": "text", "text": "Dados do artigo (JSON):\n" + json.dumps(facts(row), ensure_ascii=False, sort_keys=True)
                        + ("\n\nAs imagens acima são do artigo (rótulo ou embalagem)." if with_images and urls else "\n\nSem imagens.")})
        return client.beta.messages.create(
            model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )

    try:
        try:
            response, used = ask(True), bool(urls)
        except anthropic.BadRequestError as exc:
            # Imagem inacessível (alojamento privado, endereço caducado): repete só com os dados da ficha.
            if not urls or "image" not in str(exc).lower():
                raise
            response, used = ask(False), False
    except anthropic.AuthenticationError:
        fail("A chave da API da Anthropic foi recusada. Verifique ANTHROPIC_API_KEY no .env do servidor.", 502)
    except anthropic.RateLimitError:
        fail("O serviço de IA está com muitos pedidos neste momento. Tente daqui a um minuto.", 429)
    except anthropic.APIConnectionError:
        fail("Não foi possível contactar o serviço de IA. Verifique a ligação à internet do servidor.", 503)
    except anthropic.APIStatusError as exc:
        message = str((exc.body or {}).get("error", {}).get("message") or "") if isinstance(exc.body, dict) else ""
        if "credit balance" in message.lower():
            fail("A conta Anthropic da chave configurada não tem créditos. Carregue créditos em console.anthropic.com.", 402)
        fail(f"O serviço de IA devolveu um erro ({exc.status_code}){': ' + message[:300] if message else ''}. Tente novamente.", 502)
    if response.stop_reason == "refusal":
        fail("O serviço de IA não gerou texto para este artigo. Escreva o descritivo manualmente.", 422)
    if response.stop_reason == "max_tokens":
        fail("A resposta da IA ficou incompleta. Tente novamente.", 502)
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        proposal = json.loads(text)
    except ValueError:
        fail("A resposta da IA não veio no formato esperado. Tente novamente.", 502)
    return proposal, response.model, response.usage.input_tokens or 0, response.usage.output_tokens or 0, used


def fit(proposal):
    """Ajusta a proposta aos limites da ficha (texto da IA nunca deve fazer falhar a gravação)."""
    return {
        "description": proposal.get("description", "").strip()[:4000],
        "web_summary": proposal.get("summary", "").strip()[:300],
        "web_highlights": [h.strip()[:160] for h in proposal.get("highlights", []) if h.strip()][:8],
        "seo_title": proposal.get("seo_title", "").strip()[:70],
        "seo_description": proposal.get("seo_description", "").strip()[:170],
    }


def view(row, proposal):
    data = json.loads(row.data or "{}")
    current_values = {key: data.get(key, [] if key == "web_highlights" else "") for key in FIELDS}
    if not proposal:
        return {"article_id": row.id, "revision": row.revision, "current": current_values, "proposal": None}
    stored = json.loads(proposal.data or "{}")
    return {"article_id": row.id, "revision": row.revision, "current": current_values,
            "proposal": {"id": proposal.id, "status": proposal.status, "fields": stored.get("fields", {}),
                         "needs_review": stored.get("needs_review", []), "missing_information": stored.get("missing_information", []),
                         "sources_used": stored.get("sources_used", []), "images_used": stored.get("images_used", False),
                         "model": proposal.model, "created_at": proposal.created_at.isoformat() if proposal.created_at else None}}


def proposal_of(db, article_id):
    return db.scalar(select(ArticleContentProposal).where(ArticleContentProposal.article_id == article_id))


@router.get("/api/stock/articles/{identifier}/web-content")
def get_content(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    row = access(db, user, identifier)
    return {**view(row, proposal_of(db, row.id)), "configured": bool(os.getenv("ANTHROPIC_API_KEY"))}


@router.post("/api/stock/articles/{identifier}/web-content")
def create_proposal(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    """Gera uma proposta nova (substitui a anterior pendente). Não altera a ficha."""
    row = access(db, user, identifier)
    proposal, model, input_tokens, output_tokens, used = generate(row)
    item = proposal_of(db, row.id) or ArticleContentProposal(article_id=row.id, created_by=user.id)
    item.status, item.model, item.input_tokens, item.output_tokens = "pendente", model, input_tokens, output_tokens
    item.created_by, item.created_at, item.decided_by, item.decided_at = user.id, now(), None, None
    item.data = json.dumps({"fields": fit(proposal), "needs_review": proposal.get("needs_review", [])[:20],
                            "missing_information": proposal.get("missing_information", [])[:20],
                            "sources_used": proposal.get("sources_used", []), "images_used": used}, ensure_ascii=False)
    db.add(item)
    db.flush()
    record(db, user, "artigo_descritivo_ia_gerado", row.id, {"modelo": model, "input_tokens": input_tokens,
                                                             "output_tokens": output_tokens, "imagens": used})
    db.commit()
    return view(row, item)


class Approval(Strict):
    description: str = Field(default="", max_length=4000)
    web_summary: str = Field(default="", max_length=300)
    web_highlights: list[str] = Field(default_factory=list, max_length=8)
    seo_title: str = Field(default="", max_length=70)
    seo_description: str = Field(default="", max_length=170)
    revision: int


@router.post("/api/stock/articles/{identifier}/web-content/approve")
def approve(identifier: str, data: Approval, user=Depends(current), db: Session = Depends(db_session)):
    """Aprova a proposta (com as edições de quem revê): grava os campos do site na ficha, pela gravação normal."""
    from . import inventory as stock
    stock.lock(db)
    row = access(db, user, identifier)
    if row.revision != data.revision:
        fail("O artigo foi alterado entretanto. Reabra a proposta antes de aprovar.", 409)
    item = proposal_of(db, row.id)
    if not item or item.status != "pendente":
        fail("Não há proposta pendente para este artigo.", 409)
    stored = {key: value for key, value in json.loads(row.data or "{}").items() if key in stock.ArticleInput.model_fields}
    fields = data.model_dump(exclude={"revision"})
    try:
        article = stock.ArticleInput(**{**stored, **fields, "code": row.code, "name": row.name, "active": row.active, "revision": row.revision})
    except ValueError as exc:
        fail(f"A ficha do artigo tem dados inválidos e não pode ser gravada assim: {str(exc)[:300]}")
    before = {key: stored.get(key, [] if key == "web_highlights" else "") for key in FIELDS}
    stock.save_article(db, user, article, stock.article(db, row.id, True, stock=True))
    item.status, item.decided_by, item.decided_at = "aprovada", user.id, now()
    record(db, user, "artigo_descritivo_aprovado", row.id, {"antes": before, "depois": fields, "proposta": item.id,
                                                            "editado": fields != json.loads(item.data).get("fields")})
    db.commit()
    return view(db.get(StockArticle, row.id), item)


@router.post("/api/stock/articles/{identifier}/web-content/discard")
def discard(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    row = access(db, user, identifier)
    item = proposal_of(db, row.id)
    if not item or item.status != "pendente":
        fail("Não há proposta pendente para este artigo.", 409)
    item.status, item.decided_by, item.decided_at = "descartada", user.id, now()
    record(db, user, "artigo_descritivo_descartado", row.id, {"proposta": item.id})
    db.commit()
    return view(row, item)


def settle_on_save(db, user, article_id, payload):
    """Gravação da ficha com o texto proposto (usado no formulário): a proposta pendente fica «aplicada»."""
    item = proposal_of(db, article_id)
    if item and item.status == "pendente" and payload.get("description") and \
            payload.get("description") == json.loads(item.data or "{}").get("fields", {}).get("description"):
        item.status, item.decided_by, item.decided_at = "aplicada", user.id, now()


@router.get("/api/stock/web-content")
def queue(state: str = "sem_descritivo", q: str = "", offset: int = 0, user=Depends(current), db: Session = Depends(db_session)):
    """Lote: artigos ativos sem descritivo para o site, ou com proposta por rever."""
    from . import inventory as stock
    stock.require(user, stock.settings(db), "adjust_stock")
    proposals = {p.article_id: p for p in db.scalars(select(ArticleContentProposal))}
    text = q.strip().casefold()
    rows = []
    for article in db.scalars(select(StockArticle).where(StockArticle.active.is_(True)).order_by(StockArticle.name)):
        data = json.loads(article.data or "{}")
        if data.get("service") or text and text not in f"{article.code} {article.name} {data.get('brand', '')}".casefold():
            continue
        proposal = proposals.get(article.id)
        pending = bool(proposal and proposal.status == "pendente")
        missing = not (data.get("description") or "").strip()
        if state == "sem_descritivo" and not (missing and not pending) or state == "por_rever" and not pending:
            continue
        rows.append({"id": article.id, "code": article.code, "name": article.name, "brand": data.get("brand", ""), "family": data.get("family", ""),
                     "photo": data.get("photo", ""), "has_description": not missing, "has_images": bool(images(article)),
                     "proposal_status": proposal.status if proposal else None})
    return {"rows": rows[max(0, offset):max(0, offset) + 50], "total": len(rows), "configured": bool(os.getenv("ANTHROPIC_API_KEY"))}
