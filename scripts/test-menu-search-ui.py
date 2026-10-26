"""Menu navigation with intercepted APIs; no real requests or writes."""
import re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

boot = {
    "mode": "demonstracao", "company": {"id": "menu-test", "name": "Empresa de teste"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "user": {"id": "ui", "name": "Utilizador", "role": "administrador"},
    "stores": [], "registers": [], "accounts": [], "categories": [], "users": [], "payment_methods": {},
}
errors = []

def mock(route):
    assert route.request.method == "GET", "Menu search must not mutate data"
    path = urlsplit(route.request.url).path
    if path == "/api/bootstrap":
        result = boot
    elif path == "/api/dashboard":
        result = {}
    elif path == "/api/stock/catalog":
        result = {"articles": [], "warehouses": [], "suppliers": [], "permissions": {}, "settings": {}, "policy": {"mode": "local"}, "users": []}
    elif path.startswith("/api/stock/"):
        result = {"rows": [], "totals": {}, "limitations": []}
    else:
        result = []
    route.fulfill(json=result)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.route("**/api/**", mock)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:5174/?pagina=Apar%C3%AAncia")
    search = page.get_by_role("combobox", name="Pesquisar menus", exact=True)
    expect(search).to_be_visible()
    search.fill("reconciliacao")
    expect(page.get_by_role("option", name=re.compile("Reconciliação automática"))).to_be_visible()
    expect(page.get_by_role("listbox", name="Menus encontrados").get_by_role("option")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(search).to_have_attribute("aria-expanded", "false")
    page.keyboard.press("Control+k")
    expect(search).to_be_focused()
    search.fill("artigos")
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Artigos, stock e validades", exact=True)).to_be_visible()
    expect(search).to_have_value("")
    expect(search).to_have_attribute("aria-expanded", "false")
    search.fill("arquivo")
    options = page.get_by_role("listbox", name="Menus encontrados").get_by_role("option")
    expect(options.first).to_have_attribute("aria-selected", "true")
    page.keyboard.press("ArrowDown")
    expect(options.nth(1)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("ArrowUp")
    expect(options.first).to_have_attribute("aria-selected", "true")
    search.fill("zzznenhummenu")
    expect(page.get_by_text("Nenhum menu encontrado.", exact=True)).to_be_visible()
    page.get_by_role("button", name="Limpar pesquisa de menus").click()
    expect(search).to_have_value("")
    search.fill("fornecedores")
    output = Path(".local/menu-search")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "desktop.png"), animations="disabled")
    page.get_by_role("heading", name="Artigos, stock e validades", exact=True).click()
    expect(search).to_have_attribute("aria-expanded", "false")
    page.set_viewport_size({"width": 390, "height": 844})
    search.fill("artigos")
    expect(page.get_by_role("option", name=re.compile("Artigos e validades"))).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(output / "mobile.png"), animations="disabled")
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    page.screenshot(path=str(output / "mobile-dark.png"), animations="disabled")
    boot["user"]["role"] = "operador"
    page.reload()
    search.fill("regime de iva")
    expect(page.get_by_text("Nenhum menu encontrado.", exact=True)).to_be_visible()
    search.fill("verificacao")
    expect(page.get_by_text("Nenhum menu encontrado.", exact=True)).to_be_visible()
    search.fill("saf")
    expect(page.get_by_role("option", name=re.compile("Importar SAF-T de vendas"))).to_be_visible()
    boot["integration"]["mode"] = "integrated"
    page.reload()
    search.fill("saf")
    expect(page.get_by_text("Nenhum menu encontrado.", exact=True)).to_be_visible()
    assert not errors, errors
    browser.close()
print("PASS: menu search, accents, keyboard, navigation, role/mode filters and mobile/light/dark; no writes.")
