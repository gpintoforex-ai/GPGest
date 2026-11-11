"""Percurso da compra (encomenda → receções → fatura → devolução → nota de crédito → pagamento) e quantidades
da encomenda ao reabrir uma receção."""

from datetime import date
from decimal import Decimal as D
from uuid import uuid4

from app.purchase_receiving import open_orders
from test_inventory import stock_case  # noqa: F401  (fixture)
from test_procurement import case, confirm, draft, from_source  # noqa: F401  (fixture e auxiliares)


def test_process_joins_every_linked_document_from_any_starting_point(case):  # noqa: F811
    stock, _ = case
    client = stock[0]
    order = confirm(case, draft(case))
    first = confirm(case, from_source(case, order, "receipt", 12))
    invoice = confirm(case, from_source(case, first, "invoice", 12))
    second = confirm(case, from_source(case, order, "receipt", 8))
    returned = confirm(case, from_source(case, first, "return", 2, reason="Defeito encontrado"))
    confirm(case, from_source(case, returned, "credit", 2))
    paid = client.post("/api/document-payments", json={
        "store_id": stock[4], "payment_date": date.today().isoformat(), "amount_cents": 5000, "direction": -1,
        "reference": "Pagamento parcial", "request_key": str(uuid4()),
        "lines": [{"purchase_id": invoice["purchase_id"], "amount_cents": 5000}]})
    assert paid.status_code == 200, paid.text

    response = client.get(f"/api/procurement/{second['id']}/process")
    assert response.status_code == 200, response.text
    data = response.json()
    kinds = [d["kind"] for d in data["documents"]]
    # A partir da segunda receção chega-se à encomenda, à outra receção e a tudo o que dela nasceu.
    assert kinds == ["order", "receipt", "receipt", "invoice", "return", "credit"]
    assert [d["current"] for d in data["documents"]].count(True) == 1
    steps = {s["key"]: s for s in data["steps"]}
    assert steps["order"]["state"] == "done" and steps["receipt"]["state"] == "done"
    assert steps["invoice"]["state"] == "partial"  # a segunda receção ainda não tem fatura
    assert steps["payment"]["state"] == "partial" and steps["return"]["state"] == "done"
    finance = next(d for d in data["documents"] if d["id"] == invoice["id"])["financial"][0]
    assert finance["paid_cents"] == 5000 and finance["remaining_cents"] == 9760 and finance["planned"]
    ordered = next(d for d in data["documents"] if d["kind"] == "order")["lines"][0]
    assert D(ordered["quantity"]) == 20 and D(ordered["received"]) == 20 and D(ordered["pending"]) == 0

    # O mesmo percurso a partir da encomenda.
    assert [d["kind"] for d in client.get(f"/api/procurement/{order['id']}/process").json()["documents"]] == kinds


def test_reopened_receipt_does_not_count_its_own_quantities_as_already_received(case):  # noqa: F811
    stock, supplier = case
    order = confirm(case, draft(case))
    receipt = confirm(case, from_source(case, order, "receipt", 12))
    with stock[2]() as db:
        listed = open_orders(db, supplier, {stock[4]}, include=order["id"])[0]["lines"][0]
        reopened = open_orders(db, supplier, {stock[4]}, include=order["id"], receipt_id=receipt["id"])[0]["lines"][0]
    assert (D(listed["ordered"]), D(listed["received"]), D(listed["pending"])) == (20, 12, 8)
    # Ao reabrir esta receção, as 12 que ela recebeu voltam a contar como por receber nesta comparação.
    assert (D(reopened["received"]), D(reopened["pending"])) == (0, 20)
