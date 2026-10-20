import json
from datetime import datetime, timezone

import pytest
from app.legal import DEFAULTS, attempts, month_after
from app.platform_models import LegalPublication, PrivacyRequest
from sqlalchemy import select
from test_platform import create, enter
from test_platform import platform as platform  # noqa: F401


@pytest.fixture
def legal_platform(platform):
    attempts.clear()
    yield platform
    attempts.clear()


def complete_config():
    config = json.loads(json.dumps(DEFAULTS))
    config.update(version="test-1", audience="professionals", reviewed=True)
    config["identity"] = dict(
        legal_name="Prestador fictício de testes",
        nif="500000000",
        address="Morada fictícia",
        registry="Registo fictício",
        email="support@example.test",
        privacy_email="privacy@example.test",
    )
    for key in (
        "audience_notice",
        "commercial_terms",
        "processing_notice",
        "providers_notice",
        "transfers_notice",
        "retention_policy",
        "complaints_notice",
        "accessibility_notice",
        "dpa_instructions",
    ):
        config[key] = "Texto fictício aprovado apenas neste teste: " + key
    return config


def publish(client):
    result = client.post(
        "/api/platform/legal", json={**complete_config(), "revision": 0, "publish": True}
    )
    assert result.status_code == 200, result.text
    return client.get("/api/legal/public").json()["publication"]


def test_unfinished_drafts_are_not_public_and_cannot_be_accepted(legal_platform):
    client, _ = legal_platform
    response = client.get("/api/legal/public").json()
    assert not response["published"] and response["publication"] is None
    assert response["optional_categories"] == [] and response["marketing_available"] is False
    assert all("duration" in cookie for cookie in response["cookies"])
    data = {**DEFAULTS, "revision": 0, "publish": True}
    assert client.post("/api/platform/legal", json=data).status_code == 422
    assert client.post("/api/platform/legal", json={**data, "publish": False}).status_code == 200
    assert client.get("/api/legal/public").json()["publication"] is None
    assert client.get("/api/legal/documents/test-1/termos").status_code == 404
    assert (
        client.post(
            "/api/legal/rights", json={"email": "a@example.test", "kind": "access"}
        ).status_code
        == 503
    )


def test_publications_are_immutable_and_downloadable(legal_platform):
    client, factory = legal_platform
    publication = publish(client)
    document = client.get("/api/legal/documents/test-1/termos")
    assert document.status_code == 200 and publication["digest"] in document.text
    assert "attachment" in document.headers["Content-Disposition"]
    data = {**complete_config(), "revision": 1, "publish": True, "commercial_terms": "New terms"}
    assert client.post("/api/platform/legal", json=data).status_code == 409
    data.update(version="test-2")
    assert client.post("/api/platform/legal", json=data).status_code == 200
    assert client.get("/api/legal/documents/test-1/termos").text == document.text
    with factory() as db:
        assert len(list(db.scalars(select(LegalPublication)))) == 2


def test_consumer_offer_requires_separate_reviewed_conditions(legal_platform):
    client, _ = legal_platform
    data = {**complete_config(), "audience": "both", "revision": 0, "publish": True}
    response = client.post("/api/platform/legal", json=data)
    assert response.status_code == 422 and "consumer_terms" in response.text


def test_draft_edit_does_not_replace_current_publication(legal_platform):
    client, _ = legal_platform
    before = publish(client)
    data = {
        **complete_config(),
        "revision": 1,
        "publish": False,
        "commercial_terms": "Unapproved change",
        "reviewed": False,
    }
    assert client.post("/api/platform/legal", json=data).status_code == 200
    assert client.get("/api/legal/public").json()["publication"] == before


def test_only_platform_admin_can_publish_or_read_requests(legal_platform):
    client, _ = legal_platform
    client.cookies.clear()
    assert client.get("/api/platform/legal").status_code == 401
    assert (
        client.post("/api/platform/legal", json={**complete_config(), "revision": 0}).status_code
        == 401
    )
    assert client.get("/api/platform/privacy-requests").status_code == 401


