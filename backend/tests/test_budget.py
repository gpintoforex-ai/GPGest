"""Orçamento de despesas: ocorrências (aumentos, exceções, IVA), versões, cópia do ano anterior e desvios."""

from datetime import date

import pytest
from app import budget as module
from app.models import BusinessEntity, Category, Purchase, Store
from sqlalchemy import select


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(module, "today", lambda: date(2026, 11, 10))


def line(category, **changes):
    return {"category_id": category, "description": "Renda da loja", "amount_cents": 100000, "vat_rate": "23", "periodicity": "mensal",
            "start_date": "2026-01-05", **changes}


def prepare(client, factory, deduction=50):
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        rent = Category(name="Rendas", vat_deduction_percent=deduction)
        energy = Category(name="Energia")
        db.add_all([rent, energy])
        db.flush()
        db.add(BusinessEntity(name="Senhorio, Lda", tax_number="510774318", is_supplier=True, category_id=rent.id))
        db.add(BusinessEntity(name="Elétrica, SA", tax_number="503504564", is_supplier=True, category_id=energy.id))
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        return rent.id, energy.id, store


def purchase(factory, store, nif, number, day, base, tax):
    with factory.begin() as db:
        db.add(Purchase(store_id=store, supplier_nif=nif, supplier_name="F", document=number, document_date=day,
                        amount_cents=base + tax, tax_cents=tax, base_cents=base, origin="manual"))


def test_occurrences_apply_increases_exceptions_vat_deduction_and_payment_date(setup, fixed_today):
    client, _, factory = setup
    rent, _, _ = prepare(client, factory)
    created = client.post("/api/budgets", json={"year": 2026})
    assert created.status_code == 200, created.text
    budget = created.json()
    assert budget["status"] == "rascunho" and budget["version"] == 1 and budget["name"] == "Orçamento 2026"
    body = line(rent, payment_days=10, increases=[{"from": "2026-07-01", "percent": "5"}],
                exceptions=[{"date": "2026-08-05", "skip": True}, {"date": "2026-12-05", "amount_cents": 50000}])
    assert client.post(f"/api/budgets/{budget['id']}/lines", json=body).status_code == 200
    data = client.get("/api/budgets/overview?year=2026").json()
    row = data["lines"][0]
    # IVA 23 %, dedutível a 50 % no regime normal: custo = base + 11,5 % da base.
    assert row["months"][:6] == [111500] * 6
    assert row["months"][6] == 117075 and row["months"][7] == 0  # Aumento de 5 % a partir de julho; agosto não se realiza.
    assert row["months"][11] == 55750  # Exceção: valor fixo de 500 € sem IVA (não leva o aumento).
    first = row["occurrences"][0]
    assert first == {"date": "2026-01-05", "payment_date": "2026-01-15", "base": 100000, "vat": 23000, "deductible": 11500,
                     "gross": 123000, "cost": 111500, "exception": False}
    assert row["annual_base_cents"] == 6 * 100000 + 4 * 105000 + 50000
    assert data["kpis"]["annual_budget_cents"] == sum(row["months"]) and data["month"] == 11
    # Valor com IVA indicado: guardado sem IVA (Decimal, sem float).
    added = client.post(f"/api/budgets/{budget['id']}/lines", json=line(rent, description="Seguro", periodicity="anual", start_date="2026-03-01",
                                                                         amount_cents=12300, amount_includes_vat=True))
    assert added.status_code == 200
    seguro = next(r for r in client.get("/api/budgets/overview?year=2026").json()["lines"] if r["description"] == "Seguro")
    assert seguro["amount_cents"] == 10000 and seguro["annual_gross_cents"] == 12300
    # Regime isento: o IVA é todo custo.
    assert client.post("/api/settings/fiscal", json={"vat_regime": "isento_53", "vat_period": "trimestral"}).status_code == 200
    assert client.get("/api/budgets/overview?year=2026").json()["lines"][0]["months"][0] == 123000


