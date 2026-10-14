import json
from uuid import uuid4
from sqlalchemy import select
from app.models import BankTransaction, DocumentPayment, LiquiditySettlement, PlannedMovement, WintouchSaleRecord
from app.liquidity import today
from conftest import open_cash
from test_liquidity import liquidity, forecast


def source(fixture, erp=False):
    _, ids, factory, store, bank = fixture
    with factory.begin() as db:
        if erp:
            row = WintouchSaleRecord(enterprise_id="demo", external_id="offline-doc", store_id=store, kind="previsto", document_date=today().isoformat(), active=True, data=json.dumps({"document": "FT LOCAL", "entity": "Fornecedor Demo", "direction": -1, "amount_cents": 5000, "due_date": today().isoformat(), "occurred_at": today().isoformat() + "T12:00:00"}))
        else:
            row = PlannedMovement(store_id=store, category_id=ids["category"], entity="Fornecedor Demo", document="FT LOCAL", direction=-1, amount_cents=5000, due_date=today().isoformat())
        db.add(row); db.flush()
        return ("wintouch:" if erp else "planned:") + row.id


def payload(fixture, **extra):
    return {"amount_cents": 2000, "target": "bank:" + fixture[4], "payment_date": today().isoformat(), "category_id": fixture[1]["category"], "request_key": uuid4().hex, "reason": "Pagamento confirmado localmente", "confirmed": True, **extra}


def test_offline_erp_partial_bank_payment_adjustment_and_retry(liquidity, monkeypatch):
    client, _, factory, _, bank = liquidity
    monkeypatch.setattr("app.liquidity.erp_status", lambda db: {"active": False, "provider": "none"})
    key = source(liquidity, True)
    assert client.post("/api/liquidity/adjust/" + key, json={"expected_date": today().isoformat(), "target": "bank:" + bank, "reason": "Data confirmada"}).status_code == 200
    data = payload(liquidity)
    response = client.post("/api/liquidity/settle/" + key, json=data)
    assert response.status_code == 200, response.text
    assert client.post("/api/liquidity/settle/" + key, json=data).json()["id"] == response.json()["id"]
    result = forecast(liquidity, targets=["bank:" + bank])
    item = next(e for e in result["scheduled"] if e["key"] == key)
    assert item["amount_cents"] == 3000 and item["paid_cents"] == 2000 and not item["conflict"]
    assert result["groups"]["EUR"]["initial_cents"] == 8000
    assert result["groups"]["EUR"]["final_cents"] == 5000
    with factory() as db:
        payment = db.scalar(select(DocumentPayment))
        assert payment.amount_cents == 2000
        payment_id = payment.id
        assert json.loads(db.get(WintouchSaleRecord, key.partition(":")[2]).data)["amount_cents"] == 5000
        assert len(list(db.scalars(select(LiquiditySettlement)))) == 1
    with factory.begin() as db:
        transaction = BankTransaction(account_id=bank, movement_date=today().isoformat(), value_date=today().isoformat(), description="Pagamento confirmado", amount_cents=-2000, balance_cents=8000, fingerprint=uuid4().hex, source_account="Demo", exported_at=today().isoformat() + " 23:00:00", source_position=0)
        db.add(transaction); db.flush(); transaction_id = transaction.id
    confirmation = client.post("/api/reconciliation/confirm", json={"kind": "pagamento", "lines": [{"source": "bank", "source_id": transaction_id, "amount_cents": 2000}, {"source": "payment", "source_id": payment_id, "amount_cents": 2000}]})
    assert confirmation.status_code == 200, confirmation.text
    assert forecast(liquidity, targets=["bank:" + bank])["groups"]["EUR"]["initial_cents"] == 8000
    monkeypatch.setattr("app.liquidity.erp_status", lambda db: {"active": True, "provider": "wintouch"})
    assert client.post("/api/liquidity/settle/" + key, json=payload(liquidity)).status_code == 409


def test_native_cash_payment_updates_planned_and_real_once(liquidity):
    client, ids, _, _, bank = liquidity
    key = source(liquidity)
    session = open_cash(client, ids["registers"][0], "100").json()["id"]
    response = client.post("/api/liquidity/settle/" + key, json=payload(liquidity, target="cash:" + ids["registers"][0]))
    assert response.status_code == 200, response.text
    native = next(r for r in client.get("/api/planned-movements").json() if "planned:" + r["id"] == key)
    assert native["remaining_cents"] == 3000 and native["paid_cents"] == 2000
    cash = next(r for r in client.get("/api/sessions").json() if r["id"] == session)
    assert cash["balance_cents"] == 8000
    assert len(client.get("/api/movements").json()) == 1
    assert client.post("/api/liquidity/settle/" + key, json=payload(liquidity, amount_cents=4000)).status_code == 422
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.post("/api/liquidity/settle/" + key, json=payload(liquidity)).status_code == 403
