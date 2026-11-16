"""Classificação da ficha em desktop e telemóvel; só APIs fictícias intercetadas."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

source = Path(__file__).with_name("test-inventory-ui.py")
ns = {"__file__": str(source)}
exec(source.read_text(encoding="utf-8").split("\nwith sync_playwright() as p:")[0], ns)
ns["catalog"]["classifications"] = {
    "family": [{"id": "f1", "name": "Suplementos", "active": True},
               {"id": "f2", "name": "Alimentação", "active": True}],
    "category": [{"id": "s1", "family_id": "f1", "name": "Vitaminas", "active": True},
                 {"id": "s2", "family_id": "f2", "name": "Chás", "active": True},
                 {"id": "s3", "family_id": "f1", "name": "Antiga", "active": False}],
    "brand": [{"id": "b1", "name": "Marca de teste", "active": True}],
}
ns["catalog"]["can_manage_classifications"] = True
output = Path(__file__).resolve().parents[1] / ".local" / "article-taxonomy-ui"
output.mkdir(parents=True, exist_ok=True)
saved = []
sys.path.insert(0, str(source.resolve().parents[1] / "backend"))
from app.article_taxonomy import TaxonomySpec  # noqa: E402
from app.table_framework import Context  # noqa: E402

tables = {}
for key, title, singular, attribute in (
    ("article_families", "Famílias de artigos", "família", "family"),
    ("article_subfamilies", "Subfamílias de artigos", "subfamília", "category"),
    ("article_brands", "Marcas", "marca", "brand"),
):
    spec = TaxonomySpec(key, title, singular, attribute)
    families = [SimpleNamespace(**r) for r in ns["catalog"]["classifications"]["family"]]
    ctx = Context(db=SimpleNamespace(scalars=lambda _: families), user=SimpleNamespace(role="administrador"),
                  spec=spec, _integration={"mode": "local"})
    tables[key] = spec.describe(ctx)


def intercept(route):
    path = urlsplit(route.request.url).path
    if path.startswith("/api/tables/article_"):
        key = path.split("/")[3]
        if route.request.method == "POST":
            if route.request.post_data_json["values"]["name"] == "Duplicada":
                return route.fulfill(status=409, json={"detail": "Já existe uma classificação com esta designação."})
            saved.append(route.request.post_data_json)
            return route.fulfill(json={"id": path.split("/")[-1] if len(path.split("/")) == 6 else "new", "erp": None,
                "article_revisions": [{"id": "p1", "before": 1, "after": 2}] if path.endswith("/article_families/records/f1") else []})
        if path.endswith("/records"):
            return route.fulfill(json={"rows": [], "total": 0, "total_all": 0, "page": 1, "pages": 1, "size": 25})
        return route.fulfill(json=tables[key])
    if urlsplit(route.request.url).path == "/api/stock/articles" and route.request.method == "POST":
        saved.append(route.request.post_data_json)
        return route.fulfill(json={"id": "new"})
    if path == "/api/stock/articles/p1" and route.request.method == "PUT":
        saved.append(route.request.post_data_json)
        return route.fulfill(json={"id": "p1"})
    return ns["mock"](route)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.getenv("GPGEST_TEST_CHROMIUM") or None)
    for width in (1440, 390):
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/**", intercept)
        page.goto(os.getenv("GPGEST_UI_URL", "http://127.0.0.1:5174") + "/?pagina=Artigos%20e%20validades")
        page.get_by_role("button", name="Novo artigo", exact=True).click()
        dialog = page.get_by_role("dialog", name="Novo artigo", exact=True)
        expect(dialog.get_by_label("Subfamília / categoria", exact=True)).to_be_disabled()
        dialog.get_by_label("Família", exact=True).select_option("Suplementos")
        expect(dialog.get_by_label("Subfamília / categoria", exact=True).locator("option")).to_have_text(["Por indicar", "Vitaminas"])
        dialog.get_by_label("Subfamília / categoria", exact=True).select_option("Vitaminas")
        dialog.get_by_label("Família", exact=True).select_option("Alimentação")
        expect(dialog.get_by_label("Subfamília / categoria", exact=True)).to_have_value("")
        expect(dialog.get_by_label("Subfamília / categoria", exact=True).locator("option")).to_have_text(["Por indicar", "Chás"])
        dialog.get_by_label("Subfamília / categoria", exact=True).select_option("Chás")
        dialog.get_by_label("Marca", exact=True).select_option("Marca de teste")
        dialog.get_by_label("Código interno", exact=True).fill("TESTE")
        dialog.get_by_label("Designação", exact=True).fill("Classificação fictícia")
        dialog.get_by_role("button", name="Editar marca", exact=True).click()
        editor = dialog.get_by_role("region", name="Editar marca", exact=True)
        editor.get_by_label("Designação da classificação", exact=True).fill("Marca revista")
        page.screenshot(path=str(output / f"inline-editor-{width}.png"), full_page=True)
        editor.get_by_label("Designação da classificação", exact=True).press("Enter")
        expect(editor).not_to_be_visible()
        expect(dialog.get_by_label("Marca", exact=True)).to_have_value("Marca revista")
        expect(dialog.get_by_label("Designação", exact=True)).to_have_value("Classificação fictícia")
        assert "values" in saved[-1]  # Enter guardou só a marca, sem submeter o artigo.
        dialog.get_by_role("button", name="Nova família", exact=True).click()
        editor = dialog.get_by_role("region", name="Nova família", exact=True)
        editor.get_by_label("Designação da classificação", exact=True).fill("Cuidados")
        editor.get_by_role("button", name="Guardar classificação", exact=True).click()
        expect(dialog.get_by_label("Família", exact=True)).to_have_value("Cuidados")
        expect(dialog.get_by_label("Subfamília / categoria", exact=True)).to_have_value("")
        dialog.get_by_role("button", name="Nova subfamília", exact=True).click()
        editor = dialog.get_by_role("region", name="Nova subfamília", exact=True)
        expect(editor.get_by_label("Família da subfamília", exact=True)).to_have_value("new")
        editor.get_by_label("Designação da classificação", exact=True).fill("Pele")
        editor.get_by_role("button", name="Guardar classificação", exact=True).click()
        expect(dialog.get_by_label("Subfamília / categoria", exact=True)).to_have_value("Pele")
        dialog.get_by_role("button", name="Nova marca", exact=True).click()
        editor = dialog.get_by_role("region", name="Nova marca", exact=True)
        editor.get_by_label("Designação da classificação", exact=True).fill("Duplicada")
        editor.get_by_role("button", name="Guardar classificação", exact=True).click()
        expect(editor.get_by_role("alert")).to_contain_text("Já existe")
        expect(editor.get_by_label("Designação da classificação", exact=True)).to_have_value("Duplicada")
        editor.get_by_role("button", name="Cancelar edição da classificação", exact=True).click()
        expect(dialog.get_by_label("Marca", exact=True)).to_have_value("Marca revista")
        assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
        page.screenshot(path=str(output / f"article-{width}.png"), full_page=True)
        dialog.get_by_role("button", name="Guardar", exact=True).click()
        expect(dialog).not_to_be_visible()
        assert (saved[-1]["family"], saved[-1]["category"], saved[-1]["brand"]) == ("Cuidados", "Pele", "Marca revista")
        assert not errors, errors
        page.close()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.route("**/api/**", intercept)
    for table in tables.values():
        page.goto(os.getenv("GPGEST_UI_URL", "http://127.0.0.1:5174") + "/?pagina=" + table["title"])
        expect(page.get_by_role("heading", name=table["title"], exact=True)).to_be_visible()
        page.get_by_role("button", name="Nova " + table["singular"], exact=True).click()
        dialog = page.get_by_role("dialog", name="Nova " + table["singular"], exact=True)
        if table["key"] == "article_subfamilies":
            expect(dialog.get_by_label("Família", exact=False)).to_have_value("")
            dialog.get_by_label("Família", exact=False).select_option("f1")
        dialog.get_by_label("Designação", exact=False).fill("Nova classificação")
        page.screenshot(path=str(output / (table["key"] + ".png")), full_page=True)
        dialog.get_by_role("button", name="Criar " + table["singular"], exact=True).click()
        expect(dialog).not_to_be_visible()
        assert saved[-1]["values"]["name"] == "Nova classificação"
        if table["key"] == "article_subfamilies":
            assert saved[-1]["values"]["family_id"] == "f1"
    page.goto(os.getenv("GPGEST_UI_URL", "http://127.0.0.1:5174") + "/?pagina=Artigos%20e%20validades")
    page.get_by_role("navigation", name="Artigos e stock", exact=True).get_by_role("button", name="Artigos", exact=True).click()
    page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
    page.get_by_role("menuitem", name="Editar Suplemento de teste", exact=True).click()
    dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
    dialog.get_by_label("Designação", exact=True).fill("Rascunho preservado")
    dialog.get_by_role("button", name="Editar família", exact=True).click()
    editor = dialog.get_by_role("region", name="Editar família", exact=True)
    editor.get_by_label("Designação da classificação", exact=True).fill("Suplementos revistos")
    editor.get_by_role("button", name="Guardar classificação", exact=True).click()
    expect(editor).not_to_be_visible()
    expect(dialog.get_by_label("Família", exact=True)).to_have_value("Suplementos revistos")
    expect(dialog.get_by_label("Subfamília / categoria", exact=True)).to_have_value("Vitaminas")
    expect(dialog.get_by_label("Designação", exact=True)).to_have_value("Rascunho preservado")
    dialog.get_by_role("button", name="Guardar", exact=True).click()
    expect(dialog).not_to_be_visible()
    assert saved[-1]["revision"] == 2 and saved[-1]["name"] == "Rascunho preservado"
    ns["catalog"]["can_manage_classifications"] = False
    page.reload()
    page.get_by_role("button", name="Novo artigo", exact=True).click()
    dialog = page.get_by_role("dialog", name="Novo artigo", exact=True)
    expect(dialog.get_by_role("button", name="Nova família", exact=True)).to_have_count(0)
    expect(dialog.get_by_role("button", name="Nova marca", exact=True)).to_have_count(0)
    browser.close()
print("PASS: seleção por família, limpeza da subfamília, inativos, gravação e layout a 1440/390 px; sem escritas reais.")
