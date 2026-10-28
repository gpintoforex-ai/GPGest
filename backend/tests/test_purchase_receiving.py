"""Receção de mercadoria a partir da fatura: leitura das linhas, correspondência e entrada em stock.

Os textos imitam formatos reais de fornecedores (dados fictícios): colunas diferentes, OCR sem
alinhamento, ofertas a 100 %, bónus, desconto global e lote/validade na linha ou na seguinte.
"""

from decimal import Decimal as D
from uuid import uuid4

import pytest
from app.invoice_lines import extract
from app.models import ArchiveDocument, BusinessEntity, PurchaseArticleAlias, StockEntry
from sqlalchemy import select
from test_inventory import product, stock_case, view  # noqa: F401

COLUMNS = """
  REF        DESCRIÇÃO                           QTD   UNI  P.VENDA   DESC     VALOR LIQUIDO  IVA   PVP
  100200300  Gel de Aloé 200ml                   12,00 UNI  11,00   100,00%   0,00         23 %  16,95
  Lote:L605A - Data de Validade: 28-dez-2031
  100200301  Colagénio em pó 300g                3,00  UNI  22,77             68,31        23 %  35,00
  Lote:A42 - Data de Validade: 28-mai-2029
  Incidência  Taxa  Valor IVA  TOTAL BRUTO  68,31
"""
BONUS = """
 Cod Inter Cód. de Barras Lote Descrição Quant Bónus Preço Iva Parc Desc.
 497213 5600445611349 61477792(*)ÓLEO DE LINHAÇA BIO 25 6 2.490 2 5.00 14.193
 19804 5000488104288 61446720(*)PASTILHAS LARANJA 50G 5 1 6.500 3 5.00 30.875
 0 % Taxa 1 6% Taxa 2 13% Taxa 3 23% Total
"""
GLOBAL = """
Artigo Lote Validade Descrição Qtd.Un. PrUnitário Desc. IVA Valor
5600493200380 20113 16/07/2029 Cápsulas de cúrcuma 60 3,00 UN 29,08 0,00 23,00 69,79
5600493200595 A3384 31/03/2029 Ómega 3 60caps 1,00 UN 13,93 0,00 23,00 11,14
BPI: NIB 0010 0000 37345400001 90 / IBAN: PT50 0010 0000 3734 5400 0019 01
Mercadoria/Serviços 81,13
"""


def test_reads_columns_offers_and_next_line_lots():
    result = extract(COLUMNS, base_cents=6831)
    first, second = result["lines"]
    assert first["description"] == "Gel de Aloé 200ml" and first["reference"] == "100200300"
    # Oferta: 12 unidades a custo zero, com lote e validade da linha seguinte.
    assert (first["quantity"], first["discount"], first["total"], first["unit_cost"]) == ("12", "100", "0.00", "0.000000")
    assert (first["lot"], first["expiry"], first["vat"]) == ("L605A", "2031-12-28", "23")
    assert (second["quantity"], second["price"], second["total"], second["lot"]) == ("3", "22.77", "68.31", "A42")
    assert result["check"]["matches"] is True


def test_separates_quantity_from_description_numbers_and_counts_bonus():
    result = extract(BONUS, base_cents=4507)
    oil, pastilles = result["lines"]
    # «BIO 25» é descrição: a conta 6 × 2,49 × 0,95 = 14,19 identifica a quantidade.
    assert oil["description"] == "ÓLEO DE LINHAÇA BIO 25" and oil["quantity"] == "6"
    assert (oil["barcode"], oil["reference"], oil["lot"], oil["vat"]) == ("5600445611349", "497213", "61477792", "13")
    # 5 + 1 bónus = 6 unidades em stock pelo valor da linha.
    assert (pastilles["quantity"], pastilles["bonus"], pastilles["unit_cost"]) == ("5", "1", "5.145833")
    assert result["check"]["matches"] is True


