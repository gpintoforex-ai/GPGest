"""Classificação inteligente de rubricas: sugestões (regras, histórico, fornecedor), repartição validada,
gravação no sítio certo, regras aprovadas, lote tudo-ou-nada e efeito nos mapas de gestão."""

import pytest
from app import operation_trace
from app.models import (
    Audit,
    BusinessEntity,
    Category,
    ClassificationRule,
    DocumentERPLink,
    ERPDocument,
    ERPDocumentRubric,
    Purchase,
    PurchaseRubric,
    Store,
    User,
)
from app.rubric_expenses import erp_shares
from sqlalchemy import func, select

NIF = "510774318"


@pytest.fixture
def case(setup, monkeypatch):
    from datetime import date
    monkeypatch.setattr(operation_trace, "today", lambda: date(2026, 11, 10))
    client, _, factory = setup
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        energy, maintenance, fuel = Category(name="Energia"), Category(name="Manutenção"), Category(name="Combustíveis")
        db.add_all([energy, maintenance, fuel])
        db.flush()
        db.add(BusinessEntity(name="Elétrica", tax_number=NIF, is_supplier=True, category_id=fuel.id))
        # Histórico: dois documentos ERP repartidos 70/30 e um só em Energia.
        for index, (day, split) in enumerate((("2026-08-01", [(energy.id, 7000), (maintenance.id, 3000)]),
                                              ("2026-09-01", [(energy.id, 14000), (maintenance.id, 6000)]),
                                              ("2026-07-01", [(energy.id, 5000)]))):
            doc = ERPDocument(store_id=store, enterprise_id="e", external_id=f"h{index}", supplier_nif=NIF, supplier_name="Elétrica", document=f"FT H/{index}",
                              internal_document="", document_type="FT", document_date=day, amount_cents=sum(a for _, a in split))
            db.add(doc)
            db.flush()
            db.add_all([ERPDocumentRubric(erp_document_id=doc.id, category_id=c, amount_cents=a) for c, a in split])
        new = Purchase(store_id=store, supplier_nif=NIF, supplier_name="Elétrica", document="FT N/1", document_type="FT", document_date="2026-10-10",
                       amount_cents=12301, tax_cents=2300, base_cents=10001, origin="efatura")
        db.add(new)
        db.flush()
        ids = {"store": store, "energy": energy.id, "maintenance": maintenance.id, "fuel": fuel.id, "new": new.id}
    return client, factory, ids


