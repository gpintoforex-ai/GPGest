"""Browser regression for legal flows, with synthetic API data; no real documents or messages."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:5174"
COOKIES = [{"name": "gpgest_session", "storage": "Cookie HttpOnly", "purpose": "Autenticação", "provider": "IAZELA", "duration": "8 horas"}]
CONFIG = {"version": "test-only-1", "identity": {k: "" for k in ("legal_name", "nif", "address", "email", "registry", "privacy_email")}, "audience": "unconfirmed", "reviewed": False}
for key in ("audience_notice", "commercial_terms", "processing_notice", "providers_notice", "transfers_notice", "retention_policy", "complaints_notice", "accessibility_notice", "dpa_instructions", "consumer_terms"):
    CONFIG[key] = ""
DOC = {"title": "Termos fictícios de teste", "sections": [{"title": "Texto apenas de teste", "text": "Texto fictício para validação do navegador."}]}
PUBLICATION = {"version": "test-only-1", "digest": "a" * 64, "identity": {"privacy_email": "privacy@example.test"}, "documents": {"termos": DOC}}


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        calls = []
        external = []
        state = {"published": False, "account": False}

        def route_api(route):
            path = urlsplit(route.request.url).path
            calls.append((path, route.request.method))
            status, data = 200, {}
            if path == "/api/legal/public":
                data = {"published": state["published"], "publication": PUBLICATION if state["published"] else None, "cookies": COOKIES, "cookie_version": "test-only-1", "optional_categories": [], "marketing_available": False}
            elif path == "/api/privacy/me":
                status = 200 if state["account"] else 401
                data = {"profile": {"email": "admin@example.test", "name": "Test Admin", "role": "administrador"}, "acceptances": [], "requests": []}
            elif path == "/api/legal/rights":
                body = route.request.post_data_json
                assert "marketing" not in body and body["email"] == "person@example.test"
                data = {"reference": "test-request", "receipt": "r" * 43, "due_at": "2026-11-19T12:00:00Z", "message": "Pedido registado no teste."}
            elif path == "/api/legal/rights/status":
                data = {"reference": "test-request", "status": "received", "due_at": "2026-11-19T12:00:00Z"}
            elif path == "/api/privacy/accept-terms":
                assert route.request.post_data_json["digest"] == "a" * 64
                data = {"receipt": "test-acceptance", "version": "test-only-1", "digest": "a" * 64}
            elif path == "/api/platform/me":
                data = {"email": "platform@example.test", "mode": "demonstracao"}
            elif path in ("/api/platform/companies", "/api/platform/audit", "/api/platform/privacy-requests"):
                data = []
            elif path == "/api/platform/legal":
                data = {"config": CONFIG, "revision": 0, "pending": ["identity.legal_name", "reviewed"], "published_version": None, "preview": {"termos": DOC}}
            else:
                status = 401
            route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

        context.route("**/api/**", route_api)
        page = context.new_page()
        page.on("request", lambda request: external.append(request.url) if urlsplit(request.url).scheme in ("http", "https") and urlsplit(request.url).netloc != urlsplit(ORIGIN).netloc else None)
        page.goto(ORIGIN + "/legal/termos")
        expect(page.get_by_text("A identificação do prestador", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Registar aceitação")).to_have_count(0)
        page.goto(ORIGIN + "/legal/cookies")
        expect(page.get_by_role("table")).to_be_visible()
        expect(page.get_by_text("Não há consentimentos opcionais ativos", exact=False)).to_be_visible()
        page.evaluate("localStorage.setItem('iazela-theme','dark'); localStorage.setItem('gpgest.cash.preferences.test','{}'); localStorage.setItem('unrelated','keep')")
        page.get_by_role("button", name="Limpar preferências guardadas").click()
        assert page.evaluate("localStorage.getItem('iazela-theme')") is None
        assert page.evaluate("localStorage.getItem('gpgest.cash.preferences.test')") is None
        assert page.evaluate("localStorage.getItem('unrelated')") == "keep"
        assert not any("aceitacao" in path for path, _ in calls)

        state["published"] = True
        page.goto(ORIGIN + "/legal/direitos")
        page.get_by_label("Email para resposta").fill("person@example.test")
        page.get_by_label("Descrição do pedido", exact=False).fill("Pedido fictício sem documentos.")
        page.get_by_role("button", name="Registar pedido", exact=True).click()
        expect(page.get_by_text("test-request", exact=False).first).to_be_visible()
        page.get_by_role("button", name="Consultar estado do pedido").click()
        expect(page.get_by_text("Estado: Recebido", exact=False)).to_be_visible()

        state["account"] = True
        page.goto(ORIGIN + "/legal/termos")
        expect(page.get_by_role("button", name="Registar aceitação")).to_be_disabled()
        page.get_by_role("checkbox").check()
        with page.expect_download() as info:
            page.get_by_role("button", name="Registar aceitação").click()
        assert info.value.suggested_filename == "iazela-aceitacao-termos.json"
        expect(page.get_by_text("Aceitação registada.", exact=False)).to_be_visible()

        page.goto(ORIGIN + "/legal/direitos")
        with page.expect_download() as info:
            page.get_by_role("button", name="Exportar dados da minha conta").click()
        assert info.value.suggested_filename == "iazela-minha-conta.json"
        page.goto(ORIGIN + "/plataforma")
        expect(page.get_by_role("heading", name="Informação legal e proteção de dados")).to_be_visible()
        page.get_by_text("Configurar e rever textos legais", exact=True).click()
        expect(page.get_by_role("button", name="Publicar versão revista")).to_be_disabled()
        expect(page.get_by_role("checkbox").first).not_to_be_checked()

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(ORIGIN + "/legal/cookies")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.tagName") == "A"
        expect(page.get_by_role("navigation", name="Informação legal")).to_be_visible()
        page.screenshot(path=str(ROOT / ".local/legal-mobile.png"), full_page=True)
        assert external == [], external
        print("PASS: publication gate, preferences, rights receipt/status, separate terms acceptance, profile export, administration, mobile layout, keyboard and no third-party requests.")
        browser.close()


if __name__ == "__main__":
    main()
