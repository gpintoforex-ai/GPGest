"""ERP-origin payments must remain true even before bank reconciliation."""

from datetime import date

from app.models import Purchase, Register, WintouchSaleRecord
from app.wintouch_sales import synchronize_sales
from sqlalchemy import select
from test_sales import fixtures, reader


def test_distributed_wintouch_payment_cannot_shrink_or_disappear_before_bank(setup, monkeypatch):
    client, ids, factory = setup
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    data = fixtures()
    data["payment_methods"][0]["SaftPaymentMechanism"] = "TB"
    monkeypatch.setattr("app.wintouch_sales.Reader", lambda *args: reader(data))
    args = (
        factory.kw["bind"],
        "tenant",
        store,
        date(2026, 10, 1),
        date(2026, 10, 31),
        "https://api.wintouchcloud.com/api/v1",
        "test-key",
    )
    assert synchronize_sales(*args)["status"] == "concluida"
    with factory.begin() as db:
        source = db.scalar(
            select(WintouchSaleRecord).where(WintouchSaleRecord.external_id == "payment:p1")
        )
        document = Purchase(
            store_id=store,
            supplier_nif="500000000",
            supplier_name="Fornecedor",
            document="NC A/1",
            document_type="NC",
            document_date="2026-10-01",
            amount_cents=-1230,
            origin="manual",
        )
        db.add(document)
        db.flush()
        doc_id, source_id = document.id, source.id
    result = client.post(
        "/api/document-payments",
        json={
            "store_id": store,
            "payment_date": "2026-10-01",
            "amount_cents": 1230,
            "direction": 1,
            "method": "transferencia",
            "source": "wintouch",
            "source_id": source_id,
            "request_key": "credit-refund",
            "lines": [{"purchase_id": doc_id, "amount_cents": 1230}],
        },
    )
    assert result.status_code == 200, result.text
    assert client.get(f"/api/documents/{doc_id}/detail").json()["bank_status"] == "pendente"
    data["product_documents"][0]["Payments"][0]["Amount"] = "10"
    assert synchronize_sales(*args)["status"] == "falhou"
    data["product_documents"][0]["Payments"] = []
    assert synchronize_sales(*args)["status"] == "falhou"
    document = client.get(f"/api/documents/{doc_id}/detail").json()
    assert document["payment_status"] == "pago" and document["paid_cents"] == 1230
    allocation = document["payments"][0]
    assert (
        client.post(
            f"/api/document-payments/{allocation['id']}/allocations/{allocation['allocation_id']}/remove",
            json={"reason": "Anulação do recebimento na origem"},
        ).status_code
        == 200
    )
    assert synchronize_sales(*args)["status"] == "concluida"
    assert client.get(f"/api/documents/{doc_id}/detail").json()["payment_status"] == "por_pagar"
