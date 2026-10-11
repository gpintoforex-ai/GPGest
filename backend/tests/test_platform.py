import json
from uuid import uuid4

import httpx
import pytest
from app import db as database
from app.main import app, attempts
from app.models import CashSession, WintouchSaleRecord
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


def test_cash_sheet_wintouch_day_is_unique_and_company_scoped(platform):
    c, _ = platform
    a = create(c, "folha-caixa-a")
    b = create(c, "folha-caixa-b")
    for company in (a, b):
        assert (
            c.post(
                "/api/platform/companies/" + company["id"] + "/config",
                json=configuration(company, api_enterprise="source-" + company["slug"]),
            ).status_code
            == 200
        )
    c.cookies.clear()
    boot = enter(c, a["slug"])
    register = boot["registers"][0]["id"]
    store = boot["stores"][0]["id"]
    second = c.post("/api/registers", json={"store_id": store, "name": "Caixa segunda"}).json()[
        "id"
    ]
    payload = {"opening": "100", "sheet_day": "2026-10-02", "include_wintouch": True}
    session = c.post("/api/sessions", json={**payload, "register_id": register})
    assert session.status_code == 200, session.text
    assert session.json()["wintouch_enterprise"] == "source-folha-caixa-a"
    session_id = session.json()["id"]
    assert (
        c.post(
            f"/api/sessions/{session_id}/sources",
            json={"sheet_day": "2026-10-02", "include_wintouch": True},
        ).status_code
        == 200
    )

    assert c.post("/api/sessions", json={**payload, "register_id": second}).status_code == 409
    assert (
        c.post(f"/api/sessions/{session_id}/close", json={"cash_total": "100"}).status_code == 200
    )
    assert (
        c.post(
            f"/api/sessions/{session_id}/sources",
            json={"sheet_day": "2026-10-02", "include_wintouch": False},
        ).status_code
        == 409
    )
    assert c.post("/api/sessions", json={**payload, "register_id": second}).status_code == 409
    boot_b = enter(c, b["slug"])
    assert c.get("/api/sessions").json() == []
    assert (
        c.post(f"/api/sessions/{session_id}/count", json={"cash_total": "100"}).status_code == 404
    )
    assert (
        c.post(
            "/api/sessions", json={**payload, "register_id": boot_b["registers"][0]["id"]}
        ).status_code
        == 200
    )


def test_cash_sheet_automatically_uses_real_receipts_and_legacy_open_sheets(platform):
    c, factory = platform
    company = create(c, "folha-auto")
    assert (
        c.post(
            "/api/platform/companies/" + company["id"] + "/config",
            json=configuration(company, api_enterprise="auto-source"),
        ).status_code
        == 200
    )
    c.cookies.clear()
    boot = enter(c, company["slug"])
    store = boot["stores"][0]["id"]
    register = boot["registers"][0]["id"]
    with factory() as core:
        schema = core.get(Company, company["id"]).schema_name
    tenant_factory = sessionmaker(
        database.engine.execution_options(schema_translate_map={None: schema}),
        expire_on_commit=False,
    )
    with tenant_factory.begin() as db:
        for selected_day, value in (("2026-10-02", 500), ("2026-10-03", 900)):
            db.add(
                WintouchSaleRecord(
                    enterprise_id="auto-source",
                    external_id=str(uuid4()),
                    store_id=store,
                    kind="real",
                    active=True,
                    document_date=selected_day,
                    data=json.dumps(
                        {
                            "amount_cents": value,
                            "direction": 1,
                            "payment": "Numerário",
                            "payment_code": "NU",
                            "occurred_at": selected_day + "T12:00:00",
                        }
                    ),
                )
            )
    opened = c.post(
        "/api/sessions", json={"register_id": register, "opening": "100", "sheet_day": "2026-10-02"}
    )
    assert opened.status_code == 200 and opened.json()["wintouch_scope"]
    sheet = c.get("/api/sessions").json()[0]
    assert sheet["sheet"]["include_wintouch"] and sheet["balance_cents"] == 10500
    assert (
        c.post("/api/sessions/" + sheet["id"] + "/close", json={"cash_total": "105"}).status_code
        == 200
    )
    # An existing open sheet from the prior release gains its live projection without a GET write.
    with tenant_factory.begin() as db:
        legacy = CashSession(
            register_id=register,
            operator_id=boot["user"]["id"],
            opening_cents=10000,
            sheet_day="2026-10-03",
        )
        db.add(legacy)
        db.flush()
        legacy_id = legacy.id
    sheet = next(s for s in c.get("/api/sessions").json() if s["id"] == legacy_id)
    assert sheet["sheet"]["include_wintouch"] and sheet["balance_cents"] == 10900
    with tenant_factory() as db:
        assert db.get(CashSession, legacy_id).wintouch_scope is None
    second = c.post("/api/registers", json={"store_id": store, "name": "Caixa segunda"}).json()[
        "id"
    ]
    other = c.post(
        "/api/sessions", json={"register_id": second, "opening": "0", "sheet_day": "2026-10-03"}
    )
    assert other.status_code == 200
    summaries = {s["id"]: s for s in c.get("/api/sessions").json()}
    assert summaries[other.json()["id"]]["sheet"]["include_wintouch"]
    assert summaries[other.json()["id"]]["balance_cents"] == 0
    assert (
        next(
            row
            for row in summaries[other.json()["id"]]["sheet"]["rows"]
            if row["key"] == "numerario"
        )["wintouch_cents"]
        == 900
    )
    assert summaries[legacy_id]["balance_cents"] == 10900
    assert c.post(f"/api/sessions/{legacy_id}/count", json={"cash_total": "109"}).status_code == 200
    with tenant_factory() as db:
        assert db.get(CashSession, legacy_id).wintouch_scope


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


