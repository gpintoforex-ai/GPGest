"""Liquidity UI uses explicitly identified demo fixtures, never live financial data."""
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0])
base_intercept = intercept
manuals, scenarios = [], []
fail = False
erp_active = True
local_payments = []
boot["categories"] = [{"id": "demo-category", "name": "Rubrica Demo"}]
permissions = {"consult": True, "manual": True, "adjust": True, "recurrence": True, "scenario": True, "share": True, "reserve": True}
accounts = [{"key": "bank:demo-bank", "name": "Banco Demo · Conta demonstrativa", "store_id": "demo", "currency": "EUR", "balance_cents": 10000, "source": "Extrato de demonstração", "kind": "contabilistico", "updated_at": "2026-10-13T12:00:00Z", "balance_date": "2026-10-13", "stale": False, "missing": False}]
event = {"key": "wintouch:demo", "store_id": "demo", "title": "Pagamento de demonstração", "amount_cents": 2000, "direction": -1, "currency": "EUR", "document_date": "2026-10-01", "due_date": "2026-10-13", "expected_date": "2026-10-13", "target": "bank:demo-bank", "confidence": "confirmado", "source": "wintouch", "source_url": "/api/liquidity/origin/wintouch:demo"}
pending = {**event, "key": "planned:overdue", "title": "Cliente de demonstração vencido", "direction": 1, "expected_date": None, "exclusion": "Vencida sem data prevista"}


