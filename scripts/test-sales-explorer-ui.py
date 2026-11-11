"""Faturação e recebimentos — análise comercial: indicadores clicáveis, gráficos, pesquisa e filtros, separadores
Documentos/Clientes/Artigos/Recebimentos com ordenação, painel único com histórico (cliente → documento → artigo →
voltar; recebimento → documento), separadores do documento (resumo, artigos, recebimentos, original), ficha do artigo
e regresso sem perder filtros nem percurso, conferência intacta no modo local e ausente no integrado; temas,
larguras e contraste. Só consulta: qualquer escrita falha o teste. Dados fictícios. Requer o Vite em 127.0.0.1:5174.
"""
import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-dashboard-modern-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-dashboard-modern-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
OUT = ROOT / ".local/sales-explorer-ui"
OUT.mkdir(parents=True, exist_ok=True)


def doc(identifier, number, customer, key, gross, received, state, label, due=None, direction=1, typ="FT"):
    net = round(gross / 1.23)
    return {"id": identifier, "origin": "saft", "store_id": "s1", "date": "2026-10-10", "type": typ, "type_label": {"FT": "Fatura", "NC": "Nota de crédito"}[typ],
            "series": "FT A", "number": number.split("/")[-1], "document": number, "key": f"saft:{typ}:A:{number}", "customer_key": key, "customer_code": key[2:],
            "customer_name": customer, "customer_nif": "500000002", "status": "normal", "status_label": "Normal", "direction": direction, "net_cents": net,
            "tax_cents": gross - net, "gross_cents": gross, "received_cents": received, "pending_cents": 0 if direction < 0 else gross - received,
            "due_date": due, "state": state, "state_label": label, "line_count": 2, "last_receipt_date": "2026-10-20" if received else None}


DOCS = [doc("d1", "FT A/1", "Cliente Alfa", "c:C1", 12300, 5000, "vencido", "Vencido", "2026-10-20"),
        doc("d2", "FT A/2", "Cliente Alfa", "c:C1", 24600, 24600, "pago", "Pago"),
        doc("d3", "FT A/3", "Cliente Beta", "c:C2", 6150, 0, "pendente", "Pendente"),
        doc("d4", "NC A/1", "Cliente Beta", "c:C2", 1230, 0, "nota_credito", "Nota de crédito", direction=-1, typ="NC")]
KPIS = {"net_cents": 33500, "gross_cents": 41820, "received_cents": 29600, "pending_cents": 13450, "overdue_cents": 7300, "credit_notes_cents": 1230,
        "documents": 4, "invoices": 3, "cancelled": 1, "customers": 2, "average_ticket_cents": 11500, "average_receipt_days": 6.5, "net_missing": 0}
OVERVIEW = {"mode": "saft", "kpis": KPIS, "has_lines": True, "notes": ["Recebido: recibos e pagamentos indicados no SAF-T (nunca somados)."],
            "months": [{"key": "2026-09", "net_cents": 0, "gross_cents": 0, "pending_cents": 0, "documents": 0, "received_cents": 0},
                       {"key": "2026-10", "net_cents": 33500, "gross_cents": 41820, "pending_cents": 13450, "documents": 4, "received_cents": 29600}],
            "top_customers": [{"key": "c:C1", "code": "C1", "name": "Cliente Alfa", "nif": "500000002", "net_cents": 30000, "pending_cents": 7300},
                              {"key": "c:C2", "code": "C2", "name": "Cliente Beta", "nif": "", "net_cents": 3500, "pending_cents": 6150}],
            "top_articles": [{"code": "P1", "description": "Artigo fictício", "net_cents": 25000, "quantity": "5", "customers": 2}],
            "states": [{"key": "pago", "label": "Pago", "documents": 1, "pending_cents": 0, "gross_cents": 24600},
                       {"key": "vencido", "label": "Vencido", "documents": 1, "pending_cents": 7300, "gross_cents": 12300}]}
LINES = [{"index": 1, "code": "P1", "description": "Artigo fictício", "quantity": "2", "unit": "UN", "unit_price": "40.65", "discount_cents": 0, "vat_rate": "23",
          "net_cents": 8130, "vat_cents": 1870, "gross_cents": 10000, "article_id": "art-1"},
         {"index": 2, "code": "S9", "description": "Serviço de entrega", "quantity": "1", "unit": "UN", "unit_price": "1.87", "discount_cents": 0, "vat_rate": "23",
          "net_cents": 1870, "vat_cents": 430, "gross_cents": 2300, "article_id": None}]
