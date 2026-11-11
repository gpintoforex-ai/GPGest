"""Contratos: prazo de denúncia (não só a renovação), fidelização, renovação automática e manual, pagamentos e
decisões explícitas (nunca automáticas), sobre o motor de obrigações."""

from datetime import date

import pytest
from app import obligations
from app.models import Audit, Category, Obligation, ObligationOccurrence
from sqlalchemy import func, select


@pytest.fixture
def case(setup, monkeypatch):
    monkeypatch.setattr(obligations, "today", lambda: date(2026, 11, 10))
    client, _, factory = setup
    with factory.begin() as db:
        telecom = Category(name="Comunicações")
        db.add(telecom)
        db.flush()
        category = telecom.id
    return client, factory, category


def body(category, **changes):
    return {"entity_name": "MEO", "contract_type": "Telecomunicações", "number": "C-123", "start_date": "2025-01-01", "end_date": "2026-12-31",
            "fidelization_end": "2026-12-31", "auto_renewal": True, "renewal_months": 12, "notice_days": 30, "amount_cents": 5100,
            "payment_periodicity": "mensal", "first_payment_date": "2025-01-10", "category_id": category, "alert_days": [60, 30, 7], **changes}


def occurrences(factory, obligation_id):
    with factory() as db:
        return [o.due_date for o in db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id == obligation_id)
                                                .order_by(ObligationOccurrence.due_date))]


def test_auto_renewal_contract_alerts_before_the_notice_deadline_and_feeds_payments(case):
    client, factory, category = case
    created = client.post("/api/contracts", json=body(category))
    assert created.status_code == 200, created.text
    contract = created.json()
    assert (contract["next_renewal"], contract["notice_deadline"], contract["days_to_notice"], contract["notice_state"]) == ("2026-12-31", "2026-12-01", 21, "a_aproximar")
    assert contract["annual_cents"] == 61200 and contract["monthly_cents"] == 5100 and contract["fidelized"]
    notice = occurrences(factory, contract["notice_obligation_id"])
    assert "2026-12-01" in notice and "2027-12-01" in notice  # Todos os anos, 30 dias antes da renovação.
    with factory() as db:
        payment = db.get(Obligation, contract["payment_obligation_id"])
        assert payment.amount_cents == 5100 and payment.periodicity == "mensal" and payment.category_id == category and payment.end_date is None
        assert db.get(Obligation, contract["notice_obligation_id"]).alert_days == "[60, 30, 7]"
    # No painel das obrigações, os contratos em renovação são os prazos de denúncia (não os pagamentos).
    overview = client.get("/api/obligations/overview").json()
    assert [c["title"] for c in overview["contracts"]] == ["Prazo de denúncia — MEO · Telecomunicações"]
    assert overview["contracts"][0]["role"] == "notice" and overview["contracts"][0]["contract_id"] == contract["id"]
    # As obrigações geridas só se alteram pelo contrato.
    ob = client.get(f"/api/obligations/{contract['notice_obligation_id']}").json()
    assert ob["contract_id"] == contract["id"]
    edit = {"category": "contrato", "title": "Outro", "periodicity": "anual", "start_date": "2026-12-01", "revision": ob["revision"]}
    assert client.put(f"/api/obligations/{ob['id']}", json=edit).status_code == 409
    assert client.post(f"/api/obligations/{ob['id']}/status", json={"revision": ob["revision"], "status": "terminada", "reason": "teste"}).status_code == 409
    detail = client.get(f"/api/contracts/{contract['id']}").json()
    assert {o["role"] for o in detail["occurrences"]} == {"notice", "payment"}


