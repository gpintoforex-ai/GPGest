import importlib
import json
import re

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import Audit, BankAccount, CashSession, Movement, Register, User
from conftest import movement, open_cash
from sqlalchemy import func, inspect, select, text
from test_cash_local import declared, local, sheet
from test_platform import create, enter, platform


def active(rows):
    return [row for row in rows if not row["reversed_by"] and not row["reversal_of"]]


def test_close_posts_only_manual_totals_and_keeps_count_difference(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    payload = {**declared(numerario="300", cartao="200", mbway="50", transferencia="100"),
               "cash_total": "340", "payments": {"cartao": "195", "mbway": "50", "transferencia": "100"},
               "reason": "Falta física de dez euros", "supplier_answer": "indicado", "supplier_name": "Fornecedor XPTO"}
    payload["realized"].update(debits={"numerario": "50", "cartao": "5"}, credits={"numerario": "0"})
    assert c.post(f"/api/sessions/{identifier}/count", json=payload | {"supplier_answer": None, "supplier_name": ""}).status_code == 422  # Close-only fields are never accepted by a draft.
    draft = {key: value for key, value in payload.items() if not key.startswith("supplier_")}
    assert c.post(f"/api/sessions/{identifier}/count", json=draft).status_code == 200
    assert c.post(f"/api/sessions/{identifier}/reconcile", json=draft).status_code == 200
    assert c.get("/api/movements").json() == []
    closed = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert closed.status_code == 200, closed.text
    rows = active(c.get("/api/movements?all_records=true").json())
    assert len(rows) == 6 and all(row["origin"] == "fecho_local" for row in rows)
    assert sum(row["direction"] * row["amount_cents"] for row in rows) == 59500
    assert {row["entity"] for row in rows if row["kind"] == "fornecedor"} == {"Fornecedor XPTO"}
    closed_sheet = sheet(c, identifier)
    cash = next(row for row in closed_sheet["sheet"]["reconciliation"]["rows"] if row["key"] == "numerario")
    assert cash["expected_cents"] == 35000 and cash["counted_cents"] == 34000 and cash["count_difference_cents"] == -1000
    assert closed_sheet["balance_cents"] == closed_sheet["count_data"]["ledger_balance_cents"] == 35000
    assert closed_sheet["sheet"]["reconciliation"]["checked"] is True
    assert len(closed_sheet["count_data"]["posted_movements"]["ids"]) == 6
    assert not any(row["kind"] == "diferenca_caixa" for row in rows)
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 409
    assert len(c.get("/api/movements?all_records=true").json()) == 6
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "movimento_fecho_local")) == 6


