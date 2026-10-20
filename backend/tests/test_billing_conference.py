"""Faturação, movimentos reais e conferência: cenários de separação e anti-duplicação."""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from app.models import (
    BankAccount,
    BankBookEntry,
    BankTransaction,
    BillingConference,
    Movement,
    Register,
    User,
)
from conftest import open_cash
from saft_fixtures import invoice, payment, saft
from sqlalchemy import func, select
from test_saft_sales import confirm as confirm_import
from test_saft_sales import saft_case  # noqa: F401
from test_saft_sales import upload

TODAY = date.today().isoformat()
PERIOD = {"start": "2026-01-01", "end": TODAY}


@pytest.fixture
def case(saft_case, monkeypatch):  # noqa: F811
    client, ids, factory, store = saft_case
    # O modo local é exigido pelo SAF-T; os indicadores leem a mesma política.
    monkeypatch.setattr("app.billing_conference.policy", lambda db: {"mode": "local"})
    mapping = client.post("/api/saft/payment-mapping", json={"mapping": {"NU": "numerario", "CC": "cartao"}})
    assert mapping.status_code == 200, mapping.text
    return client, ids, factory, store


def imported(case, invoices):
    row = upload(case, saft(invoices))
    assert row["status"] == "ready", row
    assert row["purpose"]["id"] == "analise_faturacao_conferencia"
    done = confirm_import(case, row)
    assert done["status"] == "completed", done
    return done


def movements(case):
    with case[2]() as db:
        return db.scalar(select(func.count()).select_from(Movement))


def overview(case, **params):
    response = case[0].get("/api/conference", params={**PERIOD, **params})
    assert response.status_code == 200, response.text
    return response.json()


def receipt(case, amount, payment_method="numerario", document="", kind="cliente"):
    client, ids, _, _ = case
    session = open_cash(client, ids["registers"][0], "0")
    session_id = session.json()["id"] if session.status_code == 200 else client.get("/api/sessions").json()[0]["id"]
    response = client.post("/api/movements", json={
        "session_id": session_id, "category_id": ids["category"], "kind": kind, "payment": payment_method,
        "amount": amount, "occurred_at": datetime.now(timezone.utc).isoformat(), "document": document,
        "request_key": str(uuid4()),
    })
    assert response.status_code == 200, response.text
    return response.json()["id"], session_id


def doc_by_number(view, number):
    return next(d for d in view["documents"] if d["document"] == number)


def test_billing_and_receipts_are_separate_and_import_creates_no_entries(case):
    receipt(case, "900")  # Caixa já registada antes da importação.
    before = movements(case)
    imported(case, [invoice(value="1000.00")])
    assert movements(case) == before  # A importação não gera entradas em caixa/banco.
    data = overview(case)["indicators"]
    assert data["billing"]["gross_cents"] == 100000
    assert data["billing"]["net_cents"] == 81301  # Líquido de IVA (1000 / 1,23).
    assert data["receipts"]["received_cents"] == 90000
    assert data["to_justify_cents"] == 10000  # Mesma base (com IVA); não é dívida nem erro.
    assert "Não é, por si só, dívida" in data["to_justify_note"]
    # Reimportação do mesmo ficheiro: nenhum documento novo, nenhum movimento.
    again = upload(case, saft([invoice(value="1000.00")]))
    assert again["id"] and movements(case) == before
    assert len(overview(case)["documents"]) == 1


def test_receipt_today_for_older_invoice_counts_only_today(case):
    imported(case, [invoice(value="50.00")])
    receipt(case, "50", document="FT TESTE/1")
    today = overview(case, start=TODAY, end=TODAY)["indicators"]
    assert today["receipts"]["received_cents"] == 5000
    assert today["billing"]["gross_cents"] == 0  # A faturação é do dia do documento.


