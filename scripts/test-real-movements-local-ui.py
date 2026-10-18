"""Real movements respect the operating mode; mocked API only, no ERP access."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

stamp = datetime.now(timezone.utc).isoformat()
local_row = {
    "id": "local-a", "register_id": "register-a", "session_id": "session-a",
    "kind": "venda", "payment": "numerario", "amount_cents": 1000, "direction": 1,
    "occurred_at": stamp, "document": "LOCAL-UI-001", "origin": "manual", "state": "confirmado",
}
boot = {
    "mode": "demonstracao", "company": {"id": "mode-ui", "name": "Empresa fictícia"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "user": {"id": "ui", "name": "Utilizador fictício", "role": "consulta"},
    "stores": [{"id": "store-a", "name": "Loja fictícia"}],
    "registers": [{"id": "register-a", "store_id": "store-a", "name": "Caixa fictícia"}],
    "accounts": [], "categories": [], "users": [],
    "payment_methods": {"numerario": "Numerário"},
}
erp_rows = [
    {**local_row, "id": "erp-a", "store_id": "store-a", "document": "ERP-UI-001", "kind": "real"},
    {**local_row, "id": "erp-doc", "store_id": "store-a", "document": "DOC-ERP-UI-001", "kind": "documento"},
]
calls, errors = [], []


def mock_api(route):
    assert route.request.method == "GET", route.request.url
    parsed = urlsplit(route.request.url)
    calls.append(parsed.path + ("?" + parsed.query if parsed.query else ""))
    result = (
        boot if parsed.path == "/api/bootstrap"
        else [local_row] if parsed.path == "/api/movements"
        else erp_rows if parsed.path == "/api/integrations/wintouch/sales"
        else {} if parsed.path == "/api/dashboard"
        else []
    )
    route.fulfill(json=result)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", mock_api)

    def open_movements():
        page.goto("http://127.0.0.1:5174")
        page.get_by_role("button", name="Tesouraria", exact=True).click()
        page.get_by_role("button", name="Movimentos reais", exact=True).click()
        expect(page.get_by_role("heading", name="Movimentos reais", exact=True, level=1)).to_be_visible()
        expect(page.get_by_role("cell", name="LOCAL-UI-001", exact=True)).to_be_visible()
        expect(page.get_by_text("A carregar movimentos…", exact=True)).to_have_count(0)

    open_movements()
    expect(page.get_by_role("button", name="Ver documentos de venda Wintouch", exact=True)).to_have_count(0)
    assert "/api/movements?all_records=true" in calls
    assert not any("/wintouch/" in path for path in calls), calls

    boot["integration"].update({"mode": "integrated", "provider": "wintouch", "active": True})
    open_movements()
    expect(page.get_by_role("cell", name="ERP-UI-001", exact=True)).to_be_visible()
    page.get_by_role("button", name="Ver documentos de venda Wintouch", exact=True).click()
    expect(page.get_by_role("cell", name="DOC-ERP-UI-001", exact=True)).to_be_visible()
    expect(page.get_by_role("cell", name="LOCAL-UI-001", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Voltar aos movimentos reais", exact=True).click()
    expect(page.get_by_role("cell", name="LOCAL-UI-001", exact=True)).to_be_visible()
    assert "/api/integrations/wintouch/sales" in calls

    boot["integration"].update({"mode": "local", "provider": "none", "active": False})
    calls.clear()
    open_movements()
    expect(page.get_by_role("cell", name="ERP-UI-001", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Ver documentos de venda Wintouch", exact=True)).to_have_count(0)
    assert not any("/wintouch/" in path for path in calls), calls
    assert not errors, errors
    page.unroute_all(behavior="ignoreErrors")
    browser.close()
print("PASS: local movements make no Wintouch requests or links; integrated movements and document toggle preserved; mocked API only.")