def test_detects_global_discount_and_ignores_bank_details():
    result = extract(GLOBAL, base_cents=8093)
    assert [line["description"] for line in result["lines"]] == ["Cápsulas de cúrcuma 60", "Ómega 3 60caps"]
    turmeric = result["lines"][0]
    assert turmeric["confidence"] == "desconto_global" and turmeric["global_discount"] == "20.00"
    assert (turmeric["barcode"], turmeric["lot"], turmeric["expiry"]) == ("5600493200380", "20113", "2029-07-16")
    assert turmeric["unit_cost"] == "23.263333"  # 69,79 / 3: o custo já inclui o desconto global.


def test_unreadable_text_proposes_nothing():
    result = extract("Telefone 923 30 90 30 NIB 0010 0000 3734 Total 100,00 EUR")
    assert result["lines"] == [] and result["warnings"]


@pytest.fixture
def receiving(stock_case, monkeypatch):  # noqa: F811
    c, ids, factory, warehouses, store = stock_case
    with factory.begin() as db:
        supplier = BusinessEntity(name="Fornecedor Natural", tax_number="507475500", is_supplier=True, category_id=ids["category"])
        doc = ArchiveDocument(
            source_key="teste:" + uuid4().hex, origin="digitalizacao", doc_type="fatura", mime="application/pdf",
            storage="base_dados", storage_ref="archive_blobs:x", entity_nif="507475500", entity_name="Fornecedor Natural",
            document_code="FT", document_number="FA.2026/3208", issue_date="2026-10-20", base_cents=8093,
            total_cents=9954, original_name="fatura.pdf", store_id=store,
        )
        db.add_all([supplier, doc])
        db.flush()
        sid, doc_id = supplier.id, doc.id
    monkeypatch.setattr("app.archive.read_bytes", lambda db, doc: (b"%PDF-1.4", "application/pdf"))
    monkeypatch.setattr("app.purchase_receiving.pdf_layout_text", lambda raw: GLOBAL)
    return c, sid, doc_id, warehouses[0], stock_case


def test_receive_from_invoice_matches_creates_learns_and_enters_stock(receiving):
    c, sid, doc_id, warehouse, case = receiving
    existing = product(case, code="CURC60", name="Cápsulas de Cúrcuma 60", barcodes=["5600493200380"], lots=True, expiry=True)
    listed = c.get("/api/purchase-receiving/documents").json()["rows"]
    assert listed[0]["id"] == doc_id and listed[0]["supplier_id"] == sid and listed[0]["receipt_id"] is None

    read = c.post(f"/api/purchase-receiving/{doc_id}/read")
    assert read.status_code == 200, read.text
    proposal = read.json()
    assert proposal["document"]["supplier_id"] == sid and proposal["check"]["matches"] is True
    turmeric, omega = proposal["lines"]
    assert turmeric["selected"]["article_id"] == existing and turmeric["selected"]["method"] == "codigo_barras"
    assert omega["selected"] is None and omega["proposal"]["barcodes"] == ["5600493200595"] and omega["proposal"]["lots"]

    body = {
        "supplier_id": sid, "warehouse_id": warehouse, "document": "FA.2026/3208", "document_date": "2026-10-20",
        "request_key": str(uuid4()), "global_discount": "20",
        "lines": [
            {"article_id": existing, "description": turmeric["description"], "reference": turmeric["reference"],
             "barcode": turmeric["barcode"], "quantity": "3", "price": turmeric["price"], "discount": "0", "vat": "23",
             "lot": turmeric["lot"], "expiry": turmeric["expiry"]},
            {"new_article": {**omega["proposal"], "name": "Ómega 3 MaxPower 60 cápsulas"}, "description": omega["description"],
             "reference": omega["reference"], "barcode": omega["barcode"], "quantity": "1", "price": omega["price"],
             "vat": "23", "lot": omega["lot"], "expiry": omega["expiry"]},
        ],
    }
    done = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json=body)
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["lines"] == 2 and result["created_articles"] == 1
    assert D(view(case, existing)["quantity"]) == 3
    with case[2]() as db:
        entries = list(db.scalars(select(StockEntry).where(StockEntry.article_id == existing)))
        # 29,08 com 20 % de desconto global: o custo em stock é o valor líquido por unidade.
        assert len(entries) == 1 and abs(D(entries[0].unit_cost) - D("23.264")) < D("0.0001")
        assert db.scalar(select(PurchaseArticleAlias).where(PurchaseArticleAlias.kind == "ean", PurchaseArticleAlias.key == "5600493200595"))
    # A fatura fica marcada como recebida e não pode dar entrada duas vezes.
    assert c.get("/api/purchase-receiving/documents").json()["rows"][0]["receipt_id"] == result["receipt_id"]
    again = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json={**body, "request_key": str(uuid4())})
    assert again.status_code == 409
    # Repetir o mesmo pedido devolve a mesma receção (sem duplicar stock).
    assert c.post(f"/api/purchase-receiving/{doc_id}/confirm", json=body).json()["receipt_id"] == result["receipt_id"]
    # A segunda leitura já reconhece o artigo criado (correspondência aprendida).
    second = c.post(f"/api/purchase-receiving/{doc_id}/read").json()["lines"][1]
    assert second["selected"]["method"] == "aprendido" and second["selected"]["name"] == "Ómega 3 MaxPower 60 cápsulas"