def test_several_invoices_with_one_aggregated_daily_movement(case):
    client, ids, factory, store = case
    pay = lambda value: payment(value=value, code="CC", day=TODAY if TODAY <= "2026-01-31" else "2026-01-10")  # noqa: E731
    imported(case, [invoice(number=1, value="12.30", payments=pay("12.30")), invoice(number=2, value="24.60", payments=pay("24.60"))])
    _, session_id = receipt(case, "1")  # Abre caixa.
    with factory.begin() as db:
        aggregated = Movement(session_id=session_id, user_id=db.scalar(select(User.id).where(User.email == "admin@test.pt")),
                              category_id=ids["category"], kind="venda", payment="cartao", amount_cents=3690, direction=1,
                              occurred_at=datetime.now(timezone.utc), origin="fecho_local", request_key=str(uuid4()),
                              document=f"Fecho de caixa {TODAY}")
        db.add(aggregated)
        db.flush()
        aggregated_id = aggregated.id
    view = overview(case)
    docs = [doc_by_number(view, "FT TESTE/1"), doc_by_number(view, "FT TESTE/2")]
    before = movements(case)
    body = {"kind": "individual", "confirmed": True, "notes": "Fecho diário TPA inclui as duas faturas",
            "documents": [{"record_id": d["id"], "amount_cents": d["gross_cents"]} for d in docs],
            "receipts": [{"source": "movement", "id": aggregated_id, "amount_cents": 3690}]}
    response = client.post("/api/conference/confirm", json=body)
    assert response.status_code == 200, response.text
    assert movements(case) == before  # Associar não cria movimentos.
    view = overview(case)
    assert {doc_by_number(view, n)["state"] for n in ("FT TESTE/1", "FT TESTE/2")} == {"justificado"}
    assert next(r for r in view["receipts"] if r["id"] == aggregated_id)["remaining_cents"] == 0
    # O mesmo valor não pode ser associado duas vezes.
    assert client.post("/api/conference/confirm", json=body).status_code == 409
    # Estornar o recebimento exige desfazer primeiro a conferência.
    assert client.post(f"/api/movements/{aggregated_id}/reverse", json={"reason": "Teste de bloqueio"}).status_code == 409
    assert client.post(f"/api/conference/{response.json()['id']}/undo", json={"reason": "Rever"}).status_code == 200
    assert doc_by_number(overview(case), "FT TESTE/1")["state"] == "por_conferir"


def test_partial_receipt_keeps_remaining_and_requires_evidence(case):
    client = case[0]
    imported(case, [invoice(value="12.30")])
    receipt_id, _ = receipt(case, "5")
    doc = overview(case)["documents"][0]
    body = {"kind": "individual", "confirmed": True, "documents": [{"record_id": doc["id"], "amount_cents": 500}],
            "receipts": [{"source": "movement", "id": receipt_id, "amount_cents": 500}]}
    # Só o valor não basta como evidência.
    assert client.post("/api/conference/confirm", json=body).status_code == 409
    body["notes"] = "Cliente pagou sinal ao balcão"
    assert client.post("/api/conference/confirm", json=body).status_code == 200
    doc = overview(case)["documents"][0]
    assert doc["state"] == "parcial" and doc["justified_cents"] == 500 and doc["remaining_cents"] == 730
    # Valores diferentes entre os lados são rejeitados.
    body["documents"][0]["amount_cents"] = 600
    assert client.post("/api/conference/confirm", json=body).status_code in (409, 422)


def test_aggregate_daily_conference_does_not_mark_documents_received(case):
    client, _, _, store = case
    imported(case, [invoice(value="12.30")])
    view = overview(case)
    day = view["documents"][0]["date"]
    response = client.post("/api/conference/confirm", json={"kind": "agregada", "confirmed": True, "store_id": store, "day": day})
    # Sem recebimentos nesse dia, há diferença: exige justificação.
    assert response.status_code == 409
    response = client.post("/api/conference/confirm", json={"kind": "agregada", "confirmed": True, "store_id": store, "day": day,
                                                             "notes": "Venda a crédito, recebimento posterior"})
    assert response.status_code == 200, response.text
    assert response.json()["difference_cents"] == -1230
    doc = overview(case)["documents"][0]
    assert doc["state"] == "conferido_agregado" and doc["remaining_cents"] == 1230  # Não marcado como pago.


