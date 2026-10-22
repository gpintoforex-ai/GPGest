"""Responsive cash conference with isolated responses; no real writes."""
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

fixture = Path(__file__).with_name("test-visual-template-ui.py")
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
rows = [
    {"key": key, "name": name, "declared_cents": 0, "registered_cents": 0,
     "credits_cents": 0, "debits_cents": 0, "expected_cents": 4000 if key == "numerario" else 0}
    for key, name in [("numerario", "Numerário"), ("cartao", "Multibanco / TPA"),
                      ("mbway", "MB WAY"), ("transferencia", "Transferência")]
]
session = {
    "id": "responsive-demo", "register_id": "demo-register", "operator_id": "demo-user",
    "opening_cents": 4000, "balance_cents": 4000, "opened_at": "2026-10-22T10:00:00Z",
    "closed_at": None, "totals": {}, "count_data": {"values": {r["key"]: 4000 if r["key"] == "numerario" else 0 for r in rows}},
    "sheet": {"day": "2026-10-22", "workflow_mode": "local", "rows": rows,
              "reconciliation": {"rows": rows, "registered_total_cents": 0, "realized": {}}},
}


def intercept(route):
    if urlsplit(route.request.url).path == "/api/sessions":
        route.fulfill(json=[session])
    else:
        ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="pt-PT")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Folha de caixa", exact=True).click()
    page.get_by_text("Conferência e fecho", exact=True).click()
    table = page.locator(".local-cash-conference")
    expect(table).to_be_visible()
    output = Path(__file__).resolve().parents[1] / ".local/cash-responsive"
    output.mkdir(exist_ok=True)
    for theme in ["light", "dark"]:
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        for width in [1600, 1024, 768, 390, 320]:
            page.set_viewport_size({"width": width, "height": 1000})
            assert table.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), (theme, width)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (theme, width)
            expect(table.locator("tbody tr")).to_have_count(4)
            for cell in table.locator("tbody td[data-label]").all():
                assert cell.evaluate("e => e.scrollWidth <= e.clientWidth + 1"), (width, cell.get_attribute("data-label"))
            table.screenshot(path=str(output / f"conference-{theme}-{width}.png"))
    page.get_by_role("button", name="Alterar valor de Numerário", exact=True).click()
    dialog = page.get_by_role("dialog", name="Registar faturado", exact=True)
    expect(dialog.get_by_label("Faturado Numerário (€)", exact=True)).to_be_editable()
    assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    dialog.get_by_role("button", name="Cancelar", exact=True).click()
    expect(page.get_by_label("Contagem Multibanco / TPA (€)", exact=True)).to_be_editable()
    assert not errors, errors
    browser.close()
print("PASS: cash conference, 1600/1024/768/390/320px, light/dark, no horizontal overflow and editing preserved.")
