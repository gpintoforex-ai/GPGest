"""Obrigações e prazos: motor de recorrências, ocorrências sem duplicados, histórico, estados e painel."""

from datetime import date, timedelta

import pytest
from app import obligations as module
from app.recurrence import add_months, dates


def test_recurrence_clamps_month_end_and_crosses_years():
    monthly = dates(date(2026, 1, 31), "mensal", date(2026, 5, 31))
    assert [d.isoformat() for d in monthly] == ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31"]
    assert [d.isoformat() for d in dates(date(2026, 11, 15), "trimestral", date(2027, 8, 31))] == ["2026-11-15", "2027-02-15", "2027-05-15", "2027-08-15"]
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)
    assert dates(date(2026, 3, 1), "unica", date(2027, 1, 1)) == [date(2026, 3, 1)]
    assert len(dates(date(2026, 1, 5), "semanal", date(2026, 2, 1))) == 4
    assert [d.isoformat() for d in dates(date(2026, 1, 10), "personalizada", date(2027, 1, 31), every_months=5)] == ["2026-01-10", "2026-06-10", "2026-11-10"]
    # Fim da regra e início da janela.
    assert dates(date(2026, 1, 1), "anual", date(2035, 1, 1), end=date(2028, 6, 1)) == [date(2026, 1, 1), date(2027, 1, 1), date(2028, 1, 1)]
    assert dates(date(2020, 1, 15), "mensal", date(2026, 3, 31), since=date(2026, 2, 1)) == [date(2026, 2, 15), date(2026, 3, 15)]
    with pytest.raises(ValueError):
        dates(date(2026, 1, 1), "personalizada", date(2027, 1, 1))


def body(**changes):
    return {"category": "seguro", "kind": "multirriscos", "title": "Seguro multirriscos", "periodicity": "anual",
            "start_date": "2026-12-01", "amount_cents": 42000, "alert_days": [60, 30, 7, 0], **changes}


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(module, "today", lambda: date(2026, 11, 10))
    return date(2026, 11, 10)


def test_occurrences_are_created_once_and_completion_keeps_history_and_next(setup, fixed_today):
    client = setup[0]
    created = client.post("/api/obligations", json=body(periodicity="mensal", start_date="2026-10-20", title="Renda da loja", category="contrato"))
    assert created.status_code == 200, created.text
    obligation = created.json()
    dues = [o["due_date"] for o in obligation["occurrences"]]
    assert dues[-1] == "2026-10-20" and len(dues) == len(set(dues))
    # Ler várias vezes não duplica ocorrências.
    client.get("/api/obligations/overview")
    again = client.get(f"/api/obligations/{obligation['id']}").json()
    assert len(again["occurrences"]) == len(dues)
    overdue = next(o for o in again["occurrences"] if o["due_date"] == "2026-10-20")
    assert overdue["state"] == "vencido"
    done = client.post(f"/api/obligations/occurrences/{overdue['id']}/complete", json={"revision": overdue["revision"], "actual_cents": 41850, "reason": "Pago por transferência"})
    assert done.status_code == 200, done.text
    after = client.get(f"/api/obligations/{obligation['id']}").json()
    completed = next(o for o in after["occurrences"] if o["id"] == overdue["id"])
    assert completed["state"] == "concluido" and completed["actual_cents"] == 41850 and completed["completed_by"]
    # A conclusão não impede a ocorrência seguinte (20/11, daqui a 10 dias).
    assert after["next"]["due_date"] == "2026-11-20" and after["next"]["state"] == "a_aproximar"
    # Concluir outra vez é recusado; reabrir exige motivo.
    assert client.post(f"/api/obligations/occurrences/{overdue['id']}/complete", json={"revision": completed["revision"]}).status_code == 409
    assert client.post(f"/api/obligations/occurrences/{overdue['id']}/reopen", json={"revision": completed["revision"]}).status_code == 422


