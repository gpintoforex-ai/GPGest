# ruff: noqa: F811
"""Conector MCP da IAZELA: desligado sem MCP_SECRET, descoberta OAuth, registo dinâmico com os endereços do
Claude, entrada com NIF/email/palavra-passe (só administradores e gestores), PKCE S256, código de uso único,
renovação com rotação, consultas só de leitura com as permissões do utilizador e auditoria. A plataforma é
simulada (uma empresa no esquema de teste); nenhum pedido sai da máquina."""

import base64
import hashlib
import json
import re
import secrets
from contextlib import contextmanager
from datetime import timedelta
from html import unescape
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from app import mcp_server
from app.models import Audit, LoginSession, User, now
from sqlalchemy import select
from test_inventory import product, stock_case  # noqa: F401

NIF = "510774318"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
BASE = "http://testserver"


@pytest.fixture
def mcp(stock_case, monkeypatch):
    monkeypatch.setenv("MCP_SECRET", "s" * 40)
    monkeypatch.delenv("PUBLIC_API_ORIGIN", raising=False)
    company = SimpleNamespace(id="empresa-teste", nif=NIF, active=1, removed_at=None)
    monkeypatch.setattr(mcp_server, "company_by_nif", lambda nif: company if nif == NIF else None)
    monkeypatch.setattr(mcp_server, "company_by_id", lambda cid: company if cid == company.id else None)

    @contextmanager
    def open_company(_):
        with stock_case[2]() as db:
            yield db
    monkeypatch.setattr(mcp_server, "open_company", open_company)
    mcp_server._used_codes.clear()
    product(stock_case, name="Óleo de coco biológico 500 ml", sale_net="5")
    stock_case[0].cookies.clear()  # o conector não usa a sessão do navegador
    return stock_case


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(c, uris=(CALLBACK,)):
    response = c.post("/api/mcp/oauth/register", json={"client_name": "Claude", "redirect_uris": list(uris)})
    return response


def authorize(c, client_id, challenge, email="admin@test.pt", password="PasswordTest123!", nif=NIF, state="xyz"):
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK, "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", "scope": "iazela.consulta",
              "resource": BASE + "/api/mcp"}
    page = c.get("/api/mcp/oauth/authorize", params=params)
    assert page.status_code == 200 and "Ligar a IAZELA ao Claude" in page.text
    return c.post("/api/mcp/oauth/authorize", data={**params, "nif": nif, "email": email, "password": password})


def code_from(page):
    target = unescape(re.search(r'href="([^"]+)"', page.text).group(1))
    assert target.startswith(CALLBACK + "?")
    query = parse_qs(urlsplit(target).query)
    assert query["state"] == ["xyz"]
    return query["code"][0]


def connect(c):
    client_id = register(c).json()["client_id"]
    verifier, challenge = pkce()
    code = code_from(authorize(c, client_id, challenge))
    response = c.post("/api/mcp/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                                    "redirect_uri": CALLBACK, "client_id": client_id,
                                                    "code_verifier": verifier})
    assert response.status_code == 200, response.text
    return client_id, response.json()


