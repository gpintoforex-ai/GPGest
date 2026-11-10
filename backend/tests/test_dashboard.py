"""Dashboard de gestão: correspondência com as fontes, comparações, TPA e permissões."""

from datetime import date, timedelta

from app.dashboard import comparison_range
from saft_fixtures import invoice
from test_billing_conference import case, imported, overview, receipt  # noqa: F401
from test_saft_sales import saft_case  # noqa: F401

JANUARY = {"start": "2026-01-01", "end": "2026-01-31"}


def dashboard(client, **params):
    response = client.get("/api/dashboard/overview", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_sales_match_billing_source_with_credit_notes_and_cancelled_excluded(case):  # noqa: F811
    client = case[0]
    imported(case, [invoice(number=1, value="24.60"), invoice("NC", 2, value="12.30"), invoice(number=3, value="12.30", status="A")])
    data = dashboard(client, **JANUARY)
    source = overview(case, **JANUARY)["indicators"]["billing"]
    sales = data["sales"]
    assert sales["status"] == "ok" and sales["source"] == "saft" and sales["vat_basis"] == "sem IVA"
    assert sales["total_cents"] == source["net_cents"] == 2000 - 1000 and sales["documents"] == source["documents"] == 2
    assert sum(point["current_cents"] for point in sales["points"]) == sales["total_cents"]
    # As linhas do SAF-T por artigo somam o mesmo total; sem custos conhecidos, não há margem.
    assert data["margin"]["sales_cents"] == sales["total_cents"]
    assert data["margin"]["gross_margin_cents"] is None and data["margin"]["cost_coverage_percent"] == 0.0
    assert data["comparison"]["method"] == "Mês anterior completo" and sales["previous_cents"] is None


def test_tpa_settlement_is_not_a_second_receipt(case):  # noqa: F811
    client = case[0]
    receipt(case, "103", payment_method="cartao")
    today = date.today().isoformat()
    data = dashboard(client, start=today, end=today)
    treasury = data["treasury"]
    source = overview(case, start=today, end=today)["indicators"]
    assert treasury["status"] == "ok", treasury
    assert treasury["received_cents"] == source["receipts"]["net_cents"] == 10300
    assert treasury["paid_cents"] == source["payments_cents"]
    assert treasury["card_pending_cents"] == source["balances"]["card_pending_settlement_cents"]
    assert sum(flow["in_cents"] for flow in treasury["flows"]) == treasury["received_cents"]


def test_month_in_progress_compares_same_elapsed_days():
    assert comparison_range(date(2026, 10, 1), date(2026, 10, 20), "previous")[:2] == (date(2026, 9, 1), date(2026, 9, 20))
    assert comparison_range(date(2026, 3, 1), date(2026, 3, 31), "previous")[:2] == (date(2026, 2, 1), date(2026, 2, 28))
    assert comparison_range(date(2026, 1, 5), date(2026, 1, 18), "previous")[:2] == (date(2025, 12, 22), date(2026, 1, 4))
    assert comparison_range(date(2024, 2, 29), date(2024, 3, 10), "yoy")[:2] == (date(2023, 2, 28), date(2023, 3, 10))


def test_no_sales_source_is_reported_as_no_data_not_zero(setup):
    client = setup[0]
    data = dashboard(client, start=(date.today() - timedelta(days=6)).isoformat(), end=date.today().isoformat())
    assert data["sales"]["status"] == "empty" and "Sem dados de faturação" in data["sales"]["message"]
    assert data["vat"]["status"] == "empty" and data["top_articles"]["status"] == "empty"


def test_roles_cannot_read_costs_or_results_they_are_not_allowed_to_see(setup):
    client = setup[0]
    assert dashboard(client)["permissions"] == {"finance": True, "costs": True, "article_costs": True}
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}).status_code == 200
    reader = dashboard(client)
    assert reader["permissions"]["costs"] is False
    assert reader["expenses"]["status"] == "restricted" and reader["margin"]["status"] == "restricted"
    assert reader["vat"]["status"] == "restricted"
    assert client.get("/api/dashboard/expenses/drilldown", params=JANUARY).status_code == 403
    assert "documents_without_attachment" not in reader["operations"]["items"]
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    operator = dashboard(client)
    assert operator["permissions"]["finance"] is False
    assert {operator[key]["status"] for key in ("sales", "treasury", "expenses", "margin")} == {"restricted"}
    assert operator["vat"]["status"] == operator["top_articles"]["status"] == "restricted"
    assert operator["operations"]["status"] == "ok"


