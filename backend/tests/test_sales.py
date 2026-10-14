import json
from datetime import date

import httpx
import pytest
from app.models import Movement, Register, WintouchSaleRecord, WintouchSyncRun
from app.wintouch_sales import Reader, amount, payment_record, sales_snapshot, synchronize_sales
from app.wintouch_sync import SyncError
from sqlalchemy import func, select


def fixtures():
    common = {
        "CurrencyID": "eur",
        "DocumentSerieID": "s",
        "EntityName": "Cliente teste",
        "Date": "2026-10-01T10:00:00",
        "DeletedDate": None,
    }
    return {
        "document_types": [
            {"ID": "fr", "Code": "FR", "Class": 3},
            {"ID": "ft", "Code": "FT", "Class": 5},
            {"ID": "order", "Code": "ENC", "Class": 2},
            {"ID": "rc", "Code": "RC", "CashFlowMode": 1},
        ],
        "item_documents": [],
        "document_series": [{"ID": "s", "Code": "2026"}],
        "currencies": [{"ID": "eur", "Code": "EUR"}],
        "payment_methods": [
            {
                "ID": "cash",
                "Name": "Numerário",
                "TargetCreditAccountStateID": "paid",
                "SaftPaymentMechanism": "MO",
            },
            {"ID": "credit", "Name": "Crédito", "TargetCreditAccountStateID": "open"},
        ],
        "credit_account_states": [
            {"ID": "paid", "IsFinalStateEnabled": True},
            {"ID": "open", "IsFinalStateEnabled": False},
        ],
        "product_documents": [
            {
                **common,
                "ID": "sale",
                "DocumentTypeID": "fr",
                "DocumentNumber": 1,
                "BaseValue1": "10.00",
                "VATValue1": "2.30",
                "Payments": [{"ID": "p1", "PaymentMethodID": "cash", "Amount": "12.30"}],
            },
            {
                **common,
                "ID": "credit-sale",
                "DocumentTypeID": "ft",
                "DocumentNumber": 2,
                "BaseValue1": "20.00",
                "VATValue1": "0",
                "Payments": [{"ID": "p2", "PaymentMethodID": "credit", "Amount": "20"}],
            },
            {**common, "ID": "order1", "DocumentTypeID": "order", "DocumentNumber": 3},
        ],
        "payablesreceivables": [
            {
                "ID": "debt",
                "SourceProductDocumentMasterID": "credit-sale",
                "CreditAccountStateID": "open",
                "StateValue": "15",
                "DueDate": "2026-11-01",
            }
        ],
        "settlement_documents": [
            {**common, "ID": "receipt", "DocumentTypeID": "rc", "DocumentNumber": 1}
        ],
        "settlement_documents/receipt": {
            **common,
            "ID": "receipt",
            "DocumentTypeID": "rc",
            "DocumentNumber": 1,
            "Payments": [{"ID": "p3", "PaymentMethodID": "cash", "Amount": "5"}],
            "SettlementDetails": [{"SourceProductDocumentMasterID": "credit-sale"}],
        },
    }


def reader(data):
    def handler(request):
        path = request.url.path.removeprefix("/api/v1/")
        value = data[path]
        if isinstance(value, list):
            skip = int(request.url.params.get("skip", 0))
            value = value[skip : skip + 1]  # Paginação: uma página curta não termina a coleção.
        return httpx.Response(200, json=value)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Reader(client, "https://api.wintouchcloud.com/api/v1", "tenant", "never-expose")


def test_sales_payments_and_credit_are_separate():
    rows, scanned = sales_snapshot(reader(fixtures()), "2026-10-01", "2026-10-31")
    assert scanned == 3
    assert len(rows) == 5
    real = [json.loads(r["data"]) for r in rows if r["kind"] == "real"]
    assert sum(r["amount_cents"] for r in real) == 1730
    pending = [json.loads(r["data"]) for r in rows if r["kind"] == "previsto"]
    assert pending[0]["amount_cents"] == 1500
    assert pending[0]["document_group"] == "vendas"
    assert all("ENC" not in json.loads(r["data"])["document"] for r in rows)


@pytest.mark.parametrize("payment_amount", ["-12.30", "12.30"])
def test_credit_note_payment_preserves_outgoing_direction(payment_amount):
    data = fixtures()
    data["document_types"].append({"ID": "nc", "Code": "NC", "Class": 6})
    data["product_documents"][0]["DocumentTypeID"] = "nc"
    data["product_documents"][0]["Payments"][0]["Amount"] = payment_amount
    rows, _ = sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    refund = json.loads(next(row for row in rows if row["external_id"] == "payment:p1")["data"])
    assert refund["direction"] == -1
    assert refund["amount_cents"] == 1230
    assert refund["direction"] * refund["amount_cents"] == -1230


