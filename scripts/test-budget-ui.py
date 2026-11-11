"""Orçamento e previsão de despesas: indicadores, gráficos, previsto vs realizado, grelha, ficha rápida e versões.

Respostas simuladas; só os pedidos de escrita esperados são aceites e verificados. Requer o Vite em 127.0.0.1:5174.
"""
import copy
import runpy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "scripts/test-dashboard-scope-ui.py"), run_name="fixture")
ns = fixture["ns"]
OUT = ROOT / ".local/budget-ui"
OUT.mkdir(parents=True, exist_ok=True)
boot = copy.deepcopy(ns["boot"])
boot["user"]["role"] = "gestor"
boot["company"]["id"] = "budget-fixture"

LABELS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
rent_months = [111500] * 6 + [117075, 0, 117075, 117075, 117075, 55750]
actual_rent = [111500, 111500, 111500, 120000, 111500, 111500, 117075, 0, 117075, 130000, 98000, 0]
energy_actual = [0] * 9 + [0, 24600, 0]
MONTH = 11


def months():
    rows, cb, ca = [], 0, 0
    for i in range(12):
        actual = actual_rent[i] + energy_actual[i] if i < MONTH else None
        cb += rent_months[i]
        ca += actual or 0
        diff = None if actual is None else actual - rent_months[i]
        rows.append({"month": i + 1, "label": LABELS[i], "budget_cents": rent_months[i], "actual_cents": actual, "deviation_cents": diff,
                     "deviation_pct": None if diff is None or not rent_months[i] else round(diff * 100 / rent_months[i], 1),
                     "cumulative_budget_cents": cb, "cumulative_actual_cents": ca if i < MONTH else None, "closed": i < 9})
    return rows


line = {"id": "l1", "budget_id": "b1", "category_id": "cat-rent", "category_name": "Rendas", "description": "Renda da loja", "entity_id": None,
        "entity_name": "Senhorio, Lda", "cost_center_id": "cc1", "cost_center": "Loja do centro", "amount_cents": 100000, "vat_rate": "23.00",
        "periodicity": "mensal", "periodicity_label": "Mensal", "every_months": None, "start_date": "2026-01-05", "end_date": None, "payment_days": 10,
        "payment_account": "caixa", "increases": [{"from": "2026-07-01", "percent": "5"}], "exceptions": [{"date": "2026-08-05", "skip": True}], "note": "",
        "status": "ativa", "revision": 0, "months": rent_months, "month_cents": rent_months[MONTH - 1], "annual_cents": sum(rent_months),
        "annual_base_cents": 1070000, "annual_vat_cents": 246100, "annual_deductible_cents": 123050, "annual_gross_cents": 1316100,
        "occurrences": [{"date": "2026-01-05", "payment_date": "2026-01-15", "base": 100000, "vat": 23000, "deductible": 11500, "gross": 123000, "cost": 111500, "exception": False}],
        "realized_months": [111500] + [0] * 11, "realized_cents": 111500, "month_realized_cents": 0, "paid_cents": 0, "unknown_payment_cents": 0, "unpaid_cents": 111500,
        "remaining_cents": sum(rent_months) - 111500, "missing_documents": 8, "links": 1,
        "occurrence_execution": [{"date": "2026-01-05", "cost": 111500, "linked_cents": 111500, "remaining_cents": 0, "state": "realizado"}]}
ytd_b, ytd_a = sum(rent_months[:MONTH]), sum(actual_rent[:MONTH]) + sum(energy_actual[:MONTH])
budget = {"id": "b1", "year": 2026, "store_id": None, "name": "Orçamento 2026", "version": 1, "status": "rascunho", "status_label": "Rascunho",
          "previous_id": None, "note": "", "revision": 0, "approved_at": None, "approved_by": None, "created_at": "2026-11-01T10:00:00+00:00"}
