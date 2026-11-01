"""Avisos na barra do menu: sino com o total, painel por tema e navegação para a página. Só fixtures."""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-visual-template-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
ALERTS = {"total": 3, "groups": [
    {"id": "pedidos_atrasados", "label": "Pedidos de clientes em atraso", "count": 2, "tone": "danger",
     "page": "Encomendas de clientes", "params": {"area.pedidos-clientes": "pedidos"}, "items": ["PC-0001 · Ana", "PC-0002 · Bruno"]},
    {"id": "devolucoes_validade", "label": "Produtos a devolver por validade", "count": 1, "tone": "warning",
     "page": "Devoluções a fornecedores", "params": {}, "items": ["CAF · lote L1 · validade 20/11/2026"]},
]}
OUT = ROOT / ".local" / "top-alerts"
OUT.mkdir(parents=True, exist_ok=True)


def intercept(route):
    if urlsplit(route.request.url).path == "/api/alerts":
        return route.fulfill(json=ALERTS)
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for width, height in [(1440, 900), (390, 844)]:
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("pageerror", lambda e: errors.append((e.stack or str(e))[:500]))
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/")
        bell = page.get_by_label("Avisos: 3")
        expect(bell).to_be_visible()
        bell.click()
        panel = page.get_by_role("region", name="Avisos pendentes")
        expect(panel).to_contain_text("Pedidos de clientes em atraso")
        expect(panel).to_contain_text("PC-0001 · Ana")
        page.screenshot(path=str(OUT / f"open-{width}.png"))
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        box = panel.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width, box  # painel inteiro dentro do ecrã
        panel.get_by_role("button", name="Abrir Devoluções a fornecedores").click()
        page.wait_for_timeout(500)
        assert "Devolu" in page.url or "Devolu%C3%A7%C3%B5es" in page.url, page.url
        assert not errors, errors
        page.close()
    browser.close()
print("Top alerts UI passed: bell with total, grouped panel, navigation to the page, desktop and mobile. Fixtures only.")
