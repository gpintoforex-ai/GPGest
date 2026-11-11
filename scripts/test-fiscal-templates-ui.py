"""Obrigações e Prazos → Modelos fiscais: propostas pelo regime, escolha, criação «a validar» e modelos já criados.

Reutiliza os dados simulados de test-obligations-ui.py; só a escrita esperada é aceite. Requer o Vite em 127.0.0.1:5174.
"""
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-obligations-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-obligations-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
OUT = ROOT / ".local/fiscal-templates-ui"
OUT.mkdir(parents=True, exist_ok=True)


def template(key, title, rule, first, applicable=True, selected=True, created=False, reason="Regime normal com periodicidade mensal"):
    return {"key": key, "title": title, "kind": title, "rule": rule, "periodicity": "Mensal", "first_due": first, "source": "Calendário fiscal da AT",
            "applicable": applicable, "reason": reason, "created": created, "selected": selected}


templates = {"regime": "normal", "period": "mensal", "employees": 0, "notes": ["O IUC gere-se na ficha de cada viatura (separador Veículos)."], "templates": [
    template("iva_mensal_declaracao", "IVA — declaração periódica (mensal)", "Até ao dia 20 do 2.º mês seguinte ao mês a que respeita.", "2026-11-20"),
    template("iva_trimestral_declaracao", "IVA — declaração periódica (trimestral)", "Até ao dia 20 do 2.º mês seguinte ao trimestre.", "2026-11-20",
             applicable=False, selected=False, reason="Só no regime normal trimestral"),
    template("dmr", "Declaração Mensal de Remunerações (AT)", "Até ao dia 10 do mês seguinte.", "2026-12-10", applicable=False, selected=False, reason="Sem funcionários registados"),
    template("ies", "IES — Informação Empresarial Simplificada", "Até 15 de julho.", "2027-07-15", created=True, selected=False, reason="Habitual em sociedades"),
    template("imi", "IMI — pagamento (1.ª ou única prestação)", "Em maio; acima de 100 € há prestações.", "2027-05-31", selected=False, reason="Habitual em sociedades"),
]}
writes = []


def intercept(route):
    path, method = urlsplit(route.request.url).path.removeprefix("/api"), route.request.method
    if path == "/obligations/fiscal-templates" and method == "GET":
        return route.fulfill(json=templates)
    if path == "/obligations/fiscal-templates" and method == "POST":
        writes.append(route.request.post_data_json)
        return route.fulfill(json={"created": [{"key": k} for k in route.request.post_data_json["keys"]], "skipped": []})
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
        page.goto("http://127.0.0.1:5174/?pagina=Obriga%C3%A7%C3%B5es%20e%20Prazos&area.obrigacoes=fiscal")
        page.get_by_role("button", name="Modelos fiscais").click()
        dialog = page.get_by_role("dialog", name="Modelos fiscais")
        expect(dialog).to_contain_text("Regime normal · IVA mensal · 0 funcionário(s) ativo(s)")
        expect(dialog).to_contain_text("ficam «a validar»")
        table = dialog.get_by_role("table", name="Modelos fiscais disponíveis")
        expect(table.get_by_label("Criar IVA — declaração periódica (mensal)")).to_be_checked()
        expect(table.get_by_label("Criar Declaração Mensal de Remunerações (AT)")).not_to_be_checked()
        expect(table.get_by_label("Criar IES — Informação Empresarial Simplificada")).to_be_disabled()
        expect(table).to_contain_text("Já criada")
        expect(table).to_contain_text("Sem funcionários registados")
        table.get_by_label("Criar IMI — pagamento (1.ª ou única prestação)").check()
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."
        page.screenshot(path=str(OUT / f"modelos-{theme}-{width}.png"), full_page=True)
        dialog.get_by_label("Responsável (opcional)").select_option("u1")
        dialog.get_by_role("button", name="Criar 2 selecionada(s)").click()
        expect(page.get_by_text("2 obrigação(ões) fiscal(is) criada(s), a validar.")).to_be_visible()
        assert writes == [{"keys": ["iva_mensal_declaracao", "imi"], "responsible_id": "u1"}], writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: modelos fiscais (pré-seleção pelo regime, já criadas bloqueadas, escolha e criação a validar), temas e larguras.")
