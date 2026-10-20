"""SAF-T import UX with fully intercepted fictitious APIs, without real imports."""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

boot = {"mode": "demonstracao", "company": {"id": "saft-ui", "name": "Empresa fictícia"},
        "integration": {"mode": "local", "provider": "none", "active": False, "writes": False},
        "user": {"id": "ui", "name": "Utilizador fictício", "role": "administrador"},
        "stores": [{"id": "store-a", "name": "Loja A"}], "registers": [], "accounts": [],
        "categories": [], "users": [], "payment_methods": {}}
history, writes, errors = [], [], []
fail_confirm = True
mapping = {"mapping": {}, "codes": ["NU", "CD"], "methods": [{"id": "numerario", "name": "Numerário"}, {"id": "cartao", "name": "Multibanco / TPA"}]}
document = {"kind": "invoice", "document": "FT TESTE/1", "type": "FT", "date": "2026-01-10", "status": "N", "gross_cents": 1230, "net_cents": 1000, "tax_cents": 230, "direction": 1,
            "customer_id": "C1", "line_count": 1, "references": [], "payments": [], "raw": {"Line": {"LineNumber": "1", "ProductCode": "P1", "ProductDescription": "Serviço fictício", "Quantity": "1", "UnitPrice": "10.00", "CreditAmount": "10.00", "Tax": {"TaxCode": "NOR", "TaxPercentage": "23"}}}}
items = [{"id": "conflict-1", "kind": "invoice", "identity": "invoice:fictitious", "fingerprint": "a" * 64, "decision": "conflict", "outcome": "pending", "data": document, "previous": {**document, "gross_cents": 2460}}]


