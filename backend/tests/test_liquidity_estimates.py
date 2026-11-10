"""Previsão de liquidez com prioridades: compromissos e estimativas (orçamento, obrigações) sem duplicar documentos."""

import json
from datetime import date

import pytest
from app import budget as budget_module
from app import liquidity, obligations
from app.liquidity_estimates import commitments
from app.models import (
    BusinessEntity,
    Category,
    PlannedMovement,
    ProcurementDocument,
    Purchase,
    Store,
    User,
)
from sqlalchemy import select

TODAY = date(2026, 11, 10)


@pytest.fixture
def case(setup, monkeypatch):
    for module in (budget_module, liquidity, obligations):
        monkeypatch.setattr(module, "today", lambda: TODAY)
    client, _, factory = setup
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        rent, power, insurance = Category(name="Rendas"), Category(name="Energia"), Category(name="Seguros")
        db.add_all([rent, power, insurance])
        db.flush()
        supplier = BusinessEntity(name="Elétrica Lusa", tax_number="503504564", is_supplier=True, category_id=power.id, payment_days=30)
        db.add(supplier)
        db.flush()
        # Fatura de eletricidade por associar ao orçamento: substitui a ocorrência de 15/11.
        db.add(Purchase(store_id=store, supplier_nif="503504564", supplier_name="Elétrica Lusa", document="FT E/11", document_type="FT",
                        document_date="2026-11-09", amount_cents=21000, tax_cents=0, base_cents=21000, origin="efatura"))
        # Documento por liquidar que cobre a obrigação «EDP».
        db.add(PlannedMovement(store_id=store, category_id=power.id, entity="EDP Comercial", document="FT X/9", due_date="2026-11-26", amount_cents=10050, direction=-1))
        ids = {"store": store, "rent": rent.id, "power": power.id, "insurance": insurance.id, "supplier": supplier.id}
    budget = client.post("/api/budgets", json={"year": 2026}).json()
    lines = [
        {"category_id": ids["rent"], "description": "Renda", "amount_cents": 100000, "vat_rate": "23", "periodicity": "mensal", "start_date": "2026-01-05", "payment_days": 10},
        {"category_id": ids["power"], "description": "Eletricidade", "entity_name": "Elétrica Lusa", "amount_cents": 20000, "vat_rate": "0", "periodicity": "mensal", "start_date": "2026-01-15"},
        {"category_id": ids["insurance"], "description": "Seguro multirriscos", "amount_cents": 50000, "vat_rate": "0", "periodicity": "anual", "start_date": "2026-11-20"},
    ]
    for line in lines:
        assert client.post(f"/api/budgets/{budget['id']}/lines", json=line).status_code == 200
    assert client.post(f"/api/budgets/{budget['id']}/approve", json={"revision": 0}).status_code == 200
    for body in ({"category": "seguro", "title": "Seguro multirriscos", "periodicity": "unica", "start_date": "2026-11-20", "amount_cents": 50000,
                  "category_id": ids["insurance"], "entity_name": "Seguradora"},
                 {"category": "contrato", "title": "Energia EDP", "periodicity": "unica", "start_date": "2026-11-25", "amount_cents": 10000, "entity_name": "EDP"}):
        assert client.post("/api/obligations", json=body).status_code == 200
    return client, factory, ids


def forecast(client, days=40):
    response = client.post("/api/liquidity/forecast", json={"start": TODAY.isoformat(), "end": date.fromordinal(TODAY.toordinal() + days).isoformat()})
    assert response.status_code == 200, response.text
    return response.json()


def test_estimates_enter_once_with_priorities_and_duplicates_are_excluded_with_reason(case):
    client, _, _ = case
    data = forecast(client)
    events = {e["key"]: e for e in data["events"]}
    rent = next(e for e in events.values() if e["source"] == "budget" and e["title"].startswith("Renda") and e["due_date"] == "2026-11-15")
    assert rent["amount_cents"] == 123000 and rent["priority"] == 4 and rent["priority_label"] == "Estimativas" and rent["confidence"] == "provavel"
    insurance_obligation = next(e for e in events.values() if e["source"] == "obligation" and "Seguro" in e["title"])
    assert insurance_obligation["amount_cents"] == 50000 and insurance_obligation["expected_date"] == "2026-11-20"
    planned = next(e for e in events.values() if e["source"] == "planned")
    assert planned["priority"] == 2 and planned["priority_label"] == "Documentos por liquidar"
    pending = {p["key"]: p["exclusion"] for p in data["pending"]}
    # Eletricidade de novembro: a fatura por associar substitui a estimativa do orçamento.
    power = next(k for k in pending if k.startswith("budget:") and "2026-11-15" in k and "Provavelmente substituída" in pending[k])
    assert "FT E/11" in pending[power]
    # Seguro: a obrigação cobre a despesa orçamentada; a obrigação da EDP é substituída pelo documento por liquidar.
    assert any("Coberta pela obrigação «Seguro multirriscos" in reason for reason in pending.values())
    assert any(k.startswith("obligation:") and "Substituída pelo documento" in reason for k, reason in pending.items())
    scheduled_keys = {e["key"] for e in data["scheduled"]}
    assert rent["key"] in scheduled_keys and power not in scheduled_keys
    levels = {level["priority"]: level for level in data["priorities"]["levels"]}
    assert levels[2]["outgoing_cents"] == 10050 and levels[4]["outgoing_cents"] >= 123000 + 50000
    assert data["priorities"]["excluded_duplicates"] == 3


def test_linked_budget_occurrence_leaves_the_forecast_and_horizons_cut_estimates(case):
    client, _, _ = case
    budget = client.get("/api/budgets/overview?year=2026").json()["budget"]
    pending = client.get(f"/api/budgets/{budget['id']}/matching").json()["pending"]
    item = next(p for p in pending if p["label"] == "FT FT E/11")
    best = item["candidates"][0]
    assert client.post(f"/api/budgets/{budget['id']}/links", json={"source_kind": "purchase", "source_id": item["source_id"], "category_id": item["category_id"],
                                                                  "line_id": best["line_id"], "rule_date": best["rule_date"]}).status_code == 200
    data = forecast(client)
    keys = {e["key"] for e in data["events"]} | {p["key"] for p in data["pending"]}
    assert not any(k.startswith("budget:") and "2026-11-15" in k and "Eletricidade" in (next((e["title"] for e in data["events"] if e["key"] == k), "")) for k in keys)
    short = forecast(client, days=3)
    assert not [e for e in short["scheduled"] if e["source"] in ("budget", "obligation")]  # A renda de 5/11 paga-se a 15/11: fora de 3 dias.


def test_commitments_count_only_what_is_not_yet_received(case):
    _, factory, ids = case
    with factory.begin() as db:
        user = db.scalar(select(User).where(User.email == "admin@test.pt")).id
        order = ProcurementDocument(kind="order", store_id=ids["store"], supplier_id=ids["supplier"], user_id=user, status="confirmed", request_key="o1",
                                    confirmed_at=liquidity.now(), data=json.dumps({"number": "EF 1", "document_date": "2026-11-01", "lines": [
                                        {"nature": "goods", "description": "Lâmpadas", "quantity": "10", "price": "2.00", "discount": "0", "vat": "23"}]}))
        db.add(order)
    with factory() as db:
        found = commitments(db, [ids["store"]], TODAY, date(2026, 12, 31))
        assert len(found) == 1 and found[0]["amount_cents"] == 2460 and found[0]["due_date"] == "2026-12-01" and found[0]["key"].startswith("commitment:")
        assert commitments(db, [ids["store"]], TODAY, date(2026, 11, 30)) == []  # Fora do horizonte.
