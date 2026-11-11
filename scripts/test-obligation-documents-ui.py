"""Obrigações e Prazos → Documentos e associações: comprovativos do arquivo (ver e associar) e registo real
proposto, associado e usado para concluir o prazo.

Reutiliza os dados simulados de test-obligations-ui.py; só as escritas esperadas são aceites. Requer o Vite em 127.0.0.1:5174.
"""
import base64
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-obligations-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-obligations-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
OUT = ROOT / ".local/obligation-documents-ui"
OUT.mkdir(parents=True, exist_ok=True)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
proposal = {"source_kind": "purchase", "source_label": "Compra / fatura", "source_id": "p9", "date": "2026-11-15", "entity": "Autoridade Tributária",
            "label": "Guia IUC", "amount_cents": 12000, "score": 90, "reasons": ["Mesma entidade", "Valor igual ao previsto", "5 dia(s) da data-limite"], "used_elsewhere": False}
state = {"linked": False, "documents": [{"link_id": "l1", "status": "confirmada", "document_id": "d1", "name": "comprovativo-iuc.png", "mime": "image/png",
                                         "doc_type": "comprovativo", "issue_date": "2026-11-10", "total_cents": 12000, "file_url": "/api/archive/documents/d1/file"}]}
writes = []


def matches():
    link = {"id": "k1", "source_kind": "purchase", "source_label": "Compra / fatura", "source_id": "p9", "entity": "Autoridade Tributária", "label": "Guia IUC",
            "amount_cents": 12000, "reasons": proposal["reasons"], "created_by": "Gestora", "created_at": "2026-11-11T10:00:00+00:00", "undone_at": None, "undo_reason": ""}
    return {"occurrence_id": "o2", "title": "IUC — Mercedes 180", "due_date": "2026-11-20", "expected_cents": 12000, "status": "pendente",
            "links": [link] if state["linked"] else [], "proposals": [] if state["linked"] else [proposal],
            "notes": ["Associar não conclui o prazo: conclua-o com o valor efetivo quando estiver cumprido."]}


def intercept(route):
    path, method = urlsplit(route.request.url).path.removeprefix("/api"), route.request.method
    if path == "/obligations/documents":
        return route.fulfill(json=state["documents"])
    if path == "/archive/documents" and method == "GET":
        return route.fulfill(json={"total": 1, "documents": [{"id": "d2", "original_name": "declaracao.pdf", "entity_name": "AT", "issue_date": "2026-11-09", "total_cents": None}]})
    if path == "/archive/documents/d1/file":
        return route.fulfill(body=PNG, headers={"Content-Type": "image/png"})
    if path == "/archive/links/manual" and method == "POST":
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json={"id": "l2"})
    if path == "/obligations/occurrences/o2/matches":
        return route.fulfill(json=matches())
    if path == "/obligations/occurrences/o2/links" and method == "POST":
        writes.append((path, route.request.post_data_json))
        state["linked"] = True
        return route.fulfill(json=matches())
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 390)):
        writes.clear()
        state["linked"] = False
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Obriga%C3%A7%C3%B5es%20e%20Prazos")
        page.locator(".obl-upcoming li").filter(has_text="IUC — Mercedes 180").locator("summary").click()
        page.get_by_role("button", name="Documentos e associações").first.click()
        dialog = page.get_by_role("dialog", name="Documentos e associações de IUC — Mercedes 180")
        expect(dialog.get_by_role("region", name="Comprovativos e declarações")).to_contain_text("comprovativo-iuc.png")
        # Ver o original na aplicação.
        dialog.get_by_role("button", name="Ver comprovativo-iuc.png").click()
        preview = page.get_by_role("dialog", name="Documento comprovativo-iuc.png")
        expect(preview.get_by_role("img", name="Original de comprovativo-iuc.png")).to_be_visible()
        preview.get_by_role("button", name="Fechar").last.click()
        # Associar outro documento do arquivo.
        dialog.get_by_role("button", name="Associar do arquivo").click()
        dialog.get_by_label("Procurar no arquivo").fill("declara")
        dialog.get_by_role("button", name="Procurar").click()
        dialog.get_by_role("button", name="Associar declaracao.pdf").click()
        expect(dialog.get_by_label("Procurar no arquivo")).to_have_count(0)
        assert writes[-1] == ("/archive/links/manual", {"document_id": "d2", "target_kind": "obligation_occurrence", "target_id": "o2"}), writes[-1]
        # Registo real proposto → associado → concluir com o valor do registo.
        records = dialog.get_by_role("region", name="Registos reais")
        expect(records).to_contain_text("Proposta · 90 %")
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        page.screenshot(path=str(OUT / f"documentos-{theme}-{width}.png"), full_page=True)
        records.get_by_role("button", name="Associar Guia IUC").click()
        expect(records).to_contain_text("Associado")
        assert writes[-1] == ("/obligations/occurrences/o2/links", {"source_kind": "purchase", "source_id": "p9", "reasons": proposal["reasons"]}), writes[-1]
        records.get_by_role("button", name="Concluir com 120,00 €").click()
        complete = page.get_by_role("dialog", name="Concluir prazo")
        expect(complete.get_by_label("Valor efetivo (€)")).to_have_value("120,00")
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: documentos e associações (ver original, associar do arquivo, registo real proposto e associado, concluir com o seu valor), temas e larguras.")
