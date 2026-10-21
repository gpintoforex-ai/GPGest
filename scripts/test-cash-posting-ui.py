"""Real local closing and movement listing in an isolated disposable database schema."""
import json
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
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
settings = dotenv_values(root / ".local/test-cash.env", interpolate=False)
url = make_url(settings["DATABASE_URL"])
assert url.host == "127.0.0.1" and url.port == 55432 and url.database.startswith("gpgest_test_cash_")
schema = "ui_posting_" + uuid4().hex
master = create_engine(url)
with master.begin() as connection:
    connection.execute(text("CREATE SCHEMA " + schema))
test_url = url.update_query_dict({"options": "-csearch_path=" + schema})
engine = create_engine(test_url)
from app.models import Base, BankAccount, BusinessEntity, CashSession, Category, LocalPaymentMethod, Movement, PaymentMethodBankDefault, Register, Store, User, now
from app.cash_sheet import day
from app.platform_models import Company, PlatformBase

Base.metadata.create_all(engine)
PlatformBase.metadata.create_all(engine)
password = "IsolatedCashPostingBrowser123!"
with Session(engine) as db:
    user = User(name="Operador fictício", email="posting@ui.test", role="administrador", password_hash=PasswordHash.recommended().hash(password))
    store = Store(name="Loja de testes")
    db.add_all([user, store])
    db.flush()
    register = Register(name="Caixa UI", store_id=store.id)
    category = Category(name="Vendas")
    account = BankAccount(store_id=store.id, name="Conta TPA", bank="Banco UI", iban_last4="1234")
    db.add_all([register, category, account, LocalPaymentMethod(id="cartao", name="Multibanco / TPA"), BusinessEntity(name="Fornecedor A UI", is_supplier=True), BusinessEntity(name="Fornecedor B UI", is_supplier=True)])
    db.flush()
    db.add(PaymentMethodBankDefault(method_id="cartao", store_id=store.id, account_id=account.id))
    selected = day(now())
    session = CashSession(register_id=register.id, operator_id=user.id, opening_cents=10000, sheet_day=selected)
    db.add(session)
    db.flush()
    session.count_data = json.dumps({
        "realized": {"day": selected, "register_id": register.id,
                     "values": {"numerario": 30000, "cartao": 20000, "mbway": 0, "transferencia": 0, "outro": 0},
                     "debits": {"numerario": 5000, "cartao": 500}, "credits": {"cartao": 1000}},
        "values": {"numerario": 35000, "cartao": 20500}, "cash_mode": "total", "counts": {},
    })
    db.add(Movement(session_id=session.id, user_id=user.id, category_id=category.id, bank_account_id=account.id, kind="venda", payment="cartao", amount_cents=8000, direction=1, occurred_at=now(), request_key=str(uuid4())))
    db.add(Company(name="Empresa UI", slug="posting-ui", nif="500000001", schema_name="public", mode="demonstracao", operating_mode="local", api_active=0))
    db.commit()
    session_id, register_id, account_id = session.id, register.id, account.id
