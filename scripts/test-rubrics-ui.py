"""Rubrics UI smoke check using demonstration fixtures, with no real API writes."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0])
rubrics = []
base_intercept = intercept


def rubric_intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/categories":
        if route.request.method == "POST":
            rubrics.append({"id": "demo-rubric", **route.request.post_data_json})
        route.fulfill(content_type="application/json", body=json.dumps(rubrics))
    elif path == "/categories/erp/settings":
        route.fulfill(content_type="application/json", body=json.dumps({"name": "ERP Demo", "reference_required": True}))
    elif path == "/categories/erp/items":
        route.fulfill(content_type="application/json", body=json.dumps([
            {"id": "demo-water", "code": "AGUA", "name": "Água Demo", "can_reference": True},
            {"id": "already", "code": "EXISTE", "name": "Rubrica existente Demo"},
        ]))
    elif path == "/categories/erp/proposals":
        route.fulfill(content_type="application/json", body=json.dumps({"prefix": "RUB", "rows": [{"id": "demo-rubric", "name": "Água Demo", "exists": False, "pending": False, "code": "RUB0001"}]}))
    else:
        base_intercept(route)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", rubric_intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tabelas", exact=True).click()
    page.get_by_role("button", name="Rubricas", exact=True).click()
    page.get_by_role("button", name="Adicionar rubrica", exact=True).click()
    dialog = page.get_by_role("dialog", name="Configurar rubricas")
    dialog.get_by_label("Nome", exact=True).fill("Água Demo")
    dialog.get_by_label("Ícone", exact=True).select_option("droplet")
    dialog.get_by_label("Cor", exact=True).fill("#123456")
    dialog.get_by_role("button", name="Guardar rubrica", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.locator(".rubric-row")).to_contain_text("Água Demo")
    assert rubrics[0]["icon"] == "droplet" and rubrics[0]["color"] == "#123456"
    page.get_by_role("button", name="Adicionar rubrica", exact=True).click()
    assert dialog.get_by_label("Cor", exact=True).input_value() != rubrics[0]["color"]
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Editar", exact=True).click()
    expect(dialog.get_by_label("Cor", exact=True)).to_have_value("#123456")
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Enviar para Wintouch", exact=True)).to_have_count(0)
    rubrics[0]["integration"] = {"external_id": "already", "external_code": "EXISTE", "status": "confirmado"}
    page.get_by_role("button", name="Sincronizar ERP", exact=True).click()
    expect(dialog.get_by_role("checkbox")).to_have_count(1)
    expect(dialog.get_by_text("EXISTE · Rubrica existente Demo", exact=True)).to_have_count(0)
    expect(dialog.get_by_role("checkbox").first).to_be_enabled()
    dialog.get_by_role("button", name="Enviar para o ERP", exact=True).click()
    expect(dialog.get_by_text("Água Demo · Proposto: RUB0001", exact=True)).to_be_visible()
    expect(dialog.get_by_label("Configuração de referência no ERP", exact=True)).to_have_count(0)
    expect(dialog.get_by_role("button", name="Confirmar envio ao ERP (1)", exact=True)).to_be_enabled()
    dialog.get_by_role("checkbox").uncheck()
    expect(dialog.get_by_role("button", name="Confirmar envio ao ERP (0)", exact=True)).to_be_disabled()
    dialog.get_by_role("checkbox").check()
    expect(dialog.get_by_role("button", name="Confirmar envio ao ERP (1)", exact=True)).to_be_enabled()
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    browser.close()
print("Rubrics UI passed: visual creation, import preview, explicit send confirmation, mobile. Fixtures only.")