def test_lot_controlled_article_requires_lot(receiving):
    c, sid, doc_id, warehouse, case = receiving
    article = product(case, code="LOTE1", name="Artigo com lotes", lots=True)
    body = {
        "supplier_id": sid, "warehouse_id": warehouse, "document": "FA.2026/3208", "document_date": "2026-10-20",
        "request_key": str(uuid4()),
        "lines": [{"article_id": article, "description": "Artigo", "quantity": "1", "price": "2", "lot": ""}],
    }
    response = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json=body)
    assert response.status_code == 422 and "Linha 1" in response.json()["detail"]
    assert view(case, article)["quantity"] == "0"


def test_bonus_and_discounts_give_the_stock_cost():
    from app.purchase_receiving import ReceiveLine, received_line

    # 5 faturadas + 1 bónus a 6,50 € com 5 % na linha: 30,875 € por 6 unidades.
    line = ReceiveLine(article_id="a", description="Pastilhas", quantity=D(5), bonus=D(1), price=D("6.5"), discount=D(5))
    received, discount, net = received_line(line, D(0))
    assert received == 6 and net == D("30.875")
    assert abs(received * D("6.5") * (1 - discount / 100) - net) < D("0.001")
    # Oferta a 100 % e desconto global de 20 %.
    free = ReceiveLine(article_id="a", description="Oferta", quantity=D(12), price=D(11), discount=D(100))
    assert received_line(free, D(0))[1] == 100
    assert received_line(ReceiveLine(article_id="a", description="X", quantity=D(3), price=D("29.08")), D(20))[1] == D(20)


