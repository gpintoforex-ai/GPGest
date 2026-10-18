"""Interaction regression through the real UI, in a disposable local database only."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from playwright.sync_api import expect, sync_playwright
from pwdlib import PasswordHash
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
settings = dotenv_values(root / ".local/test-cash.env", interpolate=False)
url = make_url(settings["DATABASE_URL"])
assert (
    url.host == "127.0.0.1" and url.port == 55432 and url.database.startswith("gpgest_test_cash_")
)
sys.path.insert(0, str(root / "backend"))
from app.models import (  # noqa: E402
    Base,
    Category,
    Register,
    Store,
    User,
)
from app.platform_models import Company, PlatformBase  # noqa: E402

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
    from datetime import date

    from app.models import PlannedMovement

    company = Company(
        name="Empresa fictícia UI",
        slug="setup-ui",
        nif="500000001",
        schema_name="public",
        mode="demonstracao",
        operating_mode="local",
    )
    user = User(
        name="Administrador fictício",
        email="setup@ui.test",
        role="administrador",
        password_hash=PasswordHash.recommended().hash(password),
    )
    store = Store(name="Loja de testes")
    db.add_all([company, user, store])
    db.flush()
    register = Register(name="Caixa principal", store_id=store.id)
    category = Category(name="Vendas")
    from app.models import CompanyFiscalSettings

    db.add_all([register, category, CompanyFiscalSettings(id="default", vat_period="mensal")])
    db.flush()
    db.add(
        PlannedMovement(
            store_id=store.id,
            category_id=category.id,
            entity="Entidade prevista UI",
            document="Plano UI",
            due_date=date.today().isoformat(),
            amount_cents=1000,
            direction=1,
        )
    )
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
server_code += "uvicorn.run(app, host='127.0.0.1', port=8018)"
server_code = server_code.replace(
    "uvicorn.run(app,",
    """
import asyncio
@app.middleware('http')
async def delay_bank_save(request, call_next):
    if request.method == 'POST' and request.url.path == '/api/banks':
        await asyncio.sleep(.8)
    return await call_next(request)
