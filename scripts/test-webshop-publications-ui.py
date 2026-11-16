"""Publicação online (Integrações): tabela compacta com o artigo, preço, margem, estado (sincronização, erro com
endereço encurtado, nova tentativa, informação alimentar recolhível) e ações numa linha. Ecrã largo e telemóvel,
temas claro e escuro, contraste. API simulada (reaproveita o teste visual); nenhum pedido sai para a loja.
Requer o Vite em 127.0.0.1:5174.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixtures = Path(__file__).with_name("test-visual-template-ui.py")
ns = {"__file__": str(fixtures)}
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
OUT = ROOT / ".local/webshop-publications"
OUT.mkdir(parents=True, exist_ok=True)

BASE = {"barcodes": [], "gtin": "", "base_price_net": None, "channel_price_net": None, "state": "published",
        "shop_state": "published", "blockers": [], "retry_at": None, "attempts": 0, "error": "", "food_missing": [],
        "preserved": [], "woo_status": "", "revision": 1, "vat_rate": "23.0000"}
ITEMS = [
    {**BASE, "id": "a", "article_id": "1", "code": "2507", "erp_code": "2507", "name": "100% CEREBRO P/ HOMEM ACTUAL 30AMP",
     "sku": "2507", "price_net": "28.46", "price_gross": "35.01", "average_cost": None, "margin_percent": None,
     "status": "error", "status_label": "Erro", "woo_product_id": None, "synced_at": None,
     "error": "Não foi possível descarregar a imagem https://natura24.pt/wp-content/uploads/2022/01/cerebrosovexamp.jpg.",
     "retry_at": "2026-11-15T19:20:00+00:00", "attempts": 1},
    {**BASE, "id": "b", "article_id": "2", "code": "2207", "erp_code": "2207", "name": "CHAMPO HIDRATANTE CORPORE SANO",
     "sku": "2207", "price_net": "10.57", "price_gross": "13.00", "average_cost": None, "margin_percent": None,
     "status": "synced", "status_label": "Sincronizado", "woo_product_id": 375, "woo_status": "draft",
     "synced_at": "2026-11-15T19:19:00+00:00", "preserved": ["description"],
     "food_missing": ["Ingredientes e alergénios destacados (use MAIÚSCULAS para alergénios)", "Quantidade líquida / unidades",
                      "Dose diária recomendada", "Substâncias e quantidades por dose diária", "%VRN quando aplicável",
                      "Condições de utilização", "Conservação e precauções específicas", "Operador alimentar responsável: nome e morada",
                      "URL da imagem legível do rótulo em português", "Revisão do rótulo, descrição e alegações",
                      "Verificação documental da notificação aplicável", "Referência documental da notificação e do fornecedor"]},
    {**BASE, "id": "c", "article_id": "3", "code": "2347", "erp_code": "2347", "name": "PROPOMAX IMUNIDADE S/ ALCOOL 30mL",
     "sku": "2347", "price_net": "16.67", "price_gross": "20.50", "average_cost": "10.42", "margin_percent": "37.5",
     "status": "pending", "status_label": "A aguardar a loja", "woo_product_id": None, "synced_at": None},
    {**BASE, "id": "d", "article_id": "4", "code": "2101", "erp_code": "2101", "name": "MAGNÉSIO TOTAL 60CAP",
     "sku": "2101", "price_net": "12.20", "price_gross": "15.01", "average_cost": None, "margin_percent": None,
     "status": "error", "status_label": "Erro", "woo_product_id": 847, "synced_at": "2026-11-15T19:00:00+00:00",
     "error": "O produto associado (#847) já não existe na loja.", "product_missing": True,
     "retry_at": "2026-11-15T22:30:00+00:00", "attempts": 2},
]
recreated = []
writes = []


def intercept(route):
    path, method = urlsplit(route.request.url).path, route.request.method
    reply = lambda value: route.fulfill(content_type="application/json", body=json.dumps(value))  # noqa: E731
    if path == "/api/webshop/connections":
        return reply([{"id": "c1", "name": "Raizpura", "active": True}])
    if path == "/api/webshop/publications" and method == "GET":
        return reply({"connection": {"id": "c1", "name": "Raizpura", "active": True}, "cost_visible": True,
                      "counts": {"error": 1, "synced": 1, "pending": 1}, "items": ITEMS})
    if path == "/api/webshop/options":
        return reply({"company": {"id": "demo-company", "name": "Empresa de demonstração"}, "stores": [], "warehouses": []})
    if path == "/api/webshop/publications-suggestions":
        return reply([])
    if path == "/api/webshop/publications/d/recreate" and method == "POST":
        recreated.append(route.request.post_data_json)
        return reply({})
    if path.startswith("/api/webshop/"):
        writes.append(f"{method} {path}")
        return reply([] if method == "GET" else {})
    return ns["intercept"](route)


def audit(page, scope):
    page.mouse.move(1, 1)
    page.wait_for_timeout(300)
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    text = scope.inner_text()
    failures = [f for f in result.get("failures") or [] if f.get("text") and f["text"] in text]
    assert not failures, failures


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, viewport in (("light", {"width": 1440, "height": 1000}), ("dark", {"width": 390, "height": 844})):
        page = browser.new_page(viewport=viewport, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/")
        page.wait_for_load_state("networkidle")
        if viewport["width"] < 800:
            page.get_by_role("button", name="Abrir menu", exact=True).click()
        page.get_by_role("button", name="Configurações").first.click()
        page.get_by_role("button", name="Integrações").first.click()
        page.get_by_role("tab", name="Publicação online").click()
        table = page.get_by_role("table", name="publicações na loja online")
        rows = table.locator("tbody tr")
        expect(rows).to_have_count(4)
        headers = [h.split("\n")[0].strip().casefold() for h in table.locator("thead th").all_inner_texts()]
        assert headers == ["artigo", "preço", "margem", "estado", "ações"], headers
        first, second, third = rows.nth(0), rows.nth(1), rows.nth(2)
        expect(first).to_contain_text("natura24.pt/…/cerebrosovexamp.jpg")
        expect(first).not_to_contain_text("wp-content/uploads")
        expect(first).to_contain_text("1.ª falha")
        expect(second).to_contain_text("Informação alimentar em falta (12)")
        expect(second.get_by_text("Dose diária recomendada")).to_be_hidden()  # recolhido por omissão
        expect(second).to_contain_text("Editado na loja e preservado: descrição")
        expect(third).to_contain_text("37,5 %")
        assert not re.search(r"IVA 23\.0+%", table.inner_text())
        expect(table).to_contain_text("IVA 23 %")
        for name in ("Atualizar", "SKU e preço", "Histórico", "Retirar da loja"):
            expect(first.get_by_role("button", name=name, exact=True)).to_be_visible()
        if theme == "light":
            heights = [rows.nth(i).bounding_box()["height"] for i in range(4)]
            assert max(heights) < 200, heights  # uma linha por artigo, sem botões empilhados
        audit(page, table)
        table.screenshot(path=str(OUT / f"tabela-{theme}.png"))
        second.get_by_text("Informação alimentar em falta (12)").click()
        expect(second.get_by_text("Dose diária recomendada")).to_be_visible()
        # Produto apagado na loja: só o operador pede um produto novo (com confirmação).
        missing = table.locator("tr", has_text="MAGNÉSIO TOTAL 60CAP")
        expect(rows.nth(0).get_by_role("button", name="Criar produto novo na loja")).to_have_count(0)
        missing.get_by_role("button", name="Criar produto novo na loja").click()
        page.get_by_role("alertdialog").get_by_role("button", name="Criar produto novo").click()
        expect(page.get_by_text("Produto novo pedido", exact=False).first).to_be_visible()
        assert recreated[-1] == {"revision": 1}, recreated
        assert not errors, errors
        page.close()
    browser.close()
assert not writes, writes
print("PASS: publicação online compacta (colunas, erro encurtado, informação alimentar recolhível, ações numa linha), temas, telemóvel e contraste.")
