"""Connector boundaries without network calls or database changes."""

from datetime import date

import pytest
from app.erp_documents import snapshot
from app.integrations.erp import ERPIntegrationError, ERPRegistry, PurchaseDocument
from app.wintouch_sync import SyncError
from pydantic import ValidationError

START, END = date(2026, 10, 1), date(2026, 10, 31)


def document(**changes):
    return PurchaseDocument(
        **{
            "external_id": "1",
            "supplier_nif": "510774318",
            "supplier_name": "Fornecedor",
            "document": "FT A/1",
            "internal_document": "COMPRA A/1",
            "document_type": "FT",
            "document_date": "2026-10-10",
            "amount_cents": 12300,
            **changes,
        }
    )


class Adapter:
    def __init__(self, documents):
        self.documents = documents

    def purchase_documents(self, start, end):
        assert (start, end) == (START, END)
        return iter(self.documents)


def test_providers_with_same_external_id_are_dispatched_independently():
    registry = ERPRegistry()
    registry.register("erp_a", lambda: Adapter([document()]))
    registry.register("erp_b", lambda: Adapter([document(amount_cents=-12300)]))
    assert registry.purchase_documents("erp_a", START, END)[0].amount_cents == 12300
    assert registry.purchase_documents("erp_b", START, END)[0].amount_cents == -12300
    with pytest.raises(ERPIntegrationError):
        registry.purchase_documents("unknown", START, END)
    with pytest.raises(ERPIntegrationError):
        registry.register("erp_a", lambda: Adapter([]))


def test_each_read_creates_a_fresh_connection_adapter():
    instances = []

    def factory():
        adapter = Adapter([])
        instances.append(adapter)
        return adapter

    registry = ERPRegistry()
    registry.register("test", factory)
    registry.purchase_documents("test", START, END)
    registry.purchase_documents("test", START, END)
    assert len(instances) == 2 and instances[0] is not instances[1]


@pytest.mark.parametrize(
    "rows",
    [
        [document(), document()],
        [document(document_date="2026-11-01")],
        [{"external_id": "raw-vendor-data"}],
    ],
)
def test_invalid_snapshot_never_returns_partial_results(rows):
    registry = ERPRegistry()
    registry.register("test", lambda: Adapter(rows))
    with pytest.raises(ERPIntegrationError):
        registry.purchase_documents("test", START, END)


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_cents": 12.3},
        {"amount_cents": True},
        {"amount_cents": 2**63},
        {"document_date": "20261010"},
        {"document_date": "2026-02-30"},
        {"external_id": " "},
        {"supplier_nif": "1" * 31},
    ],
)
def test_contract_rejects_invalid_dates_money_and_identifiers(changes):
    with pytest.raises(ValidationError):
        document(**changes)


def test_reversed_period_does_not_open_a_connector():
    registry = ERPRegistry()

    def unopened():
        pytest.fail("Invalid range must be rejected before connecting")

    registry.register("test", unopened)
    with pytest.raises(ERPIntegrationError):
        registry.purchase_documents("test", END, START)


class WintouchReader:
    def __init__(self, **changes):
        self.changes = changes

    def collection(self, resource, embed=None):
        return {
            "document_types": [{"ID": "purchase", "Class": 32, "Code": "COMPRA"}],
            "document_series": [{"ID": "series", "Code": "A"}],
            "currencies": [{"ID": "eur", "Code": "EUR"}],
            "product_documents": [
                {
                    "ID": "invoice",
                    "DocumentTypeID": "purchase",
                    "DocumentSerieID": "series",
                    "DocumentNumber": 1,
                    "CurrencyID": "eur",
                    "Date": "2026-10-10",
                    "ExternalDocument": "FT A/1",
                    "VATNumber": "PT510774318",
                    "EntityName": "Fornecedor",
                    "NetValueTotal": "123.00",
                    **self.changes,
                }
            ],
        }[resource]


def test_existing_wintouch_entry_point_uses_common_contract():
    rows = snapshot(WintouchReader(), START.isoformat(), END.isoformat())
    assert len(rows) == 1
    assert rows[0]["external_id"] == "invoice"
    assert rows[0]["supplier_nif"] == "510774318"
    assert rows[0]["amount_cents"] == 12300
    assert rows[0]["cancelled"] is False
    credit = snapshot(WintouchReader(ExternalDocument="NC A/1"), START.isoformat(), END.isoformat())
    assert credit[0]["amount_cents"] == -12300


def test_wintouch_rejects_unsupported_currency():
    with pytest.raises(SyncError):
        snapshot(WintouchReader(CurrencyID="usd"), START.isoformat(), END.isoformat())


def test_validation_errors_do_not_include_supplier_payload():
    with pytest.raises(SyncError) as error:
        snapshot(WintouchReader(VATNumber="private" * 10), START.isoformat(), END.isoformat())
    assert "private" not in str(error.value)
