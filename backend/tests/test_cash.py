from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from conftest import movement, open_cash


def test_cash_card_and_exact_values(setup):
    c, ids, _ = setup
    s = open_cash(c, ids["registers"][0]).json()["id"]
    assert movement(c, ids, s, amount="0.10").status_code == 200
    assert movement(c, ids, s, amount="0.20").status_code == 200
    assert movement(c, ids, s, payment="cartao", amount="50").status_code == 200
    assert movement(c, ids, s, kind="despesa", amount="20").status_code == 200
    assert c.get("/api/sessions").json()[0]["balance_cents"] == 8030
    assert movement(c, ids, s, amount="1.001").status_code == 422
    assert movement(c, ids, s, amount="NaN").status_code == 422


def test_close_difference_lock_reopen_reverse(setup):
    c, ids, _ = setup
    s = open_cash(c, ids["registers"][0]).json()["id"]
    m = movement(c, ids, s).json()["id"]
    assert (
        c.post(
            "/api/sessions/" + s + "/close", json={"counts": {"10000": 1}, "reason": ""}
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/api/sessions/" + s + "/close",
            json={"counts": {"10000": 1, "1000": 1}, "reason": ""},
        ).status_code
        == 200
    )
    assert movement(c, ids, s).status_code == 409
    pdf = c.get("/api/sessions/" + s + "/report")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert (
        c.post("/api/movements/" + m + "/reverse", json={"reason": "Erro de registo"}).status_code
        == 409
    )
    assert (
        c.post("/api/sessions/" + s + "/reopen", json={"reason": "Correção autorizada"}).status_code
        == 200
    )
    assert (
        c.post("/api/movements/" + m + "/reverse", json={"reason": "Erro de registo"}).status_code
        == 200
    )
    assert (
        c.post(
            "/api/movements/" + m + "/reverse", json={"reason": "Repetição do estorno"}
        ).status_code
        == 409
    )
    assert c.get("/api/sessions").json()[0]["balance_cents"] == 10000
    assert len(c.get("/api/movements").json()) == 2


def test_duplicates_concurrent_open(setup):
    c, ids, _ = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: open_cash(c, ids["registers"][0]), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    s = next(r.json()["id"] for r in results if r.status_code == 200)
    key = str(uuid4())
    assert movement(c, ids, s, key=key).status_code == 200
    assert movement(c, ids, s, key=key).status_code == 409
    assert c.get("/api/sessions").json()[0]["balance_cents"] == 11000


def test_transfer_and_insufficient_cash(setup):
    c, ids, _ = setup
    s = open_cash(c, ids["registers"][0]).json()["id"]
    d = open_cash(c, ids["registers"][1], "0").json()["id"]
    assert movement(c, ids, s, kind="despesa", amount="101").status_code == 409
    payload = {
        "source_id": s,
        "destination_id": d,
        "amount": "30",
        "category_id": ids["category"],
        "reason": "Reforço da caixa B",
        "request_key": str(uuid4()),
    }
    assert c.post("/api/transfers", json=payload).status_code == 200
    assert c.post("/api/transfers", json=payload).status_code == 409
    assert sum(s["balance_cents"] for s in c.get("/api/sessions").json()) == 10000
    assert c.get("/api/dashboard").json()["management_result"] is None


def test_permissions_and_store_scope(setup):
    c, ids, _ = setup
    other = open_cash(c, ids["registers"][2]).json()["id"]
    c.post("/api/auth/logout", json={})
    assert c.get("/api/sessions").status_code == 401
    c.post(
        "/api/auth/login",
        json={"email": "reader@test.pt", "password": "PasswordTest123!"},
    )
    assert open_cash(c, ids["registers"][0]).status_code == 403
    assert all(s["id"] != other for s in c.get("/api/sessions").json())
    assert c.get("/api/sessions/" + other + "/report").status_code == 403
    c.post("/api/auth/logout", json={})
    c.post(
        "/api/auth/login",
        json={"email": "operator@test.pt", "password": "PasswordTest123!"},
    )
    s = open_cash(c, ids["registers"][0]).json()["id"]
    assert movement(c, ids, s).status_code == 200
    assert open_cash(c, ids["registers"][2]).status_code == 403
    assert c.post("/api/stores", json={"name": "Proibida"}).status_code == 403


