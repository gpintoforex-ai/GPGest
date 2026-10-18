"""Edit/remove register UI with fictitious, fully intercepted API data."""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

boot = {
    "mode": "demonstracao",
    "company": {"id": "register-ui", "name": "Empresa fictícia"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
    "stores": [{"id": "store-a", "name": "Loja A"}, {"id": "store-b", "name": "Loja B"}],
    "registers": [
        {
            "id": "register-unused",
            "name": "Caixa livre",
            "store_id": "store-a",
            "currency": "EUR",
            "settings": "{}",
        },
        {
            "id": "register-used",
            "name": "Caixa com histórico",
            "store_id": "store-a",
            "currency": "EUR",
            "settings": "{}",
        },
    ],
    "accounts": [],
    "categories": [],
    "users": [],
    "payment_methods": {},
}
errors, writes = [], []
fail_edit = False


def mock_api(route):
    global fail_edit
    path = urlsplit(route.request.url).path
    if route.request.method == "POST":
        assert path.startswith("/api/registers/"), path
        body = route.request.post_data_json
        writes.append((path, body))
        identifier = path.split("/")[3]
        if path.endswith("/delete"):
            assert body == {"confirmed": True}
            if identifier == "register-used":
                route.fulfill(
                    status=409,
                    json={"detail": "Não é possível remover: a caixa tem sessões no histórico."},
                )
                return
            boot["registers"] = [row for row in boot["registers"] if row["id"] != identifier]
            result = {"ok": True, "id": identifier}
        elif fail_edit:
            fail_edit = False
            route.fulfill(status=409, json={"detail": "Não foi possível guardar esta alteração."})
            return
        else:
            result = next(row for row in boot["registers"] if row["id"] == identifier)
            result.update(body)
    else:
        result = boot if path == "/api/bootstrap" else {} if path == "/api/dashboard" else []
    route.fulfill(json=result)


with sync_playwright() as playwright:
    expect.set_options(timeout=15000)
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", mock_api)

    def open_registers():
        page.goto("http://127.0.0.1:5174")
        page.get_by_role("button", name="Tabelas", exact=True).click()
        page.get_by_role("button", name="Caixas", exact=True).click()

    open_registers()
    page.get_by_role("button", name="Editar caixa Caixa livre", exact=True).click()
    dialog = page.get_by_role("dialog", name="Editar caixa", exact=True)
    expect(dialog.get_by_role("textbox", name="Nome", exact=True)).to_have_value("Caixa livre")
    expect(dialog.get_by_role("combobox", name="Estabelecimento", exact=True)).to_have_value(
        "store-a"
    )
    dialog.get_by_role("textbox", name="Nome", exact=True).fill("Rascunho")
    dialog.get_by_role("button", name="Cancelar", exact=True).click()
    page.get_by_role("button", name="Continuar a editar", exact=True).click()
    expect(dialog.get_by_role("textbox", name="Nome", exact=True)).to_have_value("Rascunho")
    dialog.get_by_role("button", name="Cancelar", exact=True).click()
    page.get_by_role("button", name="Descartar alterações", exact=True).click()
    assert not writes

    page.get_by_role("button", name="Editar caixa Caixa livre", exact=True).click()
    dialog.get_by_role("textbox", name="Nome", exact=True).fill("Caixa corrigida")
    dialog.get_by_role("combobox", name="Estabelecimento", exact=True).select_option("store-b")
    fail_edit = True
    dialog.get_by_role("button", name="Guardar alterações", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text("Não foi possível guardar")
    expect(dialog.get_by_role("textbox", name="Nome", exact=True)).to_have_value("Caixa corrigida")
    dialog.get_by_role("button", name="Guardar alterações", exact=True).click()
    expect(dialog).to_have_count(0)
    expect(
        page.get_by_role("button", name="Editar caixa Caixa corrigida", exact=True)
    ).to_be_visible()
    open_registers()
    expect(
        page.get_by_role("button", name="Editar caixa Caixa corrigida", exact=True)
    ).to_be_visible()

    before = len(writes)
    page.get_by_role("button", name="Remover caixa Caixa corrigida", exact=True).click()
    expect(page.get_by_role("button", name="Cancelar", exact=True)).to_be_focused()
    page.get_by_role("button", name="Cancelar", exact=True).click()
    assert len(writes) == before
    page.get_by_role("button", name="Remover caixa Caixa com histórico", exact=True).click()
    page.get_by_role("button", name="Remover caixa", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("sessões no histórico")
    expect(
        page.get_by_role("button", name="Editar caixa Caixa com histórico", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="Remover caixa Caixa corrigida", exact=True).click()
    page.get_by_role("button", name="Remover caixa", exact=True).click()
    expect(
        page.get_by_role("button", name="Editar caixa Caixa corrigida", exact=True)
    ).to_have_count(0)
    expect(
        page.get_by_role("status").filter(has_text="Caixa «Caixa corrigida» removida.")
    ).to_be_visible()
    assert len(boot["registers"]) == 1

    page.get_by_role("button", name="Editar caixa Caixa com histórico", exact=True).click()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(dialog.get_by_role("button", name="Fechar", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="Guardar alterações", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    output = Path(__file__).resolve().parents[1] / ".local/register-management-ui"
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / "mobile.png"))
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 1440, "height": 1000})
    boot["user"]["role"] = "consulta"
    open_registers()
    expect(page.get_by_role("button", name="Editar caixa", exact=False)).to_have_count(0)
    expect(page.get_by_role("button", name="Remover caixa", exact=False)).to_have_count(0)
    assert not errors, errors
    page.unroute_all(behavior="ignoreErrors")
    browser.close()
print(
    "PASS: register edit, unsaved guards, errors/reload, explicit delete confirmation, protected history, read-only access and mobile; mocked API only."
)
