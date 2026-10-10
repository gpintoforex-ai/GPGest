from decimal import Decimal

import pytest
from app.wintouch_sync import SyncError, normalize


def test_entity_roles_are_explicit_and_unknown_stays_unknown():
    values = normalize(
        "entities", {"ID": "e1", "Name": "Entidade", "IsClient": True, "IsSupplier": True}
    )
    assert values["is_customer"] and values["is_supplier"]
    assert "is_customer" not in normalize("entities", {"ID": "e2", "Name": "Outra"})
    with pytest.raises(SyncError):
        normalize("entities", {"ID": "e3", "Name": "Inválida", "IsSupplier": "false"})


def test_operator_cannot_manage_company_integrations(setup):
    client, _, _ = setup
    assert (
        client.post(
            "/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}
        ).status_code
        == 200
    )
    assert client.get("/api/integrations/wintouch/sync-history").status_code == 403
    assert (
        client.post("/api/integrations/wintouch/sync", json={"resource": "products"}).status_code
        == 403
    )


def test_cost_precision_and_dates_do_not_guess_timezone():
    values = normalize(
        "products",
        {
            "ID": "p1",
            "Code": "P1",
            "Name": "Artigo",
            "LastCostGlobal": Decimal("12.3456"),
            "LastUpdate": "2026-10-11T12:00:00",
        },
    )
    assert values["last_cost"] == Decimal("12.3456")
    assert "source_updated_at" not in values
    with pytest.raises(SyncError):
        normalize(
            "products", {"ID": "p1", "Code": "P1", "Name": "Artigo", "LastCostGlobal": "12.34567"}
        )
