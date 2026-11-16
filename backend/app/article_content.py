"""Descritivo dos artigos para o site, gerado por IA e revisto antes de publicar.

- A IA (Claude, o mesmo serviço do assistente) escreve uma proposta a partir dos dados da ficha (nome, marca,
  família, unidade, códigos) e das imagens do artigo: a fotografia do rótulo e a fotografia carregadas na ficha
  (enviadas à IA diretamente, sem endereço público) e as imagens por HTTPS (fotografia e imagens adicionais).
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

from . import article_images
from .access import current, record
from .assistant import MODEL
from .db import db_session
from .models import ArticleContentProposal, StockArticle, now
from .schemas import Strict

router = APIRouter()
FIELDS = ("description", "web_summary", "web_highlights", "seo_keyword", "seo_title", "seo_description")

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
  variada), inclui-as na descrição e em «needs_review», exceto os avisos gerais acrescentados automaticamente (ver
  «avisos_obrigatorios_automaticos»).
- Se «avisos_obrigatorios_automaticos» for verdadeiro, a loja acrescenta no fim da descrição, com texto fixo, os
  avisos obrigatórios gerais dos suplementos alimentares (suplemento alimentar, não exceder a toma diária, não
  substitui uma alimentação variada, regime alimentar equilibrado e modo de vida saudável, manter fora do alcance
  das crianças, conservação em local fresco e seco). Não os escrevas na descrição nem nos pontos-chave. Escreve só
  as advertências específicas deste rótulo (por exemplo, grávidas, lactantes, crianças, medicação, alergénios), na
  secção «## Advertências» (a loja junta-lhe os avisos gerais no fim), e copia-as para «needs_review».
- Não escrevas preços, stock, promoções, prazos de entrega nem comparações com outras marcas.
- «focus_keyword»: a palavra-chave principal (2 a 4 palavras, em minúsculas), como um cliente pesquisaria este
  produto em Portugal: o tipo de produto e o ingrediente ou a característica principal (e a marca, se for conhecida).
  Tem de corresponder a factos da ficha ou do rótulo.
- «description»: texto simples (sem HTML), até 2000 caracteres, em parágrafos curtos. A primeira frase começa pela
  palavra-chave principal (ou tem-na logo no início); usa-a mais 1 ou 2 vezes, com naturalidade, sem repetições
  forçadas. Quando houver informação suficiente, organiza em secções com um subtítulo curto numa linha própria a
  começar por «## » (por exemplo «## Composição», «## Modo de utilização», «## Advertências»); um dos subtítulos tem
  a palavra-chave. Cada secção só com factos confirmados: nunca escrevas uma secção vazia nem para encher texto.
- «summary»: 1 a 2 frases, até 280 caracteres.
- «highlights»: 3 a 6 pontos curtos (até 120 caracteres cada), só com factos confirmados.
- «seo_title»: até 60 caracteres, a começar pela palavra-chave principal, com o nome comercial ou a marca quando
  couber.
- «seo_description»: até 155 caracteres, com a palavra-chave principal, sem alegações de saúde.
- «sources_used»: «ficha» e/ou «imagem», conforme o que usaste.

Fotografia do rótulo (quando existe, é a fonte mais fiável):
- Lê-a com atenção: denominação, ingredientes, quantidades por dose e %VRN, dose diária recomendada, modo de
  utilização, advertências, conservação, conteúdo líquido e alergénios. Usa essa informação na descrição e nos
  pontos-chave.
- Transcreve números, unidades e nomes de ingredientes exatamente como estão. O que estiver ilegível, cortado ou
  com reflexo não se adivinha: indica-o em «missing_information».
- Se o rótulo não for deste produto (nome ou marca diferentes da ficha), não o uses e indica-o em «needs_review»."""

