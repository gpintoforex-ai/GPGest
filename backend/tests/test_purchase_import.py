import io
import zipfile
from xml.sax.saxutils import escape

import pytest
from app.models import Register
from app.purchase_import import parse_purchases


def csv_data(amount="123,00"):
    return f'NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;IVA\n500000000;Empresa;FT A/1;FT;11/10/2026;{amount};23,00\n'.encode("utf-8-sig")


def test_preview_confirmation_repeat_and_conflicts(setup):
    client, ids, factory = setup
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    path = f"/api/purchases/import/{store}"
    def upload(data, confirm=False):
        return client.post(path + f"?confirm={str(confirm).lower()}", files={"file": ("compras.csv", data)})
    preview = upload(csv_data())
    assert preview.status_code == 200, preview.text
    assert preview.json()["new_count"] == 1
    assert client.get("/api/purchases").json() == []
    assert upload(csv_data(), True).json()["new_count"] == 1
    saved = client.get("/api/purchases").json()[0]
    assert saved["origin"] == "ficheiro" and saved["payment_status"] == "por_reconciliar" and not saved["attachments"]
    assert upload(csv_data(), True).json()["duplicate_count"] == 1
    assert upload(csv_data("124,00")).json()["conflict_count"] == 1
    assert upload(csv_data("124,00"), True).status_code == 409
    assert len(client.get("/api/purchases").json()) == 1
    assert client.get("/api/purchases/import-template").status_code == 200
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert upload(csv_data(), True).status_code == 403


def test_csv_types_encodings_and_invalid_lines():
    rows = parse_purchases(csv_data(), "list.csv")
    assert rows[0]["amount_cents"] == 12300 and rows[0]["document_date"] == "2026-10-11"
    credit = csv_data().replace(b"FT A/1;FT", b"NC A/1;NC")
    assert parse_purchases(credit, "list.csv")[0]["amount_cents"] == -12300
    invalid = csv_data() + b'bad;Bad;FT A/2;FT;2026-10-11;100;0\n'
    with pytest.raises(ValueError, match="Linha 3"):
        parse_purchases(invalid, "list.csv")
    with pytest.raises(ValueError, match="colunas"):
        parse_purchases(b'Foo;Bar\n1;2', "list.csv")


def test_import_does_not_block_other_api_requests(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app import purchase_import

    client, ids, factory = setup
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    entered, release = Event(), Event()
    original = purchase_import.parse_purchases

    def slow_parse(*args):
        entered.set()
        assert release.wait(5), "O teste não libertou a importação."
        return original(*args)

    monkeypatch.setattr(purchase_import, "parse_purchases", slow_parse)
    with ThreadPoolExecutor(max_workers=2) as workers:
        importing = workers.submit(
            client.post, f"/api/purchases/import/{store}?confirm=true",
            files={"file": ("compras.csv", csv_data())},
        )
        try:
            assert entered.wait(3)
            health = workers.submit(client.get, "/api/health")
            assert health.result(timeout=2).status_code == 200
        finally:
            release.set()
        result = importing.result(timeout=5)
        assert result.status_code == 200, result.text
        assert result.json()["new_count"] == 1


def test_actual_efatura_export_columns():
    data = 'Setor;Emitente;Nº Fatura / ATCUD;Tipo;Data Emissão;Total;IVA;Base Tributável;Situação;Comunicação Emitente;Comunicação Adquirente\nOutros;500000000 - Empresa Exemplo;FT A/123 / ABCD1234-123;Fatura;2026-08-27;349,90 €;65,43 €;284,47 €;Registado;Sim;Não\nOutros;500000000 - Empresa Exemplo;NC A/5 / ABCD1234-5;Nota de crédito;2026-08-28;20,00 €;3,74 €;16,26 €;Registado;Sim;Não\n'
    for encoding in ("utf-8-sig", "cp1252"):
        rows = parse_purchases(data.encode(encoding), "e-fatura.csv")
        assert rows[0]["supplier_nif"] == "500000000"
        assert rows[0]["supplier_name"] == "Empresa Exemplo"
        assert rows[0]["document"] == "FT A/123"
        assert rows[0]["amount_cents"] == 34990
        assert rows[1]["document"] == "NC A/5"
        assert rows[1]["amount_cents"] == -2000


def test_xlsx_numeric_dates_and_shared_strings():
    headers = ['NIF emitente', 'Fornecedor', 'Documento', 'Tipo', 'Data', 'Total', 'IVA']
    values = ['500000000', 'Empresa', 'FT A/1', 'FT', '46306', '123.00', '23.00']
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for line, row in enumerate([headers, values], 1):
        xml += '<row>' + ''.join(f'<c r="{chr(65+i)}{line}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for i, v in enumerate(row)) + '</row>'
    xml += '</sheetData></worksheet>'
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('xl/worksheets/sheet1.xml', xml)
    result = parse_purchases(data.getvalue(), "list.xlsx")
    assert result[0]["amount_cents"] == 12300
    assert result[0]["document_date"] == "2026-10-11"


def test_cancelled_and_zero_total_documents_are_kept_visible_but_not_importable():
    data = ("Setor;Emitente;Nº Fatura / ATCUD;Tipo;Data Emissão;Total;IVA;Base Tributável;Situação\n"
            "Geral;500000000 - Beta;FT B/1 / X-1;Fatura;2026-07-01;10,00 €;1,87 €;8,13 €;Registado\n"
            "Geral;500000000 - Beta;FT B/2 / X-2;Fatura;2026-07-02;0,00 €;0,00 €;0,00 €;Registado\n"
            "Geral;500000000 - Beta;FT B/3 / X-3;Fatura;2026-07-03;5,00 €;0,00 €;5,00 €;Documento Anulado pelo Emitente\n")
    rows = parse_purchases(data.encode("utf-8"), "e-fatura.csv")
    assert [(row["document"], row["skip_reason"]) for row in rows] == [
        ("FT B/1", ""), ("FT B/2", "Total 0,00 €"), ("FT B/3", "Anulado pelo emitente no e-Fatura")]
