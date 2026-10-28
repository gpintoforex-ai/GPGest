"""Browser onboarding, with a disposable schema and an optional fake ERP."""
import os
import socket
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from playwright.sync_api import expect, sync_playwright
from pwdlib import PasswordHash
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
settings = dotenv_values(root / ".local/test-cash.env", interpolate=False)
url = make_url(settings["DATABASE_URL"])
assert url.host == "127.0.0.1" and url.port == 55432 and url.database.startswith("gpgest_test_cash_")
sys.path.insert(0, str(root / "backend"))
from app.models import (  # noqa: E402
    AccountBalanceReference,
    BankAccount,
    Base,
    CashSession,
    Category,
    CompanySetup,
    Movement,
    PaymentTerminal,
    Register,
    Store,
    User,
    WintouchCashAccount,
)
from app.platform_models import Company, PlatformBase  # noqa: E402

erp = "--erp" in sys.argv
schema = "ui_setup_" + uuid4().hex
master = create_engine(url)
with master.begin() as connection:
    connection.execute(text("CREATE SCHEMA " + schema))
test_url = url.update_query_dict({"options": "-csearch_path=" + schema})
engine = create_engine(test_url)
Base.metadata.create_all(engine)
PlatformBase.metadata.create_all(engine)
password = "SetupIsolatedBrowser123!"
with Session(engine) as db:
    company = Company(name="Empresa fictícia UI", slug="setup-ui", nif="500000001", schema_name="public", mode="demonstracao", operating_mode="local")
    user = User(name="Administrador fictício", email="setup@ui.test", role="administrador", password_hash=PasswordHash.recommended().hash(password))
    store = Store(name="Loja de testes")
    db.add_all([company, user, store])
    db.flush()
    register = Register(name="Caixa principal", store_id=store.id)
    db.add_all([register, Category(name="Vendas")])
    db.commit()
