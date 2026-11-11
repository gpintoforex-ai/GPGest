"""Alertas das Obrigações: avisos internos (barra e dashboard), email por dupla adesão, sem repetir alertas,
falhas repetíveis e envios incertos nunca repetidos."""

import email
import email.policy
from datetime import date
from types import SimpleNamespace

import pytest
from app import email_settings, email_transport, obligations
from app.models import ObligationNotification, User
from sqlalchemy import select


@pytest.fixture
def case(setup, monkeypatch):
    monkeypatch.setattr(obligations, "today", lambda: date(2026, 11, 11))
    client, _, factory = setup
    for body in ({"category": "fiscal", "title": "IVA mensal", "periodicity": "unica", "start_date": "2026-11-18", "amount_cents": 50000, "alert_days": [7, 0]},
                 {"category": "seguro", "title": "Seguro multirriscos", "periodicity": "unica", "start_date": "2026-11-05", "alert_days": [30, 7, 0]},
                 {"category": "outro", "title": "Licença", "periodicity": "unica", "start_date": "2027-03-01", "alert_days": [30]}):
        assert client.post("/api/obligations", json=body).status_code == 200
    return client, factory


class FakeSMTP:
    def __init__(self):
        self.sent, self.fail_next = [], None

    def connect(self, config, password):
        return self, None

    def submit(self, client, sender, recipients, raw, before_data):
        if self.fail_next == "before":
            self.fail_next = None
            raise email_transport.DeliveryFailure("Servidor indisponível")
        before_data()
        if self.fail_next == "uncertain":
            self.fail_next = None
            raise email_transport.DeliveryFailure("Ligação perdida", uncertain=True)
        message = email.message_from_bytes(raw, policy=email.policy.default)
        self.sent.append((recipients, f"{message['Subject']}\n{message.get_body().get_content()}"))


@pytest.fixture
def smtp(monkeypatch):
    fake = FakeSMTP()
    row = SimpleNamespace(revision=1, password_ciphertext="")
    monkeypatch.setattr(email_settings, "configuration", lambda db: (row, {"enabled": True, "sender_email": "avisos@empresa.pt", "sender_name": "IAZELA",
                                                                            "host": "smtp.test", "port": 587, "security": "starttls", "username": ""}))
    monkeypatch.setattr(email_transport, "connect", fake.connect)
    monkeypatch.setattr(email_transport, "submit", fake.submit)
    monkeypatch.setattr(email_transport, "disconnect", lambda client: None)
    return fake


def test_internal_alerts_show_overdue_and_next_7_days_until_read(case):
    client, _ = case
    groups = {g["id"]: g for g in client.get("/api/alerts").json()["groups"]}
    deadlines = groups["prazos"]
    assert deadlines["count"] == 2 and deadlines["tone"] == "danger" and deadlines["page"] == "Obrigações e Prazos"
    assert any("Seguro multirriscos · vencido a 05/11/2026" == item for item in deadlines["items"])
    attention = {n["key"]: n for n in client.get("/api/dashboard/overview").json()["attention"]}
    assert attention["obligations_overdue"]["severity"] == "critical" and "Seguro multirriscos" in attention["obligations_overdue"]["values"]
    assert "500,00 €" in attention["obligations_7_days"]["values"]
    # Confirmar a leitura retira o prazo dos avisos da barra (continua na lista e no painel).
    occurrences = client.get("/api/obligations/occurrences").json()
    for item in occurrences:
        if item["due_date"] <= "2026-11-18":
            assert client.post(f"/api/obligations/occurrences/{item['id']}/read", json={"revision": item["revision"]}).status_code == 200
    assert "prazos" not in {g["id"] for g in client.get("/api/alerts").json()["groups"]}


