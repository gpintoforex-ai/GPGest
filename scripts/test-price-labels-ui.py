"""Etiquetas de preço da receção: quantidades, pré-visualização, definições (logótipo) e documento de impressão.

Respostas simuladas (fixture das compras); só a gravação das definições é aceite, nada mais é escrito.
Requer o Vite em 127.0.0.1:5174.
"""
import base64
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
source = (root / "scripts/test-procurement-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(root / "scripts/test-procurement-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
catalog, supplier = ns["catalog"], ns["supplier"]
catalog["articles"].append({**catalog["articles"][0], "id": "p2", "code": "4747", "name": "Ergybase", "sale_net": "16.585", "vat_rate": "23"})
LOGO = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
receipt = {"id": "receipt-1", "kind": "receipt", "document": "FT FAD.2026D/6120", "document_date": "2026-10-27", "supplier_name": supplier["name"],
           "supplier_nif": supplier["tax_number"], "status": "confirmed", "confirmed_at": "2026-11-09T02:32:00Z", "created_at": "2026-11-09T02:32:00Z",
           "revision": 1, "total_cents": 12801, "email_history": [], "check_status": "checked", "erp_status": "not_linked", "payment_status": "unpaid",
           "attachments": [], "lines": [
               {"article_id": "p1", "description": "Suplemento", "nature": "goods", "quantity": "3", "invoiced": "0", "returned": "0", "received": "3", "credited": "0", "pending": "0"},
               {"article_id": "p1", "description": "Suplemento (bónus)", "nature": "goods", "quantity": "1", "invoiced": "0", "returned": "0", "received": "1", "credited": "0", "pending": "0"},
               {"article_id": "p2", "description": "Ergybase", "nature": "goods", "quantity": "2", "invoiced": "0", "returned": "0", "received": "2", "credited": "0", "pending": "0"},
               {"article_id": None, "description": "Portes", "nature": "service", "quantity": "1", "invoiced": "0", "returned": "0", "received": "0", "credited": "0", "pending": "0"}]}
settings = {"revision": 0, "page_width": "21.00", "page_height": "29.70", "margin_top": "1.00", "margin_left": "0.20", "columns": 5, "rows": 13,
            "label_width": "3.81", "label_height": "2.20", "gap_horizontal": "0.40", "gap_vertical": "0.00", "show_logo": True, "show_name": True,
            "uppercase_name": True, "show_price": True, "show_code": True, "price_note": "P.V.P C/ IVA à taxa em vigor", "footer": "", "logo": ""}
saved = []


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/stock/label-settings":
        if route.request.method == "POST":
            saved.append(route.request.post_data_json)
            settings.update({**route.request.post_data_json, "revision": settings["revision"] + 1})
        return route.fulfill(json=settings)
    if path == "/procurement" and route.request.method == "GET":
        return route.fulfill(json={"documents": [receipt], "can_write": True, "mode": {"mode": "local"}, "integration_note": "Circuito local."})
    if route.request.method != "GET":
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1366), ("dark", 1366), ("dark", 390)):
        settings.update(revision=0, footer="", logo="")
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=" + quote("Receções de mercadoria"))
        page.get_by_role("row").filter(has_text="FT FAD.2026D/6120").get_by_role("button", name="Etiquetas").click()
        dialog = page.get_by_role("dialog", name="Imprimir etiquetas")
        items = dialog.get_by_role("table")
        # Linha paga (3) e linha de bónus (1) do mesmo artigo: 4 etiquetas; serviços (portes) ficam de fora.
        expect(dialog.get_by_label("Etiquetas de Suplemento de teste")).to_have_value("4")
        expect(dialog.get_by_label("Etiquetas de Ergybase")).to_have_value("2")
        expect(items).not_to_contain_text("Portes")
        expect(items).to_contain_text("12,30 €")
        expect(items).to_contain_text("20,40 €")
        expect(dialog).to_contain_text("6 etiqueta(s) · 1 folha(s) de 65")
        preview = page.frame_locator("iframe[title='Pré-visualização da etiqueta']")
        expect(preview.locator(".name")).to_have_text("SUPLEMENTO DE TESTE")
        expect(preview.locator(".price")).to_have_text("12,30 €")
        expect(preview.locator(".code")).to_have_text("P001")
        # Definições: rodapé e logótipo (pré-visualização imediata; gravadas com revisão).
        dialog.get_by_role("button", name="Definições da folha e do conteúdo").click()
        dialog.get_by_label("Rodapé (por exemplo, telefone)").fill("Tel: 236 023 218")
        dialog.locator("input[type=file]").set_input_files(files=[{"name": "logo.png", "mimeType": "image/png", "buffer": LOGO}])
        expect(dialog.get_by_role("img", name="Logótipo das etiquetas")).to_be_visible()
        expect(preview.locator(".footer")).to_have_text("Tel: 236 023 218")
        expect(preview.locator(".logo img")).to_have_count(1)
        expect(dialog.get_by_role("button", name=re.compile("Imprimir"))).to_be_disabled()
        out = root / ".local/price-labels-ui"
        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out / f"definicoes-{theme}-{width}.png"), full_page=True)
        audit = page.evaluate((root / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        dialog.get_by_role("button", name="Guardar definições").click()
        expect(dialog).to_contain_text("Definições das etiquetas guardadas.")
        assert saved[-1]["footer"] == "Tel: 236 023 218" and saved[-1]["logo"].startswith("data:image/png;base64,")
        # Quantidades e posição inicial (folha já usada): começa na 4.ª etiqueta.
        dialog.get_by_label("Etiquetas de Ergybase").fill("3")
        dialog.get_by_label("Começar na etiqueta").fill("4")
        expect(dialog).to_contain_text("7 etiqueta(s) · 1 folha(s) de 65")
        page.evaluate("() => { window.__printed = 0; }")
        dialog.get_by_role("button", name="Imprimir 7 etiqueta(s)").click()
        printed = None
        for _ in range(50):
            frames = [f for f in page.frames if f != page.main_frame and "<section class=\"page\">" in (f.content() or "")]
            if frames:
                printed = frames[-1]
                break
            page.wait_for_timeout(100)
        assert printed, "O documento de impressão não foi criado."
        slots = printed.locator(".slot")
        expect(slots).to_have_count(7)
        # 4.ª posição da 1.ª fila: esquerda = 0,20 + 3 × (3,81 + 0,40) = 12,83 cm; topo = 1,00 cm.
        style = slots.first.get_attribute("style")
        assert "left:12.83" in style.replace(" ", "") or "left:12.829" in style.replace(" ", ""), style
        assert "top:1cm" in style.replace(" ", ""), style
        html = printed.content()
        assert "@page" in html and "21cm 29.7cm" in html, "Folha A4 em centímetros"
        expect(printed.locator(".price").filter(has_text="20,40 €")).to_have_count(3)
        expect(printed.locator(".footer").first).to_have_text("Tel: 236 023 218")
        page.screenshot(path=str(out / f"etiquetas-{theme}-{width}.png"), full_page=True)
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: etiquetas da receção (quantidades com bónus, serviços excluídos, pré-visualização, logótipo e rodapé, posição inicial e medidas da folha).")
