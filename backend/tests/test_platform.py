import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from app import db as database
from app import entities as entities_module
from app.main import app, attempts
from app.models import BusinessEntity, CashSession, Category, Movement, Register, User, WintouchSaleRecord
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
            "nif": str(500000000 + sum((i + 1) * ord(ch) for i, ch in enumerate(slug))),
            "admin_name": "Administrador",
            "admin_email": "admin@test.pt",
            "admin_password": "TenantPassword123!",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def enter(client, slug):
    nif = str(500000000 + sum((i + 1) * ord(ch) for i, ch in enumerate(slug)))
    assert client.post("/api/company/select", json={"nif": nif}).status_code == 200
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
    c, factory = platform
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
        with factory.begin() as core:
            core.get(Company, company["id"]).operating_mode = "integrated"
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
    with factory.begin() as core:
        core.get(Company, company["id"]).operating_mode = "integrated"
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
        "nif": "500000000",
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
        == 409
    )
    enter(c, b["slug"])
    assert c.get("/api/integrations/wintouch").json()["id"] == b["id"]


def test_sales_import_is_bound_to_current_company(platform, monkeypatch):
    c, factory = platform
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
    with factory.begin() as core:
        core.get(Company, a["id"]).operating_mode = "integrated"
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
        == 409
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
    with factory.begin() as core:
        core.get(Company, company["id"]).operating_mode = "integrated"
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


def test_nif_unique_creation_selection_and_update(platform):
    c, factory = platform
    payload = {
        "name": "Empresa NIF",
        "nif": "501234567",
        "admin_name": "Administrador",
        "admin_email": "admin@test.pt",
        "admin_password": "TenantPassword123!",
    }
    first = c.post("/api/platform/companies", json=payload)
    assert first.status_code == 200, first.text
    a = first.json()
    assert a["nif"] == payload["nif"]
    assert c.post("/api/platform/companies", json=payload).status_code == 409
    assert c.post("/api/platform/companies", json={**payload, "nif": "ABC"}).status_code == 422
    assert c.post("/api/company/select", json={"nif": "999999999"}).status_code == 404
    assert c.post("/api/company/select", json={"slug": a["slug"]}).status_code == 404
    assert c.post("/api/company/select", json={"nif": a["nif"]}).status_code == 200
    assert (
        c.post(
            "/api/auth/login", json={"email": "admin@test.pt", "password": "TenantPassword123!"}
        ).status_code
        == 200
    )
    assert c.get("/api/bootstrap").json()["company"]["nif"] == a["nif"]
    b = c.post("/api/platform/companies", json={**payload, "nif": "502345678"}).json()
    url = "/api/platform/companies/" + a["id"] + "/config"
    assert c.post(url, json=configuration(a, nif=b["nif"])).status_code == 409
    assert c.post(url, json=configuration(a, nif="503456789")).status_code == 200
    assert c.post("/api/company/select", json={"nif": a["nif"]}).status_code == 404
    assert c.post("/api/company/select", json={"nif": "503456789"}).status_code == 200
    # Preserve data when assigning the NIF to an existing company.
    with factory.begin() as db:
        db.get(Company, a["id"]).nif = None
    assert c.post("/api/company/select", json={"slug": a["slug"]}).status_code == 200
    assert c.post(url, json=configuration(a, nif="503456789")).status_code == 200
    assert c.post("/api/company/select", json={"slug": a["slug"]}).status_code == 404
    assert c.post(url, json=configuration(a, active=False)).status_code == 200
    assert c.post("/api/company/select", json={"nif": "503456789"}).status_code == 404


