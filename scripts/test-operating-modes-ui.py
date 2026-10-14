"""Mode assistant with isolated demo responses: no real ERP or company changes."""
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0])
base_intercept = intercept
boot["company"]["id"] = "demo-company"
settings = {"mode": "local", "provider": "wintouch", "api_active": False, "allow_writes": False, "api_url": "https://api.wintouchcloud.com/api/v1", "api_enterprise": "DEMO", "has_key": True, "resources": ["entities"], "authority": {}, "interval_minutes": 0}
capabilities = {"read_products": False, "read_entities": False, "write_settlements": False, "write_rubrics": False}
def policy():
    return {"mode": settings["mode"], "provider": "wintouch", "active": settings["mode"] == "integrated" and settings["api_active"], "writes": settings["allow_writes"], "capabilities": capabilities, "resources": settings["resources"]}
boot["integration"] = policy()
requests = []

def intercept_modes(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if not path.startswith("/company/integration"):
        if path.endswith("/sync-history"):
            route.fulfill(content_type="application/json", body="[]"); return
        return base_intercept(route)
    body = route.request.post_data_json or {}
    requests.append((path, body))
    if path == "/company/integration":
        value = {"settings": settings, "policy": policy(), "connectors": {"wintouch": {"name": "Wintouch", "operational": True, "limitations": []}, "eticadata": {"name": "Eticadata", "operational": False, "limitations": []}}, "capability_labels": {"read_entities": "Consultar entidades", "write_settlements": "Liquidar no ERP"}, "resource_labels": {"entities": "Entidades", "products": "Artigos"}, "records": [], "history": [], "pending": [], "mappings": [], "bindings": [], "balances": [], "entities": []}
    elif path.endswith("/preview"):
        value = {"token": "demo-token", "from": settings["mode"], "to": body["mode"], "counts": {"entities": 1, "products": 0, "financial_records": 0}, "effects": ["Os dados e anexos serão preservados.", "Nenhum envio automático."], "duplicates": [], "pending": [], "running": []}
    elif path.endswith("/apply"):
        assert body["preview_token"] == "demo-token" and body["confirmed"] is True
        settings.update({k: v for k, v in body.items() if k in settings})
        capabilities["read_entities"] = settings["mode"] == "integrated"
        boot["integration"] = policy()
        value = {"ok": True}
    else:
        value = {"success": False, "message": "Falha de demonstração; dados preservados."}
    route.fulfill(content_type="application/json", body=json.dumps(value))

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.route("**/api/**", intercept_modes)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Configurações", exact=True).click()
    expect(page.get_by_role("heading", name="Integrações", exact=True)).to_be_visible()
    expect(page.get_by_text("Modo local autónomo", exact=True).first).to_be_visible()
    expect(page.get_by_role("button", name="Testar ligação", exact=True)).to_have_count(0)
    page.get_by_label("Modo de funcionamento", exact=True).select_option("integrated")
    expect(page.get_by_role("option", name="Eticadata · Não implementado")).to_be_disabled()
    page.get_by_label("Ativar ligação", exact=True).check()
    page.get_by_label("Justificação da alteração", exact=True).fill("Verificação de demonstração")
    page.get_by_role("button", name="Pré-visualizar alteração", exact=True).click()
    dialog = page.get_by_role("dialog", name="Configurar integração")
    expect(dialog.get_by_role("button", name="Aplicar alteração", exact=True)).to_be_disabled()
    dialog.get_by_label("Confirmo os efeitos desta alteração para a empresa ativa.", exact=True).check()
    dialog.get_by_role("button", name="Aplicar alteração", exact=True).click()
    expect(page.get_by_text("Modo integrado com ERP", exact=True).first).to_be_visible()
    expect(page.get_by_text("Apenas leitura", exact=False).first).to_be_visible()
    expect(page.get_by_label("Artigos", exact=True).last).to_be_disabled()
    page.get_by_role("button", name="Testar ligação", exact=True).click()
    expect(page.get_by_text("Falha de demonstração; dados preservados.", exact=True)).to_be_visible()
    expect(page.get_by_text("Modo integrado com ERP", exact=True).first).to_be_visible()
    page.get_by_label("Modo de funcionamento", exact=True).select_option("local")
    page.get_by_label("Justificação da alteração", exact=True).fill("Regresso explícito ao modo local")
    page.get_by_role("button", name="Pré-visualizar alteração", exact=True).click()
    dialog.get_by_label("Confirmo os efeitos desta alteração para a empresa ativa.", exact=True).check()
    dialog.get_by_role("button", name="Aplicar alteração", exact=True).click()
    expect(page.get_by_text("Modo local autónomo", exact=True).first).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_role("button", name="Pré-visualizar alteração", exact=True)).to_be_visible()
    page.screenshot(path=str(output / "operating-modes-mobile.png"), full_page=True)
    assert len([r for r in requests if r[0].endswith("/apply")]) == 2
    browser.close()
print("PASS: local, integrado apenas leitura, confirmação explícita, falha sem mudança de modo e vista móvel")
