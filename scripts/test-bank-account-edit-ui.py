"""Bank account editing in Chromium with fictitious, fully intercepted API data."""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
boot = {
    "mode": "demonstracao", "company": {"id": "bank-edit-ui", "name": "Empresa fictícia"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "payment_methods": {"numerario": "Numerário"}, "payment_method_catalog": [],
    "payment_method_bank_defaults": [],
    "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
    "stores": [{"id": "store-a", "name": "Loja fictícia A"}],
    "accounts": [{"id": "account-a", "store_id": "store-a", "name": "Conta original",
                  "bank": "Banco livre", "iban_last4": "1234", "currency": "EUR", "iban_configured": False}],
    "bank_institutions": [{"id": "bank-a", "name": "Banco tabelado", "active": True}],
    "registers": [], "categories": [], "users": [],
}
errors, writes = [], []
fail_next = False


def mock_api(route):
    global fail_next
    path = urlsplit(route.request.url).path
    if route.request.method == "POST":
        assert path in {"/api/accounts/account-a", "/api/accounts"}, path
        body = route.request.post_data_json
        if fail_next:
            fail_next = False
            route.fulfill(status=422, json={"detail": "Indique os dados corretos da conta."})
            return
        writes.append((path, body))
        if path == "/api/accounts/account-a":
            assert "store_id" not in body and "currency" not in body
            boot["accounts"][0].update(body)
            result = boot["accounts"][0]
        else:
            result = {"id": "account-new", "currency": "EUR", **body}
            boot["accounts"].append(result)
    else:
        result = boot if path == "/api/bootstrap" else {} if path == "/api/dashboard" else []
    route.fulfill(json=result)


with sync_playwright() as playwright:
    expect.set_options(timeout=15000)
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", mock_api)

    def open_accounts():
        page.goto("http://127.0.0.1:5174")
        page.get_by_role("button", name="Bancos", exact=True).click()
        page.get_by_role("button", name="Múltiplas contas", exact=True).click()

    open_accounts()
    page.get_by_role("button", name="Editar conta Conta original", exact=True).click()
    dialog = page.get_by_role("dialog", name="Editar conta bancária", exact=True)
    expect(dialog.get_by_role("textbox", name="Nome", exact=True)).to_have_value("Conta original")
    expect(dialog.get_by_role("textbox", name="Nome do banco", exact=True)).to_have_value("Banco livre")
    expect(dialog.get_by_label("Últimos 4 algarismos do IBAN", exact=True)).to_have_value("1234")
    expect(dialog.get_by_role("button", name="Fechar", exact=True)).to_be_visible()
    dialog.get_by_role("textbox", name="Nome", exact=True).fill("Por guardar")
    dialog.get_by_role("button", name="Cancelar", exact=True).click()
    page.get_by_role("button", name="Continuar a editar", exact=True).click()
    expect(dialog.get_by_role("textbox", name="Nome", exact=True)).to_have_value("Por guardar")
    dialog.get_by_role("button", name="Cancelar", exact=True).click()
    page.get_by_role("button", name="Descartar alterações", exact=True).click()
    expect(dialog).to_have_count(0)
    assert not writes

    page.get_by_role("button", name="Editar conta Conta original", exact=True).click()
    dialog.get_by_role("textbox", name="Nome", exact=True).fill("Conta corrigida")
    dialog.get_by_role("combobox", name="Banco", exact=True).select_option("Banco tabelado")
    dialog.get_by_label("Últimos 4 algarismos do IBAN", exact=True).fill("5678")
    fail_next = True
    dialog.get_by_role("button", name="Guardar alterações", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text("Indique os dados corretos")
    expect(dialog.get_by_role("textbox", name="Nome", exact=True)).to_have_value("Conta corrigida")
    dialog.get_by_role("button", name="Guardar alterações", exact=True).click()
    expect(dialog).to_have_count(0)
    expect(page.get_by_role("heading", name="Conta corrigida", exact=True)).to_be_visible()
    assert writes == [("/api/accounts/account-a", {"name": "Conta corrigida", "bank": "Banco tabelado", "iban_last4": "5678"})]
    open_accounts()
    page.get_by_role("button", name="Editar conta Conta corrigida", exact=True).click()
    expect(dialog.get_by_role("combobox", name="Banco", exact=True)).to_have_value("Banco tabelado")
    page.keyboard.press("Escape")
    expect(dialog).to_have_count(0)

    # Accounts created by setup may have no IBAN, or a protected full IBAN.
    boot["accounts"][0]["iban_last4"] = ""
    open_accounts()
    page.get_by_role("button", name="Editar conta Conta corrigida", exact=True).click()
    dialog.get_by_role("textbox", name="Nome", exact=True).fill("Conta sem IBAN")
    dialog.get_by_role("button", name="Guardar alterações", exact=True).click()
    expect(dialog).to_have_count(0)
    boot["accounts"][0].update({"iban_configured": True, "iban_last4": "5678"})
    open_accounts()
    page.get_by_role("button", name="Editar conta Conta sem IBAN", exact=True).click()
    expect(dialog.get_by_label("Últimos 4 algarismos do IBAN", exact=True)).to_have_attribute("readonly", "")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(dialog.get_by_role("button", name="Fechar", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="Guardar alterações", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    output = root / ".local/bank-edit-ui"
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / "mobile.png"))
    page.keyboard.press("Escape")

    # Existing creation flow remains available with required establishment.
    page.set_viewport_size({"width": 1440, "height": 1000})
    open_accounts()
    page.get_by_role("button", name="Nova conta", exact=True).click()
    create = page.get_by_role("dialog", name="Nova conta bancária", exact=True)
    create.get_by_role("textbox", name="Nome", exact=True).fill("Conta nova")
    create.get_by_role("combobox", name="Loja", exact=True).select_option("store-a")
    create.get_by_role("combobox", name="Banco", exact=True).select_option("Banco tabelado")
    create.get_by_label("Últimos 4 algarismos do IBAN", exact=True).fill("9999")
    create.get_by_role("button", name="Guardar", exact=True).click()
    expect(create).to_have_count(0)
    expect(page.get_by_role("heading", name="Conta nova", exact=True)).to_be_visible()
    assert writes[-1][0] == "/api/accounts" and writes[-1][1]["store_id"] == "store-a"
    boot["user"]["role"] = "consulta"
    open_accounts()
    expect(page.get_by_role("button", name="Editar conta", exact=False)).to_have_count(0)
    expect(page.get_by_role("button", name="Nova conta", exact=True)).to_have_count(0)
    assert not errors, errors
    page.unroute_all(behavior="ignoreErrors")
    browser.close()
print("PASS: account edit, dirty guards, preserved error values, save/reload, protected and missing IBAN, creation, permissions and mobile; mocked API only.")
