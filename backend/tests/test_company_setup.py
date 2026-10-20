import copy
import json
from datetime import date

import pytest
from app import company_setup
from app import db as database
from app.models import (
    AccountBalanceReference,
    Audit,
    BankAccount,
    BankTransaction,
    CashSession,
    CompanySetup,
    Movement,
    PaymentTerminal,
    Register,
    StockWarehouse,
)
from app.platform_models import Company
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_platform import create, enter
from test_platform import platform as platform


def start(client, slug="setup-a"):
    company = create(client, slug)
    client.cookies.clear()
    boot = enter(client, company["slug"])
    response = client.get("/api/setup")
    assert response.status_code == 200, response.text
    state = response.json()
    state["data"]["company"].update(vat_regime="normal", vat_period="trimestral")
    return company, boot, state


def save(client, state, step="summary"):
    result = client.post("/api/setup/draft", json={"revision": state["revision"], "step": step, "data": state["data"]})
    assert result.status_code == 200, result.text
    return result.json()


def complete(client, state):
    preview = client.post("/api/setup/preview", json={"revision": state["revision"]})
    assert preview.status_code == 200, preview.text
    body = {"revision": state["revision"], "token": preview.json()["token"], "confirmed": True}
    response = client.post("/api/setup/finish", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def tenant(company, factory):
    with factory() as core:
        schema = core.get(Company, company["id"]).schema_name
    return Session(database.engine.execution_options(schema_translate_map={None: schema}))


def setup_terminals(state):
    data = state["data"]
    store = data["company"]["store_id"]
    data["registers"][0].update(fund="40,00", opening="75,00", start_date=date.today().isoformat())
    data["accounts"] = [
        {"key": key, "bank": "Banco da conta", "name": "Conta " + key, "store_id": store, "currency": "EUR", "principal": key == "A",
         "opening": "100,00", "opening_date": date.today().isoformat(), "iban": "PT50000201231234567890154" if key == "A" else ""}
        for key in ("A", "B")]
    for p in data["payments"]:
        p["selected"] = p["key"] in ("numerario", "cartao", "mbway")
        if p["key"] == "mbway":
            p["mbway_via"] = "terminal"
    data["terminals"] = [{"key": key, "name": "TPA " + key, "store_id": store, "register_id": data["registers"][0]["key"],
                          "account": key, "provider": "Prestador diferente do banco", "settlement_days": 2, "mbway": key == "B",
                          "settlement_mode": "net", "commission_percent": "0,50", "commission_fixed": None} for key in ("A", "B")]
    return state


def test_local_setup_resumes_applies_once_and_routes_two_terminals_without_duplicate_revenue(platform):
    client, factory = platform
    company, boot, state = start(client)
    state = setup_terminals(state)
    state = save(client, state, "banks")
    assert "PT50000201231234567890154" not in json.dumps(state)
    client.post("/api/auth/logout", json={})
    enter(client, company["slug"])
    resumed = client.get("/api/setup").json()
    assert resumed["step"] == "banks" and len(resumed["data"]["accounts"]) == 2
    assert resumed["iban_configured"] == ["A"]
    state, body = complete(client, resumed)
    assert state["completed"] and state["progress"] == 100
    assert client.post("/api/setup/finish", json=body).status_code == 200
    state, _ = complete(client, save(client, state))
    with tenant(company, factory) as db:
        assert db.scalar(select(func.count()).select_from(Register)) == 1
        assert db.scalar(select(func.count()).select_from(BankAccount)) == 2
        assert db.scalar(select(func.count()).select_from(PaymentTerminal)) == 2
        assert db.scalar(select(func.count()).select_from(AccountBalanceReference)) == 3
        assert db.scalar(select(func.count()).select_from(Movement)) == 0
        assert db.scalar(select(func.count()).select_from(CashSession)) == 0
        stored = db.scalar(select(BankAccount).where(BankAccount.name == "Conta A"))
        assert stored.iban_last4 == "0154" and stored.iban_ciphertext and "PT50" not in stored.iban_ciphertext
    boot = client.get("/api/bootstrap").json()
    assert set(boot["payment_methods"]) == {"numerario", "cartao", "mbway"}
    assert len(boot["payment_terminals"]) == 2
    assert not any(d["method_id"] == "cartao" for d in boot["payment_method_bank_defaults"])
    register = boot["registers"][0]["id"]
    suggestion = client.get(f"/api/registers/{register}/opening-suggestion", params={"sheet_day": date.today().isoformat()}).json()
    assert suggestion["last_opening_cents"] == 7500
    opened = client.post("/api/sessions", json={"register_id": register, "opening": "75"})
    assert opened.status_code == 200, opened.text
    assert opened.json()["retained_fund_cents"] == 4000
    created = []
    for terminal in boot["payment_terminals"]:
        from app.models import now, uid
        response = client.post("/api/movements", json={"session_id": opened.json()["id"], "category_id": boot["categories"][0]["id"],
            "payment": "cartao", "terminal_id": terminal["id"], "kind": "venda", "amount": "20,00", "occurred_at": now().isoformat(), "request_key": uid()})
        assert response.status_code == 200, response.text
        assert response.json()["bank_account_id"] == terminal["account_id"]
        created.append(response.json())
    with tenant(company, factory) as db:
        movement = created[0]
        bank = BankTransaction(account_id=movement["bank_account_id"], movement_date=date.today().isoformat(), value_date=date.today().isoformat(),
                               description="Liquidação TPA", amount_cents=1950, balance_cents=11950, fingerprint="onboard-bank", source_account="test", exported_at="2026-01-01T12:00:00", source_position=1)
        db.add(bank)
        db.commit()
        bank_id = bank.id
    response = client.post("/api/reconciliation/confirm", json={"kind": "cartao", "commission_cents": 50, "reason": "Comissão confirmada no extrato",
        "lines": [{"source": "bank", "source_id": bank_id, "amount_cents": 1950}, {"source": "movement", "source_id": created[0]["id"], "amount_cents": 2000}]})
    assert response.status_code == 200, response.text
    with tenant(company, factory) as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 2
        assert db.scalar(select(func.sum(Movement.amount_cents))) == 4000


def test_draft_errors_stale_versions_and_initial_balances_are_atomic(platform):
    client, factory = platform
    company, _, state = start(client, "setup-errors")
    state = save(client, state, "company")
    stale = client.post("/api/setup/draft", json={"revision": state["revision"] - 1, "step": "company", "data": state["data"]})
    assert stale.status_code == 409
    invalid = copy.deepcopy(state)
    invalid["data"]["accounts"] = [{"key": "bad", "name": "Conta", "bank": "Banco", "iban": "PT50000000000000000000000"}]
    assert client.post("/api/setup/draft", json={"revision": state["revision"], "step": "banks", "data": invalid["data"]}).status_code == 422
    assert client.get("/api/setup").json()["revision"] == state["revision"]
    state = setup_terminals(state)
    state = save(client, state)
    finished, _ = complete(client, state)
    invalid = copy.deepcopy(finished)
    invalid["data"]["company"]["name"] = "Nome que não deve ficar guardado"
    invalid["data"]["registers"][0]["opening"] = "100,00"
    saved = save(client, invalid)
    preview = client.post("/api/setup/preview", json={"revision": saved["revision"]}).json()
    result = client.post("/api/setup/finish", json={"revision": saved["revision"], "token": preview["token"], "confirmed": True})
    assert result.status_code == 409
    with factory() as db:
        assert db.get(Company, company["id"]).name == company["name"]
    with tenant(company, factory) as db:
        assert db.get(AccountBalanceReference, "cash:" + saved["data"]["registers"][0]["id"]).amount_cents == 7500
        assert db.scalar(select(func.count()).select_from(BankAccount)) == 2
    assert client.get("/api/setup").json()["data"]["company"]["name"] == invalid["data"]["company"]["name"]


def test_only_administrators_and_current_company_can_change_setup(platform):
    client, factory = platform
    a, boot, state = start(client, "setup-scope-a")
    saved = complete(client, save(client, setup_terminals(state)))[0]
    assert client.post("/api/platform/login", json={"email": "master@test.pt", "password": "MasterPassword123!"}).status_code == 200
    b = create(client, "setup-scope-b")
    client.cookies.clear()
    other_boot = enter(client, b["slug"])
    other = client.get("/api/setup").json()
    other["data"]["company"].update(vat_regime="normal", vat_period="mensal")
    other["data"]["accounts"] = [{**saved["data"]["accounts"][0], "store_id": other_boot["stores"][0]["id"]}]
    other = save(client, other)
    result = client.post("/api/setup/preview", json={"revision": other["revision"]})
    assert result.status_code == 200
    assert client.post("/api/setup/finish", json={"revision": other["revision"], "token": result.json()["token"], "confirmed": True}).status_code == 422
    with tenant(b, factory) as db:
        assert db.scalar(select(func.count()).select_from(BankAccount)) == 0
    with tenant(a, factory) as db:
        assert db.scalar(select(func.count()).select_from(BankAccount)) == 2
    user = client.post("/api/users", json={"name": "Gestor", "email": "manager@setup.test", "password": "SetupManager123!", "role": "gestor", "store_ids": [other_boot["stores"][0]["id"]]})
    assert user.status_code == 200
    client.post("/api/auth/logout", json={})
    client.post("/api/auth/login", json={"email": "manager@setup.test", "password": "SetupManager123!"})
    assert client.get("/api/setup").status_code == 403
    assert client.get("/api/setup/status").json()["can_edit"] is False
    assert client.post("/api/setup/draft", json={"revision": other["revision"], "step": "company", "data": other["data"]}).status_code == 403


def test_erp_is_tested_previewed_and_imported_once_and_secrets_are_not_returned(platform, monkeypatch):
    client, factory = platform
    company, _, state = start(client, "setup-erp")
    key = "private-onboarding-erp-key"
    state["data"]["mode"].update(mode="integrated", provider="wintouch", enterprise="erp-company", api_key=key)
    state = save(client, state, "mode")
    assert key not in json.dumps(state) and not state["connection_tested"]
    assert client.post("/api/setup/preview", json={"revision": state["revision"]}).status_code == 422
    import httpx
    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, params, headers):
            assert headers["Authorization"] == "ApiKey " + key
            assert headers["x-current-enterprise"] == "erp-company"
            rows = [{"ID": "erp-register", "Name": "Caixa do ERP", "Enabled": True}] if url.endswith("/cash_accounts") and not params["skip"] else []
            return httpx.Response(200, json=rows)
    monkeypatch.setattr(company_setup.httpx, "Client", Client)
    result = client.post("/api/setup/erp/test", json={"revision": state["revision"]})
    assert result.status_code == 200, result.text
    state = result.json()
    assert state["connection_tested"] and key not in result.text
    preview = client.post("/api/setup/erp/preview", json={"revision": state["revision"], "resources": ["cash_accounts"], "confirm_demo": True})
    assert preview.status_code == 200, preview.text
    snapshot = preview.json()
    assert snapshot["resources"]["cash_accounts"]["count"] == 1
    imported = {"revision": snapshot["revision"], "token": snapshot["token"], "confirmed": True}
    result = client.post("/api/setup/erp/import", json=imported)
    assert result.status_code == 200, result.text
    assert client.post("/api/setup/erp/import", json=imported).status_code == 200
    state = result.json()
    state["data"]["registers"][0]["erp_id"] = "erp-register"
    state, _ = complete(client, save(client, state))
    with factory() as db:
        stored = db.get(Company, company["id"])
        assert stored.api_active and key not in stored.api_key_ciphertext
    with tenant(company, factory) as db:
        from app.models import WintouchCashAccount
        assert db.scalar(select(func.count()).select_from(WintouchCashAccount)) == 1
        assert db.scalar(select(WintouchCashAccount)).register_id == state["data"]["registers"][0]["id"]
        assert key not in " ".join(db.scalars(select(Audit.detail)))
    state["data"]["mode"]["enterprise"] = "different-company"
    state = save(client, state)
    assert not state["connection_tested"]
    assert client.post("/api/setup/preview", json={"revision": state["revision"]}).status_code == 422