def test_erp_document_base_is_kept_only_when_consistent_with_total():
    from app.wintouch_sales import document_base
    row = {"BaseValue1": "100.00", "VATValue1": "23.00", "BaseValue2": "10.00", "VATValue2": "0.60"}
    assert document_base(row, 13360) == (11000, 2360)
    assert document_base(row, 15000) == (None, None)  # incoerente: fica só o total
    assert document_base({"NetValueTotal": "12.30"}, 1230) == (None, None)


def test_erp_sales_are_shown_without_vat_only_when_every_document_has_a_base(setup):
    import json

    from app.models import Register, WintouchSaleRecord
    client, ids, factory = setup
    today = date.today()
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        for index, (net, gross) in enumerate(((10000, 12300), (5000, 6150))):
            db.add(WintouchSaleRecord(enterprise_id="erp", external_id=f"document:{index}", store_id=store, kind="documento", document_date=today.isoformat(),
                                      data=json.dumps({"document_group": "vendas", "direction": 1, "amount_cents": gross, "net_cents": net, "tax_cents": gross - net, "cancelled": False})))
    sales = dashboard(client, start=today.isoformat(), end=today.isoformat())["sales"]
    assert sales["source"] == "erp" and sales["vat_basis"] == "sem IVA" and sales["total_cents"] == 15000
    with factory.begin() as db:
        db.add(WintouchSaleRecord(enterprise_id="erp", external_id="document:old", store_id=store, kind="documento", document_date=today.isoformat(),
                                  data=json.dumps({"document_group": "vendas", "direction": 1, "amount_cents": 1230, "cancelled": False})))
    sales = dashboard(client, start=today.isoformat(), end=today.isoformat())["sales"]
    assert sales["vat_basis"] == "com IVA" and sales["total_cents"] == 12300 + 6150 + 1230 and sales["missing_base_documents"] == 1


