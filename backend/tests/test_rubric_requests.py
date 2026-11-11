"""Natureza das rubricas: motivo legível, histórico por aplicação, pedidos de aprovação do gestor ao administrador
(aprovar aplicando, recusar, cancelar), aviso na barra do menu e notas do Dashboard sobre mudanças recentes."""

import json
from datetime import date

import pytest
from app import dashboard
from app.models import (
    Access,
    Audit,
    BankAccount,
    BankBookEntry,
    Category,
    RubricRequest,
    Store,
    User,
)
from sqlalchemy import select

IVA, TSU = 201852, 48650
FISCAL = {"nature": "fiscal", "in_operating_costs": False, "in_purchases": False, "in_treasury": True, "in_fiscal": True,
          "in_payment_budget": True}


@pytest.fixture
def case(setup, monkeypatch):
    client, _, factory = setup
    monkeypatch.setattr(dashboard, "today", lambda: date(2026, 11, 10))
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        rubrics = {name: Category(name=name) for name in ("IVA", "Impostos")}
        db.add_all(rubrics.values())
        db.flush()
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        admin = db.scalar(select(User).where(User.email == "admin@test.pt"))
        manager = User(name="Gestora", email="gestora@test.pt", role="gestor", password_hash=admin.password_hash)
        db.add(manager)
        db.flush()
        db.add(Access(user_id=manager.id, store_id=store))
        account = BankAccount(store_id=store, name="BPI", bank="BPI", iban_last4="1234")
        db.add(account)
        db.flush()
        for key, rubric, amount, text in (("iva", "IVA", IVA, "PAGAMENTO AO ESTADO IVA"), ("tsu", "Impostos", TSU, "SEGURANCA SOCIAL")):
            db.add(BankBookEntry(account_id=account.id, user_id=admin.id, movement_date="2026-09-21", amount_cents=-amount, kind="payment",
                                 description=text, reference="", request_key="req-" + key, category_id=rubrics[rubric].id, counts_as_cost=True))
        ids = {name: row.id for name, row in rubrics.items()}
    return client, factory, ids


def login(client, email):
    assert client.post("/api/auth/login", json={"email": email, "password": "PasswordTest123!"}).status_code == 200


def simulated(client, changes):
    response = client.post("/api/rubric-nature/simulate", json={"changes": changes})
    assert response.status_code == 200, response.text
    return response.json()["fingerprint"]


def test_reason_must_be_readable_and_history_groups_each_application(case):
    client, _, ids = case
    changes = [{"category_id": ids["IVA"], **FISCAL}]
    fingerprint = simulated(client, changes)
    for reason in ("jjjjjkkjk", "nbnbvc teste", "da da da"):
        response = client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": fingerprint, "reason": reason})
        assert response.status_code == 422, reason
        assert "duas palavras" in response.text
    assert client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": fingerprint,
                                                         "reason": "Separar  IVA dos custos"}).status_code == 200
    back = [{"category_id": ids["IVA"], "nature": "", "in_operating_costs": True, "in_purchases": False, "in_treasury": True,
             "in_fiscal": False, "in_payment_budget": True},
            {"category_id": ids["Impostos"], "nature": "pessoal", "in_operating_costs": True, "in_purchases": False, "in_treasury": True,
             "in_fiscal": False, "in_payment_budget": True}]
    assert client.post("/api/rubric-nature/apply", json={"changes": back, "fingerprint": simulated(client, back),
                                                         "reason": "Repor para comparar"}).status_code == 200
    history = client.get("/api/rubric-nature").json()["history"]
    assert [h["reason"] for h in history] == ["Repor para comparar", "Separar IVA dos custos"]  # espaços normalizados
    assert len(history[0]["changes"]) == 2 and history[0]["user"] == "Admin"
    first = history[1]["changes"][0]
    assert first["name"] == "IVA" and first["before"]["nature"] == "" and first["after"]["nature"] == "fiscal"
    # Notas do Dashboard: as duas mudanças recentes, a mais recente primeiro, e a nota sobre a comparação.
    notes = client.get("/api/dashboard/overview", params={"start": "2026-09-01", "end": "2026-09-30"}).json()["expenses"]["config_notes"]
    assert notes[0]["text"].startswith("Desde ") and notes[0]["text"].endswith("IVA voltou aos custos operacionais.")
    assert any("deixou de contar nos custos operacionais" in n["text"] for n in notes)
    assert notes[-1]["text"].startswith("Os dois períodos comparados usam a configuração atual")


