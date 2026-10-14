import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from app.models import (BankAccount, BankTransaction, LiquidityManual, Movement, PlannedMovement,
                        PlannedSettlement, Register, WintouchSaleRecord)
from app.liquidity import today, occurrences
from conftest import movement, open_cash
from test_deposits import deposits, delivered, command, statement


@pytest.fixture
def liquidity(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        account = BankAccount(store_id=store, name="Conta liquidez", bank="Demo", iban_last4="1234")
        db.add(account)
        db.flush()
        key = account.id
        db.add(BankTransaction(account_id=key, movement_date=today().isoformat(), value_date=today().isoformat(), description="Saldo demonstrativo", amount_cents=10000, balance_cents=10000, fingerprint=uuid4().hex, source_account="Demo", exported_at="2026-10-13 10:00:00", source_position=1))
    return client, ids, factory, store, key


def forecast(fixture, **changes):
    client = fixture[0]
    body = {"start": today().isoformat(), "end": (today() + timedelta(days=29)).isoformat(), **changes}
    response = client.post("/api/liquidity/forecast", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def manual(fixture, **changes):
    client, _, _, store, _ = fixture
    body = {"store_id": store, "title": "Compromisso Demo", "amount_cents": 2000, "direction": -1, "expected_date": today().isoformat(), "reason": "Registo de demonstração", **changes}
    response = client.post("/api/liquidity/manual", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def test_same_day_cash_flow_reserve_stale_and_csv(liquidity):
    client, _, factory, _, bank = liquidity
    manual(liquidity, direction=1, amount_cents=3000, target="bank:" + bank)
    manual(liquidity, amount_cents=5000, target="bank:" + bank)
    assert client.post("/api/liquidity/settings", json={"reserves": {"EUR": 9000}, "stale_days": 7, "reason": "Reserva aprovada"}).status_code == 200
    result = forecast(liquidity, targets=["bank:" + bank])
    group = result["groups"]["EUR"]
    assert (group["initial_cents"], group["final_cents"], group["required_cents"]) == (10000, 8000, 1000)
    assert group["daily"][0]["incoming_cents"] == 3000 and group["daily"][0]["outgoing_cents"] == 5000
    assert group["first_below"]["date"] == today().isoformat()
    with factory.begin() as db:
        row = db.scalar(select(BankTransaction).where(BankTransaction.account_id == bank))
        row.movement_date = (today() - timedelta(days=10)).isoformat()
    result = forecast(liquidity, targets=["bank:" + bank])
    assert next(a for a in result["accounts"] if a["key"] == "bank:" + bank)["stale"]
    export = client.post("/api/liquidity/export", json={"targets": ["bank:" + bank]})
    assert export.status_code == 200 and "Premissas" in export.text and "Qualidade dos saldos" in export.text


def test_partial_settlement_and_realized_are_not_counted_twice(liquidity):
    client, ids, factory, store, bank = liquidity
    with factory.begin() as db:
        planned = PlannedMovement(store_id=store, category_id=ids["category"], entity="Cliente Demo", document="DOC-DEMO", due_date=today().isoformat(), amount_cents=10000, direction=1)
        db.add(planned); db.flush(); key = planned.id
    cash = open_cash(client, ids["registers"][0], "0").json()["id"]
    result = movement(client, ids, cash, kind="cliente", amount="40")
    assert result.status_code == 200, result.text
    with factory.begin() as db:
        db.add(PlannedSettlement(planned_id=key, movement_id=result.json()["id"]))
    result = forecast(liquidity)
    assert next(e for e in result["events"] if e["key"] == "planned:" + key)["amount_cents"] == 6000
    assert result["groups"]["EUR"]["initial_cents"] == 14000
    assert result["groups"]["EUR"]["final_cents"] == 20000


def test_bank_balance_reuses_latest_export_first_position_rule(liquidity):
    client, _, factory, _, bank = liquidity
    with factory.begin() as db:
        for position, value in [(0, 12000), (1, 9000)]:
            db.add(BankTransaction(account_id=bank, movement_date=today().isoformat(), value_date=today().isoformat(), description="Exportação mais recente", amount_cents=1000, balance_cents=value, fingerprint=uuid4().hex, source_account="Demo", exported_at=today().isoformat() + " 23:00:00", source_position=position))
    assert forecast(liquidity, targets=["bank:" + bank])["groups"]["EUR"]["initial_cents"] == 12000


def test_overdue_requires_explicit_date_and_origin_stays_unchanged(liquidity):
    client, ids, factory, store, bank = liquidity
    with factory.begin() as db:
        planned = PlannedMovement(store_id=store, category_id=ids["category"], entity="Cliente Demo", document="VENCIDA", due_date=(today() - timedelta(days=5)).isoformat(), amount_cents=3000, direction=1)
        db.add(planned); db.flush(); key = "planned:" + planned.id
    result = forecast(liquidity)
    assert next(e for e in result["pending"] if e["key"] == key)["exclusion"] == "Vencida sem data prevista"
    assert next(e for e in result["pending"] if e["key"] == key)["overdue_days"] == 5
    assert next(e for e in result["pending"] if e["key"] == key)["days_until_due"] == -5
    body = {"expected_date": (today() + timedelta(days=2)).isoformat(), "target": "bank:" + bank, "confidence": "confirmado", "reason": "Cliente confirmou a data"}
    assert client.post("/api/liquidity/adjust/" + key, json=body).status_code == 200
    assert next(e for e in forecast(liquidity)["scheduled"] if e["key"] == key)["expected_date"] == body["expected_date"]
    origin = client.get("/api/liquidity/origin/" + key).json()["data"]
    assert origin["due_date"] == (today() - timedelta(days=5)).isoformat()
    with factory.begin() as db:
        db.get(PlannedMovement, key.split(":")[1]).amount_cents = 4000
    assert next(e for e in forecast(liquidity)["pending"] if e["key"] == key)["conflict"]
    assert client.post("/api/liquidity/adjust/" + key, json=body).status_code == 200
    assert next(e for e in forecast(liquidity)["scheduled"] if e["key"] == key)["amount_cents"] == 4000


def test_internal_transfer_consolidated_and_by_account(liquidity):
    client, ids, factory, store, bank = liquidity
    with factory.begin() as db:
        second = BankAccount(store_id=store, name="Destino", bank="Demo", iban_last4="9999")
        db.add(second); db.flush(); target = second.id
    manual(liquidity, target="bank:" + target, from_target="bank:" + bank, amount_cents=3500, due_date=(today() + timedelta(days=3)).isoformat(), expected_date=(today() + timedelta(days=3)).isoformat())
    result = forecast(liquidity)
    assert result["groups"]["EUR"]["final_cents"] == 10000
    assert all(e["days_until_due"] == 3 for e in result["scheduled"])
    assert forecast(liquidity, targets=["bank:" + bank])["groups"]["EUR"]["final_cents"] == 6500
    assert forecast(liquidity, targets=["bank:" + target])["groups"]["EUR"]["final_cents"] == 3500


def test_recurring_clamp_replaced_and_inactive(liquidity):
    client, ids, factory, store, bank = liquidity
    start = date = today().replace(day=1)
    row, body = manual(liquidity, target="bank:" + bank, expected_date=date.isoformat(), due_date=date.isoformat(), recurrence="monthly", recurrence_end=(today() + timedelta(days=60)).isoformat())
    occurrence = next(e for e in forecast(liquidity, end=(today() + timedelta(days=60)).isoformat())["events"] if e.get("manual_id") == row["id"])
    with factory.begin() as db:
        planned = PlannedMovement(store_id=store, category_id=ids["category"], entity="Senhorio", document="RENDA", due_date=today().isoformat(), amount_cents=2000, direction=-1)
        db.add(planned); db.flush(); key = planned.id
    assert client.post("/api/liquidity/adjust/planned:" + key, json={"expected_date": today().isoformat(), "target": "bank:" + bank, "replaces_key": occurrence["key"], "reason": "Fatura substitui compromisso"}).status_code == 200
    assert any(e["key"] == occurrence["key"] and "substituída" in e["exclusion"] for e in forecast(liquidity)["pending"])
    assert client.post("/api/liquidity/manual/" + row["id"], json={**body, "active": False}).status_code == 200
    data = {"expected_date": "2027-01-31", "recurrence": "monthly", "recurrence_end": "2027-03-31"}
    from datetime import date as Date
    assert list(occurrences(data, Date(2027, 3, 31))) == ["2027-01-31", "2027-02-28", "2027-03-31"]


def test_scenarios_conservative_custom_immutable_and_currency(liquidity):
    client, _, factory, store, bank = liquidity
    row, _ = manual(liquidity, direction=1, target="bank:" + bank, confidence="incerto")
    result = forecast(liquidity, scenario_id="conservative", delay_days=5)
    assert result["groups"]["EUR"]["final_cents"] == 10000
    key = "manual:" + row["id"]
    scenario = client.post("/api/liquidity/scenarios", json={"name": "Financiamento simulado", "changes": {key: {"amount_cents": 5000}}, "reason": "Simulação autorizada"})
    assert scenario.status_code == 200, scenario.text
    result = forecast(liquidity, scenario_id=scenario.json()["id"], compare=True)
    assert result["groups"]["EUR"]["final_cents"] == 15000 and result["comparison"]["EUR"]["final_cents"] == 12000
    with factory() as db:
        assert json.loads(db.get(LiquidityManual, row["id"]).data)["amount_cents"] == 2000
    manual(liquidity, currency="USD", direction=1, amount_cents=7000)
    result = forecast(liquidity)
    assert result["groups"]["USD"]["final_cents"] == 7000 and result["groups"]["EUR"]["final_cents"] == 12000
    assert client.post("/api/liquidity/manual", json={"store_id": store, "title": "Moeda errada", "amount_cents": 100, "currency": "USD", "target": "bank:" + bank, "reason": "Teste inválido"}).status_code == 422


def test_erp_pending_partial_snapshot_is_not_invoice_total(liquidity):
    client, _, factory, store, bank = liquidity
    with factory.begin() as db:
        db.add(WintouchSaleRecord(enterprise_id="demo", external_id="pending-demo", store_id=store, kind="previsto", document_date=today().isoformat(), active=True, data=json.dumps({"document": "FAC DEMO", "entity": "Cliente ERP", "amount_cents": 4500, "direction": 1, "due_date": today().isoformat(), "occurred_at": today().isoformat() + "T12:00:00", "source_document_id": "fac-demo"})))
    result = forecast(liquidity)
    assert next(e for e in result["scheduled"] if e["source"] == "wintouch")["amount_cents"] == 4500
    assert next(e for e in result["scheduled"] if e["source"] == "wintouch")["document_date"] == today().isoformat()
    assert result["groups"]["EUR"]["final_cents"] == 14500


def test_confirmed_partial_erp_payment_keeps_adjusted_forecast(liquidity):
    from app.models import Audit, User
    client, _, factory, store, bank = liquidity
    with factory.begin() as db:
        row = WintouchSaleRecord(enterprise_id="demo", external_id="partial-demo", store_id=store, kind="previsto", document_date=today().isoformat(), active=True, data=json.dumps({"document": "FAC PARCIAL", "amount_cents": 4500, "direction": -1, "due_date": today().isoformat(), "occurred_at": today().isoformat() + "T12:00:00"}))
        db.add(row); db.flush(); identifier = row.id
    key = "wintouch:" + identifier
    assert client.post("/api/liquidity/adjust/" + key, json={"expected_date": today().isoformat(), "target": "bank:" + bank, "reason": "Data de pagamento acordada"}).status_code == 200
    with factory.begin() as db:
        user = db.scalar(select(User).where(User.role == "administrador"))
        payment = uuid4().hex
        db.add(Audit(user_id=user.id, store_id=store, action="liquidacao_wintouch_iniciada", target_id=identifier, detail=json.dumps({"settlement_id": payment, "pending_value_cents": -4500, "amount_cents": 1500})))
        db.add(Audit(user_id=user.id, store_id=store, action="liquidacao_wintouch_concluida", target_id=payment, detail=json.dumps({"record_id": identifier})))
        row = db.get(WintouchSaleRecord, identifier)
        data = json.loads(row.data); data["amount_cents"] = 3000; row.data = json.dumps(data)
    result = forecast(liquidity, targets=["bank:" + bank])
    entry = next(e for e in result["scheduled"] if e["key"] == key)
    assert not entry["conflict"] and entry["amount_cents"] == 3000
    assert entry["paid_cents"] == 1500
    assert entry["days_until_due"] == 0
    assert result["groups"]["EUR"]["outgoing_cents"] == 3000
    with factory.begin() as db:
        row = db.get(WintouchSaleRecord, identifier)
        data = json.loads(row.data); data["due_date"] = (today() + timedelta(days=1)).isoformat(); row.data = json.dumps(data)
    assert next(e for e in forecast(liquidity)["pending"] if e["key"] == key)["conflict"]


def test_deposit_in_transit_requires_date_and_partial_credit_removed(deposits):
    client, ids, factory, cash, bank, _ = deposits
    deposit = delivered(deposits, "20")
    key = "deposit:" + deposit["id"]
    result = forecast(deposits)
    assert result["transit"][0]["amount_cents"] == 2000
    assert next(e for e in result["pending"] if e["key"] == key)["expected_date"] is None
    body = {"expected_date": today().isoformat(), "target": "bank:" + bank, "confidence": "confirmado", "reason": "Banco confirmou disponibilidade"}
    assert client.post("/api/liquidity/adjust/" + key, json=body).status_code == 200
    bank_transaction = statement(deposits, 1000)
    assert command(client, deposit, "reconcile", transaction_id=bank_transaction, amount="10").status_code == 200
    result = forecast(deposits)
    assert result["transit"][0]["amount_cents"] == 1000
    assert next(e for e in result["pending"] if e["key"] == key)["conflict"]
    assert client.post("/api/liquidity/adjust/" + key, json=body).status_code == 200
    assert next(e for e in forecast(deposits)["scheduled"] if e["key"] == key)["amount_cents"] == 1000


def test_permissions_and_foreign_target_blocked(liquidity):
    client, ids, factory, store, bank = liquidity
    assert client.post("/api/liquidity/forecast", json={"targets": ["bank:" + uuid4().hex]}).status_code == 404
    row, body = manual(liquidity, target="bank:" + bank)
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.post("/api/liquidity/settings", json={"reserves": {"EUR": 100}, "reason": "Sem permissão"}).status_code == 403
    assert client.post("/api/liquidity/manual", json={**body, "recurrence": "monthly"}).status_code == 403
    assert client.post("/api/liquidity/scenarios", json={"name": "Partilhado", "shared": True, "reason": "Sem permissão"}).status_code == 403
    with factory.begin() as db:
        other_store = db.get(Register, ids["registers"][2]).store_id
    assert client.post("/api/liquidity/manual", json={**body, "store_id": other_store}).status_code == 403


def test_document_information_attachments_require_active_explicit_erp_link(liquidity):
    from app.models import DocumentERPLink, ERPDocument, Purchase, PurchaseAttachment, now
    client, _, factory, store, bank = liquidity
    with factory.begin() as db:
        purchase = Purchase(store_id=store, supplier_nif="500000000", supplier_name="Fornecedor Demo", document="DOC DEMO", document_date=today().isoformat(), amount_cents=1000)
        erp = ERPDocument(store_id=store, enterprise_id="demo", external_id="invoice-demo", supplier_nif="500000000", supplier_name="Fornecedor Demo", document="DOC DEMO", internal_document="COMPRA DEMO", document_type="FT", document_date=today().isoformat(), amount_cents=1000)
        pending = WintouchSaleRecord(enterprise_id="demo", external_id="attachment-demo", store_id=store, kind="previsto", document_date=today().isoformat(), active=True, data=json.dumps({"document": "DOC DEMO", "amount_cents": 1000, "direction": -1, "due_date": today().isoformat(), "occurred_at": today().isoformat() + "T12:00:00", "source_document_id": "invoice-demo"}))
        db.add_all([purchase, erp, pending]); db.flush()
        db.add(PurchaseAttachment(purchase_id=purchase.id, name="DOCUMENTO.pdf", mime="application/pdf", data=b"%PDF-demo"))
        key = "wintouch:" + pending.id
        purchase_id, erp_id = purchase.id, erp.id
    assert client.get("/api/liquidity/origin/" + key).json()["attachments"] == []
    with factory.begin() as db:
        link = DocumentERPLink(purchase_id=purchase_id, erp_id=erp_id, reason="Associação confirmada")
        db.add(link); db.flush(); link_id = link.id
    attachments = client.get("/api/liquidity/origin/" + key).json()["attachments"]
    assert len(attachments) == 1 and attachments[0]["name"] == "DOCUMENTO.pdf"
    assert attachments[0]["view_url"].endswith("/view")
    with factory.begin() as db:
        db.get(DocumentERPLink, link_id).undone_at = now()
    assert client.get("/api/liquidity/origin/" + key).json()["attachments"] == []


def test_company_schema_blocks_forecasts_manual_adjustments_and_scenarios(liquidity):
    from app.db import db_session, engine
    from app.main import app
    from app.models import Base, User
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    client, _, factory, store, bank = liquidity
    row, body = manual(liquidity)
    saved = client.post("/api/liquidity/scenarios", json={"name": "Empresa original", "shared": True, "reason": "Teste de isolamento"}).json()
    schema, other_engine = "test_" + uuid4().hex, None
    previous = app.dependency_overrides[db_session]
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA " + schema))
        other_engine = create_engine(engine.url, connect_args={"options": "-csearch_path=" + schema})
        Base.metadata.create_all(other_engine)
        other_factory = sessionmaker(other_engine, expire_on_commit=False)
        with factory() as db:
            password = db.scalar(select(User.password_hash).where(User.email == "admin@test.pt"))
        with other_factory.begin() as db:
            db.add(User(name="Outra Empresa", email="admin@test.pt", password_hash=password, role="administrador"))
        def other_db():
            with other_factory() as db:
                yield db
        app.dependency_overrides[db_session] = other_db
        assert client.post("/api/auth/login", json={"email": "admin@test.pt", "password": "PasswordTest123!"}).status_code == 200
        assert client.post("/api/liquidity/forecast", json={"targets": ["bank:" + bank]}).status_code == 404
        assert client.post("/api/liquidity/forecast", json={"scenario_id": saved["id"]}).status_code == 404
        assert client.get("/api/liquidity/origin/manual:" + row["id"]).status_code == 404
        assert client.post("/api/liquidity/manual/" + row["id"], json=body).status_code == 404
        assert client.post("/api/liquidity/adjust/manual:" + row["id"], json={"reason": "Teste isolamento"}).status_code == 404
        assert client.get("/api/liquidity/setup").json()["manual"] == []
    finally:
        app.dependency_overrides[db_session] = previous
        if other_engine:
            other_engine.dispose()
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS " + schema + " CASCADE"))
