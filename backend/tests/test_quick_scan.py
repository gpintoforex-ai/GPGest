import io
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from app import db as database
from app import quick_scan
from app.main import app
from app.models import (
    AttachmentIntegrationConfig,
    Audit,
    DocumentScan,
    Purchase,
    QuickScanAccess,
    QuickScanSession,
    Register,
)
from app.platform_models import Company
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_document_scanning import Drive, photo
from test_platform import create, enter, platform  # noqa: F401

PIN = "482915"
CORNERS = [[0.05, 0.04], [0.95, 0.06], [0.93, 0.96], [0.04, 0.94]]


@pytest.fixture
def quick(setup, monkeypatch):
    client, ids, factory = setup
    quick_scan.ip_attempts.clear()

    def company():
        with factory() as db:
            db.info["company"] = {"id": "demo-company", "name": "Empresa Demo"}
            yield db

    original = app.dependency_overrides[database.db_session]
    app.dependency_overrides[database.db_session] = company
    app.dependency_overrides[quick_scan.quick_db] = company
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        db.add(AttachmentIntegrationConfig(id="default", google_email="demo@example.pt", drive_folder="root"))
    drive = Drive()
    monkeypatch.setattr("app.quick_scan.authorized_client", lambda db: (drive, db.get(AttachmentIntegrationConfig, "default")))
    # Dispositivo sem cookies: só o acesso rápido, nunca a sessão do administrador.
    with TestClient(app) as phone:
        yield client, phone, factory, store, drive
    app.dependency_overrides[database.db_session] = original
    app.dependency_overrides.pop(quick_scan.quick_db, None)


def new_access(client, store, pin=PIN, **changes):
    r = client.post("/api/quick-scan/accesses", json={"name": "Telemóvel loja", "store_id": store, "folder_id": "root", "pin": pin, **changes})
    assert r.status_code == 200, r.text
    return r.json()


def pair(phone, code):
    return phone.post("/api/quick-scan/pair", headers={"X-Quick-Scan-Pairing": code})


def paired(client, phone, store):
    created = new_access(client, store)
    r = pair(phone, created["pairing"]["code"])
    assert r.status_code == 200, r.text
    return created["access"], r.json()["device_token"]


def login(phone, device, pin=PIN):
    return phone.post("/api/quick-scan/session", json={"pin": pin}, headers={"X-Quick-Scan-Device": device})


def bearer(token):
    return {"Authorization": "Bearer " + token}


def generate(phone, token, pages=None, key=None):
    pages = pages or [({"corners": CORNERS, "colour": "bw"}, photo("white")), ({"colour": "grey"}, photo("blue", (300, 1200)))]
    payload = {"request_key": key or str(uuid4()), "edits": [edit for edit, _ in pages]}
    files = [("files", (f"p{i}.jpg", raw, "image/jpeg")) for i, (_, raw) in enumerate(pages)]
    return phone.post("/api/quick-scan/prepare", data={"payload": json.dumps(payload)}, files=files, headers=bearer(token))


