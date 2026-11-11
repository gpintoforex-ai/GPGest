"""Análise comercial (Faturação e recebimentos): clientes → documentos → artigos → recebimentos, só leitura.

Modo local (SAF-T) e modo integrado (vendas Wintouch), pagamentos parciais, recibos, pagamento no documento, notas
de crédito, anulados, vencidos pelo prazo do cliente, conferência sem somar fontes e recibos de várias faturas sem
duplicação. Dados fictícios."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app.models import (
    Audit,
    BusinessEntity,
    StockArticle,
    Store,
    WintouchProduct,
    WintouchSaleRecord,
)
from conftest import open_cash
from saft_fixtures import customer, invoice, payment, receipt, saft
from sqlalchemy import func, select
from test_saft_sales import confirm as confirm_import
from test_saft_sales import (
    saft_case,  # noqa: F401
    upload,
)

PERIOD = {"start": "2026-01-01", "end": "2026-01-31"}


@pytest.fixture
def local(saft_case, monkeypatch):  # noqa: F811
    client, ids, factory, store = saft_case
    monkeypatch.setattr("app.billing_conference.policy", lambda db: {"mode": "local"})
    assert client.post("/api/saft/payment-mapping", json={"mapping": {"NU": "numerario", "CC": "cartao"}}).status_code == 200
    invoices = [
        invoice("FT", 1, "12.30"),  # recibo parcial de 5,00 €; cliente com prazo de 30 dias → vencido
        invoice("FT", 2, "24.60"),  # recibo total
        invoice("FS", 3, "12.30", payments=payment("12.30", "NU", "2026-01-10")),  # pago no ato
        invoice("NC", 4, "12.30"),
        invoice("FT", 5, "12.30", status="A"),
        invoice("FT", 6, "12.30", customer_id="C2"),  # consumidor final, sem prazo → pendente
    ]
    receipts = [receipt(1, "FT TESTE/1", "5.00"), receipt(2, "FT TESTE/2", "24.60")]
    customers = customer() + customer("C2", "999999990", "Consumidor final")
    row = upload((client, ids, factory, store), saft(invoices, receipts, customers))
    assert row["status"] == "ready", row
    assert confirm_import((client, ids, factory, store), row)["status"] == "completed"
    with factory.begin() as db:
        entity = db.scalar(select(BusinessEntity).where(BusinessEntity.tax_number == "500000002"))
        entity.payment_days = 30
    return client, ids, factory, store


def get(client, path, **params):
    response = client.get("/api/sales/" + path, params={**PERIOD, **params})
    assert response.status_code == 200, response.text
    return response.json()


def by_number(client, number):
    return next(d for d in get(client, "documents", limit=200)["rows"] if d["document"] == number)


def test_overview_totals_states_and_no_double_counting(local):
    client, _, factory, _ = local
    with factory() as db:
        audits = db.scalar(select(func.count()).select_from(Audit))
    data = get(client, "overview")
    kpis = data["kpis"]
    assert data["mode"] == "saft"
    # Líquido: 10 + 20 + 10 − 10 (NC) + 10; o anulado fica de fora.
    assert (kpis["net_cents"], kpis["gross_cents"]) == (4000, 4920)
    assert (kpis["received_cents"], kpis["pending_cents"], kpis["overdue_cents"]) == (500 + 2460 + 1230, 730 + 1230, 730)
    assert (kpis["documents"], kpis["cancelled"], kpis["customers"], kpis["credit_notes_cents"]) == (5, 1, 2, 1230)
    assert kpis["average_ticket_cents"] == round(5000 / 4)
    states = {d["document"]: d["state"] for d in get(client, "documents", limit=200)["rows"]}
    assert states == {"FT TESTE/1": "vencido", "FT TESTE/2": "pago", "FS TESTE/3": "pago", "NC TESTE/4": "nota_credito",
                      "FT TESTE/5": "anulado", "FT TESTE/6": "pendente"}
    assert data["months"][0]["received_cents"] == 500 + 2460 + 1230
    assert data["top_customers"][0]["name"] == "Cliente fictício" and data["top_articles"][0]["code"] == "P1"
    with factory() as db:  # Consultar não escreve nada.
        assert db.scalar(select(func.count()).select_from(Audit)) == audits


def test_document_lines_receipts_and_original(local):
    client, *_ = local
    doc = by_number(client, "FT TESTE/1")
    assert (doc["received_cents"], doc["pending_cents"], doc["due_date"]) == (500, 730, "2026-02-09")
    detail = get(client, "detail", view="document", id=doc["id"])
    assert detail["customer"]["nif"] == "500000002" and detail["payment_terms"] == "30 dia(s)"
    assert [(line["code"], line["net_cents"], line["vat_cents"], line["vat_rate"]) for line in detail["lines"]] == [("P1", 1000, 230, "23")]
    assert detail["lines_match"] and detail["lines_totals"] == {"net_cents": 1000, "vat_cents": 230}
    assert [(r["source"], r["document"], r["allocated_cents"]) for r in detail["receipts"]] == [("saft_receipt", "RG TESTE/1", 500)]
    assert detail["originals"] == [] and "não são uma cópia do original" in detail["original_note"]
    credit = get(client, "detail", view="document", id=by_number(client, "NC TESTE/4")["id"])
    assert credit["lines"][0]["net_cents"] == -1000 and credit["state"] == "nota_credito" and credit["pending_cents"] == 0


def test_filters_search_by_document_value_article_and_state(local):
    client, *_ = local
    assert [d["document"] for d in get(client, "documents", state="em_divida", sort="document", direction="asc")["rows"]] == ["FT TESTE/1", "FT TESTE/6"]
    assert [d["document"] for d in get(client, "documents", q="24,60")["rows"]] == ["FT TESTE/2"]
    assert [d["document"] for d in get(client, "documents", q="ft teste/6")["rows"]] == ["FT TESTE/6"]
    assert get(client, "documents", q="serviço fictício")["total"] == 6  # pesquisa pelas linhas (artigo)
    assert get(client, "documents", article="P1", state="pago")["total"] == 2
    customers = get(client, "customers", q="500000002")["rows"]
    assert [c["code"] for c in customers] == ["C1"]
    assert get(client, "documents", customer=customers[0]["key"])["total"] == 5


def test_customer_and_article_views(local):
    client, *_ = local
    key = get(client, "customers", q="C1")["rows"][0]["key"]
    sheet = get(client, "detail", view="customer", key=key)
    assert (sheet["code"], sheet["nif"], sheet["totals"]["pending_cents"], sheet["last_date"]) == ("C1", "500000002", 730, "2026-01-10")
    assert sheet["articles"][0]["code"] == "P1" and sheet["articles"][0]["documents"] == 4
    assert any(month["key"] == "2026-01" for month in sheet["history"])
    article = get(client, "detail", view="article", code="P1")
    assert article["documents"] == 5 and article["customers"] == 2 and article["cost"] is None and article["margin_percent"] is None
    assert article["cost_note"] and {line["document"] for line in article["lines"]} >= {"FT TESTE/1", "FT TESTE/6"}
    bought = get(client, "articles", customer=key)["rows"]
    assert bought[0]["code"] == "P1" and bought[0]["average_price_cents"] == 1500  # 2 unidades por 30,00 € (NC subtrai)


def test_conference_and_saft_receipts_are_not_summed(local):
    client, ids, *_ = local
    session = open_cash(client, ids["registers"][0], "0")
    session_id = session.json()["id"] if session.status_code == 200 else client.get("/api/sessions").json()[0]["id"]
    move = client.post("/api/movements", json={"session_id": session_id, "category_id": ids["category"], "kind": "cliente", "payment": "numerario",
                                               "amount": "5", "occurred_at": datetime.now(timezone.utc).isoformat(), "document": "FT TESTE/1",
                                               "request_key": str(uuid4())})
    assert move.status_code == 200, move.text
    doc = by_number(client, "FT TESTE/1")
    body = {"kind": "individual", "confirmed": True, "notes": "Recibo RG TESTE/1 entregue ao balcão",
            "documents": [{"record_id": doc["id"], "amount_cents": 500}], "receipts": [{"source": "movement", "id": move.json()["id"], "amount_cents": 500}]}
    assert client.post("/api/conference/confirm", json=body).status_code == 200
    # O recibo do SAF-T e o recebimento da caixa descrevem o mesmo dinheiro: continua 5,00 €, não 10,00 €.
    assert by_number(client, "FT TESTE/1")["received_cents"] == 500
    detail = get(client, "detail", view="document", id=doc["id"])
    assert detail["conference"][0]["reconciliation"] == "caixa" and detail["conference"][0]["allocated_cents"] == 500
    rows = get(client, "receipts")["rows"]
    assert len({r["id"] for r in rows}) == len(rows)
    caixa = next(r for r in rows if r["source"] == "conferencia")
    opened = get(client, "detail", view="receipt", id=caixa["id"])
    assert [d["document"] for d in opened["documents"]] == ["FT TESTE/1"]


@pytest.fixture
def erp(setup):
    client, _, factory = setup
    with factory.begin() as db:
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        product = WintouchProduct(enterprise_id="wt", external_id="wp1", code="ART1", name="Artigo ERP fictício", unit="UN")
        db.add(product)
        db.flush()
        db.add(StockArticle(code="ART1", name="Artigo ERP fictício", erp_id=product.id, data=json.dumps({"family": "Família A", "brand": "Marca B"})))

        def record(kind, external, day, **data):
            db.add(WintouchSaleRecord(enterprise_id="wt", external_id=external, store_id=store, kind=kind, document_date=day,
                                      data=json.dumps({"document_group": "vendas", "currency": "EUR", "direction": 1, **data})))

        common = {"entity": "Cliente ERP", "entity_nif": "500000002", "type": "FT"}
        record("documento", "document:A", "2026-01-05", document="FT 1/1", source_document_id="A", amount_cents=12300, net_cents=10000, tax_cents=2300,
               lines=[{"product_id": "wp1", "quantity": "2", "net": "100.00"}], **common)
        record("documento", "document:B", "2026-01-06", document="FT 1/2", source_document_id="B", amount_cents=6150, net_cents=5000, tax_cents=1150, **common)
        record("documento", "document:C", "2026-01-07", document="FT 1/3", source_document_id="C", amount_cents=6150, net_cents=5000, tax_cents=1150, **common)
        record("documento", "document:D", "2026-01-08", document="FT 1/4", source_document_id="D", amount_cents=6150, net_cents=5000, tax_cents=1150,
               cancelled=True, **common)
        record("real", "payment:A1", "2026-01-05", document="FT 1/1", source_document_id="A", amount_cents=7300, payment="Numerário",
               occurred_at="2026-01-05T10:00:00", **common)
        record("real", "payment:R1", "2026-01-20", document="RE 1/1", source_document_id="R1", source_invoice_ids=["B", "C"], amount_cents=12300,
               payment="Transferência", occurred_at="2026-01-20T10:00:00", **common)
        record("previsto", "pending:A", "2026-01-05", document="FT 1/1", source_document_id="A", amount_cents=5000, due_date="2026-01-25", **common)
    return client, factory


def test_integrated_mode_uses_erp_open_balance_and_counts_receipts_once(erp):
    client, _ = erp
    data = get(client, "overview")
    assert data["mode"] == "erp"
    kpis = data["kpis"]
    assert (kpis["gross_cents"], kpis["received_cents"], kpis["pending_cents"], kpis["overdue_cents"], kpis["cancelled"]) == (24600, 19600, 5000, 5000, 1)
    # O recibo de duas faturas conta uma vez nos recebimentos do mês.
    assert data["months"][0]["received_cents"] == 7300 + 12300
    states = {d["document"]: d["state"] for d in get(client, "documents")["rows"]}
    assert states == {"FT 1/1": "vencido", "FT 1/2": "pago", "FT 1/3": "pago", "FT 1/4": "anulado"}
    receipts = get(client, "receipts")["rows"]
    recibo = next(r for r in receipts if r["document"] == "RE 1/1")
    assert len(receipts) == 2 and sorted(d["document"] for d in recibo["documents"]) == ["FT 1/2", "FT 1/3"]
    doc = next(d for d in get(client, "documents")["rows"] if d["document"] == "FT 1/1")
    detail = get(client, "detail", view="document", id=doc["id"])
    line = detail["lines"][0]
    assert (line["code"], line["quantity"], line["unit_price"], line["net_cents"]) == ("ART1", "2", "50.0000", 10000) and line["article_id"]
    article = get(client, "detail", view="article", code="ART1")
    assert (article["family"], article["brand"], article["article_id"] is not None) == ("Família A", "Marca B", True)
    with pytest.raises(AssertionError):
        get(client, "detail", view="document", id="inexistente")
