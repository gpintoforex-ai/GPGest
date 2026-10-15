from decimal import Decimal
from io import BytesIO

import pytest
from app import main
from app.models import (
    Audit,
    BankAccount,
    BankDeposit,
    CashSession,
    LocalPaymentMethod,
    Movement,
    Register,
    now,
)
from conftest import movement, open_cash
from pypdf import PdfReader
from sqlalchemy import func, select


@pytest.fixture
def local(setup, monkeypatch):
    original = main.integration_policy
    monkeypatch.setattr(main, "integration_policy", lambda db: {**original(db), "mode": "local"})
    return setup


def declared(**values):
    return {
        "realized": {
            "values": {
                key: values.get(key, "0")
                for key in ("numerario", "cartao", "mbway", "transferencia", "outro")
            }
        }
    }


def sheet(client, identifier):
    return next(row for row in client.get("/api/sessions").json() if row["id"] == identifier)


def test_declared_1000_registered_980_persists_without_new_money(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    assert movement(c, ids, identifier, payment="cartao", amount="980").status_code == 200
    before = sheet(c, identifier)["balance_cents"]
    payload = {**declared(cartao="1000"), "cash_total": "100", "payments": {"cartao": "980"}}
    for value in ("1000", "1100", "1000"):
        payload["realized"]["values"]["cartao"] = value
        result = c.post(f"/api/sessions/{identifier}/count", json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["balance_cents"] == before
    result = sheet(c, identifier)
    rec = result["sheet"]["reconciliation"]
    assert rec["declared_total_cents"] == 100000 and rec["registered_total_cents"] == 98000
    assert rec["difference_cents"] == -2000
    card = next(row for row in rec["rows"] if row["key"] == "cartao")
    assert card["expected_cents"] == 100000 and card["count_difference_cents"] == -2000
    assert result["count_data"]["realized"]["updated_by"]
    assert result["count_data"]["realized"]["updated_at"]
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422
    assert (
        c.post(
            f"/api/sessions/{identifier}/close",
            json={**payload, "reason": "Recebimento de 20 euros por identificar"},
        ).status_code
        == 200
    )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 1
        assert not db.scalar(select(Movement.id).where(Movement.kind == "diferenca_caixa"))
    closed = sheet(c, identifier)
    assert closed["sheet"]["workflow_mode"] == "local"
    assert closed["sheet"]["reconciliation"]["difference_cents"] == -2000
    assert closed["count_data"]["closed_by"]
    assert c.get(f"/api/sessions/{identifier}/report").content.startswith(b"%PDF")


def test_physical_drawer_350_counted_340_and_no_adjustment(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    movement(c, ids, identifier, amount="300")
    movement(c, ids, identifier, kind="despesa", amount="50")
    payload = {**declared(numerario="300"), "cash_total": "340"}
    result = c.post(f"/api/sessions/{identifier}/reconcile", json=payload)
    assert result.status_code == 200, result.text
    rows = {r["key"]: r for r in result.json()["sheet"]["rows"]}
    assert rows["numerario"]["expected_cents"] == 35000
    assert (
        result.json()["count_data"]["values"]["numerario"] - rows["numerario"]["expected_cents"]
        == -1000
    )
    assert result.json()["sheet"]["reconciliation"]["difference_cents"] == 0
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422
    assert (
        c.post(
            f"/api/sessions/{identifier}/close",
            json={**payload, "reason": "Falta confirmada de 10 euros"},
        ).status_code
        == 200
    )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 2
        assert db.scalar(select(Audit.id).where(Audit.action == "conferencia_folha_caixa"))
    assert (
        c.post(
            f"/api/sessions/{identifier}/reopen", json={"reason": "Recontar dinheiro da gaveta"}
        ).status_code
        == 200
    )
    reopened = sheet(c, identifier)
    assert reopened["count_data"]["realized"]["values"]["numerario"] == 30000
    assert not reopened["sheet"]["reconciliation"]["checked"]
    assert reopened["count_data"]["reopen_reason"] == "Recontar dinheiro da gaveta"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 2


def test_blank_and_zero_confirmed_are_distinct(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0], "0").json()["id"]
    initial = sheet(c, identifier)["sheet"]["reconciliation"]
    assert not initial["complete"] and all(r["declared_cents"] is None for r in initial["rows"])
    assert (
        c.post(
            f"/api/sessions/{identifier}/count",
            json={"realized": {"values": {"numerario": "0", "cartao": None}}},
        ).status_code
        == 200
    )
    assert not sheet(c, identifier)["sheet"]["reconciliation"]["complete"]
    assert (
        c.post(
            f"/api/sessions/{identifier}/close",
            json={**declared(), "cash_mode": "denominations", "counts": {}},
        ).status_code
        == 422
    )
    assert c.post(f"/api/sessions/{identifier}/close", json={"cash_total": "0"}).status_code == 422
    response = c.post(
        f"/api/sessions/{identifier}/reconcile", json={**declared(), "cash_total": "0"}
    )
    assert response.status_code == 200, response.text
    checked = sheet(c, identifier)["sheet"]["reconciliation"]["rows"]
    assert next(row for row in checked if row["key"] == "numerario")["status"] == "Conferido"
    assert all(row["status"] == "Por conferir" for row in checked if row["key"] != "numerario")
    assert (
        c.post(
            f"/api/sessions/{identifier}/close",
            json={**declared(), "counts": {"1": 0}, "cash_mode": "denominations"},
        ).status_code
        == 200
    )


def test_only_receipts_of_this_register_day_and_movement_changes_invalidate_check(local):
    c, ids, _ = local
    a = open_cash(c, ids["registers"][0]).json()["id"]
    b = open_cash(c, ids["registers"][1]).json()["id"]
    movement(c, ids, a, amount="300")
    movement(c, ids, a, kind="entrada_extra", amount="20")
    movement(c, ids, a, kind="reforco", amount="10")
    movement(c, ids, a, kind="deposito", amount="50")
    movement(c, ids, b, amount="900")
    transfer = c.post(
        "/api/transfers",
        json={
            "source_id": a,
            "destination_id": b,
            "amount": "30",
            "category_id": ids["category"],
            "reason": "Transferência interna",
            "request_key": "local-transfer-test-123",
        },
    )
    assert transfer.status_code == 200, transfer.text
    payload = {**declared(numerario="300"), "cash_total": "350"}
    response = c.post(f"/api/sessions/{a}/reconcile", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["sheet"]["reconciliation"]["registered_total_cents"] == 30000
    assert result["sheet"]["reconciliation"]["checked"]
    assert (
        next(r for r in result["sheet"]["rows"] if r["key"] == "numerario")["expected_cents"]
        == 35000
    )
    movement(c, ids, a, amount="1")
    rec = sheet(c, a)["sheet"]["reconciliation"]
    assert not rec["checked"] and rec["difference_cents"] == 100


def test_mbway_custom_methods_validation_and_no_silent_day_change(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    assert movement(c, ids, identifier, payment="mbway", amount="20").status_code == 200
    method = c.post("/api/cash/payment-methods", json={"name": "Vale oferta"})
    assert method.status_code == 200, method.text
    key = method.json()["id"]
    assert key in c.get("/api/bootstrap").json()["payment_methods"]
    assert movement(c, ids, identifier, payment=key, amount="5").status_code == 200
    assert movement(c, ids, identifier, payment="unknown").status_code == 422
    assert c.post("/api/cash/payment-methods", json={"name": "vale oferta"}).status_code == 422
    payload = {**declared(mbway="20"), "cash_total": "100"}
    payload["realized"]["values"][key] = "5"
    assert c.post(f"/api/sessions/{identifier}/reconcile", json=payload).status_code == 200
    assert (
        c.post(f"/api/sessions/{identifier}/sources", json={"sheet_day": "2026-01-01"}).status_code
        == 409
    )
    assert sheet(c, identifier)["sheet"]["reconciliation"]["registered_total_cents"] == 2500
    for invalid in ("NaN", "1.001", "9999999999", "a"):
        payload["realized"]["values"][key] = invalid
        assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 422


def test_row_differences_cannot_cancel_without_justification(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    movement(c, ids, identifier, amount="120")
    movement(c, ids, identifier, payment="cartao", amount="80")
    payload = {
        **declared(numerario="100", cartao="100"),
        "cash_total": "220",
        "payments": {"cartao": "80"},
    }
    assert (
        c.post(f"/api/sessions/{identifier}/count", json=payload).json()["sheet"]["reconciliation"][
            "difference_cents"
        ]
        == 0
    )
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422


def test_local_permissions_reopening_and_closed_snapshot(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    payload = {**declared(), "cash_total": "100"}
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    with factory.begin() as db:
        db.add(LocalPaymentMethod(id="pm_new", name="Novo meio"))
    assert len(sheet(c, identifier)["sheet"]["reconciliation"]["rows"]) == 5
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 409
    c.post("/api/auth/logout", json={})
    c.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"})
    assert (
        c.post(f"/api/sessions/{identifier}/reopen", json={"reason": "Recontar caixa"}).status_code
        == 403
    )
    assert c.post("/api/cash/payment-methods", json={"name": "Outro novo"}).status_code == 403


def test_integrated_declarations_rejected_and_current_close_preserved(setup):
    c, ids, _ = setup
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    assert c.post(f"/api/sessions/{identifier}/count", json=declared()).status_code == 409
    assert c.post(f"/api/sessions/{identifier}/reconcile", json=declared()).status_code == 409
    assert (
        c.post(
            f"/api/sessions/{identifier}/close",
            json={"cash_total": "105", "reason": "Sobra confirmada"},
        ).status_code
        == 200
    )
    assert any(row["kind"] == "diferenca_caixa" for row in c.get("/api/movements").json())


def test_receipts_only_edit_preserves_count(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    assert (
        c.post(
            f"/api/sessions/{identifier}/count", json={**declared(), "cash_total": "100"}
        ).status_code
        == 200
    )
    saved = c.post(f"/api/sessions/{identifier}/count", json=declared(cartao="20"))
    assert saved.status_code == 200 and saved.json()["count_data"]["values"]["numerario"] == 10000


def test_other_payment_counts_persist_and_require_difference_reason(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    assert movement(c, ids, identifier, payment="cartao", amount="50").status_code == 200
    payload = {**declared(cartao="50"), "cash_total": "40", "payments": {"cartao": "45"}}
    saved = c.post(f"/api/sessions/{identifier}/count", json=payload)
    assert saved.status_code == 200, saved.text
    assert sheet(c, identifier)["count_data"]["values"] == {"numerario": 4000, "cartao": 4500}
    assert sheet(c, identifier)["sheet"]["reconciliation"]["difference_cents"] == 0
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422
    closed = c.post(
        f"/api/sessions/{identifier}/close",
        json={**payload, "reason": "Diferença de cinco euros no terminal"},
    )
    assert closed.status_code == 200, closed.text
    assert sheet(c, identifier)["count_data"]["values"]["cartao"] == 4500
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 1
        assert not db.scalar(select(Movement.id).where(Movement.kind == "diferenca_caixa"))


def test_deposit_without_session_only_reduces_drawer_after_cash_exit(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    movement(c, ids, identifier, amount="300")
    with factory.begin() as db:
        session = db.get(CashSession, identifier)
        register = db.get(Register, session.register_id)
        account = BankAccount(
            store_id=register.store_id, name="Banco de teste", bank="Banco", iban_last4="0000"
        )
        db.add(account)
        db.flush()
        deposit = BankDeposit(
            number="DEP-TEST",
            request_key="deposit-test-without-session",
            store_id=register.store_id,
            register_id=register.id,
            account_id=account.id,
            user_id=session.operator_id,
            preparation_date=session.sheet_day,
            cash_amount=Decimal("50"),
        )
        db.add(deposit)
        db.flush()
        deposit_id = deposit.id
    assert sheet(c, identifier)["balance_cents"] == 40000
    with factory.begin() as db:
        deposit = db.get(BankDeposit, deposit_id)
        exit_movement = Movement(
            session_id=None,
            user_id=deposit.user_id,
            category_id=ids["category"],
            kind="deposito",
            payment="numerario",
            amount_cents=5000,
            direction=-1,
            occurred_at=now(),
            origin="deposito_modulo",
            request_key="deposit-exit-test",
        )
        db.add(exit_movement)
        db.flush()
        deposit.movement_id = exit_movement.id
        deposit.status = "em_transito"
    result = sheet(c, identifier)
    assert result["balance_cents"] == 35000
    assert result["sheet"]["reconciliation"]["registered_total_cents"] == 30000


def test_declared_sales_are_expected_without_duplicate_receipts_or_ledger_changes(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    movement(c, ids, identifier, amount="10")
    payload = {
        **declared(numerario="200", cartao="50"),
        "cash_total": "50",
        "payments": {"cartao": "50"},
    }
    saved = c.post(f"/api/sessions/{identifier}/reconcile", json=payload)
    assert saved.status_code == 200, saved.text
    result = sheet(c, identifier)
    rows = {row["key"]: row for row in result["sheet"]["reconciliation"]["rows"]}
    assert rows["numerario"]["expected_cents"] == 24000
    assert rows["numerario"]["count_difference_cents"] == -19000
    assert rows["cartao"]["expected_cents"] == 5000
    assert rows["cartao"]["count_difference_cents"] == 0
    assert result["balance_cents"] == 5000
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422
    # Correct cash count closes without requiring fictitious sale movements.
    payload["cash_total"] = "240"
    closed = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert closed.status_code == 200, closed.text
    result = sheet(c, identifier)
    assert result["expected_cents"] == 24000
    assert result["balance_cents"] == 5000
    assert result["count_data"]["ledger_balance_cents"] == 5000
    assert result["sheet"]["reconciliation"]["expectation_basis"] == "declared_sales"
    report = c.get(f"/api/sessions/{identifier}/report").content
    report_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(report)).pages)
    assert "Esperado: 240,00 EUR" in report_text
    assert "Esperado: 50,00 EUR" in report_text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 1


def test_declared_replaces_receipts_but_preserves_expenses_deposits_and_pending(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    movement(c, ids, identifier, amount="300")
    movement(c, ids, identifier, kind="despesa", amount="50")
    movement(c, ids, identifier, kind="deposito", amount="20")
    payload = {**declared(numerario="400"), "cash_total": "430"}
    response = c.post(f"/api/sessions/{identifier}/reconcile", json=payload)
    assert response.status_code == 200, response.text
    row = next(
        r for r in response.json()["sheet"]["reconciliation"]["rows"] if r["key"] == "numerario"
    )
    assert row["other_movements_cents"] == -7000
    assert row["expected_cents"] == 43000 and row["count_difference_cents"] == 0
    payload["realized"]["values"]["numerario"] = None
    response = c.post(f"/api/sessions/{identifier}/count", json=payload)
    row = next(
        r for r in response.json()["sheet"]["reconciliation"]["rows"] if r["key"] == "numerario"
    )
    assert row["expected_cents"] is None and row["count_difference_cents"] is None
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422


def test_previous_closed_snapshot_is_not_recalculated(local):
    import json

    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    payload = {**declared(numerario="200"), "cash_total": "240"}
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    with factory.begin() as db:
        session = db.get(CashSession, identifier)
        stored = json.loads(session.count_data)
        snapshot = stored["local_reconciliation"]
        snapshot.pop("expectation_basis")
        for row in snapshot["rows"]:
            for key in (
                "expected_cents",
                "counted_cents",
                "count_difference_cents",
                "fund_cents",
                "other_movements_cents",
            ):
                row.pop(key)
        session.count_data = json.dumps(stored)
        session.expected_cents = 4000
    result = sheet(c, identifier)
    assert result["sheet"]["reconciliation"] == snapshot
    assert result["expected_cents"] == 4000
    assert c.get(f"/api/sessions/{identifier}/report").content.startswith(b"%PDF")