def test_tenant_integration_uses_own_company_without_platform_session(platform):
    c, _ = platform
    a = create(c, "empresa-integracoes-a")
    b = create(c, "empresa-integracoes-b")
    c.cookies.clear()
    enter(c, a["slug"])
    assert c.get("/api/integrations/wintouch").json()["id"] == a["id"]
    assert c.get("/api/integrations/wintouch/sync-history").json() == []
    assert c.get("/api/platform/companies").status_code == 401
    assert (
        c.post(
            "/api/integrations/wintouch/sync", json={"resource": "products", "company_id": b["id"]}
        ).status_code
        == 422
    )
    enter(c, b["slug"])
    assert c.get("/api/integrations/wintouch").json()["id"] == b["id"]


def test_sales_import_is_bound_to_current_company(platform, monkeypatch):
    c, _ = platform
    a = create(c, "vendas-empresa-a")
    b = create(c, "vendas-empresa-b")
    assert (
        c.post(
            "/api/platform/companies/" + a["id"] + "/config",
            json=configuration(
                a, api_active=True, api_enterprise="sales-source-a", api_key="private-sales-key"
            ),
        ).status_code
        == 200
    )
    c.cookies.clear()
    enter(c, a["slug"])
    store = c.get("/api/bootstrap").json()["stores"][0]["id"]
    body = {"store_id": store, "start_date": "2026-10-01", "end_date": "2026-10-11"}
    assert c.post("/api/integrations/wintouch/sales", json=body).status_code == 422
    assert (
        c.post("/api/integrations/wintouch/sales", json={**body, "company_id": b["id"]}).status_code
        == 422
    )
    calls = []

    def sync(bind, enterprise, destination, start, end, url, key):
        calls.append((enterprise, destination, key))
        return {"status": "concluida", "read_count": 0, "inserted_count": 0, "updated_count": 0}

    monkeypatch.setattr("app.wintouch_sales.synchronize_sales", sync)
    response = c.post("/api/integrations/wintouch/sales", json={**body, "confirm_demo": True})
    assert response.status_code == 200, response.text
    assert calls == [("sales-source-a", store, "private-sales-key")]
    assert "private-sales-key" not in response.text
    assert c.get("/api/integrations/wintouch/sales").json() == []
    enter(c, b["slug"])
    assert c.get("/api/integrations/wintouch/sales").json() == []
    assert (
        c.post("/api/integrations/wintouch/sales", json={**body, "confirm_demo": True}).status_code
        == 422
    )


def test_manual_sync_pagination_deduplication_and_demo_confirmation(platform, monkeypatch):
    from app.db import engine
    from app.models import WintouchProduct, WintouchSyncState
    from sqlalchemy.orm import Session

    c, factory = platform
    company = create(c, "empresa-sync")
    url = "/api/platform/companies/" + company["id"]
    config = configuration(
        company, api_active=True, api_enterprise="api-sync", api_key="secret-sync-key"
    )
    assert c.post(url + "/config", json=config).status_code == 200
    assert c.post(url + "/sync", json={"resource": "products"}).status_code == 422
    assert (
        c.post(
            url + "/sync", json={"resource": "settlement_documents", "confirm_demo": True}
        ).status_code
        == 422
    )
    calls = []
    mode = {"fail": False}

    class API:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, target, params, headers):
            calls.append((target, params, headers))
            if params["skip"] and not mode["fail"]:
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {
                        "ID": "p1",
                        "Code": "P1",
                        "Name": "Artigo atualizado" if mode["fail"] else "Artigo",
                        "LastCostGlobal": "12.3456",
                        "Enabled": True,
                    }
                ],
            )

    monkeypatch.setattr("app.wintouch_sync.httpx.Client", API)
    for expected_new in (1, 0):
        result = c.post(url + "/sync", json={"resource": "products", "confirm_demo": True})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "concluida"
        assert result.json()["inserted_count"] == expected_new
    assert [x[1]["skip"] for x in calls] == [0, 1, 0, 1]
    assert all(x[2]["x-current-enterprise"] == "api-sync" for x in calls)
    mode["fail"] = True
    result = c.post(url + "/sync", json={"resource": "products", "confirm_demo": True}).json()
    assert result["status"] == "falhou"
    assert "repetiu IDs" in result["error_message"]
    with factory() as core:
        schema = core.get(Company, company["id"]).schema_name
    with Session(engine.execution_options(schema_translate_map={None: schema})) as tenant:
        products = list(tenant.scalars(select(WintouchProduct)))
        assert len(products) == 1 and products[0].name == "Artigo"
        assert tenant.scalar(select(WintouchSyncState)).last_success_at is not None
    history = c.get(url + "/sync-history")
    assert len(history.json()) == 3
    assert "secret-sync-key" not in history.text
    enter(c, company["slug"])
    assert c.get("/api/movements").json() == []


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
