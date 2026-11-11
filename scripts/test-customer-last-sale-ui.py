"""Tabela de Clientes — «Última venda»: coluna com data e indicador de atividade (texto e dica), ordenação, filtros
rápidos e por data, aviso sobre a cobertura dos dados, limites configuráveis e abertura do documento da última venda
(consulta documental da análise comercial); colunas e ações existentes intactas; temas, larguras e contraste.
Dados fictícios; só a escrita dos limites é aceite. Requer o Vite em 127.0.0.1:5174.
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
OUT = ROOT / ".local/customer-last-sale-ui"
OUT.mkdir(parents=True, exist_ok=True)


def field(name, label, typ="text", column=True):
    return {"name": name, "label": label, "type": typ, "required": name == "name", "max_length": 300, "min_length": None, "minimum": None, "maximum": None,
            "options": [], "column": column, "create_only": False, "create_required": False, "tab": "", "group": "", "span": None, "hidden": False, "help": ""}


DESCRIBE = {"key": "customers", "title": "Clientes", "singular": "cliente", "feminine": False, "description": "Entidades da empresa.",
            "fields": [field("tax_number", "NIF", "nif"), field("name", "Nome"), field("email", "Email", "email"), field("phone", "Telefone")],
            "extra_columns": [{"name": "last_sale", "label": "Última venda"}], "code_field": None, "description_field": "name", "has_active": True,
            "allow_create": True, "allow_remove": False, "remove_reason": "", "filters": [],
            "permissions": {k: True for k in ("view", "create", "edit", "status", "remove", "import", "export", "erp_remove")},
            "erp": {"integrated": False, "available": False, "reason": "A empresa está em modo local.", "operations": {}}, "local_only_reason": ""}


def customer(identifier, name, nif, sale):
    return {"id": identifier, "values": {"tax_number": nif, "name": name, "email": "", "phone": "", "active": True}, "labels": {"last_sale": sale.get("date") or ""},
            "code": None, "description": name, "active": True, "origin": "local", "erp_id": "", "erp_code": "", "sync_state": "apenas_local",
            "sync_label": "Apenas local", "last_sync_at": None, "last_error": "", "extra": {"last_sale": sale}}


def sale(state, label, date=None, document=None, days=None):
    if not date:
        return {"has_sale": False, "state": state, "label": label, "days": None, "hint": "Sem vendas nos dados disponíveis."}
    return {"has_sale": True, "date": date, "id": "doc-" + document, "store_id": "s1", "document": document, "type": "FT", "customer_code": "C",
            "state": state, "label": label, "days": days, "hint": f"Última venda há {days} dia(s).", "stale": False}


ROWS = [customer("local:a", "Cliente A", "500000010", sale("ativo", "Ativo", "2026-11-05", "FT A/10", 6)),
        customer("local:b", "Cliente B", "500000020", sale("acompanhar", "A acompanhar", "2026-08-15", "FT A/7", 88)),
        customer("local:c", "Cliente C", "500000030", sale("inativo", "Inativo", "2026-02-12", "FT A/2", 272)),
        customer("local:d", "Cliente D", "500000040", sale("sem_historico", "Sem histórico"))]
FILTERS = {"recent30": {"local:a"}, "over90": {"local:c", "local:d"}, "over180": {"local:c", "local:d"}, "over365": {"local:d"}, "never": {"local:d"}}
requests, writes = [], []


def document(params):
    return {"view": "document", "id": params["id"], "origin": "saft", "store_id": "s1", "date": "2026-11-05", "type": "FT", "type_label": "Fatura", "series": "FT A",
            "number": "10", "document": "FT A/10", "key": "saft:FT:A:10", "customer_key": "c:C", "customer_code": "C", "customer_name": "Cliente A",
            "customer_nif": "500000010", "status": "normal", "status_label": "Normal", "direction": 1, "net_cents": 10000, "tax_cents": 2300, "gross_cents": 12300,
            "received_cents": 12300, "pending_cents": 0, "due_date": None, "state": "pago", "state_label": "Pago", "line_count": 1, "last_receipt_date": "2026-11-05",
            "customer": {"code": "C", "name": "Cliente A", "nif": "500000010", "address": ""}, "entity_id": "a", "payment_terms": "Pronto pagamento", "discount_cents": 0,
            "lines": [{"index": 1, "code": "P1", "description": "Artigo fictício", "quantity": "2", "unit": "UN", "unit_price": "50", "discount_cents": 0, "vat_rate": "23",
                       "net_cents": 10000, "vat_cents": 2300, "gross_cents": 12300, "article_id": "art-1"}],
            "lines_totals": {"net_cents": 10000, "vat_cents": 2300}, "lines_match": True, "receipts": [], "conference": [], "received_source": "saft",
            "originals": ["arq-1"], "original_note": None}


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path, route.request.method
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    if path == "/api/bootstrap":
        boot = copy.deepcopy(ns["boot"])
        boot["user"]["role"] = "administrador"
        return route.fulfill(json=boot)
    if path == "/api/tables/customers":
        return route.fulfill(json=DESCRIBE)
    if path == "/api/tables/customers/records":
        requests.append(params)
        rows = [r for r in ROWS if (not params.get("activity") or r["id"] in FILTERS[params["activity"]])
                and (not params.get("sale_from") or (r["extra"]["last_sale"].get("date") or "") >= params["sale_from"])]
        if params.get("sort") == "last_sale":
            rows = sorted(rows, key=lambda r: r["labels"]["last_sale"], reverse=params.get("direction") == "desc")
        return route.fulfill(json={"rows": rows, "total": len(rows), "total_all": len(ROWS), "page": 1, "pages": 1, "size": 25,
                                   "meta": {"last_sale": {"mode": "saft", "data_from": "2025-01-02", "data_until": "2026-11-10", "days": [30, 90, 180]}}})
    if path == "/api/customers/activity-settings":
        if method == "PUT":
            writes.append(route.request.post_data_json)
        return route.fulfill(json={"days": [30, 90, 180], "revision": 0, "can_edit": True})
    if path == "/api/sales/detail":
        requests.append({"detail": True, **params})
        return route.fulfill(json=document(params))
    if method != "GET":
        writes.append(path)
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
    return ns["intercept"](route)


def audit(page):
    page.mouse.move(1, 1)
    page.wait_for_timeout(400)
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 390)):
        requests.clear()
        writes.clear()
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Clientes")
        table = page.get_by_role("table", name="Clientes")
        desktop = width > 700  # em telemóvel, a tabela comum mostra as linhas como cartões (sem cabeçalho)
        if desktop:
            expect(table.get_by_role("columnheader", name="Ordenar por Última venda")).to_be_visible()
            for column in ("NIF", "Email", "Telefone", "Ações"):  # colunas existentes intactas
                expect(table.get_by_role("columnheader", name=column, exact=False).first).to_be_visible()
        row = table.locator("tbody tr", has_text="Cliente A")
        expect(row).to_contain_text("05/11/2026")
        expect(row).to_contain_text("Ativo")
        expect(table.locator("tbody tr", has_text="Cliente B")).to_contain_text("A acompanhar")
        expect(table.locator("tbody tr", has_text="Cliente C")).to_contain_text("Inativo")
        expect(table.locator("tbody tr", has_text="Cliente D")).to_contain_text("Sem vendas")
        expect(table.locator("tbody tr", has_text="Cliente D")).to_contain_text("Sem histórico")
        expect(row.get_by_text("Ativo")).to_have_attribute("title", "Última venda há 6 dia(s).")
        expect(page.get_by_text("usa as vendas do SAF-T importado de 02/01/2025 a 10/11/2026", exact=False)).to_be_visible()
        # Ordenação (mais antiga primeiro) e filtros rápidos.
        if desktop:
            table.get_by_role("button", name="Ordenar por Última venda").click()
            page.wait_for_timeout(200)
            assert (requests[-1]["sort"], requests[-1]["direction"]) == ("last_sale", "asc"), requests[-1]
            expect(table.locator("tbody tr").first).to_contain_text("Cliente D")
        filters = page.get_by_role("group", name="Filtros de atividade comercial")
        filters.get_by_role("button", name="Sem vendas há mais de 90 dias").click()
        expect(table.locator("tbody tr")).to_have_count(2)
        assert requests[-1]["activity"] == "over90"
        filters.get_by_role("button", name="Nunca compraram").click()
        expect(table.locator("tbody tr")).to_have_count(1)
        filters.get_by_role("button", name="Todos os clientes").click()
        filters.get_by_label("Última venda desde").fill("2026-08-01")
        expect(table.locator("tbody tr")).to_have_count(2)
        assert requests[-1]["sale_from"] == "2026-08-01"
        filters.get_by_label("Última venda desde").fill("")
        audit(page)
        page.screenshot(path=str(OUT / f"clientes-{theme}-{width}.png"), full_page=True)
        # A data abre o documento da última venda (consulta documental da análise comercial).
        table.get_by_role("button", name="Última venda a Cliente A", exact=False).click()
        panel = page.get_by_role("dialog", name="Consulta comercial")
        expect(panel.get_by_role("heading", name="FT FT A/10")).to_be_visible()
        detail = [r for r in requests if r.get("detail")][-1]
        assert (detail["view"], detail["id"], detail["start"], detail["end"]) == ("document", "doc-FT A/10", "2026-11-05", "2026-11-05"), detail
        panel.get_by_role("tab", name="Artigos (1)").click()
        expect(panel.get_by_role("table", name="Artigos faturados")).to_contain_text("Artigo fictício")
        panel.get_by_role("tab", name="Documento original").click()
        expect(panel.get_by_role("link", name="Abrir original 1")).to_be_visible()
        audit(page)
        page.keyboard.press("Escape")
        # Limites de atividade (administrador).
        filters.get_by_role("button", name="Limites de atividade").click()
        settings = page.get_by_role("dialog", name="Limites de atividade")
        settings.get_by_label("A acompanhar até (dias)").fill("120")
        settings.get_by_role("button", name="Guardar limites").click()
        expect(settings).to_have_count(0)
        assert writes == [{"days": [30, 120, 180], "revision": 0}], writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: última venda na tabela de Clientes (data, estado com texto e dica, ordenação, filtros rápidos e por data, cobertura dos dados, "
      "documento com artigos e original, limites), colunas existentes intactas, temas, larguras e contraste.")
print(json.dumps({"requests": len(requests)}))
