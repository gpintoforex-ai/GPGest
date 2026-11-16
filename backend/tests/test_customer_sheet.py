"""Ficha de cliente — separadores Vendas e Documentos (consulta comercial): artigos comprados com quantidades
faturadas e devolvidas, documentos com tipo, série, estado e origem, anulados identificados e fora dos totais, cliente
sem vendas, nunca ligação pelo nome, histórico de um artigo, modo local (SAF-T) e integrado (ERP). Dados fictícios."""

import json
from decimal import Decimal

import pytest
from app.models import BusinessEntity, Store, WintouchEntity, WintouchSaleRecord
from saft_fixtures import customer, invoice, saft
from sqlalchemy import select
from test_saft_sales import confirm as confirm_import
from test_saft_sales import (
    saft_case,  # noqa: F401
    upload,
)

PERIOD = {"start": "2026-01-01", "end": "2026-12-31"}


def nif(prefix):
    digits = [int(c) for c in prefix]
    rest = sum(d * (9 - i) for i, d in enumerate(digits)) % 11
    return prefix + str(0 if rest < 2 else 11 - rest)


ALFA, DELTA = nif("50000021"), nif("50000024")
P2 = ("<Product><ProductType>P</ProductType><ProductCode>P2</ProductCode><ProductGroup>Família B</ProductGroup>"
      "<ProductDescription>Artigo fictício dois</ProductDescription><ProductNumberCode>P2</ProductNumberCode></Product>")


def two_lines(number, quantity_one, quantity_two, price_one="10.00", price_two="5.00"):
    """Fatura com duas linhas (P1 e P2), IVA a 23 %."""
    one, two = Decimal(quantity_one) * Decimal(price_one), Decimal(quantity_two) * Decimal(price_two)
    net = one + two
    tax = (net * Decimal("0.23")).quantize(Decimal(".01"))
    line = lambda n, code, desc, qty, price, value: (  # noqa: E731
        f"<Line><LineNumber>{n}</LineNumber><ProductCode>{code}</ProductCode><ProductDescription>{desc}</ProductDescription><Quantity>{qty}</Quantity>"
        f"<UnitOfMeasure>UN</UnitOfMeasure><UnitPrice>{price}</UnitPrice><TaxPointDate>2026-01-10</TaxPointDate><Description>Teste</Description>"
        f"<CreditAmount>{value:.2f}</CreditAmount><Tax><TaxType>IVA</TaxType><TaxCountryRegion>PT</TaxCountryRegion><TaxCode>NOR</TaxCode>"
        f"<TaxPercentage>23</TaxPercentage></Tax></Line>")
    base = invoice("FT", number, f"{net + tax:.2f}")
    start, end = base.index("<Line>"), base.index("</Line>") + len("</Line>")
    lines = line(1, "P1", "Serviço fictício", quantity_one, price_one, one) + line(2, "P2", "Artigo fictício dois", quantity_two, price_two, two)
    body = base[:start] + lines + base[end:]
    return body.replace(f"<TaxPayable>{(Decimal(f'{net + tax:.2f}') - (Decimal(f'{net + tax:.2f}') / Decimal('1.23')).quantize(Decimal('.01')))}</TaxPayable>",
                        f"<TaxPayable>{tax}</TaxPayable>").replace(
        f"<NetTotal>{(Decimal(f'{net + tax:.2f}') / Decimal('1.23')).quantize(Decimal('.01'))}</NetTotal>", f"<NetTotal>{net:.2f}</NetTotal>")


def record_id(factory, tax_number=None, name=None):
    with factory() as db:
        query = select(BusinessEntity).where(BusinessEntity.tax_number == tax_number) if tax_number is not None else select(BusinessEntity).where(BusinessEntity.name == name)
        return "local:" + db.scalar(query.where(BusinessEntity.removed.is_(False))).id


def sheet(client, record, **params):
    response = client.get("/api/sales/customer-sheet", params={"record_id": record, **PERIOD, **params})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def local(saft_case, monkeypatch):  # noqa: F811
    client, ids, factory, store = saft_case
    monkeypatch.setattr("app.billing_conference.policy", lambda db: {"mode": "local"})
    invoices = [two_lines(1, "2", "1"), two_lines(2, "1", "1", "12.00"),  # P1 em dois documentos, a preços diferentes
                invoice("NC", 3, "12.30"),  # devolução de 1 × P1
                invoice("FT", 4, "12.30", status="A"),  # anulado: aparece, fora dos totais
                invoice("FS", 5, "12.30", customer_id="C2")]  # outro cliente
    customers = customer("C1", ALFA, "Cliente Alfa") + customer("C2", "999999990", "Consumidor final") + P2
    row = upload((client, ids, factory, store), saft(invoices, customers=customers))
    assert row["status"] == "ready", row
    assert confirm_import((client, ids, factory, store), row)["status"] == "completed"
    with factory.begin() as db:
        db.add(BusinessEntity(name="Cliente Delta", tax_number=DELTA, is_customer=True))  # sem vendas
        db.add(BusinessEntity(name="Cliente Alfa", tax_number="", is_customer=True))  # mesmo nome, sem NIF nem ligação
    return client, factory


