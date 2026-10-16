from test_cash_local import local


def test_bank_institutions_can_be_created_edited_and_deactivated(local):
    client, _, _ = local

    assert client.get("/api/banks").json() == []

    created = client.post("/api/banks", json={"name": "  Banco Exemplo  "})
    assert created.status_code == 200, created.text
    bank = created.json()
    assert bank["name"] == "Banco Exemplo"
    assert bank["active"] is True

    duplicate = client.post("/api/banks", json={"name": "banco exemplo"})
    assert duplicate.status_code == 409

    edited = client.post(
        f"/api/banks/{bank['id']}",
        json={"name": "Banco Atualizado", "active": False},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Banco Atualizado"
    assert edited.json()["active"] is False

    listed = client.get("/api/banks").json()
    assert len(listed) == 1
    assert listed[0]["id"] == bank["id"]
    assert listed[0]["name"] == "Banco Atualizado"
    assert listed[0]["active"] is False


def test_bank_institution_edits_reject_duplicates_and_missing_ids(local):
    client, _, _ = local
    first = client.post("/api/banks", json={"name": "Banco Um"}).json()
    second = client.post("/api/banks", json={"name": "Banco Dois"}).json()

    duplicate = client.post(
        f"/api/banks/{second['id']}",
        json={"name": first["name"], "active": True},
    )
    assert duplicate.status_code == 409

    missing = client.post(
        "/api/banks/missing",
        json={"name": "Banco Novo", "active": True},
    )
    assert missing.status_code == 404