def test_platform_reset_scoped_audited_and_revokes_sessions(platform):
    c, _ = platform
    a, b = create(c, "reset-a"), create(c, "reset-b")
    users = c.get("/api/platform/companies/" + a["id"] + "/users")
    assert users.status_code == 200
    assert "password_hash" not in users.text
    user = users.json()[0]
    assert user["email"] == "admin@test.pt"
    enter(c, a["slug"])
    url = "/api/platform/companies/" + a["id"] + "/users/" + user["id"] + "/reset-password"
    new_password = "NewTenantPassword123!"
    assert c.post(url, json={"new_password": "short"}).status_code == 422
    wrong = url.replace(a["id"], b["id"])
    assert c.post(wrong, json={"new_password": new_password}).status_code == 404
    assert c.post(url, json={"new_password": new_password}).status_code == 200
    assert c.get("/api/bootstrap").status_code == 401
    assert (
        c.post(
            "/api/auth/login", json={"email": user["email"], "password": "TenantPassword123!"}
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/api/auth/login", json={"email": user["email"], "password": new_password}
        ).status_code
        == 200
    )
    audit = c.get("/api/platform/audit")
    assert "palavra_passe_reposta" in audit.text
    assert new_password not in audit.text
    c.post("/api/platform/logout", json={})
    assert c.get("/api/platform/companies/" + a["id"] + "/users").status_code == 401
    assert c.post(url, json={"new_password": new_password}).status_code == 401


def test_company_removal_blocks_access_preserves_data_and_audit(platform):
    c, factory = platform
    a = create(c, "remove-a")
    b = create(c, "remove-b")
    enter(c, a["slug"])
    url = "/api/platform/companies/" + a["id"]
    assert c.post(url + "/remove", json={"confirmation": "wrong"}).status_code == 422
    assert c.post(url + "/remove", json={"confirmation": a["nif"]}).status_code == 200
    assert c.get("/api/bootstrap").status_code == 401
    assert c.post("/api/company/select", json={"nif": a["nif"]}).status_code == 404
    assert a["id"] not in [x["id"] for x in c.get("/api/platform/companies").json()]
    assert c.post(url + "/config", json=configuration(a)).status_code == 404
    assert c.post(url + "/remove", json={"confirmation": a["nif"]}).status_code == 404
    assert "empresa_removida" in c.get("/api/platform/audit").text
    with factory() as db:
        saved = db.get(Company, a["id"])
        assert saved.removed_at and not saved.active
        with database.engine.connect() as connection:
            assert (
                connection.scalar(text('SELECT count(*) FROM "' + saved.schema_name + '".users'))
                == 1
            )
    assert enter(c, b["slug"])["company"]["id"] == b["id"]
    c.post("/api/platform/logout", json={})
    assert (
        c.post(
            "/api/platform/companies/" + b["id"] + "/remove", json={"confirmation": b["nif"]}
        ).status_code
        == 401
    )


def test_reuse_removed_company_nif_starts_with_separate_data(platform):
    c, factory = platform
    old = create(c, "reuse-old")
    enter(c, old["slug"])
    assert c.post("/api/stores", json={"name": "Old store"}).status_code == 200
    assert (
        c.post(
            "/api/platform/companies/" + old["id"] + "/remove", json={"confirmation": old["nif"]}
        ).status_code
        == 200
    )
    payload = {
        "name": "Replacement",
        "removed_action": "new",
        "nif": old["nif"],
        "admin_name": "Admin",
        "admin_email": "admin@test.pt",
        "admin_password": "TenantPassword123!",
    }
    r = c.post("/api/platform/companies", json=payload)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["id"] != old["id"]
    assert c.post("/api/company/select", json={"nif": old["nif"]}).status_code == 200
    assert (
        c.post(
            "/api/auth/login", json={"email": "admin@test.pt", "password": "TenantPassword123!"}
        ).status_code
        == 200
    )
    boot = c.get("/api/bootstrap").json()
    assert boot["company"]["id"] == new["id"] and len(boot["stores"]) == 1
    duplicate = c.post("/api/platform/companies", json=payload)
    assert duplicate.status_code == 409
    assert "J\u00e1 existe" in duplicate.json()["detail"]
    with factory() as db:
        assert db.get(Company, old["id"]).nif == old["nif"]
        assert db.get(Company, old["id"]).schema_name != db.get(Company, new["id"]).schema_name


