import json

import pytest
from app.models import Audit, BusinessEntity, CashSession
from conftest import movement, open_cash
from sqlalchemy import select


def test_imported_cash_debit_entity_survives_resync(setup):
    from app.models import Register, WintouchSaleRecord

    client, ids, factory = setup
    session = open_cash(client, ids["registers"][0], "40").json()["id"]
    with factory.begin() as db:
        sheet = db.get(CashSession, session)
        store = db.get(Register, sheet.register_id).store_id
        sheet.sheet_day = "2026-10-12"
        sheet.wintouch_scope = store + ":2026-10-12"
        sheet.wintouch_enterprise = "demo"
        imported = WintouchSaleRecord(enterprise_id="demo", external_id="withdrawal-demo",
            store_id=store, kind="real", document_date="2026-10-12", active=True,
            data=json.dumps({"occurred_at": "2026-10-12T12:00:00", "direction": -1,
                "amount_cents": 500, "payment_code": "NU", "source_kind": "cash_movement",
                "entity": "Entidade não indicada"}))
        db.add(imported)
        db.flush()
        imported_id = imported.id
    result = client.post(f"/api/sessions/{session}/close", json={"cash_mode": "total",
        "cash_total": "35", "supplier_answer": "indicado", "supplier_name": "Fornecedor Demo"})
    assert result.status_code == 200, result.text
    assert json.loads(result.json()["count_data"])["debit_supplier"]["imported_movement_ids"] == [imported_id]
    rows = client.get("/api/integrations/wintouch/sales").json()
    assert next(row for row in rows if row["id"] == imported_id)["entity"] == "Fornecedor Demo"
    with factory() as db:
        assert json.loads(db.get(WintouchSaleRecord, imported_id).data)["entity"] == "Entidade não indicada"


@pytest.mark.parametrize(
    "answer,name,stored_name",
    [
        ("indicado", "Fornecedor XPTO", "Fornecedor XPTO"),
        ("desconhecido", "", ""),
        ("nao_aplicavel", "", ""),
    ],
)
def test_supplier_answer_is_saved_and_audited_on_close(setup, answer, name, stored_name):
    client, ids, factory = setup
    session = open_cash(client, ids["registers"][0], "40").json()["id"]
    assert movement(client, ids, session, kind="despesa", amount="5").status_code == 200
    response = client.post(
        f"/api/sessions/{session}/close",
        json={
            "cash_mode": "total",
            "cash_total": "35",
            "supplier_answer": answer,
            "supplier_name": name,
        },
    )
    assert response.status_code == 200, response.text
    details = json.loads(response.json()["count_data"])["debit_supplier"]
    assert details["answer"] == answer
    assert details["name"] == stored_name
    assert details["debits_cents"] == 500
    if answer == "indicado":
        assert client.get("/api/movements").json()[0]["entity"] == stored_name
    with factory() as db:
        event = db.scalar(select(Audit).where(Audit.target_id == session, Audit.action == "fecho"))
        assert json.loads(event.detail)["debit_supplier"] == details


def test_known_supplier_requires_a_name_without_closing_cash(setup):
    client, ids, factory = setup
    session = open_cash(client, ids["registers"][0], "40").json()["id"]
    assert movement(client, ids, session, kind="despesa", amount="5").status_code == 200
    response = client.post(
        f"/api/sessions/{session}/close",
        json={
            "cash_mode": "total",
            "cash_total": "35",
            "supplier_answer": "indicado",
            "supplier_name": " ",
        },
    )
    assert response.status_code == 422
    with factory() as db:
        assert db.get(CashSession, session).closed_at is None


def test_supplier_selected_from_company_list_is_linked_by_id(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        supplier = BusinessEntity(
            name="Fornecedor da lista", tax_number="500000000", is_supplier=True
        )
        db.add(supplier)
        db.flush()
        supplier_id = "local:" + supplier.id
    session = open_cash(client, ids["registers"][0], "40").json()["id"]
    assert movement(client, ids, session, kind="despesa", amount="5").status_code == 200
    result = client.post(
        f"/api/sessions/{session}/close",
        json={
            "cash_mode": "total",
            "cash_total": "35",
            "supplier_answer": "indicado",
            "supplier_id": supplier_id,
            "supplier_name": "Nome não fiável",
        },
    )
    assert result.status_code == 200, result.text
    linked = json.loads(result.json()["count_data"])["debit_supplier"]
    assert linked["id"] == supplier_id and linked["name"] == "Fornecedor da lista"
    assert linked["tax_number"] == "500000000"
