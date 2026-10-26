"""Attachment actions with simulated receipts and files; no real data or writes."""
from pathlib import Path
import ast
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
fixture = root / "scripts/test-procurement-ui.py"
ns = {"__file__": str(fixture)}
tree = ast.parse(fixture.read_text(encoding="utf-8"))
tree.body = tree.body[:next(i for i, node in enumerate(tree.body) if isinstance(node, ast.With))]
exec(compile(tree, str(fixture), "exec"), ns)
supplier = ns["supplier"]
base = {"kind": "receipt", "store_id": "demo", "supplier_name": supplier["name"], "supplier_nif": supplier["tax_number"],
        "status": "confirmed", "document_date": "2026-10-26", "lines": [], "attachments": [], "total_cents": 20000,
        "attachment_status": "missing", "check_status": "pending", "erp_status": "not_linked", "purchase_id": None}
ns["documents"].extend([
    {**base, "id": "receipt-archive", "document": "REC-ARQUIVO", "archive_document_id": "original-1"},
    {**base, "id": "receipt-many", "document": "REC-DOIS", "attachments": ["https://example.com/one.pdf", "https://example.com/two.pdf"]},
    {**base, "id": "receipt-none", "document": "REC-SEM"},
    {**base, "id": "receipt-purchase", "document": "REC-COMPRA", "purchase_id": "purchase-1", "payment_status": "unpaid"},
])

def mock(route):
    assert route.request.method == "GET", "Attachment consultation must not mutate documents"
    path = route.request.url.split("?", 1)[0]
    if path.endswith("/api/purchases"):
        route.fulfill(json=[{"id": "purchase-1", "attachments": [{"id": "drive:file-1", "name": "Fatura digitalizada", "view_url": "/api/scans/scan-1/pdf"}]}])
    elif path.endswith("/api/archive/documents/original-1/file"):
        route.fulfill(content_type="text/plain", body="Original invoice fixture")
    else:
        ns["intercept"](route)

with sync_playwright() as p:
    expect.set_options(timeout=30000)
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.context.route("**/api/**", mock)
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
    archive = page.get_by_role("row").filter(has_text="REC-ARQUIVO")
    expect(archive).to_contain_text("Com anexo")
    expect(archive.get_by_role("link", name="Consultar anexo", exact=True)).to_have_attribute("href", "/api/archive/documents/original-1/file")
    with page.expect_popup() as opened:
        archive.get_by_role("link", name="Consultar anexo", exact=True).click()
    popup = opened.value
    expect(popup.locator("body")).to_have_text("Original invoice fixture")
    popup.close()
    page.get_by_role("row").filter(has_text="REC-DOIS").get_by_role("button", name="Consultar anexo (2)", exact=True).click()
    dialog = page.get_by_role("dialog", name="Anexos do documento", exact=True)
    expect(dialog.get_by_role("link", name="Anexo 1", exact=True)).to_have_attribute("href", "https://example.com/one.pdf")
    expect(dialog.get_by_role("link", name="Anexo 2", exact=True)).to_have_attribute("href", "https://example.com/two.pdf")
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role("row").filter(has_text="REC-SEM").get_by_role("button", name="Consultar anexo", exact=True)).to_be_disabled()
    purchase = page.get_by_role("row").filter(has_text="REC-COMPRA")
    expect(purchase.get_by_role("link", name="Consultar anexo", exact=True)).to_have_attribute("href", "/api/scans/scan-1/pdf")
    output = root / ".local/procurement-ui"
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / "attachment-actions.png"), animations="disabled")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    browser.close()
print("PASS: original archive invoice, linked purchase, multiple attachments, missing attachment, mobile; no writes.")
