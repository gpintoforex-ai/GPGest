from decimal import Decimal
from io import BytesIO
import json

import pytest
from app import main
from app.models import (
    Audit,
    BankAccount,
    BankDeposit,
    BusinessEntity,
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


def test_draft_preserves_money_and_close_posts_missing_sales_and_difference(local):
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
        assert db.scalar(select(func.count()).select_from(Movement)) == 3
        assert db.scalar(select(Movement.id).where(Movement.kind == "diferenca_caixa"))
    closed = sheet(c, identifier)
    assert closed["sheet"]["workflow_mode"] == "local"
    assert closed["sheet"]["reconciliation"]["difference_cents"] == 0
    assert closed["count_data"]["closed_by"]
    assert c.get(f"/api/sessions/{identifier}/report").content.startswith(b"%PDF")


def test_physical_drawer_350_counted_340_posts_shortage(local):
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
            json={**payload, "reason": "Falta confirmada de 10 euros", "supplier_answer": "indicado", "supplier_name": "Fornecedor de teste"},
        ).status_code
        == 200
    )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 3
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
        assert db.scalar(select(func.count()).select_from(Movement)) == 4


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
    assert (
        c.post(
            f"/api/sessions/{identifier}/count", json={"realized": {"debits": {"numerario": "50"}}}
        ).status_code
        == 409
    )
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
        assert db.scalar(select(func.count()).select_from(Movement)) == 2
        assert db.scalar(select(Movement.id).where(Movement.kind == "diferenca_caixa"))


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


def test_declared_sales_are_expected_and_close_posts_only_unrecorded_receipts(local):
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
    # Closing posts only the declared sales that have not yet been recorded.
    payload["cash_total"] = "240"
    closed = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert closed.status_code == 200, closed.text
    result = sheet(c, identifier)
    assert result["expected_cents"] == 24000
    assert result["balance_cents"] == 24000
    assert result["count_data"]["ledger_balance_cents"] == 24000
    assert result["sheet"]["reconciliation"]["expectation_basis"] == "declared_sales"
    report = c.get(f"/api/sessions/{identifier}/report").content
    report_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(report)).pages)
    assert "Esperado: 240,00 EUR" in report_text
    assert "Esperado: 50,00 EUR" in report_text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 3


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


def test_manual_debits_only_create_movements_at_close(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    payload = {**declared(numerario="300"), "cash_total": "340"}
    payload["realized"]["debits"] = {"numerario": "50"}
    payload["realized"]["notes"] = "Débitos de teste"
    saved = c.post(f"/api/sessions/{identifier}/reconcile", json=payload)
    assert saved.status_code == 200, saved.text
    result = sheet(c, identifier)
    row = next(r for r in result["sheet"]["reconciliation"]["rows"] if r["key"] == "numerario")
    assert result["count_data"]["realized"]["debits"] == {"numerario": 5000}
    assert row["expected_cents"] == 35000 and row["count_difference_cents"] == -1000
    assert row["registered_debits_cents"] == 0 and row["debits_cents"] == 5000
    assert result["balance_cents"] == 10000
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422
    payload["realized"]["debits"]["numerario"] = "60"
    edited = c.post(
        f"/api/sessions/{identifier}/count", json={"realized": {"debits": {"numerario": "60"}}}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["count_data"]["realized"]["values"]["numerario"] == 30000
    assert edited.json()["count_data"]["realized"]["notes"] == "Débitos de teste"
    assert not sheet(c, identifier)["sheet"]["reconciliation"]["checked"]
    del payload["realized"]["debits"]  # Existing clients do not erase saved debits.
    payload.update(supplier_answer="indicado", supplier_name="Fornecedor manual")
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    result = sheet(c, identifier)
    assert result["expected_cents"] == 34000
    assert result["count_data"]["realized"]["debits"]["numerario"] == 6000
    assert result["count_data"]["debit_supplier"]["debits_cents"] == 6000
    report = c.get(f"/api/sessions/{identifier}/report").content
    report_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(report)).pages)
    assert "Débitos: 60,00 EUR" in report_text
    assert "Fornecedor dos débitos: Fornecedor manual" in report_text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 2