def test_restore_removed_company_keeps_stores_users_and_requires_choice(platform):
    c, factory = platform
    a = create(c, "restore-a")
    enter(c, a["slug"])
    assert c.post("/api/stores", json={"name": "Preserved store"}).status_code == 200
    url = "/api/platform/companies/" + a["id"]
    assert c.post(url + "/remove", json={"confirmation": a["nif"]}).status_code == 200
    assert c.get("/api/platform/removed-companies/" + a["nif"]).json()[0]["id"] == a["id"]
    payload = {
        "name": "New",
        "nif": a["nif"],
        "admin_name": "Admin",
        "admin_email": "admin@test.pt",
        "admin_password": "TenantPassword123!",
    }
    assert c.post("/api/platform/companies", json=payload).status_code == 409
    assert c.post(url + "/restore", json={"confirmation": "wrong"}).status_code == 422
    assert c.post(url + "/restore", json={"confirmation": a["nif"]}).status_code == 200
    boot = enter(c, a["slug"])
    assert boot["company"]["id"] == a["id"] and len(boot["stores"]) == 2
    assert c.post(url + "/remove", json={"confirmation": a["nif"]}).status_code == 200
    assert (
        c.post("/api/platform/companies", json={**payload, "removed_action": "new"}).status_code
        == 200
    )
    assert c.post(url + "/restore", json={"confirmation": a["nif"]}).status_code == 409
    assert "empresa_recuperada" in c.get("/api/platform/audit").text
    c.post("/api/platform/logout", json={})
    assert c.get("/api/platform/removed-companies/" + a["nif"]).status_code == 401
    assert c.post(url + "/restore", json={"confirmation": a["nif"]}).status_code == 401


def test_company_mode_edit_is_validated_scoped_and_audited(platform):
    c, _ = platform
    a, b = create(c, "mode-a"), create(c, "mode-b")
    url = "/api/platform/companies/" + a["id"] + "/config"
    assert c.post(url, json=configuration(a, mode="invalid")).status_code == 422
    r = c.post(url, json=configuration(a, mode="real"))
    assert r.status_code == 200 and r.json()["mode"] == "real"
    assert enter(c, a["slug"])["mode"] == "real"
    assert enter(c, b["slug"])["mode"] == b["mode"]
    assert c.post(url, json=configuration(a)).json()["mode"] == "real"
    assert c.post(url, json=configuration(a, mode="demonstracao")).json()["mode"] == "demonstracao"
    assert "modo_alterado:demonstracao:real" in c.get("/api/platform/audit").text
    c.post("/api/platform/logout", json={})
    assert c.post(url, json=configuration(a, mode="real")).status_code == 401


def test_edit_company_email_is_scoped_and_preserves_password(platform):
    c, _ = platform
    a, b = create(c, "email-a"), create(c, "email-b")
    enter(c, a["slug"])
    user = c.get("/api/platform/companies/" + a["id"] + "/users").json()[0]
    url = "/api/platform/companies/" + a["id"] + "/users/" + user["id"] + "/email"
    assert c.post(url, json={"email": "invalid"}).status_code == 422
    assert c.post(url.replace(a["id"], b["id"]), json={"email": "new@test.pt"}).status_code == 404
    assert c.post(url, json={"email": " NEW@test.pt "}).json()["email"] == "new@test.pt"
    assert (
        c.post(
            "/api/auth/login", json={"email": "admin@test.pt", "password": "TenantPassword123!"}
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/api/auth/login", json={"email": "new@test.pt", "password": "TenantPassword123!"}
        ).status_code
        == 200
    )
    assert "email_utilizador_alterado" in c.get("/api/platform/audit").text
    enter(c, b["slug"])
    c.post("/api/platform/logout", json={})
    assert c.post(url, json={"email": "other@test.pt"}).status_code == 401


def test_clear_stale_company_and_reselect_preserves_current_session(platform):
    c, _ = platform
    a = create(c, "stale-company")
    enter(c, a["slug"])
    assert c.post("/api/company/select", json={"nif": a["nif"]}).status_code == 200
    assert c.get("/api/bootstrap").status_code == 200
    assert c.post("/api/company/clear", json={}).status_code == 200
    assert "gpgest_company" not in c.cookies and "gpgest_session" not in c.cookies
    assert enter(c, a["slug"])["company"]["id"] == a["id"]