def test_manager_request_approved_by_administrator(case):
    client, factory, ids = case
    changes = [{"category_id": ids["IVA"], **FISCAL}]
    login(client, "gestora@test.pt")
    assert client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": simulated(client, changes),
                                                         "reason": "Separar IVA dos custos"}).status_code == 403
    assert client.post("/api/rubric-nature/requests", json={"kind": "nature", "changes": changes, "reason": "xx"}).status_code == 422
    created = client.post("/api/rubric-nature/requests", json={"kind": "nature", "changes": changes, "reason": "Separar IVA dos custos"})
    assert created.status_code == 200, created.text
    request_id = created.json()["id"]
    assert client.post("/api/rubric-nature/requests", json={"kind": "nature", "changes": changes,
                                                            "reason": "Outra vez o mesmo"}).status_code == 409
    mine = client.get("/api/rubric-nature/requests").json()
    assert not mine["can_decide"] and mine["requests"][0]["mine"] and mine["requests"][0]["status"] == "pendente"
    assert mine["requests"][0]["title"] == "Natureza: IVA → Obrigação fiscal"
    operating = next(v for v in mine["requests"][0]["summary"]["views"] if v["key"] == "operating")
    assert operating["difference_cents"] == -IVA
    assert client.post(f"/api/rubric-nature/requests/{request_id}/reject", json={"reason": "Não concordo"}).status_code == 403
    with factory() as db:
        assert db.get(Category, ids["IVA"]).nature == ""  # Pedir não altera nada.

    login(client, "admin@test.pt")
    alerts = client.get("/api/alerts").json()["groups"]
    group = next(g for g in alerts if g["id"] == "rubricas_pedidos")
    assert group["page"] == "Rubricas" and group["params"] == {"area.rubricas": "natureza"} and "Gestora" in group["items"][0]
    listing = client.get("/api/rubric-nature/requests").json()
    assert listing["can_decide"] and not listing["requests"][0]["mine"]
    applied = client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": simulated(client, changes),
                                                            "reason": "Aprovado: separar IVA", "request_id": request_id})
    assert applied.status_code == 200, applied.text
    with factory() as db:
        row = db.get(RubricRequest, request_id)
        assert (row.status, row.decision_reason) == ("aprovado", "Aprovado: separar IVA")
        audit = db.scalar(select(Audit).where(Audit.action == "rubrica_natureza_alterada"))
        assert json.loads(audit.detail)["request_id"] == request_id
        assert db.scalar(select(Audit).where(Audit.action == "rubrica_pedido_aprovado"))
    assert not any(g["id"] == "rubricas_pedidos" for g in client.get("/api/alerts").json()["groups"])
    # Aprovar outra vez o mesmo pedido é recusado.
    again = [{"category_id": ids["Impostos"], **FISCAL}]
    assert client.post("/api/rubric-nature/apply", json={"changes": again, "fingerprint": simulated(client, again),
                                                         "reason": "Repetir o pedido", "request_id": request_id}).status_code == 409


def test_reject_cancel_and_reclassification_request(case):
    client, factory, ids = case
    login(client, "gestora@test.pt")
    changes = [{"category_id": ids["IVA"], **FISCAL}]
    first = client.post("/api/rubric-nature/requests", json={"kind": "nature", "changes": changes, "reason": "Separar IVA dos custos"}).json()["id"]
    with factory() as db:
        entry = db.scalar(select(BankBookEntry).where(BankBookEntry.request_key == "req-tsu")).id
    payload = {"kind": "reclassification", "items": ["bank:" + entry], "category_id": ids["IVA"], "reason": "TSU estava mal classificada"}
    second = client.post("/api/rubric-nature/requests", json=payload)
    assert second.status_code == 200, second.text
    second = second.json()["id"]
    assert client.post(f"/api/rubric-nature/requests/{second}/cancel").status_code == 200
    assert client.post(f"/api/rubric-nature/requests/{second}/cancel").status_code == 409

    login(client, "admin@test.pt")
    assert client.post(f"/api/rubric-nature/requests/{first}/cancel").status_code == 403  # Só quem pediu cancela.
    assert client.post(f"/api/rubric-nature/requests/{first}/reject", json={"reason": "ok"}).status_code == 422
    assert client.post(f"/api/rubric-nature/requests/{first}/reject", json={"reason": "Falta confirmar com o contabilista"}).status_code == 200
    statuses = {r["id"]: (r["status"], r["decision_reason"]) for r in client.get("/api/rubric-nature/requests").json()["requests"]}
    assert statuses[first] == ("recusado", "Falta confirmar com o contabilista") and statuses[second][0] == "cancelado"
    with factory() as db:
        assert db.get(Category, ids["IVA"]).nature == ""
        assert {a.action for a in db.scalars(select(Audit).where(Audit.action.like("rubrica_pedido_%")))} == {
            "rubrica_pedido_criado", "rubrica_pedido_cancelado", "rubrica_pedido_recusado"}
