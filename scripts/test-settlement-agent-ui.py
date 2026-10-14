"""Settlement proposals and execution with mock demo data; no real payments."""
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

exec(Path(__file__).with_name("test-liquidity-ui.py").read_text(encoding="utf-8").split("\nwith sync_playwright() as playwright:")[0])
base_liquidity = liquidity_intercept
created = []
proposal_requests = []
bank_safe = False
lines = [{"key": "planned:demo-local", "title": "Fornecedor Demo · FT LOCAL", "entity": "Fornecedor Demo", "store_id": "demo", "currency": "EUR", "due_date": "2026-10-01", "direction": -1, "amount_cents": 3000, "proposed_cents": 1500, "route": "local", "blocked": "", "target": "bank:demo-bank"}, {"key": "wintouch:demo-erp", "title": "Fornecedor Demo · FT ERP", "entity": "Fornecedor Demo", "store_id": "demo", "currency": "EUR", "due_date": "2026-10-02", "direction": -1, "amount_cents": 3000, "proposed_cents": 1500, "route": "erp", "blocked": ""}]

def agent_intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    body = route.request.post_data_json or {}
    if path == "/liquidity/settlement-proposal":
        proposal_requests.append(body)
        value = {"entities": ["Fornecedor Demo"], "lines": lines, "total_cents": 3000, "unallocated_cents": 0, "rule": "Documentos mais antigos primeiro."}
    elif path == "/liquidity/settlement-sustainability":
        value = {"valid": bank_safe, "message": "Saldo sustent\u00e1vel" if bank_safe else "Saldo insuficiente", "balance_cents": 10000, "after_cents": 7000, "other_outgoing_cents": 0, "minimum_cents": 7000, "reserve_cents": 0, "shortfall_cents": 0, "balance_date": "2026-10-13", "end": "2026-11-11", "assumption": "Sem receitas futuras"}
    elif path.startswith("/liquidity/settle/"):
        created.append(("local", body)); value = {"message": "Pagamento local confirmado."}
    elif path.startswith("/liquidity/origin/wintouch%3A") or path == "/liquidity/origin/wintouch:demo-erp":
        value = {"data": {"document": "FT ERP", "entity": "Fornecedor Demo", "direction": -1, "amount_cents": 3000}}
    elif path.endswith("/settlement-options"):
        value = {"types": [{"id": "type-demo", "name": "Pagamento Demo"}], "series": [{"id": "serie-demo", "type_id": "type-demo", "name": "Série Demo"}], "methods": [{"id": "method-demo", "name": "Transferência"}]}
    elif path.startswith("/integrations/wintouch/sales/") and path.endswith("/settle"):
        created.append(("erp", body)); value = {"message": "Liquidação ERP confirmada."}
    else:
        return base_liquidity(route)
    route.fulfill(content_type="application/json", body=json.dumps(value))

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.route("**/api/**", agent_intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Previsão de liquidez", exact=True).click()
    page.get_by_role("button", name="Íris · Propor liquidação", exact=True).click()
    agent = page.get_by_role("dialog", name="Íris · Proposta de liquidação", exact=True)
    expect(agent).to_be_visible()
    agent.get_by_label("Banco para a liquida\u00e7\u00e3o", exact=True).select_option("bank:demo-bank")
    agent.get_by_label("Entidade da proposta", exact=True).select_option(label="Fornecedor Demo")
    agent.get_by_label("Valor total a liquidar (€), opcional", exact=True).fill("30")
    agent.get_by_role("button", name="Propor liquidação", exact=True).click()
    expect(agent.get_by_role("button", name="Criar pagamentos propostos", exact=True)).to_be_disabled()
    assert not created
    agent.get_by_role("button", name="Validar saldo do banco", exact=True).click()
    expect(agent.get_by_text("Saldo insuficiente", exact=True)).to_be_visible()
    expect(agent.get_by_role("button", name="Criar pagamentos propostos", exact=True)).to_be_disabled()
    bank_safe = True
    agent.get_by_role("button", name="Validar saldo do banco", exact=True).click()
    expect(agent.get_by_text("Saldo sustent\u00e1vel", exact=True)).to_be_visible()
    agent.get_by_label("Justificação dos pagamentos", exact=True).fill("Pagamentos realizados e proposta revista")
    agent.get_by_label("Revi os documentos e valores.", exact=False).check()
    agent.get_by_role("button", name="Criar pagamentos propostos", exact=True).click()
    erp = page.get_by_role("dialog", name="Liquidar na Wintouch · FT ERP", exact=True)
    expect(erp).to_be_visible()
    expect(erp.get_by_label("Valor a liquidar (€)", exact=True)).to_have_value("15.00")
    assert [r[0] for r in created] == ["local"] and created[0][1]["amount_cents"] == 1500
    erp.get_by_label("Confirmo este pagamento/recebimento", exact=False).check()
    erp.get_by_role("button", name="Confirmar liquidação na Wintouch", exact=True).click()
    expect(agent).to_be_visible()
    expect(agent.get_by_text("Confirmado", exact=True)).to_have_count(2)
    assert [r[0] for r in created] == ["local", "erp"]
    assert created[1][1]["amount"] == "15.00"
    assert proposal_requests[-1]["entity"] == "Fornecedor Demo" and proposal_requests[-1]["amount_cents"] == 3000
    page.set_viewport_size({"width": 390, "height": 844})
    expect(agent.get_by_role("heading", name="Íris · Proposta de liquidação")).to_be_visible()
    page.screenshot(path=str(output / "settlement-agent-mobile.png"), full_page=True)
    browser.close()
print("PASS: proposta por entidade e valor, confirmação local, confirmação ERP e ausência de duplicados")
