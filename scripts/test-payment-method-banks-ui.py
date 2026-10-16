"""Payment bank configuration UI with fictitious, fully mocked API data."""
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
methods = [
    {"id": key, "name": name, "active": True, "kind": "base", "required": key == "numerario"}
    for key, name in (("numerario", "Numerário"), ("cartao", "Multibanco / TPA"), ("mbway", "MB WAY"), ("transferencia", "Transferência"), ("outro", "Outros meios"))
]
boot = {
    "mode": "demonstracao", "company": {"id": "bank-default-ui", "name": "Empresa fictícia"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "payment_method_catalog": methods,
    "payment_methods": {item["id"]: item["name"] for item in methods},
    "payment_method_bank_defaults": [],
    "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
    "stores": [{"id": "a", "name": "Loja A"}, {"id": "b", "name": "Loja B"}],
    "accounts": [
        {"id": "bank-a", "store_id": "a", "name": "Conta corrente A", "bank": "Banco A", "iban_last4": "1111"},
        {"id": "bank-a2", "store_id": "a", "name": "Conta TPA", "bank": "Banco B", "iban_last4": "2222"},
        {"id": "bank-b", "store_id": "b", "name": "Conta corrente B", "bank": "Banco C", "iban_last4": "3333"},
    ],
    "registers": [], "categories": [], "users": [],
}
errors, writes = [], []
fail_next = False
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda error: errors.append(str(error)))

    def mock_api(route):
        global fail_next
        path = urlsplit(route.request.url).path
        if route.request.method == "POST":
            match = re.fullmatch(r"/api/cash/payment-methods/([^/]+)/bank-default", path)
            assert match, "Unexpected write: " + path
            if fail_next:
                fail_next = False
                route.fulfill(status=422, json={"detail": "Escolha uma conta bancária deste estabelecimento."})
                return
            data = {"method_id": match[1], **route.request.post_data_json}
            writes.append(data)
            boot["payment_method_bank_defaults"] = [row for row in boot["payment_method_bank_defaults"] if (row["method_id"], row["store_id"]) != (data["method_id"], data["store_id"])]
            if data["account_id"]:
                assert next(account for account in boot["accounts"] if account["id"] == data["account_id"])["store_id"] == data["store_id"]
                boot["payment_method_bank_defaults"].append(data)
            route.fulfill(json=data)
            return
        assert route.request.method == "GET"
        route.fulfill(json=boot if path == "/api/bootstrap" else {} if path == "/api/dashboard" else [])

    page.route("**/api/**", mock_api)

    def open_methods():
        page.goto("http://127.0.0.1:5174")
        page.get_by_role("button", name="Tabelas", exact=True).click()
        page.get_by_role("button", name="Meios de pagamento", exact=True).click()

    open_methods()
    store = page.get_by_label("Estabelecimento", exact=True)
    card = page.get_by_label("Conta bancária por defeito de Multibanco / TPA", exact=True)
    mbway = page.get_by_label("Conta bancária por defeito de MB WAY", exact=True)
    transfer = page.get_by_label("Conta bancária por defeito de Transferência", exact=True)
    expect(store).to_have_value("a")
    expect(card.locator("option")).to_have_count(3)
    expect(page.get_by_label("Conta bancária por defeito de Numerário", exact=True)).to_have_count(0)
    card.select_option("bank-a2")
    expect(page.locator(".panel").get_by_role("status")).to_contain_text("conta bancária por defeito guardada")
    expect(card).to_have_value("bank-a2")
    mbway.select_option("bank-a")
    expect(mbway).to_have_value("bank-a")
    transfer.select_option("bank-a")
    expect(transfer).to_have_value("bank-a")
    store.select_option("b")
    expect(card).to_have_value("")
    expect(card.locator("option")).to_have_count(2)
    card.select_option("bank-b")
    expect(card).to_have_value("bank-b")
    store.select_option("a")
    expect(card).to_have_value("bank-a2")
    fail_next = True
    card.select_option("bank-a")
    expect(page.get_by_role("alert")).to_contain_text("Escolha uma conta bancária")
    expect(card).to_have_value("bank-a2")
    open_methods()
    expect(card).to_have_value("bank-a2")
    expect(mbway).to_have_value("bank-a")
    transfer.select_option("")
    expect(transfer).to_have_value("")
    expect(page.locator(".panel").get_by_role("status")).to_contain_text("associação bancária removida")
    output = root / ".local/payment-bank-ui"
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(output / "mobile.png"), full_page=True)
    boot["user"]["role"] = "consulta"
    page.set_viewport_size({"width": 1440, "height": 1000})
    open_methods()
    expect(page.get_by_role("heading", name="Meios de pagamento", exact=True, level=1)).to_be_visible()
    expect(card).to_have_count(0)
    expect(page.get_by_role("cell", name="Banco B · Conta TPA · IBAN …2222", exact=True)).to_be_visible()
    assert len(writes) == 5 and not errors, (writes, errors)
    browser.close()
print("PASS: bank defaults by establishment, three payment methods, reload, removal, failure handling, read-only access and mobile; mocked API only.")
