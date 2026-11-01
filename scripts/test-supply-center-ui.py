"""Supply-center interaction checks with synthetic data; never writes to a real API."""

import copy
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-visual-template-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in [1440, 390]:
            boot = copy.deepcopy(ns["boot"])
            boot["company"]["id"] = "supply-center-ui"
            store = boot["stores"][0]["id"]
            terms = {"currency": "EUR", "minimum_cents": 10000, "minimum_basis": "net",
                     "discount": "0", "tiers": [], "shipping_cents": 800,
                     "free_shipping_cents": None, "shipping_vat": "23", "vat": "23",
                     "lead_days": 7, "pending_days": 7, "confirmation_days": 3,
                     "pickup_days": 7, "email": "", "notes": "", "prices": {"article": "10"}}
            totals = {"currency": "EUR", "gross_cents": 6000, "discount_cents": 0,
                      "net_cents": 6000, "eligible_cents": 6000, "missing_cents": 4000,
                      "shipping_cents": 800, "shipping_vat_cents": 184,
                      "vat_cents": 1380, "total_cents": 8364, "lines": []}
            customers, orders, calls, errors = [], [], [], []
            for index, name in enumerate(["Ana", "Bruno"]):
                requested, need, cover = [("6", "2", "4"), ("4", "4", "0")][index]
                customers.append({"line_id": f"line-{index}", "order_id": f"order-{index}",
                                  "number": f"PC-UI-{index}", "customer": name,
                                  "requested_at": "2026-10-25", "due_date": "2026-11-02",
                                  "requested": requested, "delivered": "0", "reserved": "0",
                                  "ready_quantity": "0", "stock_cover": cover,
                                  "incoming_cover": "0", "ordered_pending": "0", "simulated": "0",
                                  "needed": need, "supplier_id": "supplier", "state": "A aguardar encomenda",
                                  "urgent": index == 0})
                orders.append({"id": f"order-{index}", "number": f"PC-UI-{index}", "store_id": store,
                               "customer_name": name, "customer_id": None, "requested_at": "2026-10-25",
                               "due_date": "2026-11-02", "created_at": "2026-10-25", "revision": 0,
                               "priority": "normal", "closed": False, "ready": False, "late": False,
                               "lines": [{"id": f"line-{index}", "article_id": "article", "article_code": "CAF",
                                          "description": "Café de teste", "supplier_id": "supplier", "state": "waiting",
                                          "state_label": "A aguardar encomenda", "provisional": False,
                                          "quantities": {"requested": requested, "to_order": requested,
                                                         **{k: "0" for k in ["available", "reserved", "simulated", "ordered", "sent", "confirmed", "received", "delivered", "cancelled"]}}}]})
            group = {"supplier_id": "supplier", "name": "Fornecedor UI", "store_id": store,
                     "article_count": 1, "client_count": 2, "urgent": True, "blocked": False,
                     "reasons": [], "expected_date": "2026-11-06", "terms": terms, "totals": totals,
                     "lines": [{"article_id": "article", "description": "Café de teste", "quantity": "6",
                                "unit_cost": "10", "discount": None, "vat": "23",
                                "allocations": [{"line_id": c["line_id"], "quantity": c["needed"]} for c in customers]}]}
            data = {"store_id": store, "stock_known": True, "read_only": True, "note": "Dados fictícios de teste.",
                    "metrics": {"pending_orders": 2, "short_articles": 1, "needs_to_buy": 1,
                                "sent_orders": 0, "partial_orders": 0, "ready_orders": 0, "urgent_orders": 1},
                    "articles": [{"id": "article", "article_id": "article", "code": "CAF", "description": "Café de teste",
                                  "physical": "4", "reserved": "0", "available": "4", "incoming": "0", "needed": "6",
                                  "urgent": True, "customers": customers}], "suppliers": [group],
                    "supplier_catalog": [{"supplier_id": "supplier", "name": "Fornecedor UI"}]}

            def intercept(route):
                path = urlsplit(route.request.url).path.removeprefix("/api")
                calls.append((path, route.request.method))
                responses = {"/bootstrap": boot, "/customer-orders": {"orders": orders, "simulations": [], "can_buy": True, "can_write": True},
                             "/stock/catalog": {"articles": [{"id": "article", "code": "CAF", "name": "Café de teste", "vat_rate": "23", "last_cost": "10"}], "warehouses": []},
                             "/customer-orders/catalog": {"customers": [], "suppliers": [{"id": "supplier", "name": "Fornecedor UI", "terms": terms}]},
                             "/customer-orders/history": [], "/procurement": {"documents": []},
                             "/order-cycle/supply-center": data, "/order-cycle/recommendations": {"items": []}}
                if path == "/customer-orders/simulations/preview":
                    body = route.request.post_data_json
                    gross = sum(int(Decimal(line["quantity"]) * Decimal(line["unit_cost"]) * 100) for line in body["lines"])
                    return route.fulfill(json={**totals, "gross_cents": gross, "net_cents": gross,
                                               "eligible_cents": gross, "missing_cents": max(0, 10000 - gross),
                                               "total_cents": gross + 800 + round((gross + 800) * .23)})
                if path in responses:
                    return route.fulfill(json=responses[path])
                return ns["intercept"](route)

            context = browser.new_context(viewport={"width": width, "height": 1000}, locale="pt-PT")
            if width == 390:
                context.add_init_script("localStorage.setItem('iazela-theme','dark')")
            page = context.new_page()
            page.route("**/api/**", intercept)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto("http://127.0.0.1:5174/entrar?pagina=" + quote("Encomendas de clientes"))
            center = page.get_by_role("region", name="Centro inteligente de encomendas", exact=True)
            center.locator("details.ocb-advanced > summary").click()
            expect(center.get_by_role("heading", name="O que falta para entregar?", exact=True)).to_be_visible()
            expect(center.get_by_text("Fornecedor UI", exact=True).last).to_be_visible()
            expect(center.get_by_role("table", name="Necessidades consolidadas por artigo — dados reais da loja", exact=True)).to_contain_text("Café de teste")
            expect(center.locator(".supply-metrics .metric-card")).to_have_count(4)
            center.get_by_role("button", name="Ver clientes", exact=True).click()
            table = center.get_by_role("table", name="Clientes que solicitaram Café de teste", exact=True)
            expect(table.locator("tbody tr")).to_have_count(2)
            center.get_by_label("Cliente", exact=True).select_option("Ana")
            expect(table.locator("tbody tr")).to_have_count(1)
            center.get_by_label("Cliente", exact=True).select_option("")
            assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
            page.screenshot(path=str(ROOT / f".local/supply-center-{width}.png"), full_page=True)
            center.locator(".supply-suppliers").get_by_role("button", name="Simular encomenda", exact=True).click()
            dialog = page.get_by_role("dialog", name="Simulador de compra", exact=True)
            expect(dialog.get_by_label("Quantidade de compra", exact=True)).to_have_value("6")
            expect(dialog.locator(".co-highlight").filter(has_text="40,00")).to_be_visible()
            dialog.get_by_role("button", name="Guardar cenário para comparar", exact=True).click()
            dialog.get_by_label("Quantidade de compra", exact=True).fill("10")
            expect(dialog.get_by_text("Mínimo de encomenda atingido.", exact=True)).to_be_visible()
            expect(dialog.get_by_role("region", name="Comparação de cenários", exact=True)).to_contain_text("Cenário 1")
            assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
            dialog.screenshot(path=str(ROOT / f".local/supply-simulator-{width}.png"))
            assert not errors, errors
            assert {path for path, method in calls if method != "GET"} == {"/customer-orders/simulations/preview"}
            print(f"PASS {width}px: consolidated needs, customer filter, one-click simulation, automatic totals and scenario comparison; no real writes", flush=True)
            context.close()
        browser.close()


if __name__ == "__main__":
    run()
