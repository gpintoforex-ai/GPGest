import json

from app.models import BusinessEntity, CashSession, Movement, Register, now, uid
from conftest import movement, open_cash
from sqlalchemy import func, select
from test_cash_local import declared, local, sheet


def suppliers(factory):
    with factory.begin() as db:
        rows = [BusinessEntity(name=name, is_supplier=True) for name in ("Fornecedor A", "Fornecedor B")]
        db.add_all(rows)
        db.flush()
        return ["local:" + row.id for row in rows]


def test_supplier_payments_post_separate_rows_and_reclose_without_duplication(local):
    c, ids, factory = local
    a, b = suppliers(factory)
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    payload = {**declared(), "cash_total": "90", "supplier_answer": "indicado", "supplier_allocations": [
        {"supplier_id": a, "amount_cents": 400}, {"supplier_id": b, "amount_cents": 600}]}
    payload["realized"]["debits"] = {"numerario": "10"}
    for generation in (1, 2):
        response = c.post(f"/api/sessions/{identifier}/close", json=payload)
        assert response.status_code == 200, response.text
        all_rows = c.get("/api/movements?all_records=true").json()
        rows = [row for row in all_rows if not row["reversed_by"] and not row["reversal_of"]]
        assert {(row["entity"], row["amount_cents"], row["payment"]) for row in rows} == {
            ("Fornecedor A", 400, "numerario"), ("Fornecedor B", 600, "numerario")}
        assert sum(row["direction"] * row["amount_cents"] for row in all_rows) == -1000
        assert len(all_rows) == 2
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Movement)) == (2 if generation == 1 else 6)
        saved = sheet(c, identifier)["count_data"]
        assert {part["supplier_id"] for part in saved["posted_movements"]["supplier_payments"]} == {a, b}
        assert c.post(f"/api/sessions/{identifier}/close", json=payload).status_code == 409
        if generation == 1:
            assert c.post(f"/api/sessions/{identifier}/reopen", json={"reason": "Verificar repartição dos pagamentos"}).status_code == 200
            assert c.get("/api/movements").json() == []
            assert c.get("/api/movements?all_records=true").json() == []


def test_already_recorded_supplier_amounts_are_deducted_from_each_supplier(local):
    c, ids, factory = local
    a, b = suppliers(factory)
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    registered = movement(c, ids, identifier, kind="despesa", amount="3").json()
    with factory.begin() as db:
        db.get(Movement, registered["id"]).entity = "Fornecedor A"
    payload = {**declared(), "cash_total": "90", "supplier_answer": "indicado", "supplier_allocations": [
        {"supplier_id": a, "amount_cents": 400}, {"supplier_id": b, "amount_cents": 600}]}
    payload["realized"]["debits"] = {"numerario": "10"}
    response = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert response.status_code == 200, response.text
    rows = c.get("/api/movements").json()
    posted = [row for row in rows if row["origin"] == "fecho_local"]
    assert {(row["entity"], row["amount_cents"]) for row in posted} == {("Fornecedor A", 100), ("Fornecedor B", 600)}
    assert sum(row["amount_cents"] for row in rows) == 1000


def test_supplier_payment_methods_are_validated_and_keep_separate_bank_rows(local):
    c, ids, factory = local
    a, b = suppliers(factory)
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    payload = {**declared(), "cash_total": "96", "payments": {"cartao": "-6"},
               "supplier_answer": "indicado", "supplier_allocations": [
                   {"supplier_id": a, "amount_cents": 400, "payment": "numerario"},
                   {"supplier_id": a, "amount_cents": 600, "payment": "cartao"}]}
    payload["realized"]["debits"] = {"numerario": "4", "cartao": "6"}
    wrong = {**payload, "supplier_allocations": [{"supplier_id": a, "amount_cents": 500, "payment": "numerario"}]}
    assert c.post(f"/api/sessions/{identifier}/close", json=wrong).status_code == 422
    wrong["supplier_allocations"][0]["payment"] = "not-a-method"
    assert c.post(f"/api/sessions/{identifier}/close", json=wrong).status_code == 422
    wrong["supplier_allocations"] = [payload["supplier_allocations"][0], payload["supplier_allocations"][0]]
    assert c.post(f"/api/sessions/{identifier}/close", json=wrong).status_code == 422
    assert c.get("/api/movements").json() == []
    response = c.post(f"/api/sessions/{identifier}/close", json=payload)
    assert response.status_code == 200, response.text
    rows = c.get("/api/movements").json()
    assert {(row["entity"], row["payment"], row["amount_cents"]) for row in rows} == {
        ("Fornecedor A", "numerario", 400), ("Fornecedor A", "cartao", 600)}


def test_legacy_grouped_supplier_payment_exposes_display_parts_without_reposting(local):
    c, ids, factory = local
    a, b = suppliers(factory)
    identifier = open_cash(c, ids["registers"][0], "100").json()["id"]
    with factory.begin() as db:
        session = db.get(CashSession, identifier)
        move = Movement(session_id=identifier, user_id=session.operator_id, category_id=ids["category"],
                        kind="fornecedor", payment="numerario", amount_cents=1000, direction=-1,
                        occurred_at=now(), origin="fecho_local", request_key=uid(), entity="Vários fornecedores")
        db.add(move)
        db.flush()
        parent_id = move.id
        session.closed_at = now()
        session.count_data = json.dumps({"workflow_mode": "local", "posted_movements": {"ids": [move.id]},
            "debit_supplier": {"answer": "indicado", "debits_cents": 1000, "unassigned_cents": 0, "allocations": [
                {"id": a, "name": "Fornecedor A", "amount_cents": 400},
                {"id": b, "name": "Fornecedor B", "amount_cents": 600}]}})
    for _ in range(2):
        rows = c.get("/api/movements?all_records=true").json()
        assert len(rows) == 1 and rows[0]["id"] == parent_id and rows[0]["amount_cents"] == 1000
        assert [(part["name"], part["amount_cents"]) for part in rows[0]["supplier_parts"]] == [("Fornecedor A", 400), ("Fornecedor B", 600)]
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 1
        # An old partial posting cannot be assigned to suppliers by guessing.
        db.get(Movement, parent_id).amount_cents = 500
    assert "supplier_parts" not in c.get("/api/movements").json()[0]
