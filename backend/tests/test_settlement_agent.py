from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select
from app.models import DocumentPayment, PlannedMovement, WintouchSaleRecord
from app.liquidity import today
from test_liquidity import liquidity
import json


def seed(fixture):
    _, ids, factory, store, _ = fixture
    with factory.begin() as db:
        for days, entity, amount in [(-20, "Fornecedor A", 3000), (-5, "Fornecedor A", 5000), (5, "Fornecedor A", 2000), (-30, "Fornecedor B", 9000)]:
            db.add(PlannedMovement(store_id=store, category_id=ids["category"], entity=entity, document="FT "+str(days), due_date=(today()+timedelta(days=days)).isoformat(), direction=-1, amount_cents=amount))


def test_overdue_budget_partial_payment_and_idempotent_execution(liquidity):
    client, ids, factory, _, bank = liquidity
    seed(liquidity)
    response = client.post("/api/liquidity/settlement-proposal", json={"entity": "Fornecedor A", "amount_cents": 4500})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total_cents"] == 4500
    assert [r["proposed_cents"] for r in result["lines"]] == [3000, 1500]
    with factory() as db:
        assert not db.scalar(select(DocumentPayment.id))
    for line in result["lines"]:
        payload = {"amount_cents": line["proposed_cents"], "target": "bank:"+bank, "category_id": ids["category"], "payment_date": today().isoformat(), "reason": "Proposta revista e pagamento realizado", "request_key": uuid4().hex, "confirmed": True}
        url = "/api/liquidity/settle/"+line["key"]
        paid = client.post(url, json=payload)
        assert paid.status_code == 200, paid.text
        assert client.post(url, json=payload).json()["id"] == paid.json()["id"]
    remaining = client.post("/api/liquidity/settlement-proposal", json={"entity": "Fornecedor A"}).json()
    assert remaining["total_cents"] == 3500 and len(remaining["lines"]) == 1
    all_dates = client.post("/api/liquidity/settlement-proposal", json={"entity": "Fornecedor A", "overdue_only": False, "amount_cents": 8000}).json()
    assert all_dates["total_cents"] == 5500 and all_dates["unallocated_cents"] == 2500
    with factory() as db:
        assert len(list(db.scalars(select(DocumentPayment)))) == 2


def test_readonly_erp_is_blocked_instead_of_local_fallback_and_permissions(liquidity, monkeypatch):
    client, ids, factory, store, _ = liquidity
    with factory.begin() as db:
        db.add(WintouchSaleRecord(enterprise_id="demo", external_id="pending:test-agent", store_id=store, kind="previsto", document_date=today().isoformat(), data=json.dumps({"entity": "Fornecedor ERP", "document": "FT ERP", "amount_cents": 5000, "direction": -1, "due_date": (today()-timedelta(days=3)).isoformat(), "occurred_at": today().isoformat()+"T12:00:00"})))
    monkeypatch.setattr("app.liquidity.erp_status", lambda db: {"active": True, "provider": "wintouch", "can_settle": False})
    result = client.post("/api/liquidity/settlement-proposal", json={"entity": "Fornecedor ERP"}).json()
    assert result["total_cents"] == 0 and result["lines"][0]["blocked"] and result["lines"][0]["route"] == "erp"
    monkeypatch.setattr("app.liquidity.erp_status", lambda db: {"active": False, "provider": "wintouch"})
    result = client.post("/api/liquidity/settlement-proposal", json={"entity": "Fornecedor ERP"}).json()
    assert result["total_cents"] == 5000 and result["lines"][0]["route"] == "local"
    assert client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.post("/api/liquidity/settlement-proposal", json={}).status_code == 403


def test_bank_sustainability_reserve_partial_no_double_count_and_stale(liquidity):
    from app.models import BankTransaction, LiquiditySettings
    client, ids, factory, store, bank = liquidity
    seed(liquidity)
    proposal = client.post("/api/liquidity/settlement-proposal", json={"entity": "Fornecedor A", "amount_cents": 4500}).json()
    body = {"target": "bank:"+bank, "lines": [{"key": r["key"], "amount_cents": r["proposed_cents"]} for r in proposal["lines"]]}
    # A has 10k and B has 9k: unassigned store outgoings are reserved conservatively.
    result = client.post("/api/liquidity/settlement-sustainability", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["after_cents"] == 5500 and result.json()["other_outgoing_cents"] == 14500
    assert result.json()["minimum_cents"] == -9000 and result.json()["valid"] is False
    with factory.begin() as db:
        db.scalar(select(BankTransaction)).balance_cents = 25000
        db.add(LiquiditySettings(id="default", reserves='{"EUR": 4000}', stale_days=7))
    result = client.post("/api/liquidity/settlement-sustainability", json=body).json()
    assert result["minimum_cents"] == 6000 and result["valid"] is True
    result = client.post("/api/liquidity/settlement-sustainability", json={**body, "reserved_cents": 3000}).json()
    assert result["minimum_cents"] == 3000 and result["shortfall_cents"] == 1000 and not result["valid"]
    with factory.begin() as db:
        db.scalar(select(BankTransaction)).movement_date = (today()-timedelta(days=10)).isoformat()
    assert client.post("/api/liquidity/settlement-sustainability", json=body).json()["valid"] is False