def mock_api(route):
    global fail_confirm, mapping
    path, method = urlsplit(route.request.url).path, route.request.method
    if method == "POST":
        writes.append(path)
        if path == "/api/saft/imports":
            assert b"teste.xml" in route.request.post_data_buffer
            if not history:
                history.append({"id": "import-ui", "store_id": "store-a", "user_id": "ui", "filename": "teste.xml", "fingerprint": "f" * 64, "revision": 1, "status": "ready", "created_at": "2026-10-17T10:00:00Z", "header": {"CompanyName": "Empresa fictícia", "TaxRegistrationNumber": "500000001", "AuditFileVersion": "1.04_01", "CurrencyCode": "EUR", "StartDate": "2026-01-01", "EndDate": "2026-01-31", "ProductID": "TESTE/Zela"}, "summary": {"counts": {"new": 0, "existing": 0, "conflict": 1, "invoice": 1, "customer": 1, "product": 1, "without_payments": 1}, "totals": {"gross_cents": 1230, "net_cents": 1000, "tax_cents": 230}, "types": {"invoice:FT": 1}, "warnings": ["Faturação não equivale a recebimentos."]}, "error": ""})
            result = history[0]
        elif path.endswith("/confirm"):
            body = route.request.post_data_json
            assert body["confirmed"] and body["conflicts"] == {"conflict-1": "replace"}
            if fail_confirm:
                fail_confirm = False
                route.fulfill(status=503, json={"detail": "Serviço temporariamente indisponível. As decisões foram mantidas."})
                return
            history[0].update(status="completed")
            history[0]["summary"]["result"] = {"imported": 0, "updated": 1, "ignored": 0, "rejected": 0}
            result = history[0]
        elif path == "/api/saft/payment-mapping":
            mapping["mapping"] = route.request.post_data_json["mapping"]
            result = mapping
        else:
            raise AssertionError(f"Unexpected write: {path}")
    elif path == "/api/bootstrap":
        result = boot
    elif path == "/api/dashboard":
        result = {}
    elif path == "/api/saft/imports":
        result = {"versions": ["1.04_01"], "max_bytes": 100000000, "imports": history}
    elif path.endswith("/items"):
        result = {"total": 1, "page": 1, "items": items}
    elif path.endswith("/items/conflict-1"):
        result = items[0]
    elif path == "/api/saft/imports/import-ui":
        result = history[0]
    elif path == "/api/saft/payment-mapping":
        result = mapping
    elif path == "/api/saft/analysis":
        result = {"limitations": "Referência do estabelecimento. Não altera o realizado nem os movimentos.", "warnings": [], "billing_total_cents": 1230, "receipts_total_cents": 0,
                  "billing": [{"date": "2026-01-10", "store_id": "store-a", "type": "FT", "documents": 1, "gross_cents": 1230, "net_cents": 1000, "tax_cents": 230}], "receipts": [], "payments": [], "unavailable": [{"id": "f", "document": "FT TESTE/1", "reason": "Informação de recebimento indisponível"}]}
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
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Importar SAF-T de vendas", exact=True).click()
    expect(page.get_by_role("heading", name="Importar SAF-T de vendas", exact=True, level=1)).to_be_visible()
    page.get_by_label("Ficheiro XML SAF-T (PT)", exact=True).set_input_files({"name": "teste.xml", "mimeType": "application/xml", "buffer": b"<fictitious/>"})
    page.get_by_role("button", name="Validar e pré-visualizar", exact=True).click()
    dialog = page.get_by_role("dialog", name="Importação SAF-T de vendas", exact=True)
    expect(dialog.get_by_text("NIF 500000001", exact=False)).to_be_visible()
    confirm = dialog.get_by_role("button", name="Confirmar importação", exact=True)
    expect(confirm).to_be_disabled()
    dialog.get_by_role("button", name="Consultar", exact=True).click()
    detail = page.get_by_role("dialog", name="Detalhe do registo SAF-T", exact=True)
    expect(detail.get_by_text("Serviço fictício", exact=True).first).to_be_visible()
    detail.get_by_role("button", name="Fechar", exact=True).first.click()
    expect(detail).not_to_be_visible()
    decision = dialog.get_by_label("Decisão FT TESTE/1", exact=True)
    decision.select_option("replace")
    dialog.get_by_role("checkbox", name="Confirmo a empresa", exact=False).check()
    confirm.click()
    expect(dialog.get_by_role("alert")).to_contain_text("Os dados preenchidos foram mantidos")
    expect(decision).to_have_value("replace")
    output = Path(".local/saft-ui")
    output.mkdir(parents=True, exist_ok=True)
    dialog.evaluate("element => element.scrollTop = 0")
    page.screenshot(path=str(output / "preview-desktop.png"), full_page=True)
    confirm.click()
    expect(dialog.get_by_text("Importação concluída:", exact=False)).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    dialog.evaluate("element => element.scrollTop = 0")
    expect(dialog.get_by_role("button", name="Fechar", exact=True).first).to_be_visible()
    box = dialog.bounding_box()
    assert box and box["x"] >= 0 and box["width"] <= 390
    page.screenshot(path=str(output / "mobile.png"), full_page=True)
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.get_by_text("Associar códigos SAF-T aos meios de pagamento", exact=True).click()
    page.get_by_label("Código SAF-T NU", exact=True).select_option("numerario")
    page.get_by_role("button", name="Guardar correspondências", exact=True).click()
    expect(page.get_by_text("Correspondências guardadas.", exact=False)).to_be_visible()
    page.get_by_role("button", name="Consultar período", exact=True).click()
    expect(page.get_by_role("heading", name="Recebimentos identificados", exact=False)).to_be_visible()
    expect(page.get_by_text("Informação de recebimento indisponível, ou sem pagamentos identificáveis neste período.", exact=True)).to_be_visible()
    page.screenshot(path=str(output / "analysis.png"), full_page=True)
    assert not errors, errors
    assert all(path.startswith("/api/saft/") for path in writes)
    browser.close()
print("SAF-T UI: upload, preview, conflicts, failure preservation, confirmation, detail, history, mapping, separate analysis, Escape and mobile passed. All APIs intercepted.")
