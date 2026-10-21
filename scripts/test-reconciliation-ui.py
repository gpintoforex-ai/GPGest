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
from app.models import Base, BankAccount, BankTransaction, Register, Store, User, WintouchSaleRecord
from app.platform_models import Company, PlatformBase

engine = create_engine(os.environ["DATABASE_URL"])
schema = "ui_reconciliation_" + uuid4().hex
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
        amount_cents=9800, balance_cents=9800, fingerprint="ui-bank", source_account="", exported_at="", source_position=1)
    db.add(bank)
    for i, value in enumerate((6000, 4000)):
        db.add(WintouchSaleRecord(enterprise_id="ui-source", external_id=f"payment:{i}", store_id=store.id, kind="real",
            document_date="2026-10-10", data=json.dumps({"amount_cents": value, "direction": 1, "payment": "Visa", "payment_code": "CD",
                "entity": f"Cliente UI {i}", "document": f"FT UI/{i}", "occurred_at": "2026-10-10T10:00:00"})))
    db.commit()
log = (root / ".local" / "reconciliation-ui-backend.log").open("w")
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
        finance = page.get_by_role("button", name="Bancos", exact=True)
        if finance.get_attribute("aria-expanded") != "true":
            finance.click()
        page.locator("nav[aria-label='Navegação principal']").get_by_role("button", name="Reconciliação automática", exact=True).click()
        # Por omissão abre nas sugestões; o painel de confirmação só aparece com seleção.
        expect(page.get_by_role("heading", name="Sugestões de correspondência")).to_be_visible()
        expect(page.locator(".reconciliation-confirm")).to_have_count(0)
        page.get_by_role("button", name="Selecionar agrupamento").click()
        expect(page.get_by_role("heading", name="Movimentos bancários (1)")).to_be_visible()
        expect(page.get_by_role("heading", name="Movimentos da aplicação e documentos (2)")).to_be_visible()
        expect(page.locator(".reconciliation-selection-bar")).to_contain_text("3")
        expect(page.get_by_label("Comissão (€)", exact=True)).to_have_value("2.00")
        expect(page.get_by_text("3 movimentos selecionados", exact=False)).to_be_visible()
        page.get_by_role("button", name="Confirmar reconciliação", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("justificação")
        page.get_by_label("Justificação", exact=True).fill("Comissão do comprovativo TPA")
        page.get_by_role("button", name="Confirmar reconciliação", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="Reconciliação confirmada").first).to_be_visible()
        page.get_by_role("button", name="Histórico", exact=True).click()
        expect(page.get_by_text("Comissão do comprovativo TPA", exact=True)).to_be_visible()
        page.get_by_role("button", name="Desfazer", exact=True).click()
        page.get_by_label("Justificação obrigatória", exact=True).fill("Conferência do lote TPA")
        page.get_by_role("button", name="Confirmar", exact=True).click()
        expect(page.get_by_text("Recebimentos de cartão · Desfeita", exact=True)).to_be_visible()
        page.get_by_role("button", name="Movimentos e sugestões", exact=True).click()
        page.get_by_role("button", name="Seleção manual", exact=False).click()
        expect(page.get_by_role("heading", name="Movimentos bancários (1)")).to_be_visible()
        page.locator(".reconciliation-columns").get_by_role("button", name="Ignorar", exact=True).first.click()
        page.get_by_label("Justificação obrigatória", exact=True).fill("Conferência pendente")
        page.get_by_role("button", name="Confirmar", exact=True).click()
        page.get_by_label("Estado", exact=False).select_option("ignorado")
        expect(page.get_by_text("Conferência pendente", exact=True)).to_be_visible()
        page.get_by_role("button", name="Reativar", exact=True).click()
        page.get_by_label("Estado", exact=False).select_option("abertos")
        expect(page.get_by_role("heading", name="Movimentos bancários (1)")).to_be_visible()
        page.screenshot(path=str(root / ".local" / "reconciliation-desktop.png"), full_page=True)
        page.locator(".reconciliation-columns input[type=checkbox]").first.check()
        expect(page.locator(".reconciliation-selection-bar")).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator(".sidebar")).not_to_be_in_viewport()
        page.get_by_label("Comissão (€)", exact=True).scroll_into_view_if_needed()
        commission_box = page.get_by_label("Comissão (€)", exact=True).bounding_box()
        kind_box = page.locator(".reconciliation-confirm select").bounding_box()
        assert commission_box["y"] > kind_box["y"] + kind_box["height"], "Campos devem ficar em linhas distintas no telemóvel."
        page.screenshot(path=str(root / ".local" / "reconciliation-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Layout ultrapassa largura do telemóvel."
        page.get_by_role("button", name="Faturas e documentos", exact=True).click()
        expect(page.get_by_role("button", name="Importar ficheiro", exact=True)).to_be_visible()
        page.get_by_role("button", name="Importar ficheiro", exact=True).click()
        dialog = page.get_by_role("dialog")
        csv = b'NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;IVA\n500000000;Empresa UI;FT UI/1;FT;2026-08-10;100;23\n'
        dialog.get_by_label("Ficheiro de compras").set_input_files({"name": "e-fatura.csv", "mimeType": "text/csv", "buffer": csv})
        dialog.get_by_role("button", name="Pré-visualizar", exact=True).click()
        expect(dialog.get_by_role("button", name="Confirmar importação de 1 compras", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="Confirmar importação de 1 compras", exact=True).click()
        expect(page.get_by_role("status").filter(has_text="1 compras importadas").first).to_be_visible()
        expect(page.get_by_role("cell", name="FT UI/1 Ficheiro", exact=True)).to_be_visible()
        assert not errors, errors
        browser.close()
    print("UI passou: agrupamento de cartão, confirmação, justificação, reversão, ignorar/reativar, importação de faturas e layout móvel.")
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