def liquidity_intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if not path.startswith("/liquidity"):
        if path.endswith("/settlement-options"):
            route.fulfill(content_type="application/json", body=json.dumps({"types": [], "series": [], "methods": []}))
            return
        return base_intercept(route)
    body = route.request.post_data_json or {}
    if path == "/liquidity/setup":
        value = {"erp": {"active": erp_active, "provider": "wintouch"}, "settings": {"reserves": {"EUR": 9000}, "stale_days": 7}, "permissions": permissions, "manual": manuals, "scenarios": scenarios}
    elif path == "/liquidity/forecast":
        if fail:
            route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Falha de demonstração"}))
            return
        scenario = next((s for s in scenarios if s["id"] == body.get("scenario_id")), None)
        daily = [{"date": "2026-10-13", "opening_cents": 10000, "incoming_cents": 0, "outgoing_cents": 2000, "closing_cents": 8000, "movements": [event]}]
        group = {"daily": daily, "initial_cents": 10000, "incoming_cents": 0, "outgoing_cents": 2000, "final_cents": 8000, "minimum_cents": 8000, "minimum_date": "2026-10-13", "reserve_cents": 9000, "required_cents": 1000, "first_below": {"date": "2026-10-13", "shortfall_cents": 1000, "causes": [event["title"]]}}
        value = {"updated_at": "2026-10-13T12:00:00Z", "settings": {"reserves": {"EUR": 9000}, "stale_days": 7}, "accounts": accounts, "selected": [accounts[0]["key"]], "scenario": {"name": scenario["name"] if scenario else body.get("scenario_id", "base"), "assumptions": {"delay_days": body.get("delay_days", 0), "exclude_uncertain": False}}, "groups": {"EUR": group}, "events": [event, pending], "scheduled": [event], "pending": [pending], "warnings": ["Dados de demonstração identificados; sem informação financeira real."], "transit": [], "comparison": {"EUR": group} if body.get("compare") else {}}
    elif path == "/liquidity/manual":
        value = {"id": "manual-demo", "store_id": "demo", "active": True, "data": body}
        manuals.append(value)
    elif path == "/liquidity/scenarios":
        value = {"id": "scenario-demo", "name": body["name"], "shared": body.get("shared", False), "data": body}
        scenarios.append(value)
    elif path.startswith("/liquidity/settle/"):
        local_payments.append(body)
        value = {"id": "local-demo", "message": "Liquidação local registada. Não foi enviada ao ERP."}
    elif path.startswith("/liquidity/origin"):
        value = {"source": "Documento de demonstração", "attachments": [{"id": "demo-pdf", "name": "DEMONSTRACAO.pdf", "view_url": "/api/documents/attachments/demo-pdf/view"}], "data": {"document": "DEMO-1", "entity": "Fornecedor Demo", "due_date": "2026-10-13", "amount_cents": 2000, "remaining_cents": 2000, "status": "Pendente"}}
    elif path == "/liquidity/export":
        route.fulfill(content_type="text/csv", body="Cenário;Base\nData;Saldo\n13/10/2026;80,00", headers={"Content-Disposition": 'attachment; filename="previsao-liquidez.csv"'})
        return
    else:
        value = {"ok": True}
    route.fulfill(content_type="application/json", body=json.dumps(value))


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.clock.install(time=datetime(2026, 10, 13, 12, tzinfo=timezone.utc))
    page.route("**/api/**", liquidity_intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Previsão de liquidez", exact=True).click()
    expect(page.get_by_role("heading", name="Previsão de liquidez", exact=True, level=1)).to_be_visible()
    conditions = page.locator("details").filter(has=page.locator("summary", has_text="Condi\u00e7\u00f5es da simula\u00e7\u00e3o"))
    expect(conditions).not_to_have_attribute("open", "")
    conditions.locator(":scope > summary").click()
    expect(conditions).to_have_attribute("open", "")
    expect(page.get_by_role("img", name="Evolução do saldo previsto em EUR. A linha tracejada indica a reserva mínima.")).to_be_visible()
    expect(page.get_by_text("Vencida sem data prevista", exact=True)).to_be_visible()
    flows = page.get_by_role("region", name="Recebimentos e pagamentos previstos", exact=True)
    expect(flows.locator(".liquidity-selected-total strong").nth(1)).to_contain_text("20,00")
    flows.get_by_role("radio", name="A receber", exact=True).check()
    expect(flows.locator(".liquidity-selected-total strong").nth(1)).to_contain_text("0,00")
    expect(flows.get_by_text("Pagamento de demonstração", exact=True)).to_have_count(0)
    flows.get_by_role("radio", name="A pagar", exact=True).check()
    expect(flows.get_by_text("Pagamento de demonstração", exact=True)).to_be_visible()
    flows.get_by_role("radio", name="Todos", exact=True).check()
    flows.get_by_placeholder("Pesquisar entidade", exact=True).fill("inexistente")
    expect(flows.locator(".liquidity-selected-total strong").nth(2)).to_contain_text("0,00")
    expect(flows.get_by_text("Pagamento de demonstra\u00e7\u00e3o", exact=True)).to_have_count(0)
    expect(flows.get_by_text("Sem movimentos nesta lista.", exact=True)).to_be_visible()
    flows.get_by_placeholder("Pesquisar entidade", exact=True).fill("DEMONSTRACAO")
    expect(flows.get_by_text("Pagamento de demonstra\u00e7\u00e3o", exact=True)).to_be_visible()
    flows.get_by_placeholder("Pesquisar entidade", exact=True).fill("")
    page.get_by_role("button", name="Informação do documento: Pagamento de demonstração", exact=True).first.click()
    information = page.get_by_role("dialog", name="Previsão de liquidez")
    expect(information.get_by_role("heading", name="Informação do documento", exact=True)).to_be_visible()
    expect(information.get_by_text("DEMO-1", exact=True)).to_be_visible()
    expect(information.get_by_role("link", name="DEMONSTRACAO.pdf", exact=True)).to_have_attribute("href", "/api/documents/attachments/demo-pdf/view")
    information.get_by_role("button", name="Fechar", exact=True).click()
    expect(page.get_by_role("columnheader", name="Data esperada", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Ajustar conta: Pagamento de demonstração", exact=True).first.click()
    expect(page.get_by_role("dialog", name="Previsão de liquidez").get_by_label("Conta prevista", exact=True)).to_be_visible()
    page.get_by_role("dialog", name="Previsão de liquidez").get_by_role("button", name="Fechar", exact=True).click()
    expect(page.get_by_role("button", name="Ajustar previsão", exact=True)).to_have_count(0)
    expect(page.get_by_role("columnheader", name="Data", exact=True).first).to_be_visible()
    expect(page.get_by_text("01/10/2026", exact=True).first).to_be_visible()
    page.get_by_role("button", name="Liquidar no ERP", exact=True).first.click()
    settlement = page.get_by_role("dialog", name="Liquidar na Wintouch · DEMO-1")
    expect(settlement).to_be_visible()
    expect(settlement.get_by_text("Fornecedor Demo", exact=False)).to_be_visible()
    settlement.get_by_role("button", name="Cancelar", exact=True).click()
    expect(page.get_by_role("button", name="1 movimento(s)", exact=True)).to_be_hidden()
    page.locator(".liquidity-daily summary").click()
    page.get_by_role("button", name="1 movimento(s)", exact=True).click()
    dialog = page.get_by_role("dialog", name="Previsão de liquidez")
    expect(dialog.get_by_text("Pagamento de demonstração", exact=True)).to_be_visible()
    expect(dialog.get_by_role("button", name="Ver origem", exact=True)).to_have_count(0)
    dialog.get_by_role("button", name="Fechar", exact=True).click()
    page.get_by_role("button", name="Novo compromisso", exact=True).click()
    dialog.get_by_label("Descrição", exact=True).fill("Salário Demo")
    dialog.get_by_label("Valor (€ ou moeda selecionada)", exact=True).fill("20")
    dialog.get_by_label("Conta de destino ou pagamento", exact=True).select_option("bank:demo-bank")
    dialog.get_by_label("Justificação", exact=True).fill("Teste demonstrativo")
    dialog.get_by_role("button", name="Guardar compromisso", exact=True).click()
    expect(page.get_by_text("Compromisso guardado apenas na previsão.", exact=True)).to_be_visible()
    page.get_by_role("button", name="Simular valor: Pagamento de demonstração", exact=True).first.click()
    dialog.get_by_label("Data esperada", exact=True).fill("2026-10-20")
    dialog.get_by_role("button", name="Guardar na simulação", exact=True).click()
    dialog.get_by_label("Nome do cenário", exact=True).fill("Cenário Demo")
    dialog.get_by_label("Justificação", exact=True).fill("Simulação demonstrativa")
    dialog.get_by_role("button", name="Guardar cenário", exact=True).click()
    expect(page.get_by_label("Cenário", exact=True)).to_have_value("scenario-demo")
    expect(page.get_by_role("button", name="Atualizar", exact=True)).to_be_enabled()
    page.get_by_label("Comparar com o cenário Base", exact=True).check()
    expect(page.get_by_text("Comparação com Base:", exact=False)).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="CSV", exact=True).click()
    assert download.value.suggested_filename == "previsao-liquidez.csv"
    erp_active = False
    page.get_by_role("button", name="Atualizar", exact=True).click()
    page.get_by_role("button", name="Liquidar", exact=True).first.click()
    expect(dialog.get_by_role("heading", name="Liquidar localmente", exact=True)).to_be_visible()
    dialog.get_by_label("Valor a liquidar (€)", exact=True).fill("10")
    dialog.get_by_label("Conta ou caixa da liquidação", exact=True).select_option("bank:demo-bank")
    dialog.get_by_label("Justificação", exact=True).fill("Pagamento demonstrativo")
    dialog.get_by_label("Confirmo que este pagamento ou recebimento foi realizado.", exact=True).check()
    dialog.get_by_role("button", name="Confirmar liquidação local", exact=True).click()
    expect(page.get_by_text("Liquidação local registada. Não foi enviada ao ERP.", exact=True)).to_be_visible()
    assert len(local_payments) == 1 and local_payments[0]["amount_cents"] == 1000
    page.screenshot(path=str(output / "liquidez-desktop.png"), full_page=True, animations="disabled")
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_role("heading", name="Previsão de liquidez", exact=True, level=1)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(output / "liquidez-mobile.png"), full_page=True, animations="disabled")
    page.get_by_label("Horizonte", exact=True).focus()
    page.keyboard.press("Tab")
    expect(page.get_by_label("Previsão desde", exact=True)).to_be_focused()
    fail = True
    page.get_by_role("button", name="Atualizar", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Falha de demonstração")
    expect(page.get_by_role("img", name="Evolução do saldo previsto em EUR. A linha tracejada indica a reserva mínima.")).to_have_count(0)
    fail = False
    page.get_by_role("button", name="Atualizar", exact=True).click()
    expect(page.get_by_role("img", name="Evolução do saldo previsto em EUR. A linha tracejada indica a reserva mínima.")).to_be_visible()
    browser.close()
print("Liquidity UI passed: graph, day explanation, original document, manual commitment, isolated scenario, comparison, CSV, mobile, keyboard, error/retry. Demo fixtures only.")