def test_partial_existing_sales_debits_credits_are_not_duplicated(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    for kind, payment, amount in (("venda", "cartao", "980"), ("venda", "numerario", "300"), ("reembolso", "numerario", "20"), ("despesa", "numerario", "30"), ("entrada_extra", "numerario", "5")):
        assert movement(c, ids, identifier, kind=kind, payment=payment, amount=amount).status_code == 200
    payload = {**declared(numerario="280", cartao="1000"), "cash_total": "340", "payments": {"cartao": "980"},
               "reason": "Cartão por conferir no terminal", "supplier_answer": "indicado", "supplier_name": "Fornecedor"}
    payload["realized"].update(debits={"numerario": "50"}, credits={"numerario": "10"})
    result = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert result.status_code == 200, result.text
    posted = [row for row in c.get("/api/movements?all_records=true").json() if row["origin"] == "fecho_local"]
    assert {(row["kind"], row["payment"], row["amount_cents"]) for row in posted} == {
        ("venda", "cartao", 2000), ("entrada_extra", "numerario", 500), ("fornecedor", "numerario", 2000)}
    assert not any(row["kind"] == "diferenca_caixa" for row in posted)
    assert sheet(c, identifier)["expected_cents"] == 34000


def test_bank_defaults_are_captured_and_reopen_reclose_keeps_auditable_totals(local):
    c, ids, factory = local
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    accounts = [c.post("/api/accounts", json={"store_id": store, "name": f"Conta {i}", "bank": f"Banco {i}", "iban_last4": f"000{i}"}).json() for i in (1, 2)]
    # The route uses its own policy import; real tenant policy is tested below.
    with factory.begin() as db:
        from app.models import LocalPaymentMethod, PaymentMethodBankDefault
        db.add(LocalPaymentMethod(id="cartao", name="Multibanco / TPA"))
        db.flush()
        db.add(PaymentMethodBankDefault(method_id="cartao", store_id=store, account_id=accounts[0]["id"]))
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    payload = {**declared(cartao="200"), "cash_total": "40", "payments": {"cartao": "200"}}
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    original = active(c.get("/api/movements").json())[0]
    assert original["bank_account_id"] == accounts[0]["id"] and original["bank_account_name"] == "Conta 1"
    assert c.post(f"/api/movements/{original['id']}/reverse", json={"reason": "Rever valor no fecho"}).status_code == 409
    with factory.begin() as db:
        db.get(PaymentMethodBankDefault, ("cartao", store)).account_id = accounts[1]["id"]
    assert active(c.get("/api/movements").json())[0]["bank_account_id"] == accounts[0]["id"]
    assert c.post(f"/api/sessions/{identifier}/reopen", json={"reason": "Corrigir faturado do dia"}).status_code == 200
    rows = c.get("/api/movements?all_records=true").json()
    assert rows == []
    with factory() as db:
        original_row = db.get(Movement, original["id"])
        reversal = db.get(Movement, original_row.reversed_by)
        assert reversal.reversal_of == original_row.id
        assert reversal.bank_account_id == original_row.bank_account_id
        assert reversal.direction * reversal.amount_cents + original_row.direction * original_row.amount_cents == 0
        assert db.scalar(select(func.count()).select_from(Movement)) == 2
    payload["realized"]["values"]["cartao"] = "150"
    payload["payments"]["cartao"] = "150"
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    rows = c.get("/api/movements?all_records=true").json()
    assert len(rows) == 1 and sum(row["direction"] * row["amount_cents"] for row in rows) == 15000
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 3
    assert active(rows)[0]["bank_account_id"] == accounts[1]["id"]
    assert sheet(c, identifier)["sheet"]["reconciliation"]["difference_cents"] == 0


def test_repeated_reopening_removes_old_closings_from_real_movements_but_keeps_manual_entries(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    manual = {
        movement(c, ids, identifier, amount="10").json()["id"],
        movement(c, ids, identifier, kind="despesa", amount="3").json()["id"],
    }
    payload = {**declared(numerario="300", cartao="50"), "cash_total": "320",
               "payments": {"cartao": "50"}, "supplier_answer": "indicado"}
    payload["realized"]["debits"] = {"numerario": "20"}
    previous = set()
    for generation in range(3):
        payload["supplier_name"] = f"Fornecedor do fecho {generation}"
        result = c.post(f"/api/sessions/{identifier}/close", json=payload)
        assert result.status_code == 200, result.text
        for path in ("/api/movements", "/api/movements?all_records=true"):
            rows = c.get(path).json()
            assert len(rows) == 5
            assert sum(row["direction"] * row["amount_cents"] for row in rows) == 33000
            assert not previous.intersection(row["id"] for row in rows)
            posted = [row for row in rows if row["origin"] == "fecho_local"]
            assert len(posted) == 3
            assert {row["entity"] for row in posted if row["kind"] == "fornecedor"} == {payload["supplier_name"]}
        previous.update(row["id"] for row in posted)
        assert c.post(f"/api/sessions/{identifier}/reopen", json={"reason": "Corrigir fornecedor e conferir novamente"}).status_code == 200
        for path in ("/api/movements", "/api/movements?all_records=true"):
            rows = c.get(path).json()
            assert {row["id"] for row in rows} == manual
            assert sum(row["direction"] * row["amount_cents"] for row in rows) == 700
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 20
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "estorno_fecho_local")) == 9
        for identifier in previous:
            original = db.get(Movement, identifier)
            assert db.get(Movement, original.reversed_by).reversal_of == identifier


