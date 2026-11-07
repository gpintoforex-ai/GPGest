"""Receção a partir de fatura ligada à encomenda: proposta, comparação, entrega superior e ligação das linhas.

Só APIs intercetadas (reaproveita o teste da receção a partir de fatura); nada é gravado.
Requer o Vite em 127.0.0.1:5174.
"""

from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "scripts/test-invoice-receiving-ui.py"
ns = {"__file__": str(source)}
exec(source.read_text(encoding="utf-8").split("\nwith sync_playwright() as p:")[0], ns)
READ, line = ns["READ"], ns["line"]
OUT = ROOT / ".local" / "invoice-order-link-ui"
OUT.mkdir(parents=True, exist_ok=True)

arthron = {"article_id": "a1", "code": "1801", "name": "ARTHRON 500", "method": "codigo", "score": 1}
ORDERS = [
    {"id": "ec-2", "document": "EC-0002", "document_date": "2026-10-30", "expected_date": None, "store_id": "store-1",
     "lines": [{"index": 0, "article_id": "a3", "description": "OMEGA 3 ULTRA 60CAPS", "unit": "un", "pending": "4", "price": "13.93"}]},
    {"id": "ec-1", "document": "EC-0001", "document_date": "2026-11-02", "expected_date": "2026-11-05", "store_id": "store-1",
     "lines": [
         {"index": 0, "article_id": "a1", "description": "ARTHRON 500", "unit": "un", "ordered": "5", "received": "2", "pending": "3", "price": "29.08"},
         {"index": 1, "article_id": "a1", "description": "ARTHRON 500 · oferta", "unit": "un", "ordered": "1", "received": "0", "pending": "1", "price": "0"},
         {"index": 2, "article_id": "a2", "description": "ARTHRON 250mL", "unit": "un", "ordered": "2", "received": "0", "pending": "2", "price": "5"},
     ]},
]
LINKED = {**READ, "orders": ORDERS, "lines": [
    line("Arthron 500ml", "3", "29.08", selected=arthron, bonus="1", lot="20113", expiry="2029-07-16"),
    READ["lines"][1],  # Ómega sem artigo associado: artigo novo, fora da encomenda.
]}
PROCESS = {"document_id": "ec-1", "cost_visible": True, "truncated": False,
           "steps": [{"key": "order", "label": "Encomenda", "state": "done", "detail": "1 confirmada(s)"},
                     {"key": "receipt", "label": "Receção", "state": "partial", "detail": "Receção parcial: há quantidades por receber"},
                     {"key": "invoice", "label": "Documento de compra", "state": "pending", "detail": "Sem fatura registada"}],
           "documents": [{"id": "ec-1", "kind": "order", "label": "Encomenda", "document": "EC-0001", "document_date": "2026-11-02",
                          "status": "partial", "confirmed_at": "2026-11-02T10:00:00Z", "current": True, "purchase_id": None,
                          "financial": [], "attachment_status": "missing", "total_cents": 15540,
                          "lines": [{"description": "ARTHRON 500", "quantity": "5", "received": "2", "invoiced": "0", "pending": "3"}]}]}
sent = []


