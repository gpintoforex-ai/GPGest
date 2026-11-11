"""Obrigações e Prazos → Alertas: adesão do utilizador, autorização da empresa (administrador), envio e histórico.

Reutiliza os dados simulados de test-obligations-ui.py; só as escritas esperadas são aceites. Requer o Vite em 127.0.0.1:5174.
"""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-obligations-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-obligations-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
ns["boot"]["user"]["role"] = "administrador"
OUT = ROOT / ".local/obligation-alerts-ui"
OUT.mkdir(parents=True, exist_ok=True)
state = {"company_enabled": False, "smtp_enabled": True, "my_email": "admin@empresa.pt", "my_preference": False,
         "my_pending": [{"title": "Entrega do IVA", "due_date": "2026-11-18", "alert": "7"}], "pending_users": 1, "pending_alerts": 2,
         "last_run": None, "last_result": None,
         "history": [{"user": "Gestora", "alert": "vencido", "status": "incerto", "error": "Ligação perdida", "created_at": "2026-11-10T09:00:00+00:00", "title": "Seguro"}],
         "rules": ["Email: um por dia com os alertas dos dias configurados em cada prazo e os vencidos (uma vez). Nunca repete um alerta enviado."]}
writes = []


def intercept(route):
    path, method = urlsplit(route.request.url).path.removeprefix("/api"), route.request.method
    if path == "/obligations/notifications" and method == "GET":
        return route.fulfill(json=state)
    if path.startswith("/obligations/notifications/") and method in ("PUT", "POST"):
        body = route.request.post_data_json
        writes.append((method, path, body))
        if path.endswith("/me"):
            state["my_preference"] = body["obligations_email"]
        elif path.endswith("/company"):
            state["company_enabled"] = body["enabled"]
        else:
            return route.fulfill(json={"emails": 1, "alerts": 2, "failed": 0, "uncertain": 0, "errors": []})
        return route.fulfill(json=state)
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 390)):
        writes.clear()
        state.update(company_enabled=False, my_preference=False)
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Obriga%C3%A7%C3%B5es%20e%20Prazos")
        page.get_by_role("button", name="Alertas", exact=True).click()
        dialog = page.get_by_role("dialog", name="Alertas e notificações")
        expect(dialog).to_contain_text("Nunca repete um alerta enviado")
        expect(dialog).to_contain_text("desativados para a empresa")
        expect(dialog.get_by_role("button", name="Enviar agora")).to_be_disabled()
        expect(dialog.get_by_role("table", name="Histórico de envios")).to_contain_text("Incerto (não repete)")
        dialog.get_by_label("Permitir lembretes por email (cada utilizador decide se os recebe)").click()  # Controlada: muda com a resposta do servidor.
        expect(dialog.get_by_label("Permitir lembretes por email (cada utilizador decide se os recebe)")).to_be_checked()
        expect(dialog.get_by_text("Lembretes por email ativados para a empresa.")).to_be_visible()
        dialog.get_by_label("Receber por email os alertas dos meus prazos (admin@empresa.pt)").click()  # Controlada: muda com a resposta do servidor.
        expect(dialog.get_by_label("Receber por email os alertas dos meus prazos (admin@empresa.pt)")).to_be_checked()
        expect(dialog.get_by_text("Vai receber os lembretes por email.")).to_be_visible()
        expect(dialog).to_contain_text("Hoje: Entrega do IVA (7 dias antes)")
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."
        page.screenshot(path=str(OUT / f"alertas-{theme}-{width}.png"), full_page=True)
        dialog.get_by_role("button", name="Enviar agora").click()
        expect(dialog.get_by_text("Lembretes enviados.")).to_be_visible()
        assert writes == [("PUT", "/obligations/notifications/company", {"enabled": True}), ("PUT", "/obligations/notifications/me", {"obligations_email": True}),
                          ("POST", "/obligations/notifications/send", {})], writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: alertas das obrigações (autorização da empresa, adesão do utilizador, envio a pedido, histórico com incerto), temas e larguras.")
