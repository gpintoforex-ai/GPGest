import importlib
import json
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import Audit, Employee, EmployeeStore, Register, User
from sqlalchemy import func, inspect, select, text
from test_platform import create, enter, platform


def stores(factory, ids):
    with factory() as db:
        return [db.get(Register, ids["registers"][index]).store_id for index in (0, 2)]


def test_employee_crud_does_not_create_credentials_and_is_audited(setup):
    c, ids, factory = setup
    a, b = stores(factory, ids)
    with factory() as db:
        before = db.scalar(select(func.count()).select_from(User))
    response = c.post("/api/employees", json={"name": "  Funcionário sem acesso  ", "email": " CONTACTO@EXEMPLO.PT ", "phone": " 912345678 ", "store_ids": [a, b, a]})
    assert response.status_code == 200, response.text
    created = response.json()
    identifier = created["id"]
    assert created["name"] == "Funcionário sem acesso"
    assert created["email"] == "contacto@exemplo.pt" and created["phone"] == "912345678"
    assert set(created["store_ids"]) == {a, b} and created["active"] is True
    assert "password_hash" not in created and "role" not in created
    assert c.get("/api/employees").json() == [created]
    assert identifier not in {user["id"] for user in c.get("/api/bootstrap").json()["users"]}
    edited = c.post(f"/api/employees/{identifier}", json={"name": "Nome atualizado", "store_ids": [b]} )
    assert edited.status_code == 200, edited.text
    assert edited.json()["email"] == edited.json()["phone"] == ""
    assert edited.json()["store_ids"] == [b]
    for active in (False, True):
        result = c.post(f"/api/employees/{identifier}/status", json={"active": active})
        assert result.status_code == 200 and result.json()["active"] is active
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == before
        assert db.scalar(select(func.count()).select_from(Employee)) == 1
        assert db.scalar(select(func.count()).select_from(EmployeeStore)) == 1
        events = list(db.scalars(select(Audit).where(Audit.target_id == identifier)))
        assert {event.action for event in events} == {"funcionario_criado", "funcionario_atualizado", "funcionario_inativado", "funcionario_ativado"}
        assert next(json.loads(event.detail) for event in events if event.action == "funcionario_criado")["store_ids"] == sorted([a, b])


def test_employee_validation_and_optional_contacts(setup):
    c, ids, factory = setup
    a, _ = stores(factory, ids)
    payload = {"name": "Pessoa", "store_ids": [a]}
    for update in ({"name": " "}, {"store_ids": []}, {"store_ids": ["other-company-store"]}, {"email": "email inválido"}, {"password": "PasswordTest123!"}):
        assert c.post("/api/employees", json={**payload, **update}).status_code == 422
    assert c.post("/api/employees/missing", json=payload).status_code == 404
    assert c.post("/api/employees/missing/status", json={"active": False}).status_code == 404
    created = c.post("/api/employees", json=payload)
    assert created.status_code == 200, created.text
    assert c.post(f"/api/employees/{created.json()['id']}/status", json={"active": "false"}).status_code == 422
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Employee)) == 1


@pytest.mark.parametrize("role,email", [("operador", "operator@test.pt"), ("consulta", "reader@test.pt"), ("gestor", "operator@test.pt")])
def test_employee_scope_and_admin_only_management(setup, role, email):
    c, ids, factory = setup
    a, b = stores(factory, ids)
    first = c.post("/api/employees", json={"name": "Loja autorizada", "store_ids": [a]}).json()
    c.post("/api/employees", json={"name": "Outra loja", "store_ids": [b]})
    with factory.begin() as db:
        db.scalar(select(User).where(User.email == email)).role = role
    c.post("/api/auth/logout", json={})
    assert c.post("/api/auth/login", json={"email": email, "password": "PasswordTest123!"}).status_code == 200
    assert [row["id"] for row in c.get("/api/employees").json()] == [first["id"]]
    payload = {"name": "Sem permissão", "store_ids": [a]}
    assert c.post("/api/employees", json=payload).status_code == 403
    assert c.post(f"/api/employees/{first['id']}", json=payload).status_code == 403
    assert c.post(f"/api/employees/{first['id']}/status", json={"active": False}).status_code == 403


def test_employees_are_tenant_scoped(platform):
    c, _ = platform
    a, b = create(c, "equipa-a"), create(c, "equipa-b")
    c.cookies.clear()
    boot = enter(c, a["slug"])
    payload = {"name": "Equipa A", "store_ids": [boot["stores"][0]["id"]]}
    created = c.post("/api/employees", json=payload)
    assert created.status_code == 200, created.text
    identifier = created.json()["id"]
    boot_b = enter(c, b["slug"])
    assert c.get("/api/employees").json() == []
    assert c.post("/api/employees", json=payload).status_code == 422
    payload["store_ids"] = [boot_b["stores"][0]["id"]]
    assert c.post(f"/api/employees/{identifier}", json=payload).status_code == 404
    assert c.post(f"/api/employees/{identifier}/status", json={"active": False}).status_code == 404
    enter(c, a["slug"])
    assert c.get("/api/employees").json()[0]["id"] == identifier
    assert c.post("/api/platform/login", json={"email": "master@test.pt", "password": "MasterPassword123!"}).status_code == 200
    reset = c.post(f"/api/platform/companies/{a['id']}/reset-data", json={"confirmation": a["nif"], "acknowledged": True})
    assert reset.status_code == 200, reset.text
    enter(c, a["slug"])
    assert c.get("/api/employees").json()[0]["id"] == identifier


def test_employee_migration_is_idempotent_and_preserves_users(setup, monkeypatch):
    _, _, factory = setup
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    migration = importlib.import_module("migrations.versions.0037_employees")
    with factory.kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"test_[0-9a-f]{32}", schema)
        users = connection.scalar(select(func.count()).select_from(User))
        EmployeeStore.__table__.drop(connection)
        Employee.__table__.drop(connection)
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert {"employees", "employee_stores"} <= set(inspect(connection).get_table_names(schema=schema))
        assert connection.scalar(text(f'SELECT count(*) FROM "{schema}".users')) == users
