from html import escape

import pytest
from app.models import BankAccount, BankTransaction, DocumentImport, Register
from app.viva_import import parse_viva
from sqlalchemy import func, select


def sample(rows=None, opening="1 000,00", closing="1 029,82"):
    if rows is None:
        rows = [
            ["02/01/2026", "02/01/2026", "Liquidação de Vendas em Cartões", "36,00", "1\u00a0036,00"],
            ["02/01/2026", "02/01/2026", "Comissão de Cartões", "-0,18", "1 035,82"],
            ["03/01/2026", "04/01/2026", "Saída de Dinheiro", "-6,00", "1 029,82"],
        ]
    header = ["Data da Transação", "Data Valor", "Descrição", "Montante", "Saldo"]
    content = '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
    content += '<table class="table-results"><tr>'
    content += "".join(f"<th>{escape(value)}</th>" for value in header) + "</tr>"
    content += f'<tr><td colspan="4">Saldo Anterior</td><td>{opening}</td></tr>'
    for row in rows:
        content += "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>"
    content += f'<tr><td colspan="4">Saldo a Transitar</td><td>{closing}</td></tr>'
    return (content + "</table></body></html>").encode("utf-8")


def test_dates_entities_signs_summaries_and_ordering():
    data = sample().replace("Liquidação".encode(), b"Liquida&#231;&#227;o")
    source, rows = parse_viva(data)
    assert source == "viva_wallet:EUR"
    assert len(rows) == 3  # Opening and carry-forward balances are not transactions.
    assert [row["amount_cents"] for row in rows] == [3600, -18, -600]
    assert rows[0]["description"] == "Liquidação de Vendas em Cartões"
    assert rows[-1]["value_date"] == "2026-01-04"
    assert rows[-1]["balance_cents"] == 102982
    assert [row["source_position"] for row in rows] == [2, 1, 0]
    assert len({row["fingerprint"] for row in rows}) == 3


@pytest.mark.parametrize("data", [
    b"invalid",
    b"\xd0\xcf\x11\xe0",  # An unrelated binary XLS is not interpreted as HTML.
    sample().replace(b"Montante", b"Total USD"),
    sample().replace(b"03/01/2026", b"32/01/2026"),
    sample().replace(b"-0,18", b"NaN"),
    sample().replace(b"-0,18", b"-0,181"),
    sample().replace(b"-0,18", b"-0,19"),
    sample(closing="1 029,81"),
    sample().replace(b"</table>", b""),
    sample().replace(b"<td>Sa", b"<td></td><td>Sa"),
    sample().replace(b"Saldo Anterior", b"Outro resumo"),
])
def test_invalid_statement_is_rejected_instead_of_partially_imported(data):
    with pytest.raises(ValueError):
        parse_viva(data)


def test_overlap_fingerprint_and_identical_occurrences():
    _, original = parse_viva(sample())
    _, overlap = parse_viva(sample(rows=[
        ["03/01/2026", "04/01/2026", "Saída de Dinheiro", "-6,00", "1 029,82"],
    ], opening="1 035,82"))
    assert original[-1]["fingerprint"] == overlap[0]["fingerprint"]
    _, identical = parse_viva(sample(rows=[
        ["03/01/2026", "03/01/2026", "Sem alteração", "0,00", "1 000,00"],
    ] * 2, closing="1 000,00"))
    assert identical[0]["fingerprint"] != identical[1]["fingerprint"]


def test_preview_confirm_dedup_permissions_and_currency(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        account = BankAccount(
            store_id=db.get(Register, ids["registers"][0]).store_id,
            name="Viva Wallet", bank="Viva Wallet", iban_last4="1234",
        )
        db.add(account)
        db.flush()
        account_id = account.id
    path = f"/api/accounts/{account_id}/import-statement"

    def upload(confirm=False, raw=None):
        return client.post(
            path + f"?confirm={str(confirm).lower()}",
            files={"file": ("ExtratoWallet_202601.xls", raw or sample())},
        )

    preview = upload()
    assert preview.status_code == 200, preview.text
    assert preview.json()["format"] == "Viva Wallet"
    assert preview.json()["new_count"] == 3
    assert preview.json()["warnings"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DocumentImport)) == 0
        assert db.scalar(select(func.count()).select_from(BankTransaction)) == 0
    assert upload(True).json()["new_count"] == 3
    repeated = upload(True).json()
    assert repeated["new_count"] == 0 and repeated["duplicate_count"] == 3
    saved = client.get(f"/api/accounts/{account_id}/transactions").json()
    assert len(saved) == 3
    assert saved[0]["movement_date"] == "2026-01-03"
    assert saved[0]["balance_cents"] == 102982
    with factory() as db:
        assert db.scalar(select(DocumentImport.source).limit(1)) == "viva_wallet_xls"
    assert upload(True, sample(closing="1 029,81")).status_code == 422
    assert upload(True, b"x" * 5_000_001).status_code == 413
    with factory.begin() as db:
        db.get(BankAccount, account_id).currency = "USD"
    assert upload(True).status_code == 422
    with factory.begin() as db:
        db.get(BankAccount, account_id).currency = "EUR"
    # A BPI file may not be merged into an existing Viva statement account.
    from test_bpi_import import sample as bpi_sample
    assert upload(True, bpi_sample()).status_code == 409
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert upload(True).status_code == 403
    client.post("/api/auth/logout")
    assert upload(True).status_code == 401
