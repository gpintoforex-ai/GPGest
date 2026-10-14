"""Isolated visual-template checks with explicitly labelled demonstration fixtures.

No real API or database is accessed. Requires the existing Vite server on 5174.
"""

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from reportlab.pdfgen import canvas

root = Path(__file__).resolve().parents[1]
output = root / ".local" / "visual-template"
output.mkdir(parents=True, exist_ok=True)

documents = []
for index in range(23):
    documents.append({
        "id": str(index), "store_id": "demo", "supplier_name": f"Fornecedor de demonstração {index:02}",
        "supplier_nif": "500000000", "document": f"FT DEMO/{index + 1}",
        "document_type": "FT", "document_date": f"2026-10-{index + 1:02}",
        "amount_cents": (index + 1) * 12300, "base_cents": 10000, "tax_cents": 2300,
        "origin": "efatura", "import_id": "demo-import", "availability": "por_verificar",
        "attachment_status": "sem_anexo", "attachments": [], "pending_days": 0,
        "erp_status": "sem_correspondencia", "erp_candidates": [], "erp_link_id": None,
        "treatment": "por_tratar", "accounting": "por_preparar", "draft": None,
        "remaining_cents": (index + 1) * 12300, "paid_cents": 0,
        "payment_status": "por_pagar", "bank_status": "pendente", "payments": [],
        "history": [], "notes": "Documento fictício usado apenas na verificação visual.",
        "location": "", "verified_at": None, "verified_by_name": None,
    })
documents[0]["erp_status"] = "divergencias"
documents[0]["erp_candidates"] = [{
    "id": "erp-demo", "document": "FT DEMO/1", "internal_document": "COMPRA DEMO/1",
    "document_date": "2026-10-01", "amount_cents": 12000, "confidence": 75,
    "reasons": ["Referência de demonstração"], "differences": ["Total"],
    "synced_at": "2026-10-12", "cancelled": False,
}]
documents[1]["erp_status"] = "confirmada"
documents[2]["draft"] = {"status": "preparado", "payload": {}}
pdf_buffer = BytesIO()
pdf_canvas = canvas.Canvas(pdf_buffer)
pdf_canvas.drawString(50, 780, "GPGest - DOCUMENTO FICTICIO DE DEMONSTRACAO VISUAL")
pdf_canvas.save()
documents[0]["attachments"] = [{"id": "demo-pdf", "name": "DEMONSTRACAO.pdf"}]
documents[0]["attachment_status"] = "com_anexo"
boot = {
    "user": {"id": "demo-user", "name": "Administrador Demo", "role": "administrador"},
    "mode": "demonstracao", "company": {"name": "Empresa de demonstração visual", "nif": "500000000"},
    "stores": [{"id": "demo", "name": "Loja de demonstração"}],
    "registers": [{"id": "demo-register", "store_id": "demo", "name": "Caixa de demonstração", "active": True, "last_opening_cents": 1500}], "accounts": [], "categories": [], "users": [],
}
fail_documents = False
delivery_closed = False


