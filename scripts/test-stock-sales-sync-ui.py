"""Movimentos de stock: «Sincronizar vendas» com período e barra de progresso.

Só APIs intercetadas e fictícias (reaproveita as do teste de inventário); nada é gravado.
Requer o Vite em 127.0.0.1:5174.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

source = Path(__file__).with_name("test-inventory-ui.py")
ns = {"__file__": str(source)}
exec(source.read_text(encoding="utf-8").split("\nwith sync_playwright() as p:")[0], ns)
ns["catalog"]["policy"] = {"mode": "integrated"}
OUTPUT = Path(__file__).resolve().parents[1] / ".local" / "stock-ui"
OUTPUT.mkdir(parents=True, exist_ok=True)

started, polls = [], []
STAGES = [
    ("A ler a lista de documentos", 0, 0),
    ("A ler documentos de venda", 12, 40),
    ("A ler documentos de venda", 40, 40),
    ("A aplicar as vendas ao stock", 3, 6),
]
RESULT = {
    "status": "concluida", "inserted_count": 9, "updated_count": 2,
    "stock": {"status": "aplicado", "applied": 5, "returns": 1, "reversed": 0, "pending_count": 1, "conflict_count": 0,
              "pending": [{"document": "FT 2026/14", "article": "Artigo fictício", "reason": "Artigo do ERP sem ficha local associada."}],
              "conflicts": []},
}


def run(index):
    if index < len(STAGES):
        stage, done, total = STAGES[index]
        return {"id": "run-1", "status": "em_curso", "stage": stage, "done": done, "total": total, "error": "", "result": None}
    return {"id": "run-1", "status": "concluida", "stage": "", "done": 6, "total": 6, "error": "", "result": RESULT}


def intercept(route):
    path = urlsplit(route.request.url).path
    if path == "/api/integrations/wintouch/sales/running":
        return route.fulfill(content_type="application/json", body="null")
    if path == "/api/integrations/wintouch/sales/start":
        started.append(route.request.post_data_json)
        return route.fulfill(json=run(0))
    if path.startswith("/api/integrations/wintouch/sales/runs/"):
        polls.append(path)
        return route.fulfill(json=run(len(polls)))
    return ns["mock"](route)


with sync_playwright() as p:
    expect.set_options(timeout=15000)
    browser = p.chromium.launch(executable_path=os.getenv("GPGEST_TEST_CHROMIUM") or None)
    for width in (1440, 390):
        started.clear()
        polls.clear()
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", intercept)
        page.goto(os.getenv("GPGEST_UI_URL", "http://127.0.0.1:5174") + "/?pagina=Artigos%20e%20validades")
        page.get_by_role("navigation", name="Artigos e stock", exact=True).get_by_role("button", name="Movimentos", exact=True).click()
        expect(page.get_by_role("heading", name="Histórico de stock", exact=True)).to_be_visible()
        page.get_by_role("button", name="Sincronizar vendas", exact=True).click()
        dialog = page.get_by_role("dialog", name="Sincronizar vendas Wintouch", exact=True)
        dialog.get_by_label("De", exact=True).fill("2026-10-01")
        dialog.get_by_label("Até", exact=True).fill("2026-10-20")
        # Empresa de demonstração: a sincronização exige confirmação explícita.
        expect(dialog.get_by_role("button", name="Sincronizar", exact=True)).to_be_disabled()
        dialog.get_by_text("Confirmo a importação Wintouch para esta empresa de demonstração.").click()
        expect(dialog.get_by_label("Confirmo a importação Wintouch para esta empresa de demonstração.")).to_be_checked()
        dialog.get_by_role("button", name="Sincronizar", exact=True).click()
        progress = dialog.get_by_role("progressbar", name="Sincronização de vendas")
        expect(progress).to_be_visible()
        expect(dialog.get_by_text("A ler documentos de venda", exact=False)).to_be_visible()
        expect(progress).to_have_attribute("aria-valuetext", "12 de 40")
        expect(dialog.get_by_role("button", name="Fechar (continua em segundo plano)")).to_be_visible()
        page.screenshot(path=str(OUTPUT / f"sales-sync-progress-{width}.png"))
        expect(dialog.get_by_text("A aplicar as vendas ao stock", exact=False)).to_be_visible()
        expect(dialog.get_by_text("Sincronização concluída: 9 registo(s) novo(s) e 2 atualizado(s).")).to_be_visible()
        expect(dialog.get_by_text("5 saída(s) de venda, 1 devolução(ões) por nota de crédito", exact=False)).to_be_visible()
        dialog.get_by_text("1 linha(s) por aplicar", exact=False).click()
        expect(dialog.get_by_text("Artigo do ERP sem ficha local associada.", exact=False)).to_be_visible()
        page.screenshot(path=str(OUTPUT / f"sales-sync-result-{width}.png"))
        assert started == [{"store_id": "store-a", "start_date": "2026-10-01", "end_date": "2026-10-20", "warehouse_id": None, "confirm_demo": True}], started
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: Sincronizar vendas nos movimentos de stock, com período, progresso e resultado (1440 e 390 px)")
