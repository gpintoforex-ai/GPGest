"""Historical accumulated balance with mocked, labelled movement fixtures."""
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import expect, sync_playwright

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0])
base_intercept = intercept
source = [{"id": str(index), "store_id": "demo", "register_id": register, "kind": "real", "occurred_at": stamp+"T12:00:00", "direction": direction, "amount_cents": amount, "payment": "Numerário", "payment_code": "NU", "entity": "Entidade Demo", "document": "DEMONSTRACAO"}
          for index, (stamp, direction, amount, register) in enumerate([
              ("2026-10-12", 1, 4000, "demo-register"), ("2026-10-13", -1, 500, "demo-register"),
              ("2026-10-14", 1, 10000, "demo-register"), ("2026-10-12", 1, 9900, "other-register"),
          ])]

source.append({**source[1], "id": "card-demo", "payment_code": "CD", "payment": "Cartão", "direction": 1, "amount_cents": 2500})

source.append({**source[1], "id": "withdrawal-rubric-demo", "occurred_at": "2026-10-14T12:00:00", "category_name": "Fornecedores", "source_kind": "cash_movement", "document": "RETIRADA-DEMO"})

def movement_intercept(route):
    if "/integrations/wintouch/sales" in route.request.url:
        route.fulfill(content_type="application/json", body=json.dumps(source))
    else:
        base_intercept(route)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.clock.install(time=datetime(2026, 10, 13, 12, tzinfo=timezone.utc))
    page.route("**/api/**", movement_intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Movimentos reais", exact=True).click()
    page.get_by_label("Caixas / bancos", exact=True).select_option("demo-register")
    expect(page.get_by_label("Data do movimento desde", exact=True)).to_have_value("2026-10-01")
    expect(page.get_by_label("Até", exact=True)).to_have_value("2026-10-31")
    expect(page.locator(".record-accumulated strong")).to_contain_text("130,00")
    expect(page.locator("tbody").get_by_text("Fornecedores", exact=True).first).to_be_visible()
    page.get_by_label("Até", exact=True).fill("2026-10-13")
    expect(page.locator("tbody").get_by_text("RETIRADA-DEMO", exact=True)).to_have_count(0)
    expect(page.locator(".record-accumulated strong")).to_contain_text("35,00")
    expect(page.locator(".record-totals dl")).to_contain_text("5,00")
    page.get_by_label("Data do movimento desde", exact=True).fill("2026-10-12")
    expect(page.locator(".record-accumulated strong")).to_contain_text("35,00")
    page.get_by_label("Até", exact=True).fill("2026-10-14")
    expect(page.locator(".record-accumulated strong")).to_contain_text("130,00")
    page.get_by_label("Caixas / bancos", exact=True).select_option("bank:unassigned")
    expect(page.locator(".record-accumulated strong")).to_contain_text("25,00")
    page.get_by_label("Caixas / bancos", exact=True).select_option("demo-register")
    expect(page.locator(".record-accumulated strong")).to_contain_text("130,00")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator(".record-accumulated")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    browser.close()
print("Accumulated UI passed: earlier days, end date cutoff, register filter, period independence, mobile. Fixtures only.")
