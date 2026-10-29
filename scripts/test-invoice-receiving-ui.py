"""Receção a partir de fatura: lista do arquivo, leitura, correspondência, artigo novo e confirmação. Só fixtures."""

import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
shared = {"__file__": str(fixtures)}
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], shared)
base_intercept = shared["intercept"]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app.table_specs  # noqa: E402,F401
from app.table_framework import TABLES  # noqa: E402

SUPPLIERS = {
    "key": "suppliers", "title": "Fornecedores", "singular": "fornecedor", "feminine": False, "description": "",
    "fields": [f.describe() for f in TABLES["suppliers"].fields], "extra_columns": [], "code_field": None,
    "description_field": "name", "has_active": True, "allow_create": True, "allow_remove": True, "remove_reason": "",
    "permissions": {k: True for k in ("view", "create", "edit", "status", "remove", "import", "export", "erp_remove")},
    "erp": {"integrated": False, "available": False, "reason": "", "operations": {}}, "local_only_reason": "",
}
lookups = []
# PDF mínimo fictício (o original real nunca entra nos testes).
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
OUT = Path(__file__).resolve().parents[1] / ".local" / "invoice-receiving-ui"
OUT.mkdir(parents=True, exist_ok=True)

ARTICLES = [
    {"id": "a1", "code": "1801", "name": "ARTHRON 500", "active": True, "origin": "Local"},
    {"id": "a2", "code": "1847", "name": "ARTHRON 250mL", "active": True, "origin": "Local"},
    {"id": "a3", "code": "1478", "name": "OMEGA 3 ULTRA 60CAPS", "active": True, "origin": "Local"},
]
DOCS = [
    {"id": "d1", "issue_date": "2026-10-20", "document": "FT FA.2026/3208", "supplier_name": "Fornecedor Natural",
     "supplier_nif": "507475500", "supplier_id": "s1", "total_cents": 9954, "file": "fatura.pdf", "receipt_id": None},
    {"id": "d3", "issue_date": "2026-10-21", "document": "FT 9/1", "supplier_name": "Fornecedor Novo", "supplier_nif": "503916099",
     "supplier_id": None, "total_cents": 2000, "file": "novo.pdf", "receipt_id": None},
    {"id": "d4", "issue_date": "2026-09-24", "document": "FR 2026/0049", "supplier_name": "Seguros Exemplo", "supplier_nif": "507475500",
     "supplier_id": "s1", "total_cents": 2842, "file": "sem-linhas.pdf", "receipt_id": None},
    {"id": "d2", "issue_date": "2026-10-10", "document": "FT 1/2", "supplier_name": "Outro", "supplier_nif": "500000000",
     "supplier_id": None, "total_cents": 1000, "file": "x.pdf", "receipt_id": "r9"},
]


def line(description, quantity, cost, selected=None, suggestions=(), **extra):
    return {
        "reference": extra.get("barcode", ""), "barcode": extra.get("barcode", ""), "lot": extra.get("lot", ""),
        "expiry": extra.get("expiry", ""), "description": description, "quantity": quantity,
        "price": cost, "discount": extra.get("discount", "0"), "vat": "23", "total": str(round(float(quantity) * float(cost), 2)),
        "unit_cost": cost, "global_discount": extra.get("global_discount", "0"), "bonus": extra.get("bonus", "0"), "confidence": "conferida", "service": extra.get("service", False),
        "evidence": description, "selected": selected, "suggestions": list(suggestions),
        "proposal": {"code": extra.get("barcode") or "NOVO", "name": description, "unit": "un", "precision": 0,
                     "vat_rate": "23", "supplier_reference": "", "barcodes": [extra.get("barcode")] if extra.get("barcode") else [],
                     "lots": bool(extra.get("lot")), "expiry": bool(extra.get("expiry"))},
    }


