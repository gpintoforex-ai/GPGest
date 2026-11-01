"""Phase B browser contracts against real shared components; no application API calls."""
import os
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".local/design-system"
OUT.mkdir(parents=True, exist_ok=True)


def contrast(colors):
    def luminance(color):
        values = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(v * weight for v, weight in zip(linear, [.2126, .7152, .0722]))
    a, b = sorted([luminance(colors["color"]), luminance(colors["background"])])
    return (b + .05) / (a + .05)


def colors(locator):
    return locator.evaluate("""node => {
        const color=getComputedStyle(node).color;
        while (getComputedStyle(node).backgroundColor === 'rgba(0, 0, 0, 0)') node=node.parentElement;
        return {color,background:getComputedStyle(node).backgroundColor};
    }""")


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        widths = [int(v) for v in os.environ.get("GPGEST_TEST_WIDTHS", "1536,768,390").split(",")]
        for width in widths:
            for theme in ["current", "dark"]:
                errors, requests = [], []
                context = browser.new_context(viewport={"width":width,"height":1024},locale="pt-PT",reduced_motion="reduce")
                context.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/**", lambda route: (requests.append(route.request.url),route.abort()))
                page.goto("http://127.0.0.1:5174/test-fixtures/business-ui.html")
                expect(page.get_by_role("heading",name="Componentes comuns",exact=True)).to_be_visible(timeout=30000)
                assert contrast(colors(page.get_by_role("heading",name="Componentes comuns",exact=True))) >= 4.5
                expect(page.get_by_text("Catálogo de validação",exact=False)).to_be_visible()
                expect(page.get_by_role("button",name="0 Indicador neutral",exact=False)).to_be_visible()
                expect(page.locator('[data-state="loading"]')).to_contain_text("A calcular")
                expect(page.locator('[data-state="loading"]')).to_be_disabled()
                expect(page.locator('[data-state="error"]')).not_to_contain_text("999")
                expect(page.locator('[aria-label="Valor em falta"]')).to_contain_text("—")
                expect(page.get_by_label("Ações executadas no teste")).to_have_text("Nenhuma ação executada")
                page.get_by_role("button",name="0 Indicador neutral",exact=False).click()
                expect(page.get_by_label("Ações executadas no teste")).to_have_text("indicador:neutral")
                process = page.get_by_role("navigation",name="Processo de teste",exact=True)
                process.get_by_role("button",name="Necessidades",exact=True).click()
                expect(process.get_by_role("button",name="Receção",exact=True)).to_be_disabled()
                for badge in page.locator(".ui-status").all():
                    assert contrast(colors(badge)) >= 4.5, (theme, badge.inner_text(), colors(badge))
                filters = page.get_by_role("group",name="Filtros de teste",exact=True)
                all_button = filters.get_by_role("button",name="Todos",exact=False)
                all_button.focus()
                all_button.press("ArrowRight")
                expect(filters.get_by_role("button",name="Pendentes",exact=False)).to_be_focused()
                page.keyboard.press("ArrowRight")
                expect(filters.get_by_role("button",name="Prontos",exact=False)).to_be_focused()
                page.keyboard.press("Enter")
                expect(page.get_by_text("Filtro atual: ready",exact=True)).to_be_visible()
                selected = filters.locator('[aria-pressed="true"]')
                selected.evaluate("node=>node.style.backgroundImage='none'")
                assert contrast(colors(selected)) >= 4.5, (theme,colors(selected))
                expect(filters.get_by_role("button",name="Indisponível",exact=True)).to_be_disabled()
                tabs = page.get_by_role("navigation",name="Áreas de teste",exact=True)
                tabs.get_by_role("button",name="Detalhe",exact=True).click()
                selected_tab = tabs.locator('[aria-current="page"]')
                selected_tab.evaluate("node=>node.style.backgroundImage='none'")
                assert contrast(colors(selected_tab)) >= 4.5, (theme,colors(selected_tab))
                progress = page.get_by_role("progressbar",name="Objetivo atingido",exact=True)
                assert progress.get_attribute("value") == "250"
                for label in ["Objetivo por configurar","Cálculo pendente","Limite zero"]:
                    assert page.get_by_role("progressbar",name=label,exact=True).get_attribute("value") is None
                expect(page.get_by_role("button",name="Rever sem evidência",exact=True)).to_be_disabled()
                suggestion = page.get_by_role("complementary",name="Rever composição",exact=True)
                suggestion.locator("summary").click()
                expect(suggestion).to_contain_text("64,50")
                suggestion.get_by_role("button",name="Rever sugestão",exact=True).click()
                expect(page.get_by_label("Ações executadas no teste")).to_have_text("indicador:neutral · necessidades · rever")
                page.get_by_role("searchbox",name="Pesquisar registos de teste",exact=True).fill("teste 12")
                grid = page.get_by_role("table",name="registos de teste",exact=True)
                expect(grid.locator("tbody tr")).to_have_count(1)
                page.get_by_role("searchbox",name="Pesquisar registos de teste",exact=True).fill("")
                page.get_by_role("group",name="Filtros de registos de teste",exact=True).locator("label",has_text="Grupo").locator("select").select_option("par")
                expect(page.get_by_text("6 registo(s) · Página 1 de 1",exact=True)).to_be_visible()
                assert grid.locator("td.numeric").first.evaluate("node=>getComputedStyle(node).textAlign") in ("right","end") or width <= 600
                with page.expect_download() as download:
                    page.get_by_role("button",name="Exportar CSV",exact=True).first.click()
                saved = download.value.path().read_bytes().decode("utf-8-sig")
                assert saved.splitlines()[0] == '"Artigo";"Quantidade"' and len(saved.splitlines()) == 7, saved
                waiting = page.locator(".workspace-data-table").nth(1)
                expect(waiting.get_by_role("status")).to_have_text("A carregar…")
                expect(waiting.get_by_text("Sem registos para apresentar")).to_have_count(0)
                failed = page.locator(".workspace-data-table").nth(2)
                expect(failed.get_by_role("alert")).to_contain_text("Falha de teste na consulta.")
                expect(failed.get_by_text("Sem registos para apresentar")).to_have_count(0)
                field = page.get_by_label("Campo de teste",exact=True)
                field.focus()
                if theme == "current":
                    assert colors(field)["background"] == "rgb(255, 255, 255)", colors(field)
                assert contrast(colors(field)) >= 4.5
                page.get_by_label("Mostrar coluna",exact=True).uncheck()
                expect(page.get_by_label("Mostrar coluna",exact=True)).not_to_be_checked()
                page.get_by_role("button",name="Abrir janela de teste",exact=True).click()
                dialog = page.get_by_role("dialog",name="Janela de teste",exact=True)
                expect(dialog).to_be_visible()
                dialog.get_by_label("Observações",exact=True).fill("Alteração de teste")
                page.keyboard.press("Escape")
                confirmation = page.get_by_role("alertdialog",name="Descartar alterações?",exact=True)
                expect(confirmation).to_be_visible()
                confirmation.get_by_role("button",name="Continuar a editar",exact=True).click()
                expect(dialog.get_by_label("Observações",exact=True)).to_have_value("Alteração de teste")
                dialog.get_by_role("button",name="Cancelar",exact=True).click()
                confirmation.get_by_role("button",name="Descartar alterações",exact=True).click()
                expect(dialog).to_have_count(0)
                expect(page.get_by_role("button",name="Abrir janela de teste",exact=True)).to_be_focused()
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (width,theme)
                assert not errors, errors
                assert not requests, requests
                page.evaluate("document.activeElement?.blur();scrollTo(0,0)")
                page.screenshot(path=str(OUT/f"phase-b-{width}-{theme}.png"),full_page=True)
                print(f"PASS {width}px {theme}: shared states, contrast, gradient fallback, keyboard filters, unknown progress, evidence review, table search, white fields, columns and dialog focus; no API calls/errors/overflow",flush=True)
                context.close()
        browser.close()


if __name__ == "__main__":
    run()
