"""Real components, synthetic data and intercepted APIs: no production operations."""
import copy
import json
import os
import re
import runpy
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
base = runpy.run_path(str(ROOT / "scripts/test-workspace-navigation-ui.py"), run_name="fixture")
ns = base["ns"]
OUT = ROOT / ".local/order-center-reference"
OUT.mkdir(parents=True, exist_ok=True)


def calculate(body):
    terms = body["terms"]
    net = sum(int((D(line["quantity"]) * D(line["unit_cost"]) * 100).quantize(D(1), rounding=ROUND_HALF_UP)) for line in body["lines"])
    free = terms.get("free_shipping_cents")
    shipping = 0 if free is not None and net >= free else terms["shipping_cents"]
    vat = round(net * .23)
    return {"currency": terms["currency"], "gross_cents": net, "discount_cents": 0, "net_cents": net, "eligible_cents": net, "missing_cents": max(0, terms["minimum_cents"] - net), "shipping_cents": shipping, "shipping_vat_cents": round(shipping * .23), "vat_cents": vat, "total_cents": net + vat + shipping + round(shipping * .23), "shipping_missing_cents": None if free is None else max(0, free - net), "lines": []}


def scenario():
    boot = copy.deepcopy(ns["boot"])
    boot["company"]["id"] = "center-reference-fixture"
    boot["mode"] = "empresa"
    boot["stores"].append({"id":"other-store","name":"Outra loja de teste"})
    boot["company"]["name"] = "Empresa de teste visual — dados fictícios"
    boot["integration"] = {"mode": "local", "provider": "none"}
    store = boot["stores"][0]["id"]
    names = ["Azeite 1L", "Massa Espiral", "Tomate Pelado", "Molho Bolonhesa", "Queijo Ralado", "Arroz Agulha", "Farinha Tipo 55", "Atum Posta", "Orégãos", "Feijão", "Grão", "Café"]
    suppliers, articles, orders = [], [], []
    full_terms = {"currency": "EUR", "minimum_cents": 25000, "minimum_basis": "net", "discount": "0", "tiers": [], "shipping_cents": 1200, "free_shipping_cents": 30000, "shipping_vat": "23", "vat": "23", "lead_days": 7, "pending_days": 7, "confirmation_days": 3, "pickup_days": 7, "sales_period_days": 60, "email": "", "notes": "", "prices": {}}
    for index in range(18):
        available = "2" if index < 6 else "1" if index < 8 else "0"
        ordered = "2" if index >= 12 else "0"
        orders.append({"id": f"order-{index}", "number": f"EC2026/{321-index}", "store_id": store, "customer_name": ["Sol Nascente", "Farmácia Central", "Cliente Final", "Saúde e Vida", "NutriBem"][index % 5], "customer_id": f"customer-{index%5}", "requested_at": date.today().isoformat(), "created_at": date.today().isoformat()+"T09:00:00Z", "due_date": date.today().isoformat(), "priority": "urgent" if index in [8,9,10,12,13] else "normal", "closed": False, "ready": index < 8, "fully_ready": index < 6, "late": index in [12,13], "notified": index < 3, "revision": 0, "phone": "", "email": "", "preferred_channel": "none", "contact_allowed": False, "notes": "", "lines": [{"id": f"line-{index}", "article_id": f"article-{index%12}", "article_code": f"REF{index%12:02}", "description": names[index%12], "supplier_id": f"supplier-{index%3}", "provisional": False, "state": "ready" if index < 8 else "waiting", "state_label": "Disponível" if index < 8 else "A aguardar", "reservations": [], "quantities": {"requested": "2", "delivered": "0", "cancelled": "0", "reserved": available, "available": available, "ordered": ordered, "simulated": "0", "sent": ordered, "confirmed": "0", "received": "0", "to_order": str(max(0, 2-int(available)-int(ordered)))}}]})
    quantities = [5, 3, 2, 5]
    prices = ["10", "20", "15", "9.1"]
    for index, name in enumerate(names):
        supplier_index = index // 4
        customers = [{"line_id": f"line-{8+index%10}", "order_id": f"order-{8+index%10}", "number": f"EC2026/{313-index%10}", "customer": orders[8+index%10]["customer_name"], "requested_at": date.today().isoformat(), "due_date": date.today().isoformat(), "requested": "10", "delivered": "0", "reserved": "0", "ready_quantity": "0", "stock_cover": "0", "incoming_cover": "0", "ordered_pending": "0", "simulated": "0", "needed": str(quantities[index%4]), "supplier_id": f"supplier-{supplier_index}", "state": "A aguardar encomenda", "urgent": index == 0}]
        articles.append({"id": f"article-{index}", "article_id": f"article-{index}", "code": f"REF{index:02}", "description": name, "physical": "6", "reserved": "0", "available": "6", "incoming": "0", "needed": str(quantities[index%4]), "urgent": index == 0, "customers": customers})
    for index, name in enumerate(["Distribuidora Alimentar A", "Suplementos Norte", "BioHealth"]):
        terms = {**full_terms, "minimum_cents": [25000, 30000, 20000][index]}
        lines = [{"article_id": f"article-{index*4+i}", "description": names[index*4+i], "quantity": str(quantities[i]), "unit_cost": prices[i], "discount": None, "vat": "23", "allocations": [{"line_id": f"line-{8+(index*4+i)%10}", "quantity": str(quantities[i])}]} for i in range(4)]
        suppliers.append({"supplier_id": f"supplier-{index}", "store_id": store, "name": name, "terms": terms, "lines": lines, "totals": calculate({"terms": terms, "lines": lines}), "article_count": 4, "client_count": 4, "expected_date": date.today().isoformat(), "blocked": False, "urgent": index == 0, "reasons": [], "unpriced": 0})
    recommendations = [{"article_id": "article-0", "description": "Azeite 1L", "code": "REF00", "supplier_id": "supplier-0", "quantity": "8", "unit_cost": "10", "vat": "23", "free": "6", "period_sales": "48", "daily_sales": "0.8", "below_minimum": True, "reasons": ["Vendas físicas de teste"], "warnings": [], "policy": {"seasonal_factor": "1"}}]
    for index, name in enumerate(["Arroz Agulha", "Farinha Tipo 55", "Atum Posta", "Orégãos"]):
        recommendations.append({**recommendations[0], "article_id": f"extra-{index}", "description": name, "quantity": "4", "unit_cost": "2.5", "period_sales": str(32-index*3), "daily_sales": str(.6-index*.1), "policy": {"seasonal_factor": "1.2" if index == 3 else "1"}})
    receipt = {"id": "receipt", "store_id": store, "kind": "receipt", "status": "confirmed", "confirmed_at": date.today().isoformat(), "document": "RC2026/145", "document_date": date.today().isoformat(), "supplier_name": suppliers[0]["name"], "supplier_id": "supplier-0", "lines": suppliers[0]["lines"], "revision": 0}
    simulation = {"id": "prepared-simulation", "store_id": store, "supplier_id": "supplier-1", "supplier_name": suppliers[1]["name"], "status": "prepared", "created_at": date.today().isoformat()+"T08:00:00Z", "terms": suppliers[1]["terms"], "lines": suppliers[1]["lines"], "totals": suppliers[1]["totals"], "procurement_id": "purchase-order", "procurement": {"id": "purchase-order", "status": "confirmed", "lines": [{"pending": "2"}], "confirmed_at": date.today().isoformat()}}
    supply = {"store_id": store, "stock_known": True, "metrics": {"pending_orders": 18, "short_articles": 12, "needs_to_buy": 12, "sent_orders": 1, "partial_orders": 1, "ready_orders": 8, "urgent_orders": 5}, "articles": articles, "suppliers": suppliers, "supplier_catalog": [{"supplier_id": supplier["supplier_id"], "name": supplier["name"]} for supplier in suppliers], "note": "Dados fictícios apenas para o teste visual."}
    return boot, orders, supply, recommendations, receipt, simulation


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in [int(value) for value in os.getenv("GPGEST_TEST_WIDTHS", "1536,1011,768,390").split(",")]:
            for theme in ["light", "dark"]:
                boot, orders, supply, recommendations, receipt, simulation = scenario()
                errors, writes, previews = [], [], []
                preview_failure = [False]
                can_buy = [True]
                read_requests = []
                other_order = copy.deepcopy(orders[0])
                other_order.update(id="other-order",store_id="other-store",customer_name="Cliente de outra loja")
                def intercept(route):
                    path = urlsplit(route.request.url).path.removeprefix("/api")
                    query = parse_qs(urlsplit(route.request.url).query)
                    if route.request.method == "GET": read_requests.append(path)
                    if route.request.method != "GET": writes.append(path)
                    responses = {"/bootstrap": boot, "/customer-orders": {"orders": orders, "simulations": [simulation], "can_buy": True, "can_write": True}, "/stock/catalog": {"articles": [{"id": article["id"], "code": article["code"], "name": article["description"], "vat_rate": "23", "last_cost": "10"} for article in supply["articles"]], "warehouses": []}, "/customer-orders/catalog": {"customers": [], "suppliers": [{"id": supplier["supplier_id"], "name": supplier["name"], "terms": supplier["terms"]} for supplier in supply["suppliers"]]}, "/customer-orders/history": [], "/procurement": {"documents": [receipt]}, "/order-cycle/supply-center": supply, "/order-cycle/recommendations": {"items": recommendations if query.get("supplier_id") == ["supplier-0"] else []}, "/order-cycle/analytics": base["analytics"]}
                    if path == "/customer-orders":
                        scoped = orders+[other_order]
                        if query.get("store_id"): scoped=[order for order in scoped if order["store_id"]==query["store_id"][0]]
                        return route.fulfill(json={"orders":scoped,"simulations":[simulation] if can_buy[0] else [],"can_buy":can_buy[0],"can_write":True})
                    if path == "/order-cycle/supply-center" and query.get("store_id")==["other-store"]:
                        return route.fulfill(json={**supply,"store_id":"other-store","articles":[],"suppliers":[],"supplier_catalog":[],"metrics":{"pending_orders":1,"needs_to_buy":0}})
                    if path == "/customer-orders/simulations/preview":
                        body = route.request.post_data_json
                        previews.append(body)
                        if preview_failure[0]: return route.fulfill(status=503,json={"detail":"Pré-visualização indisponível no teste"})
                        return route.fulfill(json=calculate(body))
                    if path in responses: return route.fulfill(json=responses[path])
                    return ns["intercept"](route)
                context = browser.new_context(viewport={"width": width, "height": 1024}, locale="pt-PT")
                context.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
                page = context.new_page()
                page.route("**/api/**", intercept)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto("http://127.0.0.1:5174/entrar?pagina="+quote("Encomendas de clientes"))
                board = page.get_by_role("region", name="Painel de gestão de encomendas", exact=True)
                expect(board.get_by_role("heading", name="Centro de Encomendas", exact=True)).to_be_visible(timeout=20000)
                expect(board.locator(".ocb-metric")).to_have_count(6)
                expect(board.locator(".ocb-panel")).to_have_count(8)
                expect(board.get_by_role("table", name="Encomendas pendentes do painel", exact=True).locator("tbody tr")).to_have_count(5)
                expect(board.locator(".ocb-supplier")).to_have_count(3)
                expect(board.get_by_role("checkbox", name="Selecionar Azeite 1L", exact=True)).to_be_visible()
                board.get_by_role("checkbox", name="Selecionar Azeite 1L", exact=True).check()
                board.get_by_role("checkbox", name="Selecionar Arroz Agulha", exact=True).check()
                board.get_by_role("button", name="Adicionar à simulação", exact=True).click()
                expect(board.locator(".ocb-mini-totals")).to_contain_text("225,50")
                expect(board.locator(".ocb-mini-totals")).to_contain_text("24,50")
                assert previews[-1]["lines"][0]["quantity"] == "8"
                assert previews[-1]["lines"][0]["allocations"] == supply["suppliers"][0]["lines"][0]["allocations"]
                assert len(previews[-1]["lines"]) == 5
                board.get_by_role("button", name="Simular encomenda", exact=True).click()
                dialog = page.get_by_role("dialog", name="Simulador de compra", exact=True)
                expect(dialog.get_by_label("Quantidade de compra", exact=True).first).to_have_value("8")
                dialog.get_by_role("button", name="Fechar", exact=True).click()
                page.get_by_role("searchbox", name="Pesquisar no centro de encomendas", exact=True).fill("Sol Nascente")
                expect(page).to_have_url(re.compile("pesquisa.encomendas="))
                expect(board.get_by_role("table", name="Encomendas pendentes do painel", exact=True).locator("tbody tr")).to_have_count(4)
                page.get_by_role("searchbox", name="Pesquisar no centro de encomendas", exact=True).fill("")
                board.get_by_role("group", name="Filtrar encomendas do painel", exact=True).get_by_role("button", name="Parcial", exact=False).click()
                expect(board.get_by_role("table", name="Encomendas pendentes do painel", exact=True).locator("tbody tr")).to_have_count(2)
                board.get_by_role("group", name="Filtrar encomendas do painel", exact=True).get_by_role("button", name=re.compile("^Todas")).click()
                board.get_by_role("combobox", name="Fornecedor do simulador do painel", exact=True).select_option("supplier-1")
                expect(board.locator(".ocb-mini-totals")).to_contain_text("185,50")
                expect(board.get_by_role("checkbox", name="Selecionar Azeite 1L", exact=True)).to_have_count(0)
                board.get_by_role("combobox", name="Fornecedor do simulador do painel", exact=True).select_option("supplier-0")
                expect(board.get_by_role("checkbox", name="Selecionar Azeite 1L", exact=True)).not_to_be_checked()
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth"), (width, theme)
                assert not errors, errors
                assert set(writes) == {"/customer-orders/simulations/preview"}, writes
                for selector in [".ocb-supplier-value > small", ".ocb-mini-totals .is-short dt", ".ocb-mini-totals .is-short dd"]:
                    colors = board.locator(selector).first.evaluate("""node => {
                        const color=getComputedStyle(node).color;
                        while (getComputedStyle(node).backgroundColor === 'rgba(0, 0, 0, 0)') node=node.parentElement;
                        return {color, background:getComputedStyle(node).backgroundColor};
                    }""")
                    assert base["contrast"](colors["color"], colors["background"]) >= 4.5, (theme, selector, colors)
                page.evaluate("document.activeElement?.blur();window.scrollTo(0,0)")
                page.screenshot(path=str(OUT/f"center-{width}-{theme}.png"), full_page=True)
                if width==1536 and theme=="light":
                    preview_failure[0]=True
                    board.get_by_role("checkbox", name="Selecionar Azeite 1L", exact=True).check()
                    board.get_by_role("button", name="Adicionar à simulação", exact=True).click()
                    expect(board).to_contain_text("Pré-visualização indisponível no teste")
                    expect(board.get_by_role("button",name="Simular encomenda",exact=True)).to_be_disabled()
                    expect(board.locator(".ocb-mini-shipping")).to_contain_text("a calcular")
                    expect(board.locator(".ocb-mini-totals")).not_to_contain_text("Mínimo atingido")
                    preview_failure[0]=False
                    board.get_by_role("combobox", name="Fornecedor do simulador do painel", exact=True).select_option("supplier-1")
                    expect(board.get_by_role("button",name="Simular encomenda",exact=True)).to_be_enabled()
                    board.get_by_role("combobox",name="Loja do centro de encomendas",exact=True).select_option("other-store")
                    expect(board.get_by_role("combobox",name="Loja do centro de encomendas",exact=True)).to_have_value("other-store")
                    expect(board.locator(".ocb-metric").first.locator("strong")).to_have_text("1")
                    expect(board.get_by_role("table",name="Encomendas pendentes do painel",exact=True)).to_contain_text("Cliente de outra loja")
                    expect(board.locator(".ocb-supplier")).to_have_count(0)
                    print("PASS failures and store scope: failed previews cannot claim thresholds or prepare orders; changing store reloads orders and proposals consistently",flush=True)
                    boot["user"]["role"]="operador"
                    can_buy[0]=False
                    read_requests.clear()
                    page.reload()
                    expect(page.get_by_role("navigation",name="Áreas das encomendas de clientes",exact=True)).to_be_visible()
                    expect(board).to_have_count(0)
                    assert not set(read_requests).intersection({"/order-cycle/analytics","/order-cycle/supply-center","/order-cycle/recommendations"}),read_requests
                    print("PASS permissions: an operator retains order navigation without loading cost indicators or recommendations",flush=True)
                assert not errors, errors
                print(f"PASS {width}px {theme}: reference panels, real-source counts, filters/search, supplier isolation, nonduplicating proposals, allocations preserved, simulator dialog; no production writes/errors/overflow", flush=True)
                context.close()
        browser.close()


if __name__ == "__main__": run()
