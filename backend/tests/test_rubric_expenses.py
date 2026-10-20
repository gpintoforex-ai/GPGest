"""Despesas por rubrica: compras do ERP (rubrica do fornecedor ou do documento) e compras locais."""

from datetime import date
from uuid import uuid4

from app.models import Category, DocumentERPLink, ERPDocument
from app.rubric_expenses import erp_rubric_labels
from test_inventory import stock_case  # noqa: F401
from test_procurement import case, confirm, draft  # noqa: F401

TODAY = date.today().isoformat()


def erp(stock, nif, amount, document, kind="FT"):
    with stock[2].begin() as db:
        row = ERPDocument(store_id=stock[4], enterprise_id="demo", external_id="ext-" + uuid4().hex[:8], supplier_nif=nif, supplier_name="Fornecedor " + nif,
                          document=document, internal_document="COMPRA/" + document, document_type=kind, document_date=TODAY, amount_cents=amount)
        db.add(row); db.flush()
        return row.id, row.external_id


def report(stock):
    response = stock[0].get(f"/api/rubric-expenses?start={TODAY}&end={TODAY}")
    assert response.status_code == 200, response.text
    return response.json()


def test_erp_purchases_use_supplier_rubric_can_be_split_and_are_not_double_counted(case):  # noqa: F811
    stock, _ = case
    supplier_category = stock[1]["category"]
    with stock[2].begin() as db:
        services = Category(name="Serviços " + uuid4().hex[:6]); db.add(services); db.flush(); services_id = services.id
    first, external = erp(stock, "500000000", 12300, "FT A/1")
    erp(stock, "500000000", -2460, "NC A/1", "NC")
    erp(stock, "999999990", 5000, "FT X/9")
    data = report(stock)
    by_rubric = {item["category_id"]: item["total_cents"] for item in data["rubrics"]}
    assert by_rubric[supplier_category] == 12300 - 2460 and by_rubric[None] == 5000 and data["unclassified"] == 1
    assert data["rubrics"][-1]["category_name"] == "Por classificar"

    wrong = stock[0].post(f"/api/rubric-expenses/erp/{first}", json={"allocations": [{"category_id": services_id, "amount": "10,00"}]})
    assert wrong.status_code == 422 and "somar o total" in wrong.json()["detail"]
    split = stock[0].post(f"/api/rubric-expenses/erp/{first}", json={"allocations": [
        {"category_id": supplier_category, "amount": "100,00"}, {"category_id": services_id, "amount": "23,00"}]})
    assert split.status_code == 200 and split.json()["rubric_source"] == "documento"
    by_rubric = {item["category_id"]: item["total_cents"] for item in report(stock)["rubrics"]}
    assert by_rubric[supplier_category] == 10000 - 2460 and by_rubric[services_id] == 2300
    with stock[2]() as db:
        assert erp_rubric_labels(db, "demo", [(external, "500000000"), (None, "500000000"), (None, "123")])[1:] == [db.get(Category, supplier_category).name, None]
    reset = stock[0].post(f"/api/rubric-expenses/erp/{first}", json={"allocations": []})
    assert reset.json()["rubric_source"] == "fornecedor"

    # Uma compra local ligada ao documento ERP conta só uma vez (pelo ERP).
    lines = [{"description": "Serviço", "nature": "service", "unit": "h", "quantity": "1", "price": "100", "vat": "23"}]
    local = confirm(case, draft(case, "invoice", lines=lines))
    assert report(stock)["total_cents"] == 12300 - 2460 + 5000 + 12300
    with stock[2].begin() as db:
        db.add(DocumentERPLink(purchase_id=local["purchase_id"], erp_id=first, reason="Teste"))
    assert report(stock)["total_cents"] == 12300 - 2460 + 5000
