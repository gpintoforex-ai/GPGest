"""Query budgets for save/refresh paths, using isolated test data only."""
import json
from types import SimpleNamespace

from sqlalchemy import event, select

from app import db as database, main
from app.cash_sheet import daily_enterprise
from app.models import Access, CashSession, User, now
from app.operating_modes import policy
from conftest import open_cash
from test_cash_local import declared, local


def capture(factory):
    statements = []
    engine = factory.kw["bind"]

    def collect(conn, cursor, statement, params, context, executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", collect)
    return statements, lambda: event.remove(engine, "before_cursor_execute", collect)


def test_bootstrap_access_queries_do_not_grow_with_the_team(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        template = db.scalar(select(User))
        register = db.get(main.Register, ids["registers"][0])
        for i in range(25):
            user = User(name=f"Consulta {i}", email=f"budget{i}@test.pt", role="consulta", password_hash=template.password_hash)
            db.add(user)
            db.flush()
            db.add(Access(user_id=user.id, store_id=register.store_id))
    statements, stop = capture(factory)
    try:
        response = client.get("/api/bootstrap")
    finally:
        stop()
    assert response.status_code == 200
    users = response.json()["users"]
    assert len(users) == 28
    assert all(len(u["store_ids"]) == 1 for u in users if u["email"].startswith("budget"))
    assert len(statements) <= 12, statements


def test_closed_sheet_list_uses_snapshots_without_per_sheet_queries(local):
    client, ids, factory = local
    identifier = open_cash(client, ids["registers"][0]).json()["id"]
    response = client.post(f"/api/sessions/{identifier}/close", json={**declared(), "cash_total": "100"})
    assert response.status_code == 200, response.text
    with factory.begin() as db:
        original = db.get(CashSession, identifier)
        for _ in range(30):
            db.add(CashSession(register_id=original.register_id, operator_id=original.operator_id,
                               opening_cents=original.opening_cents, closed_at=now(),
                               count_data=original.count_data, counted_cents=original.counted_cents))
        expected = json.loads(original.count_data)["summary"]
    statements, stop = capture(factory)
    try:
        response = client.get("/api/sessions")
    finally:
        stop()
    assert response.status_code == 200
    assert len(response.json()) == 31
    assert all(row["sheet"]["rows"] == expected for row in response.json())
    assert len(statements) <= 4, statements


def test_request_company_reuses_only_the_selected_company(monkeypatch):
    def unexpected():
        raise AssertionError("Repeated platform round trip within one request")

    monkeypatch.setattr(database, "next_platform_session", unexpected)
    selected = SimpleNamespace(id="company-a", operating_mode="local", erp_provider="none",
                               api_active=0, connector_writes=0, api_enterprise="enterprise-a")
    db = SimpleNamespace(info={"company": {"id": "company-a"}, "selected_company": selected})
    assert policy(db)["mode"] == "local"
    assert daily_enterprise(db, SimpleNamespace()) == "enterprise-a"
    # A later request loads a fresh company: configuration is never cached globally.
    next_company = SimpleNamespace(**{**selected.__dict__, "operating_mode": "integrated", "api_active": 1, "erp_provider": "wintouch", "api_enterprise": "enterprise-b"})
    next_request = SimpleNamespace(info={"company": {"id": "company-a"}, "selected_company": next_company})
    assert policy(next_request)["active"] is True
    assert daily_enterprise(next_request, SimpleNamespace()) == "enterprise-b"
    # A mismatched company snapshot must never be used for another tenant.
    loaded = SimpleNamespace(id="company-b")
    class Core:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, model, identifier):
            assert identifier == "company-b"
            return loaded
    monkeypatch.setattr(database, "next_platform_session", Core)
    db.info["company"]["id"] = "company-b"
    assert database.request_company(db) is loaded