def test_reset_data_preserves_configuration_and_archives_records(platform):
    c, factory = platform
    a, b = create(c, "reset-data-a"), create(c, "reset-data-b")
    boot = enter(c, a["slug"])
    assert (
        c.post(
            "/api/sessions", json={"register_id": boot["registers"][0]["id"], "opening": "100"}
        ).status_code
        == 200
    )
    with factory() as db:
        original = db.get(Company, a["id"]).schema_name
    url = "/api/platform/companies/" + a["id"] + "/reset-data"
    assert c.post(url, json={"confirmation": "wrong", "acknowledged": True}).status_code == 422
    assert c.post(url, json={"confirmation": a["nif"], "acknowledged": False}).status_code == 422
    assert c.post(url, json={"confirmation": a["nif"], "acknowledged": True}).status_code == 200
    assert c.get("/api/bootstrap").status_code == 401
    new_boot = enter(c, a["slug"])
    assert new_boot["user"]["id"] == boot["user"]["id"]
    assert new_boot["stores"] == boot["stores"]
    assert c.get("/api/sessions").json() == []
    with factory() as db:
        assert db.get(Company, a["id"]).schema_name != original
    with database.engine.begin() as connection:
        assert connection.scalar(text('SELECT count(*) FROM "' + original + '".cash_sessions')) == 1
        connection.execute(text('DROP SCHEMA "' + original + '" CASCADE'))
    assert "dados_reinicializados" in c.get("/api/platform/audit").text
    assert enter(c, b["slug"])["company"]["id"] == b["id"]
    c.post("/api/platform/logout", json={})
    assert c.post(url, json={"confirmation": a["nif"], "acknowledged": True}).status_code == 401


def test_entities_create_edit_and_company_isolation(platform):
    c, _ = platform
    a, b = create(c, "entities-a"), create(c, "entities-b")
    enter(c, a["slug"])
    data = {
        "name": "Cliente fornecedor",
        "tax_number": "510774318",
        "email": "entity@test.pt",
        "phone": "",
        "is_customer": True,
        "is_supplier": True,
        "active": True,
    }
    assert (
        c.post(
            "/api/entities", json={**data, "is_customer": False, "is_supplier": False}
        ).status_code
        == 422
    )
    r = c.post("/api/entities", json=data)
    assert r.status_code == 200, r.text
    key = r.json()["id"]
    assert c.get("/api/entities").json()[0]["name"] == data["name"]
    assert (
        c.post("/api/entities/" + key, json={**data, "name": "Updated", "active": False}).json()[
            "name"
        ]
        == "Updated"
    )
    enter(c, b["slug"])
    assert c.get("/api/entities").json() == []
    assert c.post("/api/entities/" + key, json=data).status_code == 404
    enter(c, a["slug"])
    assert not c.get("/api/entities").json()[0]["active"]
    c.post("/api/auth/logout", json={})
    assert c.post("/api/entities", json=data).status_code == 401