READ = {
    "document": {"id": "d1", "number": "FA.2026/3208", "document_type": "FT", "issue_date": "2026-10-20",
                 "supplier_nif": "507475500", "supplier_name": "Fornecedor Natural", "supplier_id": "s1",
                 "base_cents": 8093, "total_cents": 9954, "store_id": None, "receipt_id": None},
    "source": "ocr",
    "lines": [
        line("Arthron 500ml", "3", "29.08", global_discount="20", selected={"article_id": "a1", "code": "1801", "name": "ARTHRON 500", "method": "semelhante", "score": 0.92},
             suggestions=[{"article_id": "a2", "code": "1847", "name": "ARTHRON 250mL", "method": "semelhante", "score": 0.7}],
             barcode="5600493200380", lot="20113", expiry="2029-07-16"),
        line("Ómega 3 MaxPower 60caps", "1", "13.93", global_discount="20", bonus="1",
             suggestions=[{"article_id": "a3", "code": "1478", "name": "OMEGA 3 ULTRA 60CAPS", "method": "semelhante", "score": 0.74}],
             barcode="5600493200595", lot="A3384", expiry="2029-03-31"),
        line("Portes de envio", "1", "3.99", service=True),
    ],
    "warnings": [],
    "check": {"lines_total_cents": 8093, "document_base_cents": 8093, "matches": True},
    "suppliers": [
        {"id": "s1", "record_id": "local:s1", "name": "Fornecedor Natural", "tax_number": "507475500", "payment_term": {"name": "30 dias", "term_type": "apos", "days": 30},
         "payment_term_name": "30 dias", "category_name": "Mercadorias", "payment_method_name": "Transferência", "missing": []},
        {"id": "s2", "record_id": "wt:e2", "name": "Ervas do Monte, Lda", "tax_number": "509999990", "payment_term": {"name": "Fim do mês", "term_type": "fim_mes", "days": None},
         "payment_term_name": "Fim do mês", "category_name": "Mercadorias", "payment_method_name": "", "missing": ["Meio de pagamento"]},
    ],
    "warehouses": [{"id": "w1", "name": "Armazém principal", "store_id": "store-1"}],
}
TYPES = {"types": [
    {"id": "t-fc", "code": "FC", "description": "Fatura de Compra", "nature": "compra", "sign": 1, "payment_condition": "credito",
     "default_payment_method": None, "default_due_days": 30, "creates_cash_movement": False, "creates_debt": True},
    {"id": "t-fcd", "code": "FCD", "description": "Fatura de Compra a Dinheiro", "nature": "compra", "sign": 1, "payment_condition": "dinheiro",
     "default_payment_method": "numerario", "default_due_days": None, "creates_cash_movement": True, "creates_debt": False},
    {"id": "t-ncc", "code": "NCC", "description": "Nota de Crédito de Compra", "nature": "nota_credito_compra", "sign": -1, "payment_condition": "credito",
     "default_payment_method": None, "default_due_days": 30, "creates_cash_movement": False, "creates_debt": True},
], "methods": [{"id": "numerario", "name": "Numerário"}, {"id": "transferencia", "name": "Transferência"}],
   "open_invoices": [{"id": "pur-1", "document": "FT 1/9", "document_date": "2026-10-01", "amount_cents": 10000, "remaining_cents": 10000}]}
DOCS.append({**DOCS[0], "id": "d5", "document": "FT REPETIDO/1", "supplier_name": "Fornecedor de ofertas"})
supplier_updated = False
sent = []