def test_receipt_from_invoice_reopens_exactly_and_saves_in_place(receiving):
    c, sid, doc_id, warehouse, case = receiving
    article = product(case, code="BONUS1", name="Pastilhas de laranja")
    body = {
        "supplier_id": sid, "warehouse_id": warehouse, "document": "FA.2026/3208", "document_date": "2026-10-20",
        "request_key": str(uuid4()), "global_discount": "20",
        "lines": [{"article_id": article, "description": "Pastilhas", "reference": "PL1", "quantity": "5", "bonus": "1",
                   "price": "6.5", "discount": "5", "vat": "23"}],
    }
    receipt_id = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json=body).json()["receipt_id"]
    assert D(view(case, article)["quantity"]) == 6
    # Reabre como foi lançado: bónus, desconto da linha e desconto global.
    opened = c.get(f"/api/purchase-receiving/receipts/{receipt_id}").json()
    line = opened["lines"][0]
    assert (line["quantity"], line["bonus"], line["price"], line["discount"]) == ("5", "1", "6.5", "5")
    assert opened["receipt"]["global_discount"] == "20" and opened["receipt"]["editable"] and opened["lines"][0]["selected"]["article_id"] == article
    # Gravar a edição atualiza a mesma receção e volta a gerar o stock.
    revision = opened["receipt"]["revision"]
    saved = c.put(f"/api/purchase-receiving/receipts/{receipt_id}?revision={revision}", json={**body, "lines": [{**body["lines"][0], "quantity": "8", "bonus": "0"}]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["receipt_id"] == receipt_id and D(view(case, article)["quantity"]) == 8
    again = c.get(f"/api/purchase-receiving/receipts/{receipt_id}").json()["lines"][0]
    assert (again["quantity"], again["bonus"]) == ("8", "0")
    assert c.put(f"/api/purchase-receiving/receipts/{receipt_id}?revision={revision}", json=body).status_code == 409


def test_service_lines_count_in_the_invoice_but_not_in_stock(receiving):
    c, sid, doc_id, warehouse, case = receiving
    goods = product(case, code="CHA1", name="Chá verde")
    base = {"supplier_id": sid, "warehouse_id": warehouse, "document": "FA.2026/3208", "document_date": "2026-10-20"}
    shipping = {"new_article": {"code": "PORTES", "name": "Portes de envio", "service": True, "lots": True},
                "description": "Portes de envio", "quantity": "1", "price": "3.99", "vat": "23"}
    only_services = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json={**base, "request_key": str(uuid4()), "lines": [shipping]})
    assert only_services.status_code == 422 and "Compras" in only_services.json()["detail"]
    lines = [{"article_id": goods, "description": "Chá verde", "quantity": "4", "price": "2"}, shipping]
    done = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json={**base, "request_key": str(uuid4()), "lines": lines})
    assert done.status_code == 200, done.text
    assert done.json()["lines"] == 1 and done.json()["created_articles"] == 1
    assert D(view(case, goods)["quantity"]) == 4
    portes = next(a for a in c.get("/api/stock/catalog").json()["articles"] if a["code"] == "PORTES")
    assert portes["stock_state"] == "serviço (sem stock)" and not portes["lots"]
    # A edição reabre também a linha de serviço (faz parte do lançamento).
    opened = c.get(f"/api/purchase-receiving/receipts/{done.json()['receipt_id']}").json()
    assert [line["description"] for line in opened["lines"]] == ["Chá verde", "Portes de envio"]
    assert opened["lines"][1]["selected"]["service"] is True


def receipt_body(sid, warehouse, case, **extra):
    article = product(case, code="CURC" + uuid4().hex[:4], name="Cápsulas de cúrcuma")
    other = product(case, code="OMEGA" + uuid4().hex[:4], name="Ómega 3")
    return {
        "supplier_id": sid, "warehouse_id": warehouse, "document": "FA.2026/3208", "document_date": "2026-10-20",
        "request_key": str(uuid4()), "global_discount": "20",
        "lines": [
            {"article_id": article, "description": "Cápsulas de cúrcuma", "quantity": "3", "price": "29.08", "vat": "23"},
            {"article_id": other, "description": "Ómega 3", "quantity": "1", "price": "13.93", "vat": "23"},
        ],
        **extra,
    }


def procurement_row(c, receipt_id):
    return next(d for d in c.get("/api/procurement").json()["documents"] if d["id"] == receipt_id)


