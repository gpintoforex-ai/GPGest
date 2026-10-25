"""Deterministic browser timing checks with a fully mocked API; no real writes."""
import asyncio
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

boot = {
    "mode": "demonstracao", "company": {"id": "performance-ui", "name": "Empresa fictícia"},
    "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
    "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
    "stores": [{"id": "store", "name": "Loja fictícia"}],
    "registers": [{"id": "register", "name": "Caixa de testes", "store_id": "store"}],
    "accounts": [], "categories": [], "users": [],
}


async def main():
    calls, errors = [], []
    post_started, release_post, refresh_started, release_refresh = [asyncio.Event() for _ in range(4)]
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
        page.on("pageerror", lambda error: errors.append(str(error)))

        registers = {
            "key": "registers", "title": "Caixas", "singular": "caixa", "feminine": True, "description": "Caixas por estabelecimento.",
            "fields": [
                {"name": "name", "label": "Descrição", "type": "text", "required": True, "max_length": 100, "min_length": 2, "minimum": None, "maximum": None, "options": [], "column": True, "create_only": False, "help": ""},
                {"name": "store_id", "label": "Estabelecimento", "type": "store", "required": True, "max_length": None, "min_length": None, "minimum": None, "maximum": None, "options": [], "column": True, "create_only": False, "help": ""},
            ],
            "extra_columns": [], "code_field": None, "description_field": "name", "has_active": True, "allow_create": True, "allow_remove": True, "remove_reason": "",
            "permissions": {k: True for k in ("view", "create", "edit", "status", "remove", "import", "export", "erp_remove")},
            "erp": {"integrated": False, "available": False, "reason": "A empresa está em modo local.", "operations": {}}, "local_only_reason": "",
        }
        listing = {"rows": [{"id": "register", "values": {"name": "Caixa de testes", "store_id": "store", "active": True}, "labels": {"store_id": "Loja fictícia"}, "code": None,
                             "description": "Caixa de testes", "active": True, "origin": "local", "erp_id": "", "erp_code": "", "sync_state": "apenas_local",
                             "sync_label": "Apenas local", "last_sync_at": None, "last_error": ""}], "total": 1, "total_all": 1, "page": 1, "pages": 1, "size": 25}

        async def mock(route):
            path, method = urlsplit(route.request.url).path, route.request.method
            calls.append((method, path))
            if method == "POST":
                assert path == "/api/tables/registers/records", "Unexpected write"
                post_started.set()
                await release_post.wait()
                await route.fulfill(json={"id": "new"})
            elif path == "/api/tables/registers/records" and post_started.is_set():
                refresh_started.set()
                await release_refresh.wait()
                await route.fulfill(status=503, json={"detail": "Falha de leitura simulada"})
            elif path == "/api/tables/registers":
                await route.fulfill(json=registers)
            elif path == "/api/tables/registers/records":
                await route.fulfill(json=listing)
            else:
                await route.fulfill(json=boot if path == "/api/bootstrap" else {} if path == "/api/dashboard" else [])

        await page.route("**/api/**", mock)
        # Abre diretamente a tabela: o painel inicial não faz parte desta verificação.
        await page.goto("http://127.0.0.1:5174/?pagina=Caixas")
        await expect(page.get_by_role("table", name="Caixas")).to_be_visible()
        await page.wait_for_load_state("networkidle")
        assert ("GET", "/api/audit") not in calls
        calls.clear()
        await page.get_by_role("button", name="Nova caixa", exact=True).click()
        dialog = page.get_by_role("dialog", name="Nova caixa", exact=True)
        await dialog.get_by_label("Descrição", exact=True).fill("Nova caixa UI")
        await dialog.get_by_label("Estabelecimento", exact=True).select_option("store")
        await dialog.get_by_role("button", name="Criar caixa", exact=True).click()
        await asyncio.wait_for(post_started.wait(), 5)
        # Do not announce success or dismiss the form before the committed response.
        await expect(dialog).to_be_visible()
        await expect(page.get_by_text("Caixa criada.", exact=True)).to_have_count(0)
        release_post.set()
        await asyncio.wait_for(refresh_started.wait(), 5)
        # A slow refresh must not delay acknowledgement of an already saved record.
        await expect(dialog).to_have_count(0)
        await expect(page.get_by_text("Caixa criada.", exact=True)).to_be_visible()
        assert calls == [("POST", "/api/tables/registers/records"), ("GET", "/api/tables/registers"), ("GET", "/api/tables/registers/records")], calls
        release_refresh.set()
        await expect(page.get_by_role("alert")).to_contain_text("A operação foi guardada")
        await expect(page.get_by_text("Nova caixa UI", exact=True)).to_have_count(0)
        assert calls.count(("POST", "/api/tables/registers/records")) == 1
        # Audit is requested only when its report is opened.
        await page.get_by_role("button", name="Relatórios", exact=True).click()
        await page.wait_for_load_state("networkidle")
        assert calls.count(("GET", "/api/audit")) == 1, calls
        assert not errors, errors
        output = Path(__file__).resolve().parents[1] / ".local/save-responsiveness-ui"
        output.mkdir(exist_ok=True)
        await page.screenshot(path=str(output / "refresh-error.png"), full_page=True)
        await browser.close()
    print("PASS: confirmation follows the saved response, slow refresh does not hold the form, catalogue writes refresh only catalogue, read failure never repeats the write, audit loads on demand.")


asyncio.run(main())
