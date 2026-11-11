"""Conferência documental → «Receber mercadoria» para compras sem receção nem compra no circuito de compras.

Com a fatura no arquivo abre a receção a partir de fatura já lida; sem ela, uma receção preenchida com o
cabeçalho da compra. Só APIs intercetadas (reaproveita o teste da receção a partir de fatura); nada é gravado.
Requer o Vite em 127.0.0.1:5174.
"""

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "scripts/test-invoice-receiving-ui.py"
ns = {"__file__": str(source)}
exec(source.read_text(encoding="utf-8").split("\nwith sync_playwright() as p:")[0], ns)
documents = ns["shared"]["documents"]
OUT = ROOT / ".local" / "receive-from-documents-ui"
OUT.mkdir(parents=True, exist_ok=True)

for row in documents:
    row["procurement_documents"] = [{"id": "r-" + row["id"], "kind": "receipt", "status": "confirmed"}]
documents[22]["procurement_documents"] = []  # FT DEMO/23: sem documento relacionado.
documents[2]["procurement_documents"] = []  # FT DEMO/3: idem, mas sem fatura no arquivo.
PURCHASES = {
    "22": {"archive_document_id": "d1", "supplier_id": "s1"},
    "2": {"archive_document_id": None, "supplier_id": "s1"},
}
asked = []


def intercept(route):
    path = unquote(urlsplit(route.request.url).path).removeprefix("/api")
    if path.startswith("/purchase-receiving/purchases/"):
        key = path.rsplit("/", 1)[1]
        asked.append(key)
        row = documents[int(key)]
        return route.fulfill(json={
            "purchase": {k: row[k] for k in ("id", "document", "document_type", "document_date", "supplier_nif", "supplier_name", "amount_cents", "store_id")},
            **PURCHASES[key], "archive_receipt_id": None, "suggested_document_type_id": "t-fc", "related": [],
        })
    if path == "/entities":
        return route.fulfill(json=[{"id": "s1", "name": "Fornecedor Natural", "tax_number": "507475500", "is_supplier": True,
                                    "active": True, "removed": False, "email": "compras@example.test"}])
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="pt-PT")
    page.clock.install(time=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Contabilidade", exact=True).click()
    page.get_by_role("button", name="Conferência documental", exact=True).click()
    # Só as compras sem receção nem compra ligadas mostram «Receber mercadoria».
    linked = page.get_by_role("row").filter(has=page.get_by_text("FT DEMO/22", exact=True))
    expect(linked.get_by_role("button", name="Conferir", exact=True)).to_be_visible()
    expect(linked.get_by_role("button", name="Receber mercadoria", exact=True)).to_have_count(0)
    target = page.get_by_role("row").filter(has=page.get_by_text("FT DEMO/23", exact=True))
    target.get_by_role("button", name="Receber mercadoria", exact=True).click()
    # Fatura no arquivo: abre a receção a partir de fatura já lida, nas receções de mercadoria.
    dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
    expect(dialog.get_by_role("button", name="Confirmar receção (2)")).to_be_visible()
    expect(dialog.get_by_label("Documento do fornecedor", exact=True)).to_have_value("FA.2026/3208")
    assert "rececao.compra" not in page.url and "Rece%C3%A7%C3%B5es" in page.url, page.url
    dialog.screenshot(path=str(OUT / "from-invoice.png"))
    dialog.get_by_role("button", name="Fechar", exact=True).click()
    page.get_by_role("alertdialog").get_by_role("button", name="Descartar alterações").click() if page.get_by_role("alertdialog").count() else None
    # Reabrir à mão a receção a partir de fatura volta à lista (não repete a leitura automática).
    page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
    again = page.get_by_role("dialog", name="Receção a partir de fatura")
    expect(again.get_by_role("row").filter(has_text="Fornecedor Natural").first.get_by_role("button", name="Ler fatura")).to_be_visible()
    again.get_by_role("button", name="Fechar", exact=True).click()
    # Sem fatura no arquivo: receção manual com o cabeçalho da compra.
    page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria&rececao.compra=2")
    draft = page.get_by_role("dialog", name="Receção — rascunho")
    expect(draft.get_by_label("Documento do fornecedor", exact=True)).to_have_value("FT DEMO/3")
    expect(draft.get_by_label("Data do documento", exact=True)).to_have_value("2026-10-03")
    expect(draft.get_by_label("Fornecedor", exact=True)).to_have_value("s1")
    expect(page.get_by_text("Receção preenchida a partir da compra FT DEMO/3", exact=False)).to_be_visible()
    draft.screenshot(path=str(OUT / "manual.png"))
    assert asked == ["22", "2"], asked
    assert not errors, errors
    browser.close()
print("PASS: receber mercadoria a partir da conferência documental (fatura do arquivo e receção preenchida)")
