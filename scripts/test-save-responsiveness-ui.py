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

        async def mock(route):
            path, method = urlsplit(route.request.url).path, route.request.method
            calls.append((method, path))
            if method == "POST":
                assert path == "/api/registers", "Unexpected write"
                post_started.set()
                await release_post.wait()
                await route.fulfill(json={"id": "new", "name": "Nova caixa UI", "store_id": "store"})
            elif path == "/api/bootstrap" and post_started.is_set():
                refresh_started.set()
                await release_refresh.wait()
                await route.fulfill(status=503, json={"detail": "Falha de leitura simulada"})
            else:
                await route.fulfill(json=boot if path == "/api/bootstrap" else {} if path == "/api/dashboard" else [])

        await page.route("**/api/**", mock)
        await page.goto("http://127.0.0.1:5174")
        await page.get_by_role("button", name="Tabelas", exact=True).click()
        await page.get_by_role("button", name="Caixas", exact=True).click()
        await page.wait_for_load_state("networkidle")
        assert ("GET", "/api/audit") not in calls
        calls.clear()
        await page.get_by_role("button", name="Nova caixa", exact=True).click()
        dialog = page.get_by_role("dialog", name="Nova caixa", exact=True)
        await dialog.get_by_label("Nome", exact=True).fill("Nova caixa UI")
        await dialog.get_by_label("Loja", exact=True).select_option("store")
        await dialog.get_by_role("button", name="Guardar", exact=True).click()
        await asyncio.wait_for(post_started.wait(), 5)
        # Do not announce success or dismiss the form before the committed response.
        await expect(dialog).to_be_visible()
        await expect(page.get_by_text("Operação concluída com sucesso.", exact=True)).to_have_count(0)
        release_post.set()
        await asyncio.wait_for(refresh_started.wait(), 5)
        # A slow refresh must not delay acknowledgement of an already saved record.
        await expect(dialog).to_have_count(0)
        await expect(page.get_by_text("Operação concluída com sucesso.", exact=True)).to_be_visible()
        assert calls == [("POST", "/api/registers"), ("GET", "/api/bootstrap")], calls
        release_refresh.set()
        await expect(page.get_by_role("alert")).to_contain_text("A operação foi guardada")
        await expect(page.get_by_text("Nova caixa UI", exact=True)).to_have_count(0)
        assert calls.count(("POST", "/api/registers")) == 1
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
