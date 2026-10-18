import json

from app.models import (
    Audit,
    BankAccount,
    BankInstitution,
    BankTransaction,
    LocalPaymentMethod,
    PaymentMethodBankDefault,
    Register,
)
from sqlalchemy import select


def account_fixture(setup, **overrides):
    client, ids, factory = setup
    with factory.begin() as db:
        store_id = db.get(Register, ids["registers"][0]).store_id
        account = BankAccount(
            **{
                "store_id": store_id, "name": "Conta original", "bank": "Banco inicial",
                "iban_last4": "1234", "currency": "EUR",
                "settings": json.dumps({"principal": True, "custom": "preservado"}),
                **overrides,
            }
        )
        db.add(account)
        db.flush()
        account_id = account.id
    return client, factory, account_id


def test_edit_preserves_statement_and_payment_associations(setup):
    client, factory, account_id = account_fixture(setup)
    with factory.begin() as db:
        bank = BankInstitution(name="Banco novo")
        db.add_all([bank, LocalPaymentMethod(id="cartao", name="Multibanco / TPA")])
        db.flush()
        bank_id = bank.id
        account = db.get(BankAccount, account_id)
        db.add(PaymentMethodBankDefault(method_id="cartao", store_id=account.store_id, account_id=account_id))
        db.add(BankTransaction(
            account_id=account_id, movement_date="2026-01-02", value_date="2026-01-02",
            description="Movimento preservado", amount_cents=1000, balance_cents=2000,
            fingerprint="a" * 64, source_account="origem", exported_at="2026-01-02T00:00:00",
            source_position=0,
        ))
    response = client.post(f"/api/accounts/{account_id}", json={
        "name": "  Conta atualizada  ", "bank": "  Banco novo  ", "iban_last4": "5678",
    })
    assert response.status_code == 200, response.text
    assert response.json()["id"] == account_id
    assert response.json()["name"] == "Conta atualizada"
    assert response.json()["bank"] == "Banco novo"
    assert response.json()["iban_last4"] == "5678"
    assert "iban_ciphertext" not in response.json()
    with factory() as db:
        account = db.get(BankAccount, account_id)
        assert json.loads(account.settings) == {"principal": True, "custom": "preservado", "bank_id": bank_id}
        assert account.currency == "EUR"
        assert db.scalar(select(BankTransaction)).account_id == account_id
        assert db.scalar(select(PaymentMethodBankDefault)).account_id == account_id
        audit = db.scalar(select(Audit).where(Audit.action == "conta_editada"))
        assert audit and audit.store_id == account.store_id
    assert client.get(f"/api/accounts/{account_id}/transactions").json()[0]["amount_cents"] == 1000


def test_invalid_changes_and_bank_link_preserve_account(setup):
    client, factory, account_id = account_fixture(setup, settings='{"principal":true,"bank_id":"old"}')
    original = {"name": "Conta original", "bank": "Banco inicial", "iban_last4": "1234"}
    for changes in ({"name": " "}, {"bank": " "}, {"iban_last4": "12a4"}):
        assert client.post(f"/api/accounts/{account_id}", json={**original, **changes}).status_code == 422
    for changes in ({"store_id": "other"}, {"currency": "USD"}):
        assert client.post(f"/api/accounts/{account_id}", json={**original, **changes}).status_code == 409
    assert client.post("/api/accounts/missing", json=original).status_code == 404
    response = client.post(f"/api/accounts/{account_id}", json={**original, "bank": "Banco livre"})
    assert response.status_code == 200, response.text
    with factory() as db:
        account = db.get(BankAccount, account_id)
        assert account.name == original["name"] and account.iban_last4 == "1234"
        assert json.loads(account.settings) == {"principal": True}


