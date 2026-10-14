"""Exercise reconciliation in an isolated PostgreSQL schema and local browser."""
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from playwright.sync_api import expect, sync_playwright
from pwdlib import PasswordHash
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
load_dotenv(root / ".env")
if os.getenv("APP_MODE") != "demonstracao":
    raise RuntimeError("Execute testes apenas na base de demonstração.")
from app.models import Base, ERPDocument, BankAccount, BankTransaction, Register, Store, User, WintouchSaleRecord
from app.platform_models import Company, PlatformBase

engine = create_engine(os.environ["DATABASE_URL"])
schema = "ui_google_invoices_" + uuid4().hex
with engine.begin() as connection:
    connection.execute(text("CREATE SCHEMA " + schema))
url = engine.url.update_query_dict({"options": "-csearch_path=" + schema})
isolated = create_engine(url)
Base.metadata.create_all(isolated)
PlatformBase.metadata.create_all(isolated)
password = secrets.token_urlsafe(24)
with Session(isolated) as db:
    store = Store(name="Loja de teste")
    user = User(name="Teste UI", email="ui@gpgest.local", role="administrador", password_hash=PasswordHash.recommended().hash(password))
    db.add_all([store, user]); db.flush()
    db.add(Register(store_id=store.id, name="Caixa UI"))
    db.add(Company(name="Empresa UI", nif="500000000", slug="demo", schema_name="public", mode="demonstracao", api_enterprise="ui-source"))
    account = BankAccount(store_id=store.id, name="Conta UI", bank="BPI", iban_last4="1234")
    db.add(account); db.flush()
    bank = BankTransaction(account_id=account.id, movement_date="2026-10-10", value_date="2026-10-10", description="Crédito TPA teste",
        amount_cents=-4000, balance_cents=9800, fingerprint="ui-bank", source_account="", exported_at="", source_position=1)
    db.add(bank)
    for i, value in enumerate((6000, 4000)):
        db.add(WintouchSaleRecord(enterprise_id="ui-source", external_id=f"payment:{i}", store_id=store.id, kind="real",
            document_date="2026-10-10", data=json.dumps({"amount_cents": value, "direction": 1, "payment": "Visa", "payment_code": "CD",
                "entity": f"Cliente UI {i}", "document": f"FT UI/{i}", "occurred_at": "2026-10-10T10:00:00"})))
    db.add(ERPDocument(store_id=store.id, enterprise_id="ui-source", external_id="purchase:1", supplier_nif="500000000", supplier_name="Mercadorias", document="FT DC/1", internal_document="FTCOMPRA A/1", document_type="FT", document_date="2026-10-10", amount_cents=12300))
    db.commit()
from app.models import AttachmentIntegrationConfig, GoogleConnection, Purchase
with Session(isolated) as db:
    store = db.scalar(__import__("sqlalchemy").select(Store))
    db.add(AttachmentIntegrationConfig(id="default", google_email="mail@gmail.com", drive_folder="folder123456789", mail_folder="inbox"))
    db.add(GoogleConnection(id="default", email="mail@gmail.com", token_ciphertext="fake-test-token"))
    db.add(Purchase(store_id=store.id, supplier_name="Fornecedor Alfa", supplier_nif="510774318", document="FT ABC/1", document_date="2026-10-10", amount_cents=12300))
    db.commit()
log = (root / ".local" / "google-invoices-ui-backend.log").open("w")
server_code = "from app import db\nfrom app.main import app\nimport sys\nsys.path.insert(0, 'tests')\nfrom test_google_invoices import GoogleAPI\nfrom app import google_invoices, drive_documents\nfrom app.models import AttachmentIntegrationConfig\napi = GoogleAPI(b'%PDF-1.7 fake UI PDF')\ngoogle_invoices.authorized_client = lambda session: (api, session.get(AttachmentIntegrationConfig, 'default'))\ndrive_documents.authorized_client = google_invoices.authorized_client\nimport uvicorn\ndb.next_platform_session = db.SessionLocal\ndef core():\n    with db.SessionLocal() as session:\n        yield session\napp.dependency_overrides[db.platform_db] = core\nuvicorn.run(app, host='127.0.0.1', port=8002, access_log=False)"
server = subprocess.Popen([str(root / ".venv" / "Scripts" / "python.exe"), "-c", server_code], cwd=root / "backend",
    env={**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False), "APP_MODE": "demonstracao"}, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