def test_negative_payment_on_incoming_document_is_an_outflow():
    data = fixtures()
    data["product_documents"][0]["Payments"][0]["Amount"] = "-12.30"
    rows, _ = sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    payment = json.loads(next(row for row in rows if row["external_id"] == "payment:p1")["data"])
    assert payment["direction"] == -1 and payment["amount_cents"] == 1230
    assert payment["source_payment_amount_cents"] == -1230
    document = json.loads(next(row for row in rows if row["external_id"] == "document:sale")["data"])
    assert document["direction"] == 1


def test_mixed_payment_lines_preserve_net_received_amount():
    data = fixtures()
    data["product_documents"][0]["Payments"] = [
        {"ID": "received", "PaymentMethodID": "cash", "Amount": "20.00"},
        {"ID": "returned", "PaymentMethodID": "cash", "Amount": "-7.70"},
    ]
    rows, _ = sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    payments = [json.loads(row["data"]) for row in rows if row["external_id"] in {"payment:received", "payment:returned"}]
    assert sum(row["direction"] * row["amount_cents"] for row in payments) == 1230


def test_negative_receipt_payment_is_an_outflow():
    data = fixtures()
    data["settlement_documents/receipt"]["Payments"][0]["Amount"] = "-5"
    rows, _ = sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    payment = json.loads(next(row for row in rows if row["external_id"] == "payment:p3")["data"])
    assert payment["direction"] == -1 and payment["amount_cents"] == 500


def cash_fixtures():
    data = fixtures()
    data["document_types"].extend([
        {"ID": "cash-in", "Code": "ENTRADA", "Class": 21, "CashFlowMode": 1},
        {"ID": "cash-out", "Code": "RETIRADA", "Class": 22, "CashFlowMode": -1},
        {"ID": "cash-open", "Code": "ABRECAIXA", "Class": 24, "CashFlowMode": 0},
    ])
    base = {"CurrencyID": "eur", "DocumentSerieID": "s", "Date": "2026-10-12T00:00:00", "DocumentNumber": 1, "AccountID": "cash-account", "WorkShift": 1}
    data["item_documents"] = [
        {**base, "ID": "out", "DocumentTypeID": "cash-out", "ItemDocumentDetails": [{"Name": "Despesa", "Item": {"Name": "Material de limpeza"}}], "Payments": [{"ID": "cash-out-payment", "PaymentMethodID": "cash", "Amount": "5"}]},
        {**base, "ID": "in", "DocumentTypeID": "cash-in", "Payments": [{"ID": "cash-in-payment", "PaymentMethodID": "cash", "Amount": "40"}]},
        {**base, "ID": "opening", "DocumentTypeID": "cash-open", "PettyCash": "100"},
    ]
    return data


def test_manual_cash_in_and_out_are_imported_without_opening_balance():
    rows, _ = sales_snapshot(reader(cash_fixtures()), "2026-10-01", "2026-10-31")
    movements = [json.loads(row["data"]) for row in rows if json.loads(row["data"]).get("source_kind") == "cash_movement"]
    assert len(movements) == 2
    assert sum(row["amount_cents"] * row["direction"] for row in movements) == 3500
    assert all(row["document_group"] == "caixa" for row in movements)
    withdrawal = next(row for row in movements if row["direction"] == -1)
    assert withdrawal["amount_cents"] == 500 and withdrawal["account_id"] == "cash-account"
    assert withdrawal["category_name"] == "Material de limpeza"
    assert next(row for row in movements if row["direction"] == 1)["category_name"] == "Fundo de maneio"


def test_cancelled_and_out_of_period_cash_movements_are_excluded():
    data = cash_fixtures()
    data["item_documents"][0]["DeletedDate"] = "2026-10-12"
    data["item_documents"][1]["Date"] = "2026-09-30"
    rows, _ = sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    assert not any(json.loads(row["data"]).get("source_kind") == "cash_movement" for row in rows)


def test_cash_movement_with_unknown_direction_or_missing_payments_fails():
    data = cash_fixtures()
    data["document_types"][-2]["CashFlowMode"] = 1
    with pytest.raises(SyncError):
        sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    data = cash_fixtures()
    data["item_documents"][0]["Payments"] = []
    with pytest.raises(SyncError):
        sales_snapshot(reader(data), "2026-10-01", "2026-10-31")