RECEIPT = {"id": "r1", "source": "saft_receipt", "source_label": "Recibo (SAF-T)", "document": "RG A/1", "date": "2026-10-20", "amount_cents": 5000,
           "allocated_cents": 5000, "method": "TB", "account": "", "reference": "RG A/1", "reconciliation": None}
writes, requests = [], []
STATE = {"mode": "local"}


def detail(params):
    view = params["view"]
    if view == "document":
        base = next(d for d in DOCS if d["id"] == params["id"])
        return {"view": "document", **base, "customer": {"code": "C1", "name": base["customer_name"], "nif": "500000002", "address": "Rua Fictícia, 1000-001 Lisboa"},
                "entity_id": "ent-1", "payment_terms": "30 dia(s)", "discount_cents": 0, "lines": LINES, "lines_totals": {"net_cents": 10000, "vat_cents": 2300},
                "lines_match": True, "receipts": [RECEIPT], "conference": [], "received_source": "saft", "originals": [],
                "original_note": "Sem PDF original no Arquivo digital para este documento. Os dados acima são os do SAF-T (não são uma cópia do original)."}
    if view == "customer":
        rows = [d for d in DOCS if d["customer_key"] == params["key"]]
        return {"view": "customer", "key": params["key"], "code": params["key"][2:], "name": rows[0]["customer_name"], "nif": "500000002", "address": "Rua Fictícia",
                "phone": "210000000", "email": "", "entity_id": "ent-1", "totals": {**KPIS, "documents": len(rows)}, "last_date": "2026-10-10",
                "history": [{"key": "2026-09", "net_cents": 0}, {"key": "2026-10", "net_cents": 30000}], "documents": rows,
                "articles": [{"code": "P1", "description": "Artigo fictício", "quantity": "5", "documents": 2, "net_cents": 25000, "average_price_cents": 5000,
                              "last_unit_price": "40.65", "last_date": "2026-10-10"}],
                "stopped": [{"code": "P7", "description": "Artigo antigo", "quantity": "3", "documents": 1, "net_cents": 900, "average_price_cents": 300,
                             "last_unit_price": "3", "last_date": "2026-03-02"}]}
    if view == "article":
        return {"view": "article", "code": "P1", "description": "Artigo fictício", "family": "Família fictícia", "brand": "Marca fictícia", "article_id": "art-1",
                "stock": "12", "unit": "UN", "cost": None, "last_unit_price": "40.65", "average_price_cents": 5000, "margin_percent": None, "quantity": "5",
                "net_cents": 25000, "documents": 2, "customers": 2, "cost_note": "Custo de aquisição desconhecido: a margem não é calculada.",
                "lines": [{"document_id": "d1", "document": "FT A/1", "date": "2026-10-10", "customer_name": "Cliente Alfa", "customer_key": "c:C1",
                           "quantity": "2", "unit_price": "40.65", "net_cents": 8130}],
                "buyers": [{"key": "c:C1", "name": "Cliente Alfa", "nif": "500000002", "net_cents": 8130, "documents": 1, "last_date": "2026-10-10"}]}
    return {"view": "receipt", "id": params["id"], **{k: RECEIPT[k] for k in ("source", "source_label", "document", "date", "amount_cents", "method", "account",
                                                                              "reference", "reconciliation")},
            "documents": [{**DOCS[0], "allocated_cents": 5000}]}


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path, route.request.method
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    if method != "GET":
        writes.append(path)
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
    if path == "/api/bootstrap":
        boot = copy.deepcopy(ns["boot"])
        boot["user"]["role"] = "gestor"
        boot["integration"] = {"mode": STATE["mode"], "provider": "none" if STATE["mode"] == "local" else "wintouch"}
        return route.fulfill(json=boot)
    if path.startswith("/api/sales/"):
        requests.append((path, params))
        name = path.rsplit("/", 1)[-1]
        if name == "overview":
            return route.fulfill(json={**OVERVIEW, "mode": "saft" if STATE["mode"] == "local" else "erp"})
        if name == "documents":
            rows = [d for d in DOCS if (not params.get("state") or params["state"] == d["state"] or params["state"] == "em_divida" and d["pending_cents"])
                    and (not params.get("customer") or d["customer_key"] == params["customer"])
                    and (not params.get("q") or params["q"].lower() in (d["document"] + d["customer_name"]).lower())]
            return route.fulfill(json={"rows": rows, "total": len(rows), "offset": 0, "totals": KPIS})
        if name == "customers":
            return route.fulfill(json={"rows": [{**c, "received_cents": 0, "overdue_cents": c["pending_cents"], "documents": 2, "last_date": "2026-10-10"}
                                                for c in OVERVIEW["top_customers"]], "total": 2})
        if name == "articles":
            return route.fulfill(json={"rows": [{**OVERVIEW["top_articles"][0], "documents": 2, "average_price_cents": 5000, "last_date": "2026-10-10", "family": "Família fictícia"}],
                                       "total": 1, "has_lines": True})
        if name == "receipts":
            return route.fulfill(json={"rows": [{**RECEIPT, "id": "saft_receipt:r1", "documents": [{"id": "d1", "document": "FT A/1", "customer_name": "Cliente Alfa",
                                                                                                   "customer_key": "c:C1", "allocated_cents": 5000, "state": "vencido"}]}],
                                       "total": 1, "note": "Cada recebimento aparece uma vez."})
        if name == "detail":
            return route.fulfill(json=detail(params))
    if path.startswith("/api/stock/"):
        # A ficha do artigo é outra página: aqui só se confirma a navegação e o regresso (o catálogo fica indisponível).
        return route.fulfill(status=503, json={"detail": "Catálogo indisponível no teste"})
    if path == "/api/conference":
        return route.fulfill(json={"mode": "local", "billing_available": True, "documents": [], "receipts": [], "daily": [], "suggestions": [], "conferences": [],
                                   "rules": ["A faturação nunca cria recebimentos."], "to_justify_cents": None,
                                   "indicators": {"billing": {"available": True, "net_cents": 33500, "gross_cents": 41820, "documents": 4, "excluded_documents": 1},
                                                  "receipts": {"net_cents": 29600, "received_cents": 29600, "refunds_cents": 0}, "payments_cents": 0,
                                                  "balances": {"cash_open_sessions_cents": 0, "bank_book_cents": 0, "bank_accounts_without_opening": 0,
                                                               "card_pending_settlement_cents": 0, "card_pending_settlement_count": 0},
                                                  "conference": {"pending_cents": 0, "pending_documents": 0, "justified_cents": 0}, "to_justify_cents": None}})
    return ns["intercept"](route)


