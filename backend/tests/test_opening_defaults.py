from datetime import datetime, timedelta, timezone

from app.main import app
from app.models import CashSession
from fastapi.testclient import TestClient


def test_last_opening_per_register_beyond_recent_sessions_and_access_scope(setup):
    client, ids, factory = setup
    registers = ids["registers"]
    initial = client.get("/api/bootstrap").json()["registers"]
    assert all(row["last_opening_cents"] is None for row in initial)
    current = datetime.now(timezone.utc)
    with factory.begin() as db:
        for register, opening, opened, sheet_day in [
            (registers[0], 1200, current - timedelta(days=3), "2030-01-01"),
            (registers[0], 5520, current - timedelta(days=2), "2026-01-01"),
            (registers[1], 7550, current - timedelta(days=3), "2026-01-01"),
            (registers[2], 88888, current - timedelta(days=1), "2026-01-01"),
        ]:
            db.add(
                CashSession(
                    register_id=register,
                    operator_id=ids["operator"],
                    opening_cents=opening,
                    counted_cents=999,
                    opened_at=opened,
                    closed_at=opened + timedelta(hours=1),
                    sheet_day=sheet_day,
                )
            )
        # A primeira caixa não aparece nas 200 sessões recentes. Zero também é uma abertura válida.
        for index in range(201):
            opened = current - timedelta(minutes=202 - index)
            db.add(
                CashSession(
                    register_id=registers[1],
                    operator_id=ids["operator"],
                    opening_cents=0,
                    opened_at=opened,
                    closed_at=opened + timedelta(seconds=1),
                )
            )
    values = {
        row["id"]: row["last_opening_cents"]
        for row in client.get("/api/bootstrap").json()["registers"]
    }
    assert values == {registers[0]: 5520, registers[1]: 0, registers[2]: 88888}
    with TestClient(app) as operator:
        assert (
            operator.post(
                "/api/auth/login",
                json={
                    "email": "operator@test.pt",
                    "password": "PasswordTest123!",
                },
            ).status_code
            == 200
        )
        visible = {
            row["id"]: row["last_opening_cents"]
            for row in operator.get("/api/bootstrap").json()["registers"]
        }
        assert visible == {registers[0]: 5520, registers[1]: 0}
