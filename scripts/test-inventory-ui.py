"""Fictitious intercepted APIs only. No real catalogue/stock mutations."""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

boot = {
    "mode": "demonstracao",
    "company": {"id": "stock-ui", "name": "Empresa fictícia"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
    "stores": [{"id": "store-a", "name": "Loja A"}],
    "registers": [],
    "accounts": [],
    "categories": [],
    "users": [],
    "payment_methods": {},
}
article = {
    "id": "p1",
    "code": "P001",
    "name": "Suplemento de teste",
    "reference": "SUP",
    "barcodes": ["123456789"],
    "description": "Artigo fictício",
    "photo": "",
    "family": "Suplementos",
    "category": "Vitaminas",
    "brand": "Marca fictícia",
    "supplier_id": None,
    "supplier_reference": "",
    "unit": "un",
    "precision": 0,
    "vat_rate": "23",
    "sale_net": "10",
    "minimum": "2",
    "reorder": "10",
    "location": "A1",
    "lots": True,
    "expiry": True,
    "active": True,
    "revision": 1,
    "origin": "Local",
    "stock_state": "movimentos locais",
    "quantity": "5",
    "average_cost": "4",
    "last_cost": "5",
}
catalog = {
    "articles": [article],
    "warehouses": [{"id": "w1", "store_id": "store-a", "name": "Principal"}],
    "suppliers": [],
    "permissions": {
        key: True
        for key in [
            "view_cost",
            "change_price",
            "adjust_stock",
            "confirm_inventory",
            "manage_lots",
            "negative_stock",
            "override_fefo",
        ]
    },
    "settings": {
        "allow_negative": False,
        "recoverable_vat": True,
        "alerts": [90, 60, 30, 7],
        "permissions": {},
        "saft_mapping": {},
        "control_start": None,
        "revision": 0,
    },
    "policy": {"mode": "local"},
    "users": [],
}
report_row = {
    "id": "p1",
    "code": "P001",
    "name": article["name"],
    "quantity": "5",
    "minimum": "2",
    "below_minimum": False,
    "replenishment": "5",
    "risk_quantity": "5",
    "expired_quantity": "0",
    "net_sales": "30",
    "no_sales": False,
    "cost": "4",
    "gross_margin": "18",
    "actual": {"margin_pct": "60", "markup_pct": "150"},
    "projected": {"margin": "6", "margin_pct": "60"},
    "contribution": "17",
    "unknown_sales": 0,
}
lot = {
    "id": "lot-a",
    "article_id": "p1",
    "warehouse_id": "w1",
    "number": "LOTE-A",
    "quantity": "5",
    "expiry": "2026-10-25",
    "entry_date": "2026-10-01",
    "deadline": "validade",
    "state": "near",
    "alert": 30,
    "days": 8,
    "cost_value": "20",
    "supplier_id": None,
    "document": "FT FICTICIA/1",
}
errors, writes = [], []
fail_save = True


def mock(route):
    global fail_save
    path, method = urlsplit(route.request.url).path, route.request.method
    if method != "GET":
        writes.append(path)
        if path == "/api/stock/articles":
            if fail_save:
                fail_save = False
                route.fulfill(
                    status=422,
                    json={
                        "detail": [
                            {
                                "loc": ["body", "code"],
                                "msg": "Código já utilizado.",
                                "type": "value_error",
                            }
                        ]
                    },
                )
                return
            result = {"id": "new"}
        elif path == "/api/stock/movements":
            assert route.request.post_data_json["quantity"] == "1"
            result = {"ids": ["move-ui"]}
        else:
            raise AssertionError(f"Unexpected write {method} {path}")
    elif path == "/api/bootstrap":
        result = boot
    elif path == "/api/dashboard":
        result = {}
    elif path == "/api/stock/catalog":
        result = catalog
    elif path == "/api/stock/report":
        result = {
            "rows": [report_row],
            "totals": {
                "stock_value": "20",
                "risk_value": "20",
                "net_sales": "30",
                "gross_margin": "18",
                "loss_value": "0",
            },
            "coverage": {
                "sales_known": 3,
                "sales_unknown": 0,
                "stock_unknown": 0,
                "risk_unknown": 0,
                "loss_unknown": 0,
                "erp_stock_unavailable": 0,
            },
            "limitations": ["Custo médio ponderado móvel. Não representa lucro líquido."],
        }
    elif path == "/api/stock/lots":
        result = {"rows": [lot]}
    elif path == "/api/stock/movements":
        result = {"rows": [], "more": False}
    elif path == "/api/stock/inventories":
        result = {"rows": []}
    else:
        result = []
    route.fulfill(json=result)


with sync_playwright() as p:
    expect.set_options(timeout=15000)
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", mock)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tabelas", exact=True).click()
    page.get_by_role("button", name="Artigos e validades", exact=True).click()
    expect(
        page.get_by_role("heading", name="Artigos, stock e validades", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("Stock a custo conhecido", exact=True)).to_be_visible()
    page.get_by_role("button", name="Novo artigo", exact=True).click()
    dialog = page.get_by_role("dialog", name="Novo artigo", exact=True)
    expect(dialog.get_by_role("button", name="Fechar", exact=True)).to_be_visible()
    assert dialog.evaluate("node => node.contains(document.activeElement)")
    dialog.get_by_label("Código interno", exact=True).fill("NOVO")
    dialog.get_by_label("Designação", exact=True).fill("Produto fictício novo")
    page.keyboard.press("Escape")
    discard = page.get_by_role("alertdialog", name="Descartar alterações?", exact=True)
    expect(discard).to_be_visible()
    discard.get_by_role("button", name="Continuar a editar", exact=True).click()
    expect(dialog.get_by_label("Código interno", exact=True)).to_have_value("NOVO")
    dialog.get_by_role("button", name="Guardar", exact=True).click()
    expect(dialog.get_by_label("Código interno", exact=True)).to_have_attribute(
        "aria-invalid", "true"
    )
    expect(dialog.get_by_role("alert")).to_contain_text("Reveja os campos assinalados")
    expect(dialog.get_by_label("Designação", exact=True)).to_have_value("Produto fictício novo")
    dialog.get_by_role("button", name="Guardar", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.get_by_role("button", name="Registar movimento", exact=True).click()
    movement = page.get_by_role("dialog", name="Registar movimento de stock", exact=True)
    expect(movement.get_by_label("Quantidade (un)", exact=True)).to_have_value("1")
    movement.get_by_label("Movimento", exact=True).select_option("sale")
    movement.get_by_label("Motivo / observações", exact=True).fill("Venda fictícia de teste")
    movement.get_by_role("button", name="Registar movimento", exact=True).click()
    expect(movement).not_to_be_visible()
    page.get_by_role("button", name="Lotes e validades", exact=True).click()
    expect(page.get_by_text("LOTE-A", exact=True)).to_be_visible()
    page.get_by_role("button", name="Editar validade do lote LOTE-A", exact=True).click()
    expiry = page.get_by_role("dialog", name="Validade do lote", exact=True)
    expect(expiry.get_by_label("Data de validade", exact=True)).to_have_value("2026-10-25")
    page.keyboard.press("Escape")
    expect(expiry).not_to_be_visible()
    page.get_by_role("button", name="Painel", exact=True).click()
    output = Path(".local/stock-ui")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Novo artigo", exact=True).click()
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Fechar", exact=True)).to_be_visible()
    box = dialog.bounding_box()
    assert box and box["x"] >= 0 and box["width"] <= 390
    page.screenshot(path=str(output / "mobile-dialog.png"), full_page=True)
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    assert writes == ["/api/stock/articles", "/api/stock/articles", "/api/stock/movements"]
    browser.close()
print(
    "PASS: stock desktop/mobile, close/focus/dirty guards, validation retains draft, quantity=1, lot editor, no real writes."
)
