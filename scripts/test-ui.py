"""Teste local de navegador em esquema PostgreSQL temporário, sem dados da aplicação."""

import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
from app.models import Base, Category, Register, Store, User, WintouchSaleRecord
from app.platform_models import Company, PlatformBase

engine = create_engine(os.environ["DATABASE_URL"])
schema = "ui_" + uuid4().hex
with engine.begin() as c:
    c.execute(text("CREATE SCHEMA " + schema))
url = engine.url.update_query_dict({"options": "-csearch_path=" + schema})
isolated = create_engine(url)
Base.metadata.create_all(isolated)
PlatformBase.metadata.create_all(isolated)
password = secrets.token_urlsafe(24)
with Session(isolated) as db:
    user = User(
        name="Administrador de teste",
        email="ui@gpgest.local",
        role="administrador",
        password_hash=PasswordHash.recommended().hash(password),
    )
    store = Store(name="Loja de teste de interface")
    db.add_all([user, store])
    db.flush()
    db.add(Register(store_id=store.id, name="Caixa de teste"))
    db.add(Register(store_id=store.id, name="Caixa secundária"))
    db.add(Category(name="Vendas"))
    db.add(
        Company(
            name="Empresa UI",
            slug="demo",
            schema_name="public",
            mode="demonstracao",
            api_enterprise="ui-source",
            operating_mode="integrated",
        )
    )
    for value, code in ((500, "NU"), (700, "CD")):
        db.add(
            WintouchSaleRecord(
                enterprise_id="ui-source",
                external_id=str(uuid4()),
                store_id=store.id,
                kind="real",
                active=True,
                document_date="2026-01-01",
                data=json.dumps(
                    {
                        "amount_cents": value,
                        "direction": 1,
                        "payment": "Origem UI",
                        "payment_code": code,
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
            )
        )
    db.commit()
log = (root / ".local" / "ui-backend.log").open("w")
server = subprocess.Popen(
    [
        str(root / ".venv" / "Scripts" / "python.exe"),
        "-c",
        "from app import db\nfrom app.main import app\nimport uvicorn\ndb.next_platform_session = db.SessionLocal\ndef core():\n    with db.SessionLocal() as session:\n        yield session\napp.dependency_overrides[db.platform_db] = core\nuvicorn.run(app, host='127.0.0.1', port=8001)",
    ],
    cwd=root / "backend",
    env={
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "APP_MODE": "demonstracao",
    },
    stdout=log,
    stderr=log,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
try:
    import httpx

    for _ in range(30):
        try:
            if httpx.get("http://127.0.0.1:8001/api/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("http://127.0.0.1:5174")
        expect(page.get_by_role("heading", name="Entrar no GPGest")).to_be_visible()
        page.get_by_label("Email", exact=True).fill("ui@gpgest.local")
        page.get_by_label("Palavra-passe", exact=True).fill(password)
        page.get_by_role("button", name="Entrar", exact=True).click()
        expect(page.get_by_role("heading", name="Uma visão clara do seu negócio.")).to_be_visible()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        page.get_by_role("button", name="Nova folha de caixa", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Caixa", exact=True).select_option(label="Caixa de teste")
        expect(dialog.get_by_label("Fundo de maneio inicial (€)")).to_have_value("0,00")
        dialog.get_by_label("Fundo de maneio inicial (€)").fill("100,00")
        dialog.get_by_role("button", name="Guardar", exact=True).click()
        expect(page.locator(".cash-value")).to_contain_text("105,00")
        page.get_by_role("button", name="Movimentos reais", exact=True).click()
        page.get_by_role("button", name="Registar movimento", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Sessão aberta").select_option(label="Caixa de teste")
        dialog.get_by_label("Categoria", exact=True).select_option(label="Vendas")
        dialog.get_by_label("Montante (€)").fill("20,00")
        dialog.get_by_label("Meio de pagamento").select_option("cartao")
        dialog.get_by_role("button", name="Guardar", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(page.get_by_text("Multibanco / cartão", exact=True)).to_be_visible()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        expect(page.locator(".cash-value")).to_contain_text("105,00")
        cash_button = page.get_by_role("button", name="Contar numerário", exact=True)
        cash_button.click()
        dialog = page.get_by_role("dialog", name="Contar numerário")
        dialog.get_by_role("button", name="Valor total", exact=True).click()
        keypad = page.get_by_role("dialog", name="Teclado numérico", exact=True)
        dialog.get_by_label("Valor total de numerário (€)").click()
        expect(keypad).to_be_visible()
        for digit in ["1", "0", "5"]:
            keypad.get_by_role("button", name=digit, exact=True).click()
        keypad.get_by_role("button", name="Vírgula decimal", exact=True).click()
        for _ in range(2):
            keypad.get_by_role("button", name="0", exact=True).click()
        expect(keypad.get_by_label("Valor introduzido")).to_have_text("105,00")
        keypad.get_by_role("button", name="Confirmar", exact=True).click()
        expect(keypad).to_have_count(0)
        expect(dialog.get_by_label("Valor total de numerário (€)")).to_have_value("105,00")
        dialog.get_by_role("button", name="Aplicar contagem", exact=True).click()
        expect(cash_button).to_contain_text("105,00")
        page.get_by_label("Contagem Multibanco / cartão (€)", exact=True).fill("27,00")
        page.get_by_role("button", name="Guardar contagem", exact=True).click()
        expect(page.locator(".cash-sheet").get_by_role("status")).to_contain_text(
            "Contagem guardada"
        )
        page.reload()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        expect(cash_button).to_contain_text("105,00")
        expect(page.get_by_label("Contagem Multibanco / cartão (€)", exact=True)).to_have_value(
            "27,00"
        )
        cash_button.click()
        dialog.get_by_role("button", name="Notas e moedas", exact=True).click()
        dialog.get_by_label("Quantidade de 100,00", exact=False).click()
        keypad.get_by_role("button", name="2", exact=True).click()
        keypad.get_by_role("button", name="Confirmar", exact=True).click()
        expect(dialog.locator(".cash-count-total")).to_contain_text("200,00")
        dialog.get_by_role("button", name="Cancelar", exact=True).click()
        expect(cash_button).to_contain_text("105,00")
        expect(cash_button).to_be_focused()
        cash_button.click()
        dialog.get_by_role("button", name="Notas e moedas", exact=True).click()
        expect(dialog.get_by_label("Quantidade de 100,00", exact=False)).to_have_value("")
        dialog.get_by_label("Quantidade de 100,00", exact=False).fill("1")
        dialog.get_by_label("Quantidade de 5,00", exact=False).fill("1")
        dialog.get_by_label("Quantidade de 0,20", exact=False).fill("2")
        dialog.get_by_label("Quantidade de 0,10", exact=False).fill("1")
        expect(dialog.locator(".cash-count-total")).to_contain_text("105,50")
        dialog.get_by_label("Quantidade de 0,10", exact=False).click()
        page.keyboard.press("Backspace")
        page.keyboard.type("3")
        expect(keypad.get_by_label("Valor introduzido")).to_have_text("3")
        page.keyboard.press("Escape")
        expect(keypad).to_have_count(0)
        expect(dialog).to_be_visible()
        expect(dialog.get_by_label("Quantidade de 0,10", exact=False)).to_have_value("1")
        expect(dialog.get_by_label("Quantidade de 0,10", exact=False)).to_be_focused()
        dialog.get_by_label("Quantidade de 0,10", exact=False).fill("-1")
        expect(dialog.get_by_role("button", name="Aplicar contagem")).to_be_disabled()
        dialog.get_by_label("Quantidade de 0,10", exact=False).fill("0")
        dialog.get_by_label("Quantidade de 0,20", exact=False).fill("0")
        expect(dialog.locator(".cash-count-total")).to_contain_text("105,00")
        expect(dialog.locator("img")).to_have_count(15)
        page.wait_for_function("Array.from(document.querySelectorAll('dialog img')).every(img => img.complete && img.naturalWidth > 0)")
        for width, height in [(1366, 768), (1280, 720), (1024, 768), (390, 844), (390, 740)]:
            page.set_viewport_size({"width": width, "height": height})
            dialog.locator(".cash-count-body").evaluate("element => element.scrollTop = 0")
            assert dialog.locator(".cash-count-body").evaluate(
                """element => {
                    const bounds = element.getBoundingClientRect();
                    return element.scrollHeight <= element.clientHeight + 1 &&
                        Array.from(element.querySelectorAll('.cash-denomination')).every(card => {
                            const rect = card.getBoundingClientRect();
                            return rect.top >= bounds.top && rect.bottom <= bounds.bottom + 1 &&
                                rect.left >= bounds.left && rect.right <= bounds.right + 1;
                        });
                }"""
            ), f"Todas as notas e moedas devem ficar visíveis sem deslocação em {width}x{height}."
        page.set_viewport_size({"width": 1280, "height": 720})
        dialog.locator(".cash-count-body").evaluate("element => element.scrollTop = 0")
        page.screenshot(path=str(root / ".local" / "cash-count-desktop.png"), animations="disabled")
        page.set_viewport_size({"width": 390, "height": 844})
        dialog.locator(".cash-count-body").evaluate("element => element.scrollTop = 0")
        assert dialog.evaluate("element => element.scrollWidth <= element.clientWidth")
        assert dialog.locator(".cash-count-header").evaluate(
            "element => element.querySelector('p').getBoundingClientRect().bottom <= element.getBoundingClientRect().bottom"
        ), "O texto do cabeçalho ultrapassa a janela de contagem."
        assert dialog.get_by_role("button", name="Aplicar contagem").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize) >= 12 && element.getBoundingClientRect().bottom <= innerHeight"
        ), "O botão de aplicar tem de ficar visível e legível no telemóvel."
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(root / ".local" / "cash-count-mobile.png"), animations="disabled")
        dialog.get_by_label("Quantidade de 0,20", exact=False).click()
        keypad.get_by_role("button", name="2", exact=True).click()
        keypad.get_by_role("button", name="0", exact=True).click()
        expect(keypad.get_by_label("Valor introduzido")).to_have_text("20")
        keypad.get_by_role("button", name="Apagar último dígito", exact=True).click()
        expect(keypad.get_by_label("Valor introduzido")).to_have_text("2")
        keypad.get_by_role("button", name="Limpar", exact=True).click()
        expect(keypad.get_by_label("Valor introduzido")).to_have_text("0")
        keypad.get_by_role("button", name="4", exact=True).click()
        assert keypad.evaluate(
            "element => { const rect = element.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight; }"
        ), "O teclado numérico tem de caber no ecrã do telemóvel."
        page.screenshot(path=str(root / ".local" / "cash-keypad-mobile.png"), animations="disabled")
        keypad.get_by_role("button", name="Cancelar", exact=True).click()
        expect(dialog.get_by_label("Quantidade de 0,20", exact=False)).to_have_value("0")
        expect(dialog.locator(".cash-count-total")).to_contain_text("105,00")
        dialog.get_by_label("Quantidade de 5,00", exact=False).click()
        page.keyboard.type("2")
        page.keyboard.press("Enter")
        expect(dialog.locator(".cash-count-total")).to_contain_text("110,00")
        dialog.get_by_label("Quantidade de 5,00", exact=False).click()
        keypad.get_by_role("button", name="1", exact=True).click()
        expect(keypad.get_by_label("Valor introduzido")).to_have_text("1")
        keypad.get_by_role("button", name="Confirmar", exact=True).click()
        expect(dialog.locator(".cash-count-total")).to_contain_text("105,00")
        dialog.get_by_role("button", name="Aplicar contagem", exact=True).click()
        expect(cash_button).to_contain_text("105,00")
        page.set_viewport_size({"width": 1440, "height": 1000})
        cash_button.click()
        expect(dialog.get_by_label("Quantidade de 100,00", exact=False)).to_have_value("1")
        dialog.get_by_label("Quantidade de 100,00", exact=False).fill("2")
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        expect(cash_button).to_be_focused()
        expect(cash_button).to_contain_text("105,00")
        page.get_by_role("button", name="Guardar contagem", exact=True).click()
        expect(page.locator(".cash-sheet").get_by_role("status")).to_contain_text("Contagem guardada")
        page.reload()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        cash_button.click()
        expect(dialog.get_by_label("Quantidade de 100,00", exact=False)).to_have_value("1")
        expect(dialog.get_by_label("Quantidade de 5,00", exact=False)).to_have_value("1")
        dialog.get_by_role("button", name="Cancelar", exact=True).click()
        page.get_by_label("Contagem Multibanco / cartão (€)", exact=True).fill("19,00")
        page.get_by_role("button", name="Fechar folha de caixa", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("Justifique")
        page.get_by_label("Contagem Multibanco / cartão (€)", exact=True).fill("27,00")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="Fechar folha de caixa", exact=True).click()
        expect(page.get_by_role("link", name="Relatório PDF")).to_be_visible()
        page.get_by_role("button", name="Nova folha de caixa", exact=True).click()
        opening_dialog = page.get_by_role("dialog", name="Nova folha de caixa")
        opening_dialog.get_by_label("Caixa", exact=True).select_option(label="Caixa de teste")
        expect(opening_dialog.get_by_label("Fundo de maneio inicial (€)")).to_have_value("100,00")
        opening_dialog.get_by_label("Fundo de maneio inicial (€)").fill("123,45")
        opening_dialog.get_by_label("Caixa", exact=True).select_option(label="Caixa secundária")
        expect(opening_dialog.get_by_label("Fundo de maneio inicial (€)")).to_have_value("0,00")
        opening_dialog.get_by_label("Caixa", exact=True).select_option(label="Caixa de teste")
        expect(opening_dialog.get_by_label("Fundo de maneio inicial (€)")).to_have_value("100,00")
        opening_dialog.get_by_label("Fundo de maneio inicial (€)").fill("125,50")
        opening_dialog.get_by_role("button", name="Guardar", exact=True).click()
        expect(page.locator(".cash-sheet").first).to_contain_text("125,50")
        saved_registers = page.request.get("http://127.0.0.1:5174/api/bootstrap").json()["registers"]
        assert next(row for row in saved_registers if row["name"] == "Caixa de teste")["last_opening_cents"] == 12550
        page.get_by_role("button", name="Dashboard", exact=True).click()
        page.screenshot(
            path=str(root / ".local" / "dashboard-desktop.png"),
            full_page=True,
            animations="disabled",
        )
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(
            path=str(root / ".local" / "dashboard-mobile.png"),
            full_page=True,
            animations="disabled",
        )
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
            "Layout ultrapassa largura do telemóvel."
        )
        page.get_by_role("button", name="Abrir menu", exact=True).click()
        page.get_by_role("button", name="Configurações", exact=True).click()
        page.get_by_role("button", name="Organização", exact=True).click()
        expect(page.get_by_role("heading", name="A sua organização")).to_be_visible()
        assert not errors, errors
        browser.close()
    print(
        "UI: folha de caixa, fundo de maneio, contagem persistente, notas/moedas, diferença, fecho, PDF e layout móvel passaram."
    )
except Exception as error:
    raise RuntimeError(str(error).replace(password, "[oculto]")) from None
finally:
    server.terminate()
    server.wait(timeout=15)
    log.close()
    isolated.dispose()
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
