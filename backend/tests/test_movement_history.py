from uuid import uuid4

from app.models import CashSession, Movement
from conftest import open_cash


def test_full_movement_history_keeps_default_limit_and_scope(setup):
    client, ids, factory = setup
    session_id = open_cash(client, ids["registers"][0]).json()["id"]
    with factory.begin() as db:
        session = db.get(CashSession, session_id)
        db.add_all(
            [
                Movement(
                    session_id=session.id,
                    user_id=session.operator_id,
                    category_id=ids["category"],
                    kind="venda",
                    payment="numerario",
                    amount_cents=100,
                    direction=1,
                    occurred_at=session.opened_at,
                    request_key=uuid4().hex,
                )
                for _ in range(501)
            ]
        )
    assert len(client.get("/api/movements").json()) == 500
    assert len(client.get("/api/movements?all_records=true").json()) == 501
    client.post("/api/auth/logout", json={})
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert len(client.get("/api/movements?all_records=true").json()) == 501
