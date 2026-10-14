import base64
from xml.etree import ElementTree as ET

from cryptography.hazmat.primitives.asymmetric import rsa, padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

from app.models import BankAccount, BankTransaction, EfaturaConfig, Register
from app.integrations.efatura import NS, SEC, EfaturaError, parse_response, request_xml
import pytest


def prepare(setup):
    c, ids, factory = setup
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        other_store = db.get(Register, ids["registers"][2]).store_id
        account = BankAccount(store_id=store, name="BPI", bank="BPI", iban_last4="1234")
        other = BankAccount(store_id=other_store, name="Outra", bank="BPI", iban_last4="5678")
        db.add_all([account, other]); db.flush()
        rows = [BankTransaction(account_id=a.id, movement_date="2026-10-10", value_date="2026-10-10", description="Pagamento", amount_cents=amount, balance_cents=100000, fingerprint=str(i), source_account="sample", exported_at="2026-10-11T12:00:00", source_position=i) for i, (a, amount) in enumerate([(account, -10000), (account, -5000), (other, -10000), (account, 10000)])]
        db.add_all(rows); db.flush()
        return c, factory, store, [r.id for r in rows]


def create(c, store, document="FT A/1", amount="100", kind="FT"):
    return c.post("/api/purchases", json={"store_id": store, "supplier_nif": "500000000", "supplier_name": "Fornecedor", "document": document, "document_type": kind, "document_date": "2026-10-10", "amount": amount})


def test_independent_states_partial_grouped_and_overallocation(setup):
    c, _, store, transactions = prepare(setup)
    first = create(c, store).json()
    second = create(c, store, "FT A/2", "50").json()
    assert create(c, store).status_code == 409
    assert first["payment_status"] == "por_reconciliar" and not first["attachments"]
    path = f"/api/purchases/{first['id']}/allocate"
    assert c.post(path, json={"transaction_id": transactions[2], "amount": "10"}).status_code == 422
    assert c.post(path, json={"transaction_id": transactions[3], "amount": "10"}).status_code == 422
    partial = c.post(path, json={"transaction_id": transactions[0], "amount": "50"}).json()
    assert partial["payment_status"] == "parcial" and partial["remaining_cents"] == 5000
    assert c.post(path, json={"transaction_id": transactions[0], "amount": "1"}).status_code == 409
    assert c.post(f"/api/purchases/{second['id']}/allocate", json={"transaction_id": transactions[0], "amount": "50"}).status_code == 200
    assert c.post(path, json={"transaction_id": transactions[0], "amount": "1"}).status_code == 409
    complete = c.post(path, json={"transaction_id": transactions[1], "amount": "50"}).json()
    assert complete["payment_status"] == "reconciliado" and not complete["attachments"] and not complete["validated"]
    assert c.post(path, json={"transaction_id": transactions[1], "amount": "0"}).status_code == 422
    link = complete["allocations"][0]
    assert c.post(f"/api/purchases/{first['id']}/allocations/{link['id']}/remove").json()["payment_status"] == "parcial"
    available = c.get("/api/purchases/bank-transactions").json()
    assert next(t for t in available if t["id"] == link["transaction_id"])["available_cents"] == 5000


def test_document_validation_download_and_access(setup):
    c, _, store, _ = prepare(setup)
    row = create(c, store).json()
    path = f"/api/purchases/{row['id']}"
    assert c.post(path + "/attachments", files={"file": ("bad.html", b"<html>bad</html>", "text/html")}).status_code == 422
    a = c.post(path + "/attachments", files={"file": ("fatura.pdf", b"%PDF-1.7\ntest", "application/pdf")}).json()
    saved = c.get("/api/purchases").json()[0]
    assert saved["attachments"] and saved["payment_status"] == "por_reconciliar"
    download = c.get(f"/api/purchases/attachments/{a['id']}")
    assert download.content.startswith(b"%PDF") and download.headers["content-disposition"].startswith("attachment;")
    assert c.post(path + "/validate", json={"validated": True}).json()["validated"]
    c.post("/api/auth/logout")
    c.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert c.get("/api/purchases").status_code == 200
    assert c.post(path + "/validate", json={"validated": False}).status_code == 403
    assert c.post(path + f"/attachments/{a['id']}/remove").status_code == 403


