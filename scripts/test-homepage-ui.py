"""Read-only public landing-page smoke test, without credentials or real mutations."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto("http://127.0.0.1:5174/")
    expect(page.get_by_role("heading", level=1)).to_contain_text("O teu negócio.")
    expect(page.get_by_text("DADOS DEMONSTRATIVOS", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Bancos", exact=True).click()
    expect(page.get_by_text("O banco e os documentos, em sintonia.", exact=True)).to_be_visible()
    for link in page.locator(".hp-nav a").all():
        assert page.locator(link.get_attribute("href")).count() == 1
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(root / ".local/iazela-home-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("http://127.0.0.1:5174/")
    page.get_by_role("button", name="Abrir menu", exact=True).click()
    page.get_by_role("navigation", name="Navegação principal").get_by_role("link", name="Como funciona", exact=True).click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(root / ".local/iazela-home-mobile.png"), full_page=True)
    page.goto("http://127.0.0.1:5174/entrar")
    expect(page.get_by_label("NIF da empresa", exact=True)).to_be_visible()
    page.goto("http://127.0.0.1:5174/?nif=510774318")
    expect(page.get_by_label("NIF da empresa", exact=True)).to_have_value("510774318")
    page.goto("http://127.0.0.1:5174/plataforma")
    expect(page.get_by_label("Email da plataforma", exact=True)).to_be_visible()
    page.get_by_role("switch", name="Tema escuro", exact=True).click()
    page.goto("http://127.0.0.1:5174/")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.screenshot(path=str(root / ".local/iazela-home-dark.png"), full_page=True)
    fixtures = root / "scripts/test-visual-template-ui.py"
    namespace = {"__file__": str(fixtures)}
    exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], namespace)
    workspace = browser.new_page(viewport={"width": 1440, "height": 1000})
    workspace.route("**/api/**", namespace["intercept"])
    workspace.goto("http://127.0.0.1:5174/")
    expect(workspace.get_by_role("button", name="Contabilidade", exact=True)).to_be_visible()
    expect(workspace.locator(".iazela-home")).to_have_count(0)
    workspace.get_by_role("button", name="Contabilidade", exact=True).click()
    workspace.get_by_role("button", name="Conferência documental", exact=True).click()
    expect(workspace.get_by_label("Fornecedor ou documento", exact=True)).to_be_visible()
    assert not errors, errors
    browser.close()
print("Landing page passed: navigation, demo tabs, mobile, themes, login routes, authenticated workspace, no JavaScript errors.")
