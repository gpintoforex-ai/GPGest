"""Movimentos da rubrica: listagem igual ao gráfico, correção individual e em lote pelo circuito de cada origem
(documento: repartição de gestão; banco/caixa: rubrica de análise, só administrador), valores e tesouraria intactos,
histórico e reversão, centro de custo e observações, sub-rubricas. Dados fictícios."""

import json
from datetime import date

import pytest
from app import dashboard
from app.models import (
    Access,
    Audit,
    BankAccount,
    BankBookEntry,
    BusinessEntity,
    Category,
    CostCenter,
    Purchase,
    PurchaseRubric,
    RubricReclassification,
    Store,
    User,
)
from sqlalchemy import select

SEPTEMBER = {"start": "2026-09-01", "end": "2026-09-30"}


@pytest.fixture
def case(setup, monkeypatch):
    client, _, factory = setup
    monkeypatch.setattr(dashboard, "today", lambda: date(2026, 11, 10))
    assert client.post("/api/settings/fiscal", json={"vat_regime": "isento_53", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        rubrics = {name: Category(name=name) for name in ("Seguros", "Outros", "Impostos", "IVA")}
        db.add_all(rubrics.values())
        db.flush()
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        admin = db.scalar(select(User).where(User.email == "admin@test.pt"))
        manager = User(name="Gestora", email="gestora@test.pt", role="gestor", password_hash=admin.password_hash)
        db.add(manager)
        db.flush()
        db.add(Access(user_id=manager.id, store_id=store))
        db.add(BusinessEntity(name="Seguradora Exemplo", tax_number="999999990", is_supplier=True, category_id=rubrics["Outros"].id))
        purchase = Purchase(store_id=store, supplier_nif="999999990", supplier_name="Seguradora Exemplo", document="FT 9/1",
                            document_date="2026-09-05", amount_cents=12300, tax_cents=2300, base_cents=10000, origin="manual")
        account = BankAccount(store_id=store, name="Banco", bank="BPI", iban_last4="0001")
        db.add_all([purchase, account])
        db.flush()
        entry = BankBookEntry(account_id=account.id, user_id=admin.id, movement_date="2026-09-20", amount_cents=-20000, kind="payment",
                              description="PAGAMENTO AO ESTADO IVA 3T", reference="", request_key="mov-iva", category_id=rubrics["Impostos"].id,
                              counts_as_cost=True)
        db.add(entry)
        db.flush()
        ids = {"purchase": purchase.id, "bank": "bank:" + entry.id, "entry": entry.id, **{n: r.id for n, r in rubrics.items()}}
    return client, factory, ids


def login(client, email):
    assert client.post("/api/auth/login", json={"email": email, "password": "PasswordTest123!"}).status_code == 200


def listing(client, **params):
    response = client.get("/api/rubric-movements", params={**SEPTEMBER, **params})
    assert response.status_code == 200, response.text
    return response.json()


def operating(client):
    data = client.get("/api/dashboard/overview", params=SEPTEMBER).json()["expenses"]
    return {r["name"]: r["amount_cents"] for r in data["rows"]}, data


def test_listing_matches_chart_and_shows_edit_modes(case):
    client, _, ids = case
    data = listing(client)
    rows = {r["id"]: r for r in data["rows"]}
    purchase, bank = rows[ids["purchase"]], rows[ids["bank"]]
    assert (purchase["mode"], purchase["editable"], purchase["origin_label"], purchase["state"]) == ("purchase", True, "Compra", "por_pagar")
    assert purchase["shares"][0]["name"] == "Outros" and purchase["amount_cents"] == 12300 and purchase["tax_cents"] == 2300
    assert (bank["mode"], bank["editable"], bank["origin_label"], bank["account"]) == ("bank", True, "Banco", "BPI · Banco")
    chart, _ = operating(client)
    assert data["total_cents"] == sum(chart.values())
    only = listing(client, category=ids["Outros"])
    assert [r["id"] for r in only["rows"]] == [ids["purchase"]] and only["total_cents"] == chart["Outros"]


def test_batch_change_keeps_values_and_treasury_and_respects_permissions(case):
    client, factory, ids = case
    before_chart, before = operating(client)
    paid_before = client.get("/api/dashboard/overview", params=SEPTEMBER).json()["treasury"]
    login(client, "gestora@test.pt")
    preview = client.post("/api/rubric-movements/preview", json={"ids": [ids["purchase"], ids["bank"]], "category_id": ids["Seguros"]}).json()
    assert preview["count"] == 1 and preview["blocked"] == 1 and preview["approval"] == [ids["bank"]]
    login(client, "admin@test.pt")
    preview = client.post("/api/rubric-movements/preview", json={"ids": [ids["purchase"], ids["bank"]], "category_id": ids["Seguros"]}).json()
    assert preview["count"] == 2 and preview["total_cents"] == 12300 + 20000
    assert {i["id"]: i["before_names"] for i in preview["items"]} == {ids["purchase"]: ["Outros"], ids["bank"]: ["Impostos"]}
    body = {"ids": [ids["purchase"], ids["bank"]], "category_id": ids["Seguros"], "fingerprint": preview["fingerprint"]}
    assert client.post("/api/rubric-movements/apply", json={**body, "reason": "jjjj"}).status_code == 422
    assert client.post("/api/rubric-movements/apply", json={**body, "fingerprint": "0" * 64, "reason": "Corrigir seguros"}).status_code == 409
    applied = client.post("/api/rubric-movements/apply", json={**body, "reason": "Corrigir seguros mal classificados"})
    assert applied.status_code == 200, applied.text
    assert applied.json() == {"applied": 2, "skipped": 0}
    with factory() as db:
        purchase = db.get(Purchase, ids["purchase"])
        entry = db.get(BankBookEntry, ids["entry"])
        # Valores e registos intactos: só muda a classificação de gestão/análise.
        assert (purchase.amount_cents, entry.amount_cents, entry.category_id) == (12300, -20000, ids["Impostos"])
        assert [r.category_id for r in db.scalars(select(PurchaseRubric).where(PurchaseRubric.purchase_id == ids["purchase"]))] == [ids["Seguros"]]
        assert db.scalar(select(RubricReclassification).where(RubricReclassification.source_id == ids["entry"])).to_category_id == ids["Seguros"]
        assert len(list(db.scalars(select(Audit).where(Audit.action == "rubrica_movimento_alterada")))) == 2
    after_chart, after = operating(client)
    assert after_chart == {"Seguros": 12300 + 20000} and after["total_cents"] == before["total_cents"]
    assert client.get("/api/dashboard/overview", params=SEPTEMBER).json()["treasury"] == paid_before  # Tesouraria igual.
    assert before_chart != after_chart


def test_history_revert_and_annotation(case):
    client, factory, ids = case
    preview = client.post("/api/rubric-movements/preview", json={"ids": [ids["purchase"], ids["bank"]], "category_id": ids["Seguros"]}).json()
    client.post("/api/rubric-movements/apply", json={"ids": [ids["purchase"], ids["bank"]], "category_id": ids["Seguros"],
                                                     "fingerprint": preview["fingerprint"], "reason": "Corrigir seguros mal classificados"})
    entries = client.get("/api/rubric-movements/history", params={"id": ids["purchase"]}).json()["entries"]
    change = next(e for e in entries if e["action"] == "classificacao_rubricas")
    assert change["before"] == ["Rubrica do fornecedor"] and change["after"] == ["Seguros"] and change["revertible"]
    assert client.post("/api/rubric-movements/revert", json={"id": ids["purchase"], "audit_id": change["id"], "reason": "Afinal estava certo"}).status_code == 200
    bank_entries = client.get("/api/rubric-movements/history", params={"id": ids["bank"]}).json()["entries"]
    bank_change = next(e for e in bank_entries if e["action"] == "pagamento_rubrica_reclassificada")
    assert client.post("/api/rubric-movements/revert", json={"id": ids["bank"], "audit_id": bank_change["id"], "reason": "Afinal era IVA"}).status_code == 200
    chart, _ = operating(client)
    assert chart == {"Outros": 12300, "Impostos": 20000}
    with factory() as db:
        assert not db.scalar(select(PurchaseRubric).where(PurchaseRubric.purchase_id == ids["purchase"]))
        center = CostCenter(code="LJ1", name="Loja 1")
        db.add(center)
        db.commit()
        center_id = center.id
    body = {"id": ids["purchase"], "cost_center_id": center_id, "notes": "Apólice da viatura", "revision": 0}
    assert client.put("/api/rubric-movements/annotation", json=body).json() == {"revision": 1}
    assert client.put("/api/rubric-movements/annotation", json=body).status_code == 409  # Revisão antiga.
    row = next(r for r in listing(client)["rows"] if r["id"] == ids["purchase"])
    assert row["annotation"] == {"cost_center_id": center_id, "notes": "Apólice da viatura", "revision": 1}


def test_parent_rubric_validation_and_aggregation(case):
    client, factory, ids = case
    child = client.post("/api/tables/categories/records", json={"values": {"name": "Seguros auto", "icon": "shield", "color": "#245fc5",
                                                                            "parent_id": ids["Seguros"]}})
    assert child.status_code == 200, child.text
    child_id = child.json()["id"]
    grandchild = client.post("/api/tables/categories/records", json={"values": {"name": "Seguro frota", "icon": "tag", "color": "#245fc5",
                                                                                 "parent_id": child_id}})
    assert grandchild.status_code == 422 and "um só nível" in grandchild.text
    with factory.begin() as db:
        db.add(PurchaseRubric(purchase_id=ids["purchase"], category_id=child_id, amount_cents=12300,
                              created_by=db.scalar(select(User).where(User.email == "admin@test.pt")).id))
    data = client.get("/api/dashboard/overview", params=SEPTEMBER).json()["expenses"]
    assert data["has_parents"] is True
    operating_view = next(v for v in data["views"] if v["key"] == "operating")
    assert {r["name"] for r in operating_view["rows"]} == {"Seguros › Seguros auto", "Impostos"}
    parents = {r["name"]: r for r in operating_view["parent_rows"]}
    assert parents["Seguros"]["amount_cents"] == 12300 and parents["Seguros"]["children"] == 1
    row = next(r for r in listing(client)["rows"] if r["id"] == ids["purchase"])
    assert row["shares"][0]["name"] == "Seguros › Seguros auto"
    assert json.loads(json.dumps(row))  # serializável
