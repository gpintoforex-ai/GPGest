"""Article form layout and interactions using synthetic intercepted APIs only."""
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-inventory-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], ns)
OUT = ROOT / ".local/article-layout"
OUT.mkdir(parents=True, exist_ok=True)

def mock(route):
    if route.request.url.split("?")[0].endswith("/stock/articles/p1/details"):
        return route.fulfill(json={
            "company_name": "Empresa fictícia", "warehouses": [{"id": "w1", "name": "Principal", "quantity": "5"}],
            "history": [], "price_history": [], "purchase_suppliers": [],
            "insights": {"source": "Movimentos fictícios de teste", "available": True, "sold_30_days": "12", "incoming_quantity": "60", "last_sale": "2026-11-01", "last_purchase": "2026-10-30", "monthly": [{"month": "2026-10", "sold": "12", "purchased": "20"}], "documents": [{"id": "test-sale", "kind": "sale", "date": "2026-11-01", "quantity": "-2", "document": "FT TESTE/1"}]},
        })
    return ns["mock"](route)


with sync_playwright() as p:
    expect.set_options(timeout=15000)
    browser = p.chromium.launch()
    for width, theme in [(1440, "light"), (1024, "light"), (390, "light"), (1440, "dark"), (1024, "dark"), (390, "dark")]:
        print(f"Checking {width}px {theme}", flush=True)
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        page.set_default_timeout(60000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", mock)
        page.route("https://images.test/product.svg", lambda route: route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><rect width="160" height="160" fill="#f0faf8"/><rect x="50" y="40" width="60" height="100" rx="12" fill="#087f78"/><rect x="55" y="20" width="50" height="25" rx="5" fill="#344054"/><rect x="50" y="70" width="60" height="42" fill="white"/></svg>'))
        page.goto("http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos")
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
        page.get_by_role("menuitem", name="Editar Suplemento de teste", exact=True).click()
        dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
        help_text = dialog.get_by_text("Sobre esta ficha", exact=True)
        expect(help_text).not_to_be_visible()
        dialog.get_by_label("Informação da ficha do artigo", exact=True).click()
        expect(help_text).to_be_visible()
        dialog.get_by_label("Informação da ficha do artigo", exact=True).click()
        expect(help_text).not_to_be_visible()
        dialog.get_by_label("Definir fotografia", exact=True).click()
        dialog.get_by_label("URL da fotografia (HTTPS)", exact=True).fill("https://images.test/product.svg")
        dialog.get_by_label("Alterar fotografia", exact=True).click()
        photo = dialog.get_by_role("img", name="Fotografia de Suplemento de teste", exact=True)
        expect(photo).to_be_visible()
        expect(dialog.get_by_role("tablist", name="Ficha do artigo")).to_have_attribute("aria-orientation", "horizontal" if width <= 760 else "vertical")
        expect(dialog.get_by_label("Resumo do artigo")).to_contain_text("60,00")
        expect(dialog.get_by_label("Resumo do artigo")).to_contain_text("12,00")
        if width > 760:
            tabs = dialog.get_by_role("tablist", name="Ficha do artigo").bounding_box()
            main = dialog.locator(".article-sheet-main").bounding_box()
            assert tabs["x"] + tabs["width"] <= main["x"], (tabs, main)
        dialog.evaluate("e => e.scrollTop = 0")
        dialog.screenshot(path=str(OUT / f"article-{width}-{theme}.png"))
        tab = dialog.get_by_role("tab", name="Stock", exact=True)
        tab.click()
        page.keyboard.press("ArrowDown")
        expect(dialog.get_by_role("tab", name="Compras e fornecedores", exact=True)).to_have_attribute("aria-selected", "true")
        dialog.get_by_role("tab", name="Vendas", exact=True).click()
        expect(dialog.get_by_text("FT TESTE/1", exact=True)).to_be_visible()
        expect(dialog.get_by_text("Indisponível: sem histórico de stock médio", exact=True)).to_be_visible()
        dialog.get_by_role("tab", name="Geral", exact=True).click()
        expect(photo).to_be_visible()
        assert not dialog.evaluate("e => e.scrollWidth > e.clientWidth"), "Dialog overflows horizontally"
        dialog.evaluate("e => e.scrollTop = 0")
        dialog.get_by_role("button", name="Fechar", exact=True).click()
        discard = page.get_by_role("alertdialog", name="Descartar alterações?", exact=True)
        expect(discard).to_be_visible()
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        expect(dialog).not_to_be_visible()
        page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
        page.get_by_role("menuitem", name="Editar Suplemento de teste", exact=True).click()
        page.get_by_role("dialog", name="Editar artigo", exact=True).get_by_role("button", name="Duplicar", exact=True).click()
        duplicate = page.get_by_role("dialog", name="Novo artigo", exact=True)
        expect(duplicate.get_by_label("Código interno", exact=True)).to_have_value("P001-COPIA")
        expect(duplicate.get_by_label("Códigos de barras (separados por |)", exact=True)).to_have_value("")
        duplicate.get_by_role("button", name="Fechar", exact=True).click()
        page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
        page.get_by_role("menuitem", name="Editar Suplemento de teste", exact=True).click()
        page.get_by_role("dialog", name="Editar artigo", exact=True).get_by_label("Pesquisar artigo pelo código de barras", exact=True).click()
        expect(page.get_by_label("Pesquisar código, designação, barras ou referência do fornecedor", exact=True)).to_have_value("123456789")
        page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
        page.get_by_role("menuitem", name="Consultar", exact=True).click()
        consultation = page.get_by_role("dialog", name="Consultar artigo", exact=True)
        expect(consultation.get_by_label("Código interno", exact=True)).to_have_attribute("readonly", "")
        expect(consultation.get_by_role("switch", name="Ativo", exact=True)).to_be_disabled()
        expect(consultation.locator('button[type="submit"]')).to_have_count(0)
        assert not ns["writes"], ns["writes"]
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: reference layout, photo, real API indicators, keyboard, dirty guard and duplicate; desktop/tablet/mobile, light/dark.")
