"""Navigation regressions in isolated browser contexts; every API is intercepted.

No real accounts, financial writes, messages or application data are changed.
Requires the development frontend on 127.0.0.1:5174.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-visual-template-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
BOOT = ns["boot"]
BOOT["company"]["id"] = "isolated-ui-company"
BOOT["integration"] = {"mode": "local"}
BOOT["registers"] = [{"id": f"register-{index}", "name": f"Caixa de teste {index:02}", "store_id": "demo", "active": True} for index in range(23)]
EMAIL = {
    "enabled": False, "host": "smtp.example.test", "port": 587, "security": "starttls",
    "username": "", "has_password": False, "max_message_bytes": 20_000_000,
    "sender_name": "Empresa de teste isolado", "sender_email": "sender@example.test",
    "accountant_name": "", "accountant_email": "", "additional_to": [], "cc": [],
    "periodicity": "mensal", "custom_period_note": "", "subject_template": "Documentos de teste",
    "message_template": "Mensagem de teste isolado", "signature": "", "revision": 1,
}
OUTPUT = ROOT / ".local/workspace-ui"
OUTPUT.mkdir(exist_ok=True)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, theme in [(1440, "current"), (820, "dark"), (390, "dark")]:
            context = browser.new_context(viewport={"width": width, "height": 960}, locale="pt-PT")
            context.add_init_script(f"localStorage.setItem('iazela-theme', '{theme}')")
            calls, errors, console_errors = [], [], []

            def intercept(route):
                path = urlsplit(route.request.url).path.removeprefix("/api")
                calls.append((path, route.request.method))
                data = None
                if path == "/setup/status":
                    data = {"completed": True, "pending": [], "unclosed_count": 0}
                elif path == "/dashboard/overview":
                    data = {"period": {"label": "Teste isolado", "in_progress": False},
                            "comparison": {"method": "Período anterior", "start": "2026-09-01", "end": "2026-09-20"},
                            "generated_at": "2026-10-20T12:00:00Z", "attention": [], "mode": "local",
                            "permissions": {"finance": True, "costs": False}, "treasury": {"accounts": []}}
                elif path == "/settings/email":
                    data = EMAIL
                elif path == "/emails":
                    data = []
                elif path == "/accounting/email/periods":
                    data = {"periodicity": "mensal", "note": "Teste isolado", "periods": []}
                elif path == "/audit":
                    data = [{"id": str(index), "created_at": "2026-10-20T12:00:00Z", "action": "teste_isolado", "user_id": "demo-user", "detail": f"Registo de teste {index:02}"} for index in range(23)]
                elif path == "/drive/pending-imports":
                    data = {"configured": False, "pending": {}}
                elif path == "/legal/public":
                    data = {"published": False, "cookies": [], "optional_categories": []}
                if data is None:
                    return ns["intercept"](route)
                route.fulfill(content_type="application/json", body=json.dumps(data))

            context.route("**/api/**", intercept)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(error.stack or str(error)))
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.goto("http://127.0.0.1:5174/entrar?pagina=Dashboard")
            dashboard = page.get_by_role("tablist", name="Áreas do dashboard")
            try:
                expect(dashboard).to_be_visible(timeout=15000)
            except AssertionError:
                print("Initial diagnostic:", errors, console_errors, page.locator("body").inner_text()[:2500], flush=True)
                raise
            expect(dashboard.get_by_role("tab", name="Resumo do negócio")).to_have_attribute("aria-selected", "true")
            expect(page.get_by_role("heading", name="Operação de caixa", exact=True)).not_to_be_visible()
            dashboard.get_by_role("tab", name="Operação de caixa").click()
            expect(page.get_by_role("heading", name="Operação de caixa", exact=True)).to_be_visible()
            page.screenshot(path=str(OUTPUT / f"dashboard-{width}.png"))
            # Native history selects the previous area and restores it forward.
            page.go_back()
            expect(dashboard.get_by_role("tab", name="Resumo do negócio")).to_have_attribute("aria-selected", "true")
            page.go_forward()
            expect(dashboard.get_by_role("tab", name="Operação de caixa")).to_have_attribute("aria-selected", "true")

            # Direct destination, scoped module navigation, and lazy history data.
            page.goto("http://127.0.0.1:5174/entrar?pagina=Email")
            tabs = page.get_by_role("tablist", name="Áreas do email")
            expect(tabs).to_be_visible()
            assert ("/emails", "GET") not in calls
            host = page.get_by_label("Servidor SMTP", exact=True)
            host.fill("smtp.edited.example.test")
            tabs.get_by_role("tab", name="Destinatários").click()
            page.get_by_label("Nome do contabilista ou gabinete").fill("Rascunho preservado")
            expect(host).not_to_be_visible()
            tabs.get_by_role("tab", name="Histórico de envios").click()
            expect(page.get_by_role("heading", name="Histórico de emails")).to_be_visible()
            # Keyboard moves focus and Enter activates the next tab.
            tabs.get_by_role("tab", name="Histórico de envios").focus()
            page.keyboard.press("Home")
            page.keyboard.press("Enter")
            expect(host).to_be_visible()
            expect(host).to_have_value("smtp.edited.example.test")
            nav = page.get_by_role("navigation", name="Navegação de Configurações")
            nav.get_by_role("button", name="Aparência", exact=True).click()
            expect(page.get_by_role("heading", name="Aparência", exact=True, level=1)).to_be_visible()
            nav.get_by_role("button", name="Email", exact=True).click()
            expect(host).to_have_value("smtp.edited.example.test")
            tabs.get_by_role("tab", name="Destinatários").click()
            expect(page.get_by_label("Nome do contabilista ou gabinete")).to_have_value("Rascunho preservado")
            page.screenshot(path=str(OUTPUT / f"email-{width}.png"))
            # Invalid required field in a hidden area is revealed before submission.
            tabs.get_by_role("tab", name="Mensagem e assinatura").click()
            page.get_by_label("Assunto predefinido").fill("")
            tabs.get_by_role("tab", name="Ligação SMTP").click()
            page.get_by_role("button", name="Guardar configuração de email").click()
            expect(page.get_by_label("Assunto predefinido")).to_be_visible()
            assert not any(path == "/settings/email" and method == "POST" for path, method in calls)
            page.get_by_label("Assunto predefinido").fill("Documentos de teste")
            page.locator(".user-menu summary").click()
            page.locator(".user-menu-panel").get_by_role("button", name="Terminar sessão", exact=True).click()
            discard = page.get_by_role("alertdialog")
            expect(discard).to_be_visible()
            discard.get_by_role("button", name="Continuar a editar", exact=True).click()
            expect(discard).not_to_be_visible()
            assert not any(path == "/auth/logout" for path, _ in calls)
            page.keyboard.press("Escape")

            # A reload has no persistent form drafts; dismiss only the browser's leave prompt.
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto("http://127.0.0.1:5174/entrar?pagina=Confer%C3%AAncia+documental")
            expect(page.get_by_label("Fornecedor ou documento", exact=True)).to_be_visible()
            page.get_by_label("Fornecedor ou documento", exact=True).fill("FT DEMO/1")
            row = page.get_by_role("row").filter(has=page.get_by_role("cell", name="FT DEMO/1", exact=True))
            row.get_by_role("button", name="Conferir", exact=True).click()
            drawer = page.get_by_role("dialog")
            expect(drawer).to_have_class(__import__("re").compile("detail-drawer"))
            expect(drawer.get_by_role("heading", name="Dados da fatura", exact=True)).to_be_visible()
            detail = drawer.get_by_role("tablist", name="Detalhes do documento")
            detail.get_by_role("tab", name="Original e localização").click()
            expect(drawer.locator("iframe")).to_be_visible()
            detail.get_by_role("tab", name="Pagamentos e banco").click()
            expect(drawer.get_by_role("heading", name="Pagamentos associados e banco")).to_be_visible()
            detail.get_by_role("tab", name="Histórico", exact=True).click()
            expect(drawer.get_by_role("heading", name="Histórico de ações")).to_be_visible()
            page.screenshot(path=str(OUTPUT / f"documento-{width}.png"))
            detail.get_by_role("tab", name="Original e localização").click()
            location = drawer.get_by_label("Localização do papel ou ficheiro", exact=True)
            location.fill("Rascunho no painel")
            detail.get_by_role("tab", name="Histórico", exact=True).click()
            page.keyboard.press("Escape")
            discard = page.get_by_role("alertdialog")
            expect(discard).to_be_visible()
            discard.get_by_role("button", name="Continuar a editar", exact=True).click()
            detail.get_by_role("tab", name="Original e localização").click()
            expect(location).to_have_value("Rascunho no painel")
            page.keyboard.press("Escape")
            discard.get_by_role("button", name="Descartar alterações", exact=True).click()
            expect(drawer).not_to_be_visible()
            # Filter state survives visiting another accounting page and browser back.
            module = page.get_by_role("navigation", name="Navegação de Contabilidade")
            module.get_by_role("button", name="Entrega à contabilidade", exact=True).click()
            try:
                expect(page.get_by_role("heading", name="Entrega à contabilidade", exact=True, level=1)).to_be_visible()
            except AssertionError:
                print("Delivery diagnostic", page.url, errors, page.locator("body").inner_text()[-1700:], flush=True)
                raise
            page.go_back()
            try:
                expect(page.get_by_label("Fornecedor ou documento", exact=True)).to_have_value("FT DEMO/1", timeout=15000)
            except AssertionError:
                print("Back diagnostic", page.url, errors, page.locator("body").inner_text()[-1800:], flush=True)
                raise
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
            page.goto("http://127.0.0.1:5174/entrar?pagina=Caixas")
            table = page.get_by_role("table", name="caixas", exact=True)
            expect(table.locator("tbody tr")).to_have_count(10)
            page.get_by_role("button", name="Seguinte", exact=True).click()
            expect(table).to_contain_text("Caixa de teste 10")
            page.get_by_label("Pesquisar caixas").fill("teste 22")
            expect(table.locator("tbody tr")).to_have_count(1)
            expect(table.get_by_role("button", name="Editar caixa Caixa de teste 22")).to_be_visible()
            expect(table.get_by_role("button", name="Remover caixa Caixa de teste 22")).to_be_visible()
            page.screenshot(path=str(OUTPUT / f"caixas-{width}.png"))
            page.goto("http://127.0.0.1:5174/entrar?pagina=Relat%C3%B3rios")
            audit = page.get_by_role("table", name="histórico e auditoria", exact=True)
            expect(audit.locator("tbody tr")).to_have_count(10)
            page.get_by_label("Pesquisar histórico e auditoria").fill("teste 22")
            expect(audit.locator("tbody tr")).to_have_count(1)
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), width
            assert not errors, errors
            assert not console_errors, console_errors
            assert all(method == "GET" for _, method in calls), calls
            print(f"PASS {width}px {theme}: navigation, history, drafts, hidden validation, drawer, filters, tables/cards, no writes/errors", flush=True)
            context.close()
        browser.close()


if __name__ == "__main__":
    run()
