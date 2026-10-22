import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from app.deposits import dossier_files
from app.models import (
    AccountingPeriod,
    BankAccount,
    BankDeposit,
    BankTransaction,
    DepositCredit,
    DocumentPayment,
    Register,
)
from app.reconciliation import used
from conftest import movement, open_cash
from sqlalchemy import select


def today():
    return datetime.now(ZoneInfo("Europe/Lisbon")).date().isoformat()


@pytest.fixture
def deposits(setup):
    client, ids, factory = setup
    session = open_cash(client, ids["registers"][0], "100").json()["id"]
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        account = BankAccount(
            store_id=store, name="Conta Demo", bank="Banco Demo", iban_last4="1234"
        )
        db.add(account)
        db.flush()
        account_id = account.id
        receipt = DocumentPayment(
            store_id=store,
            payment_date=today(),
            amount_cents=2000,
            direction=1,
            entity="Cliente Demo",
            reference="CHEQUE-1",
            method="cheque",
            source="manual",
            request_key=uuid4().hex,
        )
        db.add(receipt)
        db.flush()
        receipt_id = receipt.id
    return client, ids, factory, session, account_id, receipt_id


def create(fixture, cash="10", cheque=False, **changes):
    client, ids, _, _, account, receipt = fixture
    payload = {
        "register_id": ids["registers"][0],
        "account_id": account,
        "preparation_date": today(),
        "cash_amount": cash,
        "request_key": uuid4().hex,
        "cheques": [
            {
                "number": "123",
                "bank": "Banco Emitente",
                "amount": "20",
                "cheque_date": today(),
                "receipt_id": receipt,
            }
        ]
        if cheque
        else [],
    }
    payload.update(changes)
    response = client.post("/api/deposits", json=payload)
    assert response.status_code == 200, response.text
    return response.json(), payload


def command(client, deposit, action, **changes):
    payload = {"request_key": uuid4().hex, "action_date": today()}
    payload.update(changes)
    return client.post(f"/api/deposits/{deposit['id']}/{action}", json=payload)


def delivered(fixture, cash="10", cheque=False):
    client = fixture[0]
    deposit, _ = create(fixture, cash, cheque)
    assert command(client, deposit, "prepare").status_code == 200
    response = command(client, deposit, "exit")
    assert response.status_code == 200, response.text
    assert command(client, deposit, "deliver").status_code == 200
    return deposit


def statement(fixture, amount, description="DEP DEMO"):
    _, _, factory, _, account, _ = fixture
    with factory.begin() as db:
        row = BankTransaction(
            account_id=account,
            movement_date=today(),
            value_date=today(),
            description=description,
            amount_cents=amount,
            balance_cents=amount,
            fingerprint=uuid4().hex,
            source_account="Demo",
            exported_at=today() + "T12:00:00",
            source_position=1,
        )
        db.add(row)
        db.flush()
        return row.id


@pytest.mark.parametrize(
    "cash,cheque,total,expected",
    [("10", False, "10.00", 9000), ("0", True, "20.00", 10000), ("10", True, "30.00", 9000)],
)
def test_cash_cheques_mixed_only_one_exit_and_no_bank_revenue(
    deposits, cash, cheque, total, expected
):
    client, _, factory, session, _, _ = deposits
    deposit, payload = create(deposits, cash, cheque)
    assert deposit["total"] == total and deposit["status"] == "rascunho"
    assert client.post("/api/deposits", json=payload).json()["id"] == deposit["id"]
    assert client.get("/api/movements").json() == []
    assert command(client, deposit, "prepare").status_code == 200
    assert client.get("/api/movements").json() == []
    result = command(client, deposit, "exit")
    assert result.status_code == 200, result.text
    assert command(client, deposit, "exit").json()["movement_id"] == result.json()["movement_id"]
    rows = client.get("/api/movements").json()
    assert (
        len(rows) == 1
        and rows[0]["kind"] == "deposito"
        and rows[0]["amount_cents"] == int(Decimal(total) * 100)
    )
    sheet = next(s for s in client.get("/api/sessions").json() if s["id"] == session)
    assert sheet["balance_cents"] == expected
    assert next(r for r in sheet["sheet"]["rows"] if r["key"] == "numerario")[
        "debits_cents"
    ] == int(Decimal(cash) * 100)
    with factory() as db:
        assert list(db.scalars(select(BankTransaction))) == []


