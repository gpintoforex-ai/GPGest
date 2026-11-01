"""Assistente de configuração: um erro da validação final leva à etapa e ao campo a corrigir.

Todas as chamadas à API são intercetadas; nada é gravado. Requer o Vite em 127.0.0.1:5174.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixtures = ROOT / "scripts/test-visual-template-ui.py"
ns = {"__file__": str(fixtures)}
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
MESSAGE = "Associe cada meio ERP a um único meio de pagamento local."


def payment(key, name, erp_id):
    return {"key": key, "name": name, "selected": True, "bank_receivable": key != "numerario", "account": None,
            "cash": key == "numerario", "receipts": True, "payments": True, "erp_id": erp_id, "mbway_via": "account"}


data = {"company": {"name": "Empresa de demonstração", "store_name": "Loja de demonstração", "store_id": "demo",
                    "currency": "EUR", "timezone": "Europe/Lisbon", "nif": "500000000"},
        "mode": {"mode": "local", "resources": []}, "registers": [], "terminals": [], "skipped": [],
        "accounts": [{"key": "bank", "name": "Conta de teste", "bank": "Banco Demo", "currency": "EUR", "principal": True}],
        "payments": [payment("numerario", "Numerário", "M1"), payment("cartao", "Cartão / TPA", "M2"),
                     payment("mbway", "MB WAY", "M2")]}
state = {"step": "summary", "revision": 1, "stores": ns["boot"]["stores"], "banks": [], "iban_configured": [],
         "erp_accounts": [], "erp_methods": [{"id": "M1", "name": "Numerário ERP"}, {"id": "M2", "name": "Multibanco ERP"}],
         "catalog_labels": {}, "resource_labels": {}}


def run(width):
    writes = []

    def intercept(route):
        request = route.request
        path = urlsplit(request.url).path.removeprefix("/api")
        if request.method != "GET":
            writes.append(path)
        if path == "/setup/status":
            value = {"completed": False, "started": True, "progress": 90, "pending": [], "unclosed_count": 0}
        elif path == "/setup":
            value = {**state, "data": data}
        elif path == "/setup/draft":
            body = request.post_data_json
            value = {**state, "step": body["step"], "revision": body["revision"] + 1, "data": body["data"]}
        elif path == "/setup/preview":
            return route.fulfill(status=422, content_type="application/json", body=json.dumps({"detail": MESSAGE}))
        else:
            return ns["intercept"](route)
        route.fulfill(content_type="application/json", body=json.dumps(value))

    page = browser.new_page(viewport={"width": width, "height": 900}, locale="pt-PT")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174/")
    page.get_by_role("button", name="Continuar configuração", exact=True).click()
    expect(page.get_by_role("heading", name="Resumo", exact=True)).to_be_visible()
    page.get_by_role("button", name="Atualizar e validar resumo", exact=True).click()
    alert = page.get_by_role("alert").filter(has_text=MESSAGE)
    expect(alert).to_be_visible()
    alert.get_by_role("button", name="Corrigir em Meios de pagamento").click()
    expect(page.get_by_role("heading", name="Meios de pagamento", exact=True)).to_be_visible()
    cartao = page.get_by_label("Meio ERP para Cartão / TPA", exact=True)
    mbway = page.get_by_label("Meio ERP para MB WAY", exact=True)
    expect(cartao).to_be_focused()
    expect(cartao).to_have_attribute("aria-invalid", "true")
    expect(mbway).to_have_attribute("aria-invalid", "true")
    expect(page.get_by_label("Meio ERP para Numerário", exact=True)).not_to_have_attribute("aria-invalid", "true")
    expect(page.get_by_text("Este meio ERP também está associado a «MB WAY»")).to_be_visible()
    expect(page.get_by_text("Este meio ERP também está associado a «Cartão / TPA»")).to_be_visible()
    page.screenshot(path=str(ROOT / f".local/setup-errors-{width}.png"))
    mbway.select_option(label="Sem associação")
    expect(cartao).not_to_have_attribute("aria-invalid", "true")
    expect(page.locator(".ui-field-error")).to_have_count(0)
    assert set(writes) <= {"/setup/draft", "/setup/preview"}, writes
    assert not errors, errors
    page.close()


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    for size in (1440, 390):
        run(size)
    browser.close()
print("PASS: erro da validação final leva aos meios ERP em conflito (1440 e 390 px)")