def test_sales_by_article_documents_and_totals(local):
    client, factory = local
    data = sheet(client, record_id(factory, ALFA))
    assert data["available"] and data["mode"] == "saft" and data["identity"]["codes"] == ["C1"]
    articles = {a["code"]: a for a in data["articles"]}
    p1, p2 = articles["P1"], articles["P2"]
    # P1: 2 + 1 faturados, 1 devolvido na nota de crédito; preço médio sobre a quantidade líquida.
    assert (p1["sold"], p1["returned"], p1["quantity"], p1["documents"]) == ("3", "1", "2", 3)
    assert p1["net_cents"] == 2000 + 1200 - 1000 and p1["first_date"] == "2026-01-10" and p1["average_price_cents"] == 1100
    assert (p2["sold"], p2["family"], p2["net_cents"], p2["gross_cents"]) == ("2", "Família B", 1000, 1230)
    kpis = data["kpis"]
    assert (kpis["articles"], kpis["units"], kpis["top_article"]["code"], kpis["last_date"]) == (2, "4", "P1", "2026-01-10")
    assert kpis["net_cents"] == 2500 + 1700 - 1000 and kpis["cancelled"] == 1  # o anulado não entra nos totais
    documents = {d["document"]: d for d in data["documents"]}
    assert set(documents) == {"FT TESTE/1", "FT TESTE/2", "NC TESTE/3", "FT TESTE/4"}  # o FS do outro cliente não aparece
    assert (documents["FT TESTE/4"]["state"], documents["NC TESTE/3"]["state"], documents["FT TESTE/1"]["series"]) == ("anulado", "nota_credito", "FT TESTE")
    assert documents["FT TESTE/1"]["origin_label"] == "SAF-T" and documents["FT TESTE/4"]["payment_known"] is False
    # O documento abre na consulta documental com todas as linhas.
    opened = client.get("/api/sales/detail", params={"view": "document", "id": documents["FT TESTE/1"]["id"], "start": "2026-01-10", "end": "2026-01-10"}).json()
    assert [line["code"] for line in opened["lines"]] == ["P1", "P2"] and opened["lines_match"]


def test_article_history_filters_and_no_name_matching(local):
    client, factory = local
    record = record_id(factory, ALFA)
    history = client.get("/api/sales/customer-article", params={"record_id": record, "key": "P1", **PERIOD}).json()
    assert [(line["document"], line["quantity"]) for line in history["lines"]] == [("NC TESTE/3", "-1"), ("FT TESTE/2", "1"), ("FT TESTE/1", "2")]
    assert history["price_range"] == ["10.00", "12.00"] and history["article_id"] is None and history["family"] == ""
    assert sheet(client, record, start="2026-02-01", end="2026-02-28")["documents"] == []  # filtro de período
    empty = sheet(client, record_id(factory, DELTA))
    assert empty["available"] and empty["articles"] == [] and empty["documents"] == [] and empty["kpis"]["last_date"] is None
    homonym = sheet(client, record_id(factory, "", None))
    assert homonym["available"] is False and "nome" in homonym["reason"]


@pytest.fixture
def erp(setup):
    client, _, factory = setup
    with factory.begin() as db:
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        wt = WintouchEntity(enterprise_id="wt", external_id="E1", code="CL001", name="Cliente ERP", tax_number="")
        db.add(wt)
        db.flush()
        db.add(BusinessEntity(name="Cliente ERP", tax_number="", is_customer=True, wintouch_id=wt.id))
        for key, entity_id, day in (("A", "E1", "2026-03-02"), ("B", "E2", "2026-03-03")):
            db.add(WintouchSaleRecord(enterprise_id="wt", external_id="document:" + key, store_id=store, kind="documento", document_date=day,
                                      data=json.dumps({"document_group": "vendas", "document": f"FT 1/{key}", "type": "FT", "entity": "Cliente ERP",
                                                       "entity_nif": "", "entity_id": entity_id, "direction": 1, "amount_cents": 1230, "net_cents": 1000,
                                                       "tax_cents": 230, "source_document_id": key,
                                                       "lines": [{"product_id": "wp1", "quantity": "2", "net": "10.00"}]})))
        wt_id = wt.id
    return client, wt_id


def test_integrated_mode_links_by_erp_entity_id(erp):
    client, wt_id = erp
    data = sheet(client, "wt:" + wt_id)
    assert data["mode"] == "erp" and data["identity"]["erp_id"]
    assert [d["document"] for d in data["documents"]] == ["FT 1/A"]  # o FT 1/B é de outra entidade com o mesmo nome
    assert data["documents"][0]["origin_label"] == "ERP" and data["articles"][0]["quantity"] == "2"
