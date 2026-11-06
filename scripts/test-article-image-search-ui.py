"""Ficha do artigo: pesquisa de imagens no Google (separador novo) junto à fotografia e às imagens adicionais.

Só APIs intercetadas (reaproveita o teste de artigos); nada é gravado nem se abre o Google.
Requer o Vite em 127.0.0.1:5174.
"""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-inventory-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], ns)
OUT = ROOT / ".local/article-image-search"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    for width in (1440, 390):
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", ns["mock"])
        page.goto("http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos")
        page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
        page.get_by_role("menuitem", name="Editar Suplemento de teste", exact=True).click()
        dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
        dialog.get_by_label("Definir fotografia", exact=True).click()
        link = dialog.locator(".article-photo-popover").get_by_role("link", name="Pesquisar imagens no Google", exact=True)
        expect(link).to_be_visible()
        expect(link).to_have_attribute("target", "_blank")
        expect(link).to_have_attribute("rel", "noopener noreferrer")
        url = urlsplit(link.get_attribute("href"))
        query = parse_qs(url.query)
        assert (url.netloc, url.path, query["tbm"]) == ("www.google.com", "/search", ["isch"]), url
        assert query["q"][0].startswith("Suplemento de teste"), query
        expect(dialog.get_by_text("Copiar endereço da imagem", exact=False).first).to_be_visible()
        dialog.screenshot(path=str(OUT / f"photo-{width}.png"))
        # Sem designação nem código de barras: a ligação fica inativa e explica o que falta.
        dialog.get_by_label("Designação", exact=True).fill("")
        dialog.get_by_label("Códigos de barras (separados por |)", exact=True).fill("")
        expect(link).to_have_attribute("aria-disabled", "true")
        expect(dialog.get_by_text("Indique a designação ou o código de barras para pesquisar.").first).to_be_visible()
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: pesquisa de imagens do artigo no Google (ligação, termos e estado sem dados)")
