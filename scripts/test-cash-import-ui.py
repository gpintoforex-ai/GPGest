"""ERP import button verification using demonstration fixtures only."""
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0])
boot["mode"] = "demonstracao"
base_intercept = intercept
imported = False

def cash_intercept(route):
    global imported
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/sessions/demo-sheet/import-erp":
        assert route.request.post_data_json["confirm_demo"] is True
        imported = True
        route.fulfill(content_type="application/json", body=json.dumps({"closed": False, "import": {"status": "concluida", "read_count": 3, "inserted_count": 1, "updated_count": 2}}))
    elif path == "/sessions":
        amount = 1500 if imported else 1000
        row = {"key": "numerario", "name": "Numerário", "credits_cents": amount, "debits_cents": 0, "net_cents": amount, "fund_cents": 4000, "expected_cents": 4000+amount, "count": 1}
        route.fulfill(content_type="application/json", body=json.dumps([{"id": "demo-sheet", "register_id": "demo-register", "operator_id": "demo-user", "opening_cents": 4000, "closed_at": None, "opened_at": "2026-10-13T12:00:00", "count_data": {}, "balance_cents": 4000+amount, "totals": {"numerario": amount}, "sheet": {"day": "2026-10-13", "rows": [row], "scope": "store_day", "include_wintouch": True}}]))
    else:
        base_intercept(route)

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", cash_intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Folha de caixa", exact=True).click()
    page.get_by_role("button", name="Importar movimentos do ERP", exact=True).click()
    dialog = page.get_by_role("dialog", name="Importar movimentos do ERP")
    expect(dialog.get_by_role("button", name="Importar movimentos", exact=True)).to_be_disabled()
    dialog.get_by_role("checkbox").check()
    dialog.get_by_role("button", name="Importar movimentos", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_text("Movimentos importados com sucesso: 3 registos, 1 novos e 2 atualizados.", exact=True)).to_be_visible()
    expect(page.locator(".cash-reconciliation-values")).to_contain_text("55,00")
    assert imported and not errors, errors
    browser.close()
print("Cash import UI passed: button, demonstration confirmation, successful message and refreshed balance. Fixtures only.")