def intercept(route):
    path = unquote(urlsplit(route.request.url).path).removeprefix("/api")
    if path == "/purchase-receiving/d1/read":
        return route.fulfill(json=LINKED)
    if path == "/purchase-receiving/d1/confirm":
        sent.append(route.request.post_data_json)
        return route.fulfill(json={"receipt_id": "r1", "lines": 2, "created_articles": 1, "learned": 2})
    if path == "/procurement/ec-1/process":
        return route.fulfill(json=PROCESS)
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
    page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
    dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
    dialog.get_by_role("row").filter(has_text="Fornecedor Natural").get_by_role("button", name="Ler fatura").click()
    panel = dialog.get_by_role("region", name="Encomenda ao fornecedor")
    # Proposta automática: a encomenda com mais artigos em comum com a fatura.
    expect(panel.get_by_label("Encomenda recebida com esta fatura")).to_have_value("ec-1")
    table = panel.get_by_role("table", name="Encomenda e fatura")
    # Encomendado, já recebido noutras receções e por receber: 5 − 2 = 3, que vêm nesta fatura.
    expect(table.get_by_role("row", name="ARTHRON 500 5,00 2,00 3,00 3,00").first).to_contain_text("Igual à encomenda")
    expect(table.get_by_role("row").filter(has_text="Oferta")).to_contain_text("Igual à encomenda")
    expect(table.get_by_role("row").filter(has_text="ARTHRON 250mL")).to_contain_text("Não vem nesta fatura: fica por receber")
    expect(panel.get_by_text("Fora da encomenda (entram em stock sem ligação): Ómega 3 MaxPower 60caps", exact=False)).to_be_visible()
    # Consulta da encomenda (percurso da compra), sem sair da receção.
    panel.get_by_role("button", name="Consultar encomenda EC-0001").click()
    expect(panel.get_by_role("navigation", name="Percurso da compra")).to_be_visible()
    expect(panel.get_by_text("Encomenda EC-0001", exact=False).first).to_be_visible()
    panel.get_by_role("button", name="Ocultar encomenda").click()
    # Entrega superior: 5 faturados para 3 encomendados exige autorização explícita.
    dialog.locator("fieldset.invoice-receiving-line").first.get_by_label("Quantidade", exact=True).fill("5")
    expect(table.get_by_role("row").filter(has_text="A mais: +2,00")).to_have_count(1)
    over = panel.get_by_label("Aceitar as quantidades acima do encomendado (entrega superior)")
    expect(over).not_to_be_checked()
    dialog.get_by_label("Tipo de documento", exact=True).select_option("t-fc")
    # O lote da fatura num artigo sem lotes: ativar o controlo de lotes ao lançar (validado antes de enviar).
    dialog.locator("fieldset.invoice-receiving-line").first.locator(".invoice-receiving-lots input[type=checkbox]").evaluate("el => el.click()")
    dialog.get_by_role("button", name="Confirmar receção (2)").click()
    expect(dialog.get_by_text("Há quantidades acima do encomendado", exact=False).first).to_be_visible()
    assert not sent
    panel.screenshot(path=str(OUT / "order-panel.png"))
    over.evaluate("el => el.click()")
    dialog.get_by_role("button", name="Confirmar receção (2)").click()
    expect(page.get_by_text("Receção registada: 2 linhas em stock", exact=False)).to_be_visible()
    body = sent[0]
    assert body["order_id"] == "ec-1" and body["allow_overdelivery"] is True, body
    assert (body["lines"][0]["order_line"], body["lines"][0]["order_bonus_line"]) == (0, 1), body["lines"][0]
    assert body["lines"][0]["quantity"] == "5" and body["lines"][0]["bonus"] == "1"
    assert body["lines"][1]["order_line"] is None and body["lines"][1]["order_bonus_line"] is None
    # Sem encomenda escolhida: receção direta, sem ligações.
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
    page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
    dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
    dialog.get_by_role("row").filter(has_text="Fornecedor Natural").get_by_role("button", name="Ler fatura").click()
    dialog.get_by_role("region", name="Encomenda ao fornecedor").get_by_label("Encomenda recebida com esta fatura").select_option("")
    dialog.get_by_label("Tipo de documento", exact=True).select_option("t-fc")
    dialog.locator("fieldset.invoice-receiving-line").first.locator(".invoice-receiving-lots input[type=checkbox]").evaluate("el => el.click()")
    dialog.get_by_role("button", name="Confirmar receção (2)").click()
    expect(page.get_by_text("Receção registada: 2 linhas em stock", exact=False)).to_be_visible()
    direct = sent[-1]
    assert direct["order_id"] is None and all(l["order_line"] is None for l in direct["lines"]), direct
    assert not errors, errors
    browser.close()
print("PASS: receção a partir de fatura ligada à encomenda (proposta, comparação, entrega superior, ligação das linhas)")
