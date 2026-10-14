from contextlib import contextmanager
from types import SimpleNamespace

from app import main
from app.models import CashSession, Register
from conftest import open_cash


def test_cash_button_imports_only_sheet_date_and_store(setup, monkeypatch):
    client, ids, factory = setup
    session_id = open_cash(client, ids["registers"][0]).json()["id"]
    with factory() as db:
        sheet = db.get(CashSession, session_id)
        date = sheet.sheet_day
        store = db.get(Register, sheet.register_id).store_id

    @contextmanager
    def core():
        yield SimpleNamespace(get=lambda *_: SimpleNamespace(erp_provider="wintouch", api_enterprise="demo", api_active=1, operating_mode="integrated"))

    monkeypatch.setattr(main.database, "next_platform_session", core)
    # Test company context and the existing adapter, without accessing the real ERP.
    old_db = main.app.dependency_overrides[main.db_session]

    def context_db():
        with factory() as db:
            db.info["company"] = {"id": "demo"}
            yield db

    main.app.dependency_overrides[main.db_session] = context_db
    calls = []

    def importer(data, user, db):
        calls.append(data)
        return {"status": "concluida", "read_count": 3, "inserted_count": 1, "updated_count": 2}

    monkeypatch.setattr(main, "company_sales_import", importer)
    monkeypatch.setattr(main, "sheet_source", lambda *_: (store + ":" + date, "demo"))
    try:
        response = client.post(
            f"/api/sessions/{session_id}/import-erp", json={"confirm_demo": True}
        )
        assert response.status_code == 200, response.text
        assert len(calls) == 1
        assert (
            calls[0].store_id == store
            and calls[0].start_date.isoformat() == date
            and calls[0].end_date.isoformat() == date
        )
        assert calls[0].confirm_demo is True
        client.post("/api/auth/logout", json={})
        client.post(
            "/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}
        )
        assert client.post(f"/api/sessions/{session_id}/import-erp", json={}).status_code == 403
        assert len(calls) == 1
    finally:
        main.app.dependency_overrides[main.db_session] = old_db


def test_closed_sheet_rejects_import_without_calling_adapter(setup, monkeypatch):
    client, ids, _ = setup
    session = open_cash(client, ids["registers"][0]).json()["id"]
    assert (
        client.post(f"/api/sessions/{session}/close", json={"cash_total": "100"}).status_code == 200
    )
    monkeypatch.setattr(
        main,
        "company_sales_import",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not import")),
    )
    assert client.post(f"/api/sessions/{session}/import-erp", json={}).status_code == 409
