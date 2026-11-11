"""Approved workspace navigation and analytics, using only intercepted synthetic APIs."""
import copy
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-visual-template-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
OUT = ROOT / ".local/zela-validation"
OUT.mkdir(parents=True, exist_ok=True)


def contrast(foreground, background):
    def luminance(color):
        values = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(v * weight for v, weight in zip(linear, [.2126, .7152, .0722]))
    a, b = sorted([luminance(foreground), luminance(background)])
    return (b + .05) / (a + .05)

analytics = {
    "read_only": True, "start": "2026-10-01", "end": "2026-10-30", "unit": "day",
    "definitions": {"population": "Pedidos criados no período: dados fictícios de teste.", "completed": "Entregas completas.", "on_time": "Prazo histórico.", "rotation": "Saídas de venda.", "drafts": "Rascunhos atuais."},
    "summary": {"created": 1, "pending": 1, "late": 1, "completed": 0, "average_days": None, "on_time_percent": None, "on_time_count": 0, "dated_deliveries": 0, "unknown_deadlines": 0, "drafts_below_minimum": 0},
    "states": [{"id": "waiting", "label": "A aguardar", "count": 1}],
    "series": [{"key": "2026-10-25", "created": 1, "completed": 0}],
    "articles": [{"article_id": "article", "description": "Artigo de teste", "quantity": "5", "order_ids": ["order"], "provisional": False}],
    "rotation": [{"article_id": "article", "description": "Artigo de teste", "quantity": "3", "daily": "0.1", "movement_count": 1, "movements_truncated": False, "movements": [{"id": "movement-test", "date": "2026-10-25", "warehouse": "Armazém de teste", "quantity": "3"}]}],
    "drafts": [], "forecast_by_currency": {},
    "orders": [{"id": "order", "number": "PC-TESTE", "customer": "Cliente de teste", "created_at": "2026-10-25", "due_date": "2026-10-26", "state": "waiting", "state_label": "A aguardar", "late": True, "article_ids": ["article"]}],
    "completed_orders": [],
}


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in [1440, 768, 390]:
            for theme in ["light", "dark"]:
                boot = copy.deepcopy(ns["boot"])
                boot["company"]["id"] = f"navigation-test-{width}-{theme}"
                boot["mode"] = "local"
                boot["integration"] = {"mode":"local","provider":"none"}
                errors, writes = [], []
                def intercept(route):
                    path = urlsplit(route.request.url).path.removeprefix("/api")
                    if route.request.method != "GET":
                        writes.append(path)
                    responses = {
                        "/bootstrap": boot,
                        "/customer-orders": {"orders": [], "simulations": [], "can_buy": True, "can_write": True},
                        "/stock/catalog": {"articles": [], "warehouses": []},
                        "/customer-orders/catalog": {"customers": [], "suppliers": []},
                        "/customer-orders/history": [], "/procurement": {"documents": []},
                        "/order-cycle/analytics": analytics,
                        "/order-cycle/reviews": {"items": []},
                        "/order-cycle/stock": {"items": []},
                        "/order-cycle/recommendations": {"items": []},
                        "/order-cycle/supply-center": {"store_id": "demo", "stock_known": True, "read_only": True, "metrics": {}, "articles": [], "suppliers": [], "supplier_catalog": []},
                    }
                    if path in responses:
                        return route.fulfill(json=responses[path])
                    return ns["intercept"](route)
                context = browser.new_context(viewport={"width": width, "height": 1000}, locale="pt-PT")
                context.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/**", intercept)
                page.goto("http://127.0.0.1:5174/entrar?pagina=Encomendas%20de%20clientes")
                expect(page.get_by_role("heading", name="Centro de Encomendas", exact=True)).to_be_visible(timeout=20000)
                nav = page.get_by_role("navigation", name="Navegação principal", exact=True)
                def show_menu():
                    toggle = page.get_by_role("button", name="Abrir menu", exact=True)
                    if toggle.is_visible() and "visible" not in (page.locator(".sidebar").get_attribute("class") or ""):
                        toggle.click()
                show_menu()
                expect(nav.locator(".workspace-group")).to_have_count(12)
                expect(nav.locator('[aria-current="page"]')).to_have_count(1)
                nav.locator('button.workspace-group[aria-label="Compras"]').click()
                # Clicking the already active group toggles it; open if necessary.
                group = nav.locator('button.workspace-group[aria-label="Compras"]')
                if group.get_attribute("aria-expanded") != "true":
                    group.click()
                before = page.evaluate("history.length")
                nav.get_by_role("button", name="Simulador de encomendas", exact=True).click()
                tasks = page.get_by_role("navigation", name="Áreas das encomendas de clientes", exact=True)
                expect(tasks.get_by_role("button", name="Encomendas a fornecedores", exact=True)).to_have_attribute("aria-current", "page")
                assert parse_qs(urlsplit(page.url).query)["area.pedidos-clientes"] == ["simulacoes"]
                assert page.evaluate("history.length") == before + 1
                search = page.get_by_role("combobox", name="Pesquisa global", exact=True)
                search.fill("Necessidades de compra")
                search.press("Enter")
                expect(page.get_by_role("heading", name="Centro de Encomendas", exact=True)).to_be_visible(timeout=20000)
                assert parse_qs(urlsplit(page.url).query)["filtro.abastecimento"] == ["buy"]
                page.go_back()
                expect(tasks.get_by_role("button", name="Encomendas a fornecedores", exact=True)).to_have_attribute("aria-current", "page")
                page.go_forward()
                expect(page.get_by_role("heading", name="Centro de Encomendas", exact=True)).to_be_visible(timeout=20000)
                page.reload()
                expect(page.get_by_role("heading", name="Centro de Encomendas", exact=True)).to_be_visible(timeout=20000)
                show_menu()
                expect(nav.locator('[aria-current="page"]')).to_have_count(1)
                expect(nav.locator('[aria-current="page"]')).to_have_attribute("aria-label", "Necessidades de compra")
                nav.get_by_role("button", name="Relatórios", exact=True).click()
                nav.get_by_role("button", name="Indicadores de encomendas", exact=True).click()
                page.wait_for_function("new URLSearchParams(location.search).get('area.pedidos-clientes') === 'analise'")
                if errors:
                    print(errors, flush=True)
                region = page.get_by_role("region", name="Indicadores do ciclo de encomendas", exact=True)
                expect(region.get_by_role("heading", name="Encomendas: progresso e resultados", exact=True)).to_be_visible()
                expect(region.get_by_text("Sem prazo histórico", exact=True)).to_be_visible()
                region.get_by_role("button", name="Consultar Pedidos criados", exact=True).click()
                drawer = page.get_by_role("dialog", name="Pedidos que sustentam o indicador", exact=True)
                expect(drawer.get_by_role("table", name="pedidos que sustentam o indicador", exact=True)).to_contain_text("PC-TESTE")
                drawer.get_by_role("button", name="Fechar", exact=True).click()
                rotation = region.get_by_role("table", name="rotatividade de artigos", exact=True)
                rotation.get_by_role("button").last.click()
                drawer = page.get_by_role("dialog")
                expect(drawer.get_by_text("movement-test", exact=True)).to_be_visible()
                drawer.get_by_role("button", name="Fechar", exact=True).click()
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (width, theme)
                expect(region.locator(".dash-axis").first).not_to_contain_text("€")
                assert region.locator(".dash-hit").first.evaluate("node => getComputedStyle(node).fill") == "rgba(0, 0, 0, 0)"
                assert region.locator(".dash-legend").evaluate("node => getComputedStyle(node).listStyleType") == "none"
                colors = page.evaluate("""() => {
                    const pairs = [['--text-primary','--surface-primary'],['--text-secondary','--surface-primary'],['--text-label','--surface-primary'],['--text-placeholder','--surface-primary'],['--context-success','--context-success-bg'],['--context-warning','--context-warning-bg'],['--context-info','--context-info-bg'],['--context-suggestion','--context-suggestion-bg'],['--action-primary-ink','--surface-primary']];
                    return pairs.map(([fg,bg]) => { const node=document.createElement('span');node.style.cssText=`color:var(${fg});background:var(${bg})`;document.body.append(node);const style=getComputedStyle(node);const result={fg,bg,color:style.color,background:style.backgroundColor};node.remove();return result; });
                }""")
                for pair in colors:
                    pair["ratio"] = round(contrast(pair["color"], pair["background"]), 2)
                    assert pair["ratio"] >= 4.5, (theme, pair)
                (OUT / f"contrast-{theme}.json").write_text(json.dumps(colors, indent=2), encoding="utf-8")
                assert not errors, errors
                assert not writes, writes
                page.screenshot(path=str(OUT / f"analytics-{width}-{theme}.png"), full_page=True)
                print(f"PASS {width}px {theme}: 12 groups, one active item, one history entry, back/forward/reload, keyboard search, metric and ledger drilldowns, no writes/overflow/runtime errors", flush=True)
                context.close()
        browser.close()


if __name__ == "__main__":
    run()