def test_dashboard_vat_matches_map_and_top_subtracts_returns(case):
    import json
    from sqlalchemy import select
    from app.models import BusinessEntity, Category, Purchase, WintouchSaleRecord
    client = case[0]
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    p2 = invoice(number=4, value="36.90").replace("<Quantity>1</Quantity>", "<Quantity>3</Quantity>")
    imported(case, [invoice(number=1, value="24.60"), invoice("NC", 2, value="12.30"), invoice(number=3, status="A"), p2])
    with case[2].begin() as db:
        for sale in db.scalars(select(WintouchSaleRecord).where(WintouchSaleRecord.kind == "saft_invoice")):
            payload = json.loads(sale.data)
            if payload["document"] == "FT TESTE/4":
                lines = payload["raw"]["Line"]
                line = lines[0] if isinstance(lines, list) else lines
                line["ProductCode"], line["ProductDescription"] = "P2", "Outro artigo"
                sale.data = json.dumps(payload)
        category = Category(name="Dedução parcial", vat_deduction_percent=50)
        db.add(category)
        db.flush()
        db.add(BusinessEntity(name="Fornecedor de teste", tax_number="500000009", is_supplier=True, category_id=category.id))
        db.add(Purchase(store_id=case[3], supplier_nif="500000009", supplier_name="Fornecedor de teste", document="FT COMPRA/1",
                        document_date="2026-01-12", amount_cents=12300, base_cents=10000, tax_cents=2300))
    data = dashboard(client, **JANUARY)
    vat = client.get("/api/management/vat-map", params=JANUARY).json()["periods"][0]
    assert data["vat"]["status"] == "ok"
    assert data["vat"]["deductible_cents"] == 1150
    for key in ("output_cents", "input_cents", "deductible_cents", "balance_cents"):
        assert data["vat"][key] == vat[key]
    rows = data["top_articles"]["rows"]
    assert [r["code"] for r in rows] == ["P2"]  # P1: uma venda e uma devolução, quantidade líquida zero.
    assert rows[0]["quantity"] == "3" and rows[0]["sales_cents"] == 3000
    assert dashboard(client, start="2026-02-01", end="2026-02-28")["top_articles"]["status"] == "empty"
    # Série de compras (evolução vendas vs compras): mesmos documentos e fórmula do total das despesas, nos
    # mesmos períodos da faturação.
    expenses = data["expenses"]
    assert expenses["unit"] == data["sales"]["unit"] and len(expenses["points"]) == len(data["sales"]["points"])
    assert sum(p["amount_cents"] for p in expenses["points"]) == expenses["total_cents"] == 10000 + 1150
    assert [p["label"] for p in expenses["points"]] == [p["label"] for p in data["sales"]["points"]]
    route = "/api/dashboard/expenses/drilldown"
    summary = client.get(route, params=JANUARY).json()
    assert summary["total_cents"] == expenses["total_cents"]
    key = summary["rows"][0]["id"]
    params = {**JANUARY, "category": key, "limit": 1}
    listing = client.get(route, params=params).json()
    assert listing["count"] == 1 and listing["total_cents"] == 11150
    assert client.get(route, params={**params, "offset": 1}).json()["rows"] == []
    item = listing["rows"][0]
    detail = client.get(route, params={**params, "document_id": item["id"]}).json()
    assert detail["document"]["allocations"] == [{"label": "Dedução parcial", "base_cents": 10000, "tax_cents": 2300, "amount_cents": 11150}]
    assert client.get(route, params={**params, "document_id": "inexistente"}).status_code == 404
    assert client.get(route, params={"start": "2026-02-01", "end": "2026-02-28"}).json()["total_cents"] == 0


def test_expense_drilldown_preserves_origin_deduplication_and_document_lines(setup):
    import json
    from uuid import uuid4

    from app.models import BusinessEntity, ERPDocument, ProcurementDocument, Purchase, Register, now

    client, ids, factory = setup
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        supplier = BusinessEntity(name="Fornecedor teste", tax_number="500000009", is_supplier=True, category_id=ids["category"])
        purchase = Purchase(store_id=store, supplier_nif="500000009", supplier_name="Fornecedor teste",
                            document="FT TESTE/1", document_date="2026-01-12", amount_cents=2460, base_cents=2000, tax_cents=460)
        db.add_all([supplier, purchase])
        db.flush()
        db.add(ProcurementDocument(kind="invoice", store_id=store, supplier_id=supplier.id, user_id=ids["operator"],
                                   purchase_id=purchase.id, request_key=str(uuid4()), status="confirmed", confirmed_at=now(),
                                   data=json.dumps({"category_id": ids["category"], "lines": [
                                       {"description": "Artigo", "quantity": "2", "price": "10", "discount": "0", "vat": "23"}]})))
        for index, number in enumerate(("FT TESTE/1", "FT ERP/2")):
            db.add(ERPDocument(store_id=store, enterprise_id="teste", external_id=str(index), supplier_nif="500000009",
                               supplier_name="Fornecedor teste", document=number, internal_document=number, document_type="FT",
                               document_date="2026-01-11", amount_cents=2460, base_cents=2000, tax_cents=460))
        purchase_id = purchase.id
    route = "/api/dashboard/expenses/drilldown"
    params = {**JANUARY, "store_id": store, "category": ids["category"], "limit": 1}
    first = client.get(route, params=params).json()
    second = client.get(route, params={**params, "offset": 1}).json()
    assert first["count"] == second["count"] == 2
    assert first["total_cents"] == second["total_cents"] == sum(row["amount_cents"] for row in first["rows"] + second["rows"])
    assert first["rows"][0]["id"] == purchase_id and second["rows"][0]["source"] == "ERP"
    assert second["rows"][0]["synced_at"]
    detail = client.get(route, params={**params, "document_id": purchase_id}).json()
    assert detail["lines"][0]["description"] == "Artigo" and detail["lines"][0]["base_cents"] == 2000


