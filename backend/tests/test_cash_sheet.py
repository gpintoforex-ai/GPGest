import json
from datetime import datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.cash_sheet import day
from app.models import CashSession, Movement, Register, WintouchSaleRecord
from conftest import movement, open_cash
import pytest


@pytest.mark.parametrize("counted,direction", [("95", -1), ("105", 1)])
def test_close_difference_movement_and_reopen(setup, counted, direction):
    c, ids, factory = setup
    session = open_cash(c, ids["registers"][0]).json()["id"]
    payload = {"cash_total": counted, "reason": "Conferência de caixa"}
    assert c.post(f"/api/sessions/{session}/close", json=payload).status_code == 200
    rows = c.get("/api/movements").json()
    adjustment = next(m for m in rows if m["kind"] == "diferenca_caixa")
    assert adjustment["direction"] == direction
    assert adjustment["amount_cents"] == 500
    assert adjustment["notes"] == payload["reason"]
    frozen = c.get("/api/sessions").json()[0]
    assert by_key(frozen)["numerario"]["expected_cents"] == 10000
    assert c.post(f"/api/sessions/{session}/close", json=payload).status_code == 409
    assert len(c.get("/api/movements").json()) == 1
    assert c.post(f"/api/sessions/{session}/reopen", json={"reason": "Recontar"}).status_code == 200
    assert c.get("/api/movements").json() == []
    assert by_key(c.get("/api/sessions").json()[0])["numerario"]["expected_cents"] == 10000
    reopened_cash = by_key(c.get("/api/sessions").json()[0])["numerario"]
    assert reopened_cash["credits_cents"] == 0
    assert reopened_cash["debits_cents"] == 0
    assert c.post(f"/api/sessions/{session}/close", json={"cash_total": "100"}).status_code == 200
    rows = c.get("/api/movements").json()
    assert rows == []
    with factory() as db:
        original = db.get(Movement, adjustment["id"])
        reversal = db.get(Movement, original.reversed_by)
        assert reversal.reversal_of == original.id
        assert reversal.direction * reversal.amount_cents + original.direction * original.amount_cents == 0
    assert c.post(f"/api/sessions/{session}/reopen", json={"reason": "Recontar novamente"}).status_code == 200
    assert c.post(f"/api/sessions/{session}/close", json=payload).status_code == 200
    current = c.get("/api/movements").json()
    assert len(current) == 1 and current[0]["id"] != adjustment["id"]