def test_entity_vies_lookup_and_address_persistence(platform, monkeypatch):
    c, _ = platform
    company = create(c, "entities-vies")
    enter(c, company["slug"])
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "countryCode": "PT",
                "vatNumber": "501234567",
                "valid": True,
                "name": "Empresa Exemplo, Lda.",
                "address": "Rua da Empresa, 10\n1000-001 Lisboa",
            },
        )

    real_client = httpx.Client

    def mock_client(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(entities_module, "HttpxClient", mock_client)
    response = c.get("/api/entities/lookup", params={"tax_number": "PT 501 234 567"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "is_valid": True,
        "name": "Empresa Exemplo, Lda.",
        "address": "Rua da Empresa, 10",
        "locality": "Lisboa",
        "postal_code": "1000-001",
    }
    assert requests[0].method == "POST"
    assert requests[0].url.path.endswith("/rest-api/check-vat-number")
    assert requests[0].read() == b'{"countryCode":"PT","vatNumber":"501234567"}'

    saved = c.post(
        "/api/entities",
        json={
            "name": response.json()["name"],
            "address": response.json()["address"],
            "locality": response.json()["locality"],
            "postal_code": response.json()["postal_code"],
            "tax_number": "501234567",
            "email": "",
            "phone": "",
            "is_customer": True,
            "is_supplier": False,
            "active": True,
        },
    )
    assert saved.status_code == 200, saved.text
    entity = saved.json()
    assert entity["address"] == "Rua da Empresa, 10"
    assert entity["locality"] == "Lisboa"
    assert entity["postal_code"] == "1000-001"
    assert entity["email"] == entity["phone"] == ""
    listed = c.get("/api/entities").json()
    assert listed[0]["address"] == entity["address"]
    assert listed[0]["locality"] == entity["locality"]
    assert listed[0]["postal_code"] == entity["postal_code"]

    assert c.get("/api/entities/lookup", params={"tax_number": "invalid"}).status_code == 422


def test_wintouch_entity_edits_are_local_and_survive_source_updates(platform):
    from app.models import WintouchEntity

    c, factory = platform
    a = create(c, "entities-wt")
    c.post(
        "/api/platform/companies/" + a["id"] + "/config",
        json=configuration(a, api_enterprise="entities-source"),
    )
    with factory() as core:
        schema = core.get(Company, a["id"]).schema_name
    tenant_factory = sessionmaker(
        database.engine.execution_options(schema_translate_map={None: schema}),
        expire_on_commit=False,
    )
    with tenant_factory.begin() as db:
        imported = WintouchEntity(
            enterprise_id="entities-source",
            external_id="external-entity",
            name="Imported",
            is_customer=True,
            is_supplier=False,
        )
        db.add(imported)
        db.flush()
        original_id = imported.id
    enter(c, a["slug"])
    key = "wt:" + original_id
    assert c.get("/api/entities").json()[0]["name"] == "Imported"
    category = c.post("/api/categories", json={"name": "Serviços fornecedor"}).json()
    payload = {"name": "Local edit", "is_customer": True, "is_supplier": True, "category_id": category["id"]}
    assert c.post("/api/entities/" + key, json={**payload, "category_id": "missing"}).status_code == 422
    assert c.post("/api/entities/" + key, json=payload).status_code == 200
    with tenant_factory.begin() as db:
        imported = db.get(WintouchEntity, original_id)
        assert imported.name == "Imported"
        imported.name = "Source updated"
    rows = c.get("/api/entities").json()
    assert len(rows) == 1 and rows[0]["name"] == "Local edit"
    assert rows[0]["category_id"] == category["id"]
    assert c.post("/api/entities/" + key, json={**payload, "category_id": None}).status_code == 200
    assert c.get("/api/entities").json()[0]["category_id"] is None


def test_fiscal_settings_are_validated_and_isolated(platform):
    c, _ = platform
    a = create(c, "fiscal-a")
    b = create(c, "fiscal-b")
    enter(c, a["slug"])
    assert c.get("/api/settings/fiscal").json()["vat_period"] == ""
    assert c.post("/api/settings/fiscal", json={"vat_period": "mensal"}).status_code == 200
    assert c.get("/api/settings/fiscal").json()["vat_period"] == "mensal"
    assert c.post("/api/settings/fiscal", json={"vat_period": "invalid"}).status_code == 422
    enter(c, b["slug"])
    assert c.get("/api/settings/fiscal").json()["vat_period"] == ""
    assert c.post("/api/settings/fiscal", json={"vat_period": "trimestral"}).status_code == 200
    enter(c, a["slug"])
    assert c.get("/api/settings/fiscal").json()["vat_period"] == "mensal"


def test_entity_removal_hides_and_preserves_record(platform):
    from app.models import BusinessEntity

    c, factory = platform
    a = create(c, "entity-removal")
    enter(c, a["slug"])
    entity = c.post(
        "/api/entities", json={"name": "To remove", "is_customer": True, "is_supplier": True}
    ).json()
    url = "/api/entities/" + entity["id"] + "/remove"
    assert c.post(url, json={}).status_code == 200
    assert c.get("/api/entities").json() == []
    assert c.post(url, json={}).status_code == 404
    assert "entidade_removida" in c.get("/api/audit").text
    with factory() as core:
        schema = core.get(Company, a["id"]).schema_name
    with database.SessionLocal(
        bind=database.engine.execution_options(schema_translate_map={None: schema})
    ) as tenant:
        assert tenant.get(BusinessEntity, entity["id"].split(":")[1]).removed
    c.post("/api/auth/logout", json={})
    assert c.post(url, json={}).status_code == 401


@pytest.mark.parametrize("history_source", ["movement", "cash_session"])
def test_entity_with_movements_is_deactivated_instead_of_removed(platform, history_source):
    c, factory = platform
    company = create(c, "entity-history-" + history_source.replace("_", "-"))
    enter(c, company["slug"])
    entity = c.post(
        "/api/entities", json={"name": "Supplier with history", "is_customer": True, "is_supplier": True}
    ).json()
    with factory() as core:
        schema = core.get(Company, company["id"]).schema_name
    with database.SessionLocal(
        bind=database.engine.execution_options(schema_translate_map={None: schema})
    ) as tenant:
        user = tenant.scalar(select(User))
        if history_source == "movement":
            category = Category(name="Historical expense")
            tenant.add(category)
            tenant.flush()
            tenant.add(
                Movement(
                    user_id=user.id,
                    category_id=category.id,
                    kind="despesa",
                    payment="numerario",
                    amount_cents=100,
                    direction=-1,
                    occurred_at=datetime.now(timezone.utc),
                    entity=entity["name"],
                    request_key=str(uuid4()),
                )
            )
        else:
            register = tenant.scalar(select(Register))
            tenant.add(
                CashSession(
                    register_id=register.id,
                    operator_id=user.id,
                    opening_cents=0,
                    opened_at=datetime.now(timezone.utc),
                    closed_at=datetime.now(timezone.utc),
                    count_data=json.dumps(
                        {
                            "debit_supplier": {
                                "allocations": [{"id": entity["id"], "name": entity["name"]}]
                            }
                        }
                    ),
                )
            )
        tenant.commit()

    listed = c.get("/api/entities").json()
    assert next(item for item in listed if item["id"] == entity["id"])["has_movements"] is True
    response = c.post("/api/entities/" + entity["id"] + "/remove", json={})
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "deactivated"
    retained = next(item for item in c.get("/api/entities").json() if item["id"] == entity["id"])
    assert retained["active"] is False
    assert retained["has_movements"] is True
    with database.SessionLocal(
        bind=database.engine.execution_options(schema_translate_map={None: schema})
    ) as tenant:
        record = tenant.get(BusinessEntity, entity["id"].split(":")[1])
        assert record.removed is False
        assert record.active is False
    assert "entidade_inativada" in c.get("/api/audit").text


def test_attachment_settings_are_company_scoped_and_validate_folder(platform):
    c, _ = platform
    a, b = create(c, "attachment-a"), create(c, "attachment-b")
    enter(c, a["slug"])
    data = {
        "outlook_email": "mail@hotmail.com",
        "google_email": "mail@gmail.com",
        "drive_folder": "https://drive.google.com/drive/folders/1OV1fH8lEFrvlTa7P7-OzgkVxgFmMZ5pJ?usp=sharing",
        "mail_folder": "inbox",
    }
    assert c.post("/api/settings/attachments", json=data).status_code == 200
    settings = c.get("/api/settings/attachments").json()
    assert (
        not settings["connected"]
        and settings["config"]["drive_folder"] == "1OV1fH8lEFrvlTa7P7-OzgkVxgFmMZ5pJ"
    )
    assert (
        c.post(
            "/api/settings/attachments", json={**data, "drive_folder": "https://other.example"}
        ).status_code
        == 422
    )
    enter(c, b["slug"])
    assert c.get("/api/settings/attachments").json()["config"] == {}


def test_google_authorization_is_scoped_expiring_and_browser_bound(platform, monkeypatch):
    from urllib.parse import urlsplit, parse_qs

    c, _ = platform
    a = create(c, "google-a")
    enter(c, a["slug"])
    c.post(
        "/api/settings/attachments",
        json={
            "outlook_email": "",
            "google_email": "mail@gmail.com",
            "drive_folder": "123456789012345",
            "mail_folder": "inbox",
        },
    )
    monkeypatch.setattr(
        "app.google_integration.credentials",
        lambda: {
            "client_id": "test-client",
            "client_secret": "private-test-secret",
            "redirect_uris": ["http://localhost:5174/api/integrations/attachments/google/callback"],
        },
    )
    url = "/api/integrations/attachments/google"
    assert c.post(url + "/connect", json={}).status_code == 422
    r = c.post(url + "/connect", json={}, headers={"host": "127.0.0.1:8001", "origin": "http://localhost:5174"})
    assert r.status_code == 200, r.text
    assert "private-test-secret" not in r.text
    state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
    assert (
        c.get(url + "/callback", params={"state": state, "error": "access_denied"}).status_code
        == 400
    )
    nonce = c.cookies.get("gpgest_google_nonce")
    headers = {"cookie": "gpgest_google_nonce=" + nonce}
    assert (
        c.get(
            url + "/callback", params={"state": state, "error": "access_denied"}, headers=headers
        ).status_code
        == 400
    )
    assert (
        c.get(
            url + "/callback", params={"state": state, "code": "replay"}, headers=headers
        ).status_code
        == 400
    )
    assert not c.get(url + "/status").json()["connected"]


def test_google_authorization_from_registered_public_origin(platform, monkeypatch):
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit

    from app import google_integration

    c, _ = platform
    a = create(c, "google-public")
    enter(c, a["slug"])
    c.post(
        "/api/settings/attachments",
        json={
            "outlook_email": "",
            "google_email": "mail@gmail.com",
            "drive_folder": "123456789012345",
            "mail_folder": "inbox",
        },
    )
    public = "https://gpgest.example.pt"
    callback = public + "/api/integrations/attachments/google/callback"
    monkeypatch.setenv("ALLOWED_ORIGINS", public + ",https://outra.example.pt")
    monkeypatch.setattr(
        "app.google_integration.credentials",
        lambda: {"client_id": "test-client", "client_secret": "s", "redirect_uris": [callback]},
    )
    url = "/api/integrations/attachments/google"
    status = c.get(url + "/status").json()
    assert status["configured"] and status["origins"] == [public]
    denied = c.post(url + "/connect", json={}, headers={"origin": "https://outra.example.pt"})
    assert denied.status_code == 422 and "outra.example.pt" in denied.json()["detail"]
    r = c.post(url + "/connect", json={}, headers={"origin": public})
    assert r.status_code == 200, r.text
    query = parse_qs(urlsplit(r.json()["url"]).query)
    assert query["redirect_uri"] == [callback]
    exchanged = []

    def google(request):
        if request.url.path == "/token":
            exchanged.append(parse_qs(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "access_token": "a",
                    "refresh_token": "r",
                    "scope": google_integration.SCOPES,
                },
            )
        return httpx.Response(200, json={"email": "mail@gmail.com"})

    monkeypatch.setattr(
        google_integration,
        "httpx",
        SimpleNamespace(
            Client=lambda timeout: httpx.Client(transport=httpx.MockTransport(google)),
            HTTPError=httpx.HTTPError,
        ),
    )
    done = c.get(
        url + "/callback",
        params={"state": query["state"][0], "code": "ok"},
        headers={"cookie": "gpgest_google_nonce=" + c.cookies.get("gpgest_google_nonce")},
    )
    assert done.status_code == 200, done.text
    assert exchanged[0]["redirect_uri"] == [callback]
    assert "href='https://gpgest.example.pt/'" in done.text
    assert c.get(url + "/status").json()["connected"]
