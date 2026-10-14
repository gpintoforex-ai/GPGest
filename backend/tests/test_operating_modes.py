import json
import httpx
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import db as database
from app.models import Audit, BusinessEntity, CategoryIntegrationLink, IntegrationBinding, WintouchEntity, WintouchSaleRecord
from app.platform_models import Company
from app.operating_modes import company_policy
from app.liquidity import today
from test_platform import platform, create, enter
from test_liquidity import liquidity, forecast
from app.models import BankTransaction, PlannedMovement
from uuid import uuid4


def tenant(company):
    with database.next_platform_session() as core:
        schema = core.get(Company, company["id"]).schema_name
    return Session(database.engine.execution_options(schema_translate_map={None: schema}))


def proposed(**updates):
    return {"mode": "integrated", "provider": "wintouch", "api_active": True, "api_enterprise": "demo-erp", "api_key": "isolated-test-secret", "allow_writes": False, "reason": "Configuração explícita de teste", **updates}


def apply(client, body):
    preview = client.post("/api/company/integration/preview", json=body)
    assert preview.status_code == 200, preview.text
    response = client.post("/api/company/integration/apply", json={**body, "preview_token": preview.json()["token"], "confirmed": True})
    assert response.status_code == 200, response.text
    return preview.json()


def test_local_readonly_permissions_and_tenant_isolation(platform):
    client, core = platform
    a, b = create(client, "modos-a"), create(client, "modos-b")
    client.cookies.clear()
    boot = enter(client, a["slug"])
    assert boot["integration"]["mode"] == "local"
    assert not any(boot["integration"]["capabilities"].values())
    assert client.post("/api/integrations/wintouch/sync", json={"resource": "products", "confirm_demo": True}).status_code == 409
    apply(client, proposed())
    configuration = client.get("/api/company/integration")
    assert "isolated-test-secret" not in configuration.text and "ciphertext" not in configuration.text
    assert configuration.json()["policy"]["capabilities"]["read_entities"]
    assert not configuration.json()["policy"]["capabilities"]["write_settlements"]
    assert client.get("/api/integrations/wintouch/sales/unknown/settlement-options").status_code == 409
    with core() as db:
        assert db.get(Company, b["id"]).operating_mode == "local"
        assert db.get(Company, a["id"]).mode == "demonstracao"
    client.cookies.clear()
    enter(client, b["slug"])
    assert client.get("/api/company/integration").json()["settings"]["api_enterprise"] == ""
    assert client.post("/api/company/integration/bind", json={"internal_kind": "register", "internal_id": boot["registers"][0]["id"], "external_id": "foreign-record", "reason": "Tentativa cruzada"}).status_code == 404


def test_pending_blocks_change_and_preview_becomes_stale(platform):
    client, _ = platform
    company = create(client, "modos-pendentes")
    client.cookies.clear(); boot = enter(client, company["slug"])
    body = proposed()
    preview = client.post("/api/company/integration/preview", json=body).json()
    with tenant(company) as db:
        link = CategoryIntegrationLink(category_id=boot["categories"][0]["id"], provider="wintouch", enterprise_id="demo-erp", external_id="pending-id", external_code="RUB1", status="por_verificar")
        db.add(link); db.commit(); identifier = link.id
    assert client.post("/api/company/integration/apply", json={**body, "preview_token": preview["token"], "confirmed": True}).status_code == 409
    blocked = client.post("/api/company/integration/preview", json=body).json()
    assert blocked["pending"]
    assert client.post("/api/company/integration/apply", json={**body, "preview_token": blocked["token"], "confirmed": True}).status_code == 409
    with tenant(company) as db:
        db.get(CategoryIntegrationLink, identifier).status = "confirmado"; db.commit()
    apply(client, body)
    with tenant(company) as db:
        assert db.scalar(select(Audit.id).where(Audit.action == "modo_funcionamento_alterado"))
        db.get(CategoryIntegrationLink, identifier).status = "por_verificar"; db.commit()
    local = proposed(mode="local", api_active=False, api_key="")
    preview = client.post("/api/company/integration/preview", json=local).json()
    assert client.post("/api/company/integration/apply", json={**local, "preview_token": preview["token"], "confirmed": True}).status_code == 409
    assert client.post("/api/company/integration/apply", json={**local, "preview_token": preview["token"], "confirmed": True, "pending_reviewed": True}).status_code == 200
    assert client.get("/api/company/integration").json()["pending"]