def test_documents_without_payment_info_credit_notes_and_cancelled(case):
    client = case[0]
    imported(case, [invoice(number=1, value="24.60"), invoice("NC", 2, value="12.30"), invoice(number=3, value="12.30", status="A")])
    view = overview(case)
    data = view["indicators"]["billing"]
    assert data["gross_cents"] == 1230  # 24,60 − 12,30; o anulado não conta.
    assert data["excluded_documents"] == 1 and data["credit_notes_cents"] == 1230
    cancelled = doc_by_number(view, "FT TESTE/3")
    assert cancelled["state"] == "excluido"
    receipt_id, _ = receipt(case, "12.30", document="FT TESTE/3")
    rejected = client.post("/api/conference/confirm", json={"kind": "individual", "confirmed": True,
        "documents": [{"record_id": cancelled["id"], "amount_cents": 1230}], "receipts": [{"source": "movement", "id": receipt_id, "amount_cents": 1230}]})
    assert rejected.status_code == 422
    # Sem pagamento indicado no SAF-T não há proposta de recebimento em falta.
    missing = client.get("/api/conference/missing-receipts", params=PERIOD).json()["items"]
    assert missing == []


def test_missing_receipt_review_warns_and_requires_explicit_confirmation(case):
    client, ids, _, _ = case
    imported(case, [invoice(number=1, value="12.30", payments=payment(value="12.30", code="NU", day="2026-01-10")),
                    invoice(number=2, value="12.30", payments=payment(value="12.30", code="OU", day="2026-01-10"))])
    _, session_id = receipt(case, "12.30", document="FT TESTE/1")  # Já registado, com o número do documento.
    items = {i["document"]: i for i in client.get("/api/conference/missing-receipts", params=PERIOD).json()["items"]}
    assert items["FT TESTE/2"]["proposable"] is False  # Código sem meio associado: não assume numerário.
    known = items["FT TESTE/1"]
    assert known["candidates"] and any("associe em vez de criar" in w for w in known["warnings"])
    body = {"record_id": known["record_id"], "payment_index": 0, "session_id": session_id, "confirmed": True, "request_key": str(uuid4())}
    before = movements(case)
    assert client.post("/api/conference/missing-receipts/confirm", json=body).status_code == 409
    assert movements(case) == before
    body["acknowledge_warnings"] = True
    created = client.post("/api/conference/missing-receipts/confirm", json=body)
    assert created.status_code == 200, created.text
    assert client.post("/api/conference/missing-receipts/confirm", json=body).json()["movement_id"] == created.json()["movement_id"]
    assert movements(case) == before + 1
    with case[2]() as db:
        move = db.get(Movement, created.json()["movement_id"])
        assert "2026-01-10" in move.notes and move.occurred_at.date().isoformat() != "2026-01-10"


def test_tpa_receipt_waits_for_settlement_and_bank_credit_is_not_new_revenue(case):
    client, ids, factory, store = case
    with factory.begin() as db:
        account = BankAccount(store_id=store, name="Conta TPA", bank="Banco", iban_last4="9999", currency="EUR")
        db.add(account)
        db.flush()
        account_id = account.id
        from app.models import AccountBalanceReference

        db.add(AccountBalanceReference(target="bank:" + account_id, store_id=store, amount_cents=0, reference_date="2026-01-01",
                                       currency="EUR", user_id=db.scalar(select(User.id).where(User.email == "admin@test.pt"))))
    sale_id, _ = receipt(case, "103", payment_method="cartao")
    with factory.begin() as db:
        db.get(Movement, sale_id).bank_account_id = account_id
        bank = BankTransaction(account_id=account_id, movement_date=TODAY, value_date=TODAY, description="LIQ TPA", amount_cents=10000,
                               balance_cents=10000, fingerprint=uuid4().hex, source_account="", exported_at=TODAY + "T12:00:00", source_position=0)
        db.add(bank)
        db.flush()
        bank_id = bank.id

    def balance():
        rows = client.get("/api/bank-balances", params={"start": "2026-01-01", "end": TODAY, "account_id": account_id}).json()["accounts"]
        return rows[0]

    pending = balance()
    assert pending["book_balance_cents"] == 0  # Ainda não está no banco.
    assert pending["card_pending_settlement"]["amount_cents"] == 10300
    assert overview(case)["indicators"]["balances"]["card_pending_settlement_cents"] == 10300
    # Resolver o crédito como novo recebimento é sinalizado como possível duplicação.
    preview = client.post(f"/api/bank-transactions/{bank_id}/resolution-preview",
                          json={"kind": "receipt", "description": "Liquidação TPA", "reference": ""}).json()
    assert any(c["source"] == "tpa_batch" for c in preview["candidates"])
    resolve = client.post(f"/api/bank-transactions/{bank_id}/resolve", json={"kind": "receipt", "description": "Liquidação TPA", "reference": "",
                          "preview_token": preview["preview_token"], "request_key": str(uuid4()), "confirmed": True})
    assert resolve.status_code == 409
    # A liquidação regulariza o TPA: reconciliação com comissão explicada, sem nova receita.
    response = client.post("/api/reconciliation/confirm", json={
        "lines": [{"source": "bank", "source_id": bank_id, "amount_cents": 10000}, {"source": "movement", "source_id": sale_id, "amount_cents": 10300}],
        "commission_cents": 300, "reason": "Comissão TPA"})
    assert response.status_code == 200, response.text
    settled = balance()
    assert settled["card_pending_settlement"]["amount_cents"] == 0
    assert settled["book_balance_cents"] == 10300  # Bruto liquidado; a comissão só entra se aprovada.
    received = overview(case, start=TODAY, end=TODAY)["indicators"]["receipts"]["received_cents"]
    assert received == 10300  # Um único recebimento do cliente.
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BankBookEntry)) == 0


