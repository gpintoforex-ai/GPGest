"""Natureza das rubricas: pagamentos de IVA ao Estado saem dos custos operacionais, mercadorias passam para compras e
investimentos; simulação antes/depois, aprovação de administrador com motivo e auditoria; tesouraria, extratos e IVA
calculado pelos documentos ficam iguais."""

import json
from datetime import date

import pytest
from app import dashboard
from app.models import Audit, BankAccount, BankBookEntry, BusinessEntity, Category, Purchase, Store, User
from sqlalchemy import select

SEPTEMBER = {"start": "2026-09-01", "end": "2026-09-30"}
IVA, TSU, GOODS, RENT = 201852, 48650, 10000, 50000


@pytest.fixture
def case(setup, monkeypatch):
    client, _, factory = setup
    monkeypatch.setattr(dashboard, "today", lambda: date(2026, 11, 10))
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        rubrics = {name: Category(name=name) for name in ("IVA", "Impostos", "Mercadorias", "Rendas")}
        db.add_all(rubrics.values())
        db.flush()
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        admin = db.scalar(select(User).where(User.email == "admin@test.pt")).id
        db.add(BusinessEntity(name="Grossista, Lda", tax_number="510774318", is_supplier=True, category_id=rubrics["Mercadorias"].id))
        db.add(BusinessEntity(name="Senhorio, Lda", tax_number="503504564", is_supplier=True, category_id=rubrics["Rendas"].id))
        db.add(Purchase(store_id=store, supplier_nif="510774318", supplier_name="Grossista, Lda", document="FT 1", document_date="2026-09-10",
                        amount_cents=GOODS + 2300, tax_cents=2300, base_cents=GOODS, origin="manual"))
        db.add(Purchase(store_id=store, supplier_nif="503504564", supplier_name="Senhorio, Lda", document="FT 2", document_date="2026-09-05",
                        amount_cents=RENT + 11500, tax_cents=11500, base_cents=RENT, origin="manual"))
        account = BankAccount(store_id=store, name="BPI", bank="BPI", iban_last4="1234")
        db.add(account)
        db.flush()
        for key, rubric, amount, text in (("iva", "IVA", IVA, "PAGAMENTO AO ESTADO IVA"), ("tsu", "Impostos", TSU, "SEGURANCA SOCIAL")):
            db.add(BankBookEntry(account_id=account.id, user_id=admin, movement_date="2026-09-21", amount_cents=-amount, kind="payment",
                                 description=text, reference=key.upper(), request_key="nature-" + key, category_id=rubrics[rubric].id,
                                 counts_as_cost=True))
        ids = {name: row.id for name, row in rubrics.items()}
    return client, factory, ids


def overview(client):
    response = client.get("/api/dashboard/overview", params=SEPTEMBER)
    assert response.status_code == 200, response.text
    return response.json()


def views(data):
    return {view["key"]: view["total_cents"] for view in data["expenses"]["views"]}


def proposal(client, ids):
    listing = client.get("/api/rubric-nature").json()
    by_name = {row["name"]: row for row in listing["rubrics"]}
    assert by_name["IVA"]["suggestion"]["nature"] == "fiscal" and by_name["Mercadorias"]["suggestion"]["nature"] == "mercadorias"
    assert by_name["Impostos"]["suggestion"] is None and by_name["Rendas"]["suggestion"] is None  # Impostos fica nos custos.
    changes = []
    for name in ("IVA", "Mercadorias"):
        suggested = {key: value for key, value in by_name[name]["suggestion"].items() if key != "reason"}
        changes.append({"category_id": ids[name], **suggested})
    return changes


def test_defaults_keep_previous_behaviour_and_suggestions_are_never_applied(case):
    client, factory, ids = case
    data = overview(client)
    # Por omissão (natureza por definir) tudo conta como antes: o IVA pago ao Estado está nos custos.
    assert data["expenses"]["total_cents"] == IVA + TSU + GOODS + RENT
    assert views(data) == {"operating": IVA + TSU + GOODS + RENT, "purchases": 0, "fiscal": 0, "treasury": IVA + TSU}
    assert data["expenses"]["rubrics_without_nature"] >= 4
    proposal(client, ids)
    with factory() as db:
        assert {row.nature for row in db.scalars(select(Category))} == {""}


def test_simulation_shows_before_after_and_affected_records_without_writing(case):
    client, factory, ids = case
    changes = proposal(client, ids)
    result = client.post("/api/rubric-nature/simulate", json={"changes": changes})
    assert result.status_code == 200, result.text
    body = result.json()
    totals = {row["key"]: (row["before_cents"], row["after_cents"]) for row in body["views"]}
    assert totals["operating"] == (IVA + TSU + GOODS + RENT, TSU + RENT)
    assert totals["purchases"] == (0, GOODS) and totals["fiscal"] == (0, IVA)
    assert totals["treasury"] == (IVA + TSU, IVA + TSU)  # Saídas de tesouraria iguais.
    assert totals["costs"] == (IVA + TSU + GOODS + RENT, TSU + GOODS + RENT)  # Mapa de custos e proveitos: mercadorias ficam.
    assert totals["budget"][0] == totals["budget"][1]  # Orçamento de pagamentos: IVA continua a ser um pagamento.
    assert body["affected"]["count"] == 2
    assert {row["label"] for row in body["affected"]["rows"]} == {"IVA", "FT 1"}
    with factory() as db:
        assert {row.nature for row in db.scalars(select(Category))} == {""}
        assert not db.scalar(select(Audit).where(Audit.action == "rubrica_natureza_alterada"))
    # Sem alterações reais: recusa.
    same = [{**changes[0], "nature": "", "in_operating_costs": True, "in_purchases": False, "in_treasury": True, "in_fiscal": False}]
    assert client.post("/api/rubric-nature/simulate", json={"changes": same}).status_code == 422


