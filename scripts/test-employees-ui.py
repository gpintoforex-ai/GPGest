"""Real UI/API checks in an isolated disposable database; never use application data."""
import json
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
assert url.host == "127.0.0.1" and url.port == 55432 and url.database.startswith("gpgest_test_cash_")
os.environ.update({key: value for key, value in settings.items() if value is not None})
os.environ.pop("GPGEST_ENV_FILE", None)
sys.path.insert(0, str(root / "backend"))
from app.cash_sheet import day
from app.models import Access, Base, BusinessEntity, CashSession, Category, Employee, Movement, Register, Store, User, now
from app.platform_models import Company, PlatformBase

schema = "ui_employee_" + uuid4().hex
master = create_engine(url)
with master.begin() as connection:
    connection.execute(text("CREATE SCHEMA " + schema))
test_url = url.update_query_dict({"options": "-csearch_path=" + schema})
engine = create_engine(test_url)
Base.metadata.create_all(engine)
PlatformBase.metadata.create_all(engine)
password = "IsolatedEmployeeBrowser123!"
with Session(engine) as db:
    a, b = Store(name="Loja A"), Store(name="Loja B")
    admin = User(name="Administrador UI", email="admin@ui.test", role="administrador", password_hash=PasswordHash.recommended().hash(password))
    reader = User(name="Consulta UI", email="reader@ui.test", role="consulta", password_hash=PasswordHash.recommended().hash(password))
    db.add_all([a, b, admin, reader, Company(name="Empresa UI", slug="demo", nif="500000001", schema_name="public", mode="demonstracao", operating_mode="local")])
    db.flush()
    a_id, b_id = a.id, b.id
    db.add(Access(user_id=reader.id, store_id=a.id))
    register = Register(name="Caixa UI", store_id=a.id)
    category = Category(name="Despesas")
    db.add_all([register, category, BusinessEntity(name="Fornecedor A UI", is_supplier=True), BusinessEntity(name="Fornecedor B UI", is_supplier=True)])
    db.flush()
    sheet = CashSession(register_id=register.id, operator_id=admin.id, sheet_day=day(now()), opening_cents=4000)
    db.add(sheet)
    db.flush()
    session_id = sheet.id
    methods = ("numerario", "cartao", "mbway", "transferencia", "outro")
    sheet.count_data = json.dumps({"cash_mode": "total", "values": {key: 3500 if key == "numerario" else 0 for key in methods}, "realized": {"day": sheet.sheet_day, "register_id": register.id, "values": dict.fromkeys(methods, 0)}})
    db.add(Movement(session_id=sheet.id, user_id=admin.id, category_id=category.id, kind="despesa", payment="numerario", amount_cents=500, direction=-1, occurred_at=now(), request_key=str(uuid4())))
    db.commit()