def test_notice_respects_fidelization_and_never_happens_by_itself(case):
    client, factory, category = case
    contract = client.post("/api/contracts", json=body(category, fidelization_end="2027-06-30")).json()
    decision = {"revision": contract["revision"], "action": "denunciar", "reason": "Proposta melhor da concorrência"}
    refused = client.post(f"/api/contracts/{contract['id']}/decision", json=decision)
    assert refused.status_code == 409 and "fidelizado até 2027-06-30" in refused.json()["detail"]
    done = client.post(f"/api/contracts/{contract['id']}/decision", json={**decision, "accept_fidelization_penalty": True})
    assert done.status_code == 200, done.text
    after = done.json()
    assert after["status"] == "denunciado" and after["end_date"] == "2026-12-31" and after["notice_state"] == "concluido"
    with factory() as db:
        assert db.get(Obligation, contract["notice_obligation_id"]).status == "terminada"
        assert db.get(Obligation, contract["payment_obligation_id"]).end_date == "2026-12-30"
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "contrato_denunciar")) == 1
    payments = occurrences(factory, contract["payment_obligation_id"])
    assert max(payments) == "2026-12-10" and "2027-01-10" not in payments
    # Reativar e terminar também são decisões explícitas.
    assert client.post(f"/api/contracts/{contract['id']}/decision", json={**decision, "revision": after["revision"], "action": "denunciar"}).status_code == 409
    reactivated = client.post(f"/api/contracts/{contract['id']}/decision", json={"revision": after["revision"], "action": "reativar", "reason": "Mudança de ideias"}).json()
    assert reactivated["status"] == "ativo"
    ended = client.post(f"/api/contracts/{contract['id']}/decision", json={"revision": reactivated["revision"], "action": "terminar", "reason": "Fecho da loja"})
    assert ended.json()["status"] == "terminado" and ended.json()["end_date"] == "2026-11-10"


def test_fixed_term_contract_ends_and_renews_only_manually(case):
    client, factory, category = case
    contract = client.post("/api/contracts", json=body(category, auto_renewal=False, end_date="2027-03-31", notice_days=60, fidelization_end=None,
                                                         entity_name="Senhorio", contract_type="Renda")).json()
    assert contract["notice_deadline"] == "2027-01-30" and contract["notice_state"] == "agendado"
    assert occurrences(factory, contract["notice_obligation_id"]) == ["2027-01-30"]
    with factory() as db:
        assert db.get(Obligation, contract["notice_obligation_id"]).title == "Fim do contrato — Senhorio · Renda"
        assert db.get(Obligation, contract["payment_obligation_id"]).end_date == "2027-03-31"
    renewed = client.post(f"/api/contracts/{contract['id']}/decision", json={"revision": contract["revision"], "action": "renovar", "reason": "Renovado por mais um ano"})
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["end_date"] == "2028-03-31" and renewed.json()["notice_deadline"] == "2028-01-31"
    # O prazo antigo (intocado) sai; o novo fica para lá dos 400 dias gravados e é criado quando se aproximar.
    assert occurrences(factory, contract["notice_obligation_id"]) == []
    # Renovação automática com data já passada: a próxima renovação calcula-se a partir de hoje.
    past = client.post("/api/contracts", json=body(category, end_date="2026-01-31", fidelization_end=None)).json()
    assert past["next_renewal"] == "2027-01-31" and past["notice_deadline"] == "2027-01-01"
    auto = client.post(f"/api/contracts/{past['id']}/decision", json={"revision": past["revision"], "action": "renovar", "reason": "Renovar"})
    assert auto.status_code == 409  # Na renovação automática não se renova à mão.


def test_validation_and_permissions(case):
    client, _, category = case
    assert client.post("/api/contracts", json=body(category, end_date=None)).status_code == 422  # Renovação automática sem data.
    assert client.post("/api/contracts", json=body(category, entity_name="")).status_code == 422
    assert client.post("/api/contracts", json=body(category, payment_periodicity="personalizada")).status_code == 422
    contract = client.post("/api/contracts", json=body(category)).json()
    assert client.put(f"/api/contracts/{contract['id']}", json={**body(category), "revision": 9}).status_code == 409
    changed = client.put(f"/api/contracts/{contract['id']}", json={**body(category, notice_days=90), "revision": contract["revision"]})
    assert changed.status_code == 200 and changed.json()["notice_deadline"] == "2026-10-02" and changed.json()["notice_state"] == "vencido"
    listing = client.get("/api/contracts").json()
    assert listing["summary"]["active"] == 1 and listing["summary"]["annual_cents"] == 61200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/contracts").status_code == 403