def test_explicit_association_readonly_external_and_local_metadata(platform):
    client, _ = platform
    company = create(client, "modos-entidades")
    client.cookies.clear(); boot = enter(client, company["slug"])
    apply(client, proposed())
    payload = {"name": "Fornecedor ERP", "tax_number": "500000001", "email": "", "phone": "", "is_customer": False, "is_supplier": True, "active": True, "category_id": None}
    native = client.post("/api/entities", json=payload).json()
    with tenant(company) as db:
        remote = WintouchEntity(enterprise_id="demo-erp", external_id="entity-1", name=payload["name"], tax_number=payload["tax_number"], is_customer=False, is_supplier=True)
        other = WintouchEntity(enterprise_id="demo-erp", external_id="entity-2", name="Outro fornecedor", is_supplier=True)
        db.add_all([remote, other]); db.commit(); remote_id, other_id = remote.id, other.id
    binding = {"internal_kind": "entity", "internal_id": native["id"].partition(":")[2], "external_id": remote_id, "reason": "Correspondência revista"}
    for _ in range(2):
        response = client.post("/api/company/integration/bind", json=binding)
        assert response.status_code == 200, response.text
    assert client.post("/api/company/integration/bind", json={**binding, "external_id": other_id}).status_code == 409
    key = "wt:" + remote_id
    assert client.post("/api/entities/" + key, json={**payload, "name": "Alteração externa"}).status_code == 409
    assert client.post("/api/entities/" + key, json={**payload, "category_id": boot["categories"][0]["id"]}).status_code == 200
    assert client.post("/api/entities/" + key + "/remove", json={}).status_code == 409
    with tenant(company) as db:
        assert len(list(db.scalars(select(IntegrationBinding)))) == 1
        db.get(WintouchEntity, remote_id).name = "Nome atualizado pelo ERP"; db.commit()
    listed = next(e for e in client.get("/api/entities").json() if e["id"] == key)
    assert listed["name"] == "Nome atualizado pelo ERP" and listed["category_id"] == boot["categories"][0]["id"] and listed["editable"] is False
    apply(client, proposed(mode="local", api_active=False, api_key=""))
    assert client.post("/api/entities/" + key, json={**payload, "name": "Nome local"}).status_code == 200
    with tenant(company) as db:
        assert db.get(WintouchEntity, remote_id).name == "Nome atualizado pelo ERP"


def test_pending_copy_edit_and_initial_cash_without_opening(platform):
    client, _ = platform
    company = create(client, "modos-saldos")
    client.cookies.clear(); boot = enter(client, company["slug"])
    store, register, category = boot["stores"][0]["id"], boot["registers"][0]["id"], boot["categories"][0]["id"]
    with tenant(company) as db:
        pending = WintouchSaleRecord(enterprise_id="demo-erp", external_id="pending-1", store_id=store, kind="previsto", document_date=today().isoformat(), data=json.dumps({"document": "FT teste", "entity": "Fornecedor", "direction": -1, "amount_cents": 3000, "due_date": today().isoformat(), "occurred_at": today().isoformat()+"T10:00:00"}))
        db.add(pending); db.commit(); identifier = pending.id
    body = {"category_id": category, "reason": "Assumir a gestão local", "confirmed": True}
    copied = client.post(f"/api/company/integration/pending/{identifier}/local-copy", json=body)
    assert copied.status_code == 200, copied.text
    assert client.post(f"/api/company/integration/pending/{identifier}/local-copy", json=body).json()["id"] == copied.json()["id"]
    assert client.post("/api/planned-movements/"+copied.json()["id"]+"/edit", json={"store_id": store, "category_id": category, "entity": "Fornecedor", "document": "Documento local", "direction": -1, "due_date": today().isoformat(), "amount": "25"}).status_code == 200
    balance = client.post("/api/company/integration/balances", json={"target": "cash:"+register, "amount_cents": 15000, "fund_cents": 4000, "reference_date": today().isoformat(), "reason": "Saldo de início revisto"})
    assert balance.status_code == 200, balance.text
    result = client.post("/api/liquidity/forecast", json={"start": today().isoformat(), "end": (today()+timedelta(days=5)).isoformat(), "targets": ["cash:"+register]}).json()
    assert next(a for a in result["accounts"] if a["key"] == "cash:"+register)["balance_cents"] == 15000
    assert [e["key"] for e in result["events"] if e["source"] in ("planned", "wintouch")] == ["planned:"+copied.json()["id"]]
    with tenant(company) as db:
        assert json.loads(db.get(WintouchSaleRecord, identifier).data)["amount_cents"] == 3000


