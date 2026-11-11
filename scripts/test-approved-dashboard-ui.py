"""Check fidelity to the approved work dashboard using isolated synthetic API fixtures."""
import copy
import os
import re
import runpy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "scripts/test-workspace-navigation-ui.py"), run_name="fixture")
ns = fixture["ns"]
center_fixture = runpy.run_path(str(ROOT / "scripts/test-order-center-board-ui.py"), run_name="fixture")
OUT = ROOT / ".local/zela-fidelity"
OUT.mkdir(parents=True, exist_ok=True)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in [int(value) for value in os.getenv("GPGEST_TEST_WIDTHS", "1440,1011,768,390").split(",")]:
            for theme in ["light", "dark"]:
                boot = copy.deepcopy(ns["boot"])
                boot["company"]["id"] = f"fidelity-{width}-{theme}"
                boot["integration"] = {"mode": "local", "provider": "none"}
                data = copy.deepcopy(fixture["analytics"])
                data["drafts"] = [{"id": "draft", "supplier": "Fornecedor de teste", "net_cents": 12800, "total_cents": 18000, "minimum_missing_cents": 0, "shipping_missing_cents": 2000, "currency": "EUR"}]
                center_data = center_fixture["scenario"]()
                errors, writes = [], []
                def intercept(route):
                    path = urlsplit(route.request.url).path.removeprefix("/api")
                    if route.request.method != "GET":
                        writes.append(path)
                    responses = {
                        "/bootstrap": boot, "/order-cycle/analytics": data,
                        "/order-cycle/supply-center": center_data[2],
                        "/order-cycle/recommendations": {"items": center_data[3]},
                        "/order-cycle/stock": {"items": []},
                        "/customer-orders": {"orders": [], "simulations": [], "can_buy": True, "can_write": True},
                        "/stock/catalog": {"articles": [], "warehouses": []},
                        "/customer-orders/catalog": {"customers": [], "suppliers": []},
                        "/customer-orders/history": [], "/procurement": {"documents": []},
                    }
                    if path in responses:
                        return route.fulfill(json=responses[path])
                    return ns["intercept"](route)
                context = browser.new_context(viewport={"width": width, "height": 1000}, locale="pt-PT")
                context.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/**", intercept)
                page.goto("http://127.0.0.1:5174/entrar?pagina=Dashboard")
                overview = page.get_by_role("region", name="Visão de trabalho", exact=True)
                expect(overview.get_by_role("heading", name=re.compile("Estas são as prioridades"))).to_be_visible(timeout=20000)
                expect(overview.locator(".priority-kpi")).to_have_count(4)
                primary = overview.get_by_role("button", name="Centro de Encomendas", exact=True)
                assert primary.evaluate("node=>getComputedStyle(node).backgroundColor") == ("rgb(29, 78, 216)" if theme == "light" else "rgb(37, 99, 235)")
                assert primary.evaluate("node=>getComputedStyle(node).color") == "rgb(255, 255, 255)"
                expect(overview.locator(".priority-kpi").first.locator("strong")).to_have_text("1")
                expect(overview.locator(".priority-kpi").last.locator("strong")).to_contain_text("128,00")
                expect(overview.get_by_text("1 pedido(s) ultrapassaram o prazo", exact=True)).to_be_visible()
                expect(overview.get_by_text("Fornecedor de teste: abaixo dos portes grátis", exact=True)).to_be_visible()
                expect(overview.get_by_role("img", name="Pedidos reais criados nas últimas seis semanas", exact=True)).to_be_visible()
                expect(overview.locator(".priority-route-grid > button")).to_have_count(4)
                overview.get_by_role("button", name="Ver todos os destinos", exact=True).click()
                expect(overview.locator(".priority-destinations > section")).to_have_count(12)
                overview.get_by_role("button", name="Mostrar atalhos principais", exact=True).click()
                financial = page.get_by_role("region", name="Gestão financeira e operação de caixa", exact=True)
                expect(financial).to_be_visible()
                expect(financial.get_by_role("group", name="Filtros do painel", exact=True)).to_be_visible()
                expect(page.locator("details.priority-financial")).to_have_count(0)
                expect(financial).to_contain_text("Os indicadores financeiros não estão disponíveis para o seu perfil.")
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (width, theme)
                page.evaluate("document.activeElement?.blur(); window.scrollTo(0,0)")
                page.screenshot(path=str(OUT / f"dashboard-{width}-{theme}.png"), full_page=True)
                overview.get_by_role("button", name="Ver origem e registos →", exact=True).click()
                drawer = page.get_by_role("dialog", name="Pedidos que sustentam o indicador", exact=True)
                expect(drawer).to_contain_text("PC-TESTE")
                drawer.get_by_role("button", name="Fechar", exact=True).click()
                expect(drawer).to_have_count(0)
                page.go_back()
                expect(page).to_have_url(re.compile("pagina=Dashboard"))
                expect(page.get_by_role("combobox", name="Filtrar loja", exact=True)).to_have_value("")
                expect(overview).to_be_visible()
                assert not errors, errors
                assert not writes, writes
                print(f"PASS {width}px {theme}: approved layout, 4 real-source KPIs, priorities, 6-week chart, 4 area cards, 12 destination groups, financial summary visible without disclosure, permissions retained, source drilldown; no writes/errors/overflow", flush=True)
                context.close()
        browser.close()


if __name__ == "__main__":
    run()
