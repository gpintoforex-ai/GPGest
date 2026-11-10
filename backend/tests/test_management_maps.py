"""Mapas de gestão: custos e proveitos e mapa de IVA (SAF-T de vendas e documentos de compra)."""

import json

from app.models import BusinessEntity, Category, Purchase, WintouchSaleRecord
from test_google_invoices import google_case  # noqa: F401
from test_platform import platform  # noqa: F401


def saft(db, store, nif, number, date, net, tax, direction=1, kind="FT"):
    db.add(WintouchSaleRecord(enterprise_id="saft:" + nif, external_id="doc-" + number, store_id=store, kind="saft_invoice", document_date=date,
                              data=json.dumps({"document": number, "type": kind, "date": date, "status": "N", "direction": direction,
                                               "gross_cents": net + tax, "net_cents": net, "tax_cents": tax, "payments": []})))


def test_costs_revenues_and_vat_map_use_rubric_deduction(google_case):  # noqa: F811
    client, factory, _, company, _, identifier = google_case
    nif = client.get("/api/bootstrap").json()["company"]["nif"]
    assert client.post("/api/settings/fiscal", json={"vat_regime": "normal", "vat_period": "trimestral"}).status_code == 200
    with factory.begin() as db:
        purchase = db.get(Purchase, identifier)
        purchase.base_cents, purchase.tax_cents, purchase.origin = 10000, 2300, "efatura"
        fuel = Category(name="Combustíveis", vat_deduction_percent=50); db.add(fuel); db.flush(); fuel_id = fuel.id
        db.add(BusinessEntity(name="Fornecedor Alfa", tax_number="510774318", is_supplier=True, category_id=fuel_id))
        saft(db, purchase.store_id, nif, "FT 1/1", "2026-10-05", 20000, 4600)
        saft(db, purchase.store_id, nif, "NC 1/1", "2026-10-06", 1000, 230, -1, "NC")
    data = client.get("/api/management/costs-revenues?start=2026-10-01&end=2026-10-31&group=month").json()
    assert data["periods"] == [{"key": "2026-10", "label": "Outubro 2026"}]
    assert data["revenue"]["2026-10"] == 19000
    assert data["costs"][0]["category_id"] == fuel_id and data["costs"][0]["values"]["2026-10"] == 10000 + 1150
    assert data["margin"]["2026-10"] == 19000 - 11150
    # IVA das compras no mapa de custos: os mesmos documentos e a mesma dedução do Mapa de IVA.
    assert data["purchase_vat"]["input"]["2026-10"] == 2300 and data["purchase_vat"]["deductible"]["2026-10"] == 1150
    assert data["purchase_vat"]["non_deductible"]["2026-10"] == 1150
    assert data["purchase_vat"]["totals"] == {"input_cents": 2300, "deductible_cents": 1150, "non_deductible_cents": 1150}
    vat = client.get("/api/management/vat-map?start=2026-10-01&end=2026-12-31").json()
    assert vat["available"] and vat["periods"][0]["key"] == "2026-T4" and vat["estimated_deduction"]
    period = vat["periods"][0]
    assert (period["output_cents"], period["input_cents"], period["deductible_cents"], period["balance_cents"]) == (4370, 2300, 1150, 3220)
    assert client.post("/api/settings/fiscal", json={"vat_regime": "isento_53", "vat_period": "trimestral"}).status_code == 200
    assert client.get("/api/management/vat-map?start=2026-10-01&end=2026-12-31").json()["available"] is False
    exempt = client.get("/api/management/costs-revenues?start=2026-10-01&end=2026-10-31").json()
    assert exempt["costs"][0]["values"]["2026-10"] == 12300


def test_costs_revenues_use_erp_sales_in_integrated_mode(google_case):  # noqa: F811
    client, factory, _, company, _, identifier = google_case
    with factory.begin() as db:
        store = db.get(Purchase, identifier).store_id
        for key, net, direction, extra in (("sale", 20000, 1, {}), ("return", 1500, -1, {}), ("void", 9000, 1, {"cancelled": True}),
                                           ("purchase", 7000, 1, {"document_group": "compras"})):
            db.add(WintouchSaleRecord(enterprise_id="erp", external_id=key, store_id=store, kind="documento", document_date="2026-11-08",
                                      data=json.dumps({"document_group": "vendas", "direction": direction, "amount_cents": net * 123 // 100,
                                                       "net_cents": net, "tax_cents": net * 23 // 100, **extra})))
    data = client.get("/api/management/costs-revenues?start=2026-11-01&end=2026-11-30&group=month").json()
    assert data["revenue_source"] == "erp" and data["revenue"]["2026-11"] == 20000 - 1500
    assert data["totals"]["revenue_cents"] == 18500 and data["revenue_missing_documents"] == 0
    with factory.begin() as db:
        db.add(WintouchSaleRecord(enterprise_id="erp", external_id="no-base", store_id=store, kind="documento", document_date="2026-11-09",
                                  data=json.dumps({"document_group": "vendas", "direction": 1, "amount_cents": 1230})))
    data = client.get("/api/management/costs-revenues?start=2026-11-01&end=2026-11-30&group=month").json()
    # Documento sem base tributável: fica fora dos proveitos (nunca se misturam valores com e sem IVA) e é assinalado.
    assert data["revenue"]["2026-11"] == 18500 and data["revenue_missing_documents"] == 1 and "sem base tributável" in data["notes"][0]