def test_versions_approval_revision_and_locking(setup, fixed_today):
    client, _, factory = setup
    rent, _, _ = prepare(client, factory)
    budget = client.post("/api/budgets", json={"year": 2026, "name": "Plano 2026"}).json()
    assert client.post(f"/api/budgets/{budget['id']}/approve", json={"revision": 0}).status_code == 422  # Sem linhas.
    assert client.post(f"/api/budgets/{budget['id']}/lines", json=line(rent)).status_code == 200
    approved = client.post(f"/api/budgets/{budget['id']}/approve", json={"revision": 0})
    assert approved.status_code == 200 and approved.json()["status"] == "aprovado"
    # Aprovado: bloqueado; mesmo ano e âmbito não admite outro orçamento.
    assert client.post(f"/api/budgets/{budget['id']}/lines", json=line(rent)).status_code == 409
    assert client.post("/api/budgets", json={"year": 2026}).status_code == 409
    assert client.post(f"/api/budgets/{budget['id']}/revise", json={"revision": 1, "reason": ""}).status_code == 422
    revised = client.post(f"/api/budgets/{budget['id']}/revise", json={"revision": 1, "reason": "Aumento da renda"})
    assert revised.status_code == 200, revised.text
    second = revised.json()
    assert second["version"] == 2 and second["status"] == "rascunho" and second["previous_id"] == budget["id"]
    assert client.post(f"/api/budgets/{budget['id']}/revise", json={"revision": 1, "reason": "Outra"}).status_code == 409
    # Enquanto a revisão está em rascunho, a versão aprovada continua em vigor.
    data = client.get("/api/budgets/overview?year=2026").json()
    assert data["budget"]["id"] == budget["id"] and [v["status"] for v in data["versions"]] == ["aprovado", "rascunho"]
    draft = client.get(f"/api/budgets/overview?year=2026&budget_id={second['id']}").json()
    copied = draft["lines"][0]
    changed = client.put(f"/api/budget-lines/{copied['id']}", json={**line(rent, amount_cents=110000), "revision": copied["revision"]})
    assert changed.status_code == 200, changed.text
    assert client.put(f"/api/budget-lines/{copied['id']}", json={**line(rent), "revision": 0}).status_code == 409
    assert client.post(f"/api/budgets/{second['id']}/approve", json={"revision": 0}).status_code == 200
    data = client.get("/api/budgets/overview?year=2026").json()
    assert data["budget"]["id"] == second["id"] and [v["status"] for v in data["versions"]] == ["revisto", "aprovado"]
    assert data["lines"][0]["amount_cents"] == 110000
    # A versão revista mantém as linhas originais (histórico).
    old = client.get(f"/api/budgets/overview?year=2026&budget_id={budget['id']}").json()
    assert old["lines"][0]["amount_cents"] == 100000
    # Linhas de um orçamento aprovado não se apagam.
    assert client.delete(f"/api/budget-lines/{old['lines'][0]['id']}?revision=0").status_code == 409


def test_copy_previous_year_with_update_percent(setup, fixed_today):
    client, _, factory = setup
    rent, _, store = prepare(client, factory)
    previous = client.post("/api/budgets", json={"year": 2025, "store_id": store}).json()
    assert previous["name"] == "Orçamento 2025 — Teste"
    body = line(rent, start_date="2025-02-28", end_date="2025-11-30", increases=[{"from": "2025-06-01", "percent": "2"}],
                exceptions=[{"date": "2025-04-28", "amount_cents": 20000}])
    assert client.post(f"/api/budgets/{previous['id']}/lines", json=body).status_code == 200
    copied = client.post("/api/budgets", json={"year": 2026, "store_id": store, "copy_from_id": previous["id"], "update_percent": "10"})
    assert copied.status_code == 200, copied.text
    data = client.get(f"/api/budgets/overview?year=2026&store_id={store}").json()
    row = data["lines"][0]
    assert (row["start_date"], row["end_date"], row["amount_cents"]) == ("2026-02-28", "2026-11-30", 110000)
    assert row["increases"] == [{"from": "2026-06-01", "percent": "2"}] and row["exceptions"] == [{"date": "2026-04-28", "skip": False, "amount_cents": 22000}]
    assert data["copy_sources"][0]["id"] == previous["id"]
    # O orçamento da empresa (sem estabelecimento) é independente do da loja.
    assert client.get("/api/budgets/overview?year=2026").json()["budget"] is None


