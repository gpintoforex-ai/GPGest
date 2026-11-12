"""Mapa de custos e proveitos modernizado: os totais são os de sempre; subtotais, fora dos custos, indicadores,
comparação com o ano anterior, orçamento, alertas configuráveis e coerência com os movimentos da rubrica.
Dados fictícios."""

from collections import defaultdict

import pytest
from app.management_maps import cost_documents
from app.models import (
    Access,
    Audit,
    BankAccount,
    BankBookEntry,
    BusinessEntity,
    Category,
    Purchase,
    Store,
    User,
)
from sqlalchemy import select

PERIOD = {"start": "2026-07-01", "end": "2026-10-31"}
SUPPLIERS = {"Senhorio": "999999990", "Seguradora": "999999991", "Grossista": "999999992", "Desconhecido": "999999993"}


@pytest.fixture
def case(setup):
    client, _, factory = setup
    assert client.post("/api/settings/fiscal", json={"vat_regime": "isento_53", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        rubrics = {
            "Rendas": Category(name="Rendas", fixed_cost=True),
            "Seguros": Category(name="Seguros"),
            "Mercadorias": Category(name="Mercadorias", nature="mercadorias", in_operating_costs=False, in_purchases=True),
            "IVA": Category(name="IVA", nature="fiscal", in_operating_costs=False, in_fiscal=True),
            "Comunicações": Category(name="Comunicações"),
        }
        db.add_all(rubrics.values())
        db.flush()
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        admin = db.scalar(select(User).where(User.email == "admin@test.pt"))
        manager = User(name="Gestora", email="gestora@test.pt", role="gestor", password_hash=admin.password_hash)
        db.add(manager)
        db.flush()
        db.add(Access(user_id=manager.id, store_id=store))
        for name, rubric in (("Senhorio", "Rendas"), ("Seguradora", "Seguros"), ("Grossista", "Mercadorias")):
            db.add(BusinessEntity(name=name, tax_number=SUPPLIERS[name], is_supplier=True, category_id=rubrics[rubric].id))
        db.add(BusinessEntity(name="Desconhecido", tax_number=SUPPLIERS["Desconhecido"], is_supplier=True))

        def buy(supplier, number, day, cents):
            purchase = Purchase(store_id=store, supplier_nif=SUPPLIERS[supplier], supplier_name=supplier, document=number, document_date=day,
                                amount_cents=cents, tax_cents=0, base_cents=cents, origin="manual")
            db.add(purchase)
            db.flush()
            return purchase.id

        ids = {}
        for month in ("07", "08", "09", "10"):
            buy("Senhorio", f"FR {month}", f"2026-{month}-05", 50000)
            buy("Seguradora", f"FS 25{month}", f"2025-{month}-06", 10000)  # ano anterior
        buy("Seguradora", "FS 1", "2026-07-06", 10000)
        ids["dup1"] = buy("Seguradora", "FS 2", "2026-08-03", 10000)
        ids["dup2"] = buy("Seguradora", "FS 3", "2026-08-05", 10000)  # mesmo valor, dois dias depois: possível duplicado
        buy("Seguradora", "FS 4", "2026-09-06", 10000)
        ids["october"] = buy("Seguradora", "FS 5", "2026-10-06", 60000)  # muito acima da média
        buy("Grossista", "FG 1", "2026-09-10", 200000)
        buy("Desconhecido", "FX 1", "2026-09-12", 3000)  # sem rubrica
        account = BankAccount(store_id=store, name="Banco", bank="Banco Exemplo", iban_last4="0001")
        db.add(account)
        db.flush()
        db.add(BankBookEntry(account_id=account.id, user_id=admin.id, movement_date="2026-09-20", amount_cents=-30000, kind="payment",
                             description="PAGAMENTO IVA", reference="", request_key="iva-3t", category_id=rubrics["IVA"].id, counts_as_cost=True))
        ids |= {name: rubric.id for name, rubric in rubrics.items()}
        ids["store"] = store
    return client, factory, ids


def costs(client, **params):
    response = client.get("/api/management/costs-revenues", params={**PERIOD, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_totals_unchanged_breakdown_and_movements_match(case):
    client, factory, ids = case
    data = costs(client)
    assert [p["key"] for p in data["periods"]] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    # Os totais por rubrica são os do cálculo de sempre (mesmos documentos, mesmo critério).
    expected = defaultdict(int)
    with factory() as db:
        from datetime import date
        for _, _, shares in cost_documents(db, [ids["store"]], date(2026, 7, 1), date(2026, 10, 31)):
            for category, base, tax in shares:
                if category != ids["IVA"]:
                    expected[category or ""] += base + tax
    assert {(r["category_id"] or ""): r["total_cents"] for r in data["costs"]} == dict(expected)
    rows = {r["category_name"]: r for r in data["costs"]}
    assert rows["Seguros"]["values"] == {"2026-07": 10000, "2026-08": 20000, "2026-09": 10000, "2026-10": 60000}
    assert rows["Mercadorias"]["group"] == "purchases" and rows["Rendas"]["fixed"] and rows["Seguros"]["documents"]["2026-08"] == 2
    # Subtotais sem dupla contagem: compras + despesas operacionais = total de custos, período a período.
    for key, total in data["total_costs"].items():
        assert data["subtotals"]["purchases"][key] + data["subtotals"]["operating"][key] == total
    kpis = data["kpis"]
    assert (kpis["fixed_cents"], kpis["merchandise_cents"], kpis["unclassified_cents"]) == (200000, 200000, 3000)
    assert kpis["costs_cents"] == data["totals"]["costs_cents"] == 200000 + 100000 + 200000 + 3000
    assert kpis["margin_cents"] == -kpis["costs_cents"] and kpis["margin_percent"] is None  # sem proveitos
    # IVA ao Estado: fora dos custos, à parte e pelo valor pago.
    assert "IVA" not in rows and data["outside"][0]["category_name"] == "IVA" and data["outside"][0]["kind"] == "fiscal"
    assert data["outside"][0]["values"]["2026-09"] == 30000 and data["state_payments"]["2026-09"] == 30000
    assert any(r["category_name"] == "Comunicações" for r in data["empty_rubrics"])
    # Os movimentos da rubrica (vista «costs») somam exatamente cada célula do mapa, sem registos repetidos.
    movements = client.get("/api/rubric-movements", params={**PERIOD, "view": "costs"}).json()["rows"]
    assert len({m["id"] for m in movements}) == len(movements)
    cells = defaultdict(int)
    for movement in movements:
        for share in movement["shares"]:
            cells[(share["category_id"] or "", movement["date"][:7])] += share["amount_cents"]
    for row in data["costs"]:
        for key, value in row["values"].items():
            assert cells[(row["category_id"] or "", key)] == value
    fiscal = client.get("/api/rubric-movements", params={**PERIOD, "view": "all", "category": ids["IVA"]}).json()
    assert fiscal["total_cents"] == 30000


def test_compare_alerts_and_alert_settings(case):
    client, factory, ids = case
    data = costs(client, compare="true")
    previous = data["previous"]
    assert previous["has_data"] and previous["costs"][ids["Seguros"]]["total_cents"] == 40000
    assert previous["costs"][ids["Seguros"]]["values"]["2026-07"] == 10000  # chaves dos períodos atuais
    alerts = {(a["kind"], a["category_name"], a["period"]) for a in data["alerts"]}
    assert ("above_average", "Seguros", "2026-10") in alerts
    assert ("month_change", "Seguros", "2026-08") in alerts
    assert ("increase", "Seguros", None) in alerts
    assert ("unclassified", "Por classificar", None) in alerts
    duplicate = next(a for a in data["alerts"] if a["kind"] == "duplicate")
    assert sorted(duplicate["ids"]) == sorted([ids["dup1"], ids["dup2"]]) and duplicate["tone"] == "warning"
    assert all(a["tone"] in ("info", "warning") for a in data["alerts"])  # sinais para rever, nunca erros
    settings = client.get("/api/management/alert-settings").json()
    assert settings["can_edit"] and settings["revision"] == 0
    body = {"above_average_percent": 500, "month_change_percent": 500, "increase_percent": 500, "minimum_cents": 1_000_000,
            "duplicate_days": 0, "revision": 0}
    assert client.post("/api/auth/login", json={"email": "gestora@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.put("/api/management/alert-settings", json=body).status_code == 403
    assert client.post("/api/auth/login", json={"email": "admin@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.put("/api/management/alert-settings", json=body).json()["revision"] == 1
    assert client.put("/api/management/alert-settings", json=body).status_code == 409  # revisão antiga
    assert [a["kind"] for a in costs(client, compare="true")["alerts"]] == ["unclassified"]
    with factory() as db:
        assert db.scalar(select(Audit).where(Audit.action == "limites_alertas_mapas"))


def test_budget_and_reclassification_update_the_map(case):
    client, factory, ids = case
    budget = client.post("/api/budgets", json={"year": 2026}).json()
    line = {"category_id": ids["Seguros"], "description": "Seguro multirriscos", "amount_cents": 15000, "vat_rate": "0",
            "periodicity": "mensal", "start_date": "2026-01-05"}
    assert client.post(f"/api/budgets/{budget['id']}/lines", json=line).status_code == 200
    data = costs(client)
    assert data["budget"]["available"] and data["budget"]["values"][ids["Seguros"]]["2026-10"] == 15000
    assert data["budget"]["totals"]["2026-07"] == 15000 and not data["budget"]["partial"]
    assert costs(client, group="quarter")["budget"]["available"] is False
    before = data["totals"]
    preview = client.post("/api/rubric-movements/preview", json={"ids": [ids["october"]], "category_id": ids["Rendas"]}).json()
    applied = client.post("/api/rubric-movements/apply", json={"ids": [ids["october"]], "category_id": ids["Rendas"], "fingerprint": preview["fingerprint"],
                                                               "reason": "Pagamento era da renda da loja", "origin": "mapas_de_gestao"})
    assert applied.status_code == 200, applied.text
    after = costs(client)
    rows = {r["category_name"]: r for r in after["costs"]}
    assert rows["Rendas"]["values"]["2026-10"] == 110000 and rows["Seguros"]["values"]["2026-10"] == 0
    assert after["totals"] == before  # só muda a rubrica: totais, proveitos e margem iguais
    with factory() as db:
        audit = db.scalar(select(Audit).where(Audit.action == "rubrica_movimento_alterada"))
        assert '"origem": "mapas_de_gestao"' in audit.detail
        assert db.get(Purchase, ids["october"]).amount_cents == 60000
