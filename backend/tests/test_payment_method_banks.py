import importlib
import json
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import Audit, BankAccount, BankTransaction, CashSession, LocalPaymentMethod, Movement, PaymentMethodBankDefault, Register, User
from sqlalchemy import func, inspect, select, text
from test_platform import create, enter, platform
from test_cash_local import local


@pytest.fixture
def bank_local(local, monkeypatch):
    from app import main, payment_methods

    monkeypatch.setattr(payment_methods, "policy", main.integration_policy)
    return local


def accounts(c, ids, factory):
    with factory() as db:
        stores = [db.get(Register, ids["registers"][i]).store_id for i in (0, 2)]
    rows = []
    for store, bank in ((stores[0], "Banco A"), (stores[0], "Banco B"), (stores[1], "Banco C")):
        response = c.post("/api/accounts", json={"store_id": store, "name": bank + " Corrente", "bank": bank, "iban_last4": "1234"})
        assert response.status_code == 200, response.text
        rows.append(response.json())
    return stores, rows


def test_payment_bank_defaults_persist_per_store_without_financial_changes(bank_local):
    c, ids, factory = bank_local
    stores, rows = accounts(c, ids, factory)
    assert c.get("/api/bootstrap").json()["payment_method_bank_defaults"] == []
    custom = c.post("/api/cash/payment-methods", json={"name": "Pagamento digital"}).json()["id"]
    with factory() as db:
        before = [db.scalar(select(func.count()).select_from(model)) for model in (Movement, BankTransaction, CashSession)]
    for method in ("cartao", "mbway", "transferencia", custom):
        response = c.post(f"/api/cash/payment-methods/{method}/bank-default", json={"store_id": stores[0], "account_id": rows[0]["id"]})
        assert response.status_code == 200, response.text
    assert c.post("/api/cash/payment-methods/cartao/bank-default", json={"store_id": stores[1], "account_id": rows[2]["id"]}).status_code == 200
    assert c.post("/api/cash/payment-methods/cartao/bank-default", json={"store_id": stores[0], "account_id": rows[1]["id"]}).status_code == 200
    assert c.post("/api/cash/payment-methods/mbway/status", json={"active": False}).status_code == 200
    # Editing the default never reactivates the method or loses its bank setting.
    assert c.post("/api/cash/payment-methods/mbway/bank-default", json={"store_id": stores[0], "account_id": rows[1]["id"]}).status_code == 200
    boot = c.get("/api/bootstrap").json()
    assert not next(row for row in boot["payment_method_catalog"] if row["id"] == "mbway")["active"]
    saved = {(row["method_id"], row["store_id"]): row["account_id"] for row in boot["payment_method_bank_defaults"]}
    assert len(saved) == 5 and saved["cartao", stores[0]] == rows[1]["id"] and saved["cartao", stores[1]] == rows[2]["id"]
    assert c.get("/api/bootstrap").json()["payment_method_bank_defaults"] == boot["payment_method_bank_defaults"]
    for _ in range(2):
        assert c.post("/api/cash/payment-methods/transferencia/bank-default", json={"store_id": stores[0], "account_id": None}).status_code == 200
    assert len(c.get("/api/bootstrap").json()["payment_method_bank_defaults"]) == 4
    with factory() as db:
        assert before == [db.scalar(select(func.count()).select_from(model)) for model in (Movement, BankTransaction, CashSession)]
        event = db.scalar(select(Audit).where(Audit.action == "meio_pagamento_banco_alterado", Audit.target_id == "cartao", Audit.store_id == stores[0]).order_by(Audit.created_at.desc()))
        assert json.loads(event.detail) == {"name": "Multibanco / TPA", "before": rows[0]["id"], "account_id": rows[1]["id"]}
        assert event.user_id and event.created_at