SCHEMA = {
    "type": "object",
    "properties": {
        "focus_keyword": {"type": "string"},
        "description": {"type": "string"},
        "summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "seo_title": {"type": "string"},
        "seo_description": {"type": "string"},
        "needs_review": {"type": "array", "items": {"type": "string"}},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "sources_used": {"type": "array", "items": {"type": "string", "enum": ["ficha", "imagem"]}},
    },
    "required": ["focus_keyword", "description", "summary", "highlights", "seo_title", "seo_description", "needs_review", "missing_information",
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


def facts(row, notice=False):
    """Dados da ficha enviados à IA (nada de custos, preços de compra, fornecedores nem notas internas)."""
    data = json.loads(row.data or "{}")
    return {
        "nome": row.name, "codigo": row.code, "marca": data.get("brand", ""), "familia": data.get("family", ""),
        "categoria": data.get("category", ""), "unidade": data.get("unit", ""), "codigos_de_barras": data.get("barcodes", []),
        "servico": bool(data.get("service")), "descricao_atual": data.get("description", ""),
        "avisos_obrigatorios_automaticos": bool(notice),
    }


# --- Avisos obrigatórios: texto fixo da empresa acrescentado às propostas das famílias escolhidas ---------------

NOTICE_ROW = "web_content"
DEFAULT_TITLE = "Advertências"
DEFAULT_TEXT = ("Suplemento Alimentar. Não deve exceder a toma diária indicada. Os suplementos alimentares não devem ser "
                "utilizados como substitutos de uma alimentação variada. É importante ter um regime alimentar "
                "equilibrado e um modo de vida saudável. Manter fora do alcance das crianças. Conservar em local "
                "fresco e seco.")


def notice_settings(db):
    """Título, texto e famílias dos avisos obrigatórios. Sem configuração: as famílias com «suplement» no nome."""
    from .models import ArticleFamily, StockSettings

    row = db.get(StockSettings, NOTICE_ROW)
    data = json.loads(row.data or "{}") if row else {}
    families = data.get("families")
    if families is None:
        families = [f.name for f in db.scalars(select(ArticleFamily).order_by(ArticleFamily.name))
                    if "suplement" in f.name.casefold()]
    return {"title": data.get("title", DEFAULT_TITLE), "text": data.get("text", DEFAULT_TEXT), "families": families,
            "configured": row is not None, "revision": row.revision if row else 0}


def notice_for(db, row):
    """Bloco de avisos para o artigo (título e texto), ou '' quando a família não está abrangida."""
    config = db.info.get("web_notice") or notice_settings(db)
    db.info["web_notice"] = config
    family = str(json.loads(row.data or "{}").get("family") or "").strip().casefold()
    if not config["text"].strip() or not family or family not in {f.casefold() for f in config["families"]}:
        return ""
    title = config["title"].strip()
    return "\n".join(part for part in (f"## {title}" if title else "", config["text"].strip()) if part)


def with_notice(description, notice):
    """Acrescenta os avisos à descrição (uma vez, sem os cortar). Se a descrição já tiver uma secção com o mesmo
    subtítulo (por exemplo, «## Advertências» com as advertências do rótulo), o texto entra no fim dessa secção, em vez
    de repetir o subtítulo; senão, vai no fim com o subtítulo."""
    if not notice:
        return description
    head, text = notice.split("\n", 1) if notice.startswith("## ") and "\n" in notice else ("", notice)
    flat = " ".join(description.split())
    # Já escrito (com ou sem o subtítulo): não se repete.
    if " ".join(text.split()) in flat:
        return description
    if head:
        title = head[3:].strip().casefold()
        lines = description.rstrip().split("\n")
        start = next((i for i, line in enumerate(lines)
                      if line.strip().startswith("## ") and line.strip()[3:].strip().casefold() == title), None)
        if start is not None:
            end = next((j for j in range(start + 1, len(lines)) if lines[j].strip().startswith("## ")), len(lines))
            last = end
            while last > start + 1 and not lines[last - 1].strip():
                last -= 1
            merged = "\n".join(lines[:last] + ["", text] + ([""] if end < len(lines) else []) + lines[end:])
            if len(merged) <= 4000:
                return merged
    body = description.rstrip()[: max(0, 4000 - len(notice) - 2)].rstrip()
    return f"{body}\n\n{notice}" if body else notice


def images(row, db=None):
    """Imagens por HTTPS da ficha. Com uma fotografia carregada, o endereço da fotografia deixa de contar."""
    data = json.loads(row.data or "{}")
    uploaded = article_images.uploaded(db).get(row.id, {}) if db is not None else {}
    urls = ["" if "photo" in uploaded else data.get("photo") or "", *(data.get("additional_photos") or [])]
    return [url for url in dict.fromkeys(urls) if url.startswith("https://")][:4]


def has_images(db, row):
    return bool(article_images.uploaded(db).get(row.id) or images(row, db))


IMAGE_LABELS = {"label": "Fotografia do rótulo do artigo:", "photo": "Fotografia do produto:"}


def generate(db, row, notice=""):
    """Chama a IA e devolve (proposta, modelo, tokens de entrada, tokens de saída, imagens usadas)."""
    import anthropic

    if not os.getenv("ANTHROPIC_API_KEY"):
        fail("A IA não está configurada: indique ANTHROPIC_API_KEY no ficheiro .env do servidor e reinicie a API.", 409)
    client = anthropic.Anthropic(max_retries=2, timeout=120)
    urls = images(row, db)
    stored = article_images.ai_images(db, row.id)

    def ask(with_urls, with_stored=True):
        content = []
        for role, data in stored if with_stored else []:
            content.append({"type": "text", "text": IMAGE_LABELS[role]})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        if with_urls and urls:
            content.append({"type": "text", "text": "Outras imagens do artigo (embalagem ou rótulo):"})
            content.extend({"type": "image", "source": {"type": "url", "url": url}} for url in urls)
        any_image = bool(with_stored and stored) or bool(with_urls and urls)
        content.append({"type": "text", "text": "Dados do artigo (JSON):\n" + json.dumps(facts(row, notice), ensure_ascii=False, sort_keys=True)
                        + ("\n\nAs imagens acima são deste artigo." if any_image else "\n\nSem imagens.")})
        return client.beta.messages.create(
            model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )

    try:
        try:
            response, used = ask(True), bool(urls or stored)
        except anthropic.BadRequestError as exc:
            # Imagem por endereço inacessível (alojamento privado, endereço caducado): repete sem os endereços,
            # mantendo as imagens carregadas; se ainda assim falhar por causa de uma imagem, só com a ficha.
            if not (urls or stored) or "image" not in str(exc).lower():
                raise
            try:
                if not (urls and stored):
                    raise
                response, used = ask(False), True
            except anthropic.BadRequestError as again:
                if "image" not in str(again).lower():
                    raise
                response, used = ask(False, False), False
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
        "seo_keyword": " ".join(proposal.get("focus_keyword", "").split()).lower()[:60],
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
                         "notice": stored.get("notice", False),
                         "model": proposal.model, "created_at": proposal.created_at.isoformat() if proposal.created_at else None}}


