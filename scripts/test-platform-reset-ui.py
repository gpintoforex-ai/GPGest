"""Reset controls in Chromium; mocked API, no real reset or database writes."""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

company = {
    "id": "reset-ui",
    "name": "Empresa fictícia de teste",
    "slug": "reset-ui",
    "nif": "500000001",
    "active": True,
    "mode": "demonstracao",
    "api_active": False,
    "api_url": "https://api.wintouchcloud.com/api/v1",
    "api_enterprise": "",
    "has_api_key": False,
    "erp_provider": "none",
    "operating_mode": "local",
}
calls = []
catalog_calls = []


def api(route):
    request = route.request
    path = urlsplit(request.url).path
    if path == "/api/platform/me":
        result = {"email": "platform@ui.test", "mode": "demonstracao"}
    elif path == "/api/platform/companies":
        result = [company]
    elif path.endswith("/users") or path == "/api/platform/audit":
        result = []
    elif "/reset-catalog/" in path:
        scope = path.rsplit("/", 1)[1]
        labels = {"articles": "Artigos", "customers": "Clientes", "suppliers": "Fornecedores"}
        if request.method == "GET":
            result = {"scope": scope, "label": labels[scope], "total": 3, "removable": 2, "protected": 1,
                      "shared": 0 if scope == "articles" else 1, "token": "a" * 64,
                      "protected_examples": [{"id": "protected", "name": "Ficha com histórico"}]}
        else:
            body = request.post_data_json
            catalog_calls.append((scope, body))
            if body["confirmation"] != company["nif"]:
                route.fulfill(status=422, json={"detail": "A confirmação não corresponde à empresa."})
                return
            assert body["acknowledged"] is True and body["preview_token"] == "a" * 64
            result = {"ok": True, "removed": 2, "protected": 1, "shared": 0 if scope == "articles" else 1}
    elif path.endswith("/reset-movements") and request.method == "POST":
        body = request.post_data_json
        calls.append((path, body))
        if body["confirmation"] != company["nif"]:
            route.fulfill(status=422, json={"detail": "A confirmação não corresponde à empresa."})
            return
        assert body["acknowledged"] is True
        result = {"ok": True, "scope": "movements"}
    else:
        raise AssertionError(f"Pedido inesperado: {request.method} {path}")
    route.fulfill(json=result)


with sync_playwright() as playwright:
    expect.set_options(timeout=30000)
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 1000}, locale="pt-PT")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", api)
    page.goto("http://127.0.0.1:5174/plataforma")
    page.get_by_role("button", name="Configurar empresa", exact=True).click()
    dialog = page.get_by_role("dialog", name="Empresa fictícia de teste · Configuração", exact=True)
    catalog = dialog.locator(".company-removal").filter(has=page.get_by_role("heading", name="Reinicializar uma tabela", exact=True))
    for label, scope in [("artigos", "articles"), ("clientes", "customers"), ("fornecedores", "suppliers")]:
        catalog.get_by_role("button", name="Reinicializar " + label, exact=True).click()
        expect(catalog.get_by_text("Ficha com histórico", exact=False)).to_be_visible()
        before = len(catalog_calls)
        confirm_catalog = catalog.get_by_role("button", name="Confirmar reinicialização de " + label, exact=True)
        confirm_catalog.click()
        assert len(catalog_calls) == before
        catalog.locator('input[name="confirmation"]').fill("errado")
        catalog.locator('input[name="acknowledged"]').check()
        confirm_catalog.click()
        expect(catalog.get_by_role("alert")).to_contain_text("A confirmação não corresponde à empresa.")
        expect(catalog.locator('input[name="confirmation"]')).to_have_value("errado")
        catalog.locator('input[name="confirmation"]').fill(company["nif"])
        confirm_catalog.click()
        expect(confirm_catalog).to_have_count(0)
        assert catalog_calls[-1][0] == scope
    assert [scope for scope, _ in catalog_calls] == ["articles", "articles", "customers", "customers", "suppliers", "suppliers"]
    dialog.get_by_role("button", name="Reinicializar movimentos e documentos", exact=True).click()
    expect(dialog.get_by_text("Preserva tabelas e bancos:", exact=True)).to_be_visible()
    confirm = dialog.get_by_role(
        "button", name="Confirmar reinicialização dos movimentos e documentos", exact=True
    )
    confirm.click()
    assert not calls, calls
    field = dialog.locator('input[name="confirmation"]')
    field.fill("wrong")
    dialog.locator('input[name="acknowledged"]').check()
    confirm.click()
    expect(
        dialog.get_by_role("alert").filter(has_text="A confirmação não corresponde à empresa.")
    ).to_be_visible()
    expect(field).to_have_value("wrong")
    expect(dialog.locator('input[name="acknowledged"]')).to_be_checked()
    field.fill(company["nif"])
    confirm.click()
    expect(confirm).to_have_count(0)
    expect(
        dialog.get_by_role("status").filter(has_text="Tabelas, bancos e configurações preservados")
    ).to_be_visible()
    assert len(calls) == 2 and all(path.endswith("/reset-movements") for path, _ in calls)
    page.set_viewport_size({"width": 390, "height": 844})
    dialog.get_by_role("button", name="Reinicializar movimentos e documentos", exact=True).click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    heading = dialog.locator("#company-config-title").bounding_box()
    close = dialog.locator(".ui-dialog-dismissbar > button").bounding_box()
    assert heading["x"] + heading["width"] <= close["x"] - 8, (heading, close)
    output = Path(__file__).resolve().parents[1] / ".local/interactions-ui"
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / "platform-reset-mobile.png"))
    assert not errors, errors
    page.unroute_all(behavior="ignoreErrors")
    browser.close()
print(
    "PASS: platform operational reset, explicit confirmation, preserved values on error, correct endpoint, desktop/mobile; mocked API, no real writes."
)