server_code = "from app import db\nfrom app.main import app\nimport uvicorn\ndb.next_platform_session = db.SessionLocal\ndef core():\n    with db.SessionLocal() as session:\n        yield session\napp.dependency_overrides[db.platform_db] = core\nuvicorn.run(app, host='127.0.0.1', port=8015)"
log = (root / ".local/ui-employees-backend.log").open("wb")
env = {**os.environ, "DATABASE_URL": test_url.render_as_string(hide_password=False), "ALLOWED_ORIGINS": "http://127.0.0.1:5174", "COOKIE_SECURE": "false"}
server = subprocess.Popen([str(root / ".venv/Scripts/python.exe"), "-c", server_code], cwd=root / "backend", env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
output = root / ".local/employees-ui"
output.mkdir(exist_ok=True)
try:
    for _ in range(50):
        try:
            if httpx.get("http://127.0.0.1:8015/api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    else:
        raise RuntimeError("A API isolada não iniciou; consulte o registo privado.")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def proxy(route):
            path = urlsplit(route.request.url)
            route.fulfill(response=route.fetch(url="http://127.0.0.1:8015" + path.path + ("?" + path.query if path.query else "")))
        page.route("**/api/**", proxy)
        def login(email):
            page.goto("http://127.0.0.1:5174/entrar")
            page.get_by_label("NIF da empresa", exact=True).fill("500000001")
            page.get_by_label("Email", exact=True).fill(email)
            page.get_by_label("Palavra-passe", exact=True).fill(password)
            page.get_by_role("button", name="Entrar", exact=True).click()
            expect(page.get_by_role("button", name="Tabelas", exact=True)).to_be_visible()
        def directory():
            page.get_by_role("button", name="Tabelas", exact=True).click()
            page.get_by_role("button", name="Operadores / funcionários", exact=True).click()
        login("admin@ui.test")
        directory()
        page.get_by_role("button", name="Novo funcionário", exact=True).click()
        dialog = page.get_by_role("dialog", name="Novo funcionário", exact=True)
        expect(dialog.locator('input[type="password"]')).to_have_count(0)
        dialog.get_by_label("Nome", exact=True).fill("Joana UI")
        dialog.get_by_role("checkbox", name="Loja B", exact=True).check()
        dialog.get_by_role("button", name="Guardar funcionário", exact=True).click()
        expect(dialog).to_have_count(0)
        row = page.get_by_role("row").filter(has=page.get_by_text("Joana UI", exact=True))
        expect(row).to_contain_text("Sem acesso")
        expect(row).to_contain_text("Loja A")
        expect(row).to_contain_text("Loja B")
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(Employee)) == 1
            assert db.scalar(select(func.count()).select_from(User)) == 2
        row.get_by_role("button", name="Editar Joana UI", exact=True).click()
        dialog = page.get_by_role("dialog", name="Editar funcionário", exact=True)
        dialog.get_by_label("Telefone (opcional)", exact=True).fill("910000001")
        dialog.get_by_role("button", name="Guardar funcionário", exact=True).click()
        expect(row).to_contain_text("910000001")
        row.get_by_role("button", name="Inativar Joana UI", exact=True).click()
        expect(row.get_by_role("button", name="Ativar Joana UI", exact=True)).to_be_enabled()
        page.reload()
        directory()
        expect(row).to_contain_text("Inativo")
        row.get_by_role("button", name="Ativar Joana UI", exact=True).click()
        expect(row.get_by_role("button", name="Inativar Joana UI", exact=True)).to_be_enabled()
        page.get_by_label("Tipo de registo", exact=True).select_option("without")
        expect(page.get_by_role("row")).to_have_count(2)
        page.get_by_label("Tipo de registo", exact=True).select_option("all")
        page.get_by_role("button", name="Novo utilizador", exact=True).click()
        dialog = page.get_by_role("dialog", name="Novo utilizador", exact=True)
        dialog.get_by_label("Nome", exact=True).fill("Operador com acesso UI")
        dialog.get_by_label("Email", exact=True).fill("new@ui.test")
        dialog.get_by_label("Palavra-passe inicial (mínimo 12 caracteres)", exact=True).fill(password)
        dialog.get_by_label("Loja autorizada", exact=True).select_option(a_id)
        dialog.get_by_role("button", name="Guardar", exact=True).click()
        expect(dialog).to_have_count(0)
        expect(page.get_by_role("row").filter(has=page.get_by_text("Operador com acesso UI", exact=True))).to_contain_text("Operador")
        page.screenshot(path=str(output / "directory-desktop.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "directory-mobile.png"), full_page=True, animations="disabled")
        page.set_viewport_size({"width": 1440, "height": 1000})
        # Reproduce the supplied 5 EUR close using the current allocation contract.
        page.get_by_role("button", name="Tesouraria", exact=True).click()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        page.get_by_text("Conferência e fecho", exact=True).click()
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        dialog = page.get_by_role("dialog", name="Identificar fornecedor", exact=True)
        dialog.get_by_role("combobox", name="Fornecedor", exact=True).fill("Fornecedor A UI")
        dialog.get_by_role("option", name="Fornecedor A UI", exact=True).click()
        dialog.get_by_label("Valor a atribuir ao fornecedor 1 (€)", exact=True).fill("3,00")
        dialog.get_by_role("button", name="Adicionar fornecedor", exact=True).click()
        dialog.get_by_role("combobox", name="Fornecedor", exact=True).nth(1).fill("Fornecedor B UI")
        dialog.get_by_role("option", name="Fornecedor B UI", exact=True).click()
        expect(dialog.get_by_label("Valor a atribuir ao fornecedor 2 (€)", exact=True)).to_have_value("2,00")
        dialog.get_by_role("button", name="Confirmar e fechar caixa", exact=True).click()
        expect(page.get_by_role("dialog", name="Caixa fechada", exact=True)).to_be_visible()
        page.get_by_role("dialog", name="Caixa fechada", exact=True).get_by_role("button", name="OK", exact=True).click()
        with Session(engine) as db:
            saved = json.loads(db.get(CashSession, session_id).count_data)
            assert [item["amount_cents"] for item in saved["debit_supplier"]["allocations"]] == [300, 200]
            assert db.scalar(select(func.count()).select_from(Movement)) == 1
            assert db.scalar(select(func.count()).select_from(User)) == 3
        page.get_by_label("Menu do utilizador", exact=True).click()
        page.locator(".user-menu-panel").get_by_role("button", name="Terminar sessão", exact=True).click()
        login("reader@ui.test")
        directory()
        expect(page.get_by_text("Joana UI", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Novo funcionário", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="Editar Joana UI", exact=True)).to_have_count(0)
        assert not errors, errors
        browser.close()
    print("PASS: employee CRUD, no login account, persistence, account creation, scope/permissions, responsive UI and supplier allocation close (3+2 EUR); isolated database only.")
finally:
    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
    log.close()
    engine.dispose()
    assert re.fullmatch(r"ui_employee_[0-9a-f]{32}", schema)
    with master.begin() as connection:
        connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
    master.dispose()