def test_apply_requires_administrator_reason_and_unchanged_configuration(case):
    client, factory, ids = case
    changes = proposal(client, ids)
    fingerprint = client.post("/api/rubric-nature/simulate", json={"changes": changes}).json()["fingerprint"]
    assert client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": fingerprint, "reason": "ok"}).status_code == 422
    assert client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": "0" * 64,
                                                         "reason": "Separar IVA dos custos"}).status_code == 409
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.post("/api/rubric-nature/simulate", json={"changes": changes}).status_code == 403
    assert client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": fingerprint, "reason": "Separar IVA"}).status_code == 403
    assert client.post("/api/auth/login", json={"email": "admin@test.pt", "password": "PasswordTest123!"}).status_code == 200
    before = overview(client)
    applied = client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": fingerprint, "reason": "Separar IVA dos custos"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == 2
    with factory() as db:
        iva = db.get(Category, ids["IVA"])
        assert (iva.nature, iva.in_operating_costs, iva.in_fiscal, iva.in_treasury, iva.in_payment_budget) == ("fiscal", False, True, True, True)
        audits = list(db.scalars(select(Audit).where(Audit.action == "rubrica_natureza_alterada")))
        assert len(audits) == 2
        detail = json.loads(next(a.detail for a in audits if a.target_id == ids["IVA"]))
        assert detail["reason"] == "Separar IVA dos custos" and detail["before"]["nature"] == "" and detail["after"]["nature"] == "fiscal"
        assert detail["simulation"]["operating"] == [IVA + TSU + GOODS + RENT, TSU + RENT]
        # Os registos não mudam: o movimento continua na rubrica IVA e marcado como custo sem documento.
        entry = db.scalar(select(BankBookEntry).where(BankBookEntry.request_key == "nature-iva"))
        assert entry.category_id == ids["IVA"] and entry.counts_as_cost and entry.amount_cents == -IVA
    after = overview(client)
    assert views(after) == {"operating": TSU + RENT, "purchases": GOODS, "fiscal": IVA, "treasury": IVA + TSU}
    assert after["expenses"]["total_cents"] == TSU + RENT
    # Tesouraria e previsão de IVA (pelos documentos) iguais.
    assert after["treasury"]["paid_cents"] == before["treasury"]["paid_cents"]
    assert {k: v for k, v in after["vat"].items() if k != "meta"} == {k: v for k, v in before["vat"].items() if k != "meta"}
    fiscal = next(view for view in after["expenses"]["views"] if view["key"] == "fiscal")
    assert fiscal["vat"]["payments_cents"] == IVA and fiscal["vat"]["input_cents"] == 2300 + 11500
    # Simulação antiga já não serve (a configuração mudou).
    assert client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": fingerprint,
                                                         "reason": "Repetir"}).status_code == 422


def test_drilldown_by_view_and_management_map_exclusion(case):
    client, _, ids = case
    changes = proposal(client, ids)
    fingerprint = client.post("/api/rubric-nature/simulate", json={"changes": changes}).json()["fingerprint"]
    assert client.post("/api/rubric-nature/apply", json={"changes": changes, "fingerprint": fingerprint, "reason": "Separar IVA"}).status_code == 200
    route = "/api/dashboard/expenses/drilldown"
    operating = client.get(route, params=SEPTEMBER).json()
    assert {row["label"] for row in operating["rows"]} == {"Impostos", "Rendas"}
    fiscal = client.get(route, params={**SEPTEMBER, "view": "fiscal", "category": ids["IVA"]}).json()
    assert fiscal["vat_basis"] == "valor pago" and fiscal["total_cents"] == IVA
    assert [(row["source"], row["entity"]) for row in fiscal["rows"]] == [("Banco", "PAGAMENTO AO ESTADO IVA")]
    purchases = client.get(route, params={**SEPTEMBER, "view": "purchases"}).json()
    assert [row["label"] for row in purchases["rows"]] == ["Mercadorias"]
    treasury = client.get(route, params={**SEPTEMBER, "view": "treasury"}).json()
    assert treasury["total_cents"] == IVA + TSU
    assert client.get(route, params={**SEPTEMBER, "view": "outra"}).status_code == 422
    maps = client.get("/api/management/costs-revenues", params={**SEPTEMBER, "group": "month"}).json()
    assert maps["totals"]["costs_cents"] == TSU + GOODS + RENT
    assert [row["category_name"] for row in maps["excluded"]] == ["IVA"] and maps["notes"][0].startswith("Fora dos custos")