def intercept(route):
    global delivery_closed
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/registers/demo-register/opening-suggestion":
        candidate = {"id": "fund-demo", "amount_cents": 4000, "document": "ENTRADA DEMO/1"} if "2026-10-12" in route.request.url else None
        route.fulfill(content_type="application/json", body=json.dumps({"candidate": candidate, "ambiguous": False, "last_opening_cents": 1500}))
        return
    if path.startswith("/accounting/delivery-periods"):
        if path.endswith("/close"):
            delivery_closed = True
        if path.endswith("/reopen"):
            delivery_closed = False
        if path.endswith("/export"):
            route.fulfill(content_type="application/zip", body=b"PK\x05\x06" + bytes(18))
            return
        route.fulfill(content_type="application/json", body=json.dumps({"vat_period": "mensal", "periods": [{"vat_period": "mensal", "year": 2026, "number": 9, "start": "2026-09-01", "end": "2026-09-30", "documents": 2, "without_attachment": 1, "calendar_ended": True, "closed_at": "2026-10-12T12:00:00Z" if delivery_closed else None, "reason": "Demonstração"}]}))
        return
    if path == "/documents/attachments/demo-pdf/view":
        route.fulfill(content_type="application/pdf", body=pdf_buffer.getvalue())
        return
    if path == "/documents" and fail_documents:
        route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": "Falha de ligação de demonstração"}))
        return
    result = {
        "/bootstrap": boot, "/sessions": [], "/movements": [], "/dashboard": {}, "/audit": [],
        "/documents": documents, "/documents/erp-catalog": [], "/document-payments": [],
        "/reconciliation": {"banks": [], "internals": []},
    }.get(path, {})
    if path.startswith("/documents/") and path.endswith("/detail"):
        result = next(doc for doc in documents if doc["id"] == path.split("/")[2])
    route.fulfill(content_type="application/json", body=json.dumps(result))


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="pt-PT")
    page.clock.install(time=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Contabilidade", exact=True).click()
    page.get_by_role("button", name="Conferência documental", exact=True).click()
    expect(page.get_by_label("Mês", exact=True)).to_have_value("10")
    expect(page.get_by_label("Ano", exact=True)).to_have_value("2026")
    expect(page.get_by_label("Desde", exact=True)).to_have_value("2026-10-01")
    expect(page.get_by_label("Até", exact=True)).to_have_value("2026-10-31")
    page.get_by_label("Mês", exact=True).select_option("11")
    expect(page.get_by_label("Até", exact=True)).to_have_value("2026-11-30")
    expect(page.locator("tbody tr")).to_have_count(0)
    page.get_by_label("Ano", exact=True).select_option("2024")
    page.get_by_label("Mês", exact=True).select_option("02")
    expect(page.get_by_label("Até", exact=True)).to_have_value("2024-02-29")
    page.get_by_label("Desde", exact=True).fill("2024-02-10")
    expect(page.get_by_label("Mês", exact=True)).to_have_value("")
    page.get_by_role("button", name="Mês atual", exact=True).click()
    expect(page.get_by_label("Desde", exact=True)).to_have_value("2026-10-01")
    expect(page.locator(".metric-card")).to_have_count(6)
    expect(page.locator(".metric-card").filter(has_text="Documentos importados").locator("strong")).to_have_text("23")
    expect(page.locator(".breadcrumb")).to_contain_text("Contabilidade")
    expect(page.locator("tbody tr")).to_have_count(20)
    page.get_by_role("button", name="Total", exact=False).first.click()
    expect(page.locator("tbody tr").first).to_contain_text("FT DEMO/1")
    page.get_by_role("button", name="Seguinte", exact=True).click()
    expect(page.locator("tbody tr")).to_have_count(3)
    page.get_by_role("button", name="Anterior", exact=True).click()
    page.get_by_label("Fornecedor ou documento", exact=True).fill("FT DEMO/23")
    expect(page.locator("tbody tr")).to_have_count(1)
    page.get_by_label("Fornecedor ou documento", exact=True).fill("")
    page.get_by_label("Fornecedor ou documento", exact=True).fill("FT DEMO/10")
    expect(page.locator("tbody tr").first.locator(".numeric")).to_have_text("1.230,00 €")
    page.get_by_label("Fornecedor ou documento", exact=True).fill("")
    page.get_by_role("button", name="Recolher menu lateral", exact=True).click()
    assert page.locator(".sidebar").bounding_box()["width"] == 82
    page.get_by_role("button", name="Expandir menu lateral", exact=True).click()
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(output / "desktop.png"))
    page.get_by_role("button", name="Conferir", exact=True).first.click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("heading", name="Dados da fatura", exact=True)).to_be_visible()
    expect(dialog.get_by_role("heading", name="Correspondência com o ERP", exact=True)).to_be_visible()
    expect(dialog.get_by_text("Divergências: Total", exact=True)).to_be_visible()
    expect(dialog.get_by_role("heading", name="Pré-visualização do original", exact=True)).to_be_visible()
    expect(dialog.locator("iframe")).to_be_visible()
    page.screenshot(path=str(output / "detail.png"))
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    page.locator("tbody tr").first.focus()
    page.keyboard.press("Enter")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("button", name="Fechar", exact=True)).to_be_enabled()
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    for width, label in [(820, "tablet"), (390, "mobile")]:
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), label
        page.get_by_role("button", name="Abrir menu", exact=True).click()
        expect(page.get_by_role("button", name="Contabilidade", exact=True)).to_be_visible()
        page.get_by_role("button", name="Fechar menu", exact=True).click()
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(output / f"{label}.png"))
        page.get_by_role("button", name="Conferir", exact=True).first.click()
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("button", name="Fechar", exact=True)).to_be_enabled()
        assert page.evaluate("document.querySelector('dialog').scrollWidth <= document.querySelector('dialog').clientWidth"), label + " detail"
        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
    page.get_by_label("Fornecedor ou documento", exact=True).fill("inexistente")
    expect(page.get_by_role("heading", name="Sem documentos para estes filtros")).to_be_visible()
    page.get_by_label("Fornecedor ou documento", exact=True).fill("")
    fail_documents = True
    page.reload()
    page.get_by_role("button", name="Abrir menu", exact=True).click()
    page.get_by_role("button", name="Contabilidade", exact=True).click()
    page.get_by_role("button", name="Conferência documental", exact=True).click()
    expect(page.get_by_role("button", name="Tentar novamente", exact=True)).to_be_visible()
    fail_documents = False
    page.get_by_role("button", name="Tentar novamente", exact=True).click()
    expect(page.locator("tbody tr")).to_have_count(20)
    page.get_by_label("Menu do utilizador", exact=True).click()
    expect(page.locator(".user-menu-panel").get_by_role("button", name="Terminar sessão")).to_be_visible()
    page.get_by_label("Menu do utilizador", exact=True).click()
    page.get_by_role("button", name="Abrir menu", exact=True).click()
    page.get_by_role("button", name="Bancos", exact=True).click()
    expect(page.locator("#finance-submenu").get_by_role("button", name="Reconciliação automática", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Múltiplas contas", exact=True).click()
    expect(page.locator(".breadcrumb")).to_contain_text("Bancos")
    expect(page.get_by_role("heading", name="Contas bancárias", exact=True)).to_be_visible()
    page.get_by_role("button", name="Consultar extratos", exact=True).click()
    expect(page.locator(".header-context h2")).to_have_text("Extratos")
    expect(page.get_by_text("Crie uma conta bancária para importar o extrato.", exact=True)).to_be_visible()
    page.get_by_role("button", name="Gerir contas", exact=True).click()
    expect(page.locator(".header-context h2")).to_have_text("Múltiplas contas")
    page.get_by_role("button", name="Abrir menu", exact=True).click()
    page.get_by_role("button", name="Entrega à contabilidade", exact=True).click()
    expect(page.get_by_role("button", name="Exportar dossiê", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Abrir menu", exact=True).click()
    page.get_by_role("button", name="Tesouraria", exact=True).click()
    page.get_by_role("button", name="Folha de caixa", exact=True).click()
    page.get_by_role("button", name="Nova folha de caixa", exact=True).click()
    opening_dialog = page.get_by_role("dialog")
    expect(opening_dialog.get_by_label("Fundo de maneio inicial (€)", exact=True)).to_have_value("40,00")
    expect(opening_dialog.locator('input[name="opening_wintouch_id"]')).to_have_value("fund-demo")
    opening_dialog.get_by_label("Dia da folha de caixa", exact=True).fill("2026-10-13")
    expect(opening_dialog.get_by_label("Fundo de maneio inicial (€)", exact=True)).to_have_value("15,00")
    expect(opening_dialog.locator('input[name="opening_wintouch_id"]')).to_have_count(0)
    opening_dialog.get_by_label("Dia da folha de caixa", exact=True).fill("2026-10-12")
    expect(opening_dialog.get_by_label("Fundo de maneio inicial (€)", exact=True)).to_have_value("40,00")
    opening_dialog.get_by_role("button", name="Fechar", exact=True).click()
    page.get_by_role("button", name="Abrir menu", exact=True).click()
    page.get_by_role("button", name="Entrega à contabilidade", exact=True).click()
    page.get_by_role("button", name="Encerrar período", exact=True).click()
    closure = page.get_by_role("dialog", name="Confirmar estado do período")
    closure.get_by_label("Justificação", exact=True).fill("Conferência de demonstração concluída")
    closure.get_by_role("checkbox").check()
    closure.get_by_role("button", name="Confirmar fecho", exact=True).click()
    expect(closure).not_to_be_visible()
    with page.expect_download() as download:
        page.get_by_role("button", name="Exportar dossiê", exact=True).click()
    assert download.value.suggested_filename == "dossie-mensal-2026-09.zip"
    page.get_by_role("button", name="Reabrir", exact=True).click()
    closure.get_by_label("Justificação", exact=True).fill("Reabertura de demonstração")
    closure.get_by_role("checkbox").check()
    closure.get_by_role("button", name="Confirmar reabertura", exact=True).click()
    expect(closure).not_to_be_visible()
    expect(page.get_by_role("button", name="Exportar dossiê", exact=True)).to_have_count(0)
    assert not errors, errors
    browser.close()
print("Visual template passed: metrics, sorting, search, pagination, menu, detail, keyboard dismissal, empty/error/retry, desktop/tablet/mobile. Fixtures only; no real data modified.")
