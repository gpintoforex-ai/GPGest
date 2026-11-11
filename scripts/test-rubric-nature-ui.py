"""Natureza das rubricas: vistas do Dashboard (custos operacionais, compras e investimentos, obrigações fiscais,
saídas de tesouraria) com origem e detalhe por vista; Rubricas → Natureza financeira com sugestão, simulação
antes/depois, registos afetados e aprovação com motivo (administrador) ou aviso (gestor).

Reutiliza os dados simulados de test-dashboard-modern-ui.py; só as escritas esperadas são aceites.
Requer o Vite em 127.0.0.1:5174.
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
OUT = ROOT / ".local/rubric-nature-ui"
OUT.mkdir(parents=True, exist_ok=True)

ORIGIN = {"operating": "Documentos de compra e custos sem documento registados no banco",
          "fiscal": "Pagamentos ao Estado: registos bancários e documentos nas rubricas de obrigações fiscais"}
views = [
    {"key": "operating", "label": "Custos e despesas operacionais", "basis": "sem IVA dedutível", "total_cents": 600000, "previous_cents": 500000, "change_percent": 20.0,
     "origin": ORIGIN["operating"], "rows": ns["data"]["expenses"]["rows"], "unclassified_cents": 25000},
    {"key": "purchases", "label": "Compras e investimentos", "basis": "sem IVA dedutível", "total_cents": 0, "previous_cents": 0, "change_percent": None,
     "origin": "Documentos de compra nas rubricas de análise de compras", "rows": [], "unclassified_cents": 0},
    {"key": "fiscal", "label": "Obrigações fiscais", "basis": "valor pago", "total_cents": 201852, "previous_cents": 0, "change_percent": None, "origin": ORIGIN["fiscal"],
     "rows": [{"category_id": "iva", "name": "IVA", "amount_cents": 201852, "previous_cents": 0}], "unclassified_cents": 0,
     "vat": {"output_cents": 283000, "input_cents": 200000, "deductible_cents": 180000, "non_deductible_cents": 20000, "payments_cents": 201852,
             "note": "IVA liquidado: recebido dos clientes por conta do Estado (não é receita)."}},
    {"key": "treasury", "label": "Saídas de tesouraria", "basis": "valor pago", "total_cents": 710000, "previous_cents": 650000, "change_percent": 9.2,
     "origin": "Pagamentos efetivos pela data do pagamento", "rows": [{"category_id": "iva", "name": "IVA", "amount_cents": 201852, "previous_cents": 0}], "unclassified_cents": 0},
]
ns["data"]["expenses"].update(views=views, rubrics_without_nature=3)

RUBRICS = [
    {"id": "iva", "name": "IVA", "active": True, "nature": "", "nature_label": "Por definir", "in_operating_costs": True, "in_purchases": False, "in_treasury": True,
     "in_fiscal": False, "in_payment_budget": True,
     "suggestion": {"nature": "fiscal", "in_operating_costs": False, "in_purchases": False, "in_treasury": True, "in_fiscal": True, "in_payment_budget": True,
                    "reason": "Pagamentos de IVA ao Estado são uma obrigação fiscal."}},
    {"id": "imp", "name": "Impostos", "active": True, "nature": "", "nature_label": "Por definir", "in_operating_costs": True, "in_purchases": False, "in_treasury": True,
     "in_fiscal": False, "in_payment_budget": True, "suggestion": None},
]
DEFAULT = {"in_operating_costs": True, "in_purchases": False, "in_treasury": True, "in_fiscal": False, "in_payment_budget": True}
NATURES = [{"key": "", "label": "Por definir", "flags": DEFAULT}, {"key": "operacional", "label": "Custo operacional", "flags": DEFAULT},
           {"key": "fiscal", "label": "Obrigação fiscal", "flags": {**DEFAULT, "in_operating_costs": False, "in_fiscal": True}}]
FLAGS = [{"key": k, "label": k} for k in DEFAULT]
SIMULATION = {
    "fingerprint": "f" * 64, "start": "2025-12-01", "end": "2026-11-11",
    "changes": [{"category_id": "iva", "name": "IVA", "before": {"nature": "", **DEFAULT}, "after": {**RUBRICS[0]["suggestion"], "reason": None}}],
    "views": [{"key": "operating", "label": "Custos e despesas operacionais", "before_cents": 801852, "after_cents": 600000, "difference_cents": -201852},
              {"key": "fiscal", "label": "Obrigações fiscais", "before_cents": 0, "after_cents": 201852, "difference_cents": 201852},
              {"key": "treasury", "label": "Saídas de tesouraria", "before_cents": 710000, "after_cents": 710000, "difference_cents": 0}],
    "affected": {"count": 1, "rows": [{"id": "bank:b1", "date": "2026-09-21", "label": "IVA", "entity": "PAGAMENTO AO ESTADO", "source": "Banco",
                                       "categories": ["IVA"], "amount_cents": 201852}]},
    "preserved": ["Previsão de liquidez e prazos de pagamento", "Extratos, livro bancário e reconciliação"],
}
SIMULATION["changes"][0]["after"].pop("reason")
SIMULATION["warnings"] = [{"category_id": "iva", "text": "«Vendas» não tem pagamentos nem documentos de custo no período: a alteração não muda nenhum valor."}]
CANDIDATES = [
    {"id": "bank:b1", "date": "2026-08-20", "description": "PAG ESTADO IVA 2T 2026", "origin": "Banco · BPI Principal", "amount_cents": 201852,
     "category_id": "imp", "category_name": "Impostos", "confidence": 90, "reason": "A descrição menciona IVA.", "reclassified": False, "rule": None},
    {"id": "bank:b2", "date": "2026-07-15", "description": "PAGAMENTO AT 998877", "origin": "Banco · BPI Principal", "amount_cents": 30000,
     "category_id": "imp", "category_name": "Impostos", "confidence": 55, "reason": "Pagamento ao Estado sem indicar o imposto: confirme no comprovativo.",
     "reclassified": False, "rule": None},
]
PREVIEW = {"fingerprint": "a" * 64, "start": "2024-12-01", "end": "2026-11-11", "target": "IVA", "total_cents": 201852,
           "rows": [{"id": "bank:b1", "date": "2026-08-20", "description": "PAG ESTADO IVA 2T 2026", "origin": "Banco · BPI Principal",
                     "amount_cents": 201852, "from_name": "Impostos", "to_name": "IVA"}],
           "views": [{"key": "operating", "label": "Custos e despesas operacionais", "before_cents": 801852, "after_cents": 600000, "difference_cents": -201852},
                     {"key": "fiscal", "label": "Obrigações fiscais", "before_cents": 0, "after_cents": 201852, "difference_cents": 201852},
                     {"key": "treasury", "label": "Saídas de tesouraria", "before_cents": 710000, "after_cents": 710000, "difference_cents": 0}],
           "preserved": ["Valor, data, descrição e rubrica original de cada registo (nada é alterado no banco nem na caixa)"]}
state = {"role": "administrador", "applied": False, "fiscal_rubric": False, "reclassified": False, "undone": False, "rule": False}


def fiscal_payments():
    history = []
    if state["reclassified"]:
        history = [{"id": "r1", "record": "bank:b1", "origin": "Banco · BPI Principal", "date": "2026-08-20", "description": "PAG ESTADO IVA 2T 2026",
                    "amount_cents": 201852, "from_name": "Impostos", "to_name": "IVA", "reason": "IVA estava em Impostos com a TSU", "user": "Administrador",
                    "created_at": "2026-11-11T10:00:00+00:00", "undone_at": "2026-11-11T10:05:00+00:00" if state["undone"] else None,
                    "rule": None if state["undone"] else {"pattern": "pag estado iva", "category_id": "ivaR", "counts_as_cost": True, "exists": state["rule"]}}]
    return {"start": "2024-12-01", "end": "2026-11-11", "can_apply": state["role"] == "administrador",
            "fiscal_rubrics": [{"id": "ivaR", "name": "IVA"}] if state["fiscal_rubric"] else [],
            "candidates": CANDIDATES[1:] if state["reclassified"] and not state["undone"] else CANDIDATES, "history": history}
writes, drills = [], []


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path, route.request.method
    if path == "/api/bootstrap":
        boot = copy.deepcopy(ns["boot"])
        boot["user"]["role"] = state["role"]
        return route.fulfill(json=boot)
    if path == "/api/dashboard/expenses/drilldown":
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        drills.append(params)
        if params.get("category") == "iva":
            return route.fulfill(json={"vat_basis": "valor pago", "total_cents": 201852, "count": 1, "rows": [
                {"id": "bank:b1", "label": "IVA", "date": "2026-09-21", "entity": "PAGAMENTO AO ESTADO", "source": "Banco", "amount_cents": 201852}]})
        return route.fulfill(json={"vat_basis": "valor pago", "total_cents": 201852, "count": 1, "rows": [{"id": "iva", "label": "IVA", "amount_cents": 201852}]})
    if path == "/api/rubric-nature" and method == "GET":
        rubrics = copy.deepcopy(RUBRICS)
        if state["applied"]:
            rubrics[0].update(nature="fiscal", in_operating_costs=False, in_fiscal=True, suggestion=None)
        return route.fulfill(json={"natures": NATURES, "flags": FLAGS, "can_apply": state["role"] == "administrador", "rubrics": rubrics})
    if path == "/api/rubric-nature/simulate" and method == "POST":
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json=SIMULATION)
    if path == "/api/rubric-nature/apply" and method == "POST":
        writes.append((path, route.request.post_data_json))
        state["applied"] = True
        return route.fulfill(json={"applied": 1, "views": SIMULATION["views"]})
    if path == "/api/rubric-nature/fiscal-payments":
        return route.fulfill(json=fiscal_payments())
    if path == "/api/rubric-nature/fiscal-rubric" and method == "POST":
        writes.append((path, route.request.post_data_json))
        state["fiscal_rubric"] = True
        return route.fulfill(json={"id": "ivaR", "name": "IVA"})
    if path == "/api/rubric-nature/reclassify/preview" and method == "POST":
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json=PREVIEW)
    if path == "/api/rubric-nature/reclassify" and method == "POST":
        writes.append((path, route.request.post_data_json))
        state["reclassified"] = True
        return route.fulfill(json={"applied": 1, "views": PREVIEW["views"]})
    if path == "/api/rubric-nature/reclassifications/r1/undo" and method == "POST":
        writes.append((path, route.request.post_data_json))
        state["undone"] = True
        return route.fulfill(json={"undone": "r1"})
    if path == "/api/bank-rules" and method == "POST":
        writes.append((path, route.request.post_data_json))
        state["rule"] = True
        return route.fulfill(json={"id": "rule1"})
    if path in ("/api/categories", "/api/tables/categories") and method == "GET":
        return route.fulfill(json=[])
    return ns["intercept"](route)


def audit(page):
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 390)):
        writes.clear()
        drills.clear()
        state.update(role="administrador", applied=False, fiscal_rubric=False, reclassified=False, undone=False, rule=False)
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}'); localStorage.setItem('iazela-dashboard-versao:dashboard-modern-fixture','nova')")
        page.route("**/api/**", intercept)
        # 1) Dashboard: as quatro vistas com a origem dos valores e o detalhe por vista.
        page.goto("http://127.0.0.1:5174/?pagina=Dashboard")
        banner = page.get_by_role("region", name="Nova versão do Dashboard")
        if banner.count():
            banner.get_by_role("button", name="Abrir a nova versão").click()
        page.get_by_role("tablist", name="Áreas do dashboard").get_by_role("tab", name="Evolução").click()
        group = page.get_by_role("group", name="Vista das saídas")
        expect(group.get_by_role("button")).to_have_count(4)
        card = page.locator("figure.dash-chart").filter(has=group)
        expect(card).to_contain_text("Custos e despesas operacionais (sem IVA dedutível)")
        expect(card).to_contain_text(ORIGIN["operating"])
        group.get_by_role("button", name="Obrigações fiscais").click()
        expect(group.get_by_role("button", name="Obrigações fiscais")).to_have_attribute("aria-pressed", "true")
        expect(card).to_contain_text("Obrigações fiscais (valor pago)")
        vat = card.get_by_label("IVA do período")
        for text in ("IVA liquidado nas vendas", "2.830,00", "IVA dedutível estimado", "1.800,00", "IVA não dedutível (custo)", "200,00", "Pagamentos ao Estado", "2.018,52"):
            expect(vat).to_contain_text(text)
        expect(card.get_by_role("button", name="Natureza das rubricas (3 por definir)")).to_be_visible()
        audit(page)
        page.screenshot(path=str(OUT / f"dashboard-fiscal-{theme}-{width}.png"), full_page=True)
        card.get_by_role("button", name="IVA").first.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_contain_text("PAGAMENTO AO ESTADO")
        assert drills and drills[-1].get("view") == "fiscal" and drills[-1].get("category") == "iva", drills
        page.keyboard.press("Escape")
        # 2) Rubricas → Natureza financeira: sugestão, simulação e aprovação com motivo.
        page.goto("http://127.0.0.1:5174/?pagina=Rubricas&area.rubricas=natureza")
        suggestions = page.get_by_role("region", name="Sugestões")
        expect(suggestions).to_contain_text("Pagamentos de IVA ao Estado são uma obrigação fiscal.")
        expect(page.get_by_role("button", name="Simular 0 alteração(ões)")).to_be_disabled()
        suggestions.get_by_role("button", name="Usar sugestão em IVA").click()
        grid = page.get_by_role("table", name="Natureza e análises de cada rubrica")
        expect(grid.get_by_label("Natureza de IVA")).to_have_value("fiscal")
        expect(grid.get_by_label("IVA: Custos operacionais")).not_to_be_checked()
        expect(grid.get_by_label("IVA: Obrigações fiscais")).to_be_checked()
        expect(grid.get_by_label("Impostos: Custos operacionais")).to_be_checked()
        assert not writes, writes  # Nada é gravado sem simular e aprovar.
        page.get_by_role("button", name="Simular 1 alteração(ões)").click()
        simulation = page.get_by_role("region", name="Simulação")
        assert writes[-1][0] == "/api/rubric-nature/simulate"
        assert writes[-1][1]["changes"] == [{"category_id": "iva", "nature": "fiscal", "in_operating_costs": False, "in_purchases": False, "in_treasury": True,
                                             "in_fiscal": True, "in_payment_budget": True}], writes[-1]
        before_after = simulation.get_by_role("table", name="Antes e depois por vista")
        expect(before_after.get_by_role("row").filter(has_text="Custos e despesas operacionais")).to_contain_text("−2.018,52")
        expect(before_after.get_by_role("row").filter(has_text="Saídas de tesouraria")).to_contain_text("Sem alteração")
        expect(simulation.get_by_role("table", name="Registos afetados")).to_contain_text("PAGAMENTO AO ESTADO")
        expect(simulation).to_contain_text("Não muda: Previsão de liquidez")
        expect(simulation.get_by_role("note", name="Avisos da simulação")).to_contain_text("não muda nenhum valor")
        apply = simulation.get_by_role("button", name="Aplicar alterações")
        expect(apply).to_be_disabled()
        simulation.get_by_label("Motivo da alteração").fill("Separar IVA dos custos")
        audit(page)
        page.screenshot(path=str(OUT / f"natureza-{theme}-{width}.png"), full_page=True)
        apply.click()
        expect(page.get_by_text("Natureza aplicada a 1 rubrica(s).")).to_be_visible()
        assert writes[-1][0] == "/api/rubric-nature/apply"
        assert writes[-1][1]["fingerprint"] == "f" * 64 and writes[-1][1]["reason"] == "Separar IVA dos custos", writes[-1]
        expect(page.get_by_role("table", name="Natureza e análises de cada rubrica").get_by_label("Natureza de IVA")).to_have_value("fiscal")
        # 3) Pagamentos ao Estado noutras rubricas: criar a rubrica IVA, pré-visualizar, reclassificar, regra e anular.
        fiscal = page.get_by_role("region", name="Pagamentos ao Estado")
        candidates = fiscal.get_by_role("table", name="Pagamentos que parecem IVA ao Estado")
        expect(candidates.get_by_role("row")).to_have_count(3)
        expect(candidates).to_contain_text("Indício forte")
        expect(candidates).to_contain_text("Indício médio")
        expect(fiscal.get_by_role("button", name="Pré-visualizar 0 reclassificação(ões)")).to_be_disabled()
        fiscal.get_by_role("button", name="Criar rubrica IVA (obrigação fiscal)").click()
        expect(page.get_by_text("Rubrica «IVA» criada como obrigação fiscal")).to_be_visible()
        assert ("/api/rubric-nature/fiscal-rubric", {"name": "IVA"}) in writes, writes
        expect(fiscal.get_by_role("button", name="Criar rubrica IVA (obrigação fiscal)")).to_have_count(0)
        fiscal.get_by_role("button", name="Selecionar os de indício forte (1)").click()
        expect(fiscal.get_by_label("Selecionar PAG ESTADO IVA 2T 2026 de 20/08/2026")).to_be_checked()
        expect(fiscal.get_by_label("Selecionar PAGAMENTO AT 998877 de 15/07/2026")).not_to_be_checked()
        fiscal.get_by_role("button", name="Pré-visualizar 1 reclassificação(ões)").click()
        preview = fiscal.get_by_role("region", name="Pré-visualização da reclassificação")
        expect(preview.get_by_role("table", name="Antes e depois da reclassificação").get_by_role("row").filter(
            has_text="Custos e despesas operacionais")).to_contain_text("−2.018,52")
        assert writes[-1] == ("/api/rubric-nature/reclassify/preview", {"items": ["bank:b1"], "category_id": "ivaR"}), writes[-1]
        expect(preview).to_contain_text("nada é alterado no banco nem na caixa")
        reclassify = preview.get_by_role("button", name="Reclassificar")
        expect(reclassify).to_be_disabled()
        preview.get_by_label("Motivo da reclassificação").fill("IVA estava em Impostos com a TSU")
        audit(page)
        page.screenshot(path=str(OUT / f"pagamentos-estado-{theme}-{width}.png"), full_page=True)
        reclassify.click()
        expect(page.get_by_text("1 pagamento(s) reclassificado(s).")).to_be_visible()
        assert writes[-1] == ("/api/rubric-nature/reclassify", {"items": ["bank:b1"], "category_id": "ivaR", "fingerprint": "a" * 64,
                                                               "reason": "IVA estava em Impostos com a TSU"}), writes[-1]
        expect(candidates.get_by_role("row")).to_have_count(2)
        fiscal.get_by_text("Histórico de reclassificações (1 ativa(s))").click()
        history = fiscal.get_by_role("table", name="Histórico de reclassificações")
        expect(history).to_contain_text("Impostos → IVA")
        history.get_by_role("button", name="Criar regra para os próximos").click()
        expect(history.get_by_label("Texto na descrição do extrato")).to_have_value("pag estado iva")
        history.get_by_role("button", name="Guardar regra").click()
        expect(page.get_by_text("Regra bancária criada")).to_be_visible()
        assert writes[-1][0] == "/api/bank-rules" and {k: writes[-1][1][k] for k in ("pattern", "kind", "direction", "category_id", "counts_as_cost")} == {
            "pattern": "pag estado iva", "kind": "payment", "direction": -1, "category_id": "ivaR", "counts_as_cost": True}, writes[-1]
        if not history.is_visible():
            fiscal.get_by_text("Histórico de reclassificações (1 ativa(s))").click()
        expect(history).to_contain_text("Já existe regra bancária para «pag estado iva».")
        history.get_by_role("button", name="Anular").click()
        confirm = history.get_by_role("button", name="Confirmar anulação")
        expect(confirm).to_be_disabled()
        history.get_by_label("Motivo da anulação").fill("Afinal era IRC")
        audit(page)
        confirm.click()
        expect(page.get_by_text("Reclassificação anulada")).to_be_visible()
        assert writes[-1] == ("/api/rubric-nature/reclassifications/r1/undo", {"reason": "Afinal era IRC"}), writes[-1]
        # Gestor: simula, mas não aplica.
        state.update(role="gestor", applied=False, fiscal_rubric=False, reclassified=False, undone=False, rule=False)
        page.reload()
        page.get_by_role("region", name="Sugestões").get_by_role("button", name="Usar sugestão em IVA").click()
        page.get_by_role("button", name="Simular 1 alteração(ões)").click()
        expect(page.get_by_role("region", name="Simulação")).to_contain_text("Só um administrador pode aplicar")
        expect(page.get_by_role("button", name="Aplicar alterações")).to_have_count(0)
        expect(page.get_by_role("region", name="Pagamentos ao Estado")).to_contain_text("Peça a um administrador para criar a rubrica «IVA».")
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: vistas do Dashboard por natureza (origem, IVA do período, detalhe por vista) e natureza das rubricas "
      "(sugestão, simulação antes/depois com avisos, registos afetados, aprovação com motivo; gestor sem aplicar), pagamentos ao Estado "
      "(rubrica IVA, pré-visualização, reclassificação, regra bancária, anulação), temas e larguras.")
print(json.dumps({"drilldowns": len(drills)}))