try:
    for _ in range(60):
        try:
            if httpx.get("http://127.0.0.1:8002/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    else:
        raise RuntimeError("UI test server unavailable")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("**/api/**", lambda route: route.fulfill(response=route.fetch(url=route.request.url.replace("127.0.0.1:5174", "127.0.0.1:8002"))))
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:5174")
        page.get_by_label("NIF da empresa", exact=True).fill("500000000")
        page.get_by_label("Email", exact=True).fill("ui@gpgest.local")
        page.get_by_label("Palavra-passe", exact=True).fill(password)
        page.get_by_role("button", name="Entrar", exact=True).click()
        page.get_by_role("button", name="Contabilidade", exact=True).click()
        page.get_by_role("button", name="Confer\u00eancia documental", exact=True).click()
        page.get_by_role("button", name="Anexos Gmail / Drive", exact=True).click()
        page.get_by_label("Emails desde").fill("2026-10-01")
        page.get_by_label("Emails at\u00e9").fill("2026-10-31")
        page.get_by_role("button", name="Consultar emails", exact=True).click()
        expect(page.get_by_role("button", name="Guardar selecionados no Drive (0)")).to_be_disabled()
        page.get_by_label("Selecionar FT-ABC-1.pdf").check()
        page.get_by_role("button", name="Guardar selecionados no Drive (1)").click()
        expect(page.get_by_role("button", name="Associar \u00e0 fatura")).to_be_visible()
        page.get_by_role("button", name="Associar \u00e0 fatura").click()
        dialog = page.get_by_role("dialog")
        expect(dialog.get_by_text("Confian\u00e7a:", exact=False)).to_be_visible()
        dialog.get_by_label("Fatura a associar").select_option(index=1)
        assert not dialog.locator('input[name="confirmed"]').is_checked()
        dialog.locator('input[name="confirmed"]').check()
        dialog.get_by_role("button", name="Confirmar associa\u00e7\u00e3o").click()
        expect(page.get_by_text("Associado \u00e0 fatura", exact=True)).to_be_visible()
        page.screenshot(path=str(root / ".local" / "google-invoices-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(root / ".local" / "google-invoices-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="Voltar \u00e0 confer\u00eancia documental", exact=True).click()
        page.get_by_label("Visualizar anexo de FT ABC/1", exact=True).click()
        expect(page.locator('iframe[title="Documento FT ABC/1"]')).to_be_visible()
        page.get_by_role("button", name="Fechar pré-visualização", exact=True).click()
        page.get_by_role("button", name="Clara · Verificar documentos", exact=True).click()
        picker = page.get_by_role("dialog", name="Confer\u00eancia de documentos", exact=True)
        csv = "Setor;Emitente;N\u00ba Fatura / ATCUD;Tipo;Data Emiss\u00e3o;Total;IVA;Base Tribut\u00e1vel\nGeral;510774318 - Fornecedor Alfa;FT ABC/1 / TEST-CODE;FT;10/10/2026;123,00;0,00;123,00\n".encode("utf-8-sig")
        picker.locator('input[type="file"]').set_input_files({"name": "e-fatura.csv", "mimeType": "text/csv", "buffer": csv})
        picker.get_by_role("button", name="Pr\u00e9-visualizar ficheiro", exact=True).click()
        expect(picker.get_by_role("button", name="Confirmar importa\u00e7\u00e3o e continuar", exact=True)).to_be_enabled()
        picker.get_by_role("button", name="Confirmar importa\u00e7\u00e3o e continuar", exact=True).click()
        picker.get_by_role("button", name="Procurar correspondências automaticamente", exact=True).click()
        expect(picker.get_by_text("Já associado", exact=True)).to_be_visible()
        picker.get_by_label("Visualizar anexo de FT ABC/1", exact=True).click()
        expect(picker.locator('iframe[title="Documento FT ABC/1"]')).to_be_visible()
        picker.get_by_role("button", name="Fechar pré-visualização", exact=True).click()
        picker.get_by_role("button", name="Continuar para correspondência ERP", exact=True).click()
        picker.get_by_role("button", name="Procurar correspondências no catálogo ERP", exact=True).click()
        expect(picker.get_by_role("button", name="Rever documento e proposta de lançamento", exact=True)).to_be_visible()
        picker.get_by_role("button", name="Voltar aos anexos Drive", exact=True).click()
        picker.get_by_label("Conferência individual (opcional)", exact=True).select_option(index=1)
        picker.get_by_role("button", name="Conferir caso individual", exact=True).click()
        assistant = page.get_by_role("dialog", name="Clara · Conferência documental", exact=True)
        assistant.get_by_role("button", name="Procurar anexos na pasta Drive", exact=True).click()
        assistant.get_by_role("button", name="Analisar e selecionar", exact=True).click()
        assistant.locator('input[name="confirmed"]').check()
        assistant.get_by_role("button", name="Confirmar refer\u00eancia ao anexo", exact=True).click()
        expect(assistant.get_by_text("Refer\u00eancia ao anexo guardada.", exact=False)).to_be_visible()
        assistant.get_by_role("button", name="Continuar para fornecedor", exact=True).click()
        expect(assistant.get_by_label("Nome do fornecedor", exact=True)).to_be_visible()
        assistant.locator('input[name="confirmed"]').check()
        assistant.get_by_role("button", name="Criar fornecedor", exact=True).click()
        expect(assistant.get_by_text("Fornecedor criado e ativo:", exact=False)).to_be_visible()
        assistant.get_by_role("button", name="Continuar para ERP", exact=True).click()
        expect(assistant.get_by_text("Sem correspond\u00eancia no cat\u00e1logo local.", exact=False)).to_be_visible()
        assistant.get_by_role("button", name="Rever e concluir", exact=True).click()
        expect(assistant.get_by_role("button", name="Abrir prepara\u00e7\u00e3o da proposta de despesa", exact=True)).to_be_visible()
        page.screenshot(path=str(root / ".local" / "document-assistant-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(root / ".local" / "document-assistant-mobile.png"), full_page=True)
        assistant.get_by_role("button", name="Concluir assistente", exact=True).click()
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="Configura\u00e7\u00f5es", exact=True).click()
        page.get_by_role("button", name="Desligar conta Google", exact=True).click()
        expect(page.get_by_text("Conta Google ainda n\u00e3o ligada", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Ligar Gmail e Drive", exact=True)).to_be_disabled()
        assert not errors, errors
        browser.close()
    print("Gmail UI passed: scan, selection, Drive copy, suggestions, mandatory confirmation, invoice evidence, desktop/mobile.")
except Exception as error:
    raise RuntimeError(str(error).replace(password, "[hidden]")) from None
finally:
    if server.poll() is None:
        subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    server.wait(timeout=15)
    log.close()
    isolated.dispose()
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