uvicorn.run(app,""",
)
env = {
    **os.environ,
    **{k: v for k, v in settings.items() if v is not None},
    "DATABASE_URL": test_url.render_as_string(hide_password=False),
    "APP_MODE": "demonstracao",
    "ALLOWED_ORIGINS": "http://127.0.0.1:5174",
    "COOKIE_SECURE": "false",
}
env.pop("GPGEST_ENV_FILE", None)
log = (root / ".local/ui-company-setup-backend.log").open("wb")
server = None
try:
    with socket.socket() as available:
        available.bind(("127.0.0.1", 8018))
    server = subprocess.Popen(
        [sys.executable, "-c", server_code],
        cwd=root / "backend",
        env=env,
        stdout=log,
        stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(80):
        try:
            if httpx.get("http://127.0.0.1:8018/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        raise RuntimeError("A API isolada não iniciou. Consulte o registo privado.")
    with sync_playwright() as p:
        expect.set_options(timeout=30000)
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
        page.set_default_timeout(30000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        output = root / ".local/interactions-ui"
        output.mkdir(exist_ok=True)
        counts = {}
        held_reads = []
        hold_documents = False

        def proxy(route):
            parsed = urlsplit(route.request.url)
            event = route.request.method + " " + parsed.path
            counts[event] = counts.get(event, 0) + 1
            if (
                event == "POST /api/purchases"
                and route.request.post_data_json.get("supplier_name")
                == "Fornecedor de validação UI"
            ):
                route.fulfill(
                    status=422,
                    json={
                        "detail": [
                            {
                                "loc": ["body", "supplier_nif"],
                                "msg": "Confirme o NIF deste fornecedor.",
                            }
                        ]
                    },
                )
                return
            if hold_documents and event == "GET /api/documents":
                held_reads.append(route)
                return
            if event == "POST /api/banks" and route.request.post_data_json.get("name") == "AB":
                route.fulfill(
                    status=422,
                    json={
                        "detail": [
                            {
                                "loc": ["body", "name"],
                                "type": "string_too_short",
                                "ctx": {"min_length": 3},
                            }
                        ]
                    },
                )
                return
            route.fulfill(
                response=route.fetch(
                    url="http://127.0.0.1:8018"
                    + parsed.path
                    + ("?" + parsed.query if parsed.query else "")
                )
            )

        page.route("**/api/**", proxy)
        page.goto("http://127.0.0.1:5174")
        expect(page.get_by_role("heading", name="Entrar na Zela", exact=True)).to_be_visible()
        expect(page).to_have_title("Entrar · Zela")
        expect(page.get_by_role("img", name="Zela", exact=True)).to_be_visible()
        page.screenshot(path=str(output / "zela-login-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "zela-login-mobile.png"))
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_label("NIF da empresa", exact=True).fill("500000001")
        page.get_by_label("Email", exact=True).fill("setup@ui.test")
        page.get_by_label("Palavra-passe", exact=True).fill(password)
        page.get_by_role("button", name="Entrar", exact=True).click()
        expect(page.get_by_role("button", name="Iniciar configuração", exact=True)).to_be_visible()
        expect(page).to_have_title("Visão geral · Zela")
        assert "GPGest" not in page.locator("body").inner_text()
        assert page.locator('.sidebar img[alt="Zela.me"]').first.evaluate(
            "image => image.complete && image.naturalWidth > 0"
        )
        page.screenshot(path=str(output / "zela-dashboard-desktop.png"))
        page.get_by_role("button", name="Recolher menu lateral", exact=True).click()
        expect(page.locator(".brand-compact img")).to_be_visible()
        page.screenshot(path=str(output / "zela-navigation-compact.png"))
        page.get_by_role("button", name="Expandir menu lateral", exact=True).click()

        def navigate(name, group=None):
            if name == "Dashboard":
                name = "Visão geral"
            if group:
                button = page.get_by_role("button", name=group, exact=True).first
                if button.get_attribute("aria-expanded") != "true":
                    button.click()
                scope = page.locator(
                    {
                        "Tabelas": "#tables-submenu",
                        "Tesouraria": "#finance-submenu",
                        "Bancos": "#banks-submenu",
                        "Contabilidade": "#accounting-submenu",
                    }[group]
                )
            else:
                scope = page
            scope.get_by_role("button", name=name, exact=True).click()

        def close_window(dialog):
            expect(dialog.locator(".ui-dialog-dismissbar button")).to_have_count(1)
            expect(dialog.locator(".ui-dialog-dismissbar button")).to_have_attribute(
                "title", "Fechar"
            )
            dialog.locator(".ui-dialog-dismissbar button").click()

        navigate("Bancos", "Tabelas")
        opener = page.get_by_role("button", name="Adicionar banco", exact=True)
        opener.click()
        bank = page.get_by_role("dialog", name="Adicionar banco", exact=True)
        expect(bank.get_by_label("Nome do banco", exact=True)).to_be_focused()
        bank.get_by_label("Nome do banco", exact=True).fill("Banco por guardar")
        box = bank.bounding_box()
        page.mouse.click(max(0, box["x"] - 8), max(0, box["y"] - 8))
        expect(bank).to_be_visible()
        page.screenshot(path=str(output / "bank-desktop.png"))
        page.keyboard.press("Escape")
        discard = page.get_by_role("alertdialog", name="Descartar alterações?", exact=True)
        expect(discard.get_by_role("button", name="Continuar a editar", exact=True)).to_be_focused()
        page.screenshot(path=str(output / "discard-desktop.png"))
        discard.get_by_role("button", name="Continuar a editar", exact=True).click()
        expect(bank.get_by_label("Nome do banco", exact=True)).to_have_value("Banco por guardar")
        close_window(bank)
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        expect(bank).to_have_count(0)
        expect(opener).to_be_focused()
        opener.click()
        bank.get_by_role("button", name="Guardar", exact=True).click()
        expect(bank.get_by_label("Nome do banco", exact=True)).to_have_attribute(
            "aria-invalid", "true"
        )
        expect(bank.get_by_text("Preencha este campo.", exact=True)).to_be_visible()
        bank.get_by_label("Nome do banco", exact=True).fill("Banco de teste")
        # Two synchronous submissions must produce only one backend operation.
        page.evaluate("""() => {
            const form = document.querySelector('dialog[open] form');
            form.requestSubmit(); form.requestSubmit();
            setTimeout(() => {
                const dialog = document.querySelector('dialog[open]');
                window.__savingCheck = { closeDisabled: dialog.querySelector('.ui-close').disabled, inputDisabled: dialog.querySelector('input').matches(':disabled') };
                dialog.dispatchEvent(new Event('cancel', {cancelable:true,bubbles:true}));
                window.__savingCheck.stillOpen = dialog.open;
            }, 50);
        }""")
        expect(bank).to_have_count(0)
        expect(page.get_by_role("status").filter(has_text="Banco adicionado.")).to_be_visible()
        assert counts.get("POST /api/banks") == 1, counts
        assert page.evaluate("window.__savingCheck") == {
            "closeDisabled": True,
            "inputDisabled": True,
            "stillOpen": True,
        }
        opener.click()
        bank.get_by_label("Nome do banco", exact=True).fill("AB")
        bank.get_by_role("button", name="Guardar", exact=True).click()
        expect(bank.get_by_label("Nome do banco", exact=True)).to_have_attribute(
            "aria-invalid", "true"
        )
        expect(bank.get_by_label("Nome do banco", exact=True)).to_have_value("AB")
        expect(bank.get_by_text("Introduza pelo menos 3 caracteres.", exact=True)).to_be_visible()
        close_window(bank)
        discard.get_by_role("button", name="Continuar a editar", exact=True).click()
        bank.get_by_label("Nome do banco", exact=True).fill("Banco corrigido")
        bank.get_by_role("button", name="Guardar", exact=True).click()
        expect(bank).to_have_count(0)
        page.screenshot(path=str(output / "zela-banks-desktop.png"))
        navigate("Operadores / funcionários", "Tabelas")
        page.get_by_role("button", name="Novo funcionário", exact=True).click()
        employee = page.get_by_role("dialog", name="Novo funcionário", exact=True)
        employee.get_by_label("Nome", exact=True).fill("Funcionário por guardar")
        page.keyboard.press("Escape")
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        expect(employee).to_have_count(0)
        navigate("Rubricas", "Tabelas")
        page.locator(".rubric-row").filter(has_text="Vendas").get_by_role(
            "button", name="Remover", exact=True
        ).click()
        rubric = page.get_by_role("dialog", name="Configurar rubricas", exact=True)
        expect(rubric.get_by_role("button", name="Cancelar", exact=True)).to_be_focused()
        rubric.get_by_role("button", name="Cancelar", exact=True).click()
        expect(rubric).to_have_count(0)
        navigate("Entrega à contabilidade", "Contabilidade")
        page.screenshot(path=str(output / "zela-accounting-desktop.png"))
        periods = page.get_by_role("button", name="Encerrar período", exact=True)
        expect(periods.first).to_be_visible()
        available = [button for button in periods.all() if button.is_enabled()]
        assert available, "A empresa fictícia deve ter um período anterior disponível."
        available[0].click()
        accounting = page.get_by_role("dialog", name="Confirmar estado do período", exact=True)
        expect(accounting.get_by_role("button", name="Cancelar", exact=True)).to_be_focused()
        accounting.get_by_role("button", name="Cancelar", exact=True).click()
        expect(accounting).to_have_count(0)
        navigate("Clientes", "Tabelas")
        page.get_by_role("button", name="Criar cliente", exact=True).click()
        entity = page.get_by_role("dialog", name="Criar entidade", exact=True)
        entity.get_by_label("Nome", exact=True).fill("Cliente fictício de interface")
        entity.get_by_role("button", name="Guardar", exact=True).click()
        expect(entity).to_have_count(0)
        customer = page.get_by_role("row").filter(has_text="Cliente fictício de interface")
        expect(customer).to_be_visible()
        customer.get_by_role("button", name="Remover", exact=True).click()
        removal = page.get_by_role("alertdialog", name="Remover entidade da lista?", exact=True)
        expect(removal.get_by_role("button", name="Cancelar", exact=True)).to_be_focused()
        removal.get_by_role("button", name="Cancelar", exact=True).click()
        expect(customer).to_be_visible()
        customer.get_by_role("button", name="Remover", exact=True).click()
        removal.get_by_role("button", name="Remover entidade", exact=True).click()
        expect(customer).to_have_count(0)
        navigate("Digitalizar documento", "Contabilidade")
        page.locator("main").get_by_role("button", name="Digitalizar documento", exact=True).click()
        scanner = page.get_by_role("dialog", name="Digitalizar documento", exact=True)
        scanner.get_by_label("Selecionar fotografias da galeria", exact=True).set_input_files(
            str(root / "frontend/public/euro/50.jpg")
        )
        expect(scanner.locator(".scanner-pages article")).to_have_count(1)
        close_window(scanner)
        discard.get_by_role("button", name="Continuar a editar", exact=True).click()
        expect(scanner.locator(".scanner-pages article")).to_have_count(1)
        close_window(scanner)
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        expect(scanner).to_have_count(0)
        navigate("Movimentos previstos", "Tesouraria")
        page.get_by_role("row").filter(has_text="Plano UI").get_by_role(
            "button", name="Editar", exact=True
        ).click()
        editor = page.locator(".planned-editor")
        editor.get_by_label("Entidade", exact=True).fill("Alteração ainda não guardada")
        editor.get_by_role("button", name="Cancelar", exact=True).click()
        expect(discard).to_be_visible()
        discard.get_by_role("button", name="Continuar a editar", exact=True).click()
        expect(editor.get_by_label("Entidade", exact=True)).to_have_value(
            "Alteração ainda não guardada"
        )
        # Leaving the page uses the same safe confirmation as closing a window.
        navigate("Dashboard")
        expect(discard).to_be_visible()
        discard.get_by_role("button", name="Continuar a editar", exact=True).click()
        expect(editor.get_by_label("Entidade", exact=True)).to_have_value(
            "Alteração ainda não guardada"
        )
        # Changing store also preserves the form when the user keeps editing.
        page.locator('select[aria-label="Filtrar loja"]').select_option(index=1)
        expect(discard).to_be_visible()
        discard.get_by_role("button", name="Continuar a editar", exact=True).click()
        expect(editor.get_by_label("Entidade", exact=True)).to_have_value(
            "Alteração ainda não guardada"
        )
        expect(page.locator('select[aria-label="Filtrar loja"]')).to_have_value("")
        editor.get_by_role("button", name="Cancelar", exact=True).click()
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        expect(editor).to_have_count(0)
        page.get_by_role("row").filter(has_text="Plano UI").get_by_role(
            "button", name="Editar", exact=True
        ).click()
        editor.get_by_label("Entidade", exact=True).fill("Entidade prevista atualizada")
        editor.get_by_role("button", name="Guardar", exact=True).click()
        expect(editor).to_have_count(0)
        expect(page.get_by_role("row").filter(has_text="Plano UI")).to_contain_text(
            "Entidade prevista atualizada"
        )
        page.get_by_role("row").filter(has_text="Plano UI").get_by_role(
            "button", name="Editar", exact=True
        ).click()
        editor.get_by_label("Entidade", exact=True).fill("Rascunho a descartar na navegação")
        navigate("Dashboard")
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        expect(editor).to_have_count(0)
        navigate("Folha de caixa", "Tesouraria")
        page.get_by_role("button", name="Nova folha de caixa", exact=True).click()
        opened = page.get_by_role("dialog", name="Nova folha de caixa", exact=True)
        opened.get_by_label("Caixa", exact=True).select_option(index=1)
        opened.get_by_label("Fundo de maneio inicial (€)", exact=True).fill("40")
        opened.get_by_role("button", name="Guardar", exact=True).click()
        expect(opened).to_have_count(0)
        navigate("Folha de caixa", "Tesouraria")
        sheet = page.get_by_role("region", name="Folha de caixa Caixa principal", exact=True)
        if not sheet.count():
            sheet = page.locator(".local-cash-sheet").first
        sheet.locator(".cash-sheet-details > summary").click()
        for method in ("Numerário", "Multibanco / TPA", "MB WAY", "Transferência", "Outros meios"):
            sheet.get_by_role("button", name="Sem movimento — " + method, exact=True).click()
        page.screenshot(path=str(output / "zela-cash-desktop.png"))
        sheet.get_by_role("button", name="Contar numerário", exact=True).click()
        count = page.get_by_role("dialog", name="Contar numerário", exact=True)
        if not count.count():
            count = page.locator("dialog.cash-count-dialog[open]")
        # Exercise the nested calculator and its own discard confirmation.
        count.get_by_role("button", name="Valor total", exact=True).click()
        count.get_by_label("Valor total de numerário (€)", exact=True).click()
        keypad = page.locator("dialog.numeric-keypad[open]")
        keypad.get_by_role("button", name="4", exact=True).click()
        close_window(keypad)
        expect(discard).to_be_visible()
        discard.get_by_role("button", name="Continuar a editar", exact=True).click()
        keypad.get_by_role("button", name="0", exact=True).click()
        keypad.get_by_role("button", name="Confirmar", exact=True).click()
        count.get_by_role("button", name="Aplicar contagem", exact=True).click()
        sheet.get_by_role("button", name="Fechar caixa", exact=True).click()
        confirmation = page.get_by_role("alertdialog", name="Fechar caixa?", exact=True)
        expect(confirmation.get_by_role("button", name="Cancelar", exact=True)).to_be_focused()
        confirmation.get_by_role("button", name="Cancelar", exact=True).click()
        assert not any(
            key.endswith("/close") and key.startswith("POST /api/sessions/") for key in counts
        )
        sheet.get_by_role("button", name="Fechar caixa", exact=True).click()
        confirmation.get_by_role("button", name="Confirmar e fechar caixa", exact=True).click()
        success = page.get_by_role("dialog", name="Caixa fechada", exact=True)
        close_window(success)
        sheet.get_by_role("button", name="Reabrir", exact=True).click()
        reopen = page.get_by_role("dialog", name="Reabrir caixa", exact=True)
        expect(reopen.get_by_role("button", name="Cancelar", exact=True)).to_be_focused()
        expect(reopen.get_by_text("será reaberta", exact=False)).to_be_visible()
        close_window(reopen)
        # A confirmed upload must finish even if the subsequent list read stalls.
        navigate("Conferência documental", "Contabilidade")
        page.get_by_role("button", name="Registar documento", exact=True).click()
        native_form = page.get_by_role("dialog", name="Conferência de documentos", exact=True)
        native_form.get_by_role("button", name="Guardar documento", exact=True).click()
        nif = native_form.get_by_role("textbox", name="NIF fornecedor", exact=True)
        expect(nif).to_have_attribute("aria-invalid", "true")
        expect(nif).to_have_accessible_description("Preencha este campo.")
        expect(nif).to_be_focused()
        nif.fill("500000000")
        native_form.get_by_role("textbox", name="Fornecedor", exact=True).fill(
            "Fornecedor de validação UI"
        )
        native_form.get_by_role("textbox", name="Documento", exact=True).fill("FT UI/erro")
        native_form.get_by_label("Data de emissão", exact=True).fill("2026-10-17")
        native_form.get_by_role("textbox", name="Total (€)", exact=True).fill("12,30")
        native_form.get_by_role("button", name="Guardar documento", exact=True).click()
        expect(nif).to_have_attribute("aria-invalid", "true")
        expect(nif).to_have_accessible_description("Verifique o valor introduzido.")
        expect(nif).to_have_value("500000000")
        expect(native_form.get_by_role("textbox", name="Documento", exact=True)).to_have_value(
            "FT UI/erro"
        )
        close_window(native_form)
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        page.get_by_role("button", name="Importar e-Fatura", exact=True).click()
        importing = page.get_by_role("dialog", name="Conferência de documentos", exact=True)
        importing.locator('input[type="file"]').set_input_files(
            {
                "name": "e-fatura-ui.csv",
                "mimeType": "text/csv",
                "buffer": b"NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;IVA\n500000000;Fornecedor UI;FT UI/1;FT;2026-10-17;123,00;23,00\n",
            }
        )
        importing.get_by_role("button", name="Pré-visualizar", exact=True).click()
        expect(
            importing.get_by_role("button", name="Confirmar importação", exact=True)
        ).to_be_enabled()
        hold_documents = True
        importing.get_by_role("button", name="Confirmar importação", exact=True).click()
        expect(importing).to_have_count(0)
        expect(
            page.get_by_role("status").filter(has_text="1 documentos importados")
        ).to_be_visible()
        expect(
            page.get_by_text("Importação guardada. A atualizar a lista de documentos…", exact=True)
        ).to_be_visible()
        assert len(held_reads) == 1
        hold_documents = False
        held_reads.pop().fulfill(
            status=503, json={"detail": "Leitura temporariamente indisponível"}
        )
        expect(
            page.get_by_role("alert").filter(has_text="A importação ficou guardada")
        ).to_be_visible()
        # Reopening and previewing the same file proves the write was committed once.
        page.get_by_role("button", name="Importar e-Fatura", exact=True).click()
        importing.locator('input[type="file"]').set_input_files(
            {
                "name": "e-fatura-ui.csv",
                "mimeType": "text/csv",
                "buffer": b"NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;IVA\n500000000;Fornecedor UI;FT UI/1;FT;2026-10-17;123,00;23,00\n",
            }
        )
        importing.get_by_role("button", name="Pré-visualizar", exact=True).click()
        expect(importing.get_by_text("0 novos", exact=False)).to_be_visible()
        close_window(importing)
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        navigate("Dashboard")
        navigate("Conferência documental", "Contabilidade")
        expect(page.get_by_role("row").filter(has_text="FT UI/1")).to_be_visible()
        advanced = page.locator(".doc-advanced-filters")
        advanced.locator("summary").click()
        advanced.get_by_label("Anexos", exact=True).select_option("sem_anexo")
        advanced.locator("summary").click()
        expect(advanced.locator("summary")).to_contain_text("(1)")
        expect(page.get_by_role("row").filter(has_text="FT UI/1")).to_be_visible()
        page.screenshot(path=str(output / "zela-documents-desktop.png"))
        navigate("Extratos", "Bancos")
        expect(
            page.get_by_text("Crie uma conta bancária para importar o extrato.", exact=True)
        ).to_be_visible()
        page.screenshot(path=str(output / "zela-statements-empty.png"))
        navigate("Dashboard")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelector('.sidebar').getBoundingClientRect().right <= 0")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "zela-dashboard-mobile.png"))
        menu = page.locator(".mobile-toggle")
        if menu.count():
            menu.click()
            navigation = page.get_by_role("dialog", name="Menu de navegação", exact=True)
            expect(navigation.get_by_role("button", name="Fechar", exact=True)).to_be_focused()
            page.screenshot(path=str(output / "navigation-mobile.png"))
            page.keyboard.press("Shift+Tab")
            assert navigation.evaluate("el => el.contains(document.activeElement)")
            page.keyboard.press("Escape")
            expect(menu).to_be_focused()
        page.get_by_role(
            "button", name="Iniciar configuração", exact=True
        ).click() if page.get_by_role(
            "button", name="Iniciar configuração", exact=True
        ).is_visible() else None
        wizard = page.get_by_role("dialog", name="Vamos preparar a sua Zela", exact=True)
        if wizard.count():
            expect(wizard.get_by_label("Nome da empresa", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            expect(wizard.locator('button[title="Fechar"]')).to_have_count(1)
            page.screenshot(path=str(output / "wizard-mobile.png"))
            close_window(wizard)
        assert not errors, errors
        page.wait_for_load_state("networkidle")
        page.unroute_all(behavior="ignoreErrors")
        browser.close()
    print(
        "PASS: shared windows, dirty discard/continue, outside click, Escape/focus, field errors, duplicate submit, cash close/reopen and nested calculator; desktop/mobile; isolated database."
    )

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