options = {"categories": [{"id": "cat-rent", "name": "Rendas", "vat_deduction_percent": 50}, {"id": "cat-energy", "name": "Energia", "vat_deduction_percent": None}],
           "cost_centers": [{"id": "cc1", "code": "LOJA1", "name": "Loja do centro", "active": True}], "accounts": [{"id": "caixa", "name": "Caixa"}],
           "stores": [], "periodicities": {"unica": "Única", "mensal": "Mensal", "trimestral": "Trimestral", "anual": "Anual", "personalizada": "Personalizada (de N em N meses)"},
           "statuses": {"rascunho": "Rascunho", "aprovado": "Aprovado", "revisto": "Revisto"}}
overview = {
    "today": "2026-11-10", "year": 2026, "month": MONTH, "store_id": None, "regime": "normal", "budget": budget, "versions": [budget], "copy_sources": [],
    "kpis": {"annual_budget_cents": sum(rent_months), "period_budget_cents": ytd_b, "period_actual_cents": ytd_a, "deviation_cents": ytd_a - ytd_b,
             "deviation_pct": round((ytd_a - ytd_b) * 100 / ytd_b, 1), "month_budget_cents": rent_months[MONTH - 1], "month_actual_cents": actual_rent[MONTH - 1] + energy_actual[MONTH - 1],
             "unbudgeted_cents": 24600, "lines": 1, "period_closed": False, "annual_gross_cents": 1316100, "annual_vat_cents": 246100, "annual_deductible_cents": 123050,
             "linked_cents": 111500, "paid_cents": 0, "unpaid_cents": 111500, "unknown_payment_cents": 0, "remaining_cents": sum(rent_months) - 111500, "missing_documents": 8},
    "months": months(),
    "rubrics": [
        {"category_id": "cat-rent", "category_name": "Rendas", "budgeted": True, "budget_months": rent_months, "actual_months": actual_rent,
         "month_budget_cents": 117075, "month_actual_cents": 98000, "month_deviation_cents": -19075, "month_deviation_pct": -16.3,
         "ytd_budget_cents": ytd_b, "ytd_actual_cents": sum(actual_rent[:MONTH]), "ytd_deviation_cents": sum(actual_rent[:MONTH]) - ytd_b,
         "ytd_deviation_pct": 0.8, "annual_budget_cents": sum(rent_months), "annual_actual_cents": sum(actual_rent), "state": "acima"},
        {"category_id": "cat-energy", "category_name": "Energia", "budgeted": False, "budget_months": [0] * 12, "actual_months": energy_actual,
         "month_budget_cents": 0, "month_actual_cents": 24600, "month_deviation_cents": 24600, "month_deviation_pct": None, "ytd_budget_cents": 0,
         "ytd_actual_cents": 24600, "ytd_deviation_cents": 24600, "ytd_deviation_pct": None, "annual_budget_cents": 0, "annual_actual_cents": 24600, "state": "nao_orcamentado"},
    ],
    "suppliers": [{"name": "Senhorio, Lda", "annual_cents": sum(rent_months)}],
    "lines": [line], "options": options,
    "notes": ["Orçamento sem IVA: o custo previsto usa o critério dos mapas de gestão.", "Um desvio abaixo do orçamento só é poupança depois do fecho do período contabilístico."],
}
empty = {**overview, "budget": None, "versions": [], "lines": [], "suppliers": [],
         "copy_sources": [{**budget, "id": "b2025", "year": 2025, "status": "aprovado", "status_label": "Aprovado"}]}
candidate = {"line_id": "l1", "description": "Renda da loja", "entity_name": "Senhorio, Lda", "rule_date": "2026-10-05", "expected_cents": 117075,
             "occurrence_linked_cents": 0, "confidence": 99, "reasons": ["Mesma rubrica", "Mesmo fornecedor (NIF)", "Valor dentro de 5 % do previsto"]}
