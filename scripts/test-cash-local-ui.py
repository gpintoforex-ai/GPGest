"""Real browser/API test, exclusively in a disposable local database and schema."""

import os
import re
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
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
settings = dotenv_values(root / ".local/test-cash.env", interpolate=False)
url = make_url(settings["DATABASE_URL"])
assert (
    url.host == "127.0.0.1" and url.port == 55432 and url.database.startswith("gpgest_test_cash_")
)
os.environ.update({key: value for key, value in settings.items() if value is not None})
os.environ.pop("GPGEST_ENV_FILE", None)
sys.path.insert(0, str(root / "backend"))
from app.models import Base, CashSession, Category, Movement, Register, Store, User, now
from app.platform_models import Company, PlatformBase

schema = "ui_cash_" + uuid4().hex
master = create_engine(url)
with master.begin() as connection:
    connection.execute(text("CREATE SCHEMA " + schema))
test_url = url.update_query_dict({"options": "-csearch_path=" + schema})
engine = create_engine(test_url)
Base.metadata.create_all(engine)
PlatformBase.metadata.create_all(engine)
password = "PasswordForIsolatedBrowser123!"
with Session(engine) as db:
    store = Store(name="Loja de demonstração")
    user = User(
        name="Utilizador de demonstração",
        email="ui@gpgest.local",
        role="administrador",
        password_hash=PasswordHash.recommended().hash(password),
    )
    db.add_all(
        [
            store,
            user,
            Company(
                name="Empresa UI",
                slug="demo",
                nif="500000001",
                schema_name="public",
                mode="demonstracao",
                operating_mode="local",
            ),
        ]
    )
    db.flush()
    register = Register(name="Caixa de demonstração", store_id=store.id)
    category = Category(name="Vendas")
    db.add_all([register, category])
    db.flush()
    cash = CashSession(
        register_id=register.id, operator_id=user.id, opening_cents=10000, count_data="{}"
    )
    db.add(cash)
    db.flush()
    cash_session_id = cash.id
    for method, kind, value, direction in [
        ("numerario", "venda", 30000, 1),
        ("numerario", "despesa", 5000, -1),
        ("cartao", "venda", 98000, 1),
    ]:
        db.add(
            Movement(
                session_id=cash.id,
                user_id=user.id,
                category_id=category.id,
                payment=method,
                kind=kind,
                amount_cents=value,
                direction=direction,
                occurred_at=now(),
                request_key=str(uuid4()),
            )
        )
    db.commit()
