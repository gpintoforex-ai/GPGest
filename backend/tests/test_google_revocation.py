import json

import httpx
import pytest
from app.models import Audit, GoogleConnection
from cryptography.fernet import Fernet
from sqlalchemy import select


@pytest.mark.parametrize("remote_status", [200, 503])
def test_google_disconnect_revokes_and_always_removes_local_credentials(
    setup, monkeypatch, remote_status
):
    client, _, factory = setup
    key = Fernet.generate_key()
    monkeypatch.setenv("PLATFORM_ENCRYPTION_KEY", key.decode())
    with factory.begin() as db:
        db.add(
            GoogleConnection(
                id="default",
                email="test@example.test",
                token_ciphertext=Fernet(key)
                .encrypt(json.dumps({"refresh_token": "fictitious-refresh-token"}).encode())
                .decode(),
                state_hash="old-state",
            )
        )
    calls = []

    def revoke(request):
        assert str(request.url) == "https://oauth2.googleapis.com/revoke"
        assert b"fictitious-refresh-token" in request.content
        calls.append(request)
        return httpx.Response(remote_status)

    original = httpx.Client
    monkeypatch.setattr(
        "app.google_integration.httpx.Client",
        lambda **kwargs: original(transport=httpx.MockTransport(revoke), **kwargs),
    )
    result = client.post("/api/integrations/attachments/google/disconnect", json={})
    assert result.status_code == 200, result.text
    assert result.json()["remote_revoked"] is (remote_status == 200)
    assert len(calls) == 1
    with factory() as db:
        connection = db.get(GoogleConnection, "default")
        assert connection.token_ciphertext == connection.email == connection.state_hash == ""
        audit = db.scalar(select(Audit).where(Audit.action == "google_conta_desligada"))
        assert "fictitious-refresh-token" not in audit.detail
    if remote_status != 200:
        assert "Não foi possível confirmar" in result.json()["message"]
