"""Grelha comum num ecrã real (histórico SAF-T) com APIs fictícias intercetadas; sem escritas."""
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

boot = {"mode": "demonstracao", "company": {"id": "grid-ui", "name": "Empresa fictícia"},
        "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
        "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
        "stores": [{"id": "store-a", "name": "Loja A"}], "registers": [], "accounts": [],
        "categories": [], "users": [], "payment_methods": {}}
header = {"CompanyName": "Empresa fictícia", "StartDate": "2026-01-01", "EndDate": "2026-01-31"}
imports = [
    {"id": f"imp-{i}", "filename": f"saft-{i:02d}.xml", "user_id": "ui", "status": status,
     "created_at": f"2026-10-{i + 1:02d}T10:00:00Z", "header": header,
     "summary": {"counts": {"new": i, "existing": 0, "conflict": 0}, "totals": {}, "types": {}, "warnings": [],
                 **({"result": {"imported": i, "updated": 0, "ignored": 0, "rejected": 0}} if status == "completed" else {})}}
    for i, status in enumerate(["completed"] * 8 + ["failed"] * 3 + ["ready"] * 2)
]
writes, errors = [], []


def mock_api(route):
    path, method = urlsplit(route.request.url).path, route.request.method
    if method != "GET":
        writes.append(path)
        route.fulfill(status=405, json={"detail": "Escrita não permitida no teste."})
        return
    result = {"/api/bootstrap": boot, "/api/dashboard": {},
              "/api/saft/imports": {"versions": ["1.04_01"], "max_bytes": 100000000, "imports": imports},
              "/api/saft/payment-mapping": {"mapping": {}, "codes": [], "methods": []}}.get(path, [])
    if path.startswith("/api/saft/imports/"):
        found = next(row for row in imports if row["id"] == path.split("/")[4])
        result = {"total": 0, "page": 1, "items": []} if path.endswith("/items") else found
    route.fulfill(json=result)


with sync_playwright() as playwright:
    expect.set_options(timeout=15000)
    browser = playwright.chromium.launch()
    for width in (1440, 390):
        page = browser.new_page(viewport={"width": width, "height": 900}, locale="pt-PT")
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", mock_api)
        page.goto("http://127.0.0.1:5174/entrar?pagina=Importar+SAF-T+de+vendas")
        page.get_by_role("tab", name="Histórico", exact=True).click()
        grid = page.get_by_role("table", name="histórico de importações SAF-T", exact=True)
        expect(grid).to_be_visible()
        expect(page.get_by_text("13 registo(s) · Página 1 de 2", exact=True)).to_be_visible()
        filters = page.get_by_role("group", name="Filtros de histórico de importações SAF-T", exact=True)
        filters.locator("label", has_text="Estado").locator("select").select_option("failed")
        expect(page.get_by_text("3 registo(s) · Página 1 de 1", exact=True)).to_be_visible()
        expect(grid.locator("tbody tr").first).to_contain_text("ficheiro rejeitado")
        filters.locator("label", has_text="Estado").locator("select").select_option("")
        page.get_by_role("searchbox", name="Pesquisar histórico de importações SAF-T", exact=True).fill("saft-12")
        expect(grid.locator("tbody tr")).to_have_count(1)
        with page.expect_download() as download:
            filters.get_by_role("button", name="Exportar CSV", exact=True).click()
        saved = download.value.path().read_bytes().decode("utf-8-sig").splitlines()
        assert saved[0] == '"Ficheiro / data";"Empresa / período";"Estado";"Resultado"', saved
        assert len(saved) == 2 and "saft-12.xml" in saved[1], saved
        grid.get_by_role("button", name="Consultar importação", exact=True).click()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
        page.close()
        print(f"PASS {width}px: histórico SAF-T com pesquisa, filtro de estado, paginação e CSV", flush=True)
    browser.close()
assert not writes, writes
assert not errors, errors
