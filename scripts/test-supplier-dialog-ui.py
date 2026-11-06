"""Ficha de fornecedor: separadores comuns e deslocação limitada ao corpo. Só fixtures."""

import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app.table_specs  # noqa: E402,F401
from app.cash_local import BASE_METHODS  # noqa: E402
from app.table_framework import TABLES  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

OUT = ROOT / ".local" / "supplier-dialog-ui"
OUT.mkdir(parents=True, exist_ok=True)
spec = TABLES["suppliers"]
fields = [f.describe() for f in spec.fields]
for f in fields:
    if f["type"] == "payment_method":
        f["options"] = [{"value": k, "label": v, "active": True} for k, v in BASE_METHODS.items()]
INFO = {
    "key": "suppliers", "title": "Fornecedores", "singular": "fornecedor", "feminine": False, "description": spec.description,
    "fields": fields, "extra_columns": [], "code_field": None, "description_field": "name", "has_active": True,
    "allow_create": True, "allow_remove": True, "remove_reason": "",
    "permissions": {k: True for k in ("view", "create", "edit", "status", "remove", "import", "export", "erp_remove")},
    "erp": {"integrated": False, "available": False, "reason": "A empresa está em modo local.", "operations": {}},
    "local_only_reason": "",
}
VALUES = {"tax_number": "506406768", "name": "CELEIRO INTEGRAL", "email": "", "phone": "249321237",
          "address": "RUA AMORIM ROSA Nº 37", "locality": "", "postal_code": "", "category_id": None,
          "payment_term_id": None, "payment_method_id": None, "is_customer": False, "is_supplier": True, "active": True,
          "order_minimum": "150.00", "shipping": "7.50", "free_shipping": "300.00", "lead_days": 5,
          "accepts_expiry_returns": True, "expiry_return_months": 3}
ROW = {"id": "local:1", "values": VALUES, "labels": {}, "code": None, "description": VALUES["name"], "active": True,
       "origin": "local", "erp_id": "", "erp_code": "", "sync_state": "apenas_local", "sync_label": "Apenas local",
       "last_sync_at": None, "last_error": ""}

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
ns = {"__file__": str(fixtures)}
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
base = ns["intercept"]


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/tables/suppliers":
        return route.fulfill(json=INFO)
    if path == "/tables/suppliers/records":
        return route.fulfill(json={"rows": [ROW], "total": 1, "total_all": 1, "page": 1, "pages": 1, "size": 25})
    if path.endswith("/details"):
        return route.fulfill(json={**ROW, "dependencies": [], "managed_fields": [], "remove_blocked": {"app": None, "both": None}})
    if path == "/payment-terms":
        return route.fulfill(json=[{"id": 7, "name": "30 dias", "active": True}])
    if path == "/entities/lookup":
        return route.fulfill(json={"is_valid": True, "name": "", "address": "", "locality": "", "postal_code": ""})
    return base(route)


FITS = "(() => { const d = document.querySelector('dialog[open]'); return [d.scrollHeight, d.clientHeight]; })()"
SIZE = "(() => { const r = document.querySelector('dialog[open]').getBoundingClientRect(); return [r.width, r.height]; })()"

with sync_playwright() as p:
    browser = p.chromium.launch()
    for width, height in [(1366, 657), (1280, 600), (1440, 900)]:
        page = browser.new_page(viewport={"width": width, "height": height})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Fornecedores")
        page.get_by_role("button", name="Editar CELEIRO INTEGRAL").click()
        dialog = page.get_by_role("dialog", name="Editar fornecedor")
        expect(dialog.get_by_label("Nome", exact=True)).to_have_value("CELEIRO INTEGRAL")
        before = page.evaluate(SIZE)
        for tab in ["Geral", "Contactos", "Condições de Compra", "Artigos", "Encomendas", "Compras", "Conta Corrente", "Histórico"]:
            dialog.get_by_role("tab", name=tab).click()
            page.wait_for_timeout(150)
            page.screenshot(path=str(OUT / f"{tab.replace(' ', '-').lower()}-{width}.png"))
            fits = page.evaluate(FITS)
            panels = page.evaluate("[...document.querySelectorAll('dialog[open] [role=tabpanel]')].map(e => Math.round(e.scrollHeight))")
            print(width, height, tab, "scroll/client", fits, "size", page.evaluate(SIZE), "panels", panels, flush=True)
            assert fits[0] <= fits[1] + 1, f"{tab} a {width}×{height} precisa de deslocação: {fits}"
            assert page.evaluate(SIZE) == before, "A janela mudou de tamanho ao trocar de separador."
        expect(dialog.get_by_label("Encomenda mínima aceite (€)", exact=True)).to_be_hidden()
        assert not errors, errors
        page.close()
    browser.close()
print("Supplier dialog UI passed: eight tabs with fixed dialog bounds at 1366×657, 1280×600 and 1440×900. Fixtures only.")
