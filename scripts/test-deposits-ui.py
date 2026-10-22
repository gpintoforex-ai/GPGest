"""Deposit workflow and responsive UI using labelled fixtures; no real writes."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import expect, sync_playwright

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0])
boot["accounts"] = [{"id": "demo-account", "store_id": "demo", "name": "Conta de demonstração", "bank": "Banco Demo", "iban_last4": "1234"}]
deposits = []
operations = []
base_intercept = intercept


def deposit_intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path.endswith("/deposit-balance"):
        route.fulfill(content_type="application/json", body=json.dumps({"available": True, "cash_amount": "10", "session_id": "demo-session", "movements": [{"source": "opening", "id": "demo-session", "document": "Abertura Demo", "date": "2026-10-13", "amount": "10"}], "cheques": [{"number": "DEMO-1", "bank": "Banco Demo", "amount": "20", "cheque_date": "2026-10-13", "issuer": "Cliente Demo", "source_receipt": "wintouch:demo-cheque", "notes": "Recebimento de demonstração"}], "message": "Saldo de demonstração"}))
        return
    if not path.startswith("/deposits"):
        return base_intercept(route)
    if path == "/deposits/cheque-receipts":
        result = []
    elif path == "/deposits" and route.request.method == "POST":
        body = route.request.post_data_json
        cash = float(body["cash_amount"])
        cheque = sum(float(c["amount"]) for c in body["cheques"])
        result = {**body, "id": "demo-deposit", "number": "DEP-DEMONSTRACAO", "store_id": "demo", "store_name": "Loja Demo", "status": "rascunho",
                  "cash_amount": str(cash), "cheque_amount": str(cheque), "total": str(cash+cheque), "matched": "0", "remaining": str(cash+cheque), "difference": str(-cash-cheque),
                  "details": {"cash_mode": body["cash_mode"], "counts": body["counts"]}, "type": "misto", "register_name": "Caixa Demo", "bank": "Banco Demo", "account_name": "Conta Demo",
                  "responsible": "Administrador Demo", "proof_status": "em_falta", "attachments": [], "credits": [], "history": [], "movement_id": None, "reversal_id": None,
                  "cheques": [{**c, "id": "demo-cheque", "active": True, "returned_at": None} for c in body["cheques"]]}
        deposits.append(result)
    elif path == "/deposits":
        result = deposits
    elif path.endswith("/candidates"):
        result = [{"id": "demo-credit", "date": "2026-10-13", "value_date": "2026-10-13", "description": "Crédito de demonstração", "amount": "30", "available": "30", "reasons": ["Montante igual"]}]
    elif route.request.method == "POST":
        action = path.rsplit("/", 1)[-1]
        operations.append(action)
        result = deposits[0]
        result["status"] = {"register": "entregue", "prepare": "preparado", "exit": "em_transito", "deliver": "entregue", "reconcile": "reconciliado"}[action]
        if action in ("exit", "register"):
            result["movement_id"] = "demo-movement"
        if action == "reconcile":
            result.update(matched="30", remaining="0", difference="0")
    else:
        result = deposits[0]
    route.fulfill(content_type="application/json", body=json.dumps(result))


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", deposit_intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Depósitos", exact=True).click()
    page.get_by_role("button", name="Registar depósito", exact=True).click()
    dialog = page.get_by_role("dialog", name="Depósito bancário")
    dialog.get_by_label("Caixa de origem", exact=True).select_option("demo-register")
    expect(dialog.get_by_label("Valor em numerário (€)", exact=True)).to_have_value("10")
    dialog.get_by_label("Conta bancária de destino", exact=True).select_option("demo-account")
    expect(dialog.get_by_label("Valor em numerário (€)", exact=True)).to_be_editable()
    dialog.get_by_label("Valor em numerário (€)", exact=True).fill("8")
    expect(dialog.locator(".deposit-total strong")).to_contain_text("28,00")
    dialog.get_by_label("Valor em numerário (€)", exact=True).fill("10")
    expect(dialog.get_by_role("button", name="Adicionar cheque", exact=True)).to_have_count(0)
    dialog.locator(".deposit-source-history summary").click()  # histórico fechado por omissão
    expect(dialog.get_by_text("Abertura Demo", exact=True)).to_be_visible()
    expect(dialog.locator(".deposit-total strong")).to_contain_text("30,00")
    # Um só passo: sai da caixa e entra na conta; a reconciliação faz-se depois, em Bancos.
    expect(dialog.get_by_label("Data do depósito no banco", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="Registar depósito", exact=True).click()
    expect(dialog.locator(".status-badge").first).to_have_text("Por reconciliar")
    expect(dialog.get_by_role("status").filter(has_text="Depósito registado")).to_be_visible()
    expect(dialog.locator(".deposit-next")).to_contain_text("Bancos › Reconciliação")
    for name in ["Preparar", "Confirmar saída", "Registar entrega ao banco", "Reconciliar"]:
        expect(dialog.get_by_role("button", name=name, exact=True)).to_have_count(0)
    dialog.get_by_role("button", name="Anexos", exact=True).click()
    expect(dialog.get_by_text(re.compile("Comprovativo bancário em falta"))).to_be_visible()
    expect(dialog.get_by_role("link", name="Talão interno / imprimir", exact=True)).to_have_attribute("href", "/api/deposits/demo-deposit/pdf")
    for width in [768, 390]:
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.querySelector('dialog').scrollWidth <= document.querySelector('dialog').clientWidth")
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    assert operations == ["register"], operations
    assert not errors, errors
    browser.close()
print("Deposits UI passed: mixed deposit from real movements, editable cash amount, source references, one-step bank registration, reconciliation guidance, proof pending, tablet/mobile, keyboard. Fixtures only.")
