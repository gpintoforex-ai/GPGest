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
    "supplier_reference": "FORN-REF-123",
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
    "reserved_quantity": "1",
    "available_quantity": "4",
    "average_cost": "4",
    "last_cost": "5",
}
# Artigo da Wintouch: catálogo do ERP, stock registado localmente.
erp_article = {
    **article,
    "id": "erp1",
    "code": "W100",
    "name": "Chá da Wintouch",
    "barcodes": [],
    "lots": False,
    "expiry": False,
    "origin": "ERP",
    "sale_net": None,
    "erp_price": "4.5",
    "erp_price_basis": "IVA não identificado pelo conector",
    "synced_at": "2026-10-25T10:00:00Z",
    "quantity": "3",
}
catalog = {
    "articles": [article, erp_article],
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
        elif path == "/api/stock/articles/p1" and method == "PUT":
            saved = route.request.post_data_json
            assert saved["name"] == "Edição fictícia"
            assert saved["code"] == "P001" and saved["revision"] == 1
            assert float(saved["sale_net"]) == 10
            assert "available_quantity" not in saved
            assert saved["internal_notes"] == "Nota interna fictícia"
            assert saved["attachments"][0]["url"] == "https://example.com/ficha.pdf"
            result = {"id": "p1"}
        elif path == "/api/stock/movements":
            assert route.request.post_data_json["quantity"] == "1"
            result = {"ids": ["move-ui"]}
        elif path == "/api/integrations/wintouch/sync":
            assert route.request.post_data_json["resource"] == "products"
            result = {"status": "concluida", "read_count": 3813, "inserted_count": 3813, "updated_count": 0,
                      "error_message": ""}
        elif path == "/api/stock/erp-products/link-by-code":
            body = route.request.post_data_json
            plan = {"erp_products": 3813, "already_linked": 0, "link": 975, "create": 2838,
                    "ambiguous": 1, "ambiguous_codes": ["X1"], "local_only": 0}
            result = {**plan, "linked": 975, "created": 2838 if body.get("create_missing") else 0} if body.get("confirmed") else plan
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
    elif path.endswith("/details"):
        result = {
            "company_name": "Empresa fictícia",
            "warehouses": [{"id": "w1", "name": "Principal", "quantity": "5"}],
            "last_purchase": {"date": "2026-10-01", "document": "FT TESTE/1", "unit_cost": "5"},
            "history": [{"id": "audit-1", "label": "Artigo criado", "created_at": "2026-10-01", "user_name": "Utilizador fictício"}],
            "erp": {"external_id": "100", "enterprise_id": "TESTE"},
        }
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
    page.goto("http://127.0.0.1:5174/?pagina=Artigos%20e%20validades")
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
    # Artigo do ERP: aviso claro; código e nome só de leitura; dados locais editáveis.
    expect(page.get_by_text("o stock é registado aqui", exact=False).first).to_be_visible()
    page.get_by_role("button", name="Artigos", exact=True).click()
    # Reference search and filters use actual catalogue fields.
    search = page.get_by_label("Pesquisar código, designação, barras ou referência do fornecedor")
    search.fill("FORN-REF-123")
    expect(page.get_by_role("row", name="P001", exact=False)).to_be_visible()
    search.fill("inexistente")
    expect(page.get_by_text("Nenhum artigo corresponde à pesquisa e aos filtros.")).to_be_visible()
    search.fill("")
    page.get_by_label("Estado", exact=True).select_option("false")
    expect(page.get_by_text("Nenhum artigo corresponde à pesquisa e aos filtros.")).to_be_visible()
    page.get_by_label("Estado", exact=True).select_option("")
    page.get_by_label("Família", exact=True).select_option("Suplementos")
    page.get_by_label("Situação de stock", exact=True).select_option("positive")
    expect(page.get_by_role("row", name="P001", exact=False)).to_be_visible()
    page.get_by_label("Família", exact=True).select_option("")
    page.get_by_label("Situação de stock", exact=True).select_option("")
    page.get_by_label("Mostrar custos na tabela", exact=True).check()
    expect(page.get_by_role("columnheader", name="Custo médio / última compra", exact=True)).to_be_visible()
    page.get_by_label("Mostrar custos na tabela", exact=True).uncheck()
    output = Path(".local/stock-ui")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "articles-table.png"), animations="disabled")
    page.get_by_role("button", name="Editar Suplemento de teste", exact=True).click()
    local_dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
    local_dialog.get_by_role("tab", name="Preços e margens", exact=True).click()
    page.keyboard.press("ArrowRight")
    expect(local_dialog.get_by_role("tab", name="Stock e validades", exact=True)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("ArrowLeft")
    expect(local_dialog.get_by_role("tab", name="Preços e margens", exact=True)).to_have_attribute("aria-selected", "true")
    expect(local_dialog.get_by_text("6,00", exact=False).first).to_be_visible()
    local_dialog.get_by_label("Venda com IVA (€)", exact=True).fill("24.6")
    expect(local_dialog.get_by_label("Venda sem IVA (€)", exact=True)).to_have_value("20")
    local_dialog.get_by_label("Venda sem IVA (€)", exact=True).fill("0")
    expect(local_dialog.get_by_text("O preço sem IVA está abaixo do custo médio.")).to_be_visible()
    expect(local_dialog.get_by_text("Não calculável", exact=True).first).to_be_visible()
    local_dialog.get_by_label("Venda sem IVA (€)", exact=True).fill("10")
    output = Path(".local/stock-ui")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "article-prices.png"), animations="disabled")
    local_dialog.get_by_role("tab", name="Stock e validades", exact=True).click()
    expect(local_dialog.get_by_text("Stock por armazém autorizado")).to_be_visible()
    local_dialog.get_by_label("Nível de reposição", exact=True).fill("1")
    local_dialog.get_by_role("button", name="Guardar", exact=True).click()
    expect(local_dialog.get_by_label("Nível de reposição", exact=True)).to_have_attribute("aria-invalid", "true")
    expect(local_dialog.get_by_role("tab", name="Stock e validades", exact=False)).to_have_attribute("aria-selected", "true")
    local_dialog.get_by_label("Nível de reposição", exact=True).fill("10")
    local_dialog.get_by_role("tab", name="Descrição e anexos", exact=True).click()
    local_dialog.get_by_label("Observações internas", exact=True).fill("Nota interna fictícia")
    local_dialog.get_by_label("Anexos — nome | endereço HTTPS, um por linha", exact=True).fill("Ficha | https://example.com/ficha.pdf")
    local_dialog.get_by_role("tab", name="ERP e histórico", exact=True).click()
    expect(local_dialog.get_by_text("Artigo criado", exact=True)).to_be_visible()
    local_dialog.get_by_role("tab", name="Geral", exact=True).click()
    local_dialog.get_by_label("Código interno", exact=True).fill("W100")
    local_dialog.get_by_role("button", name="Guardar", exact=True).click()
    expect(local_dialog.get_by_label("Código interno", exact=True)).to_have_attribute("aria-invalid", "true")
    local_dialog.get_by_label("Código interno", exact=True).fill("P001")
    local_dialog.get_by_label("Designação", exact=True).fill("Edição fictícia")
    # Keyboard save persists the same canonical article fields.
    page.keyboard.press("Control+s")
    expect(local_dialog).not_to_be_visible()
    expect(page.get_by_role("row", name="W100", exact=False)).to_contain_text("movimentos locais")
    page.get_by_role("button", name="Editar Chá da Wintouch", exact=True).click()
    erp_dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
    expect(erp_dialog.get_by_text("Artigo da Wintouch", exact=False)).to_be_visible()
    expect(erp_dialog.get_by_label("Código interno", exact=True)).to_have_attribute("readonly", "")
    erp_dialog.get_by_role("tab", name="Stock e validades", exact=True).click()
    expect(erp_dialog.get_by_label("Localização", exact=True)).not_to_have_attribute("readonly", "")
    expect(erp_dialog.get_by_label("Preço sem IVA (€)", exact=True)).to_have_count(0)
    page.keyboard.press("Escape")
    expect(erp_dialog).not_to_be_visible()
    page.get_by_role("button", name="Registar movimento", exact=True).click()
    movement = page.get_by_role("dialog", name="Registar movimento de stock", exact=True)
    # Os artigos do ERP aceitam movimentos de stock locais.
    expect(movement.get_by_label("Artigo", exact=True).locator("option", has_text="Chá da Wintouch")).to_have_count(1)
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
    # Associação por código: pré-visualização e confirmação explícita.
    page.get_by_role("button", name="Importações", exact=True).click()
    page.get_by_role("button", name="Associar por código", exact=True).click()
    plan = page.get_by_role("region", name="Associação por código")
    expect(plan).to_contain_text("975 fichas locais com o mesmo código")
    expect(plan.get_by_label("Criar fichas para os 2838 artigos do ERP sem ficha local")).to_be_checked()
    plan.get_by_role("button", name="Confirmar associação", exact=True).click()
    expect(page.get_by_text("975 fichas associadas ao ERP; 2838 fichas criadas.")).to_be_visible()
    # Botão «Sincronizar» (modo integrado), como nas tabelas: catálogo, pré-visualização e confirmação.
    catalog["policy"] = {"mode": "integrated"}
    page.reload()
    page.get_by_role("button", name="Sincronizar", exact=True).click()
    sync = page.get_by_role("dialog", name="Sincronizar — Artigos", exact=True)
    expect(sync.get_by_text("Catálogo recebido: 3813 artigos", exact=False)).to_be_visible()
    expect(sync.get_by_role("list", name="Resumo")).to_contain_text("975 A associar")
    expect(sync.get_by_label("Criar fichas em falta", exact=True)).to_be_checked()
    output = Path(".local/stock-ui")
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "sync.png"))
    sync.get_by_label("Criar fichas em falta", exact=True).uncheck()
    sync.get_by_role("button", name="Confirmar sincronização (975)", exact=True).click()
    expect(sync).not_to_be_visible()
    expect(page.get_by_text("Artigos sincronizados: 975 fichas associadas ao ERP; 0 fichas criadas.")).to_be_visible()
    catalog["policy"] = {"mode": "local"}
    page.reload()
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
    assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
    assert dialog.get_by_role("button", name="Guardar", exact=True).is_visible()
    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    page.screenshot(path=str(output / "mobile-dialog-dark.png"), full_page=True, animations="disabled")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.screenshot(path=str(output / "article-desktop-dark.png"), full_page=True, animations="disabled")
    page.evaluate("document.documentElement.dataset.theme = 'current'")
    page.screenshot(path=str(output / "article-desktop.png"), full_page=True, animations="disabled")
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    assert writes == [
        "/api/stock/articles",
        "/api/stock/articles",
        "/api/stock/articles/p1",
        "/api/stock/movements",
        "/api/stock/erp-products/link-by-code",
        "/api/stock/erp-products/link-by-code",
        "/api/integrations/wintouch/sync",
        "/api/stock/erp-products/link-by-code",
        "/api/stock/erp-products/link-by-code",
    ], writes
    browser.close()
print(
    "PASS: stock desktop/mobile, close/focus/dirty guards, validation retains draft, quantity=1, lot editor, no real writes."
)
