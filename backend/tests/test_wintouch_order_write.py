import copy
import json
from decimal import Decimal as D

import httpx
import pytest
from app.wintouch_order_write import dispatch_once, product_payload, purchase_payload, verify_result
from app.wintouch_sales import Reader
from fastapi import HTTPException


def catalogs():
    return {
        "stock_units": [{"ID": "un", "Code": "un"}],
        "vats": [{"ID": "vat", "TaxRate": 23}],
        "product_1st_categories": [{"ID": "cat"}],
        "price_policies": [{"ID": "price"}],
        "warehouses": [{"ID": "wh"}],
        "sectors": [{"ID": "sector"}],
        "currencies": [{"ID": "eur", "Code": "EUR"}],
        "document_types": [{"ID": "type", "Class": 32}],
        "document_series": [{"ID": "series", "DocumentTypeID": "type"}],
    }


def mapping():
    return {
        "unit_id": "un",
        "vat_id": "vat",
        "category_id": "cat",
        "price_policy_id": "price",
        "document_type_id": "type",
        "document_serie_id": "series",
        "warehouse_id": "wh",
        "sector_id": "sector",
        "currency_id": "eur",
    }


def product():
    return product_payload(
        {
            "code": "CODE",
            "name": "Artigo",
            "unit": "un",
            "vat_rate": "23",
            "sale_net": "12",
            "barcodes": ["5600000000000"],
        },
        mapping(),
        catalogs(),
        "erp-product",
    )


def purchase():
    saved = {
        "supplier_nif": "500000000",
        "document": "TEST/2",
        "document_type": "FT",
        "document_date": "2026-10-21",
        "due_date": "2026-10-22",
        "note": "Conferido",
        "lines": [
            {
                "article_id": "local",
                "description": "Artigo",
                "quantity": "5",
                "price": "10",
                "discount": "0",
                "vat": "23",
            }
        ],
    }
    entity = {"ID": "entity", "VATNumber": "500000000", "IsSupplier": True}
    products = {"local": {"ID": "p", "StockUnitID": "un", "VATPurchasesID": "vat"}}
    return purchase_payload(saved, mapping(), catalogs(), entity, products, "invoice")


def test_only_confirmed_product_fields_barcodes_and_prices_count_as_success():
    payload = product()
    operation = {"kind": "product", "payload": payload}
    assert verify_result(operation, payload)
    changed = copy.deepcopy(payload)
    changed["ProductPrices"][0]["PriceWithoutVAT"] = 13
    assert not verify_result(operation, changed)
    changed = copy.deepcopy(payload)
    changed["ProductBarCodes"] = []
    assert not verify_result(operation, changed)


def test_purchase_contract_and_three_way_confirmation_require_details_and_totals():
    payload = purchase()
    remote = {**payload, "VATNumber": "500000000", "BaseValue1": 50, "VATValue1": D("11.5")}
    operation = {
        "kind": "purchase",
        "payload": payload,
        "supplier_nif": "500000000",
        "total_cents": 6150,
        "tax_cents": 1150,
    }
    assert verify_result(operation, remote)
    assert not verify_result(operation, {**remote, "VATValue1": "11.49"})
    assert not verify_result(operation, {**remote, "ProductDocumentDetails": []})


def test_purchase_refuses_sales_types_and_nonmatching_series():
    values = catalogs()
    values["document_types"][0]["Class"] = 1
    with pytest.raises(HTTPException):
        purchase_payload({}, mapping(), values, {}, {}, "x")


def test_uncertain_creation_is_recovered_by_get_without_a_second_post():
    payload = product()
    calls, saved = [], []
    remote = None

    def transport(request):
        nonlocal remote
        calls.append(request.method)
        if request.method == "POST":
            remote = json.loads(request.content)
            assert isinstance(remote["ProductPrices"][0]["PriceWithoutVAT"], (int, float))
            raise httpx.ReadTimeout("Response lost", request=request)
        return httpx.Response(200, json=remote)

    client = httpx.Client(transport=httpx.MockTransport(transport))
    reader = Reader(client, "https://erp.test/api/v1", "tenant", "test-key")
    reader.origin_fingerprint = "origin"
    operation = {
        "kind": "product",
        "origin": "origin",
        "state": "prepared",
        "payload": json.loads(json.dumps(payload, default=str)),
    }

    def persist(value):
        saved.append(copy.deepcopy(value))

    result = dispatch_once(reader, operation, persist)
    assert result["state"] == "uncertain" and saved[0]["state"] == "sending"
    assert calls == ["POST"]
    recovered = dispatch_once(reader, operation, persist)
    assert recovered["state"] == "confirmed" and calls == ["POST", "GET"]
    dispatch_once(reader, operation, persist)
    assert calls == ["POST", "GET"]
    client.close()


def test_changed_company_origin_never_writes():
    class ReaderStub:
        origin_fingerprint = "other-company"

    with pytest.raises(HTTPException) as error:
        dispatch_once(ReaderStub(), {"origin": "original"}, lambda _: None)
    assert error.value.status_code == 409


def test_connection_requires_write_capability_resource_and_demo_confirmation(monkeypatch):
    from types import SimpleNamespace

    from app.wintouch_order_write import connection

    company = SimpleNamespace(mode="demonstracao")
    config = {
        "mode": "integrated",
        "provider": "wintouch",
        "active": True,
        "writes": True,
        "resources": ["products"],
        "capabilities": {"write_products": True},
    }
    monkeypatch.setattr("app.wintouch_order_write.request_company", lambda db: company)
    monkeypatch.setattr("app.operating_modes.policy", lambda db: config)
    user = SimpleNamespace(role="administrador")
    with pytest.raises(HTTPException, match="409"):
        with connection(None, user, write=True, resource="products"):
            pytest.fail("A demonstração não foi autorizada")
    with pytest.raises(HTTPException, match="409"):
        with connection(None, user, write=True, resource="purchase_documents", confirm_demo=True):
            pytest.fail("O recurso não foi autorizado")
    config["capabilities"]["write_products"] = False
    with pytest.raises(HTTPException, match="409"):
        with connection(None, user, write=True, resource="products", confirm_demo=True):
            pytest.fail("A capacidade não foi autorizada")