def test_erp_vat_missing_breakdown_does_not_show_false_balance(setup):
    import json
    from app.models import Register, WintouchSaleRecord
    client, ids, factory = setup
    client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "mensal"})
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        db.add(WintouchSaleRecord(enterprise_id="erp", external_id="vat-complete", store_id=store, kind="documento", document_date="2026-01-10",
                                  data=json.dumps({"document_group": "vendas", "amount_cents": 12300, "net_cents": 10000, "tax_cents": 2300})))
    assert dashboard(client, **JANUARY)["vat"]["output_cents"] == 2300
    assert client.get("/api/management/vat-map", params=JANUARY).json()["periods"][0]["output_cents"] == 2300
    with factory.begin() as db:
        db.add(WintouchSaleRecord(enterprise_id="erp", external_id="vat-incomplete", store_id=store, kind="documento", document_date="2026-01-11",
                                  data=json.dumps({"document_group": "vendas", "amount_cents": 1230})))
    vat = dashboard(client, **JANUARY)["vat"]
    assert vat["status"] == "empty" and "sem IVA discriminado" in vat["message"] and "balance_cents" not in vat
    assert client.get("/api/management/vat-map", params=JANUARY).json()["available"] is False


def test_erp_top_uses_imported_lines_without_stock_posting_and_scopes_store(setup):
    import json
    from sqlalchemy import select
    from app.models import Register, Store, WintouchProduct, WintouchSaleRecord
    client, ids, factory = setup
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        other = db.scalar(select(Store.id).where(Store.id != store))
        db.add(WintouchProduct(enterprise_id="erp", external_id="p1", code="P001", name="Artigo ERP"))
        def add(key, quantity="5", net="50.005", **changes):
            target = changes.pop("store_id", store)
            active = changes.pop("active", True)
            day = changes.pop("document_date", "2026-01-10")
            payload = {"document_group": "vendas", "document_class": 3, "lines": [{"product_id": "p1", "quantity": quantity, "net": net}], **changes}
            db.add(WintouchSaleRecord(enterprise_id="erp", external_id=key, store_id=target, kind="documento", document_date=day, active=active, data=json.dumps(payload)))
        add("sale")
        add("return", "2", "20", document_class=6)
        add("cancelled", "90", cancelled=True)
        add("inactive", "90", active=False)
        add("purchase", "90", document_group="compras")
        add("other", "10", "100", store_id=other)
        add("outside", "90", document_date="2026-02-01")
        add("unknown-product", lines=[{"product_id": "no-catalogue", "quantity": "1", "net": "5"}])
    top = dashboard(client, store_id=store, **JANUARY)["top_articles"]
    assert top["source"] == "erp" and top["status"] == "ok" and top["incomplete_documents"] == 0
    assert top["rows"][0]["code"] == "P001" and top["rows"][0]["name"] == "Artigo ERP"
    assert top["rows"][0]["quantity"] == "3" and top["rows"][0]["sales_cents"] == 3001
    assert len(top["rows"]) == 2  # Includes an ERP product without a local stock/catalogue record.
    assert dashboard(client, store_id=other, **JANUARY)["top_articles"]["rows"][0]["quantity"] == "10"
    again = dashboard(client, store_id=store, **JANUARY)["top_articles"]
    assert again["rows"] == top["rows"]  # Reading the ranking creates no stock movements.


def test_erp_top_reports_missing_and_malformed_lines(setup):
    import json
    from app.models import Register, WintouchSaleRecord
    client, ids, factory = setup
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        for key, payload in [
            ("legacy", {"document_group": "vendas"}),
            ("missing", {"document_group": "vendas", "document_class": 3}),
            ("partial", {"document_group": "vendas", "document_class": 3, "lines": [
                {"product_id": "p1", "quantity": "4", "net": "10"},
                {"product_id": "p1", "quantity": "NaN", "net": "10"},
            ]}),
        ]:
            db.add(WintouchSaleRecord(enterprise_id="erp", external_id=key, kind="documento", store_id=store, document_date="2026-01-10", data=json.dumps(payload)))
    top = dashboard(client, **JANUARY)["top_articles"]
    assert top["status"] == "ok" and top["incomplete_documents"] == 3
    assert top["rows"][0]["quantity"] == "4"