def test_erp_supplier_is_listed_suggests_type_and_gets_a_local_record(receiving, monkeypatch):
    from types import SimpleNamespace

    from app import entities
    from app.models import WintouchEntity

    c, _, _, warehouse, case = receiving
    factory, store = case[2], case[4]
    with factory.begin() as db:
        imported = WintouchEntity(enterprise_id="erp-rececao", external_id="F1", name="Fornecedor só no ERP",
                                  tax_number="509999990", is_supplier=True, active=True)
        doc = ArchiveDocument(
            source_key="teste:" + uuid4().hex, origin="digitalizacao", doc_type="fatura_recibo", mime="application/pdf",
            storage="base_dados", storage_ref="archive_blobs:y", entity_nif="509999990", entity_name="Fornecedor só no ERP",
            document_code="FR", document_number="FR 2026/7", issue_date="2026-10-20", base_cents=8093,
            total_cents=9954, original_name="fr.pdf", store_id=store,
        )
        db.add_all([imported, doc])
        db.flush()
        erp_id, doc_id = imported.id, doc.id
    original = entities.list_entities
    erp_row = {"id": "wt:" + erp_id, "name": "Fornecedor só no ERP", "tax_number": "509999990", "is_supplier": True,
               "active": True, "payment_term_id": None, "erp_supplier_payment_term_id": None}
    # A tabela de fornecedores junta as fichas locais e as do ERP (ver entities.list_entities).
    monkeypatch.setattr(entities, "list_entities", lambda user, db: [*original(user, db), erp_row])
    monkeypatch.setattr("app.db.request_company", lambda db: SimpleNamespace(api_enterprise="erp-rececao"))
    listed = next(r for r in c.get("/api/purchase-receiving/documents").json()["rows"] if r["id"] == doc_id)
    assert listed["supplier_id"] == "wt:" + erp_id and listed["supplier_name"] == "Fornecedor só no ERP"
    read = c.post(f"/api/purchase-receiving/{doc_id}/read").json()
    assert read["document"]["supplier_id"] == "wt:" + erp_id
    assert any(s["id"] == "wt:" + erp_id and s["record_id"] == "wt:" + erp_id for s in read["suppliers"])
    types = {t["code"]: t["id"] for t in c.get("/api/purchase-document-types/options").json()["types"]}
    # FR (fatura-recibo) → compra a dinheiro.
    assert read["document"]["suggested_document_type_id"] == types["FCD"]
    done = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json=receipt_body("wt:" + erp_id, warehouse, case,
                                                                                document="FR 2026/7"))
    assert done.status_code == 200, done.text
    with factory() as db:
        local = db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == erp_id))
        assert local is not None and local.is_supplier and local.tax_number == "509999990"
    assert procurement_row(c, done.json()["receipt_id"])["supplier_id"] == local.id
    # Com a ficha local criada, a lista passa a usar o identificador dela.
    again = c.post(f"/api/purchase-receiving/{doc_id}/read").json()
    assert again["document"]["supplier_id"] == local.id


def test_rounding_adjustment_matches_the_invoice_total(receiving):
    c, sid, doc_id, warehouse, case = receiving
    factory = case[2]
    # A fatura tem mais 1 cêntimo de IVA do que o cálculo por linha (99,54 €).
    with factory.begin() as db:
        db.get(ArchiveDocument, doc_id).total_cents = 9955
    read = c.post(f"/api/purchase-receiving/{doc_id}/read").json()
    types = {t["code"]: t["id"] for t in c.get("/api/purchase-document-types/options").json()["types"]}
    assert read["document"]["suggested_document_type_id"] == types["FC"]
    plain = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json=receipt_body(sid, warehouse, case))
    assert plain.status_code == 200, plain.text
    row = procurement_row(c, plain.json()["receipt_id"])
    assert (row["base_cents"], row["tax_cents"], row["total_cents"]) == (8093, 1861, 9954) and not row["rounding_adjustment"]
    # Editar com acerto: base e IVA ficam iguais aos da fatura; o acerto fica registado.
    opened = c.get(f"/api/purchase-receiving/receipts/{row['id']}").json()
    body = receipt_body(sid, warehouse, case, match_document_totals=True)
    saved = c.put(f"/api/purchase-receiving/receipts/{row['id']}?revision={opened['receipt']['revision']}", json=body)
    assert saved.status_code == 200, saved.text
    row = procurement_row(c, row["id"])
    assert (row["base_cents"], row["tax_cents"], row["total_cents"]) == (8093, 1862, 9955)
    assert row["rounding_adjustment"] == {"base_cents": 0, "tax_cents": 1}
    reopened = c.get(f"/api/purchase-receiving/receipts/{row['id']}").json()["receipt"]
    assert reopened["match_document_totals"] and reopened["rounding_adjustment"] == {"base_cents": 0, "tax_cents": 1}
    # Uma diferença maior do que um arredondamento não é acertada.
    with factory.begin() as db:
        db.get(ArchiveDocument, doc_id).total_cents = 10054
    refused = c.put(f"/api/purchase-receiving/receipts/{row['id']}?revision={reopened['revision']}", json=body)
    assert refused.status_code == 422 and "excede o acerto" in refused.json()["detail"]


