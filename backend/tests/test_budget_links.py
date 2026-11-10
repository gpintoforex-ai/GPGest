"""Associação do realizado ao orçamento: propostas, confirmação sem contar duas vezes, parciais, rejeição,
anulação, execução por linha e passagem das associações numa revisão aprovada."""

from datetime import date

import pytest
from app import budget as budget_module
from app.models import Audit, BudgetLink, BusinessEntity, Category, Purchase, Store
from sqlalchemy import func, select

RENT_NIF, POWER_NIF, PAPER_NIF = "510774318", "503504564", "500100144"


@pytest.fixture
def case(setup, monkeypatch):
    monkeypatch.setattr(budget_module, "today", lambda: date(2026, 11, 10))
    client, _, factory = setup
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        rent, power, paper = Category(name="Rendas"), Category(name="Energia"), Category(name="Papelaria")
        db.add_all([rent, power, paper])
        db.flush()
        landlord = BusinessEntity(name="Senhorio, Lda", tax_number=RENT_NIF, is_supplier=True, category_id=rent.id)
        db.add_all([landlord, BusinessEntity(name="Elétrica", tax_number=POWER_NIF, is_supplier=True, category_id=power.id),
                    BusinessEntity(name="Papelaria", tax_number=PAPER_NIF, is_supplier=True, category_id=paper.id)])
        db.flush()

        def buy(nif, name, number, day, base):
            row = Purchase(store_id=store, supplier_nif=nif, supplier_name=name, document=number, document_type="FT", document_date=day,
                           amount_cents=base, tax_cents=0, base_cents=base, origin="efatura")
            db.add(row)
            db.flush()
            return row.id
        ids = {"store": store, "rent": rent.id, "power": power.id, "landlord": landlord.id,
               "oct": buy(RENT_NIF, "Senhorio, Lda", "FT R/10", "2026-10-05", 100000), "nov": buy(RENT_NIF, "Senhorio, Lda", "FT R/11", "2026-11-03", 100000),
               "power_oct": buy(POWER_NIF, "Elétrica", "FT E/10", "2026-10-20", 21000), "paper": buy(PAPER_NIF, "Papelaria", "FT P/1", "2026-10-02", 3000)}
    budget = client.post("/api/budgets", json={"year": 2026}).json()
    line = {"vat_rate": "0", "periodicity": "mensal"}
    assert client.post(f"/api/budgets/{budget['id']}/lines", json={**line, "category_id": ids["rent"], "description": "Renda", "entity_id": ids["landlord"],
                                                                   "amount_cents": 100000, "start_date": "2026-01-05"}).status_code == 200
    assert client.post(f"/api/budgets/{budget['id']}/lines", json={**line, "category_id": ids["power"], "description": "Eletricidade",
                                                                   "amount_cents": 20000, "start_date": "2026-01-15"}).status_code == 200
    lines = {r["description"]: r["id"] for r in client.get("/api/budgets/overview?year=2026").json()["lines"]}
    return client, factory, {**ids, "budget": budget["id"], "rent_line": lines["Renda"], "power_line": lines["Eletricidade"]}


def item(pending, source_id):
    return next(p for p in pending if p["source_id"] == source_id)


def body(p, candidate=None, **extra):
    c = candidate or p["candidates"][0]
    return {"source_kind": p["source_kind"], "source_id": p["source_id"], "category_id": p["category_id"], "line_id": c["line_id"],
            "rule_date": c["rule_date"], "confidence": c["confidence"], "reasons": c["reasons"], **extra}


def test_proposals_score_supplier_date_value_and_list_unbudgeted_without_writing(case):
    client, factory, ids = case
    with factory() as db:
        audits = db.scalar(select(func.count()).select_from(Audit))
    data = client.get(f"/api/budgets/{ids['budget']}/matching")
    assert data.status_code == 200, data.text
    body_ = data.json()
    rent = item(body_["pending"], ids["oct"])
    assert rent["strength"] == "forte" and rent["candidates"][0]["rule_date"] == "2026-10-05" and rent["candidates"][0]["confidence"] == 99
    assert "Mesmo fornecedor (NIF)" in rent["candidates"][0]["reasons"]
    power = item(body_["pending"], ids["power_oct"])
    assert power["strength"] == "provavel" and power["candidates"][0]["rule_date"] == "2026-10-15" and power["candidates"][0]["confidence"] == 70
    assert [u["source_id"] for u in body_["unbudgeted"]] == [ids["paper"]] and body_["summary"]["unbudgeted_cents"] == 3000
    assert body_["editable"] and body_["summary"]["strong"] == 2
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Audit)) == audits
        assert not db.scalar(select(func.count()).select_from(BudgetLink))