def test_cash_reimport_does_not_duplicate_payments(setup, monkeypatch):
    _, ids, factory = setup
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    data = cash_fixtures()
    monkeypatch.setattr("app.wintouch_sales.Reader", lambda *args: reader(data))
    args = (factory.kw["bind"], "tenant", store, date(2026, 10, 1), date(2026, 10, 31), "https://api.wintouchcloud.com/api/v1", "test-key")
    assert synchronize_sales(*args)["status"] == "concluida"
    second = synchronize_sales(*args)
    assert second["status"] == "concluida" and second["inserted_count"] == 0
    with factory() as db:
        payments = list(db.scalars(select(WintouchSaleRecord).where(WintouchSaleRecord.external_id.in_(["payment:cash-out-payment", "payment:cash-in-payment"]))))
        assert len(payments) == 2
        assert all(row.active for row in payments)
        assert db.scalar(select(func.count()).select_from(Movement)) == 0


def test_outstanding_credit_from_document_before_import_period():
    data = fixtures()
    old = {**data["product_documents"][1], "ID": "old-credit", "Date": "2026-05-01T10:00:00"}
    data["product_documents"].append(old)
    data["product_documents/old-credit"] = old
    data["payablesreceivables"].append({
        "ID": "old-debt", "SourceProductDocumentMasterID": "old-credit",
        "CreditAccountStateID": "open", "StateValue": "7.50", "DueDate": "2026-06-01",
    })
    rows, _ = sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    pending = next(r for r in rows if r["external_id"] == "pending:old-debt")
    assert pending["kind"] == "previsto"
    assert pending["document_date"] == "2026-05-01"
    assert json.loads(pending["data"])["amount_cents"] == 750
    assert not any(r["external_id"] == "document:old-credit" for r in rows)


@pytest.mark.parametrize("document_class,direction", [(32, -1), (34, -1), (33, 1), (35, 1)])
def test_supplier_pending_balances(document_class, direction):
    data = fixtures()
    data["document_types"].append({"ID": "purchase", "Code": "COMPRA", "Class": document_class, "CashFlowMode": direction})
    data["product_documents/purchase"] = {
        **data["product_documents"][1], "ID": "purchase", "DocumentTypeID": "purchase",
        "Date": "2026-04-22T00:00:00", "EntityName": "Fornecedor teste",
    }
    data["payablesreceivables"].append({
        "ID": "supplier-debt", "SourceProductDocumentMasterID": "purchase",
        "CreditAccountStateID": "open", "StateValue": "469.10", "DueDate": "2026-05-22",
    })
    rows, _ = sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    pending = json.loads(next(r for r in rows if r["external_id"] == "pending:supplier-debt")["data"])
    assert pending["direction"] == direction
    assert pending["amount_cents"] == 46910
    assert pending["entity"] == "Fornecedor teste"
    assert pending["document_group"] == "compras"


def test_unknown_payment_classification_and_foreign_currency_fail():
    data = fixtures()
    data["payment_methods"][0]["TargetCreditAccountStateID"] = "unknown"
    with pytest.raises(SyncError):
        sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    data = fixtures()
    data["currencies"][0]["Code"] = "USD"
    with pytest.raises(SyncError):
        sales_snapshot(reader(data), "2026-10-01", "2026-10-31")
    for value in ["NaN", "Infinity", "1.001", None, True]:
        with pytest.raises(SyncError):
            amount(value)


def test_validated_payment_types_without_target_state():
    common = {"occurred_at": "2026-10-01T10:00:00"}
    payment = {"PaymentMethodID": "m", "Amount": "10"}
    for typ, mechanism in [(0, "NU"), (2, "TB"), (3, "CD"), (5, "CC"), (7, "CH"), (9, "TR")]:
        method = {
            "Name": "Meio validado",
            "Code": "M",
            "PaymentMethodType": typ,
            "SaftPaymentMechanism": mechanism,
        }
        assert payment_record(payment, common, {"m": method}, {})["amount_cents"] == 1000
    for typ, code in [(4, "CONTACORRENTE"), (8, "CONSUMOINTERNO"), (0, "DESCONTO")]:
        method = {"PaymentMethodType": typ, "Code": code, "SaftPaymentMechanism": "NU"}
        assert payment_record(payment, common, {"m": method}, {}) is None
    with pytest.raises(SyncError):
        payment_record(payment, common, {"m": {"PaymentMethodType": 99}}, {})


