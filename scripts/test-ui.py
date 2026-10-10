"""Teste local de navegador em esquema PostgreSQL temporário, sem dados da aplicação."""

import os
import secrets
import subprocess
import sys
import time
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
from app.models import Base, Category, Register, Store, User

engine = create_engine(os.environ["DATABASE_URL"])
schema = "ui_" + uuid4().hex
with engine.begin() as c:
    c.execute(text("CREATE SCHEMA " + schema))
url = engine.url.update_query_dict({"options": "-csearch_path=" + schema})
isolated = create_engine(url)
Base.metadata.create_all(isolated)
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
    db.add(Category(name="Vendas"))
    db.commit()
log = (root / ".local" / "ui-backend.log").open("w")
server = subprocess.Popen(
    [
        str(root / ".venv" / "Scripts" / "python.exe"),
        "-m",
        "uvicorn",
        "app.main:app",
        "--port",
        "8001",
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
        page.get_by_role("button", name="Caixa diária", exact=True).click()
        page.get_by_role("button", name="Abrir caixa", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Caixa", exact=True).select_option(label="Caixa de teste")
        dialog.get_by_label("Fundo inicial (€)").fill("100,00")
        dialog.get_by_role("button", name="Guardar", exact=True).click()
        expect(page.locator(".cash-value")).to_contain_text("100,00")
        page.get_by_role("button", name="Entradas e saídas", exact=True).click()
        page.get_by_role("button", name="Registar movimento", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Sessão aberta").select_option(label="Caixa de teste")
        dialog.get_by_label("Categoria", exact=True).select_option(label="Vendas")
        dialog.get_by_label("Montante (€)").fill("20,00")
        dialog.get_by_label("Meio de pagamento").select_option("cartao")
        dialog.get_by_role("button", name="Guardar", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(page.get_by_text("Multibanco / cartão", exact=True)).to_be_visible()
        page.get_by_role("button", name="Caixa diária", exact=True).click()
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Quantidade de 100,00", exact=False).fill("1")
        dialog.get_by_role("button", name="Guardar", exact=True).click()
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(page.get_by_role("link", name="Relatório PDF")).to_be_visible()
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
        expect(page.get_by_role("heading", name="A sua organização")).to_be_visible()
        assert not errors, errors
        browser.close()
    print("UI: login, abertura, movimento cartão, fecho, PDF e layout móvel passaram.")
except Exception as error:
    raise RuntimeError(str(error).replace(password, "[oculto]")) from None
finally:
    server.terminate()
    server.wait(timeout=15)
    log.close()
    isolated.dispose()
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
