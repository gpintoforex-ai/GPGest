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
schema = "ui_document_control_" + uuid4().hex
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
log = (root / ".local" / "document-control-ui-backend.log").open("w")
server = subprocess.Popen([
    str(root / ".venv" / "Scripts" / "python.exe"), "-c",
    "from app import db\nfrom app.main import app\nimport uvicorn\ndb.next_platform_session = db.SessionLocal\ndef core():\n    with db.SessionLocal() as session:\n        yield session\napp.dependency_overrides[db.platform_db] = core\nuvicorn.run(app, host='127.0.0.1', port=8002)",
], cwd=root / "backend", env={**os.environ, "DATABASE_URL": url.render_as_string(hide_password=False), "APP_MODE": "demonstracao"},
    stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
try:
    for _ in range(40):
        try:
            if httpx.get("http://127.0.0.1:8002/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    else:
        raise RuntimeError("Servidor de teste indisponível.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.route("**/api/**", lambda route: route.fulfill(response=route.fetch(url=route.request.url.replace("127.0.0.1:5174", "127.0.0.1:8002"))))
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:5174/entrar")
        page.get_by_label("NIF da empresa", exact=True).fill("500000000")
        page.get_by_label("Email", exact=True).fill("ui@gpgest.local")
        page.get_by_label("Palavra-passe", exact=True).fill(password)
        page.get_by_role("button", name="Entrar", exact=True).click()
        accounting = page.get_by_role("button", name="Contabilidade", exact=True)
        if accounting.get_attribute("aria-expanded") != "true":
            accounting.click()
        expect(page.locator("#finance-submenu").get_by_role("button", name="Conferência documental", exact=True)).to_have_count(0)
        expect(page.locator("#accounting-submenu").get_by_role("button", name="Conferência documental", exact=True)).to_be_visible()
        page.get_by_role("button", name="Conferência documental", exact=True).click()
        expect(page.locator(".header-page-context")).to_contain_text("Contabilidade")
        expect(page.get_by_role("heading", name="Conferência documental", exact=True, level=1)).to_be_visible()
        page.get_by_role("button", name="Importar e-Fatura", exact=True).click()
        dialog = page.get_by_role("dialog")
        csv = 'Setor;Emitente;Nº Fatura / ATCUD;Tipo;Data Emissão;Total;IVA;Base Tributável\nGeral;500000000 - Mercadorias;FT DC/1 / CODE-1;FT;10/10/2026;123,00;23,00;100,00\nGeral;500000001 - Energia;FT DC/2 / CODE-2;FT;10/10/2026;129,00;24,00;105,00\n'.encode("utf-8-sig")
        dialog.get_by_label("Ficheiro e-Fatura").set_input_files({"name": "e-fatura.csv", "mimeType": "text/csv", "buffer": csv})
        dialog.get_by_role("button", name="Pré-visualizar", exact=True).click()
        expect(dialog.get_by_text("2 novos", exact=False)).to_be_visible()
        dialog.get_by_role("button", name="Confirmar importação", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="2 documentos importados").first).to_be_visible(timeout=30000)
        def open_document(number):
            page.locator("tbody tr").filter(has=page.get_by_text(number, exact=True)).get_by_role("button", name="Conferir", exact=True).click()
            expect(dialog.get_by_role("heading", name=number, exact=True)).to_be_visible()
        def area(name):
            # O detalhe está organizado em separadores (Dados e ERP, Original, Despesa, Pagamentos, Histórico).
            dialog.locator(".module-tabs button", has_text=name).first.click()
        open_document("FT DC/1")
        dialog.get_by_label("Documento ERP", exact=True).select_option(index=1)
        dialog.get_by_label("Motivo da associação ou reversão").fill("Fatura conferida com ERP")
        dialog.get_by_role("button", name="Confirmar associação ERP", exact=True).click()
        expect(dialog.get_by_role("button", name="Desfazer associação ERP", exact=True)).to_be_visible()
        area("Original e localização")
        dialog.get_by_label("Disponibilidade do documento", exact=True).select_option("fisico")
        dialog.get_by_label("Localização do papel ou ficheiro").fill("Pasta outubro")
        dialog.get_by_role("button", name="Guardar verificação documental", exact=True).click()
        expect(dialog.get_by_text("Teste UI", exact=False).first).to_be_visible()
        expect(dialog.get_by_label("Disponibilidade do documento")).to_have_value("fisico")
        area("Pagamentos e banco")
        payments_section = dialog.locator("section").filter(has=page.get_by_role("heading", name="Pagamentos associados e banco", exact=True))
        expect(payments_section).to_contain_text("Por pagar")
        expect(payments_section).to_contain_text("Por reconciliar")
        dialog.get_by_role("button", name="Identificar pagamento desta fatura", exact=True).click()
        dialog.get_by_label("Total pago (€)", exact=True).fill("40")
        dialog.get_by_label("Distribuir FT DC/1", exact=True).fill("40")
        dialog.get_by_label("Data do pagamento", exact=True).fill("2026-10-10")
        dialog.get_by_role("button", name="Confirmar pagamento e distribuição", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Pagamento identificado").first).to_be_visible(timeout=30000)
        open_document("FT DC/1")
        area("Pagamentos e banco")
        expect(payments_section).to_contain_text("Parcial")
        expect(payments_section).to_contain_text("83,00")
        expect(payments_section).to_contain_text("Por reconciliar")
        dialog.get_by_role("button", name="Fechar", exact=True).click()
        open_document("FT DC/2")
        area("Despesa e contabilidade")
        dialog.get_by_role("button", name="Preparar proposta de despesa", exact=True).click()
        first_line = dialog.locator("xpath=.//fieldset[legend[normalize-space()='Linha 1']]")
        first_line.get_by_label("Categoria", exact=True).fill("Energia")
        first_line.get_by_label("Base (€)", exact=True).fill("100")
        first_line.get_by_label("IVA (%)", exact=True).fill("23")
        first_line.get_by_label("IVA (€)", exact=True).fill("23")
        dialog.get_by_role("button", name="Adicionar linha / taxa de IVA", exact=True).click()
        second_line = dialog.locator("xpath=.//fieldset[legend[normalize-space()='Linha 2']]")
        for label, value in (("Descrição", "Serviço"), ("Categoria", "Energia"), ("Base (€)", "5"), ("IVA (%)", "20"), ("IVA (€)", "1")):
            second_line.get_by_label(label, exact=True).fill(value)
        dialog.get_by_label("Revisei fornecedor", exact=False).check()
        dialog.get_by_role("button", name="Confirmar proposta revista", exact=True).click()
        expect(dialog.get_by_role("button", name="Exportar proposta revista", exact=True)).to_be_visible()
        with page.expect_download() as download:
            dialog.get_by_role("button", name="Exportar proposta revista", exact=True).click()
        assert download.value.suggested_filename == "proposta-despesa.csv"
        expect(dialog.get_by_text("Proposta exportada", exact=False).first).to_be_visible()
        dialog.get_by_role("button", name="Fechar", exact=True).click()
        page.get_by_role("button", name="Selecionar filtrados (até 200)", exact=True).click()
        page.get_by_role("button", name="Ação em lote (2)", exact=True).click()
        dialog.get_by_role("button", name="Rever documentos afetados", exact=True).click()
        expect(dialog.get_by_text("2 documentos serão alterados", exact=False)).to_be_visible()
        dialog.get_by_role("button", name="Confirmar ação em lote", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Ação em lote registada").first).to_be_visible(timeout=30000)
        advanced = page.locator("details", has=page.locator("summary", has_text="Filtros avançados")).first
        if not advanced.evaluate("d => d.open"):
            advanced.locator("summary").click()
        page.get_by_label("Pagamento", exact=True).select_option("por_pagar")
        expect(page.locator("tbody tr")).to_have_count(1)
        page.get_by_label("Pagamento", exact=True).select_option("")
        page.screenshot(path=str(root / ".local" / "document-control-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Layout exceeds mobile width."
        open_document("FT DC/1")
        area("Original e localização")
        expect(dialog.get_by_label("Disponibilidade do documento")).to_have_value("fisico")
        page.screenshot(path=str(root / ".local" / "document-control-mobile.png"), full_page=True)
        overflow = page.evaluate("""() => { const dialog = document.querySelector('dialog'); const edge = dialog.getBoundingClientRect().right; return [...dialog.querySelectorAll('*')].filter(el => el.getBoundingClientRect().right > edge).map(el => ({tag:el.tagName, text:el.textContent.slice(0,90), width:el.getBoundingClientRect().width})); }""")
        assert page.evaluate("document.querySelector('dialog').scrollWidth <= document.querySelector('dialog').clientWidth"), "Dialog exceeds mobile width: " + str(overflow)
        assert not errors, errors
        browser.close()
    print("Document UI passed: e-Fatura import, ERP association, availability, partial payment, multi-rate draft/export, batch confirmation, independent filters and mobile layout.")
except Exception as error:
    raise RuntimeError(str(error).replace(password, "[oculto]")) from None
finally:
    if server.poll() is None:
        subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    server.wait(timeout=15)
    log.close()
    isolated.dispose()
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
