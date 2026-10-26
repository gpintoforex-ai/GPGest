"""Tabelas → Tipos de Documentos: listagem, filtros por natureza/condição e ficha nova. Só fixtures."""

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app.table_specs  # noqa: E402,F401
from app.table_framework import TABLES  # noqa: E402

OUT = ROOT / ".local" / "document-types-ui"
OUT.mkdir(parents=True, exist_ok=True)
spec = TABLES["document_types"]
fields = [f.describe() for f in spec.fields]
for f in fields:
    if f["type"] == "payment_method":
        f["options"] = [{"value": "numerario", "label": "Numerário", "active": True}, {"value": "transferencia", "label": "Transferência", "active": True}]
INFO = {
    "key": "document_types", "title": spec.title, "singular": spec.singular, "feminine": False, "description": spec.description,
    "fields": fields, "extra_columns": [{"name": n, "label": label} for n, label in spec.extra_columns], "code_field": "code",
    "description_field": "description", "has_active": True, "filters": list(spec.filter_fields), "allow_create": True,
    "allow_remove": True, "remove_reason": "",
    "permissions": {k: True for k in ("view", "create", "edit", "status", "remove", "import", "export", "erp_remove")},
    "erp": {"integrated": False, "available": False, "reason": "A empresa está em modo local.", "operations": {}},
    "local_only_reason": "",
}


def row(identifier, code, description, nature, condition, effect, sign):
    values = {"code": code, "description": description, "nature": nature, "payment_condition": condition, "default_payment_method": None,
              "default_due_days": 30 if condition == "credito" else None, "series": "", "creates_cash_movement": condition == "dinheiro",
              "creates_debt": condition == "credito", "active": True}
    labels = {"nature": "Nota de crédito de compra" if nature != "compra" else "Compra", "payment_condition": condition.capitalize(),
              "sign_label": sign, "effect": effect, "documents": "0"}
    return {"id": identifier, "values": values, "labels": labels, "code": code, "description": description, "active": True, "origin": "local",
            "erp_id": "", "erp_code": "", "sync_state": "apenas_local", "sync_label": "Apenas local", "last_sync_at": None, "last_error": ""}


ROWS = [
    row("t1", "FC", "Fatura de Compra", "compra", "credito", "dívida + previsto", "+1 (fatura)"),
    row("t2", "FCD", "Fatura de Compra a Dinheiro", "compra", "dinheiro", "movimento real", "+1 (fatura)"),
    row("t3", "NCC", "Nota de Crédito de Compra", "nota_credito_compra", "credito", "dívida + previsto", "−1 (nota de crédito)"),
    row("t4", "NCCD", "Nota de Crédito Compra Dinheiro", "nota_credito_compra", "dinheiro", "movimento real", "−1 (nota de crédito)"),
]
queries = []

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
shared = {"__file__": str(fixtures)}
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], shared)


def intercept(route):
    url = urlsplit(route.request.url)
    path = url.path.removeprefix("/api")
    if path == "/tables/document_types":
        return route.fulfill(json=INFO)
    if path == "/tables/document_types/records":
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        queries.append(params)
        rows = [r for r in ROWS if not params.get("f_nature") or r["values"]["nature"] == params["f_nature"]]
        return route.fulfill(json={"rows": rows, "total": len(rows), "total_all": len(ROWS), "page": 1, "pages": 1, "size": 25})
    return shared["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174/?pagina=Tipos%20de%20Documentos")
    table = page.get_by_role("table", name="Tipos de Documentos")
    expect(table).to_contain_text("Fatura de Compra a Dinheiro")
    expect(table).to_contain_text("movimento real")
    # Filtro por natureza (e condição) além da pesquisa e do estado.
    page.get_by_label("Natureza", exact=True).select_option("nota_credito_compra")
    expect(table).not_to_contain_text("Fatura de Compra a Dinheiro")
    assert any(q.get("f_nature") == "nota_credito_compra" for q in queries), queries
    expect(page.get_by_label("Condição de pagamento", exact=True)).to_be_visible()
    page.screenshot(path=str(OUT / "list.png"))
    # Ficha nova: natureza, condição, meio por omissão, dias, série e indicadores.
    page.get_by_role("button", name="Novo tipo de documento").click()
    dialog = page.get_by_role("dialog", name="Novo tipo de documento")
    for label in ("Código", "Descrição", "Natureza", "Condição de pagamento", "Meio de pagamento por omissão", "Dias de vencimento", "Série"):
        expect(dialog.get_by_label(label, exact=True)).to_be_visible()
    expect(dialog.get_by_label("Gera movimento de caixa")).to_be_visible()
    expect(dialog.get_by_label("Gera dívida")).to_be_visible()
    page.screenshot(path=str(OUT / "new.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    browser.close()
print("Document types UI passed: list, nature filter, new record form, mobile. Fixtures only.")