def test_count_details_replace_global_amount(deposits):
    deposit, _ = create(deposits, "999", cash_mode="denominations", counts={"500": 2, "1": 3})
    assert deposit["cash_amount"] == "10.03"


def test_real_sources_replace_manual_values_and_link_cash_sheet(deposits):
    client, ids, _, session, _, _ = deposits
    assert movement(client, ids, session, kind="venda", amount="100").status_code == 200
    deposit, payload = create(deposits, "9999", from_real_movements=True)
    assert deposit["cash_amount"] == "100.00" and deposit["cheque_amount"] == "0.00"
    assert deposit["details"]["session_id"] == session
    assert deposit["details"]["sources"][0]["source"] == "movement"
    assert client.post("/api/deposits", json=payload).json()["id"] == deposit["id"]
    assert command(client, deposit, "prepare").status_code == 200
    assert movement(client, ids, session, kind="venda", amount="5").status_code == 200
    assert command(client, deposit, "exit").status_code == 409
    refreshed = client.post(f"/api/deposits/{deposit['id']}/save", json=payload)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["cash_amount"] == "105.00"
    assert len(refreshed.json()["details"]["sources"]) == 2
    assert client.get(f"/api/deposits/{deposit['id']}/pdf").status_code == 200
    assert command(client, deposit, "prepare").status_code == 200
    assert command(client, deposit, "exit").status_code == 200
    assert client.post("/api/deposits", json=payload).json()["id"] == deposit["id"]


def test_partial_real_cash_deposit_preserves_remaining_balance(deposits):
    client, ids, _, session, _, _ = deposits
    assert movement(client, ids, session, kind="venda", amount="100").status_code == 200
    deposit, payload = create(deposits, "60", from_real_movements=True, use_cash_amount=True)
    assert deposit["cash_amount"] == "60.00"
    excessive = {**payload, "cash_amount": "101"}
    assert client.post(f"/api/deposits/{deposit['id']}/save", json=excessive).status_code == 422
    assert command(client, deposit, "prepare").status_code == 200
    assert command(client, deposit, "exit").status_code == 200
    balance = client.get(f"/api/registers/{ids['registers'][0]}/deposit-balance?selected_date={today()}")
    assert balance.status_code == 200 and Decimal(balance.json()["cash_amount"]) == 40


def test_available_cash_suggestion_uses_opening_and_confirmed_deposits(deposits):
    client, ids, _, session, _, _ = deposits
    assert movement(client, ids, session, kind="venda", amount="100").status_code == 200
    path = f"/api/registers/{ids['registers'][0]}/deposit-balance?selected_date={today()}"
    assert client.get(path).json()["cash_amount"] == "100"
    assert (
        movement(client, ids, session, kind="venda", amount="200", payment="cartao").status_code
        == 200
    )
    assert client.get(path).json()["cash_amount"] == "100"
    delivered(deposits, "10")
    assert client.get(path).json()["cash_amount"] == "90"
    assert client.get(path.replace(today(), "2000-01-01")).json()["cash_amount"] == "0"


