"""Pagination and filtered totals with fictitious records only."""
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright
exec(Path(__file__).with_name("test-liquidity-ui.py").read_text(encoding="utf-8").split("\nwith sync_playwright() as playwright:")[0])

def intercept_pages(route):
    if urlsplit(route.request.url).path != "/api/liquidity/forecast":
        return liquidity_intercept(route)
    class Response:
        request = route.request
        def fulfill(self, **kwargs):
            value = json.loads(kwargs["body"])
            value["scheduled"] = [{**event, "key": "wintouch:demo-"+str(i), "entity": "Entidade Demo "+str(i), "title": "Entidade Demo "+str(i)+" · FT "+str(i)} for i in range(1, 24)]
            kwargs["body"] = json.dumps(value)
            route.fulfill(**kwargs)
    liquidity_intercept(Response())

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.route("**/api/**", intercept_pages)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Previsão de liquidez", exact=True).click()
    page.get_by_role("tab", name="Recebimentos e pagamentos", exact=True).click()
    flows = page.get_by_role("region", name="Recebimentos e pagamentos previstos", exact=True)
    expect(flows.locator("tbody tr")).to_have_count(10)
    expect(flows.get_by_text("A mostrar 1–10 de 23 registos", exact=True)).to_be_visible()
    totals = flows.locator(".liquidity-selected-total").inner_text()
    flows.get_by_role("button", name="Seguinte", exact=True).click()
    expect(flows.get_by_text("Página 2 de 3", exact=True)).to_be_visible()
    expect(flows.locator("tbody tr")).to_have_count(10)
    assert flows.locator(".liquidity-selected-total").inner_text() == totals
    flows.get_by_role("button", name="Seguinte", exact=True).click()
    expect(flows.locator("tbody tr")).to_have_count(3)
    expect(flows.get_by_role("button", name="Seguinte", exact=True)).to_be_disabled()
    flows.get_by_label("Registos por página", exact=True).select_option("25")
    expect(flows.locator("tbody tr")).to_have_count(23)
    expect(flows.get_by_text("Página 1 de 1", exact=True)).to_be_visible()
    flows.get_by_placeholder("Pesquisar entidade", exact=True).fill("Entidade Demo 22")
    expect(flows.locator("tbody tr")).to_have_count(1)
    expect(flows.get_by_text("A mostrar 1–1 de 1 registos", exact=True)).to_be_visible()
    browser.close()
print("PASS: paginação, tamanho de página, número de registos, filtros e totais de todas as páginas")