server_code = """
from sqlalchemy.orm import sessionmaker
from app import db
from app.main import app
import uvicorn
core_factory = sessionmaker(db.engine.execution_options(schema_translate_map={None: SCHEMA}), expire_on_commit=False)
db.next_platform_session = core_factory
def core():
    with core_factory() as session:
        yield session
app.dependency_overrides[db.platform_db] = core
""".replace("SCHEMA", repr(schema))
if erp:
    server_code += """
from app import company_setup
import httpx
class FakeERP:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def get(self, url, params, headers):
        assert headers['Authorization'] == 'ApiKey fictional-setup-ui-key'
        rows = [{'ID':'ui-erp-cash', 'Name':'Caixa ERP de testes', 'Enabled':True}] if url.endswith('/cash_accounts') and not params['skip'] else []
        return httpx.Response(200, json=rows)
company_setup.httpx.Client = FakeERP
"""
server_code += "uvicorn.run(app, host='127.0.0.1', port=8018)"
env = {**os.environ, **{k: v for k, v in settings.items() if v is not None}, "DATABASE_URL": test_url.render_as_string(hide_password=False), "APP_MODE": "demonstracao", "ALLOWED_ORIGINS": "http://127.0.0.1:5174", "COOKIE_SECURE": "false"}
env.pop("GPGEST_ENV_FILE", None)
log = (root / ".local/ui-company-setup-backend.log").open("wb")
server = None
try:
    with socket.socket() as available:
        available.bind(("127.0.0.1", 8018))
    server = subprocess.Popen([sys.executable, "-c", server_code], cwd=root / "backend", env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(80):
        try:
            if httpx.get("http://127.0.0.1:8018/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(.2)
    else:
        raise RuntimeError("A API isolada não iniciou. Consulte o registo privado.")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
        page.set_default_timeout(30000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def proxy(route):
            parsed = urlsplit(route.request.url)
            route.fulfill(response=route.fetch(url="http://127.0.0.1:8018" + parsed.path + ("?" + parsed.query if parsed.query else "")))
        page.route("**/api/**", proxy)
        page.goto("http://127.0.0.1:5174/entrar")
        page.get_by_label("NIF da empresa", exact=True).fill("500000001")
        page.get_by_label("Email", exact=True).fill("setup@ui.test")
        page.get_by_label("Palavra-passe", exact=True).fill(password)
        page.get_by_role("button", name="Entrar", exact=True).click()
        page.get_by_role("button", name="Iniciar configuração", exact=True).click()
        wizard = page.get_by_role("dialog", name="Vamos preparar a sua IAZELA", exact=True)
        expect(wizard.get_by_label("Nome comercial", exact=True)).to_have_value("Empresa fictícia UI")
        wizard.get_by_label("Regime de IVA", exact=True).select_option("pequenos_retalhistas")
        expect(wizard.get_by_label("Periodicidade da declaração de IVA", exact=True)).to_be_disabled()
        wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()
        if erp:
            wizard.get_by_role("radio", name="Ligação a ERP", exact=False).check()
            wizard.get_by_label("Empresa no ERP", exact=True).fill("erp-ui")
            wizard.get_by_label("Chave de acesso ERP", exact=True).fill("fictional-setup-ui-key")
            wizard.get_by_role("button", name="Testar ligação", exact=True).click()
            expect(wizard.get_by_text("Ligação testada com sucesso", exact=True)).to_be_visible()
            wizard.get_by_role("checkbox", name="Confirmo a leitura de dados reais", exact=False).check()
            wizard.get_by_role("button", name="Pré-visualizar importação inicial", exact=True).click()
            expect(wizard.get_by_text("Caixa ERP de testes", exact=True)).to_be_visible()
            wizard.get_by_role("button", name="Confirmar importação dos catálogos", exact=True).click()
            expect(wizard.get_by_text("Catálogos importados.", exact=False)).to_be_visible()
        wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()
        if erp:
            wizard.get_by_label("Caixa ERP associada 1", exact=True).select_option("ui-erp-cash")
        else:
            wizard.get_by_label("Fundo habitual da caixa 1 (EUR)", exact=True).fill("40,00")
            wizard.get_by_label("Saldo inicial da caixa 1 (EUR)", exact=True).fill("75,00")
            wizard.get_by_label("Data de início da caixa 1", exact=True).fill(date.today().isoformat())
        wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()
        if erp:
            wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()
        else:
            for i, suffix in enumerate(("A", "B"), 1):
                wizard.get_by_role("button", name="Adicionar conta bancária", exact=True).click()
                wizard.get_by_label(f"Banco da conta {i}", exact=True).fill("Banco " + suffix)
                wizard.get_by_label(f"Nome da conta {i}", exact=True).fill("Conta " + suffix)
                if i == 1:
                    wizard.get_by_label("IBAN da conta 1", exact=True).fill("PT50000201231234567890154")
                    wizard.get_by_label("Saldo inicial da conta 1 (EUR)", exact=True).fill("100,00")
                    wizard.get_by_label("Data do saldo inicial da conta 1", exact=True).fill(date.today().isoformat())
            wizard.get_by_role("button", name="Guardar e continuar mais tarde", exact=True).click()
            expect(wizard).to_have_count(0)
            page.reload()
            page.get_by_role("button", name="Continuar configuração", exact=True).click()
            expect(wizard.get_by_label("Nome da conta 1", exact=True)).to_have_value("Conta A")
            expect(wizard.get_by_label("Nome da conta 2", exact=True)).to_have_value("Conta B")
            expect(wizard.get_by_label("IBAN da conta 1", exact=True)).to_have_value("")
            wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()
        for name in ("Multibanco / TPA", "MB WAY", "Transferência", "Outros meios"):
            wizard.get_by_role("checkbox", name=name, exact=True).set_checked(not erp and name == "Multibanco / TPA")
        wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()
        if not erp:
            for i, suffix in enumerate(("A", "B"), 1):
                wizard.get_by_role("button", name="Adicionar terminal TPA", exact=True).click()
                wizard.get_by_label(f"Prestador do terminal {i}", exact=True).fill("Prestador distinto " + suffix)
                wizard.get_by_label(f"Conta de liquidação do terminal {i}", exact=True).select_option(label=f"Banco {suffix} · Conta {suffix}")
            wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()
        else:
            wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()
        wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()  # Preferências
        expect(wizard.locator(".setup-step-nav [aria-current=step]")).to_contain_text("Faturação SAF-T")
        wizard.get_by_role("button", name="Guardar e continuar", exact=True).click()  # Faturação SAF-T
        expect(wizard.get_by_role("heading", name="Resumo", exact=True)).to_be_visible()
        expect(wizard.get_by_role("button", name="Guardar e começar", exact=True)).to_be_enabled()
        output = root / ".local/company-setup-ui"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / ("erp-summary-desktop.png" if erp else "local-summary-desktop.png")), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert wizard.bounding_box()["width"] <= 390
        expect(wizard.locator(".setup-step-nav [aria-current=step]")).to_be_in_viewport()
        page.screenshot(path=str(output / ("erp-summary-mobile.png" if erp else "local-summary-mobile.png")), full_page=True)
        wizard.get_by_role("button", name="Guardar e começar", exact=True).click()
        expect(wizard.get_by_role("heading", name="A sua IAZELA está pronta", exact=True)).to_be_visible()
        expect(wizard).not_to_have_attribute("aria-busy", "true")
        saft_button = page.get_by_role("button", name="Importar SAF-T de vendas", exact=True)
        if erp:
            expect(saft_button).to_have_count(0)
            # Com a ligação ativa, as tabelas do ERP ficam por sincronizar, pela ordem das dependências.
            table_sync = wizard.get_by_role("region", name="Sincronizar tabelas com o ERP", exact=True)
            expect(table_sync.locator("li").first).to_contain_text("Prazos de pagamento")
            expect(table_sync.get_by_text("Por sincronizar", exact=True).first).to_be_visible()
            expect(table_sync.get_by_role("button", name="Sincronizar", exact=True).first).to_be_enabled()
            page.screenshot(path=str(output / "erp-table-sync.png"), full_page=True)
        else:
            expect(saft_button).to_be_visible()
            saft_button.click()  # Fecha o assistente e abre a importação.
            expect(page.get_by_role("heading", name="Importar SAF-T de vendas", exact=True)).to_be_visible()
        assert not errors, errors
        page.wait_for_load_state("networkidle")
        page.unroute_all(behavior="ignoreErrors")
        browser.close()
    with Session(engine) as db:
        state = db.get(CompanySetup, "default")
        assert state.completed_at
        assert db.scalar(select(func.count()).select_from(Movement)) == 0
        assert db.scalar(select(func.count()).select_from(CashSession)) == 0
        if erp:
            assert db.scalar(select(Company)).api_active
            assert db.scalar(select(WintouchCashAccount)).register_id == db.scalar(select(Register)).id
            assert "fictional-setup-ui-key" not in state.draft
        else:
            terminals = list(db.scalars(select(PaymentTerminal)))
            assert len(terminals) == 2 and len({t.account_id for t in terminals}) == 2
            assert all(t.commission_cents is None and t.commission_bps is None for t in terminals)
            assert db.scalar(select(func.count()).select_from(AccountBalanceReference)) == 2
            assert db.scalar(select(func.count()).select_from(BankAccount)) == 2
    print("PASS: " + ("ERP test, preview, import, association and completion" if erp else "local setup, saved resume, two terminals with distinct accounts and exact initial references") + "; desktop/mobile; isolated database, no real ERP calls.")
finally:
    if server:
        server.terminate()
        server.wait(timeout=15)
    log.close()
    engine.dispose()
    with master.begin() as connection:
        assert schema.startswith("ui_setup_") and len(schema) == 41
        connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
    master.dispose()
