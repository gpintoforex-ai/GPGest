"""Obrigações e Prazos → Veículos: lista com IUC/inspeção/seguro (datas a validar), nova viatura e venda.

Reutiliza os dados simulados de test-obligations-ui.py; só as escritas esperadas são aceites. Requer o Vite em 127.0.0.1:5174.
"""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-obligations-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-obligations-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
OUT = ROOT / ".local/vehicles-ui"
OUT.mkdir(parents=True, exist_ok=True)


def due(day, days, state, cents=None, validated=False):
    return {"due_date": day, "days": days, "state": state, "expected_cents": cents, "validated": validated}


car = {"id": "v1", "store_id": None, "plate": "AA-00-BB", "make": "Mercedes", "model": "A180", "first_registration": "2021-03-15", "age_years": 5,
       "category": "ligeiro_passageiros", "category_label": "Ligeiro de passageiros", "fuel": "Gasóleo", "status": "ativa", "status_label": "Ativa",
       "responsible_id": None, "responsible": None, "iuc_amount_cents": 12000, "iuc_validated": False, "last_inspection_date": None, "inspection_manual_next": None,
       "inspection_manual_years": None, "inspection_cost_cents": 3500, "inspection_validated": False, "insurance_entity": "Fidelidade", "insurance_policy": "AP-1",
       "insurance_renewal": "2027-01-20", "insurance_amount_cents": 42000, "insurance_periodicity": "anual", "next_service_date": None, "service_cost_cents": None,
       "contract_id": None, "category_id": None, "alert_days": [60, 30, 15, 7, 0], "notes": "", "revision": 0,
       "inspection_rule": "Ligeiro de passageiros: inspeção aos 4, 6, 8 ano(s) e depois anual, na data de aniversário da matrícula",
       "upcoming": {"iuc": due("2027-03-31", 141, "agendado", 12000), "inspection": due("2025-03-15", -605, "vencido", 3500),
                    "insurance": due("2027-01-20", 71, "agendado", 42000, True)}}
listing = {"vehicles": [car], "categories": {"ligeiro_passageiros": "Ligeiro de passageiros", "ligeiro_mercadorias": "Ligeiro de mercadorias", "motociclo": "Motociclo, triciclo ou quadriciclo"},
           "fuels": ["Gasóleo", "Elétrico"], "statuses": {}, "contracts": [{"id": "k1", "label": "Leaseplan · Leasing / renting"}], "users": [{"id": "u1", "name": "Gestora"}],
           "notes": ["Modelo: IUC anual até ao último dia do mês da matrícula; confirme no Portal das Finanças (regime e categoria do IUC)."]}
writes = []


def intercept(route):
    path, method = urlsplit(route.request.url).path.removeprefix("/api"), route.request.method
    if path == "/vehicles" and method == "GET":
        return route.fulfill(json=listing)
    if path.startswith("/vehicles") and method in ("POST", "PUT"):
        writes.append((method, path, route.request.post_data_json))
        return route.fulfill(json={**car, "id": "new"})
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
        page.goto("http://127.0.0.1:5174/?pagina=Obriga%C3%A7%C3%B5es%20e%20Prazos&area.obrigacoes=veiculo")
        table = page.get_by_role("table", name="Lista de viaturas")
        expect(table).to_contain_text("AA-00-BB")
        expect(table).to_contain_text("Ligeiro de passageiros · 5 ano(s)")
        expect(table).to_contain_text("605 dia(s) em atraso")
        expect(table.get_by_text("Data a validar")).to_have_count(2)  # IUC e inspeção; o seguro está validado.
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."
        page.screenshot(path=str(OUT / f"lista-{theme}-{width}.png"), full_page=True)
        # Venda: termina os prazos futuros, com motivo.
        table.locator("tr").filter(has_text="AA-00-BB").locator("summary").click()
        page.get_by_role("button", name="Registar venda").click()
        dialog = page.get_by_role("dialog", name="Registar venda")
        dialog.get_by_label("Motivo").fill("Venda a terceiros")
        dialog.get_by_role("button", name="Registar venda").click()
        expect(page.get_by_text("Registar venda: registado.")).to_be_visible()
        assert writes[-1] == ("POST", "/vehicles/v1/status", {"revision": 0, "status": "vendida", "reason": "Venda a terceiros"}), writes[-1]
        # Nova viatura: motociclo exige a regra manual.
        page.get_by_role("button", name="Nova viatura").click()
        drawer = page.get_by_role("dialog", name="Nova viatura")
        drawer.get_by_label("Matrícula", exact=True).fill("44-dd-55")
        drawer.get_by_label("Data da primeira matrícula").fill("2022-05-01")
        drawer.get_by_label("Categoria").select_option("motociclo")
        expect(drawer.get_by_role("button", name="Criar viatura")).to_be_disabled()
        drawer.get_by_label("Próxima inspeção").fill("2027-05-01")
        drawer.get_by_label("Depois, de quantos em quantos anos (vazio = única)").fill("2")
        drawer.get_by_label("Valor previsto (€)").fill("35,00")
        drawer.get_by_label("Leasing / renting (contrato)").select_option("k1")
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        page.screenshot(path=str(OUT / f"nova-{theme}-{width}.png"), full_page=True)
        drawer.get_by_role("button", name="Criar viatura").click()
        expect(page.get_by_text("Viatura criada; IUC, inspeção e restantes prazos foram gerados.")).to_be_visible()
        method, path, body = writes[-1]
        assert (method, path) == ("POST", "/vehicles") and body["category"] == "motociclo" and body["inspection_manual_next"] == "2027-05-01", writes[-1]
        assert body["inspection_manual_years"] == 2 and body["iuc_amount_cents"] == 3500 and body["contract_id"] == "k1" and body["last_inspection_date"] is None, body
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: viaturas (IUC/inspeção/seguro com datas a validar, venda com motivo, nova viatura com regra manual), temas e larguras.")
