"""PIN and local capture controls with intercepted quick-scan APIs; no real pairing or upload."""
import io
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    photo = Image.new("RGB", (600, 850), "white")
    draw = ImageDraw.Draw(photo)
    draw.rectangle((25, 25, 575, 825), outline="black", width=4)
    draw.text((60, 90), "TESTE ISOLADO - SEM DADOS REAIS", fill="black")
    buffer = io.BytesIO()
    photo.save(buffer, format="PNG")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_init_script("localStorage.setItem('iazela_digitalizacao_dispositivo','isolated-test-device')")
        calls, errors, native_dialogs = [], [], []

        def intercept(route):
            path = urlsplit(route.request.url).path
            calls.append(path)
            if path == "/api/quick-scan/device":
                route.fulfill(json={"name": "Dispositivo de teste", "folder": "Pasta de teste", "company": "Empresa fictícia", "store": "Loja fictícia"})
            elif path == "/api/quick-scan/session":
                assert route.request.post_data_json == {"pin": "482915"}
                assert route.request.headers.get("x-quick-scan-device") == "isolated-test-device"
                route.fulfill(json={"token": "isolated-session-token"})
            elif path == "/api/quick-scan/context":
                assert route.request.headers.get("authorization") == "Bearer isolated-session-token"
                route.fulfill(json={"today": "2026-10-20", "idle_seconds": 600, "limits": {"pages": 20}})
            elif path == "/api/quick-scan/session/end":
                route.fulfill(json={"ok": True})
            elif path == "/api/legal/public":
                route.fulfill(json={"published": False, "cookies": [], "optional_categories": []})
            elif path in ["/api/bootstrap", "/api/sessions", "/api/movements", "/api/dashboard"]:
                assert "/api/quick-scan/session/end" in calls, "Normal application data requested during PIN scanning"
                route.fulfill(status=401, json={"detail": "Sem sessão da aplicação no teste isolado"})
            else:
                raise AssertionError("Unexpected API in isolated scan test: " + path)

        context.route("**/api/**", intercept)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(error.stack or str(error)))
        page.on("dialog", lambda dialog: (native_dialogs.append(dialog.type), dialog.dismiss()))
        page.goto("http://127.0.0.1:5174/digitalizar")
        expect(page.get_by_role("heading", name="Introduza o PIN")).to_be_visible()
        page.get_by_label("PIN (6 a 12 algarismos)").fill("482915")
        page.get_by_role("button", name="Continuar", exact=True).click()
        expect(page.get_by_role("button", name="Galeria", exact=True)).to_be_visible()
        page.locator('input[type="file"][multiple]').set_input_files({"name": "teste.png", "mimeType": "image/png", "buffer": buffer.getvalue()})
        expect(page.get_by_role("heading", name="Rever o recorte", exact=False)).to_be_visible()
        page.get_by_role("button", name="Terminar", exact=True).click()
        confirmation = page.get_by_role("alertdialog")
        expect(confirmation).to_be_visible()
        confirmation.get_by_role("button", name="Cancelar", exact=True).click()
        expect(confirmation).not_to_be_visible()
        expect(page.get_by_role("heading", name="Rever o recorte", exact=False)).to_be_visible()
        assert "/api/quick-scan/session/end" not in calls
        page.get_by_role("button", name="Terminar", exact=True).click()
        confirmation.get_by_role("button", name="Terminar sem guardar", exact=True).click()
        expect(page.get_by_role("heading", name="Entrar na IAZELA")).to_be_visible()
        assert calls.count("/api/quick-scan/session/end") == 1
        assert not errors, errors
        assert not native_dialogs, native_dialogs
        assert not any("upload" in path or "/scans" in path for path in calls)
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        print("PASS: PIN, gallery, crop, shared discard confirmation, preserved local page on cancel, session exit; no real API/upload")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
