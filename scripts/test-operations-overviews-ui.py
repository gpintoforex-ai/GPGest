"""Cross-module overview/review paths; synthetic intercepted APIs, every write forbidden."""
import copy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "scripts/test-visual-template-ui.py"
base = {"__file__": str(source)}
exec(source.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], base)
cash_source = ROOT / "scripts/test-cash-responsive-ui.py"
cash = {"__file__": str(cash_source)}
exec(cash_source.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], cash)
inventory_source = ROOT / "scripts/test-inventory-ui.py"
inventory = {"__file__": str(inventory_source)}
exec(inventory_source.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], inventory)
inventory["report_row"]["below_minimum"] = True
inventory["report_row"]["quantity"] = "1"
OUT = ROOT / ".local/operations-overviews"
OUT.mkdir(parents=True, exist_ok=True)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in [1440, 390]:
            for theme in ["current", "dark"]:
                errors, writes = [], []
                boot = copy.deepcopy(base["boot"])
                boot["integration"] = {"mode": "local", "provider": "none"}
                boot["company"]["id"] = f"operations-{width}-{theme}"
                first = copy.deepcopy(cash["session"])
                first["count_data"]["values"]["numerario"]=4500
                second = {**copy.deepcopy(first), "id": "closed-sheet", "closed_at": "2026-10-22T20:00:00Z"}
                periods = [
                    {"vat_period": "mensal", "year": 2026, "number": 1, "start": "2026-01-01", "end": "2026-01-31", "documents": 5, "without_attachment": 3, "calendar_ended": True, "closed_at": None, "reason": ""},
                    {"vat_period": "mensal", "year": 2026, "number": 2, "start": "2026-02-01", "end": "2026-02-28", "documents": 7, "without_attachment": 0, "calendar_ended": True, "closed_at": "2026-03-05T10:00:00Z", "reason": "Teste"},
                ]
                meta = {"total": 1, "inbox": 1, "proposals": 1, "queue": 0, "errors": 0, "duplicates": 0, "latest": [], "origins": [], "types": [], "warnings": [], "can_manage": False, "can_upload": False, "ocr_available": True, "last_sync_at": None}

                stock_mode = [False]
                def intercept(route):
                    path = urlsplit(route.request.url).path.removeprefix("/api")
                    if route.request.method != "GET":
                        writes.append(path)
                        return route.fulfill(status=500, json={"detail": "Unexpected write"})
                    if stock_mode[0]:
                        return inventory["mock"](route)
                    if path == "/emails":
                        return route.fulfill(json=[])
                    responses = {"/bootstrap": boot, "/sessions": [first, second], "/archive/overview": meta, "/archive/documents": {"documents": [], "total": 0}, "/accounting/delivery-periods": {"periods": periods, "vat_period": "mensal", "vat_regime": "normal"}}
                    if path in responses:
                        return route.fulfill(json=responses[path])
                    return base["intercept"](route)

                context = browser.new_context(viewport={"width": width, "height": 1000}, locale="pt-PT")
                context.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
                page = context.new_page()
                page.route("**/api/**", intercept)
                page.on("pageerror", lambda error: (errors.append(str(error)), print("PAGEERROR",error.stack,flush=True)))

                page.goto("http://127.0.0.1:5174/entrar?pagina=Folha+de+caixa")
                filters = page.get_by_role("group", name="Filtrar folhas de caixa", exact=True)
                expect(filters).to_be_visible(timeout=30000)
                filters.get_by_role("button", name="Fechadas", exact=False).click()
                expect(page.locator(".cash-sheet-list > section")).to_have_count(1)
                expect(page.locator(".cash-sheet-list")).to_contain_text("Fechada")
                filters.get_by_role("button", name="Em aberto", exact=False).click()
                expect(page.locator(".cash-sheet-list > section")).to_have_count(1)
                expect(page.locator(".cash-sheet-list")).to_contain_text("Em aberto")
                advice=page.get_by_role("button",name="Rever contagem de numerário",exact=True)
                expect(advice).to_be_visible()
                advice.click()
                counter=page.get_by_role("dialog",name="Contar numerário",exact=True)
                expect(counter).to_be_visible()
                counter.get_by_role("button",name="Cancelar",exact=True).click()
                expect(counter).not_to_be_visible()
                filters.get_by_role("button", name="Todas", exact=False).click()
                expect(page.locator(".cash-sheet-list > section")).to_have_count(2)
                page.screenshot(path=str(OUT / f"cash-{width}-{theme}.png"), full_page=True)

                page.goto("http://127.0.0.1:5174/entrar?pagina=Entrega+%C3%A0+contabilidade")
                overview = page.get_by_role("region", name="Situação dos dossiês", exact=True)
                expect(overview).to_be_visible(timeout=30000)
                expect(overview.locator(".ui-kpi").filter(has=page.get_by_text("Por encerrar", exact=True)).locator(".ui-kpi-value")).to_have_text("1")
                expect(overview.locator(".ui-kpi").filter(has=page.get_by_text("Períodos com anexos em falta", exact=True)).locator(".ui-kpi-value")).to_have_text("1")
                expect(overview).to_contain_text("encerramento não significa envio")
                page.screenshot(path=str(OUT / f"accounting-{width}-{theme}.png"), full_page=True)

                page.goto("http://127.0.0.1:5174/entrar?pagina=Arquivo+%E2%80%94+Vis%C3%A3o+geral")
                review = page.get_by_role("button", name="Rever caixa de entrada", exact=True)
                expect(review).to_be_visible(timeout=30000)
                page.get_by_role("complementary", name="Rever as pendências do arquivo", exact=True).get_by_text("Origem da sugestão", exact=True).click()
                expect(page.get_by_text("Origem: visão geral do arquivo", exact=False)).to_be_visible()
                review.click()
                expect(page.get_by_role("heading", name="Caixa de entrada", exact=True)).to_be_visible()
                page.goto("http://127.0.0.1:5174/entrar?pagina=Confer%C3%AAncia+documental")
                page.get_by_label("Desde",exact=True).fill("2026-10-01")
                page.get_by_label("Até",exact=True).fill("2026-10-31")
                document_review=page.get_by_role("button",name="Consultar documento sem anexo",exact=True)
                expect(document_review).to_be_visible(timeout=30000)
                page.screenshot(path=str(OUT / f"documents-{width}-{theme}.png"), full_page=True)
                document_review.click()
                detail=page.get_by_role("dialog").last
                expect(detail).to_contain_text("FT DEMO/2",timeout=20000)
                detail.get_by_role("button",name="Fechar",exact=True).first.click()
                expect(detail).not_to_be_visible()

                base["fail_documents"]=True
                page.reload()
                expect(page.locator(".doc-overview-grid .metric-card > strong")).to_have_text(["—"]*6,timeout=20000)
                expect(page.get_by_role("button",name="Consultar documento sem anexo",exact=True)).to_have_count(0)
                expect(page.get_by_role("alert")).to_contain_text("Não foi possível atualizar")
                base["fail_documents"]=False

                stock_mode[0]=True
                page.goto("http://127.0.0.1:5174/entrar?pagina=Artigos+e+validades&area.stock=Painel")
                stock_review=page.get_by_role("button",name="Consultar artigo prioritário",exact=True)
                expect(stock_review).to_be_visible(timeout=30000)
                page.screenshot(path=str(OUT / f"stock-{width}-{theme}.png"), full_page=True)
                stock_review.click()
                article=page.get_by_role("dialog").last
                expect(article.get_by_label("Designação",exact=True)).to_have_value(inventory["article"]["name"])
                expect(article.get_by_role("button",name="Guardar",exact=True)).to_have_count(0)
                article.get_by_role("button",name="Fechar",exact=True).first.click()
                expect(article).not_to_be_visible()
                assert not writes, writes
                assert not errors, errors
                assert not page.evaluate("document.documentElement.scrollWidth>innerWidth"), (width, theme)
                print(f"PASS {width}px {theme}: cash status filters and count review preserve loaded sheets; dossier counts use query data; archive advice exposes evidence; document and stock advice open original details without writes; failed document queries never become zero; no writes/runtime errors/overflow", flush=True)
                context.close()
        browser.close()


if __name__ == "__main__":
    run()
