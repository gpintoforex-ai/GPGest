import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from app import rubrics
from app.db import db_session
from app.main import app


@pytest.fixture
def remote(setup, monkeypatch):
    client, ids, factory = setup

    @contextmanager
    def core():
        yield SimpleNamespace(get=lambda *_: SimpleNamespace(api_enterprise="tenant", api_active=True, erp_provider="wintouch"))

    monkeypatch.setattr("app.db.next_platform_session", core)

    def override():
        with factory() as db:
            db.info["company"] = {"id": "test"}
            yield db

    app.dependency_overrides[db_session] = override
    items = {
        "water": {
            "ID": "water",
            "Code": "AGUA",
            "Name": "Água",
            "Type": "M",
            "CashAccountDocumentsNature": 2,
            "VATID": None,
            "VATExemptionMotiveID": None,
        }
    }
    calls = []
    state = {"timeout": False, "hide": False}
    original = httpx.Client

    def handler(request):
        path = request.url.path.removeprefix("/api/v1/")
        if request.method == "GET" and path == "items":
            skip = int(request.url.params.get("skip", 0))
            return httpx.Response(200, json=[] if state["hide"] else list(items.values())[skip : skip + 100])
        if request.method == "GET":
            key = path.removeprefix("items/")
            return (
                httpx.Response(200, json=items[key])
                if key in items and not state["hide"]
                else httpx.Response(404)
            )
        data = json.loads(request.content)
        calls.append((request.method, data))
        if state.get("assigned_id"):
            data = {**data, "ID": "erp-assigned-id"}
        items[data["ID"]] = data
        if state["timeout"]:
            raise httpx.ReadTimeout("unknown outcome", request=request)
        return httpx.Response(204 if request.method == "PUT" else 201)

    monkeypatch.setattr(
        rubrics,
        "connection",
        lambda db: ("tenant", "https://api.wintouchcloud.com/api/v1", "secret"),
    )
    monkeypatch.setattr(
        rubrics.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    return client, items, calls, state


def test_erp_assigned_identity_is_verified_without_repeated_write(remote):
    client, items, calls, state = remote
    category = create(client)
    state["assigned_id"] = True
    result = client.post(f"/api/categories/{category['id']}/erp/send", json={"confirmed": True})
    assert result.status_code == 200, result.text
    assert result.json()["external_id"] == "erp-assigned-id"
    assert len(calls) == 1
    linked = next(row for row in client.get("/api/categories").json() if row["id"] == category["id"])["integration"]
    assert linked["status"] == "confirmado" and linked["external_id"] == "erp-assigned-id"


def create(client, name="Comunicações"):
    result = client.post(
        "/api/categories", json={"name": name, "icon": "phone", "color": "#123456"}
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_remove_local_category_requires_confirmation_and_protects_usage(setup):
    from app.models import BusinessEntity
    client, _, factory = setup
    category = create(client)
    target = f"/api/categories/{category['id']}/remove"
    assert client.post(target, json={}).status_code == 422
    with factory.begin() as db:
        entity = BusinessEntity(name="Fornecedor Demo", is_supplier=True, category_id=category["id"])
        db.add(entity)
        db.flush()
        entity_id = entity.id
    assert client.post(target, json={"confirmed": True}).status_code == 409
    with factory.begin() as db:
        db.get(BusinessEntity, entity_id).category_id = None
    assert client.post(target, json={"confirmed": True}).status_code == 200
    assert all(row["id"] != category["id"] for row in client.get("/api/categories").json())
    assert client.post(target, json={"confirmed": True}).status_code == 404


def test_remove_imported_category_is_local_only(remote):
    client, items, calls, state = remote
    assert client.post("/api/categories/erp/import", json={"ids": ["water"], "confirmed": True}).status_code == 200
    category = next(row for row in client.get("/api/categories").json() if row["name"] == "Água")
    assert client.post(f"/api/categories/{category['id']}/remove", json={"confirmed": True}).status_code == 200
    assert "water" in items and not calls


def test_generic_sync_uses_configured_provider(remote):
    client, _, calls, _ = remote
    settings = client.get("/api/categories/erp/settings")
    assert settings.status_code == 200 and settings.json()["provider"] == "wintouch"
    assert client.get("/api/categories/erp/items").json()[0]["can_reference"] is True
    assert client.post("/api/categories/erp/import", json={"ids": ["water"], "confirmed": True}).status_code == 200
    category = create(client)
    response = client.post(f"/api/categories/{category['id']}/erp/send", json={"code": "TEST", "template_id": "water", "confirmed": True})
    assert response.status_code == 200, response.text
    assert len(calls) == 1


def test_generic_sync_without_erp_is_disabled(setup):
    client, _, _ = setup
    assert client.get("/api/categories/erp/settings").status_code == 409
    assert client.get("/api/categories/erp/items").status_code == 409


def test_sequential_proposals_exclude_existing_and_reserve_codes(remote):
    client, items, calls, state = remote
    existing = create(client, "Água")
    first = create(client, "Primeira nova")
    second = create(client, "Segunda nova")
    proposals = client.get("/api/categories/erp/proposals").json()
    assert next(row for row in proposals["rows"] if row["id"] == existing["id"])["exists"]
    assert client.post(f"/api/categories/{existing['id']}/erp/send", json={"confirmed": True}).status_code == 409
    assert not calls
    state["timeout"] = True
    assert client.post(f"/api/categories/{first['id']}/erp/send", json={"confirmed": True}).status_code == 502
    state["timeout"] = False
    result = client.post(f"/api/categories/{second['id']}/erp/send", json={"confirmed": True})
    assert result.status_code == 200, result.text
    assert [payload["Code"] for _, payload in calls] == ["RUB0001", "RUB0002"]
    assert calls[0][1]["Type"] == "M"
    assert calls[0][1]["CashAccountDocumentsNature"] == 2


def test_sequence_honours_prefix_and_existing_suffixes():
    assert rubrics.next_code("CX-", ["cx-0003", "CX-0009", "AGUA"]) == "CX-0010"


@pytest.mark.parametrize("provider", ["none", "other-erp"])
def test_generic_sync_never_falls_back_to_wintouch(remote, monkeypatch, provider):
    client, _, calls, _ = remote

    @contextmanager
    def core():
        yield SimpleNamespace(get=lambda *_: SimpleNamespace(api_enterprise="tenant", api_active=True, erp_provider=provider))

    monkeypatch.setattr("app.db.next_platform_session", core)
    category = create(client)
    assert client.get("/api/categories/erp/items").status_code == 409
    assert client.post(f"/api/categories/{category['id']}/erp/send", json={"code": "TEST", "template_id": "water", "confirmed": True}).status_code == 409
    assert not calls


def test_local_visual_category_needs_no_erp(setup):
    client, _, _ = setup
    category = create(client)
    assert category["icon"] == "phone" and category["color"] == "#123456"
    assert (
        client.post("/api/categories", json={"name": "Invalid", "icon": "script"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/categories", json={"name": "Invalid", "color": "red;display:none"}
        ).status_code
        == 422
    )
    result = client.post(
        f"/api/categories/{category['id']}",
        json={"name": "Comunicações novas", "icon": "receipt", "color": "#225588"},
    )
    assert result.status_code == 200 and result.json()["icon"] == "receipt"
    assert client.get("/api/categories").status_code == 200


def test_import_preserves_local_style_and_is_idempotent(remote):
    client, _, _, _ = remote
    local = create(client, "Água")
    assert client.get("/api/categories/wintouch/items").status_code == 200
    payload = {"ids": ["water"], "confirmed": True}
    response = client.post("/api/categories/wintouch/import", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1
    assert client.post("/api/categories/wintouch/import", json=payload).json()["imported"] == 0
    rows = client.get("/api/categories").json()
    row = next(r for r in rows if r["id"] == local["id"])
    assert row["icon"] == "phone" and row["integration"]["external_id"] == "water"


def test_send_create_and_update_are_verified(remote):
    client, _, calls, _ = remote
    category = create(client)
    body = {"code": "GP-COM", "template_id": "water", "confirmed": True}
    endpoint = f"/api/categories/{category['id']}/send-wintouch"
    first = client.post(endpoint, json=body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "confirmado"
    assert calls[0][0] == "POST"
    assert "icon" not in calls[0][1] and "color" not in calls[0][1]
    client.post(f"/api/categories/{category['id']}", json={"name": "Comunicações atualizadas"})
    second = client.post(endpoint, json=body)
    assert second.status_code == 200, second.text
    assert calls[1][0] == "PUT" and calls[1][1]["ID"] == calls[0][1]["ID"]
    assert calls[1][1]["Name"] == "Comunicações atualizadas"


def test_ambiguous_send_is_verified_without_a_second_write(remote):
    client, _, calls, state = remote
    category = create(client)
    body = {"code": "GP-COM", "template_id": "water", "confirmed": True}
    endpoint = f"/api/categories/{category['id']}/send-wintouch"
    state["timeout"] = True
    result = client.post(endpoint, json=body)
    assert result.status_code == 502 and "secret" not in result.text
    state["hide"] = True
    assert client.post(endpoint, json=body).status_code == 409
    assert len(calls) == 1
    state["hide"] = False
    assert client.post(endpoint, json=body).json()["status"] == "confirmado"
    assert len(calls) == 1


def test_existing_code_and_missing_confirmation_do_not_write(remote):
    client, _, calls, _ = remote
    category = create(client)
    endpoint = f"/api/categories/{category['id']}/send-wintouch"
    assert (
        client.post(
            endpoint, json={"code": "AGUA", "template_id": "water", "confirmed": True}
        ).status_code
        == 409
    )
    assert (
        client.post(
            endpoint, json={"code": "NEW", "template_id": "water", "confirmed": False}
        ).status_code
        == 422
    )
    assert not calls