def test_rule_change_replaces_only_untouched_future_occurrences(setup, fixed_today):
    client = setup[0]
    obligation = client.post("/api/obligations", json=body(periodicity="mensal", start_date="2026-11-25", title="Avença")).json()
    first = next(o for o in obligation["occurrences"] if o["due_date"] == "2026-11-25")
    moved = client.post(f"/api/obligations/occurrences/{first['id']}/reschedule",
                        json={"revision": first["revision"], "due_date": "2026-11-30", "reason": "Prorrogação acordada com o fornecedor"})
    assert moved.status_code == 200, moved.text
    changed = client.put(f"/api/obligations/{obligation['id']}", json=body(periodicity="trimestral", start_date="2026-11-25", title="Avença", revision=obligation["revision"]))
    assert changed.status_code == 200, changed.text
    occurrences = {o["rule_date"]: o for o in changed.json()["occurrences"]}
    # A ocorrência alterada fica (com a data e a origem); as futuras passam a trimestrais.
    assert occurrences["2026-11-25"]["due_date"] == "2026-11-30" and "Prorrogação" in occurrences["2026-11-25"]["date_origin"]
    assert "2026-12-25" not in occurrences and "2027-02-25" in occurrences
    # Revisão desatualizada é recusada.
    assert client.put(f"/api/obligations/{obligation['id']}", json=body(revision=obligation["revision"])).status_code == 409


def test_overview_counts_windows_categories_and_commitments(setup, fixed_today):
    client = setup[0]
    client.post("/api/obligations", json=body(category="fiscal", kind="iva", title="IVA mensal", periodicity="mensal", start_date="2026-11-15",
                                              amount_cents=None, date_source="regra_legal", date_validated=False, date_note="A confirmar com o contabilista"))
    client.post("/api/obligations", json=body(category="veiculo", kind="iuc", title="IUC AA-00-BB", periodicity="anual", start_date="2026-12-05", amount_cents=12000))
    client.post("/api/obligations", json=body(category="contrato", title="Renovação MEO", periodicity="anual", start_date="2027-01-30", amount_cents=61200))
    data = client.get("/api/obligations/overview").json()
    kpis = data["kpis"]
    # 90 dias: IVA mensal (15/11, 15/12, 15/01), IUC e MEO.
    assert kpis["next_7"] == 1 and kpis["next_30"] == 2 and kpis["next_90"] == 5 and kpis["overdue"] == 0 and kpis["total"] == 3
    assert kpis["commitments_30_cents"] == 12000 and kpis["commitments_90_cents"] == 12000 + 61200
    assert {c["key"]: c["count"] for c in data["categories"]}["veiculo"] == 1
    assert data["calendar"]["month"] == "2026-11" and "2026-11-15" in data["calendar"]["days"]
    assert data["fiscal"][0]["date_validated"] is False and data["fiscal"][0]["next"]["due_date"] == "2026-11-15"
    assert [c["title"] for c in data["contracts"]] == ["Renovação MEO"]
    assert [m["month"] for m in data["per_month"]][:2] == ["2026-11", "2026-12"]
    upcoming = client.get("/api/obligations/occurrences", params={"category": "veiculo", "end": (fixed_today + timedelta(days=90)).isoformat()}).json()
    assert len(upcoming) == 1 and upcoming[0]["title"] == "IUC AA-00-BB"


def test_cancel_not_applicable_read_and_validation(setup, fixed_today):
    client = setup[0]
    obligation = client.post("/api/obligations", json=body(periodicity="unica", start_date="2026-11-12")).json()
    occurrence = obligation["occurrences"][0]
    assert client.post(f"/api/obligations/occurrences/{occurrence['id']}/cancel", json={"revision": 0}).status_code == 422
    na = client.post(f"/api/obligations/occurrences/{occurrence['id']}/not-applicable", json={"revision": 0, "reason": "Loja encerrada"})
    assert na.status_code == 200 and na.json()["status"] == "nao_aplicavel"
    assert client.post(f"/api/obligations/occurrences/{occurrence['id']}/read", json={"revision": 0}).status_code == 200
    view = client.get(f"/api/obligations/{obligation['id']}").json()["occurrences"][0]
    assert view["state"] == "nao_aplicavel" and view["read"] is True
    assert client.post("/api/obligations", json=body(periodicity="personalizada")).status_code == 422
    assert client.post("/api/obligations", json=body(end_date="2026-01-01")).status_code == 422


def test_only_managers_use_obligations(setup):
    client = setup[0]
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/obligations/overview").status_code == 403
    assert client.post("/api/obligations", json=body()).status_code == 403