def test_payment_bank_defaults_validate_account_and_authorize_establishment(bank_local):
    c, ids, factory = bank_local
    stores, rows = accounts(c, ids, factory)
    endpoint = "/api/cash/payment-methods/cartao/bank-default"
    payload = {"store_id": stores[0], "account_id": rows[0]["id"]}
    for update in ({"account_id": "missing"}, {"account_id": ""}, {"account_id": rows[2]["id"]}, {"account_id": 123}, {"unexpected": True}):
        assert c.post(endpoint, json={**payload, **update}).status_code == 422
    assert c.post(endpoint, json={**payload, "store_id": "missing"}).status_code == 404
    assert c.post("/api/cash/payment-methods/missing/bank-default", json=payload).status_code == 404
    assert c.post("/api/cash/payment-methods/numerario/bank-default", json=payload).status_code == 422
    assert c.post(endpoint, json={"store_id": stores[1], "account_id": rows[2]["id"]}).status_code == 200
    for email in ("operator@test.pt", "reader@test.pt"):
        c.post("/api/auth/logout", json={})
        c.post("/api/auth/login", json={"email": email, "password": "PasswordTest123!"})
        assert c.post(endpoint, json=payload).status_code == 403
        assert c.get("/api/bootstrap").json()["payment_method_bank_defaults"] == []
    with factory.begin() as db:
        db.get(User, ids["operator"]).role = "gestor"
    c.post("/api/auth/logout", json={})
    c.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"})
    assert c.post(endpoint, json=payload).status_code == 200
    assert c.post(endpoint, json={"store_id": stores[1], "account_id": rows[2]["id"]}).status_code == 403
    assert c.get("/api/bootstrap").json()["payment_method_bank_defaults"] == [{"method_id": "cartao", **payload}]


def test_payment_bank_defaults_respect_company_scope_and_integrated_mode(platform):
    from app.platform_models import Company

    c, factory = platform
    a, b = create(c, "banco-meios-a"), create(c, "banco-meios-b")
    c.cookies.clear()
    boot = enter(c, a["slug"])
    store_a = boot["stores"][0]["id"]
    response = c.post("/api/accounts", json={"store_id": store_a, "name": "Conta A", "bank": "Banco A", "iban_last4": "1111"})
    assert response.status_code == 200, response.text
    account_a = response.json()["id"]
    endpoint = "/api/cash/payment-methods/mbway/bank-default"
    assert c.post(endpoint, json={"store_id": store_a, "account_id": account_a}).status_code == 200
    boot_b = enter(c, b["slug"])
    store_b = boot_b["stores"][0]["id"]
    assert boot_b["payment_method_bank_defaults"] == []
    assert c.post(endpoint, json={"store_id": store_b, "account_id": account_a}).status_code == 422
    assert c.post(endpoint, json={"store_id": store_a, "account_id": None}).status_code == 404
    with factory.begin() as db:
        db.get(Company, a["id"]).operating_mode = "integrated"
    boot = enter(c, a["slug"])
    assert boot["payment_method_bank_defaults"] == [{"method_id": "mbway", "store_id": store_a, "account_id": account_a}]
    assert c.post(endpoint, json={"store_id": store_a, "account_id": None}).status_code == 409


def test_payment_bank_migration_is_idempotent_and_preserves_existing_configuration(bank_local, monkeypatch):
    c, ids, factory = bank_local
    stores, rows = accounts(c, ids, factory)
    assert c.post("/api/cash/payment-methods/mbway/status", json={"active": False}).status_code == 200
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    migration = importlib.import_module("migrations.versions.0038_payment_method_bank_defaults")
    with factory.kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_[0-9a-f]{32}", schema)
        PaymentMethodBankDefault.__table__.drop(connection)
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert "payment_method_bank_defaults" in inspect(connection).get_table_names(schema=schema)
        assert connection.scalar(text(f"SELECT count(*) FROM {schema}.bank_accounts")) == len(rows)
        assert connection.scalar(text(f"SELECT active FROM {schema}.local_payment_methods WHERE id='mbway'")) is False