def test_zero_or_lower_declared_values_never_reverse_existing_money(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    assert movement(c, ids, identifier, amount="300").status_code == 200
    assert movement(c, ids, identifier, kind="despesa", amount="50").status_code == 200
    payload = {**declared(numerario="200"), "cash_total": "300"}
    payload["realized"]["debits"] = {"numerario": "0"}
    response = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert response.status_code == 200, response.text
    assert len(c.get("/api/movements").json()) == 2
    assert not sheet(c, identifier)["count_data"]["posted_movements"]["ids"]


def test_partial_supplier_allocation_is_not_assigned_to_the_whole_new_debit(local):
    from app.models import BusinessEntity

    c, ids, factory = local
    with factory.begin() as db:
        supplier = BusinessEntity(name="Fornecedor parcial", is_supplier=True)
        db.add(supplier)
        db.flush()
        supplier_id = "local:" + supplier.id
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    payload = {**declared(), "cash_total": "30", "supplier_answer": "indicado",
               "supplier_allocations": [{"supplier_id": supplier_id, "amount_cents": 200}]}
    payload["realized"]["debits"] = {"numerario": "10"}
    response = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert response.status_code == 200, response.text
    rows = c.get("/api/movements").json()
    assert len(rows) == 2 and sum(row["amount_cents"] for row in rows) == 1000
    assert next(row for row in rows if row["entity"] == "Fornecedor parcial")["amount_cents"] == 200
    unassigned = next(row for row in rows if not row["entity"])
    assert unassigned["amount_cents"] == 800 and unassigned["supplier_unassigned"]
    assert "Repartição dos fornecedores" in unassigned["notes"]
    assert sheet(c, identifier)["count_data"]["debit_supplier"]["unassigned_cents"] == 800


def test_negative_sales_post_refund_without_count_adjustment(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    payload = {**declared(numerario="-20"), "cash_total": "80"}
    response = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert response.status_code == 200, response.text
    rows = c.get("/api/movements").json()
    assert len(rows) == 1 and rows[0]["kind"] == "reembolso" and rows[0]["amount_cents"] == 2000 and rows[0]["direction"] == -1
    assert sheet(c, identifier)["balance_cents"] == 8000


def test_zero_sales_do_not_invent_a_receipt_to_offset_recorded_refunds(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    assert movement(c, ids, identifier, kind="reembolso", amount="20").status_code == 200
    response = c.post(f"/api/sessions/{identifier}/close", json={**declared(), "cash_total": "100"})
    assert response.status_code == 200, response.text
    rows = c.get("/api/movements").json()
    assert len(rows) == 1 and rows[0]["kind"] == "reembolso"


def test_later_close_of_same_register_day_does_not_post_the_day_again(local):
    c, ids, factory = local
    payload = {**declared(numerario="200"), "cash_total": "240"}
    first = open_cash(c, ids["registers"][0], "40").json()["id"]
    assert c.post(f"/api/sessions/{first}/close", json=payload).status_code == 200
    second = open_cash(c, ids["registers"][0], "40").json()["id"]
    assert c.post(f"/api/sessions/{second}/close", json=payload).status_code == 200
    assert len(active(c.get("/api/movements").json())) == 1
    assert sheet(c, second)["count_data"]["posted_movements"]["ids"] == []
    result = c.post(f"/api/sessions/{first}/reopen", json={"reason": "Não remover entradas usadas no fecho seguinte"})
    assert result.status_code == 409 and sheet(c, first)["closed_at"]


def test_reopening_with_an_inactive_method_preserves_its_recorded_money(local):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    payload = {**declared(cartao="200"), "cash_total": "40", "payments": {"cartao": "200"}}
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    assert c.post("/api/cash/payment-methods/cartao/status", json={"active": False}).status_code == 200
    assert c.post(f"/api/sessions/{identifier}/reopen", json={"reason": "Rever apenas os meios ativos"}).status_code == 200
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    rows = active(c.get("/api/movements").json())
    assert len(rows) == 1 and rows[0]["amount_cents"] == 20000


def test_reopen_is_atomic_when_a_generated_movement_is_reconciled(local, monkeypatch):
    from app import reconciliation

    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "40").json()["id"]
    payload = {**declared(numerario="200"), "cash_total": "240"}
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    monkeypatch.setattr(reconciliation, "used", lambda *_: 100)
    result = c.post(f"/api/sessions/{identifier}/reopen", json={"reason": "Tentar reabrir após reconciliação"})
    assert result.status_code == 409
    assert sheet(c, identifier)["closed_at"]
    assert len(active(c.get("/api/movements").json())) == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 1


def test_posting_is_tenant_scoped_and_uses_sheet_calendar_date(platform):
    c, _ = platform
    a, b = create(c, "movimentos-fecho-a"), create(c, "movimentos-fecho-b")
    c.cookies.clear()
    boot = enter(c, a["slug"])
    response = c.post("/api/sessions", json={"register_id": boot["registers"][0]["id"], "opening": "40", "sheet_day": "2026-10-02"})
    identifier = response.json()["id"]
    payload = {**declared(numerario="200"), "cash_total": "240"}
    assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 200
    rows = c.get("/api/movements").json()
    assert len(rows) == 1 and rows[0]["occurred_at"].startswith("2026-10-02")
    assert len(c.get("/api/movements").json()) == 1
    enter(c, b["slug"])
    assert c.get("/api/movements?all_records=true").json() == []
    assert c.post(f"/api/sessions/{identifier}/reopen", json={"reason": "Outra empresa não pode reabrir"}).status_code == 404


def test_posting_discounts_receipts_already_imported_from_erp(local):
    from app.models import WintouchSaleRecord

    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    assert movement(c, ids, identifier, payment="cartao", amount="200").status_code == 200
    with factory.begin() as db:
        session = db.get(CashSession, identifier)
        store = db.get(Register, session.register_id).store_id
        session.wintouch_scope = store + ":" + session.sheet_day
        session.wintouch_enterprise = "demo"
        source = json.dumps({"payment": "Cartão", "payment_code": "CD", "amount_cents": 50000, "direction": 1,
                             "occurred_at": session.sheet_day + "T12:00:00+01:00", "source_kind": "sale"})
        row = WintouchSaleRecord(enterprise_id="demo", external_id="close-post-receipt", store_id=store,
                                kind="real", active=True, document_date=session.sheet_day, data=source)
        db.add(row)
        db.flush()
        source_id = row.id
    payload = {**declared(cartao="1000"), "cash_total": "100", "payments": {"cartao": "1000"}}
    result = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert result.status_code == 200, result.text
    posted = [row for row in c.get("/api/movements").json() if row["origin"] == "fecho_local"]
    assert len(posted) == 1 and posted[0]["payment"] == "cartao" and posted[0]["amount_cents"] == 30000
    with factory() as db:
        assert db.get(WintouchSaleRecord, source_id).data == source


def test_movement_bank_migration_is_additive_and_idempotent(local, monkeypatch):
    c, ids, factory = local
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    assert movement(c, ids, identifier).status_code == 200
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    migration = importlib.import_module("migrations.versions.0039_movement_bank_account")
    with factory.kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_[0-9a-f]{32}", schema)
        connection.execute(text(f"ALTER TABLE {schema}.movements DROP COLUMN bank_account_id"))
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert "bank_account_id" in {column["name"] for column in inspect(connection).get_columns("movements", schema=schema)}
        assert connection.scalar(select(func.count()).select_from(Movement)) == 1
