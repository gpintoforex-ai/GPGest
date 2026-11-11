"""Modelos fiscais: propostas pelo regime da empresa, criação só do escolhido, sempre «a validar», sem duplicados."""

from datetime import date

import pytest
from app import obligations
from app.models import Employee, Obligation, ObligationOccurrence
from sqlalchemy import select


@pytest.fixture
def client(setup, monkeypatch):
    monkeypatch.setattr(obligations, "today", lambda: date(2026, 11, 11))
    return setup


def by_key(data):
    return {t["key"]: t for t in data["templates"]}


def test_proposals_follow_the_vat_regime_and_employees(client):
    c, _, factory = client
    assert c.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "mensal"}).status_code == 200
    data = c.get("/api/obligations/fiscal-templates")
    assert data.status_code == 200, data.text  # Não é confundida com /api/obligations/{id}.
    items = by_key(data.json())
    assert items["iva_mensal_declaracao"]["applicable"] and items["iva_mensal_declaracao"]["selected"]
    assert items["iva_mensal_declaracao"]["first_due"] == "2026-11-20" and items["iva_mensal_pagamento"]["first_due"] == "2026-11-25"
    assert not items["iva_trimestral_declaracao"]["applicable"] and not items["iva_trimestral_declaracao"]["selected"]
    assert not items["dmr"]["applicable"] and items["dmr"]["reason"] == "Sem funcionários registados"
    assert items["irc_ppc_3"]["first_due"] == "2026-12-15" and items["irc_modelo22"]["first_due"] == "2027-05-31"
    assert items["imi"]["applicable"] and not items["imi"]["selected"]  # Só se tiver imóveis: escolha do utilizador.
    with factory.begin() as db:
        db.add(Employee(name="Ana"))
    assert c.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    items = by_key(c.get("/api/obligations/fiscal-templates").json())
    assert items["dmr"]["selected"] and items["ss_pagamento"]["source"].startswith("Segurança Social")
    assert items["iva_trimestral_declaracao"]["selected"] and items["iva_trimestral_declaracao"]["first_due"] == "2026-11-20"
    assert c.post("/api/settings/fiscal", json={"vat_regime": "isento_53", "vat_period": "trimestral"}).status_code == 200
    items = by_key(c.get("/api/obligations/fiscal-templates").json())
    assert not any(items[k]["applicable"] for k in items if k.startswith("iva_"))


def test_create_only_the_chosen_templates_as_unvalidated_and_never_twice(client):
    c, _, factory = client
    assert c.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "mensal"}).status_code == 200
    created = c.post("/api/obligations/fiscal-templates", json={"keys": ["iva_mensal_declaracao", "irc_modelo22"]})
    assert created.status_code == 200, created.text
    assert [x["key"] for x in created.json()["created"]] == ["iva_mensal_declaracao", "irc_modelo22"]
    with factory() as db:
        rows = {r.kind: r for r in db.scalars(select(Obligation).where(Obligation.category == "fiscal"))}
        iva = rows["IVA — declaração periódica"]
        assert iva.periodicity == "mensal" and iva.start_date == "2026-11-20" and not iva.date_validated and iva.date_source == "regra_legal"
        assert "dia 20 do 2.º mês" in iva.date_note and "dia útil" in iva.date_note and iva.entity_name == "Autoridade Tributária"
        dues = [o.due_date for o in db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id == iva.id).order_by(ObligationOccurrence.due_date))]
        assert dues[:3] == ["2026-11-20", "2026-12-20", "2027-01-20"]
    again = c.post("/api/obligations/fiscal-templates", json={"keys": ["iva_mensal_declaracao", "ies"]}).json()
    assert [x["key"] for x in again["created"]] == ["ies"] and again["skipped"] == ["iva_mensal_declaracao"]
    items = by_key(c.get("/api/obligations/fiscal-templates").json())
    assert items["iva_mensal_declaracao"]["created"] and not items["iva_mensal_declaracao"]["selected"]
    overview = c.get("/api/obligations/overview").json()
    assert {f["title"] for f in overview["fiscal"]} >= {"IVA — declaração periódica (mensal)", "IRC — Modelo 22", "IES — Informação Empresarial Simplificada"}
    assert all(not f["date_validated"] for f in overview["fiscal"])
    assert c.post("/api/obligations/fiscal-templates", json={"keys": ["nao_existe"]}).status_code == 422
    assert c.post("/api/obligations/fiscal-templates", json={"keys": ["ies", "ies"]}).status_code == 422
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert c.get("/api/obligations/fiscal-templates").status_code == 403
