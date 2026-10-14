import io
import zipfile
from xml.sax.saxutils import escape

from app.bpi_import import parse_bpi
from app.models import BankAccount, Register


def sample(account="123 (EUR)", amount="-1.234,56", exported="11-10-2026 11:33 Horas"):
    rows = [["BPI Net Empresas"], ["Conta e Moeda", "", account], ["Data/Hora", "", exported], ["Data Mov.", "Data Valor", "Descrição do Movimento", "Valor em EUR", "Saldo em EUR"], ["09-10-2026", "08-10-2026", "Pagamento", amount, "11.267,17"]]
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for index, row in enumerate(rows, 1):
        xml += '<row>' + ''.join(f'<c r="{chr(65+i)}{index}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for i, v in enumerate(row)) + '</row>'
    xml += '</sheetData></worksheet>'
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", xml)
    return data.getvalue()


def test_preview_import_dedup_and_wrong_account(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        account = BankAccount(store_id=db.get(Register, ids["registers"][0]).store_id, name="BPI", bank="BPI", iban_last4="1234")
        db.add(account)
        db.flush()
        account_id = account.id
    path = f"/api/accounts/{account_id}/import-bpi"
    def upload(data, confirm=False):
        return client.post(path + f"?confirm={str(confirm).lower()}", files={"file": ("bpi.xlsx", data)})
    preview = upload(sample())
    assert preview.status_code == 200, preview.text
    assert preview.json()["new_count"] == 1
    assert client.get(f"/api/accounts/{account_id}/transactions").json() == []
    assert upload(sample(), True).json()["new_count"] == 1
    repeated = upload(sample(), True).json()
    assert repeated["new_count"] == 0 and repeated["duplicate_count"] == 1
    saved = client.get(f"/api/accounts/{account_id}/transactions").json()
    assert len(saved) == 1 and saved[0]["amount_cents"] == -123456 and saved[0]["balance_cents"] == 1126717
    assert upload(sample("456 (EUR)"), True).status_code == 409
    assert upload(b"invalid").status_code == 422
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert upload(sample(), True).status_code == 403


def test_fingerprint_ignores_export_timestamp():
    _, rows = parse_bpi(sample())
    _, later = parse_bpi(sample(exported="12-10-2026 12:33 Horas"))
    assert rows[0]["fingerprint"] == later[0]["fingerprint"]
    assert rows[0]["movement_date"] == "2026-10-09"
    assert rows[0]["value_date"] == "2026-10-08"


def test_overlapping_files_preserve_legitimate_same_day_amount_and_identical_occurrences(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        account = BankAccount(store_id=db.get(Register, ids["registers"][0]).store_id, name="BPI", bank="BPI", iban_last4="1234")
        db.add(account)
        db.flush()
        account_id = account.id
    single = sample()
    with zipfile.ZipFile(io.BytesIO(single)) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode()
    last = xml[xml.rindex("<row>"):xml.rindex("</row>") + len("</row>")]
    # Same day, amount and description with a different running balance are separate movements.
    full_xml = xml.replace("</sheetData>", last.replace("11.267,17", "10.032,61") + last + "</sheetData>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", full_xml)
    data = buffer.getvalue()
    _, parsed = parse_bpi(data)
    assert len(parsed) == 3 and len({row["fingerprint"] for row in parsed}) == 3
    path = f"/api/accounts/{account_id}/import-bpi?confirm=true"
    def upload(raw):
        return client.post(path, files={"file": ("bpi.xlsx", raw)})
    assert upload(single).json()["new_count"] == 1
    assert upload(data).json()["new_count"] == 2
    assert upload(data).json()["new_count"] == 0
    assert len(client.get(f"/api/accounts/{account_id}/transactions").json()) == 3
