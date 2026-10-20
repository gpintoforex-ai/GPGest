"""Conferência faturação ↔ recebimentos com APIs fictícias intercetadas, sem escrita real."""
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
boot = {"mode": "demonstracao", "company": {"id": "conf-ui", "name": "Empresa fictícia", "nif": "500000001"},
        "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
        "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
        "stores": [{"id": "store-a", "name": "Loja A"}], "registers": [], "accounts": [],
        "categories": [], "users": [], "payment_methods": {}}
indicators = {
    "period": {"start": "2026-01-01", "end": "2026-01-31"},
    "billing": {"available": True, "net_cents": 81301, "gross_cents": 100000, "documents": 2, "credit_notes_cents": 0, "excluded_documents": 0, "basis": ""},
    "receipts": {"received_cents": 90000, "refunds_cents": 0, "net_cents": 90000, "basis": ""},
    "payments_cents": 12000,
    "balances": {"cash_open_sessions_cents": 5000, "bank_book_cents": 150000, "bank_accounts_without_opening": 0, "card_pending_settlement_cents": 90000, "card_pending_settlement_count": 1},
    "conference": {"justified_cents": 0, "aggregate_cents": 0, "pending_cents": 100000, "pending_documents": 2},
    "to_justify_cents": 10000,
    "to_justify_note": "Por justificar/conferir: pode resultar de vendas a crédito, recebimentos de períodos anteriores, adiantamentos, diferenças de datas ou dados em falta. Não é, por si só, dívida de clientes nem erro de caixa.",
}
pay = lambda amount: [{"index": 0, "code": "CC", "method": "cartao", "method_name": "Multibanco / TPA", "date": "2026-01-10", "amount_cents": amount}]  # noqa: E731
documents = [
    {"id": "d1", "store_id": "store-a", "document": "FT A/1", "type": "FT", "date": "2026-01-10", "customer_id": "C1", "status": "N", "status_label": "Normal", "eligible": True,
     "direction": 1, "gross_cents": 60000, "net_cents": 48780, "tax_cents": 11220, "saft_payments": pay(60000), "justified_cents": 0, "remaining_cents": 60000, "state": "por_conferir"},
    {"id": "d2", "store_id": "store-a", "document": "FT A/2", "type": "FT", "date": "2026-01-10", "customer_id": "C2", "status": "N", "status_label": "Normal", "eligible": True,
     "direction": 1, "gross_cents": 40000, "net_cents": 32521, "tax_cents": 7479, "saft_payments": [], "justified_cents": 0, "remaining_cents": 40000, "state": "por_conferir"},
]
receipts = [{"source": "movement", "id": "m1", "store_id": "store-a", "register_id": "r1", "register_name": "Caixa 1", "date": "2026-01-10", "payment": "cartao", "kind": "venda",
             "origin": "fecho_local", "direction": 1, "amount_cents": 90000, "document": "Fecho de caixa 2026-01-10", "reference": "", "entity": "", "notes": "", "aggregate": True,
             "linked_cents": 0, "remaining_cents": 90000}]
overview = {"mode": "local", "billing_available": True, "indicators": indicators, "documents": documents, "receipts": receipts,
            "daily": [{"date": "2026-01-10", "store_id": "store-a", "payment": "cartao", "payment_name": "Multibanco / TPA", "billing_day_cents": 100000,
                       "saft_payments_cents": 60000, "received_cents": 90000, "difference_cents": 30000, "documents": 1, "receipts": 1, "conference_id": None}],
            "suggestions": [{"kind": "individual", "documents": [{"record_id": "d1", "document": "FT A/1", "amount_cents": 60000}],
                             "receipts": [{"source": "movement", "id": "m1", "amount_cents": 60000}],
                             "reasons": ["Movimento agregado do fecho de caixa", "1 documentos com pagamento SAF-T na mesma data e meio", "Soma inferior ao movimento; o remanescente fica por associar"],
                             "confidence": "Média"}],
            "conferences": [], "rules": ["Faturação e recebimentos são medidas distintas e nunca são somadas.", "Associar documentos a recebimentos não cria movimentos."]}
writes, errors = [], []


def mock_api(route):
    path, method = urlsplit(route.request.url).path, route.request.method
    if method == "POST":
        writes.append((path, route.request.post_data_json))
        if path != "/api/conference/confirm":
            raise AssertionError(f"Unexpected write: {path}")
        result = {"id": "c1", "message": "Conferência registada. Nenhum movimento foi criado."}
    elif path == "/api/bootstrap":
        result = boot
    elif path == "/api/conference/indicators":
        result = indicators
    elif path == "/api/conference":
        result = overview
    elif path == "/api/drive/pending-imports":
        result = {"configured": False, "pending": {}}
    elif path in ("/api/dashboard",):
        result = {}
    else:
        result = []
    route.fulfill(json=result)


with sync_playwright() as playwright:
    expect.set_options(timeout=15000)
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", mock_api)
    page.goto("http://127.0.0.1:5174")
    # Painel: medidas lado a lado, nunca somadas.
    panel = page.get_by_label("Indicadores de faturação e tesouraria")
    expect(panel.get_by_text("Total faturado com IVA", exact=True)).to_be_visible()
    expect(panel.get_by_text("Recebimentos do período", exact=True)).to_be_visible()
    expect(panel.get_by_text("Por justificar/conferir: 100,00 €", exact=False)).to_be_visible()
    if shots:
        page.screenshot(path=str(shots / "conf-painel.png"), full_page=False)
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Faturação e recebimentos", exact=True).click()
    expect(page.get_by_role("heading", name="Faturação e recebimentos", level=1)).to_be_visible()
    expect(page.get_by_text("Por conferir").first).to_be_visible()
    page.get_by_role("tab", name="Sugestões (1)").click()
    expect(page.get_by_text("Movimento agregado do fecho de caixa").first).to_be_visible()
    page.get_by_role("button", name="Rever e associar").click()
    assert not writes, "Rever não pode escrever"
    expect(page.get_by_text("Valores iguais")).to_be_visible()
    if shots:
        page.screenshot(path=str(shots / "conf-selecao.png"), full_page=True)
    page.get_by_role("button", name="Confirmar associação", exact=True).click()
    dialog = page.get_by_role("alertdialog").or_(page.get_by_role("dialog")).first
    expect(dialog.get_by_text("Não cria movimentos")).to_be_visible()
    dialog.get_by_role("button", name="Confirmar associação").click()
    expect(page.get_by_text("Nenhum movimento foi criado").first).to_be_visible()
    path, body = writes[0]
    assert body["kind"] == "individual" and body["confirmed"] is True
    assert body["documents"] == [{"record_id": "d1", "amount_cents": 60000}]
    assert body["receipts"] == [{"source": "movement", "id": "m1", "amount_cents": 60000}]
    page.get_by_role("tab", name="Totais diários").click()
    expect(page.get_by_role("button", name="Confirmar totais")).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("tab", name="Documentos").click()
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"Deslocamento horizontal na página: {overflow}px"
    if shots:
        page.screenshot(path=str(shots / "conf-telemovel.png"), full_page=False)
    browser.close()
assert not errors, errors
print("Conferência UI: OK")
