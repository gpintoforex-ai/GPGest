import json

import pytest
from app.models import (
    AccountBalanceReference,
    Audit,
    BankAccount,
    CashSession,
    IntegrationBinding,
    PaymentTerminal,
    Register,
)
from sqlalchemy import select


def register_case(setup):
    client, ids, factory = setup
    with factory() as db:
        row = db.get(Register, ids["registers"][0])
        store_id = row.store_id
        other_store = db.get(Register, ids["registers"][2]).store_id
    return client, ids, factory, store_id, other_store


def test_edit_preserves_history_settings_and_currency(setup):
    client, ids, factory, store, other = register_case(setup)
    identifier = ids["registers"][0]
    with factory.begin() as db:
        row = db.get(Register, identifier)
        row.settings = json.dumps({"fund_cents": 4000, "responsible": "employee:test"})
        db.add(CashSession(register_id=identifier, operator_id=ids["operator"], opening_cents=4000))
    response = client.post(
        f"/api/registers/{identifier}", json={"name": "  Caixa corrigida  ", "store_id": store}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Caixa corrigida"
    assert response.json()["currency"] == "EUR"
    assert (
        client.post(
            f"/api/registers/{identifier}", json={"name": "Caixa corrigida", "store_id": other}
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/registers/{identifier}/delete", json={"confirmed": True}).status_code
        == 409
    )
    with factory() as db:
        row = db.get(Register, identifier)
        assert row.store_id == store
        assert json.loads(row.settings) == {"fund_cents": 4000, "responsible": "employee:test"}
        assert db.scalar(select(CashSession.register_id)) == identifier
        assert db.scalar(select(Audit.id).where(Audit.action == "caixa_editada"))


def test_move_unused_register_and_zero_reference(setup):
    client, ids, factory, store, other = register_case(setup)
    identifier = ids["registers"][0]
    with factory.begin() as db:
        db.add(
            AccountBalanceReference(
                target="cash:" + identifier,
                store_id=store,
                amount_cents=0,
                fund_cents=4000,
                reference_date="2026-10-01",
                user_id=ids["operator"],
            )
        )
    response = client.post(
        f"/api/registers/{identifier}", json={"name": "Caixa transferida", "store_id": other}
    )
    assert response.status_code == 200, response.text
    with factory() as db:
        assert db.get(Register, identifier).store_id == other
        reference = db.get(AccountBalanceReference, "cash:" + identifier)
        assert reference.store_id == other and reference.fund_cents == 4000


def test_confirmed_removal_of_unused_register_only(setup):
    client, ids, factory, store, _ = register_case(setup)
    identifier = ids["registers"][0]
    path = f"/api/registers/{identifier}/delete"
    for payload in ({}, {"confirmed": False}):
        assert client.post(path, json=payload).status_code == 422
    with factory.begin() as db:
        assert db.get(Register, identifier)
        db.add(
            AccountBalanceReference(
                target="cash:" + identifier,
                store_id=store,
                amount_cents=0,
                fund_cents=0,
                reference_date="2026-10-01",
                user_id=ids["operator"],
            )
        )
    response = client.post(path, json={"confirmed": True})
    assert response.status_code == 200, response.text
    with factory() as db:
        assert db.get(Register, identifier) is None
        assert db.get(AccountBalanceReference, "cash:" + identifier) is None
        audit = db.scalar(select(Audit).where(Audit.action == "caixa_removida"))
        assert audit and audit.target_id == identifier and json.loads(audit.detail)["name"] == "A"
        assert db.get(Register, ids["registers"][1]) is not None
    assert client.post(path, json={"confirmed": True}).status_code == 404


@pytest.mark.parametrize("dependency", ["terminal", "integration", "opening"])
def test_configured_associations_and_initial_balance_are_protected(setup, dependency):
    client, ids, factory, store, other = register_case(setup)
    identifier = ids["registers"][0]
    with factory.begin() as db:
        if dependency == "terminal":
            account = BankAccount(store_id=store, name="Banco", bank="Banco", iban_last4="1234")
            db.add(account)
            db.flush()
            db.add(
                PaymentTerminal(
                    name="TPA", store_id=store, account_id=account.id, register_id=identifier
                )
            )
        elif dependency == "integration":
            db.add(
                IntegrationBinding(
                    company_id="test",
                    connector="test",
                    external_kind="register",
                    external_id="external-test",
                    internal_kind="register",
                    internal_id=identifier,
                )
            )
        else:
            db.add(
                AccountBalanceReference(
                    target="cash:" + identifier,
                    store_id=store,
                    amount_cents=10000,
                    reference_date="2026-10-01",
                    user_id=ids["operator"],
                )
            )
    response = client.post(f"/api/registers/{identifier}/delete", json={"confirmed": True})
    assert response.status_code == 409 and "preservados" in response.json()["detail"]
    assert (
        client.post(
            f"/api/registers/{identifier}", json={"name": "Outro nome", "store_id": other}
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/registers/{identifier}", json={"name": "Outro nome", "store_id": store}
        ).status_code
        == 200
    )
    with factory() as db:
        assert db.get(Register, identifier).store_id == store
        if dependency == "opening":
            assert db.get(AccountBalanceReference, "cash:" + identifier).amount_cents == 10000


def test_validation_permissions_and_establishment_access(setup):
    client, ids, factory, store, other = register_case(setup)
    identifier = ids["registers"][0]
    path = f"/api/registers/{identifier}"
    payload = {"name": "Caixa editada", "store_id": store}
    assert client.post(path, json={**payload, "name": " "}).status_code == 422
    assert client.post(path, json={**payload, "currency": "USD"}).status_code == 422
    assert client.post(path, json={**payload, "store_id": "missing"}).status_code == 404
    assert client.post("/api/registers/missing", json=payload).status_code == 404
    for role in ("reader", "operator"):
        client.post("/api/auth/logout")
        client.post(
            "/api/auth/login", json={"email": f"{role}@test.pt", "password": "PasswordTest123!"}
        )
        assert client.post(path, json=payload).status_code == 403
        assert client.post(path + "/delete", json={"confirmed": True}).status_code == 403
    from app.models import User

    with factory.begin() as db:
        db.get(User, ids["operator"]).role = "gestor"
    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}
    )
    assert client.post(path, json=payload).status_code == 200
    assert client.post(path, json={**payload, "store_id": other}).status_code == 403
    assert (
        client.post(
            f"/api/registers/{ids['registers'][2]}/delete", json={"confirmed": True}
        ).status_code
        == 403
    )
    assert client.post(path + "/delete", json={"confirmed": True}).status_code == 200
    client.post("/api/auth/logout")
    assert (
        client.post(
            f"/api/registers/{ids['registers'][1]}/delete", json={"confirmed": True}
        ).status_code
        == 401
    )
