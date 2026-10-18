"""Document loading resilience in Chromium; fictitious data and intercepted APIs."""

from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

stamp = date.today().isoformat()
row = {
    "id": "purchase-test",
    "store_id": "store-test",
    "supplier_name": "Fornecedor fictício",
    "supplier_nif": "500000001",
    "document": "FT TESTE/001",
    "document_type": "FT",
    "document_date": stamp,
    "amount_cents": 12300,
    "tax_cents": 2300,
    "origin": "efatura",
    "availability": "por_verificar",
    "attachment_status": "sem_anexo",
    "attachments": [],
    "erp_status": "sem_correspondencia",
    "erp_candidates": [],
    "treatment": "por_tratar",
    "accounting": "por_preparar",
    "payment_status": "por_pagar",
    "bank_status": "pendente",
    "remaining_cents": 12300,
    "paid_cents": 0,
    "payments": [],
    "pending_days": 0,
}
workflow_row = {
    **row,
    "complete": False,
    "attachment_state": "unknown",
    "erp_state": "unknown",
    "drive_state": "unknown",
    "drive_candidates": [],
    "messages": [],
}
boot = {
    "mode": "demonstracao",
    "company": {"id": "loading-test", "name": "Empresa fictícia"},
    "integration": {
        "mode": "local",
        "provider": "none",
        "active": False,
        "writes": False,
        "capabilities": {"read_documents": False},
        "resources": [],
    },
    "user": {"id": "user-test", "name": "Utilizador fictício", "role": "administrador"},
    "stores": [{"id": "store-test", "name": "Loja fictícia"}],
    "registers": [],
    "accounts": [],
    "categories": [],
    "users": [],
    "payment_methods": {},
}
fail_recon = True
fail_workflow = True
calls, errors = [], []


def mock_api(route):
    parsed = urlsplit(route.request.url)
    path = parsed.path
    calls.append((route.request.method, path))
    if path == "/api/invoice-workflow/verify":
        route.fulfill(json={"checked": 1, "erp": "unavailable", "drive": "unavailable"})
        return
    assert route.request.method == "GET", (route.request.method, path)
    if path == "/api/reconciliation":
        if fail_recon:
            route.fulfill(
                status=503, json={"detail": "Consulta secundária temporariamente indisponível."}
            )
            return
        result = {"banks": [], "internals": []}
    elif path == "/api/invoice-workflow":
        if fail_workflow:
            route.fulfill(
                status=503, json={"detail": "Consulta de faturas temporariamente indisponível."}
            )
            return
        result = {"rows": [workflow_row], "summary": {"total": 1, "completed": 0, "pending": 1}}
    else:
        result = (
            boot
            if path == "/api/bootstrap"
            else [row]
            if path == "/api/documents"
            else {}
            if path == "/api/dashboard"
            else []
        )
    route.fulfill(json=result)


with sync_playwright() as playwright:
    expect.set_options(timeout=15000)
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", mock_api)
    page.goto("http://127.0.0.1:5174")
    page.get_by_role("button", name="Contabilidade", exact=True).click()
    page.get_by_role("button", name="Conferência documental", exact=True).click()
    expect(page.get_by_role("cell", name="FT TESTE/001", exact=True)).to_be_visible()
    expect(page.get_by_role("alert")).to_contain_text("reconciliação bancária")
    expect(page.get_by_role("alert")).to_contain_text("não é necessário repetir a importação")
    page.get_by_role("button", name="Clara · Verificar documentos", exact=True).click()
    dialog = page.get_by_role("dialog", name="Clara · Conferência e-Fatura", exact=True)
    dialog.get_by_role("button", name="Continuar com documentos existentes", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text("Não foi possível carregar as faturas")
    expect(dialog.get_by_text("0 faturas no período", exact=True)).to_have_count(0)
    expect(dialog.get_by_role("button", name="Verificar faturas", exact=True)).to_be_disabled()
    assert not any(method == "POST" for method, _ in calls)
    fail_workflow = False
    dialog.get_by_role("button", name="Tentar carregar faturas novamente", exact=True).click()
    expect(dialog.get_by_role("cell", name="FT TESTE/001", exact=True)).to_be_visible()
    expect(dialog.get_by_text("1 faturas no período", exact=True)).to_be_visible()
    dialog.get_by_role("button", name="Verificar faturas", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text(
        "A operação ficou guardada, mas não foi possível atualizar todas as informações"
    )
    expect(dialog.get_by_role("cell", name="FT TESTE/001", exact=True)).to_be_visible()
    assert (
        sum(method == "POST" and path == "/api/invoice-workflow/verify" for method, path in calls)
        == 1
    )
    assert not any(method == "POST" and path.endswith("/settings") for method, path in calls)
    output = Path(__file__).resolve().parents[1] / ".local/invoice-loading-ui"
    output.mkdir(exist_ok=True)
    page.screenshot(path=str(output / "desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(dialog.get_by_role("button", name="Fechar", exact=True)).to_be_visible()
    page.screenshot(path=str(output / "mobile.png"))
    assert not errors, errors
    page.unroute_all(behavior="ignoreErrors")
    browser.close()
print(
    "PASS: partial document loads preserved, workflow read retry, no misleading zero count, committed verification preserved on refresh error, settings untouched, desktop/mobile; mocked API only."
)