def test_debits_include_recorded_expenses_without_double_counting_refunds(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    for kind, value in (("venda", "300"), ("reembolso", "20"), ("despesa", "50")):
        response = movement(c, ids, identifier, kind=kind, amount=value)
        assert response.status_code == 200, response.text
    payload = {**declared(numerario="280"), "cash_total": "330"}
    payload["realized"]["debits"] = {"numerario": "50"}
    for debit, expected in (("50", 33000), ("0", 38000), (None, 33000)):
        payload["realized"]["debits"]["numerario"] = debit
        response = c.post(f"/api/sessions/{identifier}/count", json=payload)
        assert response.status_code == 200, response.text
        row = next(
            r for r in response.json()["sheet"]["reconciliation"]["rows"] if r["key"] == "numerario"
        )
        assert row["registered_debits_cents"] == 5000
        assert row["expected_cents"] == expected
    payload.update(supplier_answer="indicado", supplier_name="Fornecedor de despesas")
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 3


def test_manual_debits_validation_and_day_scope(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    payload = {"realized": {"debits": {"numerario": "0"}}}
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    assert (
        c.post(f"/api/sessions/{identifier}/sources", json={"sheet_day": "2026-01-01"}).status_code
        == 409
    )
    for invalid in ("-1", "NaN", "1.001", "9999999999", "invalid"):
        payload["realized"]["debits"]["numerario"] = invalid
        assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 422
    assert (
        c.post(
            f"/api/sessions/{identifier}/count", json={"realized": {"debits": {"unknown": "5"}}}
        ).status_code
        == 422
    )
    payload["realized"]["debits"]["numerario"] = None
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    assert (
        c.post(f"/api/sessions/{identifier}/sources", json={"sheet_day": "2026-01-01"}).status_code
        == 200
    )


def test_manual_credits_persist_and_only_post_at_close(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    payload = {**declared(numerario="300"), "cash_total": "370", "payments": {"cartao": "25"}}
    payload["realized"].update(credits={"numerario": "20", "cartao": "25"}, debits={"numerario": "50"})
    saved = c.post(f"/api/sessions/{identifier}/reconcile", json=payload)
    assert saved.status_code == 200, saved.text
    rows = {row["key"]: row for row in sheet(c, identifier)["sheet"]["reconciliation"]["rows"]}
    assert rows["numerario"]["expected_cents"] == 37000
    assert rows["cartao"]["expected_cents"] == 2500
    assert rows["numerario"]["count_difference_cents"] == rows["cartao"]["count_difference_cents"] == 0
    result = sheet(c, identifier)
    assert result["count_data"]["realized"]["credits"] == {"numerario": 2000, "cartao": 2500}
    assert result["balance_cents"] == 10000
    edited = c.post(f"/api/sessions/{identifier}/count", json={"realized": {"credits": {"numerario": "30", "cartao": "25"}}})
    assert edited.status_code == 200, edited.text
    assert not sheet(c, identifier)["sheet"]["reconciliation"]["checked"]
    assert edited.json()["count_data"]["realized"]["values"]["numerario"] == 30000
    assert edited.json()["count_data"]["realized"]["debits"]["numerario"] == 5000
    del payload["realized"]["credits"]  # Older clients preserve credits when omitted.
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422
    closed = c.post(f"/api/sessions/{identifier}/close", json={**payload, "reason": "Crédito adicional por conferir", "supplier_answer": "indicado", "supplier_name": "Fornecedor de teste"})
    assert closed.status_code == 200, closed.text
    assert sheet(c, identifier)["expected_cents"] == 38000
    report = c.get(f"/api/sessions/{identifier}/report").content
    report_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(report)).pages)
    assert "Créditos: 30,00 EUR" in report_text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 5
    difference = next(row for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa")
    assert difference["direction"] == -1 and difference["amount_cents"] == 1000


def test_credits_include_recorded_receipts_once_and_zero_is_explicit(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    for kind, value in (("venda", "300"), ("reforco", "20"), ("despesa", "50")):
        assert movement(c, ids, identifier, kind=kind, amount=value).status_code == 200
    payload = {**declared(numerario="300"), "cash_total": "370"}
    payload["realized"]["debits"] = {"numerario": "50"}
    for credit, expected in (("20", 37000), ("0", 35000), (None, 37000)):
        payload["realized"]["credits"] = {"numerario": credit}
        response = c.post(f"/api/sessions/{identifier}/count", json=payload)
        assert response.status_code == 200, response.text
        row = next(r for r in response.json()["sheet"]["reconciliation"]["rows"] if r["key"] == "numerario")
        assert row["registered_credits_cents"] == 2000
        assert row["expected_cents"] == expected
    payload.update(supplier_answer="indicado", supplier_name="Fornecedor de teste")
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 3


def test_manual_credits_validation_and_day_scope(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    payload = {"realized": {"credits": {"numerario": "0"}}}
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    assert c.post(f"/api/sessions/{identifier}/sources", json={"sheet_day": "2026-01-01"}).status_code == 409
    for invalid in ("-1", "NaN", "1.001", "9999999999", "invalid"):
        payload["realized"]["credits"]["numerario"] = invalid
        assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 422
    assert c.post(f"/api/sessions/{identifier}/count", json={"realized": {"credits": {"unknown": "5"}}}).status_code == 422
    payload["realized"]["credits"]["numerario"] = None
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    assert c.post(f"/api/sessions/{identifier}/sources", json={"sheet_day": "2026-01-01"}).status_code == 200


def test_inactive_base_method_is_excluded_from_new_entries_and_can_be_reactivated(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    result = c.post("/api/cash/payment-methods/mbway/status", json={"active": False})
    assert result.status_code == 200, result.text
    boot = c.get("/api/bootstrap").json()
    assert "mbway" not in boot["payment_methods"]
    assert next(row for row in boot["payment_method_catalog"] if row["id"] == "mbway")["active"] is False
    assert "mbway" not in {row["key"] for row in sheet(c, identifier)["sheet"]["rows"]}
    assert movement(c, ids, identifier, payment="mbway").status_code == 422
    payload = {**declared(), "cash_total": "100"}
    del payload["realized"]["values"]["mbway"]
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    closed_rows = sheet(c, identifier)["sheet"]["rows"]
    assert c.post("/api/cash/payment-methods/mbway/status", json={"active": True}).status_code == 200
    assert "mbway" in c.get("/api/bootstrap").json()["payment_methods"]
    assert sheet(c, identifier)["sheet"]["rows"] == closed_rows
    with factory() as db:
        assert db.get(LocalPaymentMethod, "mbway").active is True
        assert db.scalar(select(func.count()).select_from(Movement)) == 0
        assert db.scalar(select(Audit.id).where(Audit.action == "meio_pagamento_local_inativado"))
        assert db.scalar(select(Audit.id).where(Audit.action == "meio_pagamento_local_ativado"))


def test_inactivation_preserves_custom_method_activity_counts_names_and_closed_snapshot(local):
    c, ids, factory = local
    key = c.post("/api/cash/payment-methods", json={"name": "Vale oferta"}).json()["id"]
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    assert movement(c, ids, identifier, payment=key, amount="5").status_code == 200
    payload = {**declared(), "cash_total": "100", "payments": {key: "5"}}
    payload["realized"]["values"][key] = "5"
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    assert c.post(f"/api/cash/payment-methods/{key}/status", json={"active": False}).status_code == 200
    boot = c.get("/api/bootstrap").json()
    assert key not in boot["payment_methods"]
    assert next(row for row in boot["payment_method_catalog"] if row["id"] == key)["name"] == "Vale oferta"
    current = sheet(c, identifier)
    assert key not in {row["key"] for row in current["sheet"]["rows"]}
    assert key not in {row["key"] for row in current["sheet"]["reconciliation"]["rows"]}
    assert current["sheet"]["reconciliation"]["registered_total_cents"] == 0
    assert current["sheet"]["reconciliation"]["declared_total_cents"] == 0
    assert current["count_data"]["realized"]["values"][key] == current["count_data"]["values"][key] == 500
    assert next(row for row in c.get("/api/movements").json() if row["payment"] == key)["payment_name"] == "Vale oferta"
    assert movement(c, ids, identifier, payment=key).status_code == 422
    assert c.post("/api/cash/payment-methods", json={"name": "vale oferta"}).status_code == 422
    # Reactivating an open sheet restores the saved values, with no new movement.
    assert c.post(f"/api/cash/payment-methods/{key}/status", json={"active": True}).status_code == 200
    row = next(row for row in sheet(c, identifier)["sheet"]["reconciliation"]["rows"] if row["key"] == key)
    assert row["name"] == "Vale oferta" and row["expected_cents"] == row["counted_cents"] == 500
    assert c.post(f"/api/cash/payment-methods/{key}/status", json={"active": False}).status_code == 200
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    before = sheet(c, identifier)["sheet"]["reconciliation"]
    assert c.post(f"/api/cash/payment-methods/{key}/status", json={"active": True}).status_code == 200
    assert sheet(c, identifier)["sheet"]["reconciliation"] == before
    assert c.get(f"/api/sessions/{identifier}/report").content.startswith(b"%PDF")
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 1


def test_inactive_methods_are_excluded_but_keep_saved_zeros_counts_debits_and_credits(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0]).json()["id"]
    payload = {
        "realized": {"values": {"cartao": "0"}, "debits": {"mbway": "0"}, "credits": {"transferencia": "20"}},
        "payments": {"outro": "0"}, "cash_total": "100",
    }
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    for key in ("cartao", "mbway", "transferencia", "outro"):
        assert c.post(f"/api/cash/payment-methods/{key}/status", json={"active": False}).status_code == 200
    rows = sheet(c, identifier)["sheet"]["reconciliation"]["rows"]
    assert [row["key"] for row in rows] == ["numerario"]
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    saved = sheet(c, identifier)["count_data"]
    assert saved["realized"]["values"]["cartao"] == 0
    assert saved["realized"]["debits"]["mbway"] == 0
    assert saved["realized"]["credits"]["transferencia"] == 2000
    assert saved["values"]["outro"] == 0
    other = open_cash(c, ids["registers"][1]).json()["id"]
    assert [row["key"] for row in sheet(c, other)["sheet"]["rows"]] == ["numerario"]


def test_inactive_method_debits_differences_and_stale_fields_do_not_block_close(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    assert movement(c, ids, identifier, payment="cartao", amount="50").status_code == 200
    assert movement(c, ids, identifier, kind="despesa", payment="cartao", amount="20").status_code == 200
    payload = {**declared(cartao="50"), "cash_total": "100", "payments": {"cartao": "10"}}
    payload["realized"].update(debits={"cartao": "25"}, credits={"cartao": "5"})
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    assert c.post("/api/cash/payment-methods/cartao/status", json={"active": False}).status_code == 200
    rec = sheet(c, identifier)["sheet"]["reconciliation"]
    assert "cartao" not in {row["key"] for row in rec["rows"]}
    assert rec["complete"] and rec["declared_total_cents"] == rec["registered_total_cents"] == 0
    # A stale browser cannot overwrite inactive history or require a supplier/reason.
    payload["realized"]["values"]["cartao"] = "999"
    payload["realized"]["debits"]["cartao"] = "999"
    payload["realized"]["credits"]["cartao"] = "999"
    payload["payments"]["cartao"] = "999"
    result = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert result.status_code == 200, result.text
    closed = sheet(c, identifier)
    saved = closed["count_data"]
    assert saved["realized"]["values"]["cartao"] == 5000
    assert saved["realized"]["debits"]["cartao"] == 2500
    assert saved["realized"]["credits"]["cartao"] == 500
    assert saved["values"]["cartao"] == 1000
    assert "debit_supplier" not in saved
    assert closed["expected_cents"] == closed["counted_cents"] == 10000
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 2
    assert c.post("/api/cash/payment-methods/cartao/status", json={"active": True}).status_code == 200
    assert sheet(c, identifier)["sheet"]["reconciliation"] == closed["sheet"]["reconciliation"]


def test_inactivation_does_not_change_existing_closed_sheet_or_pdf(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    assert movement(c, ids, identifier, payment="cartao", amount="50").status_code == 200
    payload = {**declared(cartao="50"), "cash_total": "100", "payments": {"cartao": "50"}}
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    before = sheet(c, identifier)
    assert c.post("/api/cash/payment-methods/cartao/status", json={"active": False}).status_code == 200
    after = sheet(c, identifier)
    assert after["sheet"] == before["sheet"]
    assert after["count_data"] == before["count_data"]
    report = c.get(f"/api/sessions/{identifier}/report").content
    report_text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(report)).pages)
    assert "Multibanco / TPA - Faturado: 50,00 EUR" in report_text


def test_payment_method_status_permissions_required_cash_and_validation(local):
    c, _, _ = local
    assert c.post("/api/cash/payment-methods/numerario/status", json={"active": False}).status_code == 422
    assert c.post("/api/cash/payment-methods/unknown/status", json={"active": False}).status_code == 404
    assert c.post("/api/cash/payment-methods/mbway/status", json={"active": "false"}).status_code == 422
    assert c.post("/api/cash/payment-methods/mbway/status", json={}).status_code == 422
    c.post("/api/auth/logout", json={})
    for email in ("operator@test.pt", "reader@test.pt"):
        assert c.post("/api/auth/login", json={"email": email, "password": "PasswordTest123!"}).status_code == 200
        assert c.post("/api/cash/payment-methods/mbway/status", json={"active": False}).status_code == 403
        c.post("/api/auth/logout", json={})


def test_integrated_mode_does_not_allow_local_method_inactivation(setup):
    c, _, _ = setup
    assert c.post("/api/cash/payment-methods/cartao/status", json={"active": False}).status_code == 409
    assert c.get("/api/bootstrap").json()["payment_method_catalog"] is None


@pytest.mark.parametrize("payment", ["numerario", "cartao"])
def test_manual_debits_require_supplier_even_when_credits_cancel_them(local, payment):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    payload = {**declared(), "cash_total": "100", "payments": {"cartao": "0"}}
    payload["realized"].update(debits={payment: "10"}, credits={payment: "10"})
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    for answer in (None, "desconhecido", "nao_aplicavel"):
        response = c.post(f"/api/sessions/{identifier}/close", json={**payload, "supplier_answer": answer})
        assert response.status_code == 422 and "fornecedor" in response.json()["detail"]
    with factory.begin() as db:
        assert db.get(CashSession, identifier).closed_at is None
        supplier = BusinessEntity(name="Fornecedor local", tax_number="500000000", is_supplier=True)
        inactive = BusinessEntity(name="Inativo", is_supplier=True, active=False)
        customer = BusinessEntity(name="Cliente", is_supplier=False, is_customer=True)
        db.add_all([supplier, inactive, customer])
        db.flush()
        supplier_id = "local:" + supplier.id
        invalid_ids = ["local:" + inactive.id, "local:" + customer.id, "local:inexistente"]
    for invalid_id in invalid_ids:
        assert c.post(f"/api/sessions/{identifier}/close", json={
            **payload, "supplier_answer": "indicado", "supplier_id": invalid_id,
        }).status_code == 404
    response = c.post(f"/api/sessions/{identifier}/close", json={
        **payload, "supplier_answer": "indicado", "supplier_id": supplier_id, "supplier_name": "Ignorar este nome",
    })
    assert response.status_code == 200, response.text
    saved = sheet(c, identifier)["count_data"]
    linked = saved["debit_supplier"]
    assert linked["id"] == supplier_id and linked["name"] == "Fornecedor local"
    assert linked["debits_cents"] == 1000 and linked["tax_number"] == "500000000"
    assert saved["closed_by"] == saved["updated_by"] and saved["updated_at"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 2
        event = db.scalar(select(Audit).where(Audit.target_id == identifier, Audit.action == "fecho"))
        assert json.loads(event.detail)["debit_supplier"] == linked
    assert c.post(f"/api/sessions/{identifier}/reopen", json={"reason": "Confirmar fornecedor novamente"}).status_code == 200
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 422
    payload["realized"]["debits"][payment] = "0"
    payload["realized"]["credits"][payment] = "0"
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    assert "debit_supplier" not in sheet(c, identifier)["count_data"]


def test_local_supplier_association_stays_in_current_register_and_active_methods(local):
    c, ids, factory = local
    a = open_cash(c, ids["registers"][0], "100").json()["id"]
    b = open_cash(c, ids["registers"][1], "100").json()["id"]
    assert movement(c, ids, a, kind="despesa", amount="10").status_code == 200
    assert movement(c, ids, a, kind="despesa", payment="cartao", amount="20").status_code == 200
    assert movement(c, ids, b, kind="despesa", amount="20").status_code == 200
    assert c.post("/api/cash/payment-methods/cartao/status", json={"active": False}).status_code == 200
    result = c.post(f"/api/sessions/{a}/close", json={
        **declared(), "cash_total": "90", "supplier_answer": "indicado", "supplier_name": "Fornecedor caixa A",
    })
    assert result.status_code == 200, result.text
    with factory() as db:
        assert db.scalar(select(Movement).where(Movement.session_id == a, Movement.payment == "numerario")).entity == "Fornecedor caixa A"
        assert not db.scalar(select(Movement).where(Movement.session_id == a, Movement.payment == "cartao")).entity
        assert not db.scalar(select(Movement).where(Movement.session_id == b)).entity
        assert db.scalar(select(func.count()).select_from(Movement)) == 3


def test_explicit_zero_debits_override_does_not_require_supplier(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    assert movement(c, ids, identifier, kind="despesa", amount="10").status_code == 200
    payload = {**declared(), "cash_total": "100"}
    payload["realized"]["debits"] = {"numerario": "0"}
    result = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert result.status_code == 200, result.text
    assert "debit_supplier" not in sheet(c, identifier)["count_data"]