def test_selected_difference_rubric_and_switch_preserve_history(setup):
    c, ids, factory = setup
    first = c.post("/api/categories", json={"name": "Sobras e faltas", "cash_difference": True}).json()
    second = c.post("/api/categories", json={"name": "Acertos de caixa"}).json()
    session = open_cash(c, ids["registers"][0]).json()["id"]
    assert c.post(f"/api/sessions/{session}/close", json={"cash_total": "95", "reason": "Diferença confirmada"}).status_code == 200
    adjustment = next(row for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa")
    assert adjustment["category_id"] == first["id"]
    result = c.post(f"/api/categories/{second['id']}", json={"name": second["name"], "cash_difference": True})
    assert result.status_code == 200, result.text
    categories = c.get("/api/categories").json()
    assert [row["id"] for row in categories if row["cash_difference"]] == [second["id"]]
    assert next(row for row in c.get("/api/movements").json() if row["id"] == adjustment["id"])["category_id"] == first["id"]


def test_unconfigured_difference_uses_named_rubric(setup):
    c, ids, factory = setup
    session = open_cash(c, ids["registers"][0]).json()["id"]
    assert c.post(f"/api/sessions/{session}/close", json={"cash_total": "105", "reason": "Sobra confirmada"}).status_code == 200
    adjustment = next(row for row in c.get("/api/movements").json() if row["kind"] == "diferenca_caixa")
    category = next(row for row in c.get("/api/categories").json() if row["id"] == adjustment["category_id"])
    assert category["name"] == "Diferenças de caixa" and category["cash_difference"]


def test_reopen_difference_without_snapshot_reference(setup):
    c, ids, factory = setup
    session = open_cash(c, ids["registers"][0]).json()["id"]
    assert c.post(f"/api/sessions/{session}/close", json={"cash_total": "102", "reason": "Conferência"}).status_code == 200
    adjustment = c.get("/api/movements").json()[0]
    with factory.begin() as db:
        cash_session = db.get(CashSession, session)
        snapshot = json.loads(cash_session.count_data)
        snapshot.pop("adjustment_id")
        cash_session.count_data = json.dumps(snapshot)
    assert c.post(f"/api/sessions/{session}/reopen", json={"reason": "Rever fecho antigo"}).status_code == 200
    assert c.get("/api/movements").json() == []
    with factory() as db:
        assert db.get(Movement, adjustment["id"]).reversed_by
    assert by_key(c.get("/api/sessions").json()[0])["numerario"]["expected_cents"] == 10000


def by_key(response):
    return {row["key"]: row for row in response["sheet"]["rows"]}


def test_count_draft_all_methods_and_close_snapshot(setup):
    c, ids, _ = setup
    session = open_cash(c, ids["registers"][0]).json()["id"]
    movement(c, ids, session, amount="12.30")
    movement(c, ids, session, amount="20", payment="cartao")
    movement(c, ids, session, amount="5", payment="cartao", kind="reembolso")
    payload = {
        "cash_total": "112,30",
        "payments": {"cartao": "14.50", "transferencia": "0", "outro": "0"},
    }
    saved = c.post(f"/api/sessions/{session}/count", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["count_data"]["values"]["cartao"] == 1450
    assert saved.json()["closed_at"] is None
    rows = by_key(saved.json())
    assert rows["numerario"]["fund_cents"] == 10000
    assert rows["numerario"]["expected_cents"] == 11230
    assert rows["cartao"]["credits_cents"] == 2000 and rows["cartao"]["debits_cents"] == 500
    assert c.post(f"/api/sessions/{session}/close", json=payload).status_code == 422
    assert c.get("/api/sessions").json()[0]["closed_at"] is None
    assert (
        c.post(
            f"/api/sessions/{session}/close", json={**payload, "reason": "Comissão terminal"}
        ).status_code
        == 200
    )
    frozen = c.get("/api/sessions").json()[0]
    assert frozen["count_data"]["final"] and by_key(frozen)["cartao"]["expected_cents"] == 1500
    assert c.post(f"/api/sessions/{session}/count", json=payload).status_code == 409
    assert c.get(f"/api/sessions/{session}/report").content.startswith(b"%PDF")
    assert (
        c.post(f"/api/sessions/{session}/reopen", json={"reason": "Rever contagem"}).status_code
        == 200
    )
    assert not c.get("/api/sessions").json()[0]["count_data"].get("final")


def test_count_validation_and_permissions(setup):
    c, ids, _ = setup
    session = open_cash(c, ids["registers"][0]).json()["id"]
    for payload in (
        {"counts": {"100": 1.5}},
        {"counts": {"3": 1}},
        {"counts": {"100": -1}},
        {"counts": {"100": 1}, "cash_total": "1"},
        {"cash_total": "-1"},
        {"payments": {"unknown": "1"}},
        {"payments": {"cartao": "NaN"}},
    ):
        assert c.post(f"/api/sessions/{session}/count", json=payload).status_code == 422
    assert (
        c.post(f"/api/sessions/{session}/count", json={"counts": {"10000": 1}}).json()[
            "count_data"
        ]["values"]["numerario"]
        == 10000
    )
    c.post("/api/auth/logout", json={})
    c.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"})
    assert c.post(f"/api/sessions/{session}/count", json={"cash_total": "100"}).status_code == 403
    c.post("/api/auth/logout", json={})
    c.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert c.post(f"/api/sessions/{session}/count", json={"cash_total": "100"}).status_code == 403


def test_wintouch_projection_scope_dates_and_closed_values(setup):
    c, ids, factory = setup
    session = open_cash(c, ids["registers"][0]).json()["id"]
    with factory.begin() as db:
        s = db.get(CashSession, session)
        store = db.get(Register, s.register_id).store_id
        s.sheet_day = "2026-10-02"
        s.wintouch_scope = store + ":2026-10-02"
        s.wintouch_enterprise = "source-a"
        base = {
            "enterprise_id": "source-a",
            "store_id": store,
            "kind": "real",
            "active": True,
            "document_date": "2026-09-01",
        }

        # UTC 1 October late evening is 2 October in Lisbon (DST).
        def receipt(value, code, **overrides):
            db.add(
                WintouchSaleRecord(
                    **{**base, **overrides},
                    external_id=str(uuid4()),
                    data=json.dumps(
                        {
                            "amount_cents": value,
                            "direction": 1,
                            "payment": "Origem",
                            "payment_code": code,
                            "occurred_at": "2026-10-01T23:30:00Z",
                        }
                    ),
                )
            )

        receipt(1230, "NU")
        receipt(2500, "CD")
        receipt(9000, "NU", kind="documento")
        receipt(9000, "NU", enterprise_id="other-source")
        receipt(9000, "NU", active=False)
    movement(c, ids, session, amount="10")
    with factory.begin() as db:
        for move in db.query(Movement).filter_by(session_id=session):
            move.occurred_at = datetime.fromisoformat("2026-10-02T12:00:00+01:00")
    current = c.get("/api/sessions").json()[0]
    assert current["balance_cents"] == 12230
    rows = by_key(current)
    assert rows["numerario"]["local_cents"] == 1000 and rows["numerario"]["wintouch_cents"] == 1230
    assert rows["cartao"]["expected_cents"] == 2500
    assert (
        c.post(
            f"/api/sessions/{session}/close",
            json={"cash_total": "122.30", "payments": {"cartao": "25"}},
        ).status_code
        == 200
    )
    with factory.begin() as db:
        for record in db.query(WintouchSaleRecord).all():
            record.active = False
    frozen = c.get("/api/sessions").json()[0]
    assert frozen["balance_cents"] == 12230 and by_key(frozen)["cartao"]["expected_cents"] == 2500
    assert day("2026-10-01T23:30:00Z") == "2026-10-02"
    assert day("2026-10-01T23:30:00") == "2026-10-01"


def test_count_net_refund_can_be_negative(setup):
    c, ids, _ = setup
    session = open_cash(c, ids["registers"][0]).json()["id"]
    movement(c, ids, session, amount="5", payment="cartao", kind="reembolso")
    assert (
        c.post(
            f"/api/sessions/{session}/close",
            json={"cash_total": "100", "payments": {"cartao": "-5"}},
        ).status_code
        == 200
    )


def test_store_day_counts_all_manual_and_wintouch_credits_and_debits(setup):
    c, ids, factory = setup
    first = open_cash(c, ids["registers"][0]).json()["id"]
    second = open_cash(c, ids["registers"][1]).json()["id"]
    other = open_cash(c, ids["registers"][2]).json()["id"]
    movement(c, ids, first, amount="10")
    movement(c, ids, second, amount="20")
    movement(c, ids, second, amount="5", kind="despesa")
    movement(c, ids, first, amount="7", payment="cartao")
    movement(c, ids, second, amount="2", payment="cartao", kind="reembolso")
    movement(c, ids, other, amount="100")
    previous_day = movement(c, ids, second, amount="999", payment="cartao").json()["id"]
    with factory.begin() as db:
        session = db.get(CashSession, first)
        store = db.get(Register, session.register_id).store_id
        selected_day = session.sheet_day
        start = datetime.combine(
            datetime.fromisoformat(selected_day).date(), time.min, tzinfo=ZoneInfo("Europe/Lisbon")
        )
        db.get(Movement, previous_day).occurred_at = start - timedelta(minutes=1)
        session.wintouch_scope = store + ":" + selected_day
        session.wintouch_enterprise = "daily-source"
        for direction, value in ((1, 1230), (-1, 300)):
            db.add(
                WintouchSaleRecord(
                    enterprise_id="daily-source",
                    external_id=str(uuid4()),
                    store_id=store,
                    kind="real",
                    active=True,
                    document_date=selected_day,
                    data=json.dumps(
                        {
                            "payment": "Numerário",
                            "payment_code": "NU",
                            "occurred_at": selected_day + "T12:00:00",
                            "direction": direction,
                            "amount_cents": value,
                        }
                    ),
                )
            )
    result = next(s for s in c.get("/api/sessions").json() if s["id"] == first)
    rows = by_key(result)
    assert rows["numerario"]["local_cents"] == 2500
    assert rows["numerario"]["wintouch_cents"] == 930
    assert rows["numerario"]["credits_cents"] == 4230 and rows["numerario"]["debits_cents"] == 800
    assert rows["numerario"]["expected_cents"] == 13430
    assert rows["cartao"]["credits_cents"] == 700 and rows["cartao"]["debits_cents"] == 200
    # The store-wide projection does not pool money available to a register for spending.
    assert result["balance_cents"] == 11930
    assert (
        c.post(
            f"/api/sessions/{first}/close",
            json={"cash_total": "134.30", "payments": {"cartao": "5"}},
        ).status_code
        == 200
    )
    assert movement(c, ids, second, amount="1").status_code == 200
    frozen = next(s for s in c.get("/api/sessions").json() if s["id"] == first)
    assert by_key(frozen)["numerario"]["expected_cents"] == 13430
    assert frozen["balance_cents"] == 11930