def test_admin_configures_pairing_pin_scan_and_cloud_upload(quick):
    client, phone, factory, store, drive = quick
    assert client.post("/api/quick-scan/accesses", json={"name": "Telemóvel", "store_id": store, "folder_id": "root", "pin": "123456"}).status_code == 422
    assert client.post("/api/quick-scan/accesses", json={"name": "Telemóvel", "store_id": store, "folder_id": "root", "pin": "12345"}).status_code == 422
    assert client.post("/api/quick-scan/accesses", json={"name": "Telemóvel", "store_id": store, "folder_id": "outra-pasta", "pin": PIN}).status_code == 422
    created = new_access(client, store)
    code = created["pairing"]["code"]
    with factory() as db:
        item = db.get(QuickScanAccess, created["access"]["id"])
        # O PIN nunca fica em texto simples, nem na auditoria.
        assert PIN not in item.pin_hash and item.pin_hash.startswith("$argon2")
        assert all(PIN not in a.detail for a in db.scalars(select(Audit)))
    listing = client.get("/api/quick-scan/accesses").json()
    assert listing["folders"][0]["id"] == "root" and listing["accesses"][0]["pairing_pending"]
    assert pair(phone, code[:-2] + "xx").status_code == 401
    r = pair(phone, code)
    assert r.status_code == 200, r.text
    device = r.json()["device_token"]
    assert pair(phone, code).status_code == 401  # uso único
    info = phone.get("/api/quick-scan/device", headers={"X-Quick-Scan-Device": device}).json()
    assert info["store"] == "Teste" and info["company"] == "Empresa Demo"
    wrong = login(phone, device, "999111")
    assert wrong.status_code == 401 and "Restam 4" in wrong.json()["detail"]
    r = login(phone, device)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    context = phone.get("/api/quick-scan/context", headers=bearer(token)).json()
    assert [t["id"] for t in context["types"]] == ["fatura", "despesa", "comprovativo", "caixa", "outro"]
    r = generate(phone, token)
    assert r.status_code == 200, r.text
    scan = r.json()
    assert scan["pages"] == 2 and scan["size"] > 0 and not scan["cloud_saved"]
    raw = phone.get(f"/api/quick-scan/scans/{scan['id']}/pdf", headers=bearer(token)).content
    pages = PdfReader(io.BytesIO(raw)).pages
    # Talão estreito fica com 80 mm de largura; a página normal fica A4 de largura.
    assert round(float(pages[0].mediabox.width)) == 595 and round(float(pages[1].mediabox.width)) == 227
    r = phone.post(f"/api/quick-scan/scans/{scan['id']}/upload", json={"document_type": "fatura", "name": "Fatura Alfa", "description": "Gás"}, headers=bearer(token))
    assert r.status_code == 200, r.text
    assert r.json()["cloud_saved"] and r.json()["name"] == "Fatura_Alfa.pdf"
    again = phone.post(f"/api/quick-scan/scans/{scan['id']}/upload", json={"document_type": "outro", "name": "x"}, headers=bearer(token))
    assert again.status_code == 200 and again.json()["name"] == "Fatura_Alfa.pdf"
    assert drive.uploads == 1
    saved = drive.files[next(iter(drive.contents))]
    assert saved["name"] == "Fatura_Alfa.pdf"
    # Organização das Configurações (por omissão Empresa / Contabilidade / Ano / Mês / Tipo) dentro da pasta do acesso.
    chain = folder_chain(drive, saved)
    today = quick_scan.lisbon_today().isoformat()
    assert chain == ["Empresa_Demo_demo-com", "Contabilidade", today[:4], today[5:7], "Fatura_de_fornecedor"], chain
    with factory() as db:
        row = db.get(DocumentScan, scan["id"])
        details = json.loads(row.data)
        assert row.origin == "digitalizacao_rapida" and row.status == "cloud_saved" and row.drive_id and row.pdf_ciphertext is None
        assert details["quick"]["type"] == "fatura" and details["quick"]["access_name"] == "Telemóvel loja"
        # Sem compras, pagamentos ou stock automáticos.
        assert db.scalar(select(func.count()).select_from(Purchase)) == 0
        actions = {a.action for a in db.scalars(select(Audit))}
        assert {"digitalizacao_rapida_sessao", "digitalizacao_rapida_pdf_gerado", "digitalizacao_rapida_guardada"} <= actions
    recent = client.get("/api/quick-scan/accesses").json()["recent"]
    assert recent[0]["type"] == "Fatura de fornecedor" and recent[0]["access"] == "Telemóvel loja"
    # A área normal não reenvia nem cria compras a partir de digitalizações rápidas.
    assert client.post(f"/api/scans/{scan['id']}/upload", json={"confirmed": True}).status_code == 200
    assert phone.post("/api/quick-scan/session/end", headers=bearer(token)).status_code == 200
    assert phone.get("/api/quick-scan/context", headers=bearer(token)).status_code == 401


def test_names_never_collide_and_suggested_name(quick):
    client, phone, factory, store, drive = quick
    _, device = paired(client, phone, store)
    token = login(phone, device).json()["token"]
    first, second = generate(phone, token).json(), generate(phone, token).json()
    a = phone.post(f"/api/quick-scan/scans/{first['id']}/upload", json={"document_type": "despesa", "name": "Recibo"}, headers=bearer(token)).json()
    b = phone.post(f"/api/quick-scan/scans/{second['id']}/upload", json={"document_type": "despesa", "name": "Recibo.pdf"}, headers=bearer(token)).json()
    assert a["name"] == "Recibo.pdf" and b["name"] == f"Recibo_{second['id'][:8]}.pdf"
    third = generate(phone, token).json()
    assert third["name"].endswith(f"_outro_{third['id'][:8]}.pdf")
    c = phone.post(f"/api/quick-scan/scans/{third['id']}/upload", json={"document_type": "comprovativo", "name": ""}, headers=bearer(token)).json()
    assert c["name"].endswith(f"_comprovativo_{third['id'][:8]}.pdf")


