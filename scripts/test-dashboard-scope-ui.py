"""Financial overview scope, failures and permissions with intercepted synthetic APIs."""
import copy
import runpy
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "scripts/test-workspace-navigation-ui.py"), run_name="fixture")
ns = fixture["ns"]
OUT = ROOT / ".local/dashboard-scope"
OUT.mkdir(parents=True, exist_ok=True)


def overview(amount, restricted=False):
    buckets = {key: {"amount_cents": 0, "count": 0} for key in ["vencido", "7_dias", "30_dias", "mais_30", "sem_data"]}
    return {
        "mode": "local", "generated_at": "2026-11-01T10:00:00Z",
        "period": {"label": "Período de teste", "in_progress": False},
        "comparison": {"method": "Período anterior", "start": "2026-09-01", "end": "2026-09-30"},
        "permissions": {"finance": not restricted, "costs": False}, "attention": [],
        "sales": {"status": "ready", "total_cents": amount, "documents": 2, "vat_basis": "com IVA", "change_percent": None, "points": []},
        "treasury": {"status": "ready", "balance_cents": 50000, "accounts": [], "received_cents": 15000, "paid_cents": 10000, "due": {"payable": buckets, "receivable": buckets}, "flows": [], "forecast": {"available": False}},
    }


def run():
    boot = copy.deepcopy(ns["boot"])
    boot["company"]["id"] = "dashboard-scope-fixture"
    boot["integration"] = {"mode": "local", "provider": "none"}
    second = {**boot["stores"][0], "id": "scope-shop-2", "name": "Segunda loja de teste"}
    boot["stores"].append(second)
    held, errors, writes, analytics_calls = [], [], [], []
    state = {"fail": False, "hold_store": False}

    def intercept(route):
        path = urlsplit(route.request.url).path.removeprefix("/api")
        query = parse_qs(urlsplit(route.request.url).query)
        if route.request.method != "GET":
            writes.append(path)
            return route.fulfill(status=500, json={"detail": "Unexpected write"})
        if path == "/bootstrap":
            return route.fulfill(json=boot)
        if path == "/order-cycle/analytics":
            analytics_calls.append(query)
            return route.fulfill(json=fixture["analytics"])
        if path == "/dashboard/overview":
            if state["fail"]:
                return route.fulfill(status=503, json={"detail": "Falha financeira de teste"})
            if state["hold_store"] or query.get("start") == ["2026-01-01"]:
                held.append(route)
                return
            amount = 77777 if query.get("start") == ["2026-02-01"] else 98765 if query.get("store_id") == [second["id"]] else 12345
            return route.fulfill(json=overview(amount, boot["user"]["role"] == "consulta"))
        return ns["intercept"](route)

    def wait_held(page):
        deadline = time.monotonic() + 10
        while not held and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert held, "Expected an actual pending request"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
        page.route("**/api/**", intercept)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://127.0.0.1:5174/entrar?pagina=Dashboard")
        financial = page.get_by_role("region", name="Gestão financeira e operação de caixa", exact=True)
        value = financial.locator(".dash-kpi").first.locator("strong")
        expect(financial.get_by_role("group", name="Filtros do painel", exact=True)).to_be_visible(timeout=30000)
        expect(value).to_contain_text("123,45")
        expect(page.locator("details.priority-financial")).to_have_count(0)
        page.screenshot(path=str(OUT / "visible-summary.png"), full_page=True)

        state["hold_store"] = True
        page.get_by_role("combobox", name="Filtrar loja", exact=True).select_option(second["id"])
        wait_held(page)
        expect(financial).not_to_contain_text("123,45")
        expect(financial.get_by_text("A calcular…", exact=True).first).to_be_visible()
        state["hold_store"] = False
        for route in held:
            route.fulfill(json=overview(98765))
        held.clear()
        expect(value).to_contain_text("987,65")

        financial.get_by_label("Desde", exact=True).fill("2026-01-01")
        wait_held(page)
        expect(financial).not_to_contain_text("987,65")
        financial.get_by_label("Desde", exact=True).fill("2026-02-01")
        expect(value).to_contain_text("777,77")
        for route in held:
            route.fulfill(json=overview(11111))
        held.clear()
        expect(value).to_contain_text("777,77")
        expect(financial).not_to_contain_text("111,11")

        state["fail"] = True
        financial.get_by_role("button", name="Atualizar", exact=True).click()
        expect(financial).to_contain_text("São apresentados dados da última consulta deste âmbito")
        expect(value).to_contain_text("777,77")
        page.reload()
        expect(financial).to_contain_text("Falha financeira de teste", timeout=30000)
        expect(financial.get_by_text("Consulta indisponível. Tente atualizar o painel.", exact=True).first).to_be_visible()
        expect(financial.locator(".dash-kpis")).not_to_contain_text("0,00")

        state["fail"] = False
        boot["user"]["role"] = "consulta"
        before = len(analytics_calls)
        page.reload()
        expect(financial).to_contain_text("Os indicadores financeiros não estão disponíveis para o seu perfil.", timeout=30000)
        expect(financial.locator(".dash-kpi")).to_have_count(0)
        assert len(analytics_calls) == before, "A restricted role must not load purchase analytics"
        assert not errors, errors
        assert not writes, writes
        boot["user"]["role"] = "administrador"
        for theme in ["current", "dark"]:
            mobile = browser.new_page(viewport={"width": 390, "height": 844}, locale="pt-PT")
            mobile.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
            mobile.route("**/api/**", intercept)
            mobile.on("pageerror", lambda error: errors.append(str(error)))
            mobile.goto("http://127.0.0.1:5174/entrar?pagina=Dashboard")
            summary = mobile.get_by_role("region", name="Gestão financeira e operação de caixa", exact=True)
            expect(summary.locator(".dash-kpi").first.locator("strong")).to_contain_text("123,45", timeout=30000)
            assert not mobile.evaluate("document.documentElement.scrollWidth>innerWidth"), theme
            mobile.screenshot(path=str(OUT / f"financial-mobile-{theme}.png"), full_page=True)
            mobile.close()
        assert not errors, errors
        assert not writes, writes
        browser.close()
    print("PASS: financial summary visible initially; store/period changes hide prior values; late replies ignored; refresh failure labelled; initial failure never becomes zero; restricted role loads no purchase analytics; no writes/runtime errors", flush=True)


if __name__ == "__main__":
    run()