def test_erp_can_be_left_pending_without_activating_connection(platform):
    client, factory = platform
    company, _, state = start(client, "setup-pending")
    state["data"]["mode"].update(mode="integrated", defer=True)
    state, _ = complete(client, save(client, state))
    assert state["completed"] and any("ERP" in message for message in state["pending"])
    with factory() as db:
        stored = db.get(Company, company["id"])
        assert stored.operating_mode == "integrated" and not stored.api_active


def test_optional_steps_keep_unfinished_answers_without_creating_records(platform):
    client, factory = platform
    company, _, state = start(client, "setup-skip")
    state["data"]["accounts"] = [{"key": "unfinished", "bank": "Ainda por confirmar", "name": ""}]
    state["data"]["terminals"] = [{"key": "unfinished-tpa", "name": "Terminal futuro", "account": ""}]
    state["data"]["skipped"] = ["banks", "terminals"]
    state, _ = complete(client, save(client, state))
    assert state["completed"] and state["pending"]
    assert state["data"]["accounts"][0]["bank"] == "Ainda por confirmar"
    assert state["data"]["terminals"][0]["name"] == "Terminal futuro"
    with tenant(company, factory) as db:
        assert db.scalar(select(func.count()).select_from(BankAccount)) == 0
        assert db.scalar(select(func.count()).select_from(PaymentTerminal)) == 0


