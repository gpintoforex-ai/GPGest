"""Tipos de documento de compra e efeito financeiro das receções (critérios de aceitação).

FCD 100 € → saída de caixa real; FC 100 € a 30 dias → dívida e previsto; NCC 20 € associada → dívida 80 €;
NCCD 20 € → entrada de caixa; pagar a FC → previsto em real e dívida a zero; anular/editar → estorno.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from uuid import uuid4

import pytest
from app.models import Movement, PlannedMovement, Purchase, PurchaseDocumentType, StockEntry
from conftest import open_cash
from sqlalchemy import select
from test_inventory import product, stock_case, view  # noqa: F401
from test_operating_modes import tenant
from test_platform import platform  # noqa: F401
from test_procurement import case  # noqa: F401
from test_tables import company_with_erp, erp, plan, records  # noqa: F401
from test_tables import execute as run_sync


@pytest.fixture
def money(case, monkeypatch):  # noqa: F811
    monkeypatch.setattr("app.purchase_financials.policy", lambda _: {"mode": "local", "provider": "none"})
    stock, supplier = case
    c = stock[0]
    types = {t["code"]: t for t in c.get("/api/purchase-document-types/options").json()["types"]}
    session = open_cash(c, stock[1]["registers"][0], amount="200").json()["id"]
    return stock, supplier, types, session


def receipt(money, code, amount, *, quantity="1", article=None, document=None, **extra):
    stock, supplier, types, _ = money
    aid = article or product(stock)
    body = {
        "kind": "receipt", "store_id": stock[4], "supplier_id": supplier, "document": document or code + "-" + uuid4().hex[:6],
        "document_date": date.today().isoformat(), "due_date": date.today().isoformat(), "request_key": str(uuid4()),
        "document_type_id": types[code]["id"],
        "lines": [{"article_id": aid, "description": "Artigo", "quantity": quantity, "price": amount, "vat": "0",
                   "warehouse_id": stock[3][0]}],
        **extra,
    }
    created = stock[0].post("/api/procurement", json=body)
    assert created.status_code == 200, created.text
    confirmed = stock[0].post(f"/api/procurement/{created.json()['id']}/confirm", json={"revision": created.json()["revision"]})
    return confirmed, aid


def remaining(c, purchase_id):
    return next(p for p in c.get("/api/purchases").json() if p["id"] == purchase_id)


def test_seeded_types_and_table_rules(money):
    stock, _, types, _ = money
    c = stock[0]
    assert set(types) >= {"FC", "FCD", "NCC", "NCCD"}
    assert types["FC"]["payment_condition"] == "credito" and types["FC"]["creates_debt"] and not types["FC"]["creates_cash_movement"]
    assert types["NCCD"]["sign"] == -1 and types["NCCD"]["creates_cash_movement"] and not types["NCCD"]["creates_debt"]
    rows = {r["code"]: r for r in records(c, "document_types")}
    # Filtro por natureza (e condição) na listagem.
    notes = {r["code"] for r in records(c, "document_types", f_nature="nota_credito_compra")}
    assert notes == {"NCC", "NCCD"}
    assert {r["code"] for r in records(c, "document_types", f_payment_condition="dinheiro")} == {"FCD", "NCCD"}
    assert rows["FCD"]["labels"]["effect"] == "movimento real" and rows["NCC"]["labels"]["sign_label"].startswith("−1")
    # Criar (indicadores derivados da condição), editar e desativar.
    created = c.post("/api/tables/document_types/records", json={"values": {
        "code": "fcx", "description": "Fatura de compra especial", "nature": "compra", "payment_condition": "credito"}})
    assert created.status_code == 200, created.text
    row = next(r for r in records(c, "document_types") if r["code"] == "FCX")
    assert row["values"]["creates_debt"] and row["values"]["default_due_days"] == 30
    duplicate = c.post("/api/tables/document_types/records", json={"values": {
        "code": "FCX", "description": "Outra", "nature": "compra", "payment_condition": "credito"}})
    assert duplicate.status_code == 409
    # Um tipo usado num documento não se elimina: só se desativa.
    confirmed, _ = receipt(money, "FC", "10")
    assert confirmed.status_code == 200, confirmed.text
    fc = rows["FC"]["id"]
    blocked = c.post(f"/api/tables/document_types/records/{fc}/remove", json={"scope": "app", "confirmed": True})
    assert blocked.status_code == 409
    assert c.post(f"/api/tables/document_types/records/{fc}/status", json={"active": False}).status_code == 200
    assert "FC" not in {t["code"] for t in c.get("/api/purchase-document-types/options").json()["types"]}
    again = c.post("/api/tables/document_types/records/" + row["id"] + "/remove", json={"scope": "app", "confirmed": True})
    assert again.status_code == 200, again.text


def test_cash_invoice_fcd_creates_real_cash_outflow(money):
    stock, _, _, session = money
    confirmed, aid = receipt(money, "FCD", "100")
    assert confirmed.status_code == 200, confirmed.text
    doc = confirmed.json()
    assert doc["financial"]["document_type_code"] == "FCD" and D(view(stock, aid)["quantity"]) == 1
    with stock[2]() as db:
        moved = db.get(Movement, doc["financial"]["movement_id"])
        assert (moved.amount_cents, moved.direction, moved.session_id, moved.origin, moved.reference) == (10000, -1, session, "rececao", doc["id"])
        assert db.scalar(select(PlannedMovement).where(PlannedMovement.purchase_id == doc["purchase_id"])) is None
    # Sem dívida: a compra fica paga.
    assert remaining(stock[0], doc["purchase_id"])["amount_cents"] == 10000
    assert doc["payment_status"] == "paid"


def test_credit_invoice_debt_credit_note_and_payment(money):
    stock, _, types, session = money
    c = stock[0]
    confirmed, aid = receipt(money, "FC", "100")
    assert confirmed.status_code == 200, confirmed.text
    invoice = confirmed.json()
    with stock[2]() as db:
        planned = db.scalar(select(PlannedMovement).where(PlannedMovement.purchase_id == invoice["purchase_id"]))
        assert (planned.amount_cents, planned.direction) == (10000, -1)
        assert planned.due_date == (date.today() + timedelta(days=30)).isoformat()
        assert db.get(Purchase, invoice["purchase_id"]).document_type_id == types["FC"]["id"]
    assert invoice["remaining_cents"] == 10000
    # NC de 20 € associada à FC: dívida a 80 € e o previsto acompanha; os artigos saem do stock.
    options = c.get(f"/api/purchase-document-types/options?supplier_id={invoice['supplier_id']}").json()
    assert [o["id"] for o in options["open_invoices"]] == [invoice["purchase_id"]]
    note, _ = receipt(money, "NCC", "20", article=aid, credit_note_purchase_ids=[invoice["purchase_id"]])
    assert note.status_code == 200, note.text
    assert D(view(stock, aid)["quantity"]) == 0
    planned_view = next(p for p in c.get("/api/planned-movements").json() if p["purchase_id"] == invoice["purchase_id"])
    assert planned_view["remaining_cents"] == 8000
    assert note.json()["financial"]["compensated"][0]["amount_cents"] == 2000
    # Pagar a FC: o previsto passa a real e a dívida fica a zero.
    paid = c.post(f"/api/planned-movements/{planned_view['id']}/settle", json={
        "session_id": session, "amount": "80", "payment": "numerario",
        "occurred_at": datetime.now(timezone.utc).isoformat(), "request_key": str(uuid4())})
    assert paid.status_code == 200, paid.text
    assert paid.json()["remaining_cents"] == 0 and paid.json()["status"] == "Liquidado"
    docs = {d["id"]: d for d in c.get("/api/procurement").json()["documents"]}
    assert docs[invoice["id"]]["remaining_cents"] == 0 and docs[invoice["id"]]["payment_status"] == "paid"


def test_cash_credit_note_nccd_brings_cash_in(money):
    stock, _, _, session = money
    _, aid = receipt(money, "FC", "50")
    note, _ = receipt(money, "NCCD", "20", article=aid)
    assert note.status_code == 200, note.text
    with stock[2]() as db:
        moved = db.get(Movement, note.json()["financial"]["movement_id"])
        assert (moved.amount_cents, moved.direction, moved.session_id) == (2000, 1, session)
        assert db.get(Purchase, note.json()["purchase_id"]).amount_cents == -2000


def test_cash_document_requires_open_cash_and_enough_cash(money):
    stock, _, _, session = money
    too_much, _ = receipt(money, "FCD", "500")
    assert too_much.status_code == 409 and "Numerário insuficiente" in too_much.json()["detail"]
    closed = stock[0].post("/api/sessions/" + session + "/close", json={"counts": {"20000": 1}, "reason": ""})
    assert closed.status_code == 200, closed.text
    no_cash, _ = receipt(money, "FCD", "10")
    assert no_cash.status_code == 409 and "abra a folha de caixa" in no_cash.json()["detail"]


def test_annul_reverses_stock_and_money_without_deleting(money):
    stock, _, _, _ = money
    c = stock[0]
    confirmed, aid = receipt(money, "FCD", "40")
    doc = confirmed.json()
    annulled = c.post(f"/api/procurement/{doc['id']}/annul", json={"revision": doc["revision"], "reason": "Documento errado"})
    assert annulled.status_code == 200, annulled.text
    assert annulled.json()["status"] == "cancelled" and D(view(stock, aid)["quantity"]) == 0
    with stock[2]() as db:
        original = db.get(Movement, doc["financial"]["movement_id"])
        reversal = db.get(Movement, original.reversed_by)
        assert reversal.reversal_of == original.id and reversal.direction == 1 and reversal.amount_cents == 4000
        assert len(list(db.scalars(select(StockEntry).where(StockEntry.article_id == aid)))) == 2
        assert db.get(Purchase, doc["purchase_id"]) is None


def test_editing_a_typed_receipt_regenerates_debt(money):
    stock, _, _, _ = money
    c = stock[0]
    confirmed, _ = receipt(money, "FC", "100")
    doc = confirmed.json()
    fields = __import__("app.procurement", fromlist=["Draft"]).Draft.model_fields
    line_fields = __import__("app.procurement", fromlist=["Line"]).Line.model_fields
    body = {k: v for k, v in doc.items() if k in fields}
    body["lines"] = [{**{k: v for k, v in doc["lines"][0].items() if k in line_fields}, "price": "120", "rejected": "0"}]
    edited = c.put(f"/api/procurement/{doc['id']}/confirmed", json=body)
    assert edited.status_code == 200, edited.text
    purchase_id = edited.json()["purchase_id"]
    with stock[2]() as db:
        planned = list(db.scalars(select(PlannedMovement)))
        assert [p.amount_cents for p in planned] == [12000] and planned[0].purchase_id == purchase_id
        assert db.get(Purchase, purchase_id).amount_cents == 12000


def test_import_from_wintouch_without_duplicates(platform, erp):  # noqa: F811
    client, _ = platform
    company, _ = company_with_erp(client, "tipos-documento", writes=False)
    erp.data["document_types"] = {}
    erp.add("document_types", ID="d34", Code="FTCOMPRA", Name="Fatura de Compra", Class=34)
    erp.add("document_types", ID="d32", Code="FTRCMP", Name="Fatura de compra a dinheiro", Class=32)
    erp.add("document_types", ID="d35", Code="NCFORN", Name="Nota de Crédito de Fornecedor", Class=35)
    erp.add("document_types", ID="d31", Code="ENCFORN", Name="Encomenda a Fornecedor", Class=31)
    erp.add("document_types", ID="dfc", Code="FC", Name="Fatura de Compra (Wintouch)", Class=34)
    planned = plan(client, "document_types", "import")
    items = {i["label"].split(" — ")[0]: i for i in planned["items"]}
    # Orçamentos/encomendas/guias ficam de fora; novos criam-se; mesma descrição ou código → conflito a decidir.
    assert "ENCFORN" not in items and items["NCFORN"]["action"] == "criar_local"
    assert items["FTRCMP"]["action"] == "conflito" and items["FC"]["action"] == "conflito"
    # «Usar o ERP» liga o tipo pré-carregado ao da Wintouch (sem criar outro).
    outcome = run_sync(client, "document_types", planned, resolutions={items["FTRCMP"]["key"]: "erp"})
    assert outcome.status_code == 200, outcome.text
    rows = {r["code"]: r for r in records(client, "document_types")}
    assert rows["NCFORN"]["values"]["nature"] == "nota_credito_compra" and rows["NCFORN"]["values"]["payment_condition"] == "credito"
    assert rows["NCFORN"]["origin"] == "erp" and rows["NCFORN"]["values"]["creates_debt"]
    assert rows["FTRCMP"]["values"]["payment_condition"] == "dinheiro" and rows["FTRCMP"]["erp_id"] == "d32"
    assert "FCD" not in rows
    # Repetir não duplica: os já importados ou ligados não voltam a ser propostos para criar.
    again = plan(client, "document_types", "import")
    created_again = {i["key"] for i in again["items"] if i["action"] == "criar_local"}
    assert not created_again & {"erp:d35", "erp:d32"}
    with tenant(company) as db:
        assert len(list(db.scalars(select(PurchaseDocumentType)))) == 5


def test_import_disabled_in_local_mode(money, monkeypatch):
    stock, _, _, _ = money
    monkeypatch.setattr("app.operating_modes.policy", lambda db: {"mode": "local", "provider": "none"})
    info = stock[0].get("/api/tables/document_types").json()
    assert info["erp"]["available"] is False and "modo local" in info["erp"]["reason"]
