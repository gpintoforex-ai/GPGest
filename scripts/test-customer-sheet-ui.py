"""Ficha de cliente — separadores Vendas (artigos comprados) e Documentos: indicadores, pesquisa, família, ordenação,
painel com histórico (artigo → documento → voltar), documentos com anulados e notas de crédito, estado de pagamento só
com ligação financeira, original em PDF, ficha do artigo e regresso à mesma ficha e separador; separadores existentes
intactos; temas e contraste. Dados fictícios; nenhuma escrita. Requer o Vite em 127.0.0.1:5174.
"""
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-customer-last-sale-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-customer-last-sale-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
OUT = ROOT / ".local/customer-sheet-ui"
OUT.mkdir(parents=True, exist_ok=True)

DETAILS = {"id": "local:a", "values": {"tax_number": "500000010", "name": "Cliente A", "email": "", "phone": "", "active": True}, "labels": {}, "active": True,
           "dependencies": [], "origin": "local", "erp_id": "", "erp_code": "", "sync_state": "apenas_local", "sync_label": "Apenas local", "last_sync_at": None,
           "last_error": "", "managed_fields": [], "remove_blocked": {"app": "", "both": ""}, "related_entity_id": "a"}


def document(identifier, number, typ, label, state, state_label, gross, status="normal", direction=1, known=True, date="2026-10-10"):
    net = round(gross / 1.23)
    return {"id": identifier, "origin": "saft", "origin_label": "SAF-T", "store_id": "s1", "date": date, "type": typ, "type_label": label, "series": number.rsplit("/", 1)[0],
            "number": number.rsplit("/", 1)[1], "document": number, "key": f"saft:{typ}:{number}", "customer_key": "c:C1", "customer_code": "C1", "customer_name": "Cliente A",
            "customer_nif": "500000010", "status": status, "status_label": "Normal" if status == "normal" else "Anulado", "direction": direction, "net_cents": net,
            "tax_cents": gross - net, "gross_cents": gross, "received_cents": gross if state == "pago" else 0, "pending_cents": 0 if state != "pendente" else gross,
            "due_date": None, "state": state, "state_label": state_label, "line_count": 2, "last_receipt_date": None, "payment_known": known}


DOCS = [document("d1", "FT A/1", "FT", "Fatura", "pago", "Pago", 3075),
        document("d2", "FT A/2", "FT", "Fatura", "pendente", "Pendente", 2091, known=False, date="2026-09-02"),
        document("d3", "NC A/1", "NC", "Nota de crédito", "nota_credito", "Nota de crédito", 1230, direction=-1, known=False, date="2026-09-20"),
        document("d4", "FT A/3", "FT", "Fatura", "anulado", "Anulado", 1230, status="anulado", known=False, date="2026-08-01")]
ARTICLES = [{"key": "P1", "code": "P1", "description": "Artigo fictício", "family": "Família A", "service": False, "no_code": False, "unit": "UN", "sold": "3",
             "returned": "1", "quantity": "2", "net_cents": 2200, "gross_cents": 2706, "documents": 3, "first_date": "2026-09-02", "last_date": "2026-10-10",
             "last_unit_price": "12.00", "average_price_cents": 1100},
            {"key": "sem-codigo:portes", "code": "", "description": "Portes de envio", "family": "", "service": True, "no_code": True, "unit": "", "sold": "2",
             "returned": "0", "quantity": "2", "net_cents": 1000, "gross_cents": 1230, "documents": 2, "first_date": "2026-09-02", "last_date": "2026-10-10",
             "last_unit_price": "5", "average_price_cents": 500}]
SHEET = {"mode": "saft", "start": "2025-11-13", "end": "2026-11-12", "identity": {"codes": ["C1"], "nif": True, "erp_id": False}, "available": True,
         "kpis": {"net_cents": 3200, "gross_cents": 3936, "articles": 2, "units": "4", "top_article": {"code": "P1", "description": "Artigo fictício", "quantity": "3"},
                  "last_date": "2026-10-10", "cancelled": 1, "documents": 3},
         "articles": ARTICLES, "documents": DOCS, "families": ["Família A"],
         "notes": ["Vendas e documentos ligados à ficha pelos identificadores do SAF-T/ERP ou pelo NIF; nunca pelo nome.", "Notas de crédito subtraem."]}
