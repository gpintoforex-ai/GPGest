import os
from uuid import uuid4

import pytest
from app.db import db_session, engine
from app.main import app, attempts
from app.models import Access, Base, Category, Register, Store, User
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def setup():
    if os.getenv("APP_MODE") != "demonstracao":
        raise RuntimeError("Os testes devem usar uma base de demonstração, nunca uma base real.")
    # Esquema temporário isolado: nunca apagar tabelas da aplicação.
    schema = "test_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA " + schema))
    test_engine = create_engine(engine.url, connect_args={"options": "-csearch_path=" + schema})
    Base.metadata.create_all(test_engine)
    factory = sessionmaker(test_engine, expire_on_commit=False)
    with factory.begin() as db:
        store = Store(name="Teste")
        other = Store(name="Outra")
        db.add_all([store, other])
        db.flush()
        admin = User(
            name="Admin",
            email="admin@test.pt",
            role="administrador",
            password_hash=PasswordHash.recommended().hash("PasswordTest123!"),
        )
        operator = User(
            name="Operador",
            email="operator@test.pt",
            role="operador",
            password_hash=PasswordHash.recommended().hash("PasswordTest123!"),
        )
        reader = User(
            name="Consulta",
            email="reader@test.pt",
            role="consulta",
            password_hash=PasswordHash.recommended().hash("PasswordTest123!"),
        )
        db.add_all([admin, operator, reader])
        db.flush()
        db.add_all(
            [
                Access(user_id=operator.id, store_id=store.id),
                Access(user_id=reader.id, store_id=store.id),
            ]
        )
        registers = [
            Register(name="A", store_id=store.id),
            Register(name="B", store_id=store.id),
            Register(name="C", store_id=other.id),
        ]
        category = Category(name="Geral")
        db.add_all(registers + [category])
        db.flush()
        ids = {
            "registers": [r.id for r in registers],
            "category": category.id,
            "operator": operator.id,
        }

    def override():
        with factory() as db:
            yield db

    app.dependency_overrides[db_session] = override
    attempts.clear()
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/auth/login",
                json={"email": "admin@test.pt", "password": "PasswordTest123!"},
            ).status_code
            == 200
        )
        yield client, ids, factory
    app.dependency_overrides.clear()
    test_engine.dispose()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA " + schema + " CASCADE"))


def open_cash(c, register, amount="100"):
    return c.post("/api/sessions", json={"register_id": register, "opening": amount})


def movement(c, ids, session, kind="venda", payment="numerario", amount="10", key=None):
    from datetime import datetime, timezone

    return c.post(
        "/api/movements",
        json={
            "session_id": session,
            "category_id": ids["category"],
            "kind": kind,
            "payment": payment,
            "amount": amount,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "request_key": key or str(uuid4()),
        },
    )