@pytest.mark.parametrize("discounts", [("100", "0"), ("0", "100")])
def test_same_article_on_separate_paid_and_free_lines(receiving, discounts):
    c, sid, doc_id, warehouse, case = receiving
    article = product(case, code="REPETIDO", name="Artigo com oferta")
    body = {
        "supplier_id": sid, "warehouse_id": warehouse, "document": "FA.2026/3208",
        "document_date": "2026-10-20", "request_key": str(uuid4()),
        "lines": [
            {"article_id": article, "description": "Artigo com oferta", "quantity": str(qty),
             "price": "10", "discount": discount, "vat": "23"}
            for qty, discount in zip((2, 3), discounts)
        ],
    }
    response = c.post(f"/api/purchase-receiving/{doc_id}/confirm", json=body)
    assert response.status_code == 200, response.text
    receipt_id = response.json()["receipt_id"]
    assert response.json()["lines"] == 2
    assert D(view(case, article)["quantity"]) == 5
    with case[2]() as db:
        entries = list(db.scalars(select(StockEntry).where(StockEntry.article_id == article)))
        assert len(entries) == 2
        assert sorted(D(entry.unit_cost) for entry in entries) == [D(0), D(10)]
    reopened = c.get(f"/api/purchase-receiving/receipts/{receipt_id}").json()
    assert [line["selected"]["article_id"] for line in reopened["lines"]] == [article, article]
    assert [D(line["discount"]) for line in reopened["lines"]] == list(map(D, discounts))
    assert c.post(f"/api/purchase-receiving/{doc_id}/confirm", json=body).json()["receipt_id"] == receipt_id
    assert D(view(case, article)["quantity"]) == 5


def test_supplier_can_be_edited_and_refreshed_from_receiving(receiving):
    from app.models import PaymentTerm

    c, sid, _, _, case = receiving
    with case[2].begin() as db:
        term = PaymentTerm(code="45DIAS", name="45 dias", term_type="apos", days=45)
        db.add(term)
        db.flush()
        term_id = term.id
    options = c.get("/api/purchase-receiving/suppliers")
    assert options.status_code == 200, options.text
    supplier = next(s for s in options.json() if s["id"] == sid)
    assert supplier["record_id"] == "local:" + sid
    path = "/api/tables/suppliers/records/" + supplier["record_id"]
    details = c.get(path + "/details").json()
    values = {**details["values"], "name": "Fornecedor atualizado", "payment_term_id": term_id}
    saved = c.post(path, json={"values": values})
    assert saved.status_code == 200, saved.text
    refreshed = next(s for s in c.get("/api/purchase-receiving/suppliers").json() if s["id"] == sid)
    assert refreshed["name"] == "Fornecedor atualizado"
    assert refreshed["payment_term"] == {"name": "45 dias", "term_type": "apos", "days": 45}


def test_supplier_refresh_requires_receiving_permissions(receiving):
    c, _, _, _, _ = receiving
    login = c.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert login.status_code == 200
    assert c.get("/api/purchase-receiving/suppliers").status_code == 403