proposal = {"source_kind": "purchase", "source_id": "p10", "category_id": "cat-rent", "category_name": "Rendas", "date": "2026-10-04", "cost_cents": 117075,
            "linked_cents": 0, "remaining_cents": 117075, "supplier_nif": "510774318", "supplier_name": "Senhorio, Lda", "label": "FT R/10", "store_id": "s1",
            "total_cents": 117075, "origin": "Compra", "strength": "forte", "proposed_cents": 117075,
            "candidates": [candidate, {**candidate, "rule_date": "2026-09-05", "confidence": 72, "reasons": ["Mesma rubrica", "29 dias da data prevista"]}]}
matching = {"budget_id": "b1", "year": 2026, "editable": True, "pending": [proposal],
            "unbudgeted": [{**proposal, "source_id": "p11", "category_id": "cat-energy", "category_name": "Energia", "label": "FT E/1", "supplier_name": "Elétrica",
                            "remaining_cents": 24600, "reason": "Sem despesa prevista desta rubrica"}],
            "linked": [], "credits": [], "summary": {"pending": 1, "strong": 1, "ambiguous": 0, "unbudgeted": 1, "unbudgeted_cents": 24600, "linked": 3},
            "thresholds": {"strong": 85, "likely": 65}, "notes": ["Nada é associado sem confirmação."]}
line_links = {"line_id": "l1", "budget_id": "b1", "links": [{"id": "k1", "rule_date": "2026-01-05", "source_kind": "purchase", "source_id": "p1", "amount_cents": 111500,
              "status": "confirmada", "confidence": 99, "method": "sugestao", "reasons": [], "decided_by": "Gestora", "decided_at": "2026-02-01T10:00:00+00:00",
              "undo_reason": "", "payment": "por_pagar", "supplier_name": "Senhorio, Lda", "label": "FT R/1", "origin": "Compra", "total_cents": 111500}]}
state = {"data": overview}
writes = []


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path.removeprefix("/api"), route.request.method
    if path == "/bootstrap":
        return route.fulfill(json=boot)
    if path == "/budgets/b1/matching":
        return route.fulfill(json=matching)
    if path == "/budget-lines/l1/links":
        return route.fulfill(json=line_links)
    if path.startswith("/budget-links/") and method == "POST":
        writes.append((method, path, route.request.post_data_json))
        return route.fulfill(json={"ok": True})
    if path == "/budgets/overview":
        query = parse_qs(url.query)
        assert query.get("year") in (["2026"], ["2025"], ["2027"]), query
        return route.fulfill(json=state["data"])
    if method in ("POST", "PUT", "DELETE") and (path.startswith("/budgets") or path.startswith("/budget-lines")):
        writes.append((method, path, route.request.post_data_json))
        if path == "/budgets":
            return route.fulfill(json={**budget, "id": "new"})
        if path.endswith("/approve"):
            return route.fulfill(json={**budget, "status": "aprovado", "status_label": "Aprovado", "revision": 1})
        return route.fulfill(json={"id": "l2"})
    if method != "GET":
        writes.append(("inesperada", method, path))
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
    return ns["intercept"](route)


