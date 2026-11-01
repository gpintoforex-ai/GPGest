"""Local count differences are actual closing entries, with reversible audit history."""

import pytest
from app.models import Category, Movement
from conftest import open_cash
from sqlalchemy import select
from test_cash_local import declared, sheet
from test_cash_local import local as local


@pytest.mark.parametrize("counted,delta", [("95", -500), ("105", 500), ("0", -10000)])
def test_local_difference_posts_actual_money_and_preserves_conference(local, counted, delta):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "0").json()["id"]
    payload = {
        **declared(numerario="100"),
        "cash_total": counted,
        "reason": "Diferença física confirmada",
    }
    assert c.post(f"/api/sessions/{identifier}/count", json=payload).status_code == 200
    assert c.get("/api/movements").json() == []
    result = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert result.status_code == 200, result.text
    rows = c.get("/api/movements").json()
    difference = next(row for row in rows if row["kind"] == "diferenca_caixa")
    assert difference["direction"] * difference["amount_cents"] == delta
    assert difference["payment"] == "numerario" and difference["reference"] == identifier
    assert difference["origin"] == "fecho_local" and payload["reason"] in difference["notes"]
    saved = sheet(c, identifier)
    conference = next(
        row for row in saved["sheet"]["reconciliation"]["rows"] if row["key"] == "numerario"
    )
    assert conference["expected_cents"] == saved["expected_cents"] == 10000
    assert conference["count_difference_cents"] == delta
    assert saved["balance_cents"] == 10000 + delta
    with factory() as db:
        assert db.get(Category, difference["category_id"]).cash_difference
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 409
    assert len(c.get("/api/movements").json()) == 2
    assert (
        c.post(
            f"/api/sessions/{identifier}/reopen", json={"reason": "Recontar antes de fechar"}
        ).status_code
        == 200
    )
    assert c.get("/api/movements").json() == []
    with factory() as db:
        original = db.get(Movement, difference["id"])
        reversal = db.get(Movement, original.reversed_by)
        assert reversal.reversal_of == original.id
        assert (
            original.direction * original.amount_cents + reversal.direction * reversal.amount_cents
            == 0
        )
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    assert (
        len([row for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa"])
        == 1
    )


def test_differences_per_method_do_not_cancel_each_other(local):
    c, ids, _ = local
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    payload = {
        **declared(cartao="50", mbway="20"),
        "cash_total": "40",
        "payments": {"cartao": "45", "mbway": "25"},
        "reason": "Diferenças verificadas por meio",
    }
    result = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert result.status_code == 200, result.text
    differences = [
        row for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa"
    ]
    assert {(row["payment"], row["direction"] * row["amount_cents"]) for row in differences} == {
        ("cartao", -500),
        ("mbway", 500),
    }
    assert sheet(c, identifier)["balance_cents"] == 4000
    assert (
        c.post("/api/cash/payment-methods/cartao/status", json={"active": False}).status_code == 200
    )
    assert (
        c.post(
            f"/api/sessions/{identifier}/reopen", json={"reason": "Rever diferenças dos meios"}
        ).status_code
        == 200
    )
    assert not any(row["kind"] == "diferenca_caixa" for row in c.get("/api/movements").json())


def test_subsequent_close_only_posts_remaining_day_difference(local):
    c, ids, _ = local
    payload = {
        **declared(numerario="100"),
        "cash_total": "95",
        "reason": "Falta confirmada de cinco euros",
    }
    for count in ("95", "95", "97"):
        identifier = open_cash(c, ids["registers"][0], "0").json()["id"]
        payload["cash_total"] = count
        result = c.post(f"/api/sessions/{identifier}/close", json=payload)
        assert result.status_code == 200, result.text
        assert sheet(c, identifier)["balance_cents"] == int(count) * 100
    rows = [row for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa"]
    assert len(rows) == 2
    assert sum(row["direction"] * row["amount_cents"] for row in rows) == -300
    saved = sheet(c, identifier)
    assert saved["count_data"]["posted_movements"]["differences"][0]["delta_cents"] == 200


def test_reconciled_difference_blocks_all_reopening(local, monkeypatch):
    from app import reconciliation

    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "0").json()["id"]
    payload = {
        **declared(numerario="100"),
        "cash_total": "95",
        "reason": "Falta confirmada de cinco euros",
    }
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    target = next(
        row["id"] for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa"
    )
    monkeypatch.setattr(
        reconciliation, "used", lambda db, kind, identifier: 1 if identifier == target else 0
    )
    response = c.post(
        f"/api/sessions/{identifier}/reopen", json={"reason": "Rever fecho reconciliado"}
    )
    assert response.status_code == 409
    assert sheet(c, identifier)["closed_at"]
    with factory() as db:
        assert all(not row.reversed_by for row in db.scalars(select(Movement)))


def test_difference_keeps_default_bank_and_sheet_date(local):
    from app.models import LocalPaymentMethod, PaymentMethodBankDefault, Register

    c, ids, factory = local
    with factory() as db:
        store_id = db.get(Register, ids["registers"][0]).store_id
    account = c.post(
        "/api/accounts",
        json={
            "store_id": store_id,
            "name": "Conta de teste",
            "bank": "Banco fictício",
            "iban_last4": "1234",
        },
    ).json()
    with factory.begin() as db:
        if not db.get(LocalPaymentMethod, "cartao"):
            db.add(LocalPaymentMethod(id="cartao", name="Multibanco / TPA"))
            db.flush()
        db.add(
            PaymentMethodBankDefault(
                method_id="cartao", store_id=store_id, account_id=account["id"]
            )
        )
    identifier = c.post(
        "/api/sessions",
        json={"register_id": ids["registers"][0], "opening": "40", "sheet_day": "2026-10-02"},
    ).json()["id"]
    payload = {
        **declared(cartao="100"),
        "cash_total": "40",
        "payments": {"cartao": "95"},
        "reason": "Falta confirmada no terminal",
    }
    response = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert response.status_code == 200, response.text
    difference = next(
        row for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa"
    )
    assert difference["bank_account_id"] == account["id"]
    assert difference["occurred_at"].startswith("2026-10-02")
    assert difference["direction"] == -1 and difference["amount_cents"] == 500


def test_custom_payment_difference_is_posted(local):
    c, ids, _ = local
    result = c.post("/api/cash/payment-methods", json={"name": "Vale de teste"})
    assert result.status_code == 200, result.text
    method = result.json()["id"]
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    payload = {
        **declared(),
        "cash_total": "40",
        "payments": {method: "18"},
        "reason": "Diferença confirmada nos vales",
    }
    payload["realized"]["values"][method] = "20"
    response = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert response.status_code == 200, response.text
    difference = next(
        row for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa"
    )
    assert (
        difference["payment"] == method
        and difference["amount_cents"] == 200
        and difference["direction"] == -1
    )