def test_comparison_deviation_unbudgeted_and_closed_period(setup, fixed_today):
    client, _, factory = setup
    rent, energy, store = prepare(client, factory, deduction=None)
    budget = client.post("/api/budgets", json={"year": 2026}).json()
    assert client.post(f"/api/budgets/{budget['id']}/lines", json=line(rent, vat_rate="0")).status_code == 200
    purchase(factory, store, "510774318", "FT 1", "2026-10-05", 120000, 0)
    purchase(factory, store, "510774318", "FT 2", "2026-11-05", 90000, 0)
    purchase(factory, store, "503504564", "FT 9", "2026-11-07", 20000, 4600)  # Energia: sem orçamento.
    data = client.get("/api/budgets/overview?year=2026&month=11").json()
    rubrics = {r["category_name"]: r for r in data["rubrics"]}
    rent_row = rubrics["Rendas"]
    assert rent_row["month_budget_cents"] == 100000 and rent_row["month_actual_cents"] == 90000
    assert (rent_row["month_deviation_cents"], rent_row["month_deviation_pct"]) == (-10000, -10.0)
    assert rent_row["ytd_budget_cents"] == 1100000 and rent_row["ytd_actual_cents"] == 210000 and rent_row["state"] == "abaixo"
    # Rubrica sem orçamento: desvio em euros, percentagem não aplicável. IVA 100 % dedutível (rubrica sem %).
    energy_row = rubrics["Energia"]
    assert energy_row["state"] == "nao_orcamentado" and energy_row["ytd_deviation_pct"] is None and energy_row["ytd_deviation_cents"] == 20000
    assert data["kpis"]["unbudgeted_cents"] == 20000
    assert data["kpis"]["period_actual_cents"] == 230000 and data["kpis"]["period_budget_cents"] == 1100000
    october = data["months"][9]
    assert october["budget_cents"] == 100000 and october["actual_cents"] == 120000 and october["deviation_pct"] == 20.0
    assert data["months"][11]["actual_cents"] is None  # Dezembro ainda não chegou ao mês selecionado.
    assert not data["kpis"]["period_closed"] and not october["closed"]


def test_permissions_cost_centers_and_validation(setup, fixed_today):
    client, ids, factory = setup
    rent, _, _ = prepare(client, factory)
    center = client.post("/api/cost-centers", json={"code": "loja1", "name": "Loja do centro"})
    assert center.status_code == 200 and center.json()["code"] == "LOJA1"
    assert client.post("/api/cost-centers", json={"code": "LOJA1", "name": "Outra"}).status_code == 409
    budget = client.post("/api/budgets", json={"year": 2026}).json()
    with_center = client.post(f"/api/budgets/{budget['id']}/lines", json=line(rent, cost_center_id=center.json()["id"], payment_account="caixa"))
    assert with_center.status_code == 200
    assert client.post(f"/api/budgets/{budget['id']}/lines", json=line(rent, start_date="2027-01-01")).status_code == 422
    assert client.post(f"/api/budgets/{budget['id']}/lines", json=line(rent, payment_account="banco:nao-existe")).status_code == 422
    assert client.post(f"/api/budgets/{budget['id']}/lines", json=line(rent, periodicity="personalizada")).status_code == 422
    row = client.get("/api/budgets/overview?year=2026").json()["lines"][0]
    assert row["cost_center"] == "Loja do centro"
    assert client.delete(f"/api/budget-lines/{row['id']}?revision=0").status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/budgets/overview?year=2026").status_code == 403
    assert client.post("/api/budgets", json={"year": 2027}).status_code == 403
