"""Read-only legacy supplier split rendering with fictitious API data."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
parent = {
    "id": "legacy-parent", "session_id": "closed-sheet", "register_id": "register",
    "occurred_at": datetime.now(timezone.utc).isoformat(), "kind": "fornecedor", "payment": "numerario",
    "payment_name": "Numerário", "amount_cents": 1000, "direction": -1, "entity": "Vários fornecedores",
    "origin": "fecho_local", "document": "Fecho de caixa", "reversed_by": None, "reversal_of": None,
    "supplier_parts": [{"supplier_id": "supplier-a", "name": "Fornecedor A", "amount_cents": 400},
                       {"supplier_id": "supplier-b", "name": "Fornecedor B", "amount_cents": 600}],
}
boot = {
    "mode": "demonstracao", "company": {"id": "supplier-history", "name": "Empresa fictícia"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
    "stores": [{"id": "store", "name": "Loja fictícia"}], "registers": [{"id": "register", "name": "Caixa de testes", "store_id": "store"}],
    "accounts": [], "categories": [], "users": [],
}
errors, attachment_reads = [], []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda error: errors.append(str(error)))

    def mock(route):
        assert route.request.method == "GET", "No configuration or financial writes allowed"
        path = urlsplit(route.request.url).path
        if path.endswith("/attachments"):
            attachment_reads.append(path)
        route.fulfill(json=boot if path == "/api/bootstrap" else [parent] if path == "/api/movements" else {} if path == "/api/dashboard" else [])

    page.route("**/api/**", mock)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Movimentos reais", exact=True).click()
    table = page.locator("main table").filter(has=page.get_by_role("columnheader", name="Origem", exact=True))
    expect(table.locator("tbody tr")).to_have_count(2)
    expect(table.get_by_text("Vários fornecedores", exact=True)).to_have_count(0)
    for name, value in (("Fornecedor A", "4,00"), ("Fornecedor B", "6,00")):
        expect(table.get_by_role("row").filter(has=page.get_by_role("cell", name=name, exact=True))).to_contain_text(value)
    page.get_by_label("Entidade", exact=True).fill("Fornecedor A")
    expect(table.locator("tbody tr")).to_have_count(1)
    page.get_by_label("Entidade", exact=True).fill("")
    expect(table.locator("tbody tr")).to_have_count(2)
    output = root / ".local/supplier-history-ui"
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(output / "mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 1000})
    table.get_by_role("button", name="Anexos", exact=True).first.click()
    expect(page.get_by_role("heading", name="Anexos do movimento", exact=True)).to_be_visible()
    assert attachment_reads and set(attachment_reads) == {"/api/movements/legacy-parent/attachments"}, attachment_reads
    assert not errors, errors
    browser.close()
print("PASS: legacy grouped payment becomes supplier rows with conserved amounts, entity filter, original attachments and mobile; mocked GET only.")
