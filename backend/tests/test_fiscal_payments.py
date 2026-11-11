"""Pagamentos ao Estado em rubricas de custos (ex.: IVA em «Impostos», com a TSU): deteção pela descrição, criação da
rubrica fiscal, reclassificação pré-visualizada e auditada sem alterar o registo bancário, anulação; mais sugestões de
natureza e avisos na simulação."""

import json
from datetime import date

import pytest
from app import dashboard
from app.models import (
    Audit,
    BankAccount,
    BankBookEntry,
    Category,
    RubricReclassification,
    Store,
    User,
)
from sqlalchemy import select

SEPTEMBER = {"start": "2026-09-01", "end": "2026-09-30"}
IVA, TSU, STATE = 201852, 48650, 30000


@pytest.fixture
def case(setup, monkeypatch):
    client, _, factory = setup
    monkeypatch.setattr(dashboard, "today", lambda: date(2026, 11, 10))
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    names = ("Impostos", "Vendas", "Imobilizado", "Transferências e fundos", "Vencimentos")
    with factory.begin() as db:
        rubrics = {name: Category(name=name) for name in names}
        db.add_all(rubrics.values())
        db.flush()
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        admin = db.scalar(select(User).where(User.email == "admin@test.pt")).id
        account = BankAccount(store_id=store, name="BPI", bank="BPI", iban_last4="1234")
        db.add(account)
        db.flush()
        rows = (("iva", "Impostos", -IVA, "PAG ESTADO IVA 3T 2026 REF 123456789", True),
                ("tsu", "Impostos", -TSU, "SEGURANCA SOCIAL 202608", True),
                ("at", "Impostos", -STATE, "PAGAMENTO AT 998877", False),  # Pagamento ao Estado registado como «não é custo».
                ("venda", "Vendas", 10000, "TPA VENDAS", False))
        for key, rubric, amount, text, cost in rows:
            db.add(BankBookEntry(account_id=account.id, user_id=admin, movement_date="2026-09-21", amount_cents=amount,
                                 kind="payment" if amount < 0 else "receipt", description=text, reference="", request_key="fp-" + key,
                                 category_id=rubrics[rubric].id, counts_as_cost=cost))
        db.flush()
        entries = {key: db.scalar(select(BankBookEntry.id).where(BankBookEntry.request_key == "fp-" + key)) for key, *_ in rows}
        ids = {name: row.id for name, row in rubrics.items()}
    return client, factory, ids, entries


def views(client):
    response = client.get("/api/dashboard/overview", params=SEPTEMBER)
    assert response.status_code == 200, response.text
    return {view["key"]: view["total_cents"] for view in response.json()["expenses"]["views"]}


def login(client, email):
    assert client.post("/api/auth/login", json={"email": email, "password": "PasswordTest123!"}).status_code == 200


def test_more_nature_suggestions(case):
    client, *_ = case
    rubrics = {row["name"]: row["suggestion"] for row in client.get("/api/rubric-nature").json()["rubrics"]}
    assert rubrics["Imobilizado"]["nature"] == "investimento" and rubrics["Imobilizado"]["in_purchases"]
    assert rubrics["Transferências e fundos"]["nature"] == "financeiro" and not rubrics["Transferências e fundos"]["in_operating_costs"]
    assert rubrics["Vencimentos"]["nature"] == "pessoal" and rubrics["Vencimentos"]["in_operating_costs"]
    assert rubrics["Impostos"] is None and rubrics["Vendas"] is None


def test_simulation_warnings(case):
    client, _, ids, _ = case
    nothing = {"in_operating_costs": False, "in_purchases": False, "in_treasury": False, "in_fiscal": False, "in_payment_budget": True}
    changes = [
        {"category_id": ids["Vendas"], "nature": "", "in_operating_costs": True, "in_purchases": False, "in_treasury": False,
         "in_fiscal": False, "in_payment_budget": True},
        {"category_id": ids["Imobilizado"], "nature": "investimento", "in_operating_costs": False, "in_purchases": False,
         "in_treasury": True, "in_fiscal": False, "in_payment_budget": True},
        {"category_id": ids["Impostos"], "nature": "", **nothing},
    ]
    body = client.post("/api/rubric-nature/simulate", json={"changes": changes}).json()
    texts = {row["category_id"]: [] for row in body["warnings"]}
    for row in body["warnings"]:
        texts[row["category_id"]].append(row["text"])
    assert any("não muda nenhum valor" in t and "entradas" in t for t in texts[ids["Vendas"]])
    assert any("costuma entrar em «Análise de compras e investimentos»" in t for t in texts[ids["Imobilizado"]])
    assert any("sai dos custos operacionais sem entrar" in t for t in texts[ids["Imobilizado"]])
    assert any("deixa de aparecer em todas as vistas" in t for t in texts[ids["Impostos"]])