def test_confirm_never_counts_twice_supports_partials_and_updates_execution(case):
    client, factory, ids = case
    pending = client.get(f"/api/budgets/{ids['budget']}/matching").json()["pending"]
    rent, power = item(pending, ids["oct"]), item(pending, ids["power_oct"])
    assert client.post(f"/api/budgets/{ids['budget']}/links", json=body(rent)).status_code == 200
    assert client.post(f"/api/budgets/{ids['budget']}/links", json=body(rent)).status_code == 409  # Já associado por inteiro.
    assert client.post(f"/api/budgets/{ids['budget']}/links", json=body(power, amount_cents=21001)).status_code == 409
    assert client.post(f"/api/budgets/{ids['budget']}/links", json=body(power, amount_cents=15000)).status_code == 200
    after = client.get(f"/api/budgets/{ids['budget']}/matching").json()
    assert item(after["pending"], ids["power_oct"])["remaining_cents"] == 6000 and ids["oct"] in [x["source_id"] for x in after["linked"]]
    wrong_date = body(item(after["pending"], ids["nov"]), rule_date="2026-11-06")
    assert client.post(f"/api/budgets/{ids['budget']}/links", json=wrong_date).status_code == 422
    lines = {r["description"]: r for r in client.get("/api/budgets/overview?year=2026&month=10").json()["lines"]}
    renda = lines["Renda"]
    assert renda["realized_cents"] == 100000 and renda["month_realized_cents"] == 100000 and renda["unpaid_cents"] == 100000 and renda["paid_cents"] == 0
    # Ocorrências passadas sem documento: janeiro a setembro e 5 de novembro (hoje é 10/11).
    assert renda["missing_documents"] == 10 and renda["remaining_cents"] == 11 * 100000
    october = next(o for o in renda["occurrence_execution"] if o["date"] == "2026-10-05")
    assert october["state"] == "realizado" and october["linked_cents"] == 100000
    eletricidade = lines["Eletricidade"]
    assert next(o for o in eletricidade["occurrence_execution"] if o["date"] == "2026-10-15")["state"] == "parcial"
    detail = client.get(f"/api/budget-lines/{ids['rent_line']}/links").json()["links"]
    assert detail[0]["source_id"] == ids["oct"] and detail[0]["payment"] == "por_pagar" and detail[0]["label"] == "FT FT R/10" and detail[0]["decided_by"] == "Admin"


def test_reject_undo_batch_all_or_nothing_and_permissions(case):
    client, factory, ids = case
    pending = client.get(f"/api/budgets/{ids['budget']}/matching").json()["pending"]
    rent_nov = item(pending, ids["nov"])
    assert client.post(f"/api/budgets/{ids['budget']}/links/reject", json=body(rent_nov)).status_code == 200
    after = client.get(f"/api/budgets/{ids['budget']}/matching").json()
    left = [c["rule_date"] for p in after["pending"] + after["unbudgeted"] if p["source_id"] == ids["nov"] for c in p.get("candidates", [])]
    assert "2026-11-05" not in left
    rent = item(after["pending"], ids["oct"])
    power = item(after["pending"], ids["power_oct"])
    bad = body(power, amount_cents=99999999)
    assert client.post(f"/api/budgets/{ids['budget']}/links/batch", json={"items": [body(rent), bad]}).status_code == 409
    with factory() as db:
        assert not db.scalar(select(func.count()).select_from(BudgetLink).where(BudgetLink.status == "confirmada"))
    assert client.post(f"/api/budgets/{ids['budget']}/links/batch", json={"items": [body(rent), body(power)]}).json()["confirmed"] == 2
    link = client.get(f"/api/budget-lines/{ids['rent_line']}/links").json()["links"][0]
    assert client.post(f"/api/budget-links/{link['id']}/undo", json={"reason": ""}).status_code == 422
    assert client.post(f"/api/budget-links/{link['id']}/undo", json={"reason": "Documento errado"}).status_code == 200
    assert item(client.get(f"/api/budgets/{ids['budget']}/matching").json()["pending"], ids["oct"])["remaining_cents"] == 100000
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get(f"/api/budgets/{ids['budget']}/matching").status_code == 403


def test_links_follow_an_approved_revision_and_only_the_version_in_force_accepts_links(case):
    client, factory, ids = case
    pending = client.get(f"/api/budgets/{ids['budget']}/matching").json()["pending"]
    assert client.post(f"/api/budgets/{ids['budget']}/links", json=body(item(pending, ids["oct"]))).status_code == 200
    assert client.post(f"/api/budgets/{ids['budget']}/approve", json={"revision": 0}).status_code == 200
    revised = client.post(f"/api/budgets/{ids['budget']}/revise", json={"revision": 1, "reason": "Renda atualizada"}).json()
    draft = client.get(f"/api/budgets/overview?year=2026&budget_id={revised['id']}").json()
    # O rascunho mostra o realizado da linha de origem; não recebe associações enquanto a versão aprovada está em vigor.
    assert next(r for r in draft["lines"] if r["description"] == "Renda")["realized_cents"] == 100000
    power = item(client.get(f"/api/budgets/{revised['id']}/matching").json()["pending"], ids["power_oct"])
    assert client.post(f"/api/budgets/{revised['id']}/links", json=body(power)).status_code == 409
    assert client.post(f"/api/budgets/{revised['id']}/approve", json={"revision": 0}).status_code == 200
    current = client.get("/api/budgets/overview?year=2026").json()
    assert current["budget"]["id"] == revised["id"]
    renda = next(r for r in current["lines"] if r["description"] == "Renda")
    assert renda["realized_cents"] == 100000 and renda["links"] == 1 and current["kpis"]["linked_cents"] == 100000
    with factory() as db:
        copies = list(db.scalars(select(BudgetLink).where(BudgetLink.budget_id == revised["id"])))
        assert len(copies) == 1 and copies[0].method == "revisao" and copies[0].copied_from_id
        # A versão revista mantém as suas associações (histórico) e a parcela continua a contar uma vez por versão.
        assert db.scalar(select(func.count()).select_from(BudgetLink).where(BudgetLink.budget_id == ids["budget"])) == 1
    assert client.post(f"/api/budgets/{ids['budget']}/links", json=body(power)).status_code == 409  # Versão revista.
