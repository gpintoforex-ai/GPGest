"""Janela «Editar cliente»: separadores, setas entre registos, alterações por guardar e NIF. Só fixtures."""

import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app.table_specs  # noqa: E402,F401
from app.cash_local import BASE_METHODS  # noqa: E402
from app.table_framework import TABLES  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

OUT = ROOT / ".local" / "entity-dialog-ui"
OUT.mkdir(parents=True, exist_ok=True)
spec = TABLES["customers"]
fields = [f.describe() for f in spec.fields]
for f in fields:
    if f["type"] == "payment_method":
        f["options"] = [{"value": k, "label": v, "active": True} for k, v in BASE_METHODS.items()]
INFO = {
    "key": "customers", "title": "Clientes", "singular": "cliente", "feminine": False, "description": spec.description,
    "fields": fields, "extra_columns": [], "code_field": None, "description_field": "name", "has_active": True,
    "allow_create": True, "allow_remove": True, "remove_reason": "",
    "permissions": {k: True for k in ("view", "create", "edit", "status", "remove", "import", "export", "erp_remove")},
    "erp": {"integrated": False, "available": False, "reason": "A empresa está em modo local.", "operations": {}},
    "local_only_reason": "",
}


def row(identifier, name, nif):
    values = {"tax_number": nif, "name": name, "email": "", "phone": "", "address": "", "locality": "", "postal_code": "",
              "category_id": None, "payment_term_id": None, "payment_method_id": None, "is_customer": True, "is_supplier": False, "active": True}
    return {"id": identifier, "values": values, "labels": {}, "code": None, "description": name, "active": True, "origin": "local",
            "erp_id": "", "erp_code": "", "sync_state": "apenas_local", "sync_label": "Apenas local", "last_sync_at": None, "last_error": ""}


ROWS = [row("local:1", "ADÃO GONÇALVES DOMINGUES", "138261938"), row("local:2", "ADELAIDE FERREIRA", "128558180")]

fixtures = Path(__file__).with_name("test-visual-template-ui.py")
ns = {"__file__": str(fixtures)}
exec(fixtures.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
base = ns["intercept"]


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/tables/customers":
        return route.fulfill(json=INFO)
    if path == "/tables/customers/records":
        return route.fulfill(json={"rows": ROWS, "total": 2, "total_all": 2, "page": 1, "pages": 1, "size": 25})
    if path.endswith("/details"):
        identifier = path.split("/")[4].replace("%3A", ":")
        current = next(r for r in ROWS if r["id"] == identifier)
        return route.fulfill(json={**current, "dependencies": [], "managed_fields": [], "remove_blocked": {"app": None, "both": None}})
    if path == "/payment-terms":
        return route.fulfill(json=[{"id": 7, "name": "30 dias", "active": True}])
    if path == "/entities/lookup":
        return route.fulfill(json={"is_valid": False, "name": "", "address": "", "locality": "", "postal_code": ""})
    return base(route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174/?pagina=Clientes")
    # Depois de tentar criar com campos em falta, a primeira escolha numa lista fica logo aceite.
    page.get_by_role("button", name="Novo cliente").click()
    creating = page.get_by_role("dialog", name="Novo cliente")
    creating.get_by_label("Nome", exact=True).fill("CLIENTE NOVO")
    creating.get_by_role("button", name="Criar cliente").click()
    term = creating.get_by_label("Prazo de pagamento", exact=True)
    expect(term).to_be_visible()
    expect(creating.get_by_text("Preencha este campo.").first).to_be_visible()
    # Teclado real (eventos nativos, como no rato), não select_option, que dispara os eventos por script.
    term.focus()
    page.keyboard.press("ArrowDown")
    expect(term).to_have_value("7")
    method = creating.get_by_label("Meio de pagamento", exact=True)
    method.focus()
    page.keyboard.press("ArrowDown")
    expect(method).not_to_have_value("")
    creating.get_by_role("button", name="Cancelar").click()
    page.get_by_role("button", name="Descartar alterações").click()
    expect(creating).to_have_count(0)
    page.get_by_role("button", name="Editar ADÃO GONÇALVES DOMINGUES").click()
    dialog = page.get_by_role("dialog", name="Editar cliente")
    expect(dialog.get_by_role("tab", name="Condições comerciais")).to_be_visible()
    expect(dialog.get_by_text("NIF válido.")).to_be_visible()
    expect(dialog.get_by_text("1 de 2")).to_be_visible()
    expect(dialog.get_by_role("button", name="Consultar VIES")).to_have_count(0)
    page.screenshot(path=str(OUT / "edit.png"))
    # Mudar de separador não altera o tamanho da janela.
    size = "(() => { const r = document.querySelector('dialog[open]').getBoundingClientRect(); return [r.width, r.height]; })()"
    before = page.evaluate(size)
    dialog.get_by_role("tab", name="Condições comerciais").click()
    expect(dialog.get_by_label("Prazo de pagamento", exact=True)).to_be_visible()
    page.screenshot(path=str(OUT / "edit-conditions.png"))
    assert page.evaluate(size) == before, f"A janela mudou de tamanho: {before} -> {page.evaluate(size)}"
    dialog.get_by_role("tab", name="Dados gerais").click()
    # Seguinte, sem alterações: muda logo de registo.
    dialog.get_by_role("button", name="Registo seguinte").click()
    expect(dialog.get_by_label("Nome", exact=True)).to_have_value("ADELAIDE FERREIRA")
    expect(dialog.get_by_text("2 de 2")).to_be_visible()
    # NIF inválido: indicação imediata, por texto.
    dialog.get_by_label("NIF", exact=True).fill("123456780")
    expect(dialog.get_by_text("NIF inválido: o dígito de controlo não corresponde.")).to_be_visible()
    # Com alterações por guardar, mudar de registo pede confirmação.
    dialog.get_by_role("button", name="Registo anterior").click()
    page.wait_for_selector("[role=alertdialog]")
    expect(page.get_by_text("Tem alterações por guardar. Pretende sair sem guardar?")).to_be_visible()
    page.get_by_role("button", name="Continuar a editar").click()
    expect(dialog.get_by_label("NIF", exact=True)).to_have_value("123456780")
    # NIF válido e novo: consulta o VIES ao sair do campo.
    dialog.get_by_label("NIF", exact=True).fill("138261938")
    dialog.get_by_label("Email", exact=True).click()
    expect(dialog.get_by_text("NIF válido; não consta no VIES.")).to_be_visible()
    # Portátil comum: a ficha cabe sem deslocação.
    page.set_viewport_size({"width": 1366, "height": 768})
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "edit-laptop.png"))
    fits = page.evaluate("(() => { const d = document.querySelector('dialog[open]'); return [d.scrollHeight, d.clientHeight]; })()")
    assert fits[0] <= fits[1] + 1, f"A janela precisa de deslocação: {fits}"
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(OUT / "edit-mobile.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors, errors
    browser.close()
print("Entity dialog UI passed: tabs, previous/next with unsaved-change guard, NIF check digit and VIES on blur, mobile. Fixtures only.")