def test_connection_failure_preserves_mode_and_periodic_requires_explicit_consent(platform, monkeypatch):
    client, _ = platform
    company = create(client, "modos-falha")
    client.cookies.clear(); enter(client, company["slug"])
    assert client.post("/api/company/integration/preview", json=proposed(interval_minutes=15)).status_code == 422
    apply(client, proposed())
    def failed(*args, **kwargs):
        raise RuntimeError("Never expose API key in errors")
    monkeypatch.setattr("app.operating_modes.httpx.Client", failed)
    response = client.post("/api/company/integration/test", json={})
    assert response.status_code == 200 and response.json()["success"] is False
    assert "API key" not in response.text
    assert client.get("/api/bootstrap").json()["integration"]["mode"] == "integrated"


def test_uncertain_settlement_verified_by_read_only_without_repeating_write(platform, monkeypatch):
    client, _ = platform
    company = create(client, "modos-verificar")
    client.cookies.clear(); boot = enter(client, company["slug"])
    apply(client, proposed())
    with tenant(company) as db:
        row = WintouchSaleRecord(enterprise_id="demo-erp", external_id="pending:external-1", store_id=boot["stores"][0]["id"], kind="previsto", document_date=today().isoformat(), data=json.dumps({"document": "FT Demo", "entity": "Fornecedor", "direction": -1, "amount_cents": 5000, "due_date": today().isoformat()}))
        db.add(row); db.flush(); identifier = row.id
        db.add(Audit(user_id=boot["user"]["id"], action="liquidacao_wintouch_iniciada", target_id=identifier, detail=json.dumps({"settlement_id": "assigned-id", "amount_cents": 2000})))
        db.commit()
    original_client = httpx.Client
    calls = []
    def transport(request):
        calls.append(request.method)
        value = {"ID": "assigned-id", "Amount": 20} if "settlement_documents" in request.url.path else {"ID": "external-1", "StateValue": 30}
        return httpx.Response(200, json=value)
    monkeypatch.setattr("app.operating_modes.httpx.Client", lambda **kwargs: original_client(transport=httpx.MockTransport(transport)))
    response = client.post(f"/api/company/integration/pending/{identifier}/verify", json={})
    assert response.status_code == 200, response.text
    assert calls == ["GET", "GET"]
    assert client.get("/api/company/integration").json()["pending"] == []
    with tenant(company) as db:
        assert json.loads(db.get(WintouchSaleRecord, identifier).data)["amount_cents"] == 3000


def test_bank_reference_already_includes_older_local_payment(liquidity):
    client, ids, factory, store, bank = liquidity
    yesterday = (today()-timedelta(days=1)).isoformat()
    with factory.begin() as db:
        db.scalar(select(BankTransaction)).movement_date = yesterday
        row = PlannedMovement(store_id=store, category_id=ids["category"], entity="Fornecedor", document="Documento Demo", direction=-1, due_date=today().isoformat(), amount_cents=5000)
        db.add(row); db.flush(); identifier = row.id
    response = client.post("/api/liquidity/settle/planned:"+identifier, json={"amount_cents": 2000, "target": "bank:"+bank, "payment_date": yesterday, "category_id": ids["category"], "request_key": uuid4().hex, "reason": "Pagamento já refletido no saldo inicial", "confirmed": True})
    assert response.status_code == 200, response.text
    response = client.post("/api/company/integration/balances", json={"target": "bank:"+bank, "amount_cents": 15000, "reference_date": today().isoformat(), "reason": "Saldo após os pagamentos anteriores"})
    assert response.status_code == 200, response.text
    result = forecast(liquidity, targets=["bank:"+bank])
    assert result["groups"]["EUR"]["initial_cents"] == 15000
    assert next(e for e in result["events"] if e["key"] == "planned:"+identifier)["amount_cents"] == 3000


def test_consultation_cannot_configure_or_set_balances(setup):
    client, ids, _ = setup
    assert client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/company/integration").status_code == 403
    assert client.post("/api/company/integration/preview", json=proposed(mode="local")).status_code == 403
    assert client.post("/api/company/integration/balances", json={"target": "cash:"+ids["registers"][0], "amount_cents": 1000, "reference_date": today().isoformat(), "reason": "Tentativa sem permissão"}).status_code == 403


def test_periodic_reads_selected_catalog_and_stops_in_local_mode(platform, monkeypatch):
    from app.integration_scheduler import tick
    client, _ = platform
    company = create(client, "modos-periodico")
    client.cookies.clear(); enter(client, company["slug"])
    apply(client, proposed(resources=["entities"], interval_minutes=15, periodic_demo_confirmed=True))
    calls = []
    monkeypatch.setattr("app.wintouch_sync.synchronize", lambda bind, enterprise, resource, url, key: calls.append(resource))
    tick(); tick()
    assert calls == ["entities"]
    apply(client, proposed(mode="local", api_active=False, api_key=""))
    tick()
    assert calls == ["entities"]