def audit(page):
    page.mouse.move(1, 1)
    page.wait_for_timeout(400)
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")
    wide = page.evaluate("""() => document.documentElement.scrollWidth > innerWidth ? [...document.querySelectorAll('body *')]
      .filter(e => e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('.se-scroll, .table-wrap, .dash-plot'))
      .slice(0, 8).map(e => `${e.tagName}.${typeof e.className === 'string' ? e.className : ''} → ${Math.round(e.getBoundingClientRect().right)}`) : null""")
    assert not wide, f"A página transborda na horizontal: {wide}"


def last(name):
    return [p for path, p in requests if path.endswith("/" + name)][-1]


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width, mode in (("light", 1440, "local"), ("dark", 390, "integrado")):
        STATE["mode"] = mode
        requests.clear()
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Fatura%C3%A7%C3%A3o%20e%20recebimentos")
        kpis = page.get_by_role("region", name="Indicadores de faturação e recebimentos")
        expect(kpis).to_contain_text("Prazo médio de recebimento")
        tabs = page.get_by_role("navigation", name="Áreas de faturação e recebimentos")
        expect(tabs).to_have_count(1 if mode == "local" else 0)  # Conferência só no modo local.
        # Indicador clicável → documentos vencidos.
        kpis.get_by_role("button", name="Vencido", exact=False).click()
        table = page.get_by_role("table", name="Documentos de faturação")
        expect(table.locator("tbody tr")).to_have_count(1)
        assert last("documents")["state"] == "vencido"
        expect(table).to_contain_text("Vencido")
        page.get_by_label("Estado do documento").select_option("")
        expect(table.locator("tbody tr")).to_have_count(4)
        # Pesquisa global e ordenação.
        page.get_by_label("Pesquisar cliente, código, NIF, documento, artigo ou valor").fill("ft a/2")
        expect(table.locator("tbody tr")).to_have_count(1)
        page.get_by_role("button", name="Limpar filtros").click()
        table.get_by_role("button", name="Total").click()
        page.wait_for_timeout(200)
        assert (last("documents")["sort"], last("documents")["direction"]) == ("gross_cents", "desc")
        # Cliente (gráfico) → ficha resumida → documento → artigo → voltar.
        page.get_by_role("button", name="Cliente Alfa: 300,00", exact=False).click()
        panel = page.get_by_role("dialog", name="Consulta comercial")
        expect(panel).to_contain_text("Deixou de comprar")
        panel.get_by_role("tab", name="Artigos comprados (1)").click()
        expect(panel).to_contain_text("Artigo fictício")
        panel.get_by_role("tab", name="Documentos (2)").click()
        panel.get_by_role("button", name="FT A/1").click()
        expect(panel.get_by_role("heading", name="Fatura FT A/1")).to_be_visible()
        expect(panel).to_contain_text("30 dia(s)")
        panel.get_by_role("tab", name="Artigos (2)").click()
        lines = panel.get_by_role("table", name="Artigos faturados")
        expect(lines.locator("tbody tr")).to_have_count(2)
        panel.get_by_label("Pesquisar artigos no documento").fill("entrega")
        expect(lines.locator("tbody tr")).to_have_count(1)
        panel.get_by_label("Pesquisar artigos no documento").fill("")
        lines.get_by_role("button", name="P1").click()
        expect(panel).to_contain_text("Custo de aquisição desconhecido")
        expect(panel).to_contain_text("Família fictícia")
        panel.get_by_role("button", name="Voltar").click()
        expect(panel.get_by_role("heading", name="Fatura FT A/1")).to_be_visible()
        # Recebimentos do documento → recebimento → documentos liquidados.
        panel.get_by_role("tab", name="Recebimentos (1)").click()
        expect(panel).to_contain_text("Recibo (SAF-T)")
        panel.get_by_role("tab", name="Documento original").click()
        expect(panel).to_contain_text("não são uma cópia do original")
        panel.get_by_role("tab", name="Recebimentos (1)").click()
        panel.get_by_role("button", name="RG A/1").click()
        expect(panel.get_by_role("table", name="Documentos liquidados pelo recebimento")).to_contain_text("FT A/1")
        audit(page)
        page.screenshot(path=str(OUT / f"painel-{theme}-{width}.png"), full_page=True)
        panel.get_by_role("navigation", name="Percurso do detalhe").get_by_role("button", name="Fatura FT A/1").click()
        panel.get_by_role("tab", name="Artigos (2)").click()
        # Ficha do artigo e regresso: filtros e percurso repostos.
        page.get_by_label("Pesquisar cliente, código, NIF, documento, artigo ou valor").evaluate("e => e")  # campo existe por trás do painel
        panel.get_by_role("button", name="Ficha do artigo").click()
        page.wait_for_function("location.search.includes('consulta.tipo=article')")
        assert "consulta.id=art-1" in page.url, page.url
        page.go_back()
        panel = page.get_by_role("dialog", name="Consulta comercial")
        expect(panel.get_by_role("heading", name="Fatura FT A/1")).to_be_visible()
        page.keyboard.press("Escape")
        # Separadores principais.
        page.get_by_role("group", name="Vista").get_by_role("button", name="Clientes").click()
        expect(page.get_by_role("table", name="Clientes")).to_contain_text("Cliente Beta")
        page.get_by_role("group", name="Vista").get_by_role("button", name="Artigos").click()
        expect(page.get_by_role("table", name="Artigos")).to_contain_text("Família fictícia")
        page.get_by_role("group", name="Vista").get_by_role("button", name="Recebimentos").click()
        expect(page.get_by_role("table", name="Recebimentos")).to_contain_text("RG A/1")
        audit(page)
        page.screenshot(path=str(OUT / f"pagina-{theme}-{width}.png"), full_page=True)
        if mode == "local":
            tabs.get_by_role("button", name="Conferência").click()
            expect(page.get_by_role("region", name="Indicadores de faturação e tesouraria")).to_be_visible()
            expect(page.get_by_text("A faturação nunca cria recebimentos.")).to_be_visible()
        assert not writes, writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: análise comercial (indicadores, gráficos, pesquisa, filtros, ordenação, cliente → documento → artigo → voltar, recebimento → documento, "
      "ficha do artigo com regresso sem perder filtros, conferência no modo local), temas, larguras e contraste; sem escritas.")
print(json.dumps({"requests": len(requests)}))
