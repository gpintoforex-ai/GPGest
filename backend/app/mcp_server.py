"""Conector MCP da IAZELA para o Claude (claude.ai, app e Claude Code): consultas só de leitura.

Permite perguntar no chat do Claude pelas vendas, artigos, clientes, IVA, validades e documentos da empresa,
com os mesmos números do assistente IAZELA (são as mesmas ferramentas de consulta).

- Desligado por omissão: só responde com MCP_SECRET (32 caracteres ou mais) no .env do servidor.
- Protocolo MCP «Streamable HTTP» sem estado, em POST /api/mcp (respostas JSON, sem SSE).
- Autorização OAuth 2.1 com registo dinâmico de clientes (RFC 7591) e PKCE S256: o utilizador entra com o NIF
  da empresa, o email e a palavra-passe da IAZELA numa página da própria IAZELA (o Claude nunca vê a
  palavra-passe). Só administradores e gestores, como o assistente.
- Tokens assinados (HMAC-SHA256 com uma chave derivada de MCP_SECRET), sem tabelas novas: acesso 1 hora,
  renovação 30 dias com rotação. Mudar MCP_SECRET revoga todos; desativar o utilizador ou a empresa revoga
  os dele no pedido seguinte.
- Cada consulta corre com as permissões e as lojas do utilizador e fica na auditoria (`mcp_consulta`).
  Nenhuma ferramenta grava: as propostas (preços, imagens, publicação) ficam só no painel da IAZELA.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select

from .access import record
from .db import company_db, next_platform_session
from .models import LoginSession, User, now
from .platform_models import Company

router = APIRouter()

PATH = "/api/mcp"
SCOPE = "iazela.consulta"
ROLES = {"administrador", "gestor"}
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 120
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
# Ferramentas do assistente expostas no conector: só consultas (nenhuma grava nem propõe alterações).
READ_TOOLS = ("comparativo_vendas", "artigos_mais_vendidos", "compras_do_cliente", "clientes_sem_compras",
              "previsao_iva", "documentos_em_falta_contabilidade", "artigos_a_terminar_validade",
              "produtos_a_encomendar", "procurar_artigos")
INSTRUCTIONS = (
    "Dados reais da empresa na IAZELA (gestão de lojas, Portugal). Responde em português de Portugal. "
    "Usa só os dados devolvidos pelas ferramentas e indica sempre o período e a fonte. Valores em euros com "
    "vírgula decimal; datas DD/MM/AAAA. O IVA calculado é indicativo e não substitui o contabilista. "
    "Este conector só consulta: para alterar dados, indica onde se faz na app IAZELA."
)
# Códigos de autorização já usados (uso único), até expirarem. Por processo: basta para um servidor.
_used_codes: dict[str, float] = {}


# --- Configuração e tokens assinados -----------------------------------------------------------------------

def secret():
    value = os.getenv("MCP_SECRET", "")
    return value if len(value) >= 32 else ""


def enabled_or_404():
    if not secret():
        raise HTTPException(404, "Not Found")


def origin(request: Request):
    from .article_images import public_origin

    return public_origin(request)


def resource_url(request):
    return origin(request) + PATH


def _b64(data: bytes):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key():
    return hashlib.sha256(b"iazela-mcp:" + secret().encode()).digest()


def sign(kind, ttl, **claims):
    body = _b64(json.dumps({"k": kind, "exp": int(time.time()) + ttl, "n": secrets.token_urlsafe(8), **claims},
                           separators=(",", ":")).encode())
    return body + "." + _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())


def verify(token, kind):
    """Conteúdo de um token válido, do tipo indicado e não expirado; senão None."""
    try:
        body, signature = (token or "").split(".", 1)
        expected = _b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        claims = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if claims.get("k") != kind or int(claims.get("exp", 0)) < time.time():
        return None
    return claims


def company_where(*conditions):
    """Empresa ativa (não removida) da plataforma, separada da sessão, ou None."""
    with next_platform_session() as core:
        company = core.scalar(select(Company).where(Company.active == 1, *conditions))
        if not company or getattr(company, "removed_at", None):
            return None
        core.expunge(company)
        return company


def company_by_nif(nif):
    return company_where(Company.nif == nif) if nif else None


def company_by_id(company_id):
    return company_where(Company.id == company_id) if company_id else None


def open_company(company):
    return company_db(company)


def fingerprint(client_id):
    return hashlib.sha256(client_id.encode()).hexdigest()[:24]


# --- Descoberta OAuth (RFC 9728 e RFC 8414) ------------------------------------------------------------------

def resource_metadata(request):
    base = origin(request)
    return {"resource": base + PATH, "authorization_servers": [base + PATH], "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"], "resource_name": "IAZELA"}


def server_metadata(request):
    base = origin(request) + PATH
    return {"issuer": base, "authorization_endpoint": base + "/oauth/authorize", "token_endpoint": base + "/oauth/token",
            "registration_endpoint": base + "/oauth/register", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE]}


@router.get("/.well-known/oauth-protected-resource" + PATH)
@router.get(PATH + "/.well-known/oauth-protected-resource")
def protected_resource(request: Request):
    enabled_or_404()
    return resource_metadata(request)


@router.get("/.well-known/oauth-authorization-server" + PATH)
@router.get("/.well-known/openid-configuration" + PATH)
@router.get(PATH + "/.well-known/oauth-authorization-server")
@router.get(PATH + "/.well-known/openid-configuration")
def authorization_server(request: Request):
    enabled_or_404()
    return server_metadata(request)


def oauth_error(error, description, status=400):
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers={"Cache-Control": "no-store"})


# --- Registo dinâmico de clientes (RFC 7591) -----------------------------------------------------------------

def redirect_allowed(uri):
    """Redirecionamentos do Claude: claude.ai/claude.com (apps alojadas) e loopback (Claude Code)."""
    parts = urlsplit(uri)
    if parts.fragment:
        return False
    if parts.scheme == "https" and parts.hostname in {"claude.ai", "claude.com"} and parts.path == "/api/mcp/auth_callback":
        return parts.port is None
    return parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}


def loopback_match(registered, given):
    """RFC 8252: nos endereços loopback, a porta pode mudar a cada ligação."""
    a, b = urlsplit(registered), urlsplit(given)
    if a.hostname in {"localhost", "127.0.0.1"} and a.scheme == "http":
        return (a.scheme, a.hostname, a.path) == (b.scheme, b.hostname, b.path)
    return registered == given


def client_redirects(client_id):
    claims = verify(client_id, "client")
    return claims.get("r", []) if claims else None


@router.post(PATH + "/oauth/register")
async def register(request: Request):
    enabled_or_404()
    try:
        data = await request.json()
    except ValueError:
        return oauth_error("invalid_client_metadata", "Pedido JSON inválido.")
    uris = data.get("redirect_uris") if isinstance(data, dict) else None
    if not isinstance(uris, list) or not uris or len(uris) > 5 or not all(isinstance(u, str) and redirect_allowed(u) for u in uris):
        return oauth_error("invalid_redirect_uri", "Só são aceites os endereços de retorno do Claude.")
    name = str(data.get("client_name") or "Claude")[:80]
    client_id = sign("client", 10 * 365 * 24 * 3600, r=uris, name=name)
    return JSONResponse({"client_id": client_id, "client_name": name, "redirect_uris": uris,
                         "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                         "response_types": ["code"], "client_id_issued_at": int(time.time())}, status_code=201)


# --- Autorização: página de entrada da IAZELA ---------------------------------------------------------------

def authorize_params(source):
    return {key: str(source.get(key) or "") for key in
            ("response_type", "client_id", "redirect_uri", "state", "scope", "code_challenge",
             "code_challenge_method", "resource")}


def check_request(params, request):
    """Valida o pedido de autorização. Devolve a mensagem de erro (sem redirecionar) ou ''."""
    redirects = client_redirects(params["client_id"])
    if redirects is None:
        return "Cliente desconhecido. Volte a adicionar o conector no Claude."
    if not any(loopback_match(r, params["redirect_uri"]) for r in redirects):
        return "Endereço de retorno não registado para este cliente."
    if params["response_type"] != "code":
        return "Tipo de resposta não suportado."
    if params["code_challenge_method"] != "S256" or not (43 <= len(params["code_challenge"]) <= 128):
        return "Pedido sem PKCE S256."
    if params["resource"] and params["resource"].rstrip("/") != resource_url(request):
        return "Recurso pedido não corresponde a este servidor."
    return ""


PAGE = """<!doctype html><html lang="pt-PT"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}<title>IAZELA · Ligar ao Claude</title>
<style>body{{font-family:system-ui,sans-serif;background:#f4f7f6;color:#17201d;margin:0;display:grid;place-items:center;min-height:100vh}}
main{{background:#fff;border:1px solid #d6e0dc;border-radius:14px;padding:28px;max-width:380px;width:calc(100% - 32px)}}
h1{{font-size:1.25rem;margin:0 0 6px}}p{{color:#46524e;font-size:.92rem;line-height:1.45}}label{{display:grid;gap:4px;margin:12px 0;font-weight:600;font-size:.9rem}}
input{{font:inherit;padding:10px;border:1px solid #9fb0aa;border-radius:8px}}button,a.button{{display:block;width:100%;margin-top:16px;padding:11px;border:0;border-radius:8px;background:#0b6b5c;color:#fff;font:inherit;font-weight:600;text-align:center;text-decoration:none;cursor:pointer}}
.error{{background:#fdecea;color:#8a1c12;padding:10px;border-radius:8px}}ul{{padding-left:18px;color:#46524e;font-size:.9rem}}</style></head>
<body><main>{body}</main></body></html>"""


def page(body, refresh=""):
    return HTMLResponse(PAGE.format(body=body, refresh=refresh), headers={"Cache-Control": "no-store"})


def login_form(params, error="", nif="", email=""):
    hidden = "".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in params.items())
    return page(f"""<h1>Ligar a IAZELA ao Claude</h1>
<p>O Claude vai poder <strong>consultar</strong> os dados desta empresa a que tem acesso (vendas, artigos, clientes,
IVA, validades e documentos), com as suas permissões. Não pode alterar nada. As perguntas e os dados consultados
são enviados à Anthropic.</p>
{f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ''}
<form method="post" action="{PATH}/oauth/authorize">{hidden}
<label>NIF da empresa<input name="nif" inputmode="numeric" autocomplete="off" required value="{html.escape(nif)}"></label>
<label>Email<input name="email" type="email" autocomplete="username" required value="{html.escape(email)}"></label>
<label>Palavra-passe<input name="password" type="password" autocomplete="current-password" required></label>
<button type="submit">Autorizar</button></form>
<p>Só administradores e gestores. Pode desligar o conector a qualquer momento nas definições do Claude.</p>""")


@router.get(PATH + "/oauth/authorize")
def authorize_page(request: Request):
    enabled_or_404()
    params = authorize_params(request.query_params)
    problem = check_request(params, request)
    if problem:
        return page(f'<h1>Pedido inválido</h1><p class="error">{html.escape(problem)}</p>')
    return login_form(params)


@router.post(PATH + "/oauth/authorize")
async def authorize_submit(request: Request):
    from time import monotonic

    from .main import attempts, dummy_hash, hasher

    enabled_or_404()
    form = await request.form()
    params = authorize_params(form)
    problem = check_request(params, request)
    if problem:
        return page(f'<h1>Pedido inválido</h1><p class="error">{html.escape(problem)}</p>')
    nif, email, password = (str(form.get(k) or "").strip() for k in ("nif", "email", "password"))
    key = "mcp:" + (request.client.host if request.client else "local")
    times = attempts[key]
    while times and times[0] < monotonic() - 900:
        times.popleft()
    if len(times) >= 10:
        return login_form(params, "Demasiadas tentativas. Aguarde 15 minutos.", nif, email)
    times.append(monotonic())
    company = company_by_nif(nif)
    if not company:
        hasher.verify(password, dummy_hash)
        return login_form(params, "Empresa, email ou palavra-passe incorretos.", nif, email)
    with open_company(company) as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        valid = hasher.verify(password, user.password_hash if user else dummy_hash)
        if not user or not valid or not user.active:
            return login_form(params, "Empresa, email ou palavra-passe incorretos.", nif, email)
        if user.role not in ROLES:
            return login_form(params, "O conector está disponível para administradores e gestores.", nif, email)
        times.clear()
        record(db, user, "mcp_autorizado", user.id, {"cliente": fingerprint(params["client_id"])})
        db.commit()
        code = sign("code", CODE_TTL, c=company.id, u=user.id, cid=fingerprint(params["client_id"]),
                    ru=params["redirect_uri"], cc=params["code_challenge"])
    query = {"code": code, **({"state": params["state"]} if params["state"] else {})}
    target = params["redirect_uri"] + ("&" if "?" in params["redirect_uri"] else "?") + urlencode(query)
    safe = html.escape(target, quote=True)
    # Retorno por meta refresh (e ligação), não por redirecionamento: a política form-action 'self' da app
    # bloquearia um 302 para o Claude depois de submeter o formulário.
    return page(f'<h1>Autorizado</h1><p>A voltar ao Claude…</p><a class="button" href="{safe}">Continuar no Claude</a>',
                refresh=f'<meta http-equiv="refresh" content="0;url={safe}">')


# --- Tokens --------------------------------------------------------------------------------------------------

def tokens(company_id, user_id, client):
    return {"access_token": sign("access", ACCESS_TTL, c=company_id, u=user_id, cid=client),
            "token_type": "Bearer", "expires_in": ACCESS_TTL, "scope": SCOPE,
            "refresh_token": sign("refresh", REFRESH_TTL, c=company_id, u=user_id, cid=client)}


def still_allowed(company_id, user_id):
    company = company_by_id(company_id)
    if not company:
        return False
    with open_company(company) as db:
        user = db.get(User, user_id)
        return bool(user and user.active and user.role in ROLES)


@router.post(PATH + "/oauth/token")
async def token(request: Request):
    enabled_or_404()
    form = await request.form()
    grant = str(form.get("grant_type") or "")
    client_id = str(form.get("client_id") or "")
    if client_redirects(client_id) is None:
        return oauth_error("invalid_client", "Cliente desconhecido.", 401)
    client = fingerprint(client_id)
    if grant == "authorization_code":
        code = str(form.get("code") or "")
        claims = verify(code, "code")
        verifier = str(form.get("code_verifier") or "")
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        now_ts = time.time()
        for used, expires in list(_used_codes.items()):
            if expires < now_ts:
                _used_codes.pop(used, None)
        if (not claims or claims["cid"] != client or claims["ru"] != str(form.get("redirect_uri") or claims["ru"])
                or not hmac.compare_digest(challenge, claims["cc"]) or claims["n"] in _used_codes):
            return oauth_error("invalid_grant", "Código de autorização inválido, expirado ou já usado.")
        _used_codes[claims["n"]] = claims["exp"]
    elif grant == "refresh_token":
        claims = verify(str(form.get("refresh_token") or ""), "refresh")
        if not claims or claims["cid"] != client:
            return oauth_error("invalid_grant", "Token de renovação inválido ou expirado.")
    else:
        return oauth_error("unsupported_grant_type", "Tipo de concessão não suportado.")
    if not still_allowed(claims["c"], claims["u"]):
        return oauth_error("invalid_grant", "O utilizador ou a empresa já não tem acesso.")
    return JSONResponse(tokens(claims["c"], claims["u"], client), headers={"Cache-Control": "no-store"})


# --- MCP (JSON-RPC 2.0 sobre HTTP) ---------------------------------------------------------------------------

def unauthorized(request, description="Autorização necessária."):
    meta = origin(request) + "/.well-known/oauth-protected-resource" + PATH
    return JSONResponse({"error": "invalid_token", "error_description": description}, status_code=401,
                        headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}", scope="{SCOPE}"'})


def rpc_result(identifier, result):
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def rpc_error(identifier, code, message):
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


class InternalRequest:
    """O que as ferramentas do assistente leem do pedido: cookies de sessão e a base das chamadas internas."""

    def __init__(self, request, session_token, company_id):
        self.cookies = {"gpgest_session": session_token, "gpgest_company": company_id}
        self.base_url = request.base_url


def read_tools(request, user, db, company_id):
    """Ferramentas de consulta do assistente para este utilizador, com uma sessão interna de 5 minutos
    (as consultas internas usam os mesmos endpoints e permissões dos ecrãs). Devolve (ferramentas, fecho)."""
    from .assistant import tools_for

    session_token = secrets.token_urlsafe(48)
    session_hash = hashlib.sha256(session_token.encode()).hexdigest()
    db.add(LoginSession(token_hash=session_hash, user_id=user.id, expires_at=now() + timedelta(minutes=5)))
    db.commit()

    def close():
        item = db.get(LoginSession, session_hash)
        if item:
            db.delete(item)
            db.commit()

    tools = tools_for(InternalRequest(request, session_token, company_id), user, db, "")[0]
    return {t.name: t for t in tools if not isinstance(t, dict) and t.name in READ_TOOLS}, close


def tool_list(tools):
    listed = []
    for name in READ_TOOLS:
        spec = tools[name].to_dict()
        listed.append({"name": spec["name"], "description": spec.get("description", ""),
                       "inputSchema": spec["input_schema"],
                       "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}})
    return listed


def handle(message, request, user, db, company_id):
    identifier = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        return rpc_result(identifier, {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "iazela", "title": "IAZELA", "version": "1.0.0"},
            "instructions": INSTRUCTIONS})
    if method == "ping":
        return rpc_result(identifier, {})
    if method not in ("tools/list", "tools/call"):
        return rpc_error(identifier, -32601, f"Método não suportado: {method}")
    tools, close = read_tools(request, user, db, company_id)
    try:
        if method == "tools/list":
            return rpc_result(identifier, {"tools": tool_list(tools)})
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name not in tools:
            return rpc_error(identifier, -32602, f"Ferramenta desconhecida: {name}")
        try:
            text = tools[name].call(arguments)
            failed = '"erro"' in text[:20]
        except Exception as exc:  # noqa: BLE001 - argumentos inválidos: o erro volta ao modelo
            text, failed = json.dumps({"erro": f"Argumentos inválidos: {str(exc)[:300]}"}, ensure_ascii=False), True
        record(db, user, "mcp_consulta", name, {"argumentos": arguments, "erro": failed})
        db.commit()
        return rpc_result(identifier, {"content": [{"type": "text", "text": text}], "isError": failed})
    finally:
        close()


@router.post(PATH)
async def mcp(request: Request):
    enabled_or_404()
    authorization = request.headers.get("authorization", "")
    claims = verify(authorization[7:].strip(), "access") if authorization.lower().startswith("bearer ") else None
    if not claims:
        return unauthorized(request, "Token em falta, inválido ou expirado.")
    try:
        message = await request.json()
    except ValueError:
        return JSONResponse(rpc_error(None, -32700, "JSON inválido."), status_code=400)
    if isinstance(message, list):
        return JSONResponse(rpc_error(None, -32600, "Lotes JSON-RPC não suportados."), status_code=400)
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not message.get("method"):
        return JSONResponse(rpc_error(None, -32600, "Pedido JSON-RPC inválido."), status_code=400)
    if "id" not in message:
        return Response(status_code=202)  # notificação (por exemplo notifications/initialized)
    company = company_by_id(claims["c"])
    if not company:
        return unauthorized(request, "Empresa indisponível.")
    with open_company(company) as db:
        user = db.get(User, claims["u"])
        if not user or not user.active or user.role not in ROLES:
            return unauthorized(request, "O utilizador já não tem acesso.")
        return JSONResponse(handle(message, request, user, db, company.id))


@router.get(PATH)
@router.delete(PATH)
def no_stream():
    enabled_or_404()
    return Response(status_code=405, headers={"Allow": "POST"})
