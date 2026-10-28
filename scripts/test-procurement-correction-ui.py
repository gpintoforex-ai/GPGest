"""Lista de receções: coluna «Conferência / anexo», edição de rascunhos e de receções confirmadas. Só fixtures."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-visual-template-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
inv = {"__file__": str(ROOT / "scripts/test-inventory-ui.py")}
exec(Path(inv["__file__"]).read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], inv)
catalog = inv["catalog"]
catalog["warehouses"][0]["store_id"] = "demo"
OUT = ROOT / ".local" / "procurement-correction-ui"
OUT.mkdir(parents=True, exist_ok=True)
supplier = {"id": "supplier", "name": "Fornecedor de teste", "tax_number": "500000000", "is_supplier": True, "active": True, "removed": False}
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")


def doc(identifier, kind, status, **extra):
    line = {"article_id": "p1", "description": inv["article"]["name"], "nature": "goods", "supplier_reference": "", "unit": "un",
            "quantity": "12", "rejected": "0", "price": "10", "discount": "0", "vat": "23", "warehouse_id": "w1", "lot_id": None,
            "lot_number": "", "expiry": None, "source_id": None, "source_line": 0, "category_id": None, "reason": "",
            "return_reason": "outro", "received": "0", "invoiced": extra.pop("invoiced", "0"), "returned": "0", "credited": "0",
            "pending": extra.pop("pending", "12")}
    return {"id": identifier, "kind": kind, "status": status, "store_id": "demo", "supplier_id": "supplier", "supplier_name": supplier["name"],
            "supplier_nif": supplier["tax_number"], "document": identifier.upper(), "document_type": "FT", "document_date": "2026-10-20",
            "due_date": "2026-10-20", "expected_date": None, "contact": "", "payment_terms": "", "notes": "", "attachments": [],
            "purchase_id": None, "erp_id": None, "category_id": None, "request_key": "k-" + identifier, "revision": 1,
            "confirmed_at": None if status == "draft" else "2026-10-20T10:00:00Z", "created_at": "2026-10-20T10:00:00Z",
            "total_cents": 14760, "email_history": [], "check_status": "pending", "erp_status": "not_linked", "payment_status": "unpaid",
            "lines": [line], **extra}


DOCUMENTS = [
    doc("rec-1", "receipt", "confirmed", archive_document_id="arq-1"),
    doc("rec-2", "receipt", "draft"),
    doc("rec-3", "receipt", "confirmed", pending="0", invoiced="12"),
    doc("rec-4", "receipt", "confirmed"),
]
EDIT_VIEW = {
    "document": {"id": "arq-1", "number": "REC-1", "document_type": "FT", "issue_date": "2026-10-20", "supplier_nif": "500000000",
                 "supplier_name": supplier["name"], "supplier_id": "supplier", "base_cents": 3088, "total_cents": 3798, "store_id": "demo",
                 "receipt_id": "rec-1"},
    "receipt": {"id": "rec-1", "revision": 1, "status": "confirmed", "editable": True, "warehouse_id": "w1", "global_discount": "0",
                "document_type_id": "t-fc", "payment_method": None, "due_date": "2026-11-19", "credit_note_purchase_ids": []},
    "source": "rececao",
    "lines": [{"reference": "PL1", "barcode": "", "lot": "", "expiry": "", "description": "Pastilhas", "quantity": "5", "bonus": "1",
               "price": "6.5", "discount": "5", "vat": "23", "global_discount": "0", "service": False, "evidence": "", "total": "0",
               "unit_cost": "0", "selected": {"article_id": "p1", "code": "P001", "name": inv["article"]["name"], "method": "lancado", "score": 1},
               "suggestions": [], "proposal": {"code": "PL1", "name": "Pastilhas", "unit": "un", "precision": 0, "vat_rate": "23",
                                               "supplier_reference": "PL1", "barcodes": [], "lots": False, "expiry": False}}],
    "warnings": [], "check": {"lines_total_cents": 0, "document_base_cents": 3088, "matches": None},
    "suppliers": [{"id": "supplier", "name": supplier["name"], "tax_number": "500000000", "payment_term": None}],
    "warehouses": [{"id": "w1", "name": "Principal", "store_id": "demo"}],
}
TYPES = {"types": [{"id": "t-fc", "code": "FC", "description": "Fatura de Compra", "nature": "compra", "sign": 1, "payment_condition": "credito",
                    "default_payment_method": None, "default_due_days": 30, "creates_cash_movement": False, "creates_debt": True}],
         "methods": [{"id": "numerario", "name": "Numerário"}], "open_invoices": []}
calls = []


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    method = route.request.method
    if path.startswith("/archive/documents/"):
        return route.fulfill(body=PDF, content_type="application/pdf")
    if path == "/purchase-document-types/options":
        return route.fulfill(json=TYPES)
    if path == "/purchase-receiving/receipts/rec-1" and method == "GET":
        result = EDIT_VIEW
    elif path == "/purchase-receiving/receipts/rec-1" and method == "PUT":
        calls.append(("fatura", route.request.url, route.request.post_data_json))
        result = {"receipt_id": "rec-1", "lines": 1, "created_articles": 0, "learned": 3}
    elif path == "/procurement/rec-4/document-type":
        calls.append(("tipo", route.request.url, route.request.post_data_json))
        result = {**DOCUMENTS[3], "revision": 2, "document_type_id": "t-fc", "purchase_id": "compra-4"}
    elif path == "/procurement/rec-4/confirmed":
        calls.append(("manual", route.request.url, route.request.post_data_json))
        result = {**DOCUMENTS[3], "revision": 3}
    elif path == "/procurement":
        result = {"documents": DOCUMENTS, "can_write": True, "mode": {"mode": "local"}, "integration_note": "Circuito local."}
    elif path == "/stock/catalog":
        result = catalog
    elif path == "/entities":
        result = [supplier]
    elif path in ("/purchases", "/documents/erp-catalog"):
        result = []
    elif path == "/procurement-ledger":
        result = {"rows": [], "more": False}
    else:
        return ns["intercept"](route)
    route.fulfill(content_type="application/json", body=json.dumps(result))


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
    rows = page.get_by_role("row")
    first = rows.filter(has_text="REC-1")
    # Coluna «Conferência / anexo»: estado por tipo e o anexo como ação na própria coluna.
    expect(first.get_by_text("Fatura por associar")).to_be_visible()
    expect(first.get_by_role("link", name="Consultar anexo")).to_have_attribute("href", "/api/archive/documents/arq-1/file")
    expect(rows.filter(has_text="REC-3").get_by_text("Faturada")).to_be_visible()
    expect(rows.filter(has_text="REC-2").get_by_text("Por confirmar")).to_be_visible()
    page.screenshot(path=str(OUT / "list.png"))
    # Já faturada: corrige-se com devolução, por isso não há «Editar».
    expect(rows.filter(has_text="REC-3").get_by_role("button", name="Editar")).to_have_count(0)
    # Rascunho: «Editar» abre o formulário de rascunho.
    rows.filter(has_text="REC-2").get_by_role("button", name="Editar").click()
    expect(page.get_by_role("dialog", name="Receção — rascunho")).to_be_visible()
    page.keyboard.press("Escape")
    if page.get_by_role("alertdialog").count():
        page.get_by_role("button", name="Descartar alterações").click()
    # Receção de fatura confirmada: reabre a mesma janela, com o lançamento gravado (bónus, desconto).
    first.get_by_role("button", name="Editar").click()
    edit = page.get_by_role("dialog", name="Editar receção a partir de fatura")
    expect(edit.get_by_label("Artigo de Pastilhas", exact=True)).to_have_value("p1")
    line = edit.locator("fieldset.invoice-receiving-line")
    expect(line.get_by_label("Quantidade", exact=True)).to_have_value("5")
    expect(line.get_by_label("Bónus", exact=True)).to_have_value("1")
    expect(line.get_by_label("Desconto (%)", exact=True)).to_have_value("5")
    expect(edit.get_by_role("button", name="Outra fatura")).to_have_count(0)
    line.get_by_label("Quantidade", exact=True).fill("8")
    page.screenshot(path=str(OUT / "edit-invoice.png"))
    edit.get_by_role("button", name="Guardar alterações (1)").click()
    expect(page.get_by_text("Receção atualizada: 1 linhas; os movimentos de stock foram gerados de novo.")).to_be_visible()
    kind, url, body = calls[-1]
    assert kind == "fatura" and url.endswith("revision=1") and body["lines"][0]["quantity"] == "8" and body["lines"][0]["bonus"] == "1"
    assert body["document_type_id"] == "t-fc" and body["due_date"] == "2026-11-19"
    # Receção confirmada sem tipo: «Definir tipo» gera só o efeito financeiro (só tipos de fatura).
    expect(rows.filter(has_text="REC-3").get_by_role("button", name="Definir tipo")).to_have_count(0)
    rows.filter(has_text="REC-4").get_by_role("button", name="Definir tipo").click()
    typing = page.get_by_role("dialog", name="Tipo de documento da receção")
    typing.get_by_label("Tipo de documento", exact=True).select_option("t-fc")
    expect(typing.get_by_text("O stock já registado não muda.", exact=False)).to_be_visible()
    page.screenshot(path=str(OUT / "assign-type.png"))
    typing.get_by_role("button", name="Gravar tipo de documento").click()
    expect(page.get_by_text("Tipo de documento registado", exact=False)).to_be_visible()
    kind, _, body = calls[-1]
    assert kind == "tipo" and body == {"revision": 1, "document_type_id": "t-fc", "payment_method": None, "due_date": "2026-11-19"}, body
    # Receção manual confirmada: o mesmo formulário do lançamento, em modo de edição.
    rows.filter(has_text="REC-4").get_by_role("button", name="Editar").click()
    manual = page.get_by_role("dialog", name="Receção — edição")
    expect(manual.get_by_label("Documento do fornecedor", exact=True)).to_have_value("REC-4")
    # Receção antiga sem tipo de documento: passa a ser obrigatório escolher.
    manual.get_by_label("Tipo de documento", exact=True).select_option("t-fc")
    manual.get_by_role("button", name="Guardar alterações").click()
    expect(page.get_by_text("Receção atualizada: os movimentos de stock foram gerados de novo.")).to_be_visible()
    kind, _, body = calls[-1]
    assert kind == "manual" and body["revision"] == 1 and body["document"] == "REC-4" and body["document_type_id"] == "t-fc"
    assert not errors, errors
    browser.close()
print("Procurement list UI passed: conference/attachment column, edit draft, assign a document type, edit confirmed receipts (invoice window and manual form). Fixtures only.")