def proposal_of(db, article_id):
    return db.scalar(select(ArticleContentProposal).where(ArticleContentProposal.article_id == article_id))


@router.get("/api/stock/articles/{identifier}/web-content")
def get_content(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    row = access(db, user, identifier)
    uploaded = article_images.uploaded(db).get(row.id, {})
    return {**view(row, proposal_of(db, row.id)), "configured": bool(os.getenv("ANTHROPIC_API_KEY")),
            "has_label": "label" in uploaded, "has_images": has_images(db, row)}


@router.post("/api/stock/articles/{identifier}/web-content")
def create_proposal(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    """Gera uma proposta nova (substitui a anterior pendente). Não altera a ficha."""
    row = access(db, user, identifier)
    notice = notice_for(db, row)
    proposal, model, input_tokens, output_tokens, used = generate(db, row, notice)
    fields = fit(proposal)
    fields["description"] = with_notice(fields["description"], notice)
    item = proposal_of(db, row.id) or ArticleContentProposal(article_id=row.id, created_by=user.id)
    item.status, item.model, item.input_tokens, item.output_tokens = "pendente", model, input_tokens, output_tokens
    item.created_by, item.created_at, item.decided_by, item.decided_at = user.id, now(), None, None
    item.data = json.dumps({"fields": fields, "notice": bool(notice), "needs_review": proposal.get("needs_review", [])[:20],
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
    seo_keyword: str = Field(default="", max_length=60)
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
                     "photo": article_images.photo_url(db, article.id) or data.get("photo", ""),
                     "has_description": not missing, "has_images": has_images(db, article),
                     "has_label": "label" in article_images.uploaded(db).get(article.id, {}),
                     "proposal_status": proposal.status if proposal else None})
    return {"rows": rows[max(0, offset):max(0, offset) + 50], "total": len(rows), "configured": bool(os.getenv("ANTHROPIC_API_KEY"))}


class NoticeInput(Strict):
    title: str = Field(default="", max_length=100)
    text: str = Field(default="", max_length=1500)
    families: list[str] = Field(default_factory=list, max_length=200)
    revision: int


@router.get("/api/stock/web-content/settings")
def get_notice(user=Depends(current), db: Session = Depends(db_session)):
    from . import inventory as stock
    from .models import ArticleFamily

    stock.require(user, stock.settings(db), "adjust_stock")
    options = [f.name for f in db.scalars(select(ArticleFamily).where(ArticleFamily.active.is_(True)).order_by(ArticleFamily.name))]
    return {**notice_settings(db), "family_options": options}


@router.put("/api/stock/web-content/settings")
def set_notice(data: NoticeInput, user=Depends(current), db: Session = Depends(db_session)):
    """Avisos obrigatórios acrescentados às propostas de descritivo das famílias escolhidas (não altera fichas)."""
    from . import inventory as stock
    from .models import StockSettings

    stock.require(user, stock.settings(db), "adjust_stock")
    stock.lock(db)
    before = notice_settings(db)
    if before["revision"] != data.revision:
        fail("Os avisos foram alterados entretanto. Atualize antes de gravar.", 409)
    families = sorted({f.strip() for f in data.families if f.strip()}, key=str.casefold)
    value = {"title": data.title.strip(), "text": " ".join(data.text.split()), "families": families}
    row = db.get(StockSettings, NOTICE_ROW) or StockSettings(id=NOTICE_ROW, revision=0, data="{}")
    row.data, row.revision = json.dumps(value, ensure_ascii=False), row.revision + 1
    db.add(row)
    record(db, user, "descritivo_avisos_alterados", NOTICE_ROW,
           {"antes": {k: before[k] for k in ("title", "text", "families")}, "depois": value})
    db.commit()
    db.info.pop("web_notice", None)
    return {**notice_settings(db), "family_options": get_notice(user, db)["family_options"]}