def test_wrong_pin_progressive_lockout_and_admin_unlock(quick):
    client, phone, factory, store, _ = quick
    item, device = paired(client, phone, store)
    for _ in range(4):
        assert login(phone, device, "000111").status_code == 401
    locked = login(phone, device, "000111")
    assert locked.status_code == 429 and "1 minuto" in locked.json()["detail"]
    # Bloqueado: nem o PIN correto entra.
    assert login(phone, device).status_code == 429
    with factory.begin() as db:
        db.get(QuickScanAccess, item["id"]).locked_until -= timedelta(minutes=2)
    for _ in range(4):
        assert login(phone, device, "000111").status_code == 401
    assert "5 minuto" in login(phone, device, "000111").json()["detail"]
    assert client.get("/api/quick-scan/accesses").json()["accesses"][0]["locked_until"]
    assert client.post(f"/api/quick-scan/accesses/{item['id']}/unlock").status_code == 200
    assert login(phone, device).status_code == 200


def test_session_expires_by_inactivity_and_revocation_blocks_device(quick):
    client, phone, factory, store, _ = quick
    item, device = paired(client, phone, store)
    token = login(phone, device).json()["token"]
    with factory.begin() as db:
        row = db.scalar(select(QuickScanSession))
        row.last_seen_at -= timedelta(minutes=11)
    expired = phone.get("/api/quick-scan/context", headers=bearer(token))
    assert expired.status_code == 401 and "PIN" in expired.json()["detail"]
    token = login(phone, device).json()["token"]
    scan = generate(phone, token).json()
    # Desativar termina a sessão de imediato, incluindo envios.
    edit = {"name": "Telemóvel loja", "store_id": store, "folder_id": "root", "active": False}
    assert client.put(f"/api/quick-scan/accesses/{item['id']}", json=edit).status_code == 200
    assert phone.post(f"/api/quick-scan/scans/{scan['id']}/upload", json={"document_type": "outro"}, headers=bearer(token)).status_code == 401
    assert login(phone, device).status_code == 401
    assert client.put(f"/api/quick-scan/accesses/{item['id']}", json={**edit, "active": True}).status_code == 200
    token = login(phone, device).json()["token"]
    assert client.post(f"/api/quick-scan/accesses/{item['id']}/revoke").status_code == 200
    assert phone.get("/api/quick-scan/context", headers=bearer(token)).status_code == 401
    assert phone.get("/api/quick-scan/device", headers={"X-Quick-Scan-Device": device}).status_code == 401
    assert client.post(f"/api/quick-scan/accesses/{item['id']}/pairing").status_code == 409


def test_new_pairing_replaces_device_and_quick_session_has_no_other_access(quick):
    client, phone, factory, store, _ = quick
    item, device = paired(client, phone, store)
    token = login(phone, device).json()["token"]
    for path in ("/api/bootstrap", "/api/scans/setup", "/api/quick-scan/accesses", "/api/purchases"):
        assert phone.get(path, headers=bearer(token)).status_code in (401, 404), path
    assert phone.get("/api/quick-scan/scans/xyz/pdf", headers=bearer(token)).status_code == 404
    # A sessão do administrador (cookie) não serve para o acesso rápido.
    assert client.get("/api/quick-scan/context").status_code == 401
    code = client.post(f"/api/quick-scan/accesses/{item['id']}/pairing").json()["code"]
    assert phone.get("/api/quick-scan/context", headers=bearer(token)).status_code == 401
    assert phone.get("/api/quick-scan/device", headers={"X-Quick-Scan-Device": device}).status_code == 401
    other = pair(phone, code).json()["device_token"]
    assert login(phone, other).status_code == 200
    # Só o administrador configura acessos.
    assert client.post("/api/auth/logout").status_code == 200
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/quick-scan/accesses").status_code == 403