def test_candidates_fiscal_rubric_and_reclassification(case):
    client, factory, ids, entries = case
    listing = client.get("/api/rubric-nature/fiscal-payments").json()
    found = {row["id"]: row for row in listing["candidates"]}
    # IVA explícito primeiro; pagamento à AT sem imposto indicado com confiança média; TSU e recebimentos nunca.
    assert list(found) == ["bank:" + entries["iva"], "bank:" + entries["at"]]
    assert found["bank:" + entries["iva"]]["confidence"] == 90 and found["bank:" + entries["at"]]["confidence"] == 55
    assert found["bank:" + entries["iva"]]["rule"]["pattern"] == "pag estado iva ref"
    assert listing["fiscal_rubrics"] == [] and listing["can_apply"]
    before = views(client)
    assert before == {"operating": IVA + TSU, "purchases": 0, "fiscal": 0, "treasury": IVA + TSU + STATE}

    created = client.post("/api/rubric-nature/fiscal-rubric", json={"name": "IVA"})
    assert created.status_code == 200, created.text
    iva = created.json()["id"]
    assert client.post("/api/rubric-nature/fiscal-rubric", json={"name": "iva"}).status_code == 409
    with factory() as db:
        row = db.get(Category, iva)
        assert (row.nature, row.in_operating_costs, row.in_fiscal, row.in_treasury) == ("fiscal", False, True, True)

    selection = {"items": ["bank:" + entries["iva"], "bank:" + entries["at"]], "category_id": iva}
    preview = client.post("/api/rubric-nature/reclassify/preview", json=selection)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    totals = {row["key"]: (row["before_cents"], row["after_cents"]) for row in body["views"]}
    assert totals["operating"][0] - totals["operating"][1] == IVA  # O pagamento à AT não contava como custo.
    assert totals["fiscal"] == (0, IVA + STATE) and totals["treasury"][0] == totals["treasury"][1]
    assert body["total_cents"] == IVA + STATE and {row["from_name"] for row in body["rows"]} == {"Impostos"}
    with factory() as db:
        assert not db.scalar(select(RubricReclassification))

    confirm = {**selection, "fingerprint": body["fingerprint"], "reason": "Separar o IVA da TSU"}
    assert client.post("/api/rubric-nature/reclassify", json={**confirm, "fingerprint": "0" * 64}).status_code == 409
    assert client.post("/api/rubric-nature/reclassify", json={**confirm, "reason": "ok"}).status_code == 422
    login(client, "operator@test.pt")
    assert client.get("/api/rubric-nature/fiscal-payments").status_code == 403
    assert client.post("/api/rubric-nature/reclassify", json=confirm).status_code == 403
    login(client, "admin@test.pt")
    applied = client.post("/api/rubric-nature/reclassify", json=confirm)
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == 2
    assert views(client) == {"operating": TSU, "purchases": 0, "fiscal": IVA + STATE, "treasury": IVA + TSU + STATE}
    maps = client.get("/api/management/costs-revenues", params={**SEPTEMBER, "group": "month"}).json()
    assert maps["totals"]["costs_cents"] == TSU
    with factory() as db:
        # O registo bancário não muda: rubrica original, valor e marcação de custo intactos.
        entry = db.get(BankBookEntry, entries["iva"])
        assert (entry.category_id, entry.amount_cents, entry.counts_as_cost) == (ids["Impostos"], -IVA, True)
        audits = list(db.scalars(select(Audit).where(Audit.action == "pagamento_rubrica_reclassificada")))
        assert len(audits) == 2
        detail = json.loads(audits[0].detail)
        assert detail["reason"] == "Separar o IVA da TSU" and detail["after_category_id"] == iva
    listing = client.get("/api/rubric-nature/fiscal-payments").json()
    assert listing["candidates"] == [] and [r["name"] for r in listing["fiscal_rubrics"]] == ["IVA"]
    assert len(listing["history"]) == 2 and all(row["to_name"] == "IVA" and not row["undone_at"] for row in listing["history"])
    # Repetir para a mesma rubrica é recusado.
    assert client.post("/api/rubric-nature/reclassify/preview", json=selection).status_code == 422

    # Anular repõe a rubrica original nas análises.
    target = next(row for row in listing["history"] if row["record"] == "bank:" + entries["iva"])["id"]
    assert client.post(f"/api/rubric-nature/reclassifications/{target}/undo", json={"reason": "x"}).status_code == 422
    undone = client.post(f"/api/rubric-nature/reclassifications/{target}/undo", json={"reason": "Afinal era IRC"})
    assert undone.status_code == 200, undone.text
    assert client.post(f"/api/rubric-nature/reclassifications/{target}/undo", json={"reason": "Afinal era IRC"}).status_code == 409
    assert views(client) == {"operating": IVA + TSU, "purchases": 0, "fiscal": STATE, "treasury": IVA + TSU + STATE}
    with factory() as db:
        assert db.scalar(select(Audit).where(Audit.action == "pagamento_rubrica_reposta"))
