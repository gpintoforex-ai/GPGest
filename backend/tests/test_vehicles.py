"""Viaturas: IUC e inspeções por categoria e idade (modelos a validar), seguro, revisão, inspeção seguinte ao
concluir, venda e proteção das obrigações geridas."""

from datetime import date

import pytest
from app import obligations
from app.models import Obligation, ObligationOccurrence
from sqlalchemy import select


@pytest.fixture
def client(setup, monkeypatch):
    monkeypatch.setattr(obligations, "today", lambda: date(2026, 11, 10))
    return setup


def body(**changes):
    return {"plate": "aa-00-bb", "make": "Mercedes", "model": "A180", "first_registration": "2021-03-15", "category": "ligeiro_passageiros",
            "fuel": "Gasóleo", "iuc_amount_cents": 12000, "inspection_cost_cents": 3500, "insurance_entity": "Fidelidade", "insurance_policy": "AP-1",
            "insurance_renewal": "2027-01-20", "insurance_amount_cents": 42000, **changes}


def pending(factory, obligation_id):
    with factory() as db:
        return [(o.due_date, o.status) for o in db.scalars(select(ObligationOccurrence).where(ObligationOccurrence.obligation_id == obligation_id)
                                                            .order_by(ObligationOccurrence.due_date))]


def test_passenger_car_iuc_inspection_and_insurance_follow_the_category_rule(client):
    c, _, factory = client
    created = c.post("/api/vehicles", json=body())
    assert created.status_code == 200, created.text
    vehicle = created.json()
    assert vehicle["plate"] == "AA-00-BB" and vehicle["age_years"] == 5 and "4, 6, 8 ano(s) e depois anual" in vehicle["inspection_rule"]
    up = vehicle["upcoming"]
    # Inspeção aos 4 anos (15/03/2025) já passou e não há registo: a seguinte é aos 6 anos.
    assert up["inspection"]["due_date"] == "2027-03-15" and up["inspection"]["validated"] is False and up["inspection"]["expected_cents"] == 3500
    # IUC até ao fim do mês da matrícula; sem prazos anteriores ao registo (supõem-se pagos).
    assert up["iuc"]["due_date"] == "2027-03-31" and up["iuc"]["expected_cents"] == 12000 and up["iuc"]["validated"] is False
    assert up["insurance"]["due_date"] == "2027-01-20" and up["insurance"]["expected_cents"] == 42000
    with factory() as db:
        iuc = db.scalar(select(Obligation).where(Obligation.kind == "IUC"))
        assert iuc.date_source == "regra_legal" and "Portal das Finanças" in iuc.date_note and iuc.category == "veiculo"
        assert all(due >= "2026-11-10" for due, _ in pending(factory, iuc.id))
    # Goods vehicle: 2 years, then annual (not the passenger rule).
    van = c.post("/api/vehicles", json=body(plate="11-AA-22", category="ligeiro_mercadorias", first_registration="2024-06-01", insurance_renewal=None)).json()
    assert van["upcoming"]["inspection"]["due_date"] == "2027-06-01" and "insurance" not in van["upcoming"]
    # Last inspection inside the 3-month window counts for that deadline.
    checked = c.post("/api/vehicles", json=body(plate="22-BB-33", last_inspection_date="2025-03-10", insurance_renewal=None)).json()
    assert checked["upcoming"]["inspection"]["due_date"] == "2027-03-15"
    late = c.post("/api/vehicles", json=body(plate="33-CC-44", last_inspection_date="2024-01-10", insurance_renewal=None)).json()
    assert late["upcoming"]["inspection"]["due_date"] == "2025-03-15" and late["upcoming"]["inspection"]["state"] == "vencido"


def test_completing_the_inspection_creates_the_next_one_and_keeps_history(client):
    c, _, factory = client
    vehicle = c.post("/api/vehicles", json=body(plate="33-CC-44", last_inspection_date="2024-01-10", insurance_renewal=None)).json()
    detail = c.get(f"/api/vehicles/{vehicle['id']}").json()
    inspection = next(o for o in detail["occurrences"] if o["role"] == "inspection")
    done = c.post(f"/api/obligations/occurrences/{inspection['id']}/complete", json={"revision": inspection["revision"], "actual_cents": 3800, "reason": "Aprovada"})
    assert done.status_code == 200, done.text
    after = c.get(f"/api/vehicles/{vehicle['id']}").json()
    assert after["last_inspection_date"] == "2025-03-15" and after["upcoming"]["inspection"]["due_date"] == "2027-03-15"
    history = [(o["due_date"], o["status"]) for o in after["occurrences"] if o["role"] == "inspection"]
    assert ("2025-03-15", "concluida") in history and ("2027-03-15", "pendente") in history
    # Managed obligations cannot be edited directly.
    ob = c.get(f"/api/obligations/{inspection['obligation_id']}").json()
    assert ob["vehicle_id"] == vehicle["id"]
    assert c.put(f"/api/obligations/{ob['id']}", json={"category": "veiculo", "title": "Outro título", "periodicity": "unica", "start_date": "2027-01-01",
                                                       "revision": ob["revision"]}).status_code == 409


def test_manual_rule_selling_validation_and_permissions(client):
    c, _, factory = client
    assert c.post("/api/vehicles", json=body(category="motociclo", plate="44-DD-55")).status_code == 422  # Regra manual exige data.
    moto = c.post("/api/vehicles", json=body(category="motociclo", plate="44-DD-55", inspection_manual_next="2027-05-01", inspection_manual_years=2)).json()
    assert moto["upcoming"]["inspection"]["due_date"] == "2027-05-01" and moto["upcoming"]["inspection"]["validated"] is True
    assert "Regra manual" in moto["inspection_rule"]
    assert c.post("/api/vehicles", json=body(plate="44 dd 55", category="motociclo", inspection_manual_next="2027-05-01")).status_code == 409  # Matrícula repetida.
    sold = c.post(f"/api/vehicles/{moto['id']}/status", json={"revision": moto["revision"], "status": "vendida", "reason": "Venda a terceiros"})
    assert sold.status_code == 200 and sold.json()["status"] == "vendida" and sold.json()["upcoming"] == {}
    with factory() as db:
        owned = list(db.scalars(select(Obligation).where(Obligation.data.like(f'%"vehicle_id": "{moto["id"]}"%'))))
        assert len(owned) == 3 and all(o.status == "terminada" for o in owned)  # IUC, inspeção e seguro.
    assert c.put(f"/api/vehicles/{moto['id']}", json={**body(category="motociclo", plate="44-DD-55", inspection_manual_next="2027-05-01"), "revision": 0}).status_code == 409
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert c.get("/api/vehicles").status_code == 403
