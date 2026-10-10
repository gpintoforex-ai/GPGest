from uuid import uuid4

import httpx
import pytest
from app import db as database
from app.main import app, attempts
from app.platform import attempts as platform_attempts
from app.platform import hasher
from app.platform_models import Company, PlatformAdmin, PlatformBase
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def platform(monkeypatch):
    schema = "platform_test_" + uuid4().hex
    with database.engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA " + schema))
    bind = database.engine.execution_options(schema_translate_map={None: schema})
    PlatformBase.metadata.create_all(bind)
    factory = sessionmaker(bind, expire_on_commit=False)
    with factory.begin() as db:
        db.add(
            PlatformAdmin(email="master@test.pt", password_hash=hasher.hash("MasterPassword123!"))
        )

    def core():
        with factory() as db:
            db.info["platform_schema"] = schema
            yield db

    app.dependency_overrides[database.platform_db] = core
    monkeypatch.setattr(database, "next_platform_session", factory)
    monkeypatch.setenv("PLATFORM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    attempts.clear()
    platform_attempts.clear()
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/platform/login",
                json={"email": "master@test.pt", "password": "MasterPassword123!"},
            ).status_code
            == 200
        )
        yield client, factory
    with factory() as db:
        schemas = list(db.scalars(select(Company.schema_name)))
    app.dependency_overrides.clear()
    bind.dispose()
    with database.engine.begin() as connection:
        for tenant in schemas:
            assert tenant.startswith("gpgest_c_") and len(tenant) == 41
            connection.execute(text("DROP SCHEMA " + tenant + " CASCADE"))
        connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))


def create(client, slug):
    r = client.post(
        "/api/platform/companies",
        json={
            "name": slug,
            "slug": slug,
            "admin_name": "Administrador",
            "admin_email": "admin@test.pt",
            "admin_password": "TenantPassword123!",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def enter(client, slug):
    assert client.post("/api/company/select", json={"slug": slug}).status_code == 200
    assert (
        client.post(
            "/api/auth/login", json={"email": "admin@test.pt", "password": "TenantPassword123!"}
        ).status_code
        == 200
    )
    return client.get("/api/bootstrap").json()


def configuration(company, **updates):
    return {
        "name": company["name"],
        "active": True,
        "api_active": False,
        "api_url": "https://api.wintouchcloud.com/api/v1",
        "api_enterprise": "",
        **updates,
    }


def test_company_validation_explains_fields_and_normalizes_slug(platform):
    c, _ = platform
    payload = {
        "name": "Empresa de teste",
        "slug": "identificador inválido",
        "admin_name": "Administrador",
        "admin_email": "ADMIN@test.pt",
        "admin_password": "curta",
    }
    response = c.post("/api/platform/companies", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Identificador da empresa" in detail
    assert "Palavra-passe inicial: introduza pelo menos 12 caracteres" in detail
    assert payload["admin_password"] not in detail
    response = c.post(
        "/api/platform/companies",
        json={**payload, "slug": "  EMPRESA-NOVA  ", "admin_password": "TenantPassword123!"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["slug"] == "empresa-nova"


def test_company_isolation_and_separate_admin(platform):
    c, _ = platform
    a, b = create(c, "empresa-a"), create(c, "empresa-b")
    first = enter(c, a["slug"])
    assert first["company"]["id"] == a["id"]
    assert c.post("/api/stores", json={"name": "Loja exclusiva A"}).status_code == 200
    token_a = c.cookies.get("gpgest_session")
    second = enter(c, b["slug"])
    assert len(second["stores"]) == 1
    assert second["stores"][0]["id"] != first["stores"][0]["id"]
    assert c.get("/api/movements").json() == []
    c.cookies.set("gpgest_session", token_a, domain="testserver.local", path="/")
    assert c.get("/api/bootstrap").status_code == 401
    c.cookies.clear()
    enter(c, b["slug"])
    assert c.get("/api/platform/companies").status_code == 401
    assert c.post("/api/platform/companies", json={"name": "Invalid"}).status_code in {401, 422}


def test_encrypted_credentials_readonly_test_and_deactivation(platform, monkeypatch):
    c, factory = platform
    company = create(c, "empresa-api")
    config = configuration(
        company, api_active=True, api_enterprise="enterprise-123", api_key="secret-test-api-key"
    )
    url = "/api/platform/companies/" + company["id"]
    assert (
        c.post(
            url + "/config", json={**config, "api_url": "https://untrusted.example/api"}
        ).status_code
        == 422
    )
    assert (
        c.post(
            url + "/config",
            json={**config, "api_url": "https://api.wintouchcloud.com:invalid/api/v1"},
        ).status_code
        == 422
    )
    result = c.post(url + "/config", json=config)
    assert result.status_code == 200, result.text
    assert result.json()["has_api_key"]
    assert "secret-test-api-key" not in c.get("/api/platform/companies").text
    with factory() as db:
        saved = db.get(Company, company["id"])
        assert saved.api_key_ciphertext != config["api_key"]
        assert (
            Fernet(__import__("os").environ["PLATFORM_ENCRYPTION_KEY"])
            .decrypt(saved.api_key_ciphertext.encode())
            .decode()
            == config["api_key"]
        )
    calls = []

    class ReadOnlyClient:
        def __init__(self, **kwargs):
            assert not kwargs["follow_redirects"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(200, json={"items": []})

    monkeypatch.setattr("app.platform.httpx.Client", ReadOnlyClient)
    assert c.post(url + "/test", json={}).json()["success"]
    assert len(calls) == 1
    assert calls[0][1]["headers"]["x-current-enterprise"] == "enterprise-123"
    assert calls[0][1]["headers"]["Authorization"] == "ApiKey secret-test-api-key"
    assert "secret-test-api-key" not in c.get("/api/platform/audit").text
    enter(c, company["slug"])
    assert (
        c.post(url + "/config", json={**config, "active": False, "api_key": None}).status_code
        == 200
    )
    assert c.get("/api/bootstrap").status_code == 401
    assert c.post("/api/company/select", json={"slug": company["slug"]}).status_code == 404
