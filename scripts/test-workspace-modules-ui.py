"""Task access and state preservation using existing, intercepted module fixtures.

No API request reaches the real backend. Forecast POSTs are read-only test responses.
"""
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def load_fixture(file, delimiter):
    path = ROOT / "scripts" / file
    namespace = {"__file__": str(path)}
    exec(path.read_text(encoding="utf-8").split(delimiter)[0], namespace)
    return namespace


def run():
    cases = [
        ("liquidity", "Previsão de liquidez", load_fixture("test-liquidity-ui.py", "\nwith sync_playwright() as playwright:"), "liquidity_intercept"),
        ("erp", "Integrações", load_fixture("test-operating-modes-ui.py", "\nwith sync_playwright() as playwright:"), "intercept_modes"),
        ("stock", "Artigos e validades", load_fixture("test-inventory-ui.py", "\nwith sync_playwright() as p:"), "mock"),
        ("saft", "Importar SAF-T de vendas", load_fixture("test-saft-sales-ui.py", "\nwith sync_playwright() as playwright:"), "mock_api"),
        ("archive", "Arquivo — Visão geral", load_fixture("test-visual-template-ui.py", "\nwith sync_playwright() as playwright:"), "intercept"),
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width in [1440, 390]:
            for case, destination, fixture, handler in cases:
                context = browser.new_context(viewport={"width": width, "height": 960})
                errors, calls = [], []

                def intercept(route):
                    path = urlsplit(route.request.url).path
                    calls.append((path, route.request.method))
                    if path == "/api/legal/public":
                        route.fulfill(json={"published": False, "cookies": [], "optional_categories": []})
                    elif path == "/api/drive/pending-imports":
                        route.fulfill(json={"configured": False, "pending": {}})
                    elif case == "archive" and path == "/api/archive/overview":
                        route.fulfill(json={"can_manage": True, "can_upload": True, "is_admin": True, "total": 0, "inbox": 0, "proposals": 0, "queue": 0, "errors": 0, "duplicates": 0, "ocr_available": False, "latest": [], "origins": [], "types": {}, "origin_labels": {}})
                    elif case == "archive" and path == "/api/archive/documents":
                        route.fulfill(json={"total": 0, "documents": []})
                    elif case == "archive" and path in ["/api/archive/links", "/api/archive/audit"]:
                        route.fulfill(json=[])
                    elif case == "archive" and path == "/api/archive/settings":
                        route.fulfill(json={"retention_years": 10, "ocr_enabled": False, "auto_process": False, "include_drive": False, "auto_confirm_strong": False})
                    else:
                        fixture[handler](route)

                context.route("**/api/**", intercept)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(error.stack or str(error)))
                page.goto("http://127.0.0.1:5174/entrar?pagina=" + quote(destination))
                if case == "liquidity":
                    tabs = page.get_by_role("tablist", name="Áreas da previsão de liquidez")
                    expect(tabs).to_be_visible()
                    expect(page.get_by_role("heading", name="Saldos de origem")).to_be_visible()
                    tabs.get_by_role("tab", name="Recebimentos e pagamentos", exact=True).click()
                    search = page.get_by_placeholder("Pesquisar entidade", exact=True)
                    search.fill("inexistente")
                    expect(page.get_by_text("Sem movimentos nesta lista.", exact=True)).to_be_visible()
                    tabs.get_by_role("tab", name="Pendências", exact=True).click()
                    expect(page.get_by_text("Vencida sem data prevista", exact=True)).to_be_visible()
                    tabs.get_by_role("tab", name="Compromissos e recorrências", exact=True).click()
                    expect(page.get_by_role("heading", name="Compromissos e recorrências manuais")).to_be_visible()
                    tabs.get_by_role("tab", name="Recebimentos e pagamentos", exact=True).click()
                    expect(search).to_have_value("inexistente")
                elif case == "erp":
                    outer = page.get_by_role("tablist", name="Áreas das integrações")
                    outer.get_by_role("tab", name="Configuração ERP").click()
                    tabs = page.get_by_role("tablist", name="Áreas da integração ERP")
                    expect(page.get_by_role("heading", name="Modo e ligação", exact=True)).to_be_visible()
                    for label, heading in [("Associações e saldos", "Associações de contas e entidades"), ("Pendências", "Operações pendentes"), ("Histórico e origem", "Histórico de sincronização")]:
                        tabs.get_by_role("tab", name=label, exact=True).click()
                        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
                    tabs.get_by_role("tab", name="Ligação ERP", exact=True).click()
                    expect(page.get_by_role("button", name="Pré-visualizar alteração", exact=True)).to_be_visible()
                elif case == "stock":
                    tabs = page.get_by_role("navigation", name="Artigos e stock")
                    tabs.get_by_role("button", name="Artigos", exact=True).click()
                    expect(page.get_by_role("heading", name="Catálogo de artigos")).to_be_visible()
                    tabs.get_by_role("button", name="Lotes e validades", exact=True).click()
                    expect(page.get_by_role("heading", name="Lotes com stock")).to_be_visible()
                    page.go_back()
                    expect(page.get_by_role("heading", name="Catálogo de artigos")).to_be_visible()
                elif case == "saft":
                    tabs = page.get_by_role("tablist", name="Áreas do SAF-T")
                    expect(tabs).to_be_visible()
                    tabs.get_by_role("tab", name="Histórico", exact=True).click()
                    expect(page.get_by_role("heading", name="Histórico de importações")).to_be_visible()
                    tabs.get_by_role("tab", name="Meios de pagamento", exact=True).click()
                    expect(page.get_by_text("Associar códigos SAF-T aos meios de pagamento", exact=True)).to_be_visible()
                    tabs.get_by_role("tab", name="Análise", exact=True).click()
                    expect(page.get_by_role("heading", name="Analisar faturação e recebimentos")).to_be_visible()
                else:
                    navigation = page.get_by_role("navigation", name="Navegação de Arquivo digital")
                    expect(page.get_by_role("heading", name="Recebidos recentemente")).to_be_visible()
                    navigation.get_by_role("button", name="Todos os documentos", exact=True).click()
                    search = page.get_by_label("Pesquisar", exact=True)
                    search.fill("Pesquisa preservada")
                    navigation.get_by_role("button", name="Associações", exact=True).click()
                    expect(page.get_by_role("navigation", name="Estado das associações")).to_be_visible()
                    navigation.get_by_role("button", name="Todos os documentos", exact=True).click()
                    expect(search).to_have_value("Pesquisa preservada")
                    navigation.get_by_role("button", name="Configurações", exact=True).click()
                    expect(page.get_by_role("heading", name="Processamento e conservação")).to_be_visible()
                    expect(page.get_by_role("button", name="Guardar configuração", exact=True)).to_be_visible()
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), (case, width)
                assert not errors, errors
                assert all(method == "GET" or path == "/api/liquidity/forecast" for path, method in calls), calls
                print(f"PASS {case} {width}px: task access, URLs/state, no overflow/errors or financial writes", flush=True)
                context.close()
        browser.close()


if __name__ == "__main__":
    run()