writes, requests = [], []


def detail(params):
    if params["view"] == "customer_article":
        return {"view": "customer_article", "record_id": params["record_id"], "key": "P1", "code": "P1", "description": "Artigo fictício", "family": "Família A",
                "brand": "", "article_id": "art-1", "stock": "7", "unit": "UN", "service": False, "no_code": False, "summary": ARTICLES[0], "price_range": ["10.00", "12.00"],
                "lines": [{"document_id": "d1", "document": "FT A/1", "type_label": "Fatura", "date": "2026-10-10", "direction": 1, "quantity": "2", "unit": "UN",
                           "unit_price": "12.00", "discount_cents": 0, "net_cents": 2400, "gross_cents": 2952}]}
    doc = next(d for d in DOCS if d["id"] == params["id"])
    return {"view": "document", **doc, "customer": {"code": "C1", "name": "Cliente A", "nif": "500000010", "address": ""}, "entity_id": "a", "payment_terms": "",
            "discount_cents": 0, "lines": [{"index": 1, "code": "P1", "description": "Artigo fictício", "quantity": "2", "unit": "UN", "unit_price": "12.00", "discount_cents": 0,
                                            "vat_rate": "23", "net_cents": 2400, "vat_cents": 552, "gross_cents": 2952, "article_id": "art-1"}],
            "lines_totals": {"net_cents": 2400, "vat_cents": 552}, "lines_match": True, "receipts": [], "conference": [], "received_source": "saft",
            "originals": ["arq-1"] if doc["id"] == "d1" else [], "original_note": None if doc["id"] == "d1" else "Sem PDF original no Arquivo digital para este documento."}


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path, route.request.method
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    if method != "GET":
        writes.append(path)
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
    if unquote(path).startswith("/api/tables/customers/records/local:a/details"):
        return route.fulfill(json=DETAILS)
    if path == "/api/sales/customer-sheet":
        requests.append(params)
        return route.fulfill(json=SHEET)
    if path == "/api/sales/detail":
        requests.append({"detail": True, **params})
        return route.fulfill(json=detail(params))
    if path.startswith("/api/archive/documents/") and path.endswith("/file"):
        return route.fulfill(body=b"%PDF-1.4\n%fictitious\n", content_type="application/pdf")
    if path.startswith("/api/stock/"):
        return route.fulfill(status=503, json={"detail": "Catálogo indisponível no teste"})
    if path in ("/api/customer-orders", "/api/customer-orders/history", "/api/archive/documents"):
        return route.fulfill(json={"orders": []} if path == "/api/customer-orders" else [] if path.endswith("history") else {"documents": []})
    return ns["intercept"](route)