def intercept(route):
    global supplier_updated
    path = unquote(urlsplit(route.request.url).path).removeprefix("/api")
    if path == "/procurement":
        return route.fulfill(json={"documents": [], "can_write": True, "integration_note": ""})
    if path == "/stock/catalog":
        return route.fulfill(json={"articles": ARTICLES, "warehouses": [{"id": "w1", "store_id": "store-1", "name": "Armazém principal"}], "suppliers": []})
    if path in ("/entities", "/purchases", "/documents/erp-catalog"):
        return route.fulfill(json=[])
    if path == "/procurement-ledger":
        return route.fulfill(json={"rows": [], "more": False})
    if path == "/purchase-document-types/options":
        return route.fulfill(json=TYPES)
    if path == "/purchase-receiving/documents":
        return route.fulfill(json={"rows": DOCS})
    if path.startswith("/archive/documents/") and path.endswith("/file"):
        return route.fulfill(body=PDF, content_type="application/pdf")
    if path == "/purchase-receiving/d5/read":
        return route.fulfill(json={**READ, "document": {**READ["document"], "id": "d5"},
            "lines": [
                line("Artigo oferecido", "2", "10", discount="100", selected={"article_id": "a1", "code": "1801", "name": "ARTHRON 500", "method": "codigo"}),
                line("Artigo pago", "3", "10", selected={"article_id": "a2", "code": "1847", "name": "ARTHRON 250mL", "method": "codigo"}),
            ]})
    if path == "/purchase-receiving/d5/confirm":
        sent.append(route.request.post_data_json)
        return route.fulfill(json={"receipt_id": "r5", "lines": 2, "created_articles": 0})
    if path == "/purchase-receiving/d1/read":
        return route.fulfill(json=READ)
    if path == "/purchase-receiving/d4/read":
        # FR (fatura-recibo): propõe a compra a dinheiro; o IVA da fatura tem mais 1 cêntimo (arredondamento).
        empty = {**READ["document"], "id": "d4", "number": "FR 2026/0049", "document_type": "FR", "base_cents": 2773,
                 "total_cents": 2940, "suggested_document_type_id": "t-fcd"}
        return route.fulfill(json={**READ, "document": empty, "lines": [], "source": "ocr",
                                   "warnings": ["Não foi possível ler linhas de artigos com segurança. Preencha-as a partir do original."],
                                   "check": {"lines_total_cents": 0, "document_base_cents": 2773, "matches": None}})
    if path == "/purchase-receiving/d4/confirm":
        sent.append(route.request.post_data_json)
        return route.fulfill(json={"receipt_id": "r4", "lines": 1, "created_articles": 1, "learned": 1})
    if path == "/purchase-receiving/d3/read":
        unknown = {**READ["document"], "supplier_id": None, "supplier_nif": "503916099", "supplier_name": "Fornecedor Novo"}
        return route.fulfill(json={**READ, "document": unknown})
    if path == "/purchase-receiving/suppliers":
        return route.fulfill(json=[{**row, "record_id": "local:" + row["id"],
            **({"name": "Fornecedor Natural atualizado", "payment_term": {"name": "45 dias", "term_type": "apos", "days": 45}} if row["id"] == "s1" and supplier_updated else {})}
            for row in READ["suppliers"]])
    if path == "/tables/suppliers/records/local:s1/details":
        return route.fulfill(json={"id": "local:s1", "values": {"name": "Fornecedor Natural", "tax_number": "507475500", "active": True},
            "labels": {}, "active": True, "dependencies": [], "managed_fields": []})
    if path == "/tables/suppliers/records/local:s1" and route.request.method == "POST":
        assert route.request.post_data_json["values"]["name"] == "Fornecedor Natural atualizado"
        supplier_updated = True
        return route.fulfill(json={"id": "local:s1"})
    if path == "/tables/suppliers":
        return route.fulfill(json=SUPPLIERS)
    if path == "/payment-terms":
        return route.fulfill(json=[])
    if path == "/entities/lookup":
        lookups.append(route.request.url)
        return route.fulfill(json={"is_valid": True, "name": "Fornecedor Novo, Lda", "address": "Rua A, 1", "locality": "Pombal", "postal_code": "3100-001"})
    if path == "/purchase-receiving/d1/confirm":
        sent.append(route.request.post_data_json)
        return route.fulfill(json={"receipt_id": "r1", "lines": 2, "created_articles": 1, "learned": 6})
    return base_intercept(route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
    page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
    dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
    expect(dialog.get_by_text("Fornecedor Natural")).to_be_visible()
    # A fatura já recebida não pode ser lida outra vez.
    rows = dialog.get_by_role("row")
    expect(rows.filter(has_text="Outro").get_by_role("button", name="Ler fatura")).to_be_disabled()
    rows.filter(has_text="Fornecedor Natural").get_by_role("button", name="Ler fatura").click()
    expect(dialog.get_by_role("list", name="Leitura da fatura")).to_contain_text("Lida por OCR")
    # Original da fatura ao lado da conferência; pode ocultar-se e voltar a mostrar.
    original = dialog.get_by_role("complementary", name="Original da fatura")
    expect(original.locator("iframe")).to_have_attribute("src", __import__("re").compile("^blob:"))
    dialog.get_by_role("button", name="Ocultar fatura").click()
    expect(original).to_have_count(0)
    dialog.get_by_role("button", name="Mostrar fatura").click()
    expect(original.locator("iframe")).to_be_visible()
    expect(dialog.get_by_text("Soma das linhas = base do documento", exact=False)).to_be_visible()
    # Linha relacionada (nome semelhante) com lote e validade lidos.
    arthron = dialog.get_by_label("Artigo de Arthron 500ml", exact=True)
    expect(arthron).to_have_value("a1")
    # Linha sem correspondência: artigo novo proposto, com aviso de semelhantes.
    omega = dialog.get_by_label("Artigo de Ómega 3 MaxPower 60caps", exact=True)
    expect(omega).to_have_value("__novo__")
    expect(dialog.get_by_text("1 artigo com nome semelhante", exact=False)).to_be_visible()
    # Portes: proposto sem entrada em stock.
    expect(dialog.get_by_label("Incluir Portes de envio", exact=True)).not_to_be_checked()
    # Descontos: o global (comum a todas as linhas) vai para o cabeçalho; custo = líquido ÷ unidades.
    expect(dialog.get_by_label("Desconto global (%)", exact=True)).to_have_value("20")
    expect(dialog.get_by_label("Valores de Arthron 500ml")).to_contain_text("69,792")
    expect(dialog.get_by_label("Valores de Ómega 3 MaxPower 60caps")).to_contain_text("2 un. em stock")
    # Resumo do IVA e total no fim: todas as linhas da fatura (portes incluídos, sem desconto global).
    summary = dialog.get_by_role("region", name="Resumo do documento")
    expect(summary.get_by_role("row", name="23 %")).to_contain_text("84,93")
    expect(summary).to_contain_text("104,46")
    expect(summary).to_contain_text("99,54")
    expect(summary).to_contain_text("Diferença de")
    # Comparação por cores, coluna a coluna, com texto: aqui base, IVA e total diferem da fatura.
    difference = summary.locator("tr.invoice-receiving-difference td")
    expect(difference).to_have_count(3)
    expect(difference.nth(2)).to_have_class(__import__("re").compile("is-different"))
    expect(difference.nth(2)).to_contain_text("diferente")
    expect(summary.locator(".invoice-receiving-result.is-different")).to_be_visible()
    # Diferença real (portes, linhas): não há acerto de arredondamento.
    expect(summary.get_by_role("button", name="Acertar ao total da fatura")).to_have_count(0)
    summary.scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "summary.png"))
    # Cabeçalho: prazo da ficha do fornecedor e vencimento (20/10/2026 + 30 dias).
    expect(dialog.get_by_label("Prazo de pagamento", exact=True)).to_have_value("30 dias")
    # Fornecedor: pesquisa a escrever (nome ou NIF), como na folha de caixa.
    picker = dialog.get_by_role("combobox", name="Fornecedor")
    expect(picker).to_have_value("Fornecedor Natural")
    picker.fill("ervas")
    expect(dialog.get_by_role("option", name="Ervas do Monte, Lda", exact=False)).to_be_visible()
    expect(dialog.get_by_role("option", name="Fornecedor Natural", exact=False)).to_have_count(0)
    dialog.get_by_role("option", name="Ervas do Monte, Lda", exact=False).click()
    expect(dialog.get_by_label("Prazo de pagamento", exact=True)).to_have_value("Fim do mês")
    # Ficha incompleta (sem meio de pagamento): aviso com «Editar fornecedor» e documento por lançar.
    expect(dialog.get_by_label("Meio de pagamento do fornecedor", exact=True)).to_have_value("Sem meio na ficha do fornecedor")
    expect(dialog.get_by_text("está incompleta: falta Meio de pagamento", exact=False)).to_be_visible()
    expect(dialog.get_by_role("button", name="Editar fornecedor")).to_have_count(2)
    page.screenshot(path=str(OUT / "supplier-incomplete.png"))
    picker.fill("507475")
    dialog.get_by_role("option", name="Fornecedor Natural", exact=False).click()
    expect(dialog.get_by_label("Rubrica", exact=True)).to_have_value("Mercadorias")
    expect(dialog.get_by_label("Meio de pagamento do fornecedor", exact=True)).to_have_value("Transferência")
    expect(dialog.get_by_role("button", name="Editar fornecedor")).to_have_count(1)


    # Editar a ficha sem sair da receção: preserva linhas e atualiza nome e prazo.
    dialog.get_by_role("button", name="Editar fornecedor", exact=True).click()
    supplier_editor = page.get_by_role("dialog", name="Editar fornecedor", exact=True)
    # Espera pela ficha carregada; escrever antes seria substituído pelos dados lidos.
    expect(supplier_editor.get_by_role("textbox", name="Nome", exact=False)).to_have_value("Fornecedor Natural")
    supplier_editor.get_by_role("textbox", name="Nome", exact=False).fill("Fornecedor Natural atualizado")
    supplier_editor.get_by_role("button", name="Guardar alterações", exact=True).click()
    expect(supplier_editor).to_have_count(0)
    expect(picker).to_have_value("Fornecedor Natural atualizado")
    expect(dialog.get_by_label("Prazo de pagamento", exact=True)).to_have_value("45 dias")
    expect(arthron).to_have_value("a1")
    # Linhas que precisam de artigo (criar/associar) a vermelho, com texto.
    expect(dialog.get_by_text("1 linha precisa de artigo", exact=False)).to_be_visible()
    omega_line = dialog.locator("fieldset.invoice-receiving-line.needs-article")
    expect(omega_line).to_have_count(1)
    expect(omega_line).to_contain_text("Artigo por criar")
    page.screenshot(path=str(OUT / "review.png"), full_page=True)
    # Procurar outro artigo bloqueia a confirmação até escolher.
    arthron.select_option("__outro__")
    expect(dialog.get_by_role("button", name="Confirmar receção (2)")).to_be_disabled()
    dialog.get_by_label("Procurar artigo", exact=True).fill("1847 · ARTHRON 250mL")
    expect(arthron).to_have_value("a2")
    arthron.select_option("a1")
    # Tipo de documento obrigatório: FC (crédito) propõe o vencimento pelo prazo do fornecedor (45 dias, após editar a ficha).
    expect(dialog.get_by_role("button", name="Confirmar receção (2)")).to_be_disabled()
    dialog.get_by_label("Tipo de documento", exact=True).select_option("t-fc")
    expect(dialog.get_by_label("Vencimento", exact=True)).to_have_value("2026-12-04")
    expect(dialog.get_by_text("dívida ao fornecedor e movimento previsto no vencimento", exact=False)).to_be_visible()
    # Uma data de vencimento ajustada à mão não é substituída ao guardar a entidade.
    dialog.get_by_label("Vencimento", exact=True).fill("2026-12-10")
    dialog.get_by_role("button", name="Editar fornecedor", exact=True).click()
    supplier_editor = page.get_by_role("dialog", name="Editar fornecedor", exact=True)
    # Espera pela ficha carregada; escrever antes seria substituído pelos dados lidos.
    expect(supplier_editor.get_by_role("textbox", name="Nome", exact=False)).to_have_value("Fornecedor Natural")
    supplier_editor.get_by_role("textbox", name="Nome", exact=False).fill("Fornecedor Natural atualizado")
    supplier_editor.get_by_role("button", name="Guardar alterações", exact=True).click()
    expect(supplier_editor).to_have_count(0)
    expect(dialog.get_by_label("Vencimento", exact=True)).to_have_value("2026-12-10")
    dialog.get_by_role("button", name="Confirmar receção (2)").click()
    expect(page.get_by_text("Receção registada: 2 linhas em stock, 1 artigos novos criados.")).to_be_visible()
    body = sent[0]
    assert body["supplier_id"] == "s1" and body["warehouse_id"] == "w1" and len(body["lines"]) == 2
    assert body["global_discount"] == "20" and body["lines"][0]["price"] == "29.08" and body["lines"][0]["discount"] == "0"
    assert body["document_type_id"] == "t-fc" and body["due_date"] == "2026-12-10" and body["payment_method"] is None
    assert body["lines"][1]["quantity"] == "1" and body["lines"][1]["bonus"] == "1"
    assert body["lines"][0]["article_id"] == "a1" and body["lines"][0]["lot"] == "20113" and body["lines"][0]["expiry"] == "2029-07-16"
    assert body["lines"][1]["new_article"]["code"] == "5600493200595" and body["lines"][1]["new_article"]["lots"] is True
    # NIF sem fornecedor: criar a partir da fatura, com NIF e nome já preenchidos (e consulta ao VIES).
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
    page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
    dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
    dialog.get_by_role("row").filter(has_text="Fornecedor Novo").get_by_role("button", name="Ler fatura").click()
    expect(dialog.get_by_text("O NIF 503916099 não corresponde a nenhum fornecedor.", exact=False)).to_be_visible()
    expect(dialog.get_by_role("button", name="Confirmar receção (2)")).to_be_disabled()
    dialog.get_by_role("button", name="Criar fornecedor", exact=True).click()
    supplier = page.get_by_role("dialog", name="Novo fornecedor")
    expect(supplier.get_by_label("NIF", exact=True)).to_have_value("503916099")
    expect(supplier.get_by_label("Nome", exact=True)).to_have_value("Fornecedor Novo")
    expect(supplier.get_by_text("Confirmado no VIES.")).to_be_visible()
    assert any("503916099" in url for url in lookups)
    page.screenshot(path=str(OUT / "new-supplier.png"))
    supplier.get_by_role("button", name="Cancelar").click()
    if page.get_by_role("alertdialog").count():
        page.get_by_role("button", name="Descartar alterações").click()
    expect(supplier).to_have_count(0)
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
    # Fatura sem linhas lidas: acrescentar a linha à mão e criar o artigo para lançar a fatura.
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
    page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
    dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
    dialog.get_by_role("row").filter(has_text="FR 2026/0049").get_by_role("button", name="Ler fatura").click()
    expect(dialog.get_by_text("Não há linhas lidas", exact=False)).to_be_visible()
    dialog.get_by_role("button", name="Adicionar linha").click()
    manual = dialog.locator("fieldset.invoice-receiving-line")
    expect(manual).to_have_count(1)
    expect(manual).to_contain_text("Artigo por criar")
    # Sem código do artigo novo, a confirmação explica o que falta (sem pedido ao servidor).
    manual.get_by_label("Descrição", exact=True).fill("Chá verde 100 g")
    manual.get_by_label("Preço unitário (€)", exact=True).fill("27.73")
    manual.get_by_label("IVA (%)").first.select_option("6")
    # Tipo proposto pelo documento (FR → compra a dinheiro), com o meio de pagamento por omissão.
    expect(dialog.get_by_label("Tipo de documento", exact=True)).to_have_value("t-fcd")
    expect(dialog.get_by_label("Meio de pagamento", exact=True)).to_have_value("numerario")
    # Acerto de arredondamento: 1 cêntimo de IVA, a base e o IVA ficam iguais aos da fatura.
    region = dialog.get_by_role("region", name="Resumo do documento")
    region.get_by_role("button", name="Acertar ao total da fatura").click()
    expect(region.locator("tr.invoice-receiving-adjustment")).to_contain_text("0,01")
    expect(region).to_contain_text("ficam iguais aos da fatura")
    page.screenshot(path=str(OUT / "rounding.png"))
    # Nota de crédito a crédito: pode abater faturas em aberto do fornecedor.
    dialog.get_by_label("Tipo de documento", exact=True).select_option("t-ncc")
    dialog.get_by_label("FT 1/9 · 01/10/2026 · em aberto 100,00 €", exact=False).check()
    dialog.get_by_role("button", name="Confirmar receção (1)").click()
    expect(dialog.get_by_text("Linha 1: indique o código e a designação do artigo novo.")).to_be_visible()
    expect(manual.get_by_label("Designação", exact=True)).to_have_value("Chá verde 100 g")
    manual.get_by_label("Código", exact=True).fill("CHA100")
    expect(dialog.get_by_role("region", name="Resumo do documento")).to_contain_text("29,40")
    dialog.get_by_role("button", name="Adicionar linha").click()
    dialog.get_by_role("button", name="Remover linha 2").click()
    expect(dialog.locator("fieldset.invoice-receiving-line")).to_have_count(1)
    dialog.get_by_role("button", name="Confirmar receção (1)").click()
    expect(page.get_by_text("Receção registada: 1 linhas em stock, 1 artigos novos criados.", exact=False)).to_be_visible()
    manual_body = sent[-1]
    assert manual_body["lines"][0]["new_article"]["code"] == "CHA100" and manual_body["lines"][0]["new_article"]["name"] == "Chá verde 100 g"
    assert manual_body["lines"][0]["price"] == "27.73" and manual_body["lines"][0]["vat"] == "6"
    assert manual_body["document_type_id"] == "t-ncc" and manual_body["credit_note_purchase_ids"] == ["pur-1"]
    assert manual_body["match_document_totals"] is True and body["match_document_totals"] is False
    # Duas linhas já associadas: procurar o mesmo artigo fora das sugestões,
    # preservando a oferta a 100 % separada da linha paga.
    page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
    dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
    dialog.get_by_role("row").filter(has_text="FT REPETIDO/1").get_by_role("button", name="Ler fatura").click()
    for description in ("Artigo oferecido", "Artigo pago"):
        article = dialog.get_by_label(f"Artigo de {description}", exact=True)
        article.select_option("__outro__")
        dialog.get_by_label("Procurar artigo", exact=True).fill("1478 · OMEGA 3 ULTRA 60CAPS")
        expect(article).to_have_value("a3")
        expect(article.locator("option:checked")).to_have_text("1478 · OMEGA 3 ULTRA 60CAPS")
    expect(dialog.get_by_label("Desconto (%)", exact=True).nth(0)).to_have_value("100")
    expect(dialog.get_by_label("Desconto (%)", exact=True).nth(1)).to_have_value("0")
    dialog.get_by_label("Tipo de documento", exact=True).select_option("t-fc")
    dialog.get_by_role("button", name="Confirmar receção (2)").click()
    expect(dialog).to_have_count(0)
    repeated = sent[-1]["lines"]
    assert [row["article_id"] for row in repeated] == ["a3", "a3"]
    assert [row["discount"] for row in repeated] == ["100", "0"]
    # Telemóvel: sem deslocação horizontal.
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
    expect(page.get_by_role("dialog", name="Receção a partir de fatura")).to_be_visible()
    expect(page.get_by_role("dialog", name="Receção a partir de fatura").get_by_role("button", name="Ler fatura").first).to_be_visible()
    page.get_by_role("dialog", name="Receção a partir de fatura").get_by_role("row").filter(has_text="Fornecedor Natural").get_by_role("button", name="Ler fatura").click()
    # No telemóvel o original começa oculto (botão «Mostrar fatura»).
    expect(page.get_by_role("button", name="Mostrar fatura")).to_be_visible()
    page.screenshot(path=str(OUT / "mobile.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    browser.close()
print("Invoice receiving UI passed: archive list, read result, matches, new article proposal, search other, confirm payload, mobile. Fixtures only.")
