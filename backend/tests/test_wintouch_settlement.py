import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from app.wintouch_settlement import payload, encode_payload, rejection_message


class ReadOnlySource:
    def collection(self, resource, embed=None):
        return iter({
            "document_types": [{"ID": "receipt", "Class": 18, "CashFlowMode": 1}],
            "document_series": [{"ID": "series", "DocumentTypeID": "receipt", "Code": "2026"}],
            "credit_account_states": [{"ID": "paid", "IsFinalStateEnabled": True}],
            "payment_methods": [{"ID": "cash", "Name": "Numerário", "TargetCreditAccountStateID": "paid"}],
            "currencies": [{"ID": "eur", "Code": "EUR"}],
        }[resource])

    def get(self, resource):
        assert resource == "product_documents/invoice"
        return {"ID": "invoice", "CurrencyID": "eur", "SectorID": "sector"}


@pytest.mark.parametrize("body,expected", [
    ([{"ErrorMessage": "Falta a série"}], "Falta a série"),
    ({"errors": {"Payments": ["Falta a conta"]}}, "Falta a conta"),
    ({"detail": [{"loc": ["body", "Date"], "msg": "Data inválida"}]}, "Data inválida"),
    ({"Message": "Recusado", "ModelState": {"Serie": ["Série inválida"]}}, "Série inválida"),
])
def test_nested_rejection_message(body, expected):
    import httpx
    assert expected in rejection_message(httpx.Response(422, json=body))


def inputs():
    pending = {"StateValue": "12.30", "EntityID": "entity", "SourceProductDocumentMasterID": "invoice",
               "CreditAccountStateID": "open", "DueDate": "2026-10-11", "Installment": 1, "Version": "v1"}
    data = SimpleNamespace(document_type_id="receipt", document_serie_id="series", payment_method_id="cash",
                           amount="2.30", occurred_at=datetime.now(timezone.utc), notes="Pagamento parcial")
    return pending, data


def test_cheque_requires_number_and_sends_it():
    class ChequeSource(ReadOnlySource):
        def collection(self, resource, embed=None):
            if resource == "payment_methods":
                return iter([{"ID": "cheque", "Name": "Cheque", "PaymentMethodType": 7,
                              "SaftPaymentMechanism": "CH", "TargetCreditAccountStateID": "paid"}])
            return super().collection(resource, embed)
    pending, data = inputs()
    data.payment_method_id = "cheque"
    with pytest.raises(HTTPException, match="número do cheque"):
        payload(ChequeSource(), pending, data, 1, "operation")
    data.payment_number = " 123456 "
    result = payload(ChequeSource(), pending, data, 1, "operation")
    assert result["Payments"][0]["PaymentMethodNumber"] == "123456"


def test_settlement_uses_current_balance_and_exact_json():
    pending, data = inputs()
    result = payload(ReadOnlySource(), pending, data, 1, "operation")
    decoded = json.loads(encode_payload(result), parse_float=Decimal)
    assert decoded["Amount"] == Decimal("2.30")
    assert decoded["SettlementDetails"][0]["PreviousStateValue"] == Decimal("12.30")
    assert decoded["SettlementDetails"][0]["Version"] == "v1"
    assert decoded["Payments"][0]["Amount"] == decoded["Amount"]


@pytest.mark.parametrize("field,value", [("amount", "12.31"), ("amount", "0"),
    ("amount", "-1"), ("document_serie_id", "other-series"), ("payment_method_id", "credit")])
def test_invalid_settlement_rejected_before_write(field, value):
    pending, data = inputs()
    setattr(data, field, value)
    with pytest.raises(HTTPException) as error:
        payload(ReadOnlySource(), pending, data, 1, "operation")
    assert error.value.status_code == 422


@pytest.mark.parametrize("timeout", [False, True, "rejected", "assigned_id", "location"])
def test_remote_submission_not_duplicated(setup, monkeypatch, timeout):
    import httpx
    from uuid import uuid4
    from app import main
    from app.models import WintouchSaleRecord, Register
    c, ids, factory = setup
    with factory.begin() as db:
        store_id = db.get(Register, ids["registers"][0]).store_id
        row = WintouchSaleRecord(enterprise_id="tenant", store_id=store_id,
            external_id="pending:debt", kind="previsto", active=True,
            document_date="2026-10-11", data=json.dumps({"direction": 1, "amount_cents": 1230}))
        db.add(row)
        db.flush()
        record_id = row.id
    monkeypatch.setattr(main, "settlement_context", lambda db, user, key:
        (db.get(WintouchSaleRecord, key), "https://example.test/api", "test-key"))
    operation = str(uuid4())
    assigned = str(uuid4()) if timeout in ("assigned_id", "location") else operation
    writes = []
    pending, _ = inputs()
    pending.update(ID="debt", CreditAccountState={"IsFinalStateEnabled": False})

    def handler(request):
        resource = request.url.path.removeprefix("/api/")
        if request.method == "POST":
            writes.append(json.loads(request.content))
            if timeout == "rejected":
                return httpx.Response(400, json={"Message": "Série indisponível"})
            if timeout is True:
                raise httpx.ReadTimeout("Test timeout", request=request)
            if timeout == "location":
                return httpx.Response(201, headers={"Location": "https://example.test/api/settlement_documents/" + assigned})
            return httpx.Response(201, json={"ID": assigned})
        if resource == "payablesreceivables/debt":
            return httpx.Response(200, json={**pending, "StateValue": "10.00" if writes else "12.30"})
        if resource == "settlement_documents/" + assigned:
            return httpx.Response(200, json={"ID": assigned, "Amount": "2.30"})
        if resource == "payablesreceivables":
            return httpx.Response(200, json=[] if int(request.url.params.get("skip", 0)) else
                                  [{**pending, "StateValue": "10.00" if writes else "12.30"}])
        source = ReadOnlySource()
        if resource == "product_documents/invoice":
            return httpx.Response(200, json=source.get(resource))
        values = list(source.collection(resource))
        return httpx.Response(200, json=[] if int(request.url.params.get("skip", 0)) else values)

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler)))
    body = {"document_type_id": "receipt", "document_serie_id": "series", "payment_method_id": "cash",
            "amount": "2.30", "occurred_at": datetime.now(timezone.utc).isoformat(), "request_key": operation}
    url = f"/api/integrations/wintouch/sales/{record_id}/settle"
    first = c.post(url, json=body)
    assert first.status_code == (422 if timeout == "rejected" else 502 if timeout is True else 200), first.text
    second = c.post(url, json=body)
    assert second.status_code == (422 if timeout == "rejected" else 409 if timeout is True else 200), second.text
    assert len(writes) == (2 if timeout == "rejected" else 1)
    if timeout == "rejected":
        assert "Série indisponível" in second.json()["detail"]
