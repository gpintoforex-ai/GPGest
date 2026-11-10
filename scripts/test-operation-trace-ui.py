"""Rastreabilidade das operações: resumo por ligação, lista do que precisa de intervenção e ficha lado a lado.

Respostas simuladas; a página é só de leitura e não pode fazer pedidos de escrita. Requer o Vite em 127.0.0.1:5174.
"""
import base64
import copy
import runpy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "scripts/test-dashboard-scope-ui.py"), run_name="fixture")
ns = fixture["ns"]
OUT = ROOT / ".local/operation-trace-ui"
OUT.mkdir(parents=True, exist_ok=True)
boot = copy.deepcopy(ns["boot"])
boot["user"]["role"] = "gestor"
boot["company"]["id"] = "trace-fixture"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
STATES = {"confirmada": "Confirmada", "provavel": "Provável", "varias": "Várias possibilidades", "em_falta": "Em falta", "divergencia": "Divergência", "nao_aplicavel": "Não aplicável"}


def d(state, label, **extra):
    return {"state": state, "state_label": STATES[state], "label": label, **extra}


diverging = {"kind": "purchase", "id": "p2", "store_id": "s1", "store": "Loja", "supplier_nif": "503504564", "supplier_name": "Energia, SA", "document_type": "FT",
             "document": "FT B/7", "document_date": "2026-10-05", "amount_cents": 5000, "base_cents": 5000, "tax_cents": 0, "origin": "manual", "origin_label": "Registo manual",
             "dimensions": {"efatura": d("em_falta", "Ainda não visto no e-Fatura"), "pdf": d("confirmada", "1 ficheiro(s) associado(s)"),
                            "erp": d("divergencia", "Diferenças: Total"), "rubrica": d("em_falta", "Por classificar"),
                            "pagamento": d("em_falta", "Por pagar", overdue=False), "banco": d("nao_aplicavel", "Sem pagamento a reconciliar")},
             "attention": ["PDF: …", "ERP: Diferenças: Total", "Rubrica: Por classificar"], "paid_cents": 0, "remaining_cents": 5000}
ok = {**diverging, "id": "p1", "supplier_name": "Senhorio, Lda", "document": "FT A/1", "origin_label": "e-Fatura (AT)", "attention": [],
      "dimensions": {k: d("confirmada", "OK") for k in ("efatura", "pdf", "erp", "rubrica", "pagamento", "banco")}}
summary = {k: {s: 0 for s in STATES} for k in ("efatura", "pdf", "erp", "rubrica", "pagamento", "banco")}
for row in (diverging, ok):
    for k, v in row["dimensions"].items():
        summary[k][v["state"]] += 1
listing = {"start": "2026-08-01", "end": "2026-11-10", "mode": "integrated", "total": 2, "attention": 1, "summary": summary, "rows": [diverging, ok], "states": STATES,
           "dimensions": {}, "notes": ["Só leitura: as ligações confirmam-se nos módulos de origem."]}
detail = {**diverging, "comparison": [
    {"key": "supplier_nif", "label": "NIF do fornecedor", "values": {"documento": "503504564", "erp": "503504564", "pdf": None}, "different": False},
    {"key": "document", "label": "Número", "values": {"documento": "FT B/7", "erp": "FT B/7", "pdf": None}, "different": False},
    {"key": "amount_cents", "label": "Total", "values": {"documento": 5000, "erp": 5100, "pdf": None}, "different": True}],
    "erp_candidates": [{"id": "e2"}], "files": [{"id": "a1", "name": "fatura.png", "mime": "image/png", "source": "anexo", "view_url": "/api/documents/attachments/a1/view"}],
    "rubrics": [{"category_id": None, "category_name": "Por classificar", "base_cents": 5000, "tax_cents": 0}], "rubric_source": None,
    "planned": [{"id": "pl", "due_date": "2026-11-04", "amount_cents": -5000, "settled": False}], "payments": [], "procurement_documents": [],
    "history": [{"id": "h1", "created_at": "2026-10-06T10:00:00+00:00", "actor": "Gestora", "action": "documento_ligado_erp"}], "links": {}}
writes = []


def intercept(route):
    path, method = urlsplit(route.request.url).path.removeprefix("/api"), route.request.method
    if path == "/bootstrap":
        return route.fulfill(json=boot)
    if path == "/operations":
        return route.fulfill(json=listing)
    if path == "/operations/purchase/p2":
        return route.fulfill(json=detail)
    if path == "/documents/attachments/a1/view":
        return route.fulfill(body=PNG, headers={"Content-Type": "image/png"})
    if method != "GET":
        writes.append((method, path))
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
    return ns["intercept"](route)


def audit(page):
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 1440), ("dark", 390)):
        writes.clear()
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Rastreabilidade")
        expect(page.get_by_role("heading", name="Rastreabilidade das operações")).to_be_visible()
        grid = page.get_by_role("table", name="Operações de compra")
        expect(grid.locator("tbody tr")).to_have_count(1)  # Só o que precisa de intervenção.
        expect(grid).to_contain_text("Energia, SA")
        expect(grid).to_contain_text("Divergência")
        expect(page.get_by_text("1 de 2 operação(ões) precisam de intervenção")).to_be_visible()
        page.get_by_label("Só o que precisa de intervenção").uncheck()
        expect(grid.locator("tbody tr")).to_have_count(2)
        # Resumo por ligação → filtro.
        page.get_by_role("group", name="Resumo por ligação").get_by_role("button", name="ERP: 1 confirmada(s), 1 por resolver. Filtrar").click()
        expect(grid.locator("tbody tr")).to_have_count(1)
        audit(page)
        page.screenshot(path=str(OUT / f"lista-{theme}-{width}.png"), full_page=True)
        grid.get_by_role("button", name="Abrir a ficha de FT B/7").click()
        drawer = page.get_by_role("dialog", name="Ficha da operação FT B/7")
        chain = drawer.get_by_role("list", name="Percurso da operação")
        for text in ("e-Fatura", "Em falta", "ERP", "Divergência", "Diferenças: Total"):
            expect(chain).to_contain_text(text)
        compare = drawer.get_by_role("table", name="Comparação dos dados")
        expect(compare.locator("tr.is-different")).to_have_count(1)
        expect(compare.locator("tr.is-different")).to_contain_text("51,00 €")
        expect(drawer.get_by_role("img", name="Original de fatura.png")).to_be_visible()
        expect(drawer).to_contain_text("Vencimento 04/11/2026")
        audit(page)
        page.screenshot(path=str(OUT / f"ficha-{theme}-{width}.png"), full_page=True)
        drawer.get_by_role("button", name="Fechar").last.click()
        assert not writes, writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: rastreabilidade (resumo por ligação, só o que precisa de intervenção, ficha com percurso, PDF e dados lado a lado), só leitura, temas e larguras.")