server_code = "from app import db\nfrom app.main import app\nimport uvicorn\ndb.next_platform_session = db.SessionLocal\ndef core():\n    with db.SessionLocal() as session:\n        yield session\napp.dependency_overrides[db.platform_db] = core\nuvicorn.run(app, host='127.0.0.1', port=8016)"
log = (root / ".local/ui-cash-posting-backend.log").open("wb")
env = {**os.environ, **{key: value for key, value in settings.items() if value is not None}, "DATABASE_URL": test_url.render_as_string(hide_password=False), "APP_MODE": "demonstracao", "ALLOWED_ORIGINS": "http://127.0.0.1:5174", "COOKIE_SECURE": "false"}
env.pop("GPGEST_ENV_FILE", None)
server = None
try:
    with socket.socket() as available_port:
        available_port.bind(("127.0.0.1", 8016))
    server = subprocess.Popen([sys.executable, "-c", server_code], cwd=root / "backend", env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(80):
        try:
            if httpx.get("http://127.0.0.1:8016/api/health", timeout=1).status_code == 200:
                break
        except httpx.RequestError:
            time.sleep(0.2)
    else:
        raise RuntimeError("A API de testes não iniciou; consulte o registo privado.")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
        errors = []
        requests = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append((request.method, urlsplit(request.url).path))
                if "/api/" in request.url else None)
        def proxy(route):
            path = urlsplit(route.request.url)
            route.fulfill(response=route.fetch(url="http://127.0.0.1:8016" + path.path + ("?" + path.query if path.query else "")))
        page.route("**/api/**", proxy)
        page.goto("http://127.0.0.1:5174/entrar")
        page.get_by_label("NIF da empresa", exact=True).fill("500000001")
        page.get_by_label("Email", exact=True).fill("posting@ui.test")
        page.get_by_label("Palavra-passe", exact=True).fill(password)
        page.get_by_role("button", name="Entrar", exact=True).click()
        finance = page.get_by_role("button", name="Tesouraria", exact=True)
        finance.click()
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        page.get_by_text("Conferência e fecho", exact=True).click()
        page.wait_for_load_state("networkidle")
        requests.clear()
        page.get_by_role("button", name="Guardar rascunho", exact=True).click()
        expect(page.get_by_text("Rascunho guardado.", exact=True)).to_be_visible()
        page.wait_for_load_state("networkidle")
        assert requests == [("POST", f"/api/sessions/{session_id}/count")], requests
        requests.clear()
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        supplier = page.get_by_role("dialog", name="Identificar fornecedor", exact=True)
        expect(supplier).to_be_visible()
        supplier.get_by_role("combobox", name="Fornecedor", exact=True).fill("Fornecedor A UI")
        supplier.get_by_role("option", name="Fornecedor A UI", exact=True).click()
        supplier.get_by_label("Meio de pagamento do fornecedor 1", exact=True).select_option("numerario")
        supplier.get_by_label("Valor a atribuir ao fornecedor 1 (€)", exact=True).fill("20,00")
        supplier.get_by_role("button", name="Adicionar fornecedor", exact=True).click()
        supplier.get_by_role("combobox", name="Fornecedor", exact=True).nth(1).fill("Fornecedor B UI")
        supplier.get_by_role("option", name="Fornecedor B UI", exact=True).click()
        supplier.get_by_label("Meio de pagamento do fornecedor 2", exact=True).select_option("numerario")
        supplier.get_by_label("Valor a atribuir ao fornecedor 2 (€)", exact=True).fill("30,00")
        supplier.get_by_role("button", name="Adicionar fornecedor", exact=True).click()
        supplier.get_by_role("combobox", name="Fornecedor", exact=True).nth(2).fill("Fornecedor A UI")
        supplier.get_by_role("option", name="Fornecedor A UI", exact=True).click()
        supplier.get_by_label("Meio de pagamento do fornecedor 3", exact=True).select_option("cartao")
        expect(supplier.get_by_label("Valor a atribuir ao fornecedor 3 (€)", exact=True)).to_have_value("5,00")
        supplier.get_by_role("button", name="Confirmar e fechar caixa", exact=True).click()
        confirmation = page.get_by_role("dialog", name="Caixa fechada", exact=True)
        expect(confirmation).to_be_visible()
        expect(confirmation).to_contain_text("adicionados aos movimentos reais")
        page.wait_for_load_state("networkidle")
        for path in ("/api/bootstrap", "/api/sessions", "/api/movements", "/api/dashboard"):
            assert requests.count(("GET", path)) == 1, requests
        assert ("GET", "/api/audit") not in requests, requests
        confirmation.get_by_role("button", name="OK", exact=True).click()
        assert page.evaluate("async id => { const r = await fetch(`/api/sessions/${id}/report`); return r.ok && r.headers.get('content-type').includes('application/pdf'); }", session_id)
        page.get_by_role("button", name="Movimentos reais", exact=True).click()
        table = page.locator("main table").filter(has=page.get_by_role("columnheader", name="Origem", exact=True))
        expect(table.locator("tbody tr")).to_have_count(7)
        expect(table.get_by_text("Fecho de caixa", exact=True)).to_have_count(6)
        expect(table.get_by_role("row").filter(has=page.get_by_role("cell", name="Fornecedor B UI", exact=True))).to_contain_text("30,00")
        expect(table.get_by_role("cell", name="Fornecedor A UI", exact=True)).to_have_count(2)
        expect(table.get_by_role("button", name="Estornar", exact=True)).to_have_count(1)
        page.get_by_label("Caixas / bancos", exact=True).select_option("bank:" + account_id)
        expect(table.locator("tbody tr")).to_have_count(4)
        page.get_by_label("Caixas / bancos", exact=True).select_option(register_id)
        expect(table.locator("tbody tr")).to_have_count(3)
        page.get_by_label("Caixas / bancos", exact=True).select_option("")
        output = root / ".local/cash-posting-ui"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / "movements-desktop.png"), full_page=True)
        # Reopening must remove the generated close from the real-movement list.
        # Native entries survive; the old supplier and reversal rows stay in audit.
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        page.get_by_text("Ver detalhes do fecho", exact=True).click()
        page.get_by_role("button", name="Reabrir", exact=True).click()
        reopen = page.get_by_role("dialog", name="Reabrir caixa", exact=True)
        reopen.get_by_label("Motivo", exact=True).fill("Corrigir repartição dos fornecedores")
        reopen.get_by_role("button", name="Guardar", exact=True).click()
        expect(page.get_by_role("button", name="Fechar caixa", exact=True)).to_be_visible()
        page.get_by_role("button", name="Movimentos reais", exact=True).click()
        expect(table.locator("tbody tr")).to_have_count(1)
        expect(table.get_by_text("Fecho de caixa", exact=True)).to_have_count(0)
        expect(table).to_contain_text("80,00")
        page.screenshot(path=str(output / "movements-reopened.png"), full_page=True)
        page.get_by_role("button", name="Folha de caixa", exact=True).click()
        page.get_by_text("Conferência e fecho", exact=True).click()
        page.get_by_role("button", name="Fechar caixa", exact=True).click()
        supplier = page.get_by_role("dialog", name="Identificar fornecedor", exact=True)
        supplier.get_by_role("combobox", name="Fornecedor", exact=True).fill("Fornecedor B UI")
        supplier.get_by_role("option", name="Fornecedor B UI", exact=True).click()
        supplier.get_by_label("Meio de pagamento do fornecedor 1", exact=True).select_option("numerario")
        supplier.get_by_label("Valor a atribuir ao fornecedor 1 (€)", exact=True).fill("50,00")
        supplier.get_by_role("button", name="Adicionar fornecedor", exact=True).click()
        supplier.get_by_role("combobox", name="Fornecedor", exact=True).nth(1).fill("Fornecedor B UI")
        supplier.get_by_role("option", name="Fornecedor B UI", exact=True).click()
        supplier.get_by_label("Meio de pagamento do fornecedor 2", exact=True).select_option("cartao")
        expect(supplier.get_by_label("Valor a atribuir ao fornecedor 2 (€)", exact=True)).to_have_value("5,00")
        supplier.get_by_role("button", name="Confirmar e fechar caixa", exact=True).click()
        expect(confirmation).to_be_visible()
        confirmation.get_by_role("button", name="OK", exact=True).click()
        page.get_by_role("button", name="Movimentos reais", exact=True).click()
        expect(table.locator("tbody tr")).to_have_count(6)
        expect(table.get_by_text("Fecho de caixa", exact=True)).to_have_count(5)
        expect(table.get_by_role("cell", name="Fornecedor A UI", exact=True)).to_have_count(0)
        expect(table.get_by_role("cell", name="Fornecedor B UI", exact=True)).to_have_count(2)
        expect(table.get_by_text("Estornado", exact=True)).to_have_count(0)
        expect(table.get_by_text("Estorno", exact=True)).to_have_count(0)
        page.screenshot(path=str(output / "movements-reclosed.png"), full_page=True)
        page.reload()
        page.get_by_role("button", name="Tesouraria", exact=True).click()
        page.get_by_role("button", name="Movimentos reais", exact=True).click()
        expect(table.locator("tbody tr")).to_have_count(6)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "movements-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    with Session(engine) as db:
        rows = list(db.scalars(select(Movement)))
        assert len(rows) == 18
        assert len([row for row in rows if not row.reversed_by and not row.reversal_of]) == 6
        assert len([row for row in rows if row.reversal_of]) == 6
        assert sum(row.direction * row.amount_cents for row in rows) == 45500
        assert sum(row.direction * row.amount_cents for row in rows if row.bank_account_id == account_id) == 20500
        assert json.loads(db.get(CashSession, session_id).count_data)["posted_movements"]["ids"]
    print("PASS: reopening lists only the native movement, reclose shows only current supplier payments, exact totals and reversal audit preserved, bank/cash filters, reload and mobile; isolated database only.")
finally:
    if server:
        server.terminate()
        server.wait(timeout=15)
    log.close()
    engine.dispose()
    with master.begin() as connection:
        assert schema.startswith("ui_posting_") and len(schema) == 43
        connection.execute(text("DROP SCHEMA " + schema + " CASCADE"))
    master.dispose()