def rpc(c, token, method, params=None, identifier=1):
    return c.post("/api/mcp", headers={"Authorization": f"Bearer {token}"},
                  json={"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}})


def test_connector_is_off_without_secret(stock_case, monkeypatch):
    monkeypatch.delenv("MCP_SECRET", raising=False)
    c = stock_case[0]
    assert c.post("/api/mcp", json={}).status_code == 404
    assert c.get("/.well-known/oauth-protected-resource/api/mcp").status_code == 404
    monkeypatch.setenv("MCP_SECRET", "curto")  # chave fraca também não liga o conector
    assert c.get("/api/mcp/.well-known/oauth-authorization-server").status_code == 404


def test_discovery_starts_with_401_and_metadata(mcp):
    c = mcp[0]
    unauthorized = c.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert unauthorized.status_code == 401
    challenge = unauthorized.headers["www-authenticate"]
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/api/mcp"' in challenge
    resource = c.get("/.well-known/oauth-protected-resource/api/mcp").json()
    assert resource["resource"] == BASE + "/api/mcp" and resource["authorization_servers"] == [BASE + "/api/mcp"]
    for path in ("/.well-known/oauth-authorization-server/api/mcp", "/.well-known/openid-configuration/api/mcp",
                 "/api/mcp/.well-known/oauth-authorization-server"):
        server = c.get(path).json()
        assert server["issuer"] == BASE + "/api/mcp" and server["code_challenge_methods_supported"] == ["S256"]
        assert server["registration_endpoint"] == BASE + "/api/mcp/oauth/register"
        assert server["token_endpoint_auth_methods_supported"] == ["none"]


def test_registration_only_accepts_claude_redirects(mcp):
    c = mcp[0]
    assert register(c).status_code == 201
    assert register(c, ("http://localhost:33418/callback",)).status_code == 201  # Claude Code (loopback)
    for bad in ("https://evil.example/api/mcp/auth_callback", "http://claude.ai/api/mcp/auth_callback",
                "https://claude.ai/outro"):
        response = register(c, (bad,))
        assert response.status_code == 400 and response.json()["error"] == "invalid_redirect_uri"


def test_full_oauth_flow_then_read_only_queries_with_audit(mcp):
    c, factory = mcp[0], mcp[2]
    _, issued = connect(c)
    assert issued["token_type"] == "Bearer" and issued["expires_in"] == 3600 and issued["refresh_token"]
    token = issued["access_token"]
    init = rpc(c, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}).json()["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["capabilities"]["tools"] is not None
    assert "português de Portugal" in init["instructions"]
    note = c.post("/api/mcp", headers={"Authorization": f"Bearer {token}"},
                  json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202
    listed = rpc(c, token, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in listed}
    assert names == set(mcp_server.READ_TOOLS)  # nenhuma ferramenta que proponha ou grave alterações
    assert not names & {"propor_alteracao_preco", "propor_imagem", "gerar_descritivo", "propor_publicacao", "mostrar_grafico"}
    assert all(t["annotations"]["readOnlyHint"] and t["inputSchema"]["type"] == "object" for t in listed)
    found = rpc(c, token, "tools/call", {"name": "procurar_artigos", "arguments": {"texto": "coco"}}).json()["result"]
    assert found["isError"] is False
    assert json.loads(found["content"][0]["text"])["artigos"][0]["preco_com_iva_eur"] == "6.15"
    failed = rpc(c, token, "tools/call", {"name": "procurar_artigos", "arguments": {"texto": "x"}}).json()["result"]
    assert failed["isError"] is True and "2 caracteres" in failed["content"][0]["text"]
    assert rpc(c, token, "tools/call", {"name": "propor_alteracao_preco", "arguments": {}}).json()["error"]["code"] == -32602
    assert rpc(c, token, "resources/list").json()["error"]["code"] == -32601
    with factory() as db:
        actions = [a.action for a in db.scalars(select(Audit).where(Audit.action.like("mcp_%")))]
        assert actions.count("mcp_autorizado") == 1 and actions.count("mcp_consulta") == 2
        # As sessões internas de cada consulta não ficam abertas.
        admin = db.scalar(select(User).where(User.email == "admin@test.pt"))
        sessions = db.scalars(select(LoginSession).where(LoginSession.user_id == admin.id)).all()
        assert all(s.expires_at > now() + timedelta(hours=1) for s in sessions)  # só a do login do teste


def test_wrong_password_role_and_company_are_refused(mcp):
    c = mcp[0]
    client_id = register(c).json()["client_id"]
    _, challenge = pkce()
    assert "incorretos" in authorize(c, client_id, challenge, password="errada").text
    assert "incorretos" in authorize(c, client_id, challenge, nif="999999999").text
    assert "administradores e gestores" in authorize(c, client_id, challenge, email="operator@test.pt").text
    # Pedido de autorização adulterado: sem PKCE ou com um retorno não registado.
    bad = c.get("/api/mcp/oauth/authorize", params={"response_type": "code", "client_id": client_id,
                                                    "redirect_uri": "https://evil.example/cb", "code_challenge": challenge,
                                                    "code_challenge_method": "S256"})
    assert "Pedido inválido" in bad.text and "evil.example" not in bad.text
    plain = c.get("/api/mcp/oauth/authorize", params={"response_type": "code", "client_id": client_id,
                                                      "redirect_uri": CALLBACK, "code_challenge": challenge,
                                                      "code_challenge_method": "plain"})
    assert "PKCE" in plain.text


def test_code_needs_the_verifier_and_is_single_use_and_refresh_rotates(mcp):
    c = mcp[0]
    client_id = register(c).json()["client_id"]
    verifier, challenge = pkce()
    code = code_from(authorize(c, client_id, challenge))
    exchange = {"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": client_id}
    wrong = c.post("/api/mcp/oauth/token", data={**exchange, "code_verifier": "x" * 50})
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    first = c.post("/api/mcp/oauth/token", data={**exchange, "code_verifier": verifier})
    assert first.status_code == 200
    again = c.post("/api/mcp/oauth/token", data={**exchange, "code_verifier": verifier})
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    other_client = register(c).json()["client_id"]
    stolen = c.post("/api/mcp/oauth/token", data={"grant_type": "refresh_token", "client_id": other_client,
                                                  "refresh_token": first.json()["refresh_token"]})
    assert stolen.status_code == 400
    renewed = c.post("/api/mcp/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                   "refresh_token": first.json()["refresh_token"]})
    assert renewed.status_code == 200 and renewed.json()["refresh_token"] != first.json()["refresh_token"]
    # Um token de renovação não serve como token de acesso.
    assert rpc(c, first.json()["refresh_token"], "ping").status_code == 401
    assert rpc(c, renewed.json()["access_token"], "ping").json()["result"] == {}


def test_deactivated_user_and_new_secret_revoke_access(mcp, monkeypatch):
    c, factory = mcp[0], mcp[2]
    client_id, issued = connect(c)
    with factory.begin() as db:
        db.scalar(select(User).where(User.email == "admin@test.pt")).active = 0
    assert rpc(c, issued["access_token"], "ping").status_code == 401
    refresh = c.post("/api/mcp/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id,
                                                   "refresh_token": issued["refresh_token"]})
    assert refresh.status_code == 400
    with factory.begin() as db:
        db.scalar(select(User).where(User.email == "admin@test.pt")).active = 1
    assert rpc(c, issued["access_token"], "ping").status_code == 200
    monkeypatch.setenv("MCP_SECRET", "t" * 40)
    assert rpc(c, issued["access_token"], "ping").status_code == 401