def test_historical_duplicates_are_previewed_and_corrected_only_on_confirmation(case):
    client = case[0]
    first, _ = receipt(case, "20", document="FT 99")
    second, _ = receipt(case, "20", document="FT 99")
    before = movements(case)
    preview = client.get("/api/conference/duplicates").json()
    assert preview["checks"][0]["ok"] is True  # Nenhum movimento gerado por SAF-T.
    item = next(i for i in preview["items"] if i["kind"] == "movimentos_identicos")
    assert {r["id"] for r in item["records"]} == {first, second}
    assert movements(case) == before  # Pré-visualização não altera nada.
    target = item["correct"]
    assert client.post("/api/conference/duplicates/correct", json={**target, "reason": "xx", "confirmed": True}).status_code == 422
    fixed = client.post("/api/conference/duplicates/correct", json={**target, "reason": "Recebimento lançado duas vezes", "confirmed": True})
    assert fixed.status_code == 200, fixed.text
    with case[2]() as db:
        original = db.get(Movement, target["id"])
        assert original.reversed_by and db.get(Movement, original.reversed_by).reversal_of == original.id
    assert not [i for i in client.get("/api/conference/duplicates").json()["items"] if i["kind"] == "movimentos_identicos"]


def test_permissions_and_tenant_scope(case, monkeypatch):
    client = case[0]
    imported(case, [invoice(value="12.30")])
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/conference", params=PERIOD).status_code == 200  # Consulta pode ver.
    doc = client.get("/api/conference", params=PERIOD).json()["documents"]
    assert client.post("/api/conference/confirm", json={"kind": "individual", "confirmed": True, "notes": "x" * 10,
        "documents": [{"record_id": doc[0]["id"] if doc else "x", "amount_cents": 1}], "receipts": [{"source": "movement", "id": "x", "amount_cents": 1}]}).status_code in (403, 404)
    assert client.get("/api/conference/duplicates").status_code == 403
    with case[2]() as db:
        assert db.scalar(select(func.count()).select_from(BillingConference)) == 0
    # Registos de outra empresa (outro NIF) nunca aparecem.
    monkeypatch.setattr("app.billing_conference.saft_source", lambda db: "saft:999999999")
    assert client.get("/api/conference", params=PERIOD).json()["documents"] == []


def register_store(case):
    with case[2]() as db:
        return db.get(Register, case[1]["registers"][0]).store_id


def test_conference_migration_is_additive_and_repeatable(case, monkeypatch):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.models import BillingConferenceLine
    from sqlalchemy import inspect, text

    receipt(case, "10")
    before = movements(case)
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    migration = importlib.import_module("migrations.versions.0048_billing_conference")
    with case[2].kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        BillingConferenceLine.__table__.drop(connection)
        BillingConference.__table__.drop(connection)
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert {"billing_conferences", "billing_conference_lines"} <= set(inspect(connection).get_table_names(schema=schema))
    assert movements(case) == before  # A migração não altera movimentos nem saldos.