def margin_case(setup):
    """Vendas Wintouch de janeiro com todos os casos da margem histórica e da cobertura de custos."""
    import json
    from decimal import Decimal
    from app.models import Register, StockArticle, StockEntry, StockWarehouse, WintouchProduct, WintouchSaleRecord
    from app.wintouch_stock import line_key
    client, ids, factory = setup
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        warehouse = StockWarehouse(store_id=store, name="Armazém margem")
        products = {key: WintouchProduct(enterprise_id="erp", external_id=key, code=key.upper(), name=f"Artigo {key}")
                    for key in ("p1", "p2", "p3", "p4", "p5", "orphan")}
        db.add_all([warehouse, *products.values()])
        db.flush()
        details = {"p1": {"family": "Suplementos"}, "p5": {"service": True}}
        articles = {key: StockArticle(code=p.code, name=p.name, erp_id=p.id, data=json.dumps(details.get(key, {})))
                    for key, p in products.items() if key != "orphan"}
        db.add_all(articles.values())
        db.flush()

        def entry(key, kind, quantity, cost, day, request_key):
            db.add(StockEntry(article_id=articles[key].id, warehouse_id=warehouse.id, user_id=ids["operator"], kind=kind, quantity=Decimal(quantity),
                              unit_cost=None if cost is None else Decimal(cost), occurred_on=day, request_key=request_key, data="{}"))
        entry("p1", "purchase", "6", "10.42", "2026-01-05", "margin:p1:1")
        entry("p1", "sale", "-1", "9", "2026-01-12", line_key("erp", "l-b"))  # Custo gravado na saída de stock da venda.
        entry("p1", "purchase", "5", "20", "2026-01-20", "margin:p1:2")  # Compra posterior: não muda vendas anteriores.
        entry("p3", "purchase", "2", "4", "2026-01-25", "margin:p3")  # Só depois da venda: sem custo histórico.
        entry("p4", "purchase", "1", "0", "2026-01-02", "margin:p4")  # Oferta: custo zero conhecido.

        def sale(key, product, line_id, quantity, net, day, document_class=40):
            db.add(WintouchSaleRecord(enterprise_id="erp", external_id=key, store_id=store, kind="documento", document_date=day,
                                      data=json.dumps({"document_group": "vendas", "document_class": document_class,
                                                       "lines": [{"id": line_id, "product_id": product, "quantity": quantity, "net": net}]})))
        sale("a", "p1", "l-a", "2", "29.52", "2026-01-10")
        sale("b", "p1", "l-b", "1", "15", "2026-01-12")
        sale("return", "p1", "l-r", "1", "14.76", "2026-01-15", document_class=6)
        sale("no-cost", "p2", "l-2", "1", "50", "2026-01-11")
        sale("no-history", "p3", "l-3", "1", "10", "2026-01-11")
        sale("zero", "p4", "l-4", "1", "8", "2026-01-11")
        sale("service", "p5", "l-5", "1", "7", "2026-01-11")
        sale("no-match", "orphan", "l-6", "1", "2", "2026-01-11")
        sale("unknown", "missing", "l-7", "1", "3", "2026-01-11")
    return client, store


