"""Extratos BPI em PDF: leitura pelo texto com os saldos validados linha a linha, mudança de ano, descrições com
espaços e datas, importação com pré-visualização e duplicados, recusa total quando um saldo não bate certo.

Todos os dados (conta, empresa, NIF, referências e valores) são fictícios."""

import io

import pytest
from app.bpi_pdf_import import parse_bpi_pdf
from app.models import BankAccount, BankTransaction, Register
from reportlab.pdfgen import canvas
from sqlalchemy import select

ACCOUNT = "9-1234567-000-001"
ROWS = [
    ("30/12", "30/12", "DD CAMARA MUNICIPAL 00000000001", "-12,50", "987,50"),
    ("", "31/12", "DEPOSITO EM NUMERARIO", "2 500,00", "3 487,50"),
    ("02/01", "02/01", "19/01 COMPRA EL-E 0000000/01 CTT - CORREIOS      LISBOA", "-10,25", "3 477,25"),
    ("", "02/01", "PAGAMENTO AO ESTADO - 111 222 333 444 555", "-100,00", "3 377,25"),
    ("", "02/01", "COBRANCA TSU NIF=999999990 - 202512", "-300,00", "3 077,25"),
]


def statement(rows=ROWS, account=ACCOUNT, currency="EUR", closing="3 077,25", period="De 01/12/2025 a 05/01/2026"):
    data = io.BytesIO()
    pdf = canvas.Canvas(data, pagesize=(842, 595))
    pdf.setFont("Helvetica", 8)
    y = 560

    def line(*cells):
        nonlocal y
        for x, text, right in cells:
            (pdf.drawRightString if right else pdf.drawString)(x, y, text)
        y -= 14

    line((700, "EXTRACTO DE CONTA", False))
    line((40, "Conta", False), (120, account, False), (420, "EMPRESA EXEMPLO, LDA", False))
    line((40, "Período", False), (120, period, False))
    line((40, "DATA", False), (80, "DATA", False), (130, "DESCRIÇÃO DO MOVIMENTO", False), (600, "MOEDA", False), (700, "VALOR", True), (800, "SALDO", True))
    line((40, f"CONTA VALOR BPI NEGOCIOS Nº: {account}", False), (600, currency, False))
    line((130, "SALDO ANTERIOR CONTABILISTICO", False), (800, "1 000,00", True))
    for movement, value, description, amount, balance in rows:
        line(*([(40, movement, False)] if movement else []), (80, value, False), (130, description, False), (700, amount, True), (800, balance, True))
    line((130, "SALDO ACTUAL CONTABILISTICO", False), (800, closing, True))
    line((130, "SALDO ACTUAL DISPONIVEL", False), (800, closing, True))
    line((40, "Taxa anual nominal de 10,00%, TAEG de 11,00%.", False))
    pdf.showPage()
    pdf.save()
    return data.getvalue()


def test_parse_validates_balances_years_and_descriptions():
    source, rows, summary = parse_bpi_pdf(statement())
    assert source == "9-1234567.000.001 (EUR)"  # Mesmo formato do extrato XLSX do BPI Net Empresas.
    assert summary == {"period": ["2025-12-01", "2026-01-05"], "opening_cents": 100000, "closing_cents": 307725, "count": 5}
    assert [(r["movement_date"], r["value_date"]) for r in rows] == [
        ("2025-12-30", "2025-12-30"), ("2025-12-30", "2025-12-31"), ("2026-01-02", "2026-01-02"), ("2026-01-02", "2026-01-02"), ("2026-01-02", "2026-01-02")]
    assert rows[2]["description"] == "19/01 COMPRA EL-E 0000000/01 CTT - CORREIOS LISBOA"
    assert [r["amount_cents"] for r in rows] == [-1250, 250000, -1025, -10000, -30000]
    assert len({r["fingerprint"] for r in rows}) == 5 and rows[0]["exported_at"] == "2026-01-05T00:00:00"


@pytest.mark.parametrize("case, message", [
    ({"rows": [*ROWS[:3], (*ROWS[3][:4], "3 377,26"), ROWS[4]]}, "não batem certo"),
    ({"closing": "3 077,26"}, "saldo final"),
    ({"currency": "USD"}, "euros"),
    ({"period": "sem período"}, "conta e o período"),
])
def test_any_inconsistency_rejects_the_whole_file(case, message):
    with pytest.raises(ValueError, match=message):
        parse_bpi_pdf(statement(**case))
    with pytest.raises(ValueError):
        parse_bpi_pdf(b"%PDF-1.4 isto nao e um pdf")


def test_import_preview_confirm_duplicates_and_wrong_account(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        account = BankAccount(store_id=db.get(Register, ids["registers"][0]).store_id, name="BPI", bank="BPI", iban_last4="0001")
        db.add(account)
        db.flush()
        account_id = account.id
    path = f"/api/accounts/{account_id}/import-statement"

    def upload(data, confirm=False, name="Extrato_Conta_2026-01-05.pdf"):
        return client.post(path + f"?confirm={str(confirm).lower()}", files={"file": (name, data, "application/pdf")})

    preview = upload(statement())
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["format"] == "BPI (extrato PDF)" and body["new_count"] == 5 and not body["imported"]
    assert "saldo anterior 1.000,00 € + movimentos = saldo final 3.077,25 €, validado linha a linha" in body["warnings"][0]
    with factory() as db:
        assert not db.scalar(select(BankTransaction.id))
    confirmed = upload(statement(), True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["new_count"] == 5
    again = upload(statement(), True).json()
    assert again["new_count"] == 0 and again["duplicate_count"] == 5
    with factory() as db:
        saved = list(db.scalars(select(BankTransaction).where(BankTransaction.account_id == account_id)))
        assert {row.source_account for row in saved} == {"9-1234567.000.001 (EUR)"} and len(saved) == 5
    assert upload(statement(account="9-7654321-000-001"), True).status_code == 409  # Outra conta.
    bad = upload(statement(closing="3 077,26"), True)
    assert bad.status_code == 422 and "não foi importado" in bad.json()["detail"]
