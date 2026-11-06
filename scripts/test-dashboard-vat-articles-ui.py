"""VAT estimate and article ranking, with isolated synthetic responses."""
import copy
import runpy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "scripts/test-dashboard-scope-ui.py"), run_name="fixture")
ns = fixture["ns"]
OUT = ROOT / ".local/dashboard-vat-articles"
OUT.mkdir(parents=True, exist_ok=True)
boot = copy.deepcopy(ns["boot"])
boot["user"]["role"] = "gestor"
boot["integration"] = {"mode": "local", "provider": "none"}
data = fixture["overview"](10000)
data["permissions"]["costs"] = True
data["vat"] = {"status": "ok", "balance_cents": 1150, "output_cents": 2300, "deductible_cents": 1150}
data["top_articles"] = {"status": "ok", "source": "erp", "incomplete_documents": 1, "rows": [{"code": "P001", "name": "Artigo de teste", "quantity": "12.5", "sales_cents": 10000}]}


def intercept(route):
    path = urlsplit(route.request.url).path
    if path == "/api/bootstrap":
        return route.fulfill(json=boot)
    if path == "/api/dashboard/overview":
        return route.fulfill(json=data)
    if path == "/api/order-cycle/analytics":
        return route.fulfill(json=fixture["fixture"]["analytics"])
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for width in (1440, 390):
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Dashboard")
        card = page.locator(".dash-kpi").filter(has_text="Previsão de IVA")
        expect(card).to_contain_text("11,50")
        expect(card).to_contain_text("A pagar estimado")
        page.screenshot(path=str(OUT / f"vat-{width}.png"), full_page=True)
        page.get_by_role("tab", name="Artigos mais vendidos", exact=True).click()
        expect(page.get_by_role("heading", name="Top 10 de artigos vendidos", exact=True)).to_be_visible()
        expect(page.get_by_text("Top parcial:", exact=False)).to_be_visible()
        row = page.get_by_role("row").filter(has_text="Artigo de teste")
        expect(row).to_contain_text("P001")
        expect(row).to_contain_text("12,5")
        expect(row).to_contain_text("100,00")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.screenshot(path=str(OUT / f"articles-{width}.png"), full_page=True)
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: VAT estimate and top articles visible on desktop/mobile; no runtime errors or page overflow.")