def test_suggestions_rank_history_and_supplier_and_apply_percent_split_exactly(case):
    client, factory, ids = case
    data = client.get(f"/api/classification/suggest?kind=purchase&id={ids['new']}")
    assert data.status_code == 200, data.text
    body = data.json()
    assert body["target"] == "purchase" and body["total_cents"] == 12301 and body["current"] == []
    first, second = body["suggestions"][:2]
    assert first["source"] == "historico" and first["confidence"] == 75 and "2 de 3 documento(s)" in first["reasons"][0]
    assert {s["category_id"]: s["percent"] for s in first["shares"]} == {ids["energy"]: "70.00", ids["maintenance"]: "30.00"}
    assert sum(s["amount_cents"] for s in first["shares"]) == 12301
    assert second["source"] == "fornecedor" and second["shares"][0]["category_id"] == ids["fuel"]
    # Pendentes: aparece como rubrica do fornecedor (por confirmar), com a sugestão.
    pending = client.get("/api/classification/pending?start=2026-10-01&end=2026-10-31").json()
    row = next(r for r in pending["rows"] if r["id"] == ids["new"])
    assert row["state"] == "fornecedor" and not row["safe"] and row["suggestions"][0]["source"] == "historico"
    # Validação da repartição.
    shares = [{"category_id": ids["energy"], "value": "70"}, {"category_id": ids["maintenance"], "value": "20"}]
    assert client.post("/api/classification/apply", json={"kind": "purchase", "id": ids["new"], "mode": "percent", "shares": shares}).status_code == 422
    amounts = [{"category_id": ids["energy"], "value": "100.00"}, {"category_id": ids["maintenance"], "value": "23.00"}]
    assert client.post("/api/classification/apply", json={"kind": "purchase", "id": ids["new"], "mode": "amount", "shares": amounts}).status_code == 422
    shares[1]["value"] = "30"
    applied = client.post("/api/classification/apply", json={"kind": "purchase", "id": ids["new"], "mode": "percent", "shares": shares})
    assert applied.status_code == 200, applied.text
    stored = {s["category_id"]: s["amount_cents"] for s in applied.json()["shares"]}
    assert stored == {ids["energy"]: 8611, ids["maintenance"]: 3690} and sum(stored.values()) == 12301
    with factory() as db:
        assert {r.category_id: r.amount_cents for r in db.scalars(select(PurchaseRubric))} == stored
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "classificacao_rubricas")) == 1
    # Mapas: base e IVA repartidos na mesma proporção; rastreabilidade: rubrica confirmada; deixa de estar pendente.
    maps = client.get("/api/management/costs-revenues?start=2026-10-01&end=2026-10-31").json()
    costs = {c["category_id"]: c["total_cents"] for c in maps["costs"]}
    assert costs[ids["energy"]] + costs[ids["maintenance"]] == 10001 and ids["fuel"] not in costs
    trace = client.get("/api/operations?start=2026-10-01&end=2026-10-31").json()
    assert next(r for r in trace["rows"] if r["id"] == ids["new"])["dimensions"]["rubrica"]["state"] == "confirmada"
    assert ids["new"] not in [r["id"] for r in client.get("/api/classification/pending?start=2026-10-01&end=2026-10-31").json()["rows"]]


def test_saved_rule_becomes_top_suggestion_and_counts_uses(case):
    client, factory, ids = case
    shares = [{"category_id": ids["energy"], "value": "123.01"}]
    saved = client.post("/api/classification/apply", json={"kind": "purchase", "id": ids["new"], "mode": "amount", "shares": shares,
                                                          "save_rule": {"supplier": True, "document_type": True}, "rule_name": "Eletricidade"})
    assert saved.status_code == 200, saved.text
    rule_id = saved.json()["rule_id"]
    with factory.begin() as db:
        rule = db.get(ClassificationRule, rule_id)
        assert (rule.supplier_nif, rule.document_type, rule.keyword) == (NIF, "FT", None)
        other = Purchase(store_id=ids["store"], supplier_nif=NIF, supplier_name="Elétrica", document="FT N/2", document_type="FT",
                         document_date="2026-10-20", amount_cents=5000, tax_cents=0, base_cents=5000, origin="efatura")
        db.add(other)
        db.flush()
        other_id = other.id
    top = client.get(f"/api/classification/suggest?kind=purchase&id={other_id}").json()["suggestions"][0]
    # 92 pela regra (fornecedor + tipo) e +3 porque o histórico confirma a mesma repartição.
    assert top["source"] == "regra" and top["rule_id"] == rule_id and top["confidence"] == 95 and len(top["reasons"]) == 2
    pending = client.get("/api/classification/pending?start=2026-10-01&end=2026-10-31").json()
    assert next(r for r in pending["rows"] if r["id"] == other_id)["safe"] and pending["summary"]["seguras"] >= 1
    used = client.post("/api/classification/apply", json={"kind": "purchase", "id": other_id, "mode": "percent", "rule_id": rule_id,
                                                         "shares": [{"category_id": ids["energy"], "value": "100"}]})
    assert used.status_code == 200
    rules = client.get("/api/classification/rules").json()
    assert rules[0]["hits"] == 1 and rules[0]["supplier_name"] == "Elétrica" and rules[0]["shares"][0]["category_name"] == "Energia"
    # Regras: sem critério ou sem 100 % recusadas; revisão desatualizada recusada.
    body = {"name": "Sem critério", "shares": [{"category_id": ids["energy"], "value": "100"}]}
    assert client.post("/api/classification/rules", json=body).status_code == 422
    assert client.post("/api/classification/rules", json={**body, "keyword": "gasóleo", "shares": [{"category_id": ids["fuel"], "value": "90"}]}).status_code == 422
    assert client.put(f"/api/classification/rules/{rule_id}", json={**body, "supplier_nif": NIF, "revision": 5}).status_code == 409
    assert client.put(f"/api/classification/rules/{rule_id}", json={**body, "supplier_nif": NIF, "active": False, "revision": 0}).status_code == 200
    assert client.get(f"/api/classification/suggest?kind=purchase&id={other_id}").json()["suggestions"][0]["source"] != "regra"


