"""Fixed header and guarded record browsing, including filtered page boundaries. Synthetic APIs only."""
import copy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-inventory-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], ns)
ns["catalog"]["articles"] = [{**copy.deepcopy(ns["article"]), "id": f"browse-{i}", "code": f"NAV{i:03}", "name": f"Navegar {i:02}"} for i in range(1, 27)]
calls = []

def mock(route):
    if urlsplit(route.request.url).path == "/api/stock/catalog":
        calls.append(parse_qs(urlsplit(route.request.url).query))
    return ns["mock"](route)

with sync_playwright() as p:
    browser = p.chromium.launch()
    for width in (1440, 390):
        page = browser.new_page(viewport={"width": width, "height": 900}, locale="pt-PT")
        page.set_default_timeout(30000)
        page.route("**/api/**", mock)
        page.goto("http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos")
        page.get_by_label("Pesquisar código, designação, barras ou referência do fornecedor", exact=True).fill("Navegar")
        page.wait_for_timeout(600)
        page.get_by_label("Mais ações para Navegar 25", exact=True).click()
        page.get_by_role("menuitem", name="Editar Navegar 25", exact=True).click()
        dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
        header = dialog.locator(".record-sheet-header")
        initial_y = header.bounding_box()["y"]
        dialog.locator(".article-sheet-layout").evaluate("e => e.scrollTop = e.scrollHeight")
        assert abs(header.bounding_box()["y"] - initial_y) < 1
        expect(dialog.get_by_role("button", name="Guardar", exact=True)).to_be_visible()
        dialog.get_by_role("tab", name="Stock", exact=True).click()
        dialog.get_by_label("Artigo seguinte", exact=True).click()
        expect(dialog.get_by_label("Código interno", exact=True)).to_have_value("NAV026")
        expect(dialog.get_by_label("Artigo seguinte", exact=True)).to_be_disabled()
        expect(dialog.get_by_role("tab", name="Stock", exact=True)).to_have_attribute("aria-selected", "true")
        dialog.get_by_label("Artigo anterior", exact=True).click()
        expect(dialog.get_by_label("Código interno", exact=True)).to_have_value("NAV025")
        dialog.get_by_label("Designação", exact=True).fill("Rascunho por guardar")
        dialog.get_by_label("Artigo seguinte", exact=True).click()
        discard = page.get_by_role("alertdialog", name="Descartar alterações?", exact=True)
        discard.get_by_role("button", name="Continuar a editar", exact=True).click()
        expect(dialog.get_by_label("Designação", exact=True)).to_have_value("Rascunho por guardar")
        dialog.get_by_label("Artigo seguinte", exact=True).click()
        discard.get_by_role("button", name="Descartar alterações", exact=True).click()
        expect(dialog.get_by_label("Código interno", exact=True)).to_have_value("NAV026")
        dialog.get_by_role("button", name="Fechar", exact=True).click()
        page.get_by_label("Mais ações para Navegar 25", exact=True).click()
        page.get_by_role("menuitem", name="Consultar", exact=True).click()
        consultation = page.get_by_role("dialog", name="Consultar artigo", exact=True)
        consultation.get_by_label("Artigo seguinte", exact=True).click()
        expect(consultation.get_by_label("Código interno", exact=True)).to_have_value("NAV026")
        expect(consultation.get_by_label("Código interno", exact=True)).to_have_attribute("readonly", "")
        assert any(c.get("page") == ["2"] and c.get("q") == ["Navegar"] for c in calls)
        assert not ns["writes"], ns["writes"]
        assert not consultation.evaluate("e => e.scrollWidth > e.clientWidth")
        page.close()
    browser.close()
print("PASS: fixed header, previous/next across filtered pages, last-record boundary, section retained, dirty guard and read-only mode; desktop/mobile.")