server_code = "from app import db\nfrom app.main import app\nimport uvicorn\ndb.next_platform_session = db.SessionLocal\ndef core():\n    with db.SessionLocal() as session:\n        yield session\napp.dependency_overrides[db.platform_db] = core\nuvicorn.run(app, host='127.0.0.1', port=8013)"
log = (root / ".local/ui-cash-backend.log").open("wb")
env = {
    **os.environ,
    "DATABASE_URL": test_url.render_as_string(hide_password=False),
    "ALLOWED_ORIGINS": "http://127.0.0.1:5174",
    "COOKIE_SECURE": "false",
}
server = subprocess.Popen(
    [str(root / ".venv/Scripts/python.exe"), "-c", server_code],
    cwd=root / "backend",
    env=env,
    stdout=log,
    stderr=log,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
output = root / ".local/cash-local-ui"
output.mkdir(exist_ok=True)
errors = []
try:
    for _ in range(40):
        try:
            if httpx.get("http://127.0.0.1:8013/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    else:
        raise RuntimeError("A API isolada não iniciou; consulte o log privado.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
        page.set_default_timeout(30000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        close_requests = []
        page.on("request", lambda request: close_requests.append(request.url)
                if request.method == "POST" and urlsplit(request.url).path.endswith("/close") else None)

        def proxy(route):
            parsed = urlsplit(route.request.url)
            response = route.fetch(
                url="http://127.0.0.1:8013"
                + parsed.path
                + ("?" + parsed.query if parsed.query else "")
            )
            route.fulfill(response=response)

        page.route("**/api/**", proxy)
        page.goto("http://127.0.0.1:5174", timeout=60000)
        page.get_by_label("NIF da empresa", exact=True).fill("500000001")
        page.get_by_label("Email", exact=True).fill("ui@gpgest.local")
        page.get_by_label("Palavra-passe", exact=True).fill(password)
        page.get_by_role("button", name="Entrar", exact=True).click()
        page.get_by_role("button", name="Tesouraria", exact=True).click()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        page.get_by_role("button", name="Tabelas", exact=True).click()
        page.get_by_role("button", name="Meios de pagamento", exact=True).click()
        expect(page.get_by_role("button", name="Inativar Numerário", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Inativar MB WAY", exact=True).click()
        expect(page.get_by_role("button", name="Ativar MB WAY", exact=True)).to_be_enabled()
        page.screenshot(path=str(output / "payment-method-inactive.png"), full_page=True)
        page.reload()
        page.get_by_role("button", name="Tabelas", exact=True).click()
        page.get_by_role("button", name="Meios de pagamento", exact=True).click()
        expect(page.get_by_role("button", name="Ativar MB WAY", exact=True)).to_be_enabled()
        page.get_by_role("button", name="Ativar MB WAY", exact=True).click()
        expect(page.get_by_role("button", name="Inativar MB WAY", exact=True)).to_be_enabled()
        page.get_by_role("button", name="Tesouraria", exact=True).click()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        expect(
            page.get_by_role("status", name="Apuramento de numerário", exact=True)
        ).to_be_visible()
        expect(page.get_by_role("heading", name="Realizado do dia", exact=True)).to_be_visible()
        page.get_by_text("Conferência e fecho", exact=True).click()
        expect(page.get_by_role("heading", name="Realizado do dia", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Fechar caixa", exact=True)).to_be_enabled()
        expect(page.locator(".local-cash-close-guidance")).to_have_count(0)
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        warning = page.get_by_role("alertdialog", name="Para fechar a caixa", exact=True)
        expect(warning).to_contain_text("Preencha o faturado de:")
        expect(page.get_by_role("dialog", name="Caixa fechada", exact=True)).to_have_count(0)
        warning.get_by_role("button", name="Voltar à conferência", exact=True).click()
        expect(page.get_by_role("link", name="Relatório PDF", exact=True)).to_have_count(0)
        assert not close_requests
        expect(page.locator(".local-realized-inputs")).to_have_count(0)

        def realized_row(label):
            return page.locator(".local-cash-table tbody tr").filter(
                has=page.get_by_text(label, exact=True)
            )

        def open_realized(label):
            realized_row(label).locator(".local-realized-button").click()
            return page.get_by_role("dialog", name="Registar faturado", exact=True)

        def open_debits(label):
            realized_row(label).locator(".local-debit-button").click()
            return page.get_by_role("dialog", name="Calculadora", exact=True)

        def enter_adjustment(dialog, expression):
            dialog.get_by_role("button", name="Limpar", exact=True).click()
            page.keyboard.type(expression)
            expect(dialog.get_by_label("Valor introduzido", exact=True)).to_have_text(expression)

        # Cancelling keeps the declaration unfilled, and the empty value cannot be applied.
        dialog = open_realized("Numerário")
        expect(dialog.get_by_role("button", name="Aplicar valor", exact=True)).to_be_disabled()
        dialog.get_by_label("Faturado Numerário (€)", exact=True).fill("999,00")
        dialog.get_by_role("button", name="Cancelar", exact=True).click()
        expect(realized_row("Numerário").locator(".local-realized-button")).to_have_text(
            "Registar valor"
        )
        for label, value in [
            ("Numerário", "300,00"),
            ("Multibanco / TPA", "1000,00"),
        ]:
            dialog = open_realized(label)
            dialog.get_by_label(f"Faturado {label} (€)", exact=True).fill(value)
            if label == "Numerário":
                dialog.get_by_text("Observações do dia e referência POS", exact=True).click()
                dialog.get_by_label("Referência ao fecho do POS (opcional)", exact=True).fill(
                    "POS TESTE 001"
                )
                dialog.get_by_label("Observações do realizado", exact=True).fill("Fecho diário")
            dialog.get_by_role("button", name="Aplicar valor", exact=True).click()
        for label in ("Numerário", "Multibanco / TPA"):
            expect(page.get_by_role("button", name=f"Sem movimento — {label}", exact=True)).to_be_disabled()
        for label in ("MB WAY", "Transferência", "Outros meios"):
            shortcut = page.get_by_role("button", name=f"Sem movimento — {label}", exact=True)
            shortcut.click()
            expect(shortcut).to_have_attribute("aria-pressed", "true")
            expect(realized_row(label).locator(".local-realized-button")).to_contain_text("0,00")
            expect(page.get_by_label(f"Contagem {label} (€)", exact=True)).to_have_value("0,00")
        shortcut = page.get_by_role("button", name="Sem movimento — Transferência", exact=True)
        shortcut.click()
        expect(realized_row("Transferência").locator(".local-realized-button")).to_have_text("Registar valor")
        expect(page.get_by_label("Contagem Transferência (€)", exact=True)).to_have_value("")
        shortcut.click()
        # Clearing a declared zero returns to pending, and confirming zero restores completeness.
        dialog = open_realized("MB WAY")
        expect(dialog.get_by_label("Faturado MB WAY (€)", exact=True)).to_have_value("0,00")
        dialog.get_by_role("button", name="Deixar por preencher", exact=True).click()
        expect(realized_row("MB WAY").locator(".local-realized-button")).to_have_text(
            "Registar valor"
        )
        dialog = open_realized("MB WAY")
        dialog.get_by_label("Faturado MB WAY (€)", exact=True).fill("0,001")
        expect(dialog.get_by_role("button", name="Aplicar valor", exact=True)).to_be_disabled()
        dialog.get_by_label("Faturado MB WAY (€)", exact=True).fill("0,00")
        dialog.get_by_role("button", name="Aplicar valor", exact=True).click()
        page.get_by_role("button", name="Contar numerário", exact=True).click()
        counter = page.get_by_role("dialog", name="Contar numerário", exact=True)
        counter.get_by_role("button", name="Notas e moedas", exact=True).click()
        counter.get_by_label("Quantidade de 50,00 €", exact=True).fill("6")
        counter.get_by_label("Quantidade de 20,00 €", exact=True).fill("2")
        counter.get_by_role("button", name="Aplicar contagem", exact=True).click()
        expect(page.get_by_role("button", name="Contar numerário", exact=True)).to_contain_text(
            "340,00"
        )
        page.get_by_label("Contagem Multibanco / TPA (€)", exact=True).fill("980,00")
        for label in ("MB WAY", "Transferência", "Outros meios"):
            expect(page.get_by_label(f"Contagem {label} (€)", exact=True)).to_have_value("0,00")
        dialog = open_debits("Numerário")
        expect(dialog.get_by_label("Valor introduzido", exact=True)).to_have_text("50,00")
        enter_adjustment(dialog, "0-1")
        expect(dialog.get_by_role("button", name="Aplicar valor", exact=True)).to_be_disabled()
        enter_adjustment(dialog, "10/0")
        expect(dialog.get_by_role("button", name="Aplicar valor", exact=True)).to_be_disabled()
        enter_adjustment(dialog, "55")
        dialog.get_by_role("button", name="Cancelar", exact=True).click()
        expect(realized_row("Numerário").locator(".local-debit-button")).to_contain_text("50,00")
        expect(realized_row("Numerário").locator(".local-debit-button")).to_be_focused()
        dialog = open_debits("Numerário")
        enter_adjustment(dialog, "50+5")
        dialog.get_by_role("button", name="Aplicar valor", exact=True).click()
        expect(realized_row("Numerário").locator("td").last).to_contain_text("-5,00")
        dialog = open_debits("Numerário")
        dialog.get_by_role("button", name="Usar débitos dos movimentos", exact=True).click()
        expect(realized_row("Numerário").locator("td").last).to_contain_text("-10,00")
        dialog = open_debits("Numerário")
        enter_adjustment(dialog, "0")
        dialog.get_by_role("button", name="Aplicar valor", exact=True).click()
        expect(realized_row("Numerário").locator("td").last).to_contain_text("-60,00")
        dialog = open_debits("Numerário")
        enter_adjustment(dialog, "50")
        dialog.get_by_role("button", name="Aplicar valor", exact=True).click()
        dialog = open_debits("Numerário")
        dialog.get_by_role("button", name="Crédito (entrada)", exact=True).click()
        expect(dialog.get_by_label("Valor introduzido", exact=True)).to_have_text("0,00")
        enter_adjustment(dialog, "10+5*2")
        dialog.get_by_role("button", name="Aplicar valor", exact=True).click()
        expect(realized_row("Numerário").locator("td").nth(3)).to_contain_text("370,00")
        expect(realized_row("Numerário").locator(".local-debit-button")).to_contain_text("-30,00")
        page.get_by_role("button", name="Guardar rascunho", exact=True).click()
        expect(page.get_by_text("Rascunho guardado.", exact=True)).to_be_visible()
        page.reload()
        page.get_by_role("button", name="Tesouraria", exact=True).click()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        page.get_by_text("Conferência e fecho", exact=True).click()
        expect(realized_row("Numerário").locator(".local-debit-button")).to_contain_text("-30,00")
        expect(realized_row("Numerário").locator("td").nth(3)).to_contain_text("370,00")
        dialog = open_debits("Numerário")
        dialog.get_by_role("button", name="Crédito (entrada)", exact=True).click()
        expect(dialog.get_by_label("Valor introduzido", exact=True)).to_have_text("20,00")
        dialog.get_by_role("button", name="Usar créditos dos movimentos", exact=True).click()
        expect(realized_row("Numerário").locator("td").nth(3)).to_contain_text("350,00")
        for label in ("MB WAY", "Transferência", "Outros meios"):
            expect(page.get_by_role("button", name=f"Sem movimento — {label}", exact=True)).to_have_attribute("aria-pressed", "true")
            expect(page.get_by_label(f"Contagem {label} (€)", exact=True)).to_have_value("0,00")
        dialog = open_realized("Multibanco / TPA")
        expect(dialog.get_by_label("Faturado Multibanco / TPA (€)", exact=True)).to_have_value(
            "1000,00"
        )
        dialog.get_by_text("Observações do dia e referência POS", exact=True).click()
        expect(
            dialog.get_by_label("Referência ao fecho do POS (opcional)", exact=True)
        ).to_have_value("POS TESTE 001")
        expect(dialog.get_by_label("Observações do realizado", exact=True)).to_have_value(
            "Fecho diário"
        )
        dialog.get_by_label("Faturado Multibanco / TPA (€)", exact=True).fill("980,00")
        page.keyboard.press("Escape")
        expect(realized_row("Multibanco / TPA").locator(".local-realized-button")).to_contain_text(
            re.compile(r"1\s?000,00")
        )
        expect(realized_row("Multibanco / TPA").locator(".local-realized-button")).to_be_focused()
        expect(page.get_by_role("button", name="Contar numerário", exact=True)).to_contain_text(
            "340,00"
        )
        expect(page.get_by_label("Contagem Multibanco / TPA (€)", exact=True)).to_have_value(
            "980,00"
        )
        cash_cells = realized_row("Numerário").locator("td")
        expect(cash_cells.last).to_contain_text("-10,00")
        card = realized_row("Multibanco / TPA")
        expect(card.locator("td").last).to_contain_text("-20,00")
        expect(card.locator("td").nth(3)).to_contain_text(re.compile(r"1\s?000,00"))
        expect(page.get_by_role("columnheader", name="Faturado", exact=True)).to_be_visible()
        expect(page.get_by_role("columnheader", name="Esperado", exact=True)).to_be_visible()
        expect(page.get_by_text("Consultar movimentos registados", exact=True)).to_be_visible()
        expect(page.get_by_text("Falta de 10,00", exact=False)).to_be_visible()
        # A saved zero must not keep an inactive method in the cash conference.
        page.get_by_role("button", name="Guardar rascunho", exact=True).click()
        expect(page.get_by_text("Rascunho guardado.", exact=True)).to_be_visible()
        page.get_by_label("Contagem Outros meios (€)", exact=True).fill("123x")
        page.get_by_label("Parâmetros da folha de caixa", exact=True).click()
        page.get_by_role("button", name="Configurar meios de pagamento", exact=True).click()
        page.get_by_role("button", name="Inativar Outros meios", exact=True).click()
        expect(page.get_by_role("button", name="Ativar Outros meios", exact=True)).to_be_enabled()
        page.get_by_role("button", name="Fechar meios de pagamento e voltar", exact=True).click()
        expect(realized_row("Outros meios")).to_have_count(0)
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        inactive_warning = page.get_by_role("alertdialog", name="Para fechar a caixa", exact=True)
        expect(inactive_warning).to_contain_text("pelo menos 5 caracteres")
        expect(inactive_warning).not_to_contain_text("Introduza valores válidos")
        expect(inactive_warning).not_to_contain_text("Outros meios")
        inactive_warning.get_by_role("button", name="Voltar à conferência", exact=True).click()
        page.reload()
        page.get_by_role("button", name="Tesouraria", exact=True).click()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        page.get_by_text("Conferência e fecho", exact=True).click()
        expect(realized_row("Outros meios")).to_have_count(0)
        expect(page.get_by_label("Contagem Multibanco / TPA (€)", exact=True)).to_have_value("980,00")
        expect(page.get_by_role("button", name="Fechar caixa", exact=True)).to_be_enabled()
        expect(page.locator(".local-cash-close-guidance")).to_have_count(0)
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        warning = page.get_by_role("alertdialog", name="Para fechar a caixa", exact=True)
        expect(warning).to_contain_text("pelo menos 5 caracteres")
        warning.get_by_role("button", name="Voltar à conferência", exact=True).click()
        assert not close_requests
        page.get_by_role("button", name="Conferir", exact=True).click()
        expect(page.get_by_text("Conferência guardada.", exact=True)).to_be_visible()
        page.get_by_label("Justificação das diferenças (obrigatória para fechar)", exact=True).fill(
            "Conferência de demonstração: diferenças por identificar"
        )
        expect(page.locator(".local-cash-close-guidance")).to_have_count(0)
        page.screenshot(path=str(output / "desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "mobile.png"), full_page=True)
        dialog = open_realized("Numerário")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "mobile-dialog.png"), full_page=True)
        dialog.get_by_role("button", name="Cancelar", exact=True).click()
        dialog = open_debits("Numerário")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "mobile-debit-dialog.png"), full_page=True)
        dialog.get_by_role("button", name="Cancelar", exact=True).click()
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        supplier_dialog = page.get_by_role("dialog", name="Identificar fornecedor", exact=True)
        expect(supplier_dialog).to_be_visible()
        expect(supplier_dialog).to_contain_text("50,00")
        expect(supplier_dialog.get_by_role("button", name="Confirmar e fechar caixa", exact=True)).to_be_disabled()
        assert not close_requests
        supplier_dialog.get_by_role("button", name="Cancelar", exact=True).click()
        expect(supplier_dialog).to_have_count(0)
        expect(page.get_by_role("button", name="Fechar caixa", exact=True)).to_be_focused()
        expect(page.get_by_role("button", name="Contar numerário", exact=True)).to_contain_text("340,00")
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        supplier_dialog.get_by_role("combobox", name="Fornecedor", exact=True).fill("Fornecedor UI")
        supplier_dialog.get_by_role("button", name="＋ Criar novo", exact=True).click()
        entity_dialog = page.get_by_role("dialog", name="Criar entidade — começar pelo NIF", exact=True)
        entity_dialog.get_by_role("button", name="Preencher manualmente", exact=True).click()
        entity_dialog = page.get_by_role("dialog", name="Criar entidade", exact=True)
        entity_dialog.get_by_label("NIF", exact=True).fill("500000000")
        entity_dialog.get_by_role("button", name="Criar fornecedor", exact=True).click()
        expect(supplier_dialog.get_by_role("button", name="Confirmar e fechar caixa", exact=True)).to_be_enabled()
        assert not close_requests
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        supplier_dialog.screenshot(path=str(output / "supplier-mobile.png"))
        supplier_dialog.get_by_role("button", name="Confirmar e fechar caixa", exact=True).click()
        confirmation = page.get_by_role("dialog", name="Caixa fechada", exact=True)
        expect(confirmation).to_be_visible()
        expect(confirmation).to_contain_text("os montantes ainda não registados foram adicionados aos movimentos reais.")
        expect(confirmation.get_by_role("button", name="OK", exact=True)).to_be_focused()
        confirmation.screenshot(path=str(output / "close-confirmation-mobile.png"))
        confirmation.get_by_role("button", name="OK", exact=True).click()
        expect(confirmation).to_have_count(0)
        expect(page.get_by_role("link", name="Relatório PDF", exact=True)).to_be_visible()
        assert len(close_requests) == 1
        page.get_by_text("Ver detalhes do fecho", exact=True).click()
        expect(page.get_by_role("link", name="Relatório PDF", exact=True)).not_to_be_visible()
        expect(
            page.get_by_role("status", name="Apuramento de numerário", exact=True)
        ).to_be_visible()
        page.screenshot(path=str(output / "closed-summary.png"), full_page=True)
        page.get_by_text("Ver detalhes do fecho", exact=True).click()
        expect(page.locator(".local-realized-button")).to_have_count(0)
        expect(page.get_by_text("Fornecedor UI", exact=True)).to_be_visible()
        page.get_by_text("Observações do dia e referência POS", exact=True).click()
        expect(
            page.get_by_text("Referência ao fecho do POS: POS TESTE 001", exact=True)
        ).to_be_visible()
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Movement)) == 3
            import json
            saved = json.loads(db.get(CashSession, cash_session_id).count_data)
            assert saved["debit_supplier"]["name"] == "Fornecedor UI"
            assert saved["debit_supplier"]["debits_cents"] == 5000
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="Reabrir", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Motivo", exact=True).fill("Recontagem autorizada de demonstração")
        dialog.get_by_role("button", name="Guardar", exact=True).click()
        dialog = open_realized("Numerário")
        expect(dialog.get_by_label("Faturado Numerário (€)", exact=True)).to_have_value("300,00")
        dialog.get_by_role("button", name="Cancelar", exact=True).click()
        assert not errors, errors
        browser.close()
    print(
        "PASS: registo por meio, cancelamento, zero explícito, rascunho, atualização, conferência, fecho sem ajustes, reabertura e vista móvel"
    )
finally:
    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
    log.close()
    engine.dispose()
    assert schema.startswith("ui_cash_") and len(schema) == 40
    with master.begin() as connection:
        connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
    master.dispose()
