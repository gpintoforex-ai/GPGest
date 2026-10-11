import json
from uuid import uuid4

from app.cash_sheet import day
from app.models import CashSession, Register, WintouchSaleRecord
from conftest import movement, open_cash


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