def test_count_and_manual_entry_preferences_do_not_invent_cash_or_revenue(platform):
    client, factory = platform
    company, boot, state = start(client, "setup-preferences")
    register = state["data"]["registers"][0]
    register.update(fund="40", opening="10", start_date=date.today().isoformat(), count_required=False)
    state["data"]["preferences"].update(manual_realized=False, require_reason=False, daily_close=False, unclosed_warning=False)
    state, _ = complete(client, save(client, state))
    opened = client.post("/api/sessions", json={"register_id": register["id"], "opening": "10"}).json()
    from app.models import now, uid
    response = client.post("/api/movements", json={"session_id": opened["id"], "category_id": boot["categories"][0]["id"], "payment": "numerario",
        "kind": "venda", "amount": "5", "occurred_at": now().isoformat(), "request_key": uid()})
    assert response.status_code == 200, response.text
    sheet = client.get("/api/sessions").json()[0]
    assert sheet["sheet"]["manual_realized"] is False
    assert next(r for r in sheet["sheet"]["reconciliation"]["rows"] if r["key"] == "numerario")["declared_cents"] == 500
    closed = client.post(f"/api/sessions/{opened['id']}/close", json={})
    assert closed.status_code == 200, closed.text
    assert closed.json()["counted_cents"] is None and closed.json()["expected_cents"] == 1500
    assert client.get(f"/api/sessions/{opened['id']}/report").status_code == 200
    with tenant(company, factory) as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 1
        assert db.scalar(select(AccountBalanceReference)).amount_cents == 1000
        assert db.scalar(select(Register)).settings
    assert client.get("/api/setup/status").json()["unclosed_count"] == 0