def audit(page):
    page.mouse.move(1, 1)
    page.wait_for_timeout(400)
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 1024)):
        writes.clear()
        requests.clear()
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Clientes")
        page.get_by_role("button", name="Editar Cliente A").click()
        for tab in ("Geral", "Contactos", "Condições Comerciais", "Vendas", "Encomendas", "Conta Corrente", "Documentos", "Histórico"):
            expect(page.get_by_role("tab", name=tab, exact=True)).to_be_visible()  # separadores existentes intactos
        expect(page.get_by_text("Sem IVA · últimos 12 meses")).to_be_visible()  # indicador de faturação já preenchido
        # Vendas: artigos comprados.
        page.get_by_role("tab", name="Vendas", exact=True).click()
        kpis = page.get_by_role("group", name="Indicadores de vendas do cliente")
        for text in ("Artigos distintos", "Unidades vendidas", "Valor de vendas", "Artigo mais comprado", "Última compra", "10/10/2026"):
            expect(kpis).to_contain_text(text)
        table = page.get_by_role("table", name="Artigos comprados pelo cliente")
        expect(table.locator("tbody tr")).to_have_count(2)
        expect(table).to_contain_text("3 faturadas · 1 devolvidas")
        expect(table).to_contain_text("Sem código")
        page.get_by_label("Pesquisar artigo por código ou descrição").fill("portes")
        expect(table.locator("tbody tr")).to_have_count(1)
        page.get_by_label("Pesquisar artigo por código ou descrição").fill("")
        page.get_by_label("Família de artigos").select_option("Família A")
        expect(table.locator("tbody tr")).to_have_count(1)
        page.get_by_label("Família de artigos").select_option("")
        table.get_by_role("button", name="Sem IVA").click()  # ordenar por valor (ascendente após o 1.º clique: estava descendente)
        expect(table.locator("tbody tr").first).to_contain_text("Portes de envio")
        page.get_by_role("combobox", name="Período das vendas").first.select_option("tudo")
        page.wait_for_timeout(200)
        assert requests[-1]["start"] < "2017-01-01", requests[-1]
        # Artigo → histórico ao cliente → documento → voltar.
        table.get_by_role("button", name="Artigo fictício").click()
        panel = page.get_by_role("dialog", name="Consulta comercial")
        expect(panel).to_contain_text("Histórico de vendas a este cliente")
        expect(panel).to_contain_text("12,00")
        assert [r for r in requests if r.get("detail")][-1]["view"] == "customer_article"
        panel.get_by_role("button", name="FT A/1").click()
        expect(panel.get_by_role("heading", name="Fatura FT A/1")).to_be_visible()
        opened = [r for r in requests if r.get("detail")][-1]
        assert (opened["view"], opened["start"], opened["end"]) == ("document", "2026-10-10", "2026-10-10"), opened
        panel.get_by_role("button", name="Voltar").click()
        expect(panel).to_contain_text("Histórico de vendas a este cliente")
        audit(page)
        page.screenshot(path=str(OUT / f"vendas-{theme}-{width}.png"), full_page=True)
        page.keyboard.press("Escape")
        # Documentos.
        page.get_by_role("tab", name="Documentos", exact=True).click()
        docs = page.get_by_role("table", name="Documentos do cliente")
        expect(docs.locator("tbody tr")).to_have_count(4)
        expect(docs.locator("tbody tr", has_text="FT A/3")).to_contain_text("Anulado")
        expect(docs.locator("tbody tr", has_text="NC A/1")).to_contain_text("Nota de crédito")
        expect(docs.locator("tbody tr", has_text="FT A/2")).to_contain_text("Sem ligação financeira")
        expect(page.get_by_role("status").filter(has_text="anulado(s) fora dos totais")).to_be_visible()
        page.get_by_label("Pesquisar documento por número, tipo, data ou valor").fill("30,75")
        expect(docs.locator("tbody tr")).to_have_count(1)
        page.get_by_label("Pesquisar documento por número, tipo, data ou valor").fill("")
        docs.get_by_role("button", name="Abrir documento FT A/1").click()
        panel = page.get_by_role("dialog", name="Consulta comercial")
        panel.get_by_role("tab", name="Documento original").click()
        expect(panel.locator("iframe[title='Documento original FT A/1']")).to_be_visible()
        expect(panel.get_by_role("link", name="Descarregar")).to_have_attribute("download", "FT_A_1.pdf")
        audit(page)
        page.screenshot(path=str(OUT / f"documentos-{theme}-{width}.png"), full_page=True)
        # Ficha do artigo e regresso à mesma ficha de cliente, no mesmo separador.
        panel.get_by_role("tab", name="Artigos (1)").click()
        panel.get_by_role("button", name="Ficha do artigo").click()
        page.wait_for_function("location.search.includes('consulta.tipo=article')")
        # Na página de Artigos, fecha-se a ficha do artigo (a aplicação não muda de página com um diálogo aberto) e volta-se.
        for _ in range(3):
            if not page.locator("dialog[open]").count():
                break
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        page.go_back()
        expect(page.get_by_role("tab", name="Documentos", exact=True)).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("table", name="Documentos do cliente")).to_be_visible()
        assert not writes, writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: ficha de cliente — Vendas (indicadores, pesquisa, família, ordenação, período, artigo → documento → voltar) e Documentos (anulados, NC, "
      "pagamento só com ligação financeira, original em PDF), ficha do artigo e regresso à mesma ficha; separadores intactos; temas e contraste; sem escritas.")
print(json.dumps({"requests": len(requests)}))