def test_efatura_config_sync_dedup_preserves_documents(setup, monkeypatch):
    import app.purchases as module
    c, factory, store, _ = prepare(setup)
    path = f"/api/purchases/efatura/{store}"
    credentials = {"nif": "500000000", "username": "500000000/1", "password": "SecretTest!", "environment": "testes"}
    saved = c.post(path, json=credentials)
    assert saved.status_code == 200, saved.text
    assert "SecretTest" not in saved.text and "password_ciphertext" not in saved.text
    with factory() as db:
        assert db.get(EfaturaConfig, store).password_ciphertext != credentials["password"]
    assert c.post(path, json={**credentials, "nif": "500000001"}).status_code == 422
    assert c.post(path + "/sync", json={"start": "2026-10-01", "end": "2026-11-01"}).status_code == 422
    assert c.post(path + "/sync", json={"start": "2026-10-01", "end": "2026-10-31"}).status_code == 422
    item = {"supplier_nif": "500000000", "supplier_name": "500000000", "document": "FT A/100", "document_type": "FT", "document_date": "2026-10-01", "amount_cents": 10000, "tax_cents": 2300}
    monkeypatch.setattr(module, "fetch_invoices", lambda *args: [item])
    payload = {"start": "2026-10-01", "end": "2026-10-31"}
    assert c.post(path + "/sync", json=payload).json()["imported"] == 1
    row = c.get("/api/purchases").json()[0]
    c.post(f"/api/purchases/{row['id']}/validate", json={"validated": True})
    c.post(f"/api/purchases/{row['id']}/attachments", files={"file": ("invoice.pdf", b"%PDF-1.7", "application/pdf")})
    assert c.post(path + "/sync", json=payload).json()["imported"] == 0
    same = c.get("/api/purchases").json()[0]
    assert same["validated"] and same["attachments"]
    item["amount_cents"] = 12000
    assert c.post(path + "/sync", json=payload).json()["updated"] == 1
    assert not c.get("/api/purchases").json()[0]["validated"]


def test_soap_security_and_acquirer_contract():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    first = request_xml("500000000", "500000000/1", "TestPassword", private.public_key(), "2026-10-01", "2026-10-31", 2)
    root = ET.fromstring(first)
    token = root.find(f".//{{{SEC}}}UsernameToken")
    key = private.decrypt(base64.b64decode(token.findtext(f"{{{SEC}}}Nonce")), rsa_padding.PKCS1v15())
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    encoded = base64.b64decode(token.findtext(f"{{{SEC}}}Password"))
    unpadder = padding.PKCS7(128).unpadder()
    decoded = decryptor.update(encoded) + decryptor.finalize()
    assert unpadder.update(decoded) + unpadder.finalize() == b"TestPassword"
    assert root.findtext(f".//{{{NS}}}CustomerTaxID") == "500000000"
    assert root.findtext(f".//{{{NS}}}nPage") == "2"
    second = request_xml("500000000", "500000000/1", "TestPassword", private.public_key(), "2026-10-01", "2026-10-31", 2)
    assert first != second and b"TestPassword" not in first


def test_response_credit_notes_and_errors():
    response = f'<InvoicesResponse xmlns="{NS}"><InvoicesList><Invoice><InvoiceNo>NC A/1</InvoiceNo><InvoiceType>NC</InvoiceType><InvoiceDate>2026-10-01</InvoiceDate><TaxRegistrationNumber>500000000</TaxRegistrationNumber><GrossTotal>123.00</GrossTotal><TaxPayable>23.00</TaxPayable></Invoice><PaginationTotal><totalPages>2</totalPages><nPage>1</nPage></PaginationTotal></InvoicesList><estadoExecucao><EstadoOperacao>200</EstadoOperacao></estadoExecucao></InvoicesResponse>'.encode()
    rows, pages, page = parse_response(response)
    assert rows[0]["amount_cents"] == -12300 and pages == 2 and page == 1
    with pytest.raises(EfaturaError):
        parse_response(response.replace(b">200<", b">99<"))
    with pytest.raises(EfaturaError):
        parse_response(b'<!DOCTYPE test><test/>')
