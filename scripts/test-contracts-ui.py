"""Obrigações e Prazos → Contratos: lista com prazo de denúncia, novo contrato, denúncia com fidelização e renovação.

Reutiliza os dados simulados de test-obligations-ui.py; só as escritas esperadas são aceites. Requer o Vite em 127.0.0.1:5174.
"""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-obligations-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-obligations-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
OUT = ROOT / ".local/contracts-ui"
OUT.mkdir(parents=True, exist_ok=True)

meo = {"id": "k1", "store_id": None, "entity_id": None, "entity_name": "MEO", "contract_type": "Telecomunicações", "description": "", "number": "C-123",
       "start_date": "2025-01-01", "end_date": "2026-12-31", "fidelization_end": "2027-06-30", "auto_renewal": True, "renewal_months": 12, "notice_days": 30,
       "amount_cents": 5100, "vat_rate": "23.00", "payment_periodicity": "mensal", "payment_periodicity_label": "Mensal", "payment_every_months": None,
       "first_payment_date": "2025-01-10", "category_id": "cat-tel", "responsible_id": "u1", "responsible": "Gestora", "alert_days": [90, 60, 30, 15, 7, 0],
       "status": "ativo", "status_label": "Ativo", "notes": "", "revision": 0, "next_renewal": "2026-12-31", "notice_deadline": "2026-12-01", "days_to_notice": 21,
       "notice_state": "a_aproximar", "fidelized": True, "annual_cents": 61200, "monthly_cents": 5100, "notice_obligation_id": "o-n", "payment_obligation_id": "o-p"}
rent = {**meo, "id": "k2", "entity_name": "Senhorio", "contract_type": "Renda", "number": "", "auto_renewal": False, "fidelization_end": None, "fidelized": False,
        "end_date": "2027-03-31", "next_renewal": "2027-03-31", "notice_days": 60, "notice_deadline": "2027-01-30", "days_to_notice": 81, "notice_state": "agendado",
        "amount_cents": 120000, "annual_cents": 1440000, "monthly_cents": 120000}
listing = {"contracts": [meo, rent], "types": ["Telecomunicações", "Renda"], "statuses": {}, "users": [{"id": "u1", "name": "Gestora"}],
           "periodicities": {"unica": "Única", "mensal": "Mensal", "anual": "Anual", "personalizada": "Personalizada (de N em N meses)"},
           "summary": {"active": 2, "annual_cents": 1501200, "notice_30": 1, "fidelized": 1}}
writes = []


def intercept(route):
    path, method = urlsplit(route.request.url).path.removeprefix("/api"), route.request.method
    if path == "/contracts" and method == "GET":
        return route.fulfill(json=listing)
    if path == "/contracts/k1" and method == "GET":
        return route.fulfill(json={**meo, "occurrences": [{"id": "x", "due_date": "2026-12-01", "role": "notice", "expected_cents": None, "status": "pendente", "days": 21}]})
    if path.startswith("/contracts") and method in ("POST", "PUT"):
        writes.append((method, path, route.request.post_data_json))
        return route.fulfill(json={**meo, "id": "new"})
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 1440), ("dark", 390)):
        writes.clear()
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Obriga%C3%A7%C3%B5es%20e%20Prazos&area.obrigacoes=contrato")
        table = page.get_by_role("table", name="Lista de contratos")
        expect(table).to_contain_text("MEO")
        expect(table).to_contain_text("01/12/2026")
        expect(table).to_contain_text("30 dias de pré-aviso · daqui a 21 dia(s)")
        expect(table).to_contain_text("A aproximar-se")
        expect(table).to_contain_text("Sem renovação automática")
        expect(page.get_by_text("2 ativo(s) · 15.012,00 € por ano")).to_be_visible()
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."
        page.screenshot(path=str(OUT / f"lista-{theme}-{width}.png"), full_page=True)
        # Denúncia de um contrato fidelizado: exige aceitar a penalização.
        table.locator("tr").filter(has_text="MEO").locator("summary").click()
        page.get_by_role("button", name="Registar denúncia").click()
        dialog = page.get_by_role("dialog", name="Registar denúncia")
        expect(dialog).to_contain_text("Fidelizado até 30/06/2027")
        dialog.get_by_label("Motivo").fill("Proposta melhor")
        expect(dialog.get_by_role("button", name="Registar denúncia")).to_be_disabled()
        dialog.get_by_label("Aceito a eventual penalização").check()
        dialog.get_by_role("button", name="Registar denúncia").click()
        expect(page.get_by_text("Registar denúncia: decisão registada.")).to_be_visible()
        assert writes[-1] == ("POST", "/contracts/k1/decision", {"revision": 0, "action": "denunciar", "reason": "Proposta melhor", "effective_date": None,
                                                                 "accept_fidelization_penalty": True}), writes[-1]
        # Novo contrato: o prazo de denúncia é mostrado antes de guardar.
        page.get_by_role("button", name="Novo contrato").click()
        drawer = page.get_by_role("dialog", name="Novo contrato")
        drawer.get_by_label("Fornecedor ou entidade").fill("EDP Comercial")
        drawer.get_by_label("Tipo de contrato").fill("Energia")
        drawer.get_by_label("Data de início").fill("2026-01-01")
        drawer.get_by_label("Fim do período em curso / renovação").fill("2026-12-31")
        drawer.get_by_label("Pré-aviso para denúncia (dias)").fill("15")
        expect(drawer).to_contain_text("Prazo de denúncia: 16/12/2026")
        drawer.get_by_label("Valor de cada pagamento (€, com IVA)").fill("85,40")
        drawer.get_by_label("Rubrica financeira").select_option("cat-tel")
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        page.screenshot(path=str(OUT / f"novo-{theme}-{width}.png"), full_page=True)
        drawer.get_by_role("button", name="Criar contrato").click()
        expect(page.get_by_text("Contrato criado; os prazos de denúncia e os pagamentos foram gerados.")).to_be_visible()
        method, path, body = writes[-1]
        assert (method, path) == ("POST", "/contracts") and body["amount_cents"] == 8540 and body["notice_days"] == 15 and body["auto_renewal"] is True, writes[-1]
        assert body["end_date"] == "2026-12-31" and body["category_id"] == "cat-tel" and body["alert_days"] == [90, 60, 30, 15, 7, 0], body
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: contratos (prazo de denúncia na lista, denúncia com fidelização confirmada, novo contrato com prazo calculado), temas e larguras.")