def test_email_needs_both_authorizations_and_never_repeats_an_alert(case, smtp):
    client, factory = case
    assert client.post("/api/obligations/notifications/send").status_code == 409  # Empresa sem lembretes.
    assert client.put("/api/obligations/notifications/company", json={"enabled": True}).status_code == 200
    state = client.get("/api/obligations/notifications").json()
    assert state["company_enabled"] and not state["my_preference"] and state["pending_alerts"] == 0  # Ninguém aderiu.
    assert client.post("/api/obligations/notifications/send").json()["emails"] == 0
    mine = client.put("/api/obligations/notifications/me", json={"obligations_email": True}).json()
    assert mine["my_preference"] and {p["alert"] for p in mine["my_pending"]} == {"7", "vencido"}  # A licença (30 dias) ainda não.
    sent = client.post("/api/obligations/notifications/send").json()
    assert sent == {"emails": 1, "alerts": 2, "failed": 0, "uncertain": 0, "errors": []}
    recipients, raw = smtp.sent[0]
    assert recipients == ["admin@test.pt"] and "IVA mensal: até 18/11/2026" in raw and "vencido desde 05/11/2026" in raw
    assert client.post("/api/obligations/notifications/send").json()["emails"] == 0  # Sem repetição.
    with factory() as db:
        assert {n.status for n in db.scalars(select(ObligationNotification))} == {"enviado"}


def test_failures_before_sending_retry_but_uncertain_deliveries_do_not(case, smtp):
    client, factory = case
    client.put("/api/obligations/notifications/company", json={"enabled": True})
    client.put("/api/obligations/notifications/me", json={"obligations_email": True})
    smtp.fail_next = "before"
    failed = client.post("/api/obligations/notifications/send").json()
    assert failed["failed"] == 1 and failed["emails"] == 0 and "Servidor indisponível" in failed["errors"][0]
    retried = client.post("/api/obligations/notifications/send").json()
    assert retried["emails"] == 1 and retried["alerts"] == 2
    # Novo prazo de hoje: envio incerto fica registado e não se repete automaticamente.
    assert client.post("/api/obligations", json={"category": "outro", "title": "Renovar alvará", "periodicity": "unica", "start_date": "2026-11-11",
                                                 "alert_days": [0]}).status_code == 200
    smtp.fail_next = "uncertain"
    uncertain = client.post("/api/obligations/notifications/send").json()
    assert uncertain["uncertain"] == 1
    assert client.post("/api/obligations/notifications/send").json()["emails"] == 0
    with factory() as db:
        assert sorted(n.status for n in db.scalars(select(ObligationNotification))) == ["enviado", "enviado", "incerto"]


def test_responsible_receives_only_if_opted_in_and_permissions(case, smtp):
    client, factory = case
    with factory.begin() as db:
        manager = User(name="Gestora", email="gestora@test.pt", role="gestor", password_hash=db.scalar(select(User.password_hash).limit(1)))
        db.add(manager)
        db.flush()
        manager_id = manager.id
    item = next(o for o in client.get("/api/obligations/occurrences").json() if o["title"] == "IVA mensal")
    ob = client.get(f"/api/obligations/{item['obligation_id']}").json()
    body = {k: ob[k] for k in ("category", "kind", "title", "periodicity", "start_date", "alert_days", "amount_cents")}
    assert client.put(f"/api/obligations/{ob['id']}", json={**body, "responsible_id": manager_id, "revision": ob["revision"]}).status_code == 200
    client.put("/api/obligations/notifications/company", json={"enabled": True})
    client.put("/api/obligations/notifications/me", json={"obligations_email": True})
    result = client.post("/api/obligations/notifications/send").json()
    # Só o seguro (sem responsável) vai para o administrador; o IVA é da gestora, que não aderiu.
    assert result["alerts"] == 1 and "Seguro multirriscos" in smtp.sent[0][1] and "IVA mensal" not in smtp.sent[0][1]
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/obligations/notifications").status_code == 403
    assert "prazos" not in {g["id"] for g in client.get("/api/alerts").json()["groups"]}