def test_network_failure_retry_without_duplicate_and_cloud_errors(quick, monkeypatch):
    client, phone, factory, store, drive = quick
    _, device = paired(client, phone, store)
    token = login(phone, device).json()["token"]
    scan = generate(phone, token).json()
    drive.fail = True
    failed = phone.post(f"/api/quick-scan/scans/{scan['id']}/upload", json={"document_type": "caixa"}, headers=bearer(token))
    assert failed.status_code == 502 and "não cria duplicados" in failed.json()["detail"]
    # O PDF continua disponível para descarregar enquanto se repete.
    assert phone.get(f"/api/quick-scan/scans/{scan['id']}/pdf", headers=bearer(token)).status_code == 200
    retry = phone.post(f"/api/quick-scan/scans/{scan['id']}/upload", json={"document_type": "caixa"}, headers=bearer(token))
    assert retry.status_code == 200 and retry.json()["cloud_saved"] and drive.uploads == 1
    # Repetir a geração com a mesma referência devolve o mesmo documento.
    key = str(uuid4())
    first = generate(phone, token, key=key).json()
    assert generate(phone, token, key=key).json()["id"] == first["id"]
    drive.writable = False
    folder = phone.post(f"/api/quick-scan/scans/{first['id']}/upload", json={"document_type": "outro"}, headers=bearer(token))
    assert folder.status_code == 503 and "pasta de destino" in folder.json()["detail"]
    drive.writable = True

    def expired(db):
        raise HTTPException(409, "Volte a ligar a conta Google.")
    monkeypatch.setattr("app.quick_scan.authorized_client", expired)
    auth = phone.post(f"/api/quick-scan/scans/{first['id']}/upload", json={"document_type": "outro"}, headers=bearer(token))
    assert auth.status_code == 503 and "autorização expirada" in auth.json()["detail"] and "token" not in auth.json()["detail"].lower()
    with factory() as db:
        assert db.get(DocumentScan, first["id"]).status == "upload_error"


def test_page_validation_limits_and_modes(quick):
    client, phone, factory, store, _ = quick
    _, device = paired(client, phone, store)
    token = login(phone, device).json()["token"]
    bad = generate(phone, token, pages=[({"corners": [[0.9, 0.1], [0.1, 0.1], [0.1, 0.9], [0.9, 0.9]]}, photo())])
    assert bad.status_code == 422
    assert generate(phone, token, pages=[({}, b"not an image")]).status_code == 422
    pdf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(pdf, "PDF")
    assert generate(phone, token, pages=[({}, pdf.getvalue())]).status_code == 422
    with factory.begin() as db:
        from app.models import DocumentScanSettings
        db.add(DocumentScanSettings(id="default", max_pages=1))
    assert generate(phone, token).status_code == 422
    for mode in ("colour", "enhanced", "grey", "bw"):
        r = phone.post("/api/quick-scan/preview", data={"edit": json.dumps({"colour": mode, "rotation": 90, "corners": CORNERS})},
                       files={"file": ("a.jpg", photo(size=(800, 600)), "image/jpeg")}, headers=bearer(token))
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
        image = Image.open(io.BytesIO(r.content))
        assert image.height > image.width  # rodada 90°


def test_company_isolation_with_identifier_not_pin(platform):  # noqa: F811
    client, core = platform
    quick_scan.ip_attempts.clear()
    a, b = create(client, "rapida-empresa-a"), create(client, "rapida-empresa-b")
    client.cookies.clear()
    boot = enter(client, a["slug"])
    with core() as db:
        schema = db.get(Company, a["id"]).schema_name
    factory = sessionmaker(database.engine.execution_options(schema_translate_map={None: schema}), expire_on_commit=False)
    with factory.begin() as db:
        db.add(AttachmentIntegrationConfig(id="default", google_email="a@example.pt", drive_folder="pastaempresaA1"))
    r = client.post("/api/quick-scan/accesses", json={"name": "Tablet", "store_id": boot["stores"][0]["id"], "folder_id": "pastaempresaA1", "pin": PIN})
    assert r.status_code == 200, r.text
    code = r.json()["pairing"]["code"]
    with TestClient(app) as phone:
        forged = code.replace(a["id"], b["id"])
        assert pair(phone, forged).status_code == 401
        device = pair(phone, code).json()["device_token"]
        token = login(phone, device).json()["token"]
        assert phone.get("/api/quick-scan/context", headers=bearer(token)).status_code == 200
        assert phone.get("/api/quick-scan/device", headers={"X-Quick-Scan-Device": device.replace(a["id"], b["id"])}).status_code == 401
        assert login(phone, device.replace(a["id"], b["id"])).status_code == 401
        assert phone.get("/api/quick-scan/context", headers=bearer(token.replace(a["id"], b["id"]))).status_code == 401
        # Sem token, o PIN sozinho não identifica empresa nenhuma.
        assert phone.post("/api/quick-scan/session", json={"pin": PIN}).status_code == 401