def audit(page):
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 1440), ("dark", 390)):
        writes.clear()
        state["data"] = overview
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Or%C3%A7amento")
        expect(page.get_by_role("heading", name="Orçamento e previsão de despesas")).to_be_visible()
        kpis = page.get_by_role("group", name="Indicadores do orçamento")
        for text in ("Orçamento anual", "Previsto até novembro", "Realizado até novembro", "Desvio acumulado", "Despesas não previstas", "246,00 €", "A pagar com IVA (ano)", "13.161,00 €"):
            expect(kpis).to_contain_text(text)
        expect(page.get_by_label("Versão do orçamento")).to_contain_text("Versão 1 · Rascunho")
        expect(page.get_by_role("img", name="Previsto e realizado por mês")).to_be_visible()
        expect(page.get_by_role("img", name="Desvio mensal")).to_be_visible()
        expect(page.get_by_label("Orçamento anual por rubrica")).to_contain_text("Rendas")
        audit(page)
        page.screenshot(path=str(OUT / f"visao-{theme}-{width}.png"), full_page=True)
        # Indicador → comparação por rubrica, já filtrada nas despesas não previstas.
        kpis.get_by_role("button", name="Despesas não previstas: 246,00 €. Ver detalhe").click()
        rubrics = page.get_by_role("table", name="Rubricas")
        expect(rubrics.locator("tbody tr")).to_have_count(1)
        expect(rubrics).to_contain_text("Energia")
        expect(rubrics).to_contain_text("n.a.")
        expect(rubrics).to_contain_text("Não prevista")
        page.get_by_label("Situação").select_option("")
        expect(rubrics.locator("tbody tr")).to_have_count(2)
        expect(rubrics).to_contain_text("Acima do orçamento")
        audit(page)
        page.screenshot(path=str(OUT / f"rubricas-{theme}-{width}.png"), full_page=True)
        # Grelha e ficha rápida de uma despesa recorrente com valor com IVA, aumento e exceção.
        page.get_by_role("navigation", name="Áreas do orçamento").get_by_role("button", name="Despesas previstas").click()
        grid = page.get_by_role("table", name="Grelha do orçamento")
        expect(grid).to_contain_text("Renda da loja")
        expect(grid).to_contain_text("1 aumento(s)")
        page.get_by_role("button", name="Nova despesa prevista").click()
        drawer = page.get_by_role("dialog", name="Nova despesa prevista")
        drawer.get_by_label("Descrição").fill("Eletricidade")
        drawer.get_by_label("Rubrica").select_option("cat-energy")
        expect(drawer).to_contain_text("IVA dedutível previsto: 100 %")
        drawer.get_by_label("Valor por ocorrência (€)").fill("123,00")
        drawer.get_by_label("O valor indicado é").select_option("com")
        drawer.get_by_label("Primeira data da despesa").fill("2026-01-20")
        drawer.get_by_role("button", name="Acrescentar aumento").click()
        drawer.get_by_label("A partir de").fill("2026-09-01")
        drawer.get_by_label("Aumento (%)").fill("3.5")
        drawer.get_by_role("button", name="Acrescentar exceção").click()
        drawer.get_by_label("Data da ocorrência").fill("2026-08-20")
        audit(page)
        page.screenshot(path=str(OUT / f"nova-{theme}-{width}.png"), full_page=True)
        drawer.get_by_role("button", name="Criar despesa prevista").click()
        expect(page.get_by_text("Despesa prevista criada.")).to_be_visible()
        method, path, body = writes[-1]
        assert (method, path) == ("POST", "/budgets/b1/lines"), writes[-1]
        assert body["amount_cents"] == 12300 and body["amount_includes_vat"] is True and body["vat_rate"] == "23" and body["periodicity"] == "mensal", body
        assert body["increases"] == [{"from": "2026-09-01", "percent": "3.5"}] and body["exceptions"] == [{"date": "2026-08-20", "skip": True}], body
        # Associação ao realizado: proposta forte, outra ocorrência com valor parcial, despesas sem previsão.
        expect(kpis).to_contain_text("Associado ao orçamento")
        expect(kpis).to_contain_text("8 previsto(s) sem documento")
        expect(grid).to_contain_text("8 sem documento")
        page.get_by_role("navigation", name="Áreas do orçamento").get_by_role("button", name="Associações").click()
        proposals = page.get_by_role("table", name="Propostas de associação")
        expect(proposals).to_contain_text("Forte · 99 %")
        expect(proposals).to_contain_text("Mesmo fornecedor (NIF)")
        expect(page.get_by_role("table", name="Despesas sem previsão")).to_contain_text("Sem despesa prevista desta rubrica")
        audit(page)
        page.screenshot(path=str(OUT / f"associacoes-{theme}-{width}.png"), full_page=True)
        proposals.get_by_role("button", name="Associar FT R/10").click()
        expect(page.get_by_text("Associação confirmada.")).to_be_visible()
        assert writes[-1] == ("POST", "/budgets/b1/links", {"source_kind": "purchase", "source_id": "p10", "category_id": "cat-rent", "line_id": "l1",
                                                            "rule_date": "2026-10-05", "confidence": 99, "reasons": candidate["reasons"]}), writes[-1]
        proposals.get_by_role("button", name="Escolher outra para FT R/10").click()
        choose = page.get_by_role("dialog", name="Escolher despesa prevista")
        choose.get_by_label("Renda da loja · 05/09/2026", exact=False).check()
        choose.get_by_label("Valor a associar (€)").fill("500,00")
        choose.get_by_role("button", name="Associar").click()
        expect(page.get_by_text("Associação confirmada.")).to_be_visible()
        assert writes[-1][2]["rule_date"] == "2026-09-05" and writes[-1][2]["amount_cents"] == 50000, writes[-1]
        page.get_by_role("button", name="Selecionar as fortes").click()
        page.get_by_role("button", name="Associar 1 selecionada(s)").click()
        expect(page.get_by_text("1 associação(ões) confirmada(s).")).to_be_visible()
        assert writes[-1][1] == "/budgets/b1/links/batch" and len(writes[-1][2]["items"]) == 1, writes[-1]
        # Ficha da despesa: documentos associados e anulação com motivo.
        page.get_by_role("navigation", name="Áreas do orçamento").get_by_role("button", name="Despesas previstas").click()
        grid.get_by_role("button", name="Renda da loja").click()
        line_drawer = page.get_by_role("dialog", name="Despesa prevista")
        links_table = line_drawer.get_by_role("table", name="Associações da despesa")
        expect(links_table).to_contain_text("FT R/1")
        expect(links_table).to_contain_text("Por pagar")
        links_table.get_by_role("button", name="Anular a associação de FT R/1").click()
        undo = page.get_by_role("dialog", name="Anular associação")
        undo.get_by_label("Motivo").fill("Documento errado")
        undo.get_by_role("button", name="Anular associação").click()
        expect(page.get_by_text("Associação anulada.")).to_be_visible()
        assert writes[-1] == ("POST", "/budget-links/k1/undo", {"reason": "Documento errado"}), writes[-1]
        # Aprovar o rascunho.
        page.get_by_role("button", name="Aprovar", exact=True).click()
        dialog = page.get_by_role("dialog", name="Aprovar orçamento")
        expect(dialog).to_contain_text("fica bloqueada")
        dialog.get_by_role("button", name="Aprovar", exact=True).click()
        expect(page.get_by_text("Orçamento aprovado.")).to_be_visible()
        assert writes[-1] == ("POST", "/budgets/b1/approve", {"revision": 0, "reason": ""}), writes[-1]
        # Sem orçamento: criar a partir do ano anterior com atualização.
        state["data"] = empty
        page.reload()
        create = page.get_by_role("region", name="Criar orçamento")
        expect(create).to_contain_text("Sem orçamento de 2026 da empresa")
        create.get_by_label("Copiar de").select_option("b2025")
        create.get_by_label("Atualização dos valores (%)").fill("3")
        audit(page)
        create.get_by_role("button", name="Criar orçamento de 2026").click()
        expect(page.get_by_text("Orçamento criado em rascunho.")).to_be_visible()
        assert writes[-1] == ("POST", "/budgets", {"year": 2026, "store_id": None, "name": "", "note": "", "copy_from_id": "b2025", "update_percent": "3"}), writes[-1]
        assert not [w for w in writes if w[0] == "inesperada"], writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: orçamento (indicadores com detalhe, gráficos, previsto vs realizado, grelha, ficha rápida com IVA/aumento/exceção, aprovação, cópia do ano anterior), temas e larguras.")