def test_rights_receipt_minimizes_disclosure_and_does_not_delete(legal_platform):
    client, factory = legal_platform
    publish(client)
    result = client.post(
        "/api/legal/rights",
        json={"email": "person@example.test", "kind": "erasure", "message": "Private request"},
    )
    assert result.status_code == 200, result.text
    received = result.json()
    assert client.get("/api/platform/privacy-requests").json()[0]["status"] == "received"
    response = client.post("/api/legal/rights/status", json={"receipt": received["receipt"]})
    assert (
        response.status_code == 200
        and "email" not in response.json()
        and "message" not in response.json()
    )
    assert client.post("/api/legal/rights/status", json={"receipt": "x" * 43}).status_code == 404
    with factory() as db:
        stored = db.get(PrivacyRequest, received["reference"])
        assert received["receipt"] not in stored.receipt_hash
        assert not stored.identity_verified


def test_identity_communication_deadline_and_revision_controls(legal_platform):
    client, _ = legal_platform
    publish(client)
    receipt = client.post(
        "/api/legal/rights", json={"email": "person@example.test", "kind": "access"}
    ).json()
    endpoint = "/api/platform/privacy-requests/" + receipt["reference"]
    update = {
        "revision": 0,
        "status": "answered",
        "identity_verified": False,
        "note": "Reviewed request",
        "communication_sent": True,
    }
    assert client.post(endpoint, json=update).status_code == 422
    update.update(
        status="in_progress", identity_verified=True, extend=True, communication_sent=False
    )
    assert client.post(endpoint, json=update).status_code == 422
    update["communication_sent"] = True
    result = client.post(endpoint, json=update)
    assert result.status_code == 200, result.text
    assert result.json()["revision"] == 1
    assert len(result.json()["history"]) == 1
    assert client.post(endpoint, json=update).status_code == 409


def test_month_deadline_is_calendar_based():
    start = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
    assert month_after(start) == datetime(2026, 2, 28, 12, tzinfo=timezone.utc)
    assert month_after(start, 3) == datetime(2026, 4, 30, 11, tzinfo=timezone.utc)
    # The deadline follows the civil day in Lisbon across daylight-saving changes.
    summer_midnight = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)
    assert month_after(summer_midnight) == datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc)


def test_tenant_acceptance_and_profile_exports_are_scoped(legal_platform):
    client, _ = legal_platform
    published = publish(client)
    a, b = create(client, "legal-tenant-a"), create(client, "legal-tenant-b")
    enter(client, a["slug"])
    endpoint = "/api/privacy/accept-terms"
    data = {"version": published["version"], "digest": published["digest"], "confirmed": True}
    assert client.post(endpoint, json={**data, "digest": "0" * 64}).status_code == 409
    receipt = client.post(endpoint, json=data)
    assert receipt.status_code == 200, receipt.text
    assert client.post(endpoint, json=data).json()["receipt"] == receipt.json()["receipt"]
    assert (
        client.post(
            "/api/privacy/requests", json={"email": "admin@test.pt", "kind": "company_export"}
        ).status_code
        == 200
    )
    mine = client.get("/api/privacy/me").json()
    assert len(mine["acceptances"]) == 1 and len(mine["requests"]) == 1
    assert "password" not in json.dumps(mine) and "token" not in json.dumps(mine)
    enter(client, b["slug"])
    other = client.get("/api/privacy/me").json()
    assert other["acceptances"] == [] and other["requests"] == []
    assert (
        client.get("/api/platform/privacy-requests").status_code == 200
    )  # Separate platform session still present.


def test_public_rights_rate_limit_and_no_marketing_coupling(legal_platform):
    client, _ = legal_platform
    publish(client)
    for _ in range(5):
        assert (
            client.post(
                "/api/legal/rights", json={"email": "person@example.test", "kind": "objection"}
            ).status_code
            == 200
        )
    assert (
        client.post(
            "/api/legal/rights", json={"email": "person@example.test", "kind": "objection"}
        ).status_code
        == 429
    )
    assert (
        client.post(
            "/api/legal/rights",
            json={"email": "person@example.test", "kind": "access", "marketing": True},
        ).status_code
        == 422
    )
