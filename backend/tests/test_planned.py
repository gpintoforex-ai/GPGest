from datetime import datetime, timezone
from uuid import uuid4

from app.models import Movement, Register
from conftest import open_cash
from sqlalchemy import func, select


def create(c, ids, factory, direction=1):
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    response = c.post(
        "/api/planned-movements",
        json={
            "store_id": store,
            "category_id": ids["category"],
            "entity": "Cliente teste",
            "document": "FT 2026/1",
            "due_date": "2026-12-01",
            "amount": "100",
            "direction": direction,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def payment(session, amount):
    return {
        "session_id": session,
        "amount": amount,
        "payment": "transferencia",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "request_key": str(uuid4()),
    }


def test_partial_full_retry_and_reversal(setup):
    c, ids, factory = setup
    row = create(c, ids, factory)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 0
    session = open_cash(c, ids["registers"][0]).json()["id"]
    url = f"/api/planned-movements/{row['id']}/settle"
    body = payment(session, "40")
    response = c.post(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["remaining_cents"] == 6000
    assert c.post(url, json=body).json()["paid_cents"] == 4000
    assert c.post(url, json=payment(session, "60.01")).status_code == 409
    full = c.post(url, json=payment(session, "60"))
    assert full.json()["status"] == "Liquidado"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 2
        movements = list(db.scalars(select(Movement)))
        assert all(m.document == "FT 2026/1" and m.entity == "Cliente teste" for m in movements)
    first = response.json()["settlements"][0]["id"]
    assert (
        c.post(f"/api/movements/{first}/reverse", json={"reason": "Pagamento anulado"}).status_code
        == 200
    )
    reopened = c.get("/api/planned-movements").json()[0]
    assert reopened["remaining_cents"] == 4000
    assert reopened["status"] == "Parcial"


def test_payment_scope_and_read_only(setup):
    c, ids, factory = setup
    row = create(c, ids, factory, direction=-1)
    other = open_cash(c, ids["registers"][2]).json()["id"]
    assert (
        c.post(f"/api/planned-movements/{row['id']}/settle", json=payment(other, "100")).status_code
        == 422
    )
    session = open_cash(c, ids["registers"][0]).json()["id"]
    settled = c.post(f"/api/planned-movements/{row['id']}/settle", json=payment(session, "100"))
    assert settled.status_code == 200
    assert settled.json()["settlements"][0]["direction"] == -1
    c.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert c.get("/api/planned-movements").status_code == 200
    assert (
        c.post(f"/api/planned-movements/{row['id']}/settle", json=payment(session, "1")).status_code
        == 403
    )
