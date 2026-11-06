"""Archive date-filter regression with intercepted APIs; no real documents are changed."""
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-visual-template-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
OUT = ROOT / ".local/archive-filters"
OUT.mkdir(parents=True, exist_ok=True)
DOC = {
    "id": "previous-month", "original_name": "Fatura de outubro.pdf",
    "origin_label": "Carregamento", "received_at": "2026-10-15T10:00:00Z",
    "issue_date": "2026-10-14", "type_label": "Fatura", "entity_name": "Fornecedor de teste",
    "document_number": "FT TESTE/1", "document_code": "FT", "total_cents": 12300,
    "processing_status": "processado", "review_status": "por_rever", "relation": "sem_relacao",
}
calls = []


def intercept(route):
    url = urlsplit(route.request.url)
    if url.path == "/api/archive/overview":
        return route.fulfill(json={"can_manage": True, "can_upload": False, "types": {}, "origin_labels": {}})
    if url.path == "/api/archive/documents":
        params = parse_qs(url.query, keep_blank_values=True)
        calls.append(params)
        start, end = params.get("start", [""])[0], params.get("end", [""])[0]
        rows = [DOC] if (not start or DOC["issue_date"] >= start) and (not end or DOC["issue_date"] <= end) else []
        if params.get("q", [""])[0] and params["q"][0] not in DOC["original_name"]:
            rows = []
        return route.fulfill(json={"total": len(rows), "documents": rows})
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for width in (1440, 390):
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=" + quote("Todos os documentos"))
        expect(page.get_by_role("button", name=DOC["original_name"], exact=True)).to_be_visible()
        expect(page.get_by_label("Emitido desde", exact=True)).to_have_value("")
        expect(page.get_by_label("Até", exact=True)).to_have_value("")
        page.get_by_label("Emitido desde", exact=True).fill("2026-11-01")
        page.get_by_role("button", name="Filtrar", exact=True).click()
        expect(page.get_by_text("Nenhum documento corresponde aos filtros.", exact=True)).to_be_visible()
        page.get_by_role("button", name="Limpar filtros", exact=True).click()
        expect(page.get_by_role("button", name=DOC["original_name"], exact=True)).to_be_visible()
        assert calls[-1]["start"] == [""] and calls[-1]["end"] == [""]
        assert calls[-1]["offset"] == ["0"]
        page.screenshot(path=str(OUT / f"all-{width}.png"), full_page=True)
        page.goto("http://127.0.0.1:5174/?pagina=" + quote("Caixa de entrada"))
        expect(page.get_by_role("button", name=DOC["original_name"], exact=True)).to_be_visible()
        assert calls[-1]["inbox"] == ["true"]
        assert calls[-1]["start"] == [""] and calls[-1]["end"] == [""]
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: older documents visible by default in archive and inbox; explicit date filtering and clearing work on desktop/mobile.")