def test_setup_migration_is_additive_idempotent_and_preserves_existing_data(platform, monkeypatch):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    client, factory = platform
    company, _, _ = start(client, "setup-migration")
    with tenant(company, factory) as db:
        bind = db.get_bind()
    schema = bind.get_execution_options()["schema_translate_map"][None]
    migration = importlib.import_module("migrations.versions.0041_company_setup")
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    monkeypatch.setattr(helper, "schemas", lambda connection: [schema])
    with bind.begin() as conn:
        from sqlalchemy import text
        before = conn.scalar(text(f'SELECT count(*) FROM "{schema}".users'))
        for table, columns in (("movements", ["terminal_id", "payment_method_id"]), ("cash_sessions", ["retained_fund_cents"]),
                               ("registers", ["settings"]), ("local_payment_methods", ["settings"]), ("bank_accounts", ["settings", "iban_ciphertext"])):
            for column in columns:
                conn.execute(text(f'ALTER TABLE "{schema}"."{table}" DROP COLUMN "{column}"'))
        PaymentTerminal.__table__.drop(conn)
        CompanySetup.__table__.drop(conn)
        conn.execute(text(f'ALTER TABLE "{schema}".wt_payment_methods DROP CONSTRAINT wt_payment_mapping'))
        conn.execute(text(f"ALTER TABLE \"{schema}\".wt_payment_methods ADD CONSTRAINT wt_payment_mapping CHECK (local_payment IS NULL OR local_payment IN ('numerario','cartao','transferencia','outro'))"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
        assert "company_setup" in inspect(conn).get_table_names(schema=schema)
        assert "payment_terminals" in inspect(conn).get_table_names(schema=schema)
        assert conn.scalar(text(f'SELECT count(*) FROM "{schema}".users')) == before
        assert conn.scalar(text(f'SELECT count(*) FROM "{schema}".movements')) == 0
        assert conn.scalar(text(f'SELECT settings FROM "{schema}".registers LIMIT 1')) == "{}"


@pytest.mark.parametrize("value", ["PT50000201231234567890154", "DE89370400440532013000"])
def test_iban_validation(value):
    assert company_setup.iban(value) == value


def test_same_iban_cannot_create_duplicate_accounts_and_failure_keeps_draft(platform):
    client, factory = platform
    company, _, state = start(client, "setup-iban-duplicates")
    state = setup_terminals(state)
    state["data"]["accounts"][1]["iban"] = state["data"]["accounts"][0]["iban"]
    state = save(client, state)
    preview = client.post("/api/setup/preview", json={"revision": state["revision"]}).json()
    result = client.post("/api/setup/finish", json={"revision": state["revision"], "token": preview["token"], "confirmed": True})
    assert result.status_code == 422, result.text
    with tenant(company, factory) as db:
        assert db.scalar(select(func.count()).select_from(BankAccount)) == 0
        assert db.scalar(select(func.count()).select_from(AccountBalanceReference)) == 0
    assert len(client.get("/api/setup").json()["data"]["accounts"]) == 2


def test_erp_receipts_respect_explicit_payment_associations(platform):
    from types import SimpleNamespace

    from app.cash_local import receipt_rows
    from app.cash_sheet import day
    from app.models import LocalPaymentMethod, WintouchPaymentMethod, WintouchSaleRecord, now

    client, factory = platform
    company, boot, _ = start(client, "setup-payment-mapping")
    register_id = boot["registers"][0]["id"]
    store_id = boot["stores"][0]["id"]
    with tenant(company, factory) as db:
        db.add(LocalPaymentMethod(id="pm_mobile", name="Serviço móvel", settings="{}"))
        db.add(WintouchPaymentMethod(enterprise_id="mapped-erp", external_id="mobile", name="MB WAY", local_payment="pm_mobile"))
        db.add(WintouchSaleRecord(enterprise_id="mapped-erp", external_id="receipt", store_id=store_id,
            kind="real", document_date=date.today().isoformat(), data=json.dumps({"payment": "MB WAY",
                "payment_code": "CD", "payment_method_id": "mobile", "amount_cents": 2500,
                "direction": 1, "occurred_at": now().isoformat()})))
        db.commit()
        sheet = SimpleNamespace(register_id=register_id, sheet_day=day(now()), opened_at=now(), wintouch_enterprise="mapped-erp", wintouch_scope=None)
        rows = receipt_rows(db, sheet, [{"key": "pm_mobile", "name": "Serviço móvel"}, {"key": "mbway", "name": "MB WAY"}])
        assert {r["key"]: r["registered_cents"] for r in rows} == {"pm_mobile": 2500, "mbway": 0}
        assert db.scalar(select(func.count()).select_from(Movement)) == 0


def test_finish_preserves_saft_mapping_and_lists_saft_steps(platform):
    client, factory = platform
    company, boot, state = start(client, "setup-saft")
    state["data"]["mode"]["mode"] = "local"
    state = save(client, state, "saft")
    assert state["step"] == "saft"
    preview = client.post("/api/setup/preview", json={"revision": state["revision"]}).json()
    assert "Associar os códigos de pagamento SAF-T aos meios de pagamento" in preview["pending"]
    assert "Importar o primeiro ficheiro SAF-T de vendas" in preview["pending"]
    finished, _ = complete(client, state)
    with tenant(company, factory) as db:
        row = db.get(CompanySetup, "default")
        row.settings = json.dumps({**json.loads(row.settings), "saft_payment_mapping": {"NU": "numerario"}})
        db.commit()
    # Repetir a configuração não pode apagar a associação SAF-T guardada na empresa.
    complete(client, save(client, client.get("/api/setup").json()))
    with tenant(company, factory) as db:
        stored = json.loads(db.get(CompanySetup, "default").settings)
        assert stored["saft_payment_mapping"] == {"NU": "numerario"}
        assert stored["preferences"]["saft_block_unmapped"] is True
    state = client.get("/api/setup").json()
    assert "Associar os códigos de pagamento SAF-T aos meios de pagamento" not in state["pending"]


def test_vat_regime_is_required_applied_and_prefilled(platform):
    client, factory = platform
    company, boot, state = start(client, "setup-iva")
    state["data"]["company"].update(vat_regime="", vat_period="")
    state = save(client, state)
    preview = client.post("/api/setup/preview", json={"revision": state["revision"]})
    assert preview.status_code == 422 and "regime de IVA" in preview.json()["detail"]
    state["data"]["company"].update(vat_regime="isento_53", vat_period="")
    state = save(client, state)
    assert client.post("/api/setup/preview", json={"revision": state["revision"]}).status_code == 422
    # Os pequenos retalhistas declaram sempre trimestralmente, qualquer que seja o pedido.
    state["data"]["company"].update(vat_regime="pequenos_retalhistas", vat_period="mensal")
    complete(client, save(client, state))
    assert client.get("/api/settings/fiscal").json() == {"vat_regime": "pequenos_retalhistas", "vat_period": "trimestral"}
    with tenant(company, factory) as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "configuracao_fiscal_alterada")) == 1
    # Uma alteração posterior nas Configurações aparece ao rever a configuração.
    assert client.post("/api/settings/fiscal", json={"vat_regime": "isento_53", "vat_period": "mensal"}).status_code == 200
    with tenant(company, factory) as db:
        db.get(CompanySetup, "default").draft = "{}"
        db.commit()
    data = client.get("/api/setup").json()["data"]["company"]
    assert (data["vat_regime"], data["vat_period"]) == ("isento_53", "mensal")