def test_cash_balance_explains_the_source_of_negative_movements(deposits):
    client, ids, _, session, _, _ = deposits
    response = client.post(
        "/api/movements",
        json={
            "session_id": session,
            "category_id": ids["category"],
            "kind": "fornecedor",
            "payment": "numerario",
            "amount": "5",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "entity": "Fornecedor Demo",
            "document": f"Fecho de caixa {today()}",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text

    balance = client.get(
        f"/api/registers/{ids['registers'][0]}/deposit-balance?selected_date={today()}"
    )
    assert balance.status_code == 200, balance.text
    row = next(item for item in balance.json()["movements"] if item["source"] == "movement")
    assert row["amount"] == "-5"
    assert row["description"] == "Saída · Pagamento a fornecedor · Geral · Fornecedor Demo"


def test_deposit_uses_accumulated_erp_without_any_cash_sheet(deposits):
    from app.models import CashSession, WintouchCashAccount, WintouchSaleRecord

    client, ids, factory, session, _, _ = deposits
    register_id = ids["registers"][1]
    with factory.begin() as db:
        store = db.get(Register, register_id).store_id
        db.add(
            WintouchCashAccount(
                enterprise_id="demo",
                external_id="POS2",
                name="POS Demo",
                store_id=store,
                register_id=register_id,
            )
        )
        for index, (stamp, direction, value) in enumerate(
            [("2026-01-01", 1, 4000), (today(), -1, 500), ("2099-01-01", 1, 10000), (today(), -1, 6400), ("2026-01-01", 1, 4000), (today(), 1, 4000)]
        ):
            db.add(
                WintouchSaleRecord(
                    enterprise_id="demo",
                    external_id=f"real-{index}",
                    store_id=store,
                    kind="real",
                    document_date=stamp,
                    active=True,
                    data=json.dumps(
                        {
                            "occurred_at": stamp + "T12:00:00",
                            "account_id": "POS2",
                            "payment_code": "NU",
                            "amount_cents": value,
                            "direction": direction,
                            "document": f"REAL-{index}",
                            "category_name": "Valor do turno" if index == 3 else "Fundo de maneio" if index >= 4 else "Despesa",
                            "source_kind": "cash_movement" if index >= 4 else "payment",
                        }
                    ),
                )
            )
    balance = client.get(f"/api/registers/{register_id}/deposit-balance?selected_date={today()}")
    assert balance.status_code == 200 and balance.json()["cash_amount"] == "35"
    imported = client.get("/api/integrations/wintouch/sales").json()
    assert all(row.get("category_name") != "Valor do turno" for row in imported)
    assert all(row.get("category_name") != "Fundo de maneio" for row in imported)
    from app.cash_sheet import real_receipts
    with factory() as db:
        receipts = list(real_receipts(db, store, today(), "demo"))
        assert len(receipts) == 1 and receipts[0]["amount_cents"] == 500
    deposit, _ = create(deposits, register_id=register_id, from_real_movements=True)
    assert deposit["cash_amount"] == "35.00" and len(deposit["details"]["sources"]) == 2
    assert command(client, deposit, "prepare").status_code == 200
    assert command(client, deposit, "exit").status_code == 200
    history = client.get("/api/movements?all_records=true").json()
    assert any(
        row["register_id"] == register_id
        and row["session_id"] is None
        and row["amount_cents"] == 3500
        for row in history
    )
    assert (
        client.get(f"/api/registers/{register_id}/deposit-balance?selected_date={today()}").json()[
            "cash_amount"
        ]
        == "0"
    )
    assert (
        command(
            client, deposit, "cancel", returned=True, reason="Valores regressaram à origem"
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/registers/{register_id}/deposit-balance?selected_date={today()}").json()[
            "cash_amount"
        ]
        == "35"
    )
    with factory() as db:
        assert not list(
            db.scalars(select(CashSession).where(CashSession.register_id == register_id))
        )


def test_unmapped_pos_uses_closed_sales_conference_as_origin(deposits):
    from app.models import CashSession, WintouchSaleRecord

    client, ids, factory, session_id, _, _ = deposits
    with factory.begin() as db:
        session = db.get(CashSession, session_id)
        store = db.get(Register, session.register_id).store_id
        session.wintouch_enterprise = "demo"
        session.wintouch_scope = store + ":" + today()
        db.add(
            WintouchSaleRecord(
                enterprise_id="demo",
                external_id="RECEIPT-CLOSED",
                store_id=store,
                kind="real",
                document_date=today(),
                active=True,
                data=json.dumps(
                    {
                        "occurred_at": today() + "T12:00:00",
                        "account_id": "UNMAPPED",
                        "payment_code": "NU",
                        "amount_cents": 2500,
                        "direction": 1,
                        "document": "SALE-REF",
                    }
                ),
            )
        )
    assert (
        client.post(
            f"/api/sessions/{session_id}/close", json={"cash_mode": "total", "cash_total": "125"}
        ).status_code
        == 200
    )
    register = ids["registers"][0]
    result = client.get(f"/api/registers/{register}/deposit-balance?selected_date={today()}")
    assert result.json()["cash_amount"] == "25" and session_id in result.json()["session_ids"]
    assert client.get("/api/integrations/wintouch/sales").json()[0]["register_id"] == register
    deposit, _ = create(deposits, from_real_movements=True)
    assert command(client, deposit, "prepare").status_code == 200
    assert command(client, deposit, "exit").status_code == 200
    assert (
        client.get(f"/api/registers/{register}/deposit-balance?selected_date={today()}").json()[
            "cash_amount"
        ]
        == "0"
    )


def test_edit_mixed_draft_keeps_decimal_cheques_and_no_duplicate_records(deposits):
    deposit, payload = create(deposits, "10", True)
    payload["cheques"] = [
        {
            key: deposit["cheques"][0][key]
            for key in ["number", "bank", "amount", "cheque_date", "issuer", "receipt_id", "notes"]
        }
    ]
    payload["cash_amount"] = "12.30"
    result = deposits[0].post(f"/api/deposits/{deposit['id']}/save", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["total"] == "32.30" and len(result.json()["cheques"]) == 1


def test_imported_cheque_receipt_can_be_selected_and_reserved(deposits):
    from app.models import WintouchSaleRecord

    client, ids, factory, _, _, _ = deposits
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        receipt = WintouchSaleRecord(
            enterprise_id="demo",
            external_id="CHEQUE-DEMO",
            store_id=store,
            kind="real",
            document_date=today(),
            active=True,
            data=json.dumps(
                {
                    "payment_code": "CH",
                    "direction": 1,
                    "amount_cents": 2000,
                    "occurred_at": today() + "T12:00:00",
                    "document": "CHEQUE-DEMO",
                }
            ),
        )
        db.add(receipt)
        db.flush()
        key = "wintouch:" + receipt.id
    assert any(r["id"] == key for r in client.get("/api/deposits/cheque-receipts").json())
    deposit, payload = create(
        deposits,
        "0",
        cheques=[
            {
                "number": "ERP-CHEQUE",
                "bank": "Banco Demo",
                "amount": "20",
                "cheque_date": today(),
                "source_receipt": key,
            }
        ],
    )
    assert deposit["cheques"][0]["source_receipt"] == key
    assert not any(r["id"] == key for r in client.get("/api/deposits/cheque-receipts").json())


def test_insufficient_cash_excludes_card_and_requires_admin_reason(deposits):
    client, ids, _, session, _, _ = deposits
    assert (
        movement(client, ids, session, kind="venda", amount="200", payment="cartao").status_code
        == 200
    )
    deposit, _ = create(deposits, "150")
    command(client, deposit, "prepare")
    assert command(client, deposit, "exit").status_code == 409
    assert command(client, deposit, "exit", allow_insufficient=True).status_code == 422
    assert (
        command(
            client,
            deposit,
            "exit",
            allow_insufficient=True,
            reason="Exceção autorizada pelo administrador",
        ).status_code
        == 200
    )


def test_duplicate_cheque_is_blocked_in_active_deposits(deposits):
    first, payload = create(deposits, "0", True)
    payload["request_key"] = uuid4().hex
    assert deposits[0].post("/api/deposits", json=payload).status_code == 409
    assert command(deposits[0], first, "cancel", reason="Preparação anulada").status_code == 200
    assert deposits[0].post("/api/deposits", json=payload).status_code == 200


def test_partial_reconciliation_multiple_credits_and_repeat(deposits):
    client, _, factory, _, _, _ = deposits
    deposit = delivered(deposits, "10", True)
    first = statement(deposits, 1000)
    second = statement(deposits, 2000)
    key = uuid4().hex
    response = command(
        client, deposit, "reconcile", transaction_id=first, amount="10", request_key=key
    )
    assert (
        response.status_code == 200 and response.json()["status"] == "parcialmente_reconciliado"
    ), response.text
    assert response.json()["remaining"] == "20.00" and response.json()["difference"] == "-20.00"
    assert (
        command(
            client, deposit, "reconcile", transaction_id=first, amount="10", request_key=key
        ).status_code
        == 200
    )
    with factory() as db:
        assert used(db, "bank", first) == 1000
        assert len(list(db.scalars(select(DepositCredit)))) == 1
    result = command(client, deposit, "reconcile", transaction_id=second, amount="20")
    assert result.status_code == 200 and result.json()["status"] == "reconciliado", result.text
    assert (
        command(client, deposit, "cancel", returned=True, reason="Valores regressaram").status_code
        == 409
    )
    assert command(client, deposit, "undo", reason="Associação incorreta").status_code == 200
    assert (
        command(client, deposit, "cancel", returned=True, reason="Valores regressaram").status_code
        == 200
    )


def test_cancellation_before_and_after_exit_preserves_history(deposits):
    client, _, _, session, _, _ = deposits
    draft, _ = create(deposits)
    assert command(client, draft, "cancel", reason="Rascunho anulado").status_code == 200
    assert client.get("/api/movements").json() == []
    deposit = delivered(deposits)
    assert command(client, deposit, "cancel", reason="Valores devolvidos").status_code == 422
    result = command(client, deposit, "cancel", reason="Valores devolvidos", returned=True)
    assert result.status_code == 200, result.text
    assert result.json()["reversal_id"] and result.json()["movement_id"]
    assert (
        command(client, deposit, "cancel", reason="Valores devolvidos", returned=True).status_code
        == 200
    )
    sheet = next(s for s in client.get("/api/sessions").json() if s["id"] == session)
    assert sheet["balance_cents"] == 10000
    rows = client.get("/api/movements").json()
    # Caixa: saída e estorno; banco (derivado do depósito): entrada e estorno.
    assert sorted(m["origin"] for m in rows) == ["deposito_banco", "deposito_banco", "deposito_modulo", "deposito_modulo"]


def test_pdf_proof_validation_and_dossier_pending(deposits):
    client, _, factory, _, _, _ = deposits
    deposit, _ = create(deposits)
    assert deposit["proof_status"] == "em_falta"
    pdf = client.get(f"/api/deposits/{deposit['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    with factory() as db:
        files = dossier_files(db, today(), today())
        assert "talao-interno.pdf" in " ".join(files)
        assert deposit["id"] in files["depositos/pendencias.json"].decode()
    endpoint = f"/api/deposits/{deposit['id']}/attachments"
    assert (
        client.post(endpoint, files={"file": ("bad.pdf", b"bad", "application/pdf")}).status_code
        == 422
    )
    result = client.post(endpoint, files={"file": ("proof.pdf", pdf.content, "application/pdf")})
    assert result.status_code == 200 and result.json()["proof_status"] == "por_validar"
    attachment = result.json()["attachments"][0]["id"]
    validated = client.post(endpoint + f"/{attachment}/validate", json={})
    assert validated.status_code == 200 and validated.json()["proof_status"] == "validado"
    with factory() as db:
        files = dossier_files(db, today(), today())
        assert json.loads(files["depositos/pendencias.json"]) == []


def test_period_lock_and_scope_permissions(deposits):
    client, ids, factory, _, _, _ = deposits
    deposit, _ = create(deposits)
    with factory.begin() as db:
        db.add(
            AccountingPeriod(
                vat_period="mensal",
                year=int(today()[:4]),
                number=int(today()[5:7]),
                start=today(),
                end=today(),
                closed_at=datetime.now(ZoneInfo("Europe/Lisbon")),
                closed_by=ids["operator"],
                reason="Teste",
            )
        )
    assert command(client, deposit, "prepare").status_code == 409
    client.post("/api/auth/logout", json={})
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert command(client, deposit, "prepare").status_code == 403
    assert client.get("/api/deposits/not-in-this-company").status_code == 404


def test_separate_bank_fees_and_returned_cheques(deposits):
    client = deposits[0]
    deposit = delivered(deposits, "0", True)
    credit = statement(deposits, 2000)
    assert (
        command(client, deposit, "reconcile", transaction_id=credit, amount="20").status_code == 200
    )
    fee = statement(deposits, -100, "Comissão")
    assert (
        command(
            client, deposit, "fee", transaction_id=fee, amount="1", reason="Comissão de depósito"
        ).status_code
        == 200
    )
    cheque = client.get(f"/api/deposits/{deposit['id']}").json()["cheques"][0]
    debit = statement(deposits, -2000, "Cheque devolvido")
    response = command(
        client,
        deposit,
        "cheque-return",
        transaction_id=debit,
        cheque_id=cheque["id"],
        amount="20",
        reason="Cheque devolvido pelo banco",
    )
    assert response.status_code == 200 and response.json()["cheques"][0]["returned_at"], (
        response.text
    )
    assert len(response.json()["credits"]) == 3
    assert response.json()["fees"] == "1.00"
    assert response.json()["returned_amount"] == "20.00"
    assert response.json()["bank_net"] == "-1.00"


def test_bank_credit_cannot_be_used_twice_by_existing_reconciliation(deposits):
    from app.models import Reconciliation, ReconciliationLine

    client, _, factory, _, _, _ = deposits
    deposit = delivered(deposits)
    bank_id = statement(deposits, 1000)
    with factory.begin() as db:
        store_id = db.scalar(select(BankDeposit.store_id).where(BankDeposit.id == deposit["id"]))
        reconciliation = Reconciliation(
            kind="transferencia",
            store_id=store_id,
            reason="Já conciliado",
            commission_cents=0,
            difference_cents=0,
        )
        db.add(reconciliation)
        db.flush()
        db.add(
            ReconciliationLine(
                reconciliation_id=reconciliation.id,
                source="bank",
                source_id=bank_id,
                amount_cents=1000,
            )
        )
    assert (
        command(client, deposit, "reconcile", transaction_id=bank_id, amount="10").status_code
        == 409
    )


def test_company_schema_isolation_blocks_detail_actions_pdf_and_proof(deposits):
    from app.db import db_session, engine
    from app.main import app
    from app.models import Base, User
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    client, _, factory, _, _, _ = deposits
    deposit, _ = create(deposits)
    other_schema = "test_" + uuid4().hex
    other_engine = None
    previous = app.dependency_overrides[db_session]
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA " + other_schema))
        other_engine = create_engine(
            engine.url, connect_args={"options": "-csearch_path=" + other_schema}
        )
        Base.metadata.create_all(other_engine)
        other_factory = sessionmaker(other_engine, expire_on_commit=False)
        with factory() as db:
            password = db.scalar(select(User.password_hash).where(User.email == "admin@test.pt"))
        with other_factory.begin() as db:
            db.add(
                User(
                    name="Outra Empresa",
                    email="admin@test.pt",
                    password_hash=password,
                    role="administrador",
                )
            )

        def other_db():
            with other_factory() as db:
                yield db

        app.dependency_overrides[db_session] = other_db
        assert (
            client.post(
                "/api/auth/login", json={"email": "admin@test.pt", "password": "PasswordTest123!"}
            ).status_code
            == 200
        )
        endpoint = f"/api/deposits/{deposit['id']}"
        assert client.get(endpoint).status_code == 404
        assert client.get(endpoint + "/pdf").status_code == 404
        assert client.get(endpoint + "/attachments/unknown").status_code == 404
        assert command(client, deposit, "exit").status_code == 404
    finally:
        app.dependency_overrides[db_session] = previous
        if other_engine:
            other_engine.dispose()
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS " + other_schema + " CASCADE"))


def test_register_in_one_step_then_reconcile_in_banks(deposits):
    """Talão entregue no banco: um só registo; a reconciliação faz-se depois, em Bancos › Reconciliação."""
    client, _, factory, _, account, _ = deposits
    deposit, _ = create(deposits, "10", True)
    registered = command(client, deposit, "register")
    assert registered.status_code == 200, registered.text
    body = registered.json()
    assert body["status"] == "entregue" and body["movement_id"] and body["delivery_date"] == today()
    # Repetir não duplica: a saída da caixa fica registada uma única vez.
    assert command(client, deposit, "register").json()["movement_id"] == body["movement_id"]
    # Movimentos reais: saída da caixa e entrada na conta de destino (derivada do depósito, sem duplicar).
    rows = client.get("/api/movements").json()
    assert sorted((m["kind"], m["direction"], m.get("bank_account_id")) for m in rows) == [("deposito", -1, None), ("deposito", 1, account)]
    bank_row = next(m for m in rows if m["direction"] == 1)
    assert bank_row["origin"] == "deposito_banco" and bank_row["amount_cents"] == 3000 and bank_row["occurred_at"].startswith(today())
    # Aparece na reconciliação geral como entrada na conta de destino, por reconciliar.
    overview = client.get("/api/reconciliation").json()
    row = next(i for i in overview["internals"] if i["source"] == "movement" and i["id"] == body["movement_id"])
    assert row["amount_cents"] == 3000 and row["account_id"] == account and row["status"] == "pendente"
    # O crédito do extrato com a referência do talão é sugerido automaticamente.
    bank = statement(deposits, 3000, "DEPOSITO " + body["number"])
    overview = client.get("/api/reconciliation").json()
    assert any(body["movement_id"] in json.dumps(s) and bank in json.dumps(s) for s in overview["suggestions"])
    confirm = client.post("/api/reconciliation/confirm", json={"lines": [
        {"source": "bank", "source_id": bank, "amount_cents": 3000},
        {"source": "movement", "source_id": body["movement_id"], "amount_cents": 3000}], "kind": "transferencia"})
    assert confirm.status_code == 200, confirm.text
    after = client.get(f"/api/deposits/{deposit['id']}").json()
    assert after["status"] == "reconciliado"
    with factory() as db:
        assert used(db, "bank", bank) == 3000
    # Reconciliado: não pode ser cancelado sem desfazer a associação em Bancos.
    blocked = command(client, deposit, "cancel", reason="Erro de registo", returned=True)
    assert blocked.status_code == 409 and "Bancos" in blocked.json()["detail"]
    assert client.post(f"/api/reconciliation/{confirm.json()['id']}/undo", json={"reason": "Associação errada"}).status_code == 200
    assert client.get(f"/api/deposits/{deposit['id']}").json()["status"] == "entregue"
    # Depois de desfeita, o cancelamento faz estorno (não apaga) e sai da reconciliação.
    assert command(client, deposit, "cancel", reason="Erro de registo", returned=True).status_code == 200
    bank_side = [m for m in client.get("/api/movements").json() if m["origin"] == "deposito_banco"]
    assert sorted(m["direction"] for m in bank_side) == [-1, 1] and any(m["reversal_of"] for m in bank_side)
    overview = client.get("/api/reconciliation").json()
    assert not any(i["id"] == body["movement_id"] for i in overview["internals"] if i["source"] == "movement")


def test_module_credit_counts_in_general_reconciliation(deposits):
    """Um crédito associado no ecrã de Depósitos reduz o que falta reconciliar em Bancos (sem duplicar)."""
    client = deposits[0]
    deposit = delivered(deposits, "10")
    bank = statement(deposits, 600)
    assert command(client, deposit, "reconcile", transaction_id=bank, amount="6").status_code == 200
    movement_id = client.get(f"/api/deposits/{deposit['id']}").json()["movement_id"]
    row = next(i for i in client.get("/api/reconciliation").json()["internals"] if i["id"] == movement_id)
    assert row["available_cents"] == 400 and row["status"] == "parcial"