def test_pairing_qr_is_readable_and_admin_only(quick):
    import zxingcpp
    client, phone, factory, store, _ = quick
    code = new_access(client, store)["pairing"]["code"]
    link = "https://iazela.example/?digitalizar=" + code
    r = client.post("/api/quick-scan/qr", json={"text": link})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert phone.post("/api/quick-scan/qr", json={"text": link}).status_code == 401
    assert client.post("/api/quick-scan/qr", json={"text": "https://outro.example/"}).status_code == 422
    image = zxingcpp.create_barcode(link, zxingcpp.BarcodeFormat.QRCode).to_image(scale=4)
    assert zxingcpp.read_barcodes(image)[0].text == link



def folder_chain(drive, item):
    """Nomes das subpastas entre a pasta do acesso («root») e o ficheiro."""
    chain, parent = [], item["parents"][0]
    while parent != "root":
        chain.insert(0, drive.files[parent]["name"])
        parent = drive.files[parent]["parents"][0]
    return chain


def fiscal_photo():
    """Página com o QR fiscal (código AT) de uma fatura, como numa fotografia."""
    import zxingcpp
    from PIL import Image
    code = zxingcpp.create_barcode("A:503916099*B:510774318*C:PT*D:FT*E:N*F:20261020*G:FT FA.2026/264994*H:J6N7FD2P-264994*I1:PT*I7:110.65*I8:25.45*N:25.45*O:136.10*Q:abcd*R:0030", zxingcpp.BarcodeFormat.QRCode)
    view = memoryview(code.to_image(scale=6))
    qr = Image.frombytes("L", (view.shape[1], view.shape[0]), view.tobytes())
    page = Image.new("RGB", (1240, 1754), "white")
    page.paste(qr.convert("RGB"), (800, 1300))
    buffer = io.BytesIO()
    page.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def test_name_with_supplier_and_date_from_fiscal_qr_and_organised_folders(quick):
    client, phone, factory, store, drive = quick
    from app.models import BusinessEntity, DocumentScanSettings
    with factory.begin() as db:
        db.add(BusinessEntity(name="Farmoplex Produtos Naturais, Lda.", tax_number="503916099"))
        db.add(DocumentScanSettings(id="default", organisation="period_type"))
    _, device = paired(client, phone, store)
    token = login(phone, device).json()["token"]
    scan = generate(phone, token, pages=[({"colour": "enhanced"}, fiscal_photo())]).json()
    assert scan["suggested_name"] == "2026-10-20_Farmoplex_Produtos_Naturais_FT_FA.2026_264994"
    assert scan["supplier"] == "Farmoplex Produtos Naturais, Lda." and scan["supplier_nif"] == "503916099"
    assert scan["document"] == "FT FA.2026/264994" and scan["document_date"] == "2026-10-20"
    saved = phone.post(f"/api/quick-scan/scans/{scan['id']}/upload", json={"document_type": "fatura", "name": scan["suggested_name"]}, headers=bearer(token))
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == "2026-10-20_Farmoplex_Produtos_Naturais_FT_FA.2026_264994.pdf"
    # Ano / Mês / Tipo pela data do documento (do QR), não pela data de hoje.
    chain = folder_chain(drive, drive.files[next(iter(drive.contents))])
    assert chain == ["2026", "10", "Fatura_de_fornecedor"], chain
    # Sem QR legível: data de hoje e tipo, como antes.
    plain = generate(phone, token).json()
    assert plain["suggested_name"] == "" and not plain["supplier_nif"]