def test_attachments_private_and_closed(setup):
    c, ids, _ = setup
    s = open_cash(c, ids["registers"][0]).json()["id"]
    m = movement(c, ids, s).json()["id"]
    assert (
        c.post(
            "/api/movements/" + m + "/attachments",
            files={"file": ("evil.pdf", b"not pdf", "application/pdf")},
        ).status_code
        == 422
    )
    raw = b"%PDF-1.4\nexample"
    r = c.post(
        "/api/movements/" + m + "/attachments",
        files={"file": ("test.pdf", raw, "application/pdf")},
    )
    assert r.status_code == 200
    a = r.json()["id"]
    assert c.get("/api/attachments/" + a).content == raw
    c.post(
        "/api/sessions/" + s + "/close",
        json={"counts": {"10000": 1, "1000": 1}, "reason": ""},
    )
    assert (
        c.post(
            "/api/movements/" + m + "/attachments",
            files={"file": ("test.pdf", raw, "application/pdf")},
        ).status_code
        == 409
    )
    c.post("/api/auth/logout", json={})
    assert c.get("/api/attachments/" + a).status_code == 401


def test_concurrent_spending(setup):
    c, ids, _ = setup
    s = open_cash(c, ids["registers"][0]).json()["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: movement(c, ids, s, kind="despesa", amount="80"), range(2))
        )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert c.get("/api/sessions").json()[0]["balance_cents"] == 2000


def test_handover_responsibility_and_reopening_permission(setup):
    c, ids, _ = setup
    s = open_cash(c, ids["registers"][0]).json()["id"]
    c.post("/api/auth/logout", json={})
    c.post(
        "/api/auth/login",
        json={"email": "operator@test.pt", "password": "PasswordTest123!"},
    )
    assert movement(c, ids, s).status_code == 403
    c.post("/api/auth/logout", json={})
    c.post(
        "/api/auth/login",
        json={"email": "admin@test.pt", "password": "PasswordTest123!"},
    )
    assert (
        c.post(
            "/api/sessions/" + s + "/handover",
            json={
                "operator_id": ids["operator"],
                "reason": "Passagem ao novo operador",
            },
        ).status_code
        == 200
    )
    c.post("/api/auth/logout", json={})
    c.post(
        "/api/auth/login",
        json={"email": "operator@test.pt", "password": "PasswordTest123!"},
    )
    assert movement(c, ids, s).status_code == 200
    assert (
        c.post(
            "/api/sessions/" + s + "/close",
            json={"counts": {"10000": 1, "1000": 1}, "reason": ""},
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/api/sessions/" + s + "/reopen",
            json={"reason": "Operador sem autorização"},
        ).status_code
        == 403
    )


def test_password_sessions_and_origin_protection(setup):
    from app.main import app
    from fastapi.testclient import TestClient

    c, ids, _ = setup
    assert (
        c.post(
            "/api/stores",
            json={"name": "Tentativa externa"},
            headers={"Origin": "https://example.org"},
        ).status_code
        == 403
    )
    with TestClient(app) as other:
        assert (
            other.post(
                "/api/auth/login",
                json={"email": "admin@test.pt", "password": "PasswordTest123!"},
            ).status_code
            == 200
        )
        assert (
            c.post(
                "/api/auth/password",
                json={
                    "current_password": "wrong",
                    "new_password": "AnotherPassword123!",
                },
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/api/auth/password",
                json={
                    "current_password": "PasswordTest123!",
                    "new_password": "AnotherPassword123!",
                },
            ).status_code
            == 200
        )
        assert other.get("/api/bootstrap").status_code == 401
        assert c.get("/api/bootstrap").status_code == 200