def test_complete_iban_is_preserved_and_not_exposed(setup):
    client, factory, account_id = account_fixture(setup, iban_ciphertext="encrypted-test-value")
    data = {"name": "Conta corrigida", "bank": "Banco inicial", "iban_last4": "1234"}
    response = client.post(f"/api/accounts/{account_id}", json=data)
    assert response.status_code == 200
    assert response.json()["iban_configured"] is True
    assert "encrypted-test-value" not in response.text
    assert client.post(f"/api/accounts/{account_id}", json={**data, "iban_last4": "5678"}).status_code == 409
    with factory() as db:
        account = db.get(BankAccount, account_id)
        assert account.iban_ciphertext == "encrypted-test-value" and account.iban_last4 == "1234"


def test_account_without_iban_can_be_renamed(setup):
    client, _, account_id = account_fixture(setup, iban_last4="")
    assert client.post(f"/api/accounts/{account_id}", json={
        "name": "Conta sem IBAN", "bank": "Banco inicial", "iban_last4": "",
    }).status_code == 200


def test_edit_opening_balance_and_primary(setup):
    from app.models import AccountBalanceReference
    client, factory, account_id = account_fixture(setup)
    data = {"name": "Conta", "bank": "Banco inicial", "iban_last4": "1234", "opening": "150,25", "opening_date": "2026-01-01", "principal": True}
    response = client.post(f"/api/accounts/{account_id}", json=data)
    assert response.status_code == 200, response.text
    with factory() as db:
        assert db.get(AccountBalanceReference, "bank:" + account_id).amount_cents == 15025
    assert client.post(f"/api/accounts/{account_id}", json={**data, "opening": "200"}).status_code == 409


def test_roles_and_store_access(setup):
    client, ids, factory = setup
    client, factory, account_id = account_fixture(setup)
    with factory.begin() as db:
        other = BankAccount(store_id=db.get(Register, ids["registers"][2]).store_id,
                            name="Conta outra loja", bank="Outro banco", iban_last4="9876")
        db.add(other)
        db.flush()
        other_id = other.id
    data = {"name": "Conta corrigida", "bank": "Banco inicial", "iban_last4": "1234"}
    for role in ("reader", "operator"):
        client.post("/api/auth/logout")
        client.post("/api/auth/login", json={"email": f"{role}@test.pt", "password": "PasswordTest123!"})
        assert client.post(f"/api/accounts/{account_id}", json=data).status_code == 403
    with factory.begin() as db:
        from app.models import User
        db.get(User, ids["operator"]).role = "gestor"
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"})
    assert client.post(f"/api/accounts/{account_id}", json=data).status_code == 200
    assert client.post(f"/api/accounts/{other_id}", json=data).status_code == 403
    client.post("/api/auth/logout")
    assert client.post(f"/api/accounts/{account_id}", json=data).status_code == 401


def test_institution_rename_updates_linked_accounts_only(setup):
    client, factory, account_id = account_fixture(setup)
    with factory.begin() as db:
        bank = BankInstitution(name="Banco inicial")
        other_bank = BankInstitution(name="Outro banco")
        db.add_all([bank, other_bank])
        db.flush()
        bank_id = bank.id
        account = db.get(BankAccount, account_id)
        linked = BankAccount(store_id=account.store_id, name="Ligada por ID", bank="Nome antigo",
                             iban_last4="0000", settings=json.dumps({"bank_id": bank.id, "principal": True}))
        unrelated = BankAccount(store_id=account.store_id, name="Outra conta", bank="Banco inicial",
                                iban_last4="0001", settings=json.dumps({"bank_id": other_bank.id}))
        db.add_all([linked, unrelated])
        db.flush()
        linked_id, unrelated_id = linked.id, unrelated.id
    response = client.post(f"/api/banks/{bank_id}", json={"name": "Banco renomeado", "active": False})
    assert response.status_code == 200
    with factory() as db:
        assert db.get(BankAccount, account_id).bank == "Banco renomeado"
        assert db.get(BankAccount, linked_id).bank == "Banco renomeado"
        assert json.loads(db.get(BankAccount, linked_id).settings)["principal"] is True
        assert db.get(BankAccount, unrelated_id).bank == "Banco inicial"
