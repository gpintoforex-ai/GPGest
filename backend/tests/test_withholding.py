"""Retenção na fonte na reconciliação: proposta reconhecida (taxa da ficha ou habitual), confirmação com a fatura paga
na totalidade e a retenção explícita, retenções por entregar ao Estado e valor previsto da obrigação. Dados fictícios."""

import json

import pytest
from app.models import (
    Audit,
    BankAccount,
    BankTransaction,
    BusinessEntity,
    Obligation,
    ObligationOccurrence,
    Purchase,
    Reconciliation,
    Register,
    User,
)
from sqlalchemy import select

NIF = "999999990"


@pytest.fixture
def case(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        account = BankAccount(store_id=store, name="Banco", bank="BPI", iban_last4="0001")
        db.add(account)
        db.flush()
        bank = BankTransaction(account_id=account.id, movement_date="2026-08-04", value_date="2026-08-04",
                               description="TRF CR SEPA+ 0001 P/ PT50000000000000000000000 JOAO EXEMPLO", amount_cents=-16725,
                               balance_cents=0, fingerprint="w1", source_account="", exported_at="", source_position=0)
        purchase = Purchase(store_id=store, supplier_nif=NIF, supplier_name="Joao Exemplo Silva", document="FACT 1/450",
                            document_date="2026-07-31", amount_cents=18450, base_cents=15000, tax_cents=3450, origin="manual")
        db.add_all([bank, purchase])
        db.flush()
        result = {"store": store, "bank": bank.id, "purchase": purchase.id}
    return client, factory, result


def proposal(client, ids):
    data = client.get("/api/reconciliation").json()
    return next((s for s in data["suggestions"] if s["bank_id"] == ids["bank"] and s["source_id"] == ids["purchase"]), None)


def test_suggestion_recognises_usual_or_configured_rate(case):
    client, factory, ids = case
    found = proposal(client, ids)
    assert found and found["withholding"] == {"cents": 1725, "basis_points": 1150, "base_cents": 15000, "configured": False}
    assert any("Retenção na fonte de 11,5 % sobre 150,00 €: 17,25 €" in r and "taxa habitual" in r for r in found["reasons"])
    with factory.begin() as db:
        db.add(BusinessEntity(name="Joao Exemplo Silva", tax_number=NIF, is_supplier=True, withholding=True, withholding_basis_points=1150))
    assert proposal(client, ids)["withholding"]["configured"] is True
    with factory.begin() as db:
        db.scalar(select(BusinessEntity).where(BusinessEntity.tax_number == NIF)).withholding_basis_points = 2500
    # Taxa da ficha diferente: não se presume outra taxa.
    other = proposal(client, ids)
    assert other is None or "withholding" not in other


def test_payment_of_base_without_vat_is_not_presumed_withholding(case):
    client, factory, ids = case
    with factory.begin() as db:
        db.get(BankTransaction, ids["bank"]).amount_cents = -15000  # Pagou a base: a diferença (23 %) é o IVA.
    found = proposal(client, ids)
    assert found is None or "withholding" not in found


def test_confirm_with_withholding_and_amounts_to_deliver(case):
    client, factory, ids = case
    lines = [{"source": "bank", "source_id": ids["bank"], "amount_cents": 16725},
             {"source": "purchase", "source_id": ids["purchase"], "amount_cents": 18450}]
    base = {"lines": lines, "kind": "pagamento", "commission_cents": 0, "difference_cents": 1725,
            "reason": "Retenção na fonte IRS 11,5 % sobre 150,00 €"}
    assert client.post("/api/reconciliation/confirm", json={**base, "withholding_cents": 2000}).status_code == 422
    assert client.post("/api/reconciliation/confirm", json={**base, "withholding_cents": 1725, "reason": ""}).status_code == 422
    confirmed = client.post("/api/reconciliation/confirm", json={**base, "withholding_cents": 1725})
    assert confirmed.status_code == 200, confirmed.text
    with factory() as db:
        group = db.get(Reconciliation, confirmed.json()["id"])
        assert (group.difference_cents, group.withholding_cents) == (1725, 1725)
        audit = db.scalar(select(Audit).where(Audit.action == "reconciliacao_confirmada", Audit.target_id == group.id))
        assert json.loads(audit.detail)["withholding_cents"] == 1725
    data = client.get("/api/reconciliation").json()
    purchase = next(i for i in data["internals"] if i["id"] == ids["purchase"])
    assert purchase["status"] in {"reconciliado", "pago"} and purchase["available_cents"] == 0  # Fatura paga na totalidade.

    listing = client.get("/api/withholdings").json()
    month = listing["months"][0]
    assert (month["kind"], month["month"], month["total_cents"], month["due_date"]) == ("a_entregar", "2026-08", 1725, "2026-09-20")
    assert month["items"][0]["documents"][0]["document"] == "FACT 1/450" and month["occurrence"] is None

    with factory.begin() as db:
        admin = db.scalar(select(User).where(User.email == "admin@test.pt")).id
        obligation = Obligation(store_id=None, category="fiscal", kind="Retenções na fonte", title="Retenções na fonte (IRS/IRC) — pagamento",
                                periodicity="mensal", start_date="2026-01-20", created_by=admin)
        db.add(obligation)
        db.flush()
        occurrence = ObligationOccurrence(obligation_id=obligation.id, rule_date="2026-09-20", due_date="2026-09-20")
        db.add(occurrence)
        db.flush()
        occurrence_id = occurrence.id
    month = client.get("/api/withholdings").json()["months"][0]
    assert month["occurrence"]["id"] == occurrence_id and month["occurrence"]["expected_cents"] is None
    body = {"month": "2026-08", "occurrence_id": occurrence_id, "revision": month["occurrence"]["revision"]}
    assert client.post("/api/withholdings/forecast", json={**body, "month": "2026-07"}).status_code == 422
    updated = client.post("/api/withholdings/forecast", json=body)
    assert updated.status_code == 200, updated.text
    assert client.post("/api/withholdings/forecast", json=body).status_code == 409  # Revisão antiga.
    with factory() as db:
        assert db.get(ObligationOccurrence, occurrence_id).expected_cents == 1725
        assert db.scalar(select(Audit).where(Audit.action == "retencoes_valor_previsto"))

    # Desfazer a reconciliação retira a retenção da lista.
    assert client.post(f"/api/reconciliation/{confirmed.json()['id']}/undo", json={"reason": "Associação errada"}).status_code == 200
    assert client.get("/api/withholdings").json()["months"] == []


def test_financial_discount_is_recognised_and_listed(case):
    client, factory, ids = case
    with factory.begin() as db:
        bank = db.get(BankTransaction, ids["bank"])
        bank.amount_cents, bank.movement_date = -10981, "2026-07-31"
        purchase = db.get(Purchase, ids["purchase"])
        purchase.amount_cents, purchase.base_cents, purchase.tax_cents = 11256, 9151, 2105
    found = proposal(client, ids)
    assert found and "withholding" not in found
    assert found["discount"] == {"cents": 275, "basis_points": 300, "on": "base", "basis_cents": 9151}
    assert any("Possível desconto financeiro de 3 % sobre a base de 91,51 €: 2,75 € (confirme)" in r for r in found["reasons"])
    lines = [{"source": "bank", "source_id": ids["bank"], "amount_cents": 10981},
             {"source": "purchase", "source_id": ids["purchase"], "amount_cents": 11256}]
    body = {"lines": lines, "kind": "pagamento", "commission_cents": 0, "difference_cents": 275, "discount_cents": 275,
            "reason": "Desconto financeiro de 3 % sobre a base de 91,51 €"}
    assert client.post("/api/reconciliation/confirm", json={**body, "withholding_cents": 100}).status_code == 422  # Excede a diferença.
    confirmed = client.post("/api/reconciliation/confirm", json=body)
    assert confirmed.status_code == 200, confirmed.text
    with factory() as db:
        group = db.get(Reconciliation, confirmed.json()["id"])
        assert (group.difference_cents, group.discount_cents, group.withholding_cents) == (275, 275, 0)
    month = client.get("/api/withholdings").json()["months"][0]
    assert (month["kind"], month["month"], month["total_cents"]) == ("desconto_obtido", "2026-07", 275) and "due_date" not in month
