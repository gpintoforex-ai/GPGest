import inspect
import os
import re
from uuid import uuid4

import pytest
from app.db import db_session, engine
from app.main import app, attempts
from app.models import Access, Base, Category, Register, Store, User
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Argon2 é lento de propósito: calcular o hash de teste uma única vez.
PASSWORD_HASH = PasswordHash.recommended().hash("PasswordTest123!")


DDL_IN_TEST = re.compile(
    r"current_schema\(\)|__table__\.drop|Operations\.context|ALTER\s+TABLE|DROP\s+(TABLE|COLUMN)",
    re.IGNORECASE,
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "fresh_schema: usar um esquema próprio (testes que alteram a estrutura das tabelas)"
    )


def pytest_collection_modifyitems(items):
    # Testes de migração alteram tabelas: detetá-los pelo código e dar-lhes um esquema próprio,
    # para não estragarem o esquema partilhado pelos restantes.
    for item in items:
        function = getattr(item, "function", None)
        try:
            source = inspect.getsource(function) if function else ""
        except (OSError, TypeError):
            source = ""
        if DDL_IN_TEST.search(source):
            item.add_marker(pytest.mark.fresh_schema)


def require_demo():
    if os.getenv("APP_MODE") != "demonstracao":
        raise RuntimeError("Os testes devem usar uma base de demonstração, nunca uma base real.")


def create_test_schema():
    # Esquema temporário isolado: nunca apagar tabelas da aplicação.
    schema = "test_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA " + schema))
    test_engine = create_engine(engine.url, connect_args={"options": "-csearch_path=" + schema})
    Base.metadata.create_all(test_engine)
    return schema, test_engine


def drop_test_schema(schema, test_engine):
    test_engine.dispose()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA " + schema + " CASCADE"))


EMPTY_TABLES = "; ".join(
    'DELETE FROM "' + table.name + '"' for table in reversed(Base.metadata.sorted_tables)
)


@pytest.fixture(scope="session")
def shared_schema():
    # Criar as 86 tabelas custa ~2 s; fazê-lo uma vez por sessão e esvaziar entre testes.
    require_demo()
    schema, test_engine = create_test_schema()
    yield schema, test_engine
    drop_test_schema(schema, test_engine)


@pytest.fixture
def setup(request):
    require_demo()
    fresh = request.node.get_closest_marker("fresh_schema") is not None
    if fresh:
        schema, test_engine = create_test_schema()
    else:
        schema, test_engine = request.getfixturevalue("shared_schema")
        # DELETE em lote (filhas antes das mães) é muito mais rápido do que TRUNCATE em
        # tabelas pequenas; os identificadores são UUID, sem sequências a reiniciar.
        with test_engine.begin() as conn:
            conn.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            conn.exec_driver_sql(EMPTY_TABLES)
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
            password_hash=PASSWORD_HASH,
        )
        operator = User(
            name="Operador",
            email="operator@test.pt",
            role="operador",
            password_hash=PASSWORD_HASH,
        )
        reader = User(
            name="Consulta",
            email="reader@test.pt",
            role="consulta",
            password_hash=PASSWORD_HASH,
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
    if fresh:
        drop_test_schema(schema, test_engine)


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