def test_reimport_updates_and_failure_rolls_back(setup, monkeypatch):
    _, ids, factory = setup
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    data = fixtures()
    monkeypatch.setattr("app.wintouch_sales.Reader", lambda *args: reader(data))
    args = (
        factory.kw["bind"],
        "tenant",
        store,
        date(2026, 10, 1),
        date(2026, 10, 31),
        "https://api.wintouchcloud.com/api/v1",
        "secret",
    )
    first = synchronize_sales(*args)
    assert first["status"] == "concluida", first
    assert first["inserted_count"] == 5
    second = synchronize_sales(*args)
    assert second["inserted_count"] == 0 and second["updated_count"] == 5
    data["payablesreceivables"][0]["StateValue"] = "10"
    third = synchronize_sales(*args)
    assert third["status"] == "concluida"
    data["product_documents"][0]["Payments"][0]["Amount"] = "bad"
    assert synchronize_sales(*args)["status"] == "falhou"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WintouchSaleRecord)) == 5
        assert db.scalar(select(func.count()).select_from(Movement)) == 0
        pending = db.scalar(select(WintouchSaleRecord).where(WintouchSaleRecord.kind == "previsto"))
        assert json.loads(pending.data)["amount_cents"] == 1000
        failed = db.scalar(select(WintouchSyncRun).where(WintouchSyncRun.status == "falhou"))
        assert failed.inserted_count == failed.updated_count == 0
        assert "secret" not in failed.error_message


def test_settled_credit_disappears_and_reader_cannot_import(setup, monkeypatch):
    c, ids, factory = setup
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    data = fixtures()
    monkeypatch.setattr("app.wintouch_sales.Reader", lambda *args: reader(data))
    args = (
        factory.kw["bind"],
        "tenant",
        store,
        date(2026, 10, 1),
        date(2026, 10, 31),
        "https://api.wintouchcloud.com/api/v1",
        "secret",
    )
    assert synchronize_sales(*args)["status"] == "concluida"
    data["payablesreceivables"][0]["CreditAccountStateID"] = "paid"
    assert synchronize_sales(*args)["status"] == "concluida"
    assert not any(
        r["kind"] == "previsto" for r in c.get("/api/integrations/wintouch/sales").json()
    )
    c.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert c.get("/api/integrations/wintouch/sales").status_code == 200
    assert (
        c.post(
            "/api/integrations/wintouch/sales",
            json={"store_id": store, "start_date": "2026-10-01", "end_date": "2026-10-31"},
        ).status_code
        == 403
    )


def test_reconciled_receipt_cannot_shrink_or_disappear(setup, monkeypatch):
    from app.models import BankAccount, BankTransaction
    c, ids, factory = setup
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    data = fixtures()
    data["payment_methods"][0]["SaftPaymentMechanism"] = "CD"
    monkeypatch.setattr("app.wintouch_sales.Reader", lambda *args: reader(data))
    args = (factory.kw["bind"], "tenant", store, date(2026, 10, 1), date(2026, 10, 31), "https://api.wintouchcloud.com/api/v1", "secret")
    assert synchronize_sales(*args)["status"] == "concluida"
    with factory.begin() as db:
        sale = db.scalar(select(WintouchSaleRecord).where(WintouchSaleRecord.external_id == "payment:p1"))
        account = BankAccount(store_id=store, name="Cartões", bank="BPI", iban_last4="1234")
        db.add(account); db.flush()
        bank = BankTransaction(account_id=account.id, movement_date="2026-10-01", value_date="2026-10-01", description="TPA",
            amount_cents=1230, balance_cents=1230, fingerprint="card", source_account="", exported_at="", source_position=1)
        db.add(bank); db.flush()
        sale_id, bank_id = sale.id, bank.id
    result = c.post("/api/reconciliation/confirm", json={"kind": "cartao", "lines": [
        {"source": "bank", "source_id": bank_id, "amount_cents": 1230},
        {"source": "wintouch", "source_id": sale_id, "amount_cents": 1230}]})
    assert result.status_code == 200, result.text
    assert synchronize_sales(*args)["status"] == "concluida"
    data["product_documents"][0]["Payments"][0]["Amount"] = "10"
    assert synchronize_sales(*args)["status"] == "falhou"
    with factory() as db:
        assert json.loads(db.get(WintouchSaleRecord, sale_id).data)["amount_cents"] == 1230
    data["product_documents"][0]["Payments"] = []
    assert synchronize_sales(*args)["status"] == "falhou"
    assert c.post(f'/api/reconciliation/{result.json()["id"]}/undo', json={"reason": "Pagamento corrigido na origem"}).status_code == 200
    assert synchronize_sales(*args)["status"] == "concluida"