def test_fiscal_settings_accept_previous_requests_without_regime(platform):
    client, _ = platform
    start(client, "setup-iva-legado")
    assert client.post("/api/settings/fiscal", json={"vat_period": "mensal"}).json() == {"vat_regime": "normal", "vat_period": "mensal"}
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": ""}).status_code == 422
    assert client.post("/api/settings/fiscal", json={"vat_regime": "", "vat_period": ""}).json() == {"vat_regime": "", "vat_period": ""}


def test_setup_proposes_one_warehouse_for_the_main_store(platform):
    client, factory = platform
    company, boot, state = start(client, "setup-armazem")
    assert state["data"]["company"]["create_warehouse"] is True
    assert state["data"]["company"]["warehouse_name"] == "Armazém principal"
    state["data"]["company"]["warehouse_name"] = " "
    state = save(client, state)
    assert client.post("/api/setup/preview", json={"revision": state["revision"]}).status_code == 422
    state["data"]["company"]["warehouse_name"] = "Armazém da loja"
    complete(client, save(client, state))
    # Repetir a configuração não duplica o armazém.
    complete(client, save(client, client.get("/api/setup").json()))
    with tenant(company, factory) as db:
        rows = list(db.scalars(select(StockWarehouse)))
        assert [(row.name, row.store_id) for row in rows] == [("Armazém da loja", state["data"]["company"]["store_id"])]
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "stock_warehouse")) == 1
    view = client.get("/api/setup").json()
    assert [w["name"] for w in view["warehouses"]] == ["Armazém da loja"]
    # Sem rascunho, um estabelecimento com armazém não volta a propor outro.
    with tenant(company, factory) as db:
        db.get(CompanySetup, "default").draft = "{}"
        db.commit()
    data = client.get("/api/setup").json()["data"]["company"]
    assert (data["create_warehouse"], data["warehouse_name"]) == (False, "Armazém da loja")


def test_setup_without_warehouse_creates_none(platform):
    client, factory = platform
    company, boot, state = start(client, "setup-sem-armazem")
    state["data"]["company"]["create_warehouse"] = False
    complete(client, save(client, state))
    with tenant(company, factory) as db:
        assert db.scalar(select(func.count()).select_from(StockWarehouse)) == 0
