import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from app.db import db_session
from app.main import app
from app.models import Register, WintouchSaleRecord


@pytest.fixture
def opening_case(setup, monkeypatch):
    client, ids, factory = setup

    @contextmanager
    def core():
        yield SimpleNamespace(get=lambda *_: SimpleNamespace(api_enterprise="tenant"))

    monkeypatch.setattr("app.db.next_platform_session", core)

    def override():
        with factory() as db:
            db.info["company"] = {"id": "test"}
            yield db

    app.dependency_overrides[db_session] = override
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        for external, amount, direction in [("fund", 4000, 1), ("expense", 500, -1)]:
            db.add(
                WintouchSaleRecord(
                    enterprise_id="tenant",
                    external_id=external,
                    store_id=store,
                    kind="real",
                    document_date="2026-10-12",
                    data=json.dumps(
                        {
                            "source_kind": "cash_movement",
                            "amount_cents": amount,
                            "direction": direction,
                            "payment_code": "NU",
                            "payment": "Numerário",
                            "occurred_at": "2026-10-12T09:00:00",
                            "document": external,
                        }
                    ),
                )
            )
    return client, ids, factory


def test_opening_entry_is_suggested_and_not_counted_twice(opening_case):
    client, ids, _ = opening_case
    register = ids["registers"][0]
    suggestion = client.get(
        f"/api/registers/{register}/opening-suggestion?sheet_day=2026-10-12"
    ).json()
    assert suggestion["candidate"]["amount_cents"] == 4000
    source = suggestion["candidate"]["id"]
    response = client.post(
        "/api/sessions",
        json={
            "register_id": register,
            "sheet_day": "2026-10-12",
            "opening": "40",
            "opening_wintouch_id": source,
        },
    )
    assert response.status_code == 200, response.text
    session = response.json()["id"]
    cash = next(r for r in client.get("/api/sessions").json() if r["id"] == session)
    assert cash["balance_cents"] == 3500
    summary = next(r for r in cash["sheet"]["rows"] if r["key"] == "numerario")
    assert summary["credits_cents"] == 0 and summary["debits_cents"] == 500
    assert summary["expected_cents"] == 3500
    assert (
        client.post(
            f"/api/sessions/{session}/count", json={"cash_total": "35", "cash_mode": "total"}
        ).status_code
        == 200
    )
    cash = next(r for r in client.get("/api/sessions").json() if r["id"] == session)
    assert cash["balance_cents"] == 3500 and cash["count_data"]["opening_wintouch_id"] == source
    assert (
        client.post(
            "/api/sessions",
            json={
                "register_id": ids["registers"][1],
                "sheet_day": "2026-10-12",
                "opening": "40",
                "opening_wintouch_id": source,
            },
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/registers/{register}/opening-suggestion?sheet_day=2026-10-13").json()[
            "candidate"
        ]
        is None
    )
    closed = client.post(
        f"/api/sessions/{session}/close", json={"cash_total": "35", "cash_mode": "total"}
    )
    assert closed.status_code == 200, closed.text
    assert json.loads(closed.json()["count_data"])["opening_wintouch_id"] == source


def test_ambiguous_entries_are_not_selected_automatically(opening_case):
    client, ids, factory = opening_case
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        db.add(
            WintouchSaleRecord(
                enterprise_id="tenant",
                external_id="another-fund",
                store_id=store,
                kind="real",
                document_date="2026-10-12",
                data=json.dumps(
                    {
                        "source_kind": "cash_movement",
                        "amount_cents": 1000,
                        "direction": 1,
                        "payment_code": "NU",
                        "occurred_at": "2026-10-12T11:00:00",
                    }
                ),
            )
        )
    result = client.get(
        f"/api/registers/{ids['registers'][0]}/opening-suggestion?sheet_day=2026-10-12"
    ).json()
    assert result["ambiguous"] and result["candidate"] is None