def test_linked_purchase_writes_erp_document_and_batch_is_all_or_nothing(case):
    client, factory, ids = case
    with factory.begin() as db:
        erp = ERPDocument(store_id=ids["store"], enterprise_id="e", external_id="n1", supplier_nif=NIF, supplier_name="Elétrica", document="FT N/1",
                          internal_document="", document_type="FT", document_date="2026-10-10", amount_cents=12301)
        db.add(erp)
        db.flush()
        db.add(DocumentERPLink(purchase_id=ids["new"], erp_id=erp.id, reason="Confirmado"))
        erp_id = erp.id
    target = client.get(f"/api/classification/suggest?kind=purchase&id={ids['new']}").json()
    assert target["target"] == "erp"
    good = {"kind": "purchase", "id": ids["new"], "mode": "percent", "shares": [{"category_id": ids["maintenance"], "value": "100"}]}
    bad = {"kind": "erp", "id": erp_id, "mode": "amount", "shares": [{"category_id": ids["energy"], "value": "1.00"}]}
    assert client.post("/api/classification/batch", json={"items": [good, bad]}).status_code == 422
    with factory() as db:
        assert not db.scalar(select(func.count()).select_from(ERPDocumentRubric).where(ERPDocumentRubric.erp_document_id == erp_id))
    assert client.post("/api/classification/batch", json={"items": [good, {**good}]}).status_code == 422  # Repetido.
    assert client.post("/api/classification/batch", json={"items": [good]}).json()["applied"] == 1
    with factory() as db:
        rows = list(db.scalars(select(ERPDocumentRubric).where(ERPDocumentRubric.erp_document_id == erp_id)))
        assert [(r.category_id, r.amount_cents) for r in rows] == [(ids["maintenance"], 12301)]
        assert not db.scalar(select(func.count()).select_from(PurchaseRubric))


def test_erp_shares_rescale_linked_purchase_split_and_permissions(case):
    client, factory, ids = case
    with factory.begin() as db:
        admin = db.scalar(select(User).where(User.email == "admin@test.pt")).id
        erp = ERPDocument(store_id=ids["store"], enterprise_id="e", external_id="n2", supplier_nif=NIF, supplier_name="Elétrica", document="FT N/1",
                          internal_document="", document_type="FT", document_date="2026-10-10", amount_cents=20000)
        db.add(erp)
        db.flush()
        erp_id = erp.id
        db.add_all([PurchaseRubric(purchase_id=ids["new"], category_id=ids["energy"], amount_cents=9000, created_by=admin),
                    PurchaseRubric(purchase_id=ids["new"], category_id=ids["maintenance"], amount_cents=3301, created_by=admin),
                    DocumentERPLink(purchase_id=ids["new"], erp_id=erp_id, reason="Confirmado")])
    with factory() as db:
        # O documento ERP sem rubrica própria usa a repartição da compra ligada, proporcional ao seu total.
        split, source = erp_shares(db, [db.get(ERPDocument, erp_id)])[erp_id]
        assert source == "documento" and sum(a for _, a in split) == 20000 and dict(split)[ids["energy"]] == 14633
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get(f"/api/classification/suggest?kind=purchase&id={ids['new']}").status_code == 403
    assert client.get("/api/classification/rules").status_code == 403