def test_margin_uses_cost_of_goods_sold_and_classifies_missing_costs(setup):
    client, store = margin_case(setup)
    margin = dashboard(client, store_id=store, **JANUARY)["margin"]
    # Serviço fora; mercadorias: 29,52 + 15 − 14,76 + 50 + 10 + 8 + 2 + 3 = 102,76 €.
    assert margin["status"] == "ok" and margin["source"] == "erp" and margin["sales_cents"] == 10276
    assert margin["services_cents"] == 700 and margin["service_lines"] == 1
    # Com custo: venda A ao custo médio à data (10,42; a compra de 20 € é posterior), venda B ao custo da
    # saída de stock (9), devolução ao custo médio (subtrai) e a oferta a custo zero.
    assert margin["covered_cents"] == 2952 + 1500 - 1476 + 800 and margin["cost_cents"] == 2084 + 900 - 1042
    assert margin["gross_margin_cents"] == 3776 - 1942 and margin["gross_margin_percent"] == 48.6
    # Cobertura parcial: a margem é apresentada, mas sempre identificada como parcial.
    assert margin["cost_coverage_percent"] == 36.7 and margin["level"] == "critical" and margin["partial"] is True
    assert margin["uncovered_cents"] == 5000 + 1000 + 200 + 300
    # Estimado (sem saída de stock): venda A, devolução e oferta; a venda B usa o custo gravado.
    assert margin["estimated_cents"] == 2952 - 1476 + 800 and margin["zero_cost_cents"] == 800
    assert {r["reason"]: r["sales_cents"] for r in margin["reasons"]} == {
        "no_cost": 5000, "no_history": 1000, "zero_cost": 800, "unidentified": 300, "no_match": 200}
    assert margin["review_articles"] == 5 and margin["negative_articles"] == 0
    assert sum(p["covered_cents"] for p in margin["points"]) == margin["covered_cents"]
    assert margin["rows"][0]["name"] == "Artigo sem ficha" or any(r["name"] == "Suplementos" for r in margin["rows"])


def test_margin_articles_grid_lists_cost_source_reason_and_erp_state(setup):
    client, store = margin_case(setup)
    response = client.get("/api/dashboard/margin-articles", params={"store_id": store, **JANUARY})
    assert response.status_code == 200, response.text
    rows = {row["code"]: row for row in response.json()["rows"]}
    assert rows["P1"]["cost_source"] == "mixed" and rows["P1"]["family"] == "Suplementos" and rows["P1"]["reasons"] == []
    from decimal import Decimal
    assert rows["P1"]["erp_status"].startswith("Sincronizado") and Decimal(rows["P1"]["quantity"]) == 2  # 2 + 1 − 1 devolvida
    assert rows["P2"]["reasons"] == ["no_cost"] and rows["P2"]["unit_cost"] is None and rows["P2"]["needs_review"]
    assert rows["P3"]["reasons"] == ["no_history"]
    assert rows["P4"]["reasons"] == ["zero_cost"] and rows["P4"]["margin_cents"] == 800
    assert rows["ORPHAN"]["reasons"] == ["no_match"] and rows["ORPHAN"]["erp_status"] == "Sem ficha local"
    assert rows["ERP:missing"]["reasons"] == ["unidentified"] and rows["ERP:missing"]["erp_status"] == "Sem correspondência"
    assert "P5" not in rows  # Serviço: fora da grelha de margem.
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/dashboard/margin-articles", params=JANUARY).status_code == 403


def test_margin_without_sales_in_period_is_empty_not_zero(setup):
    client, store = margin_case(setup)
    margin = dashboard(client, store_id=store, start="2026-03-01", end="2026-03-31")["margin"]
    assert margin["status"] == "empty" and margin["gross_margin_cents"] is None and margin["cost_coverage_percent"] is None


def test_top_does_not_add_erp_lines_to_saft_sales(case):
    import json
    from app.models import WintouchSaleRecord
    imported(case, [invoice()])
    with case[2].begin() as db:
        db.add(WintouchSaleRecord(enterprise_id="erp", external_id="same-sale", kind="documento", store_id=case[3], document_date="2026-01-10",
                                  data=json.dumps({"document_group": "vendas", "document_class": 3, "lines": [{"product_id": "P1", "quantity": "100", "net": "1000"}]})))
    top = dashboard(case[0], **JANUARY)["top_articles"]
    assert top["source"] == "saft" and top["rows"][0]["quantity"] == "1"
    assert top["rows"][0]["sales_cents"] == 1000
