"""Proveitos, custos e margens por rubrica (Mapas de gestão): indicadores com variação e fórmula, tabela mensal com
grupos, subtotais e colunas fixas, filtros (pesquisa, ano e meses, tipo, sem movimentos, limpar), comparação com o
ano anterior e com o orçamento, alertas e respetivos limites, movimentos de cada valor com correção da rubrica
(origem «mapas_de_gestao»), gráficos clicáveis, exportação; temas claro e escuro, computador e telemóvel, contraste.

Dados fictícios; a API é simulada e só as escritas esperadas são aceites. Requer o Vite em 127.0.0.1:5174.
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
OUT = ROOT / ".local/management-costs-ui"
OUT.mkdir(parents=True, exist_ok=True)

KEYS = ["2026-07", "2026-08", "2026-09", "2026-10"]
LABELS = ["Julho 2026", "Agosto 2026", "Setembro 2026", "Outubro 2026"]


def values(*cents):
    return dict(zip(KEYS, cents))


def rubric(identifier, name, group, cents, icon="tag", fixed=False, nature=""):
    return {"category_id": identifier, "category_name": name, "values": values(*cents), "total_cents": sum(cents), "group": group, "nature": nature,
            "fixed": fixed, "color": "#245fc5", "icon": icon, "parent_name": None, "documents": values(*(1 if c else 0 for c in cents)),
            "records": sum(1 for c in cents if c)}


COSTS = [
    rubric("c-merc", "Mercadorias", "purchases", (500000, 410000, 560000, 365000), "package", nature="mercadorias"),
    rubric("c-renda", "Renda", "operating", (26439, 26439, 26403, 26403), "landmark", fixed=True),
    rubric("c-seg", "Seguros", "operating", (24844, 24844, 27593, 60000), "shield", fixed=True),
    rubric("c-com", "Comunicações", "operating", (6681, 8375, 8372, 8594), "phone"),
    rubric(None, "Por classificar", "operating", (0, 0, 3000, 0)),
]
REVENUE = values(800913, 937662, 869251, 815111)


def costs_payload():
    total = {k: sum(r["values"][k] for r in COSTS) for k in KEYS}
    margin = {k: REVENUE[k] - total[k] for k in KEYS}
    subtotal = {g: {k: sum(r["values"][k] for r in COSTS if r["group"] == g) for k in KEYS} for g in ("purchases", "operating")}
    costs_total, revenue_total = sum(total.values()), sum(REVENUE.values())
    kpis = {"revenue_cents": revenue_total, "costs_cents": costs_total, "margin_cents": revenue_total - costs_total,
            "margin_percent": round((revenue_total - costs_total) * 100 / revenue_total, 1), "merchandise_cents": sum(COSTS[0]["values"].values()),
            "investment_cents": 0, "operating_cents": sum(subtotal["operating"].values()),
            "fixed_cents": sum(r["total_cents"] for r in COSTS if r["fixed"]), "unclassified_cents": 3000, "state_payments_cents": 65182}
    prev_kpis = {**kpis, "revenue_cents": 3100000, "costs_cents": 2600000, "margin_cents": 500000, "margin_percent": 16.1, "merchandise_cents": 1700000,
                 "fixed_cents": 200000}
    return {
        "regime": "normal", "group": "month", "periods": [{"key": k, "label": label} for k, label in zip(KEYS, LABELS)],
        "revenue": REVENUE, "costs": COSTS, "total_costs": total, "margin": margin, "subtotals": subtotal,
        "purchase_vat": {"input": values(1000, 1000, 1000, 1000), "deductible": values(800, 800, 800, 800), "non_deductible": values(200, 200, 200, 200),
                         "totals": {"input_cents": 4000, "deductible_cents": 3200, "non_deductible_cents": 800}},
        "sales_vat": values(184210, 215662, 199928, 187475), "state_payments": values(0, 0, 65182, 0),
        "totals": {"revenue_cents": revenue_total, "costs_cents": costs_total, "margin_cents": revenue_total - costs_total}, "kpis": kpis,
        "revenue_source": "saft", "revenue_missing_documents": 0, "excluded": [{"category_id": "c-iva", "category_name": "IVA", "total_cents": 65182}],
        "outside": [{"category_id": "c-iva", "category_name": "IVA", "kind": "fiscal", "values": values(0, 0, 65182, 0), "total_cents": 65182, "color": "#805400", "icon": "landmark"}],
        "previous": {"has_data": True, "periods": [{"key": k, "label": k} for k in KEYS], "revenue": values(750000, 800000, 790000, 760000),
                     "total_costs": values(650000, 640000, 660000, 650000), "margin": values(100000, 160000, 130000, 110000),
                     "subtotals": {"purchases": values(420000, 420000, 430000, 430000), "operating": values(230000, 220000, 230000, 220000)},
                     "costs": {"c-merc": {"values": values(420000, 420000, 430000, 430000), "total_cents": 1700000},
                               "c-seg": {"values": values(24000, 24000, 24000, 24000), "total_cents": 96000}},
                     "only_previous": [], "kpis": prev_kpis, "totals": {}},
        "budget": {"available": True, "name": "Orçamento 2026", "version": 1, "status_label": "Aprovado", "partial": False,
                   "values": {"c-seg": values(25000, 25000, 25000, 25000), "c-renda": values(26400, 26400, 26400, 26400)},
                   "totals": values(51400, 51400, 51400, 51400)},
        "alerts": [
            {"id": "a1", "kind": "unclassified", "tone": "warning", "category_id": None, "category_name": "Por classificar", "period": None, "period_label": None,
             "title": "Despesas sem classificação", "message": "30,00 € em 1 registo(s) sem rubrica.", "amount_cents": 3000, "reference_cents": None, "ids": []},
            {"id": "a2", "kind": "duplicate", "tone": "warning", "category_id": "c-seg", "category_name": "Seguros", "period": "2026-08", "period_label": "Agosto 2026",
             "title": "Possível duplicado", "message": "2 registos de Seguradora Exemplo com o mesmo valor (124,22 €) em 5 dia(s) ou menos.",
             "amount_cents": 12422, "reference_cents": None, "ids": ["p-dup1", "p-dup2"]},
            {"id": "a3", "kind": "above_average", "tone": "info", "category_id": "c-seg", "category_name": "Seguros", "period": "2026-10", "period_label": "Outubro 2026",
             "title": "Acima da média", "message": "Seguros: 600,00 € em Outubro 2026, 133% acima da média.", "amount_cents": 60000, "reference_cents": 25760, "ids": []},
        ],
        "alert_settings": {"above_average_percent": 50, "month_change_percent": 50, "increase_percent": 25, "minimum_cents": 10000, "duplicate_days": 5, "revision": 0},
        "empty_rubrics": [{"category_id": "c-luz", "category_name": "Luz", "group": "operating", "fixed": False, "nature": "", "color": "#805400", "icon": "zap", "parent_name": None}],
        "notes": ["Margem de gestão estimada = proveitos sem IVA − custos deste mapa."],
    }


def move(identifier, label, entity, category, name, cents, day="2026-10-06"):
    return {"id": identifier, "date": day, "payment_date": None, "label": label, "entity": entity, "source": "Compra", "origin": "purchase", "origin_label": "Compra",
            "shares": [{"category_id": category, "name": name, "base_cents": cents, "tax_cents": 0, "amount_cents": cents}], "amount_cents": cents,
            "base_cents": cents, "tax_cents": 0, "category_amount_cents": cents, "mode": "purchase", "editable": True, "blocked": "", "state": "por_pagar",
            "account_id": None, "account": "", "annotation": {"cost_center_id": None, "notes": "", "revision": 0}}


MOVES = [move("p-seg10", "FS 5", "Seguradora Exemplo", "c-seg", "Seguros", 60000),
         move("p-dup1", "FS 2", "Seguradora Exemplo", "c-seg", "Seguros", 12422, "2026-08-03"),
         move("p-dup2", "FS 3", "Seguradora Exemplo", "c-seg", "Seguros", 12422, "2026-08-05"),
         move("p-renda", "FR 10", "Senhorio Exemplo", "c-renda", "Renda", 26403)]
CATEGORIES = [{"id": "c-merc", "name": "Mercadorias", "parent_id": None, "color": "#245fc5", "icon": "package"},
              {"id": "c-renda", "name": "Renda", "parent_id": None, "color": "#245fc5", "icon": "landmark"},
              {"id": "c-seg", "name": "Seguros", "parent_id": None, "color": "#245fc5", "icon": "shield"}]
state = {"maps": 0}
requests, writes = [], []


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path, route.request.method
    params = {k: v[0] for k, v in parse_qs(url.query).items()}
    if path == "/api/bootstrap":
        boot = copy.deepcopy(ns["boot"])
        boot["user"]["role"] = "administrador"
        return route.fulfill(json=boot)
    if path == "/api/management/costs-revenues":
        state["maps"] += 1
        requests.append(params)
        return route.fulfill(json=costs_payload())
    if path == "/api/management/alert-settings":
        if method == "PUT":
            writes.append((path, route.request.post_data_json))
        return route.fulfill(json={**costs_payload()["alert_settings"], "can_edit": True})
    if path == "/api/rubric-movements" and method == "GET":
        requests.append({"movements": True, **params})
        return route.fulfill(json={"rows": MOVES, "total_cents": sum(m["amount_cents"] for m in MOVES), "can_edit_payments": True, "categories": CATEGORIES,
                                   "cost_centers": [], "accounts": []})
    if path == "/api/rubric-movements/preview":
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json={"fingerprint": "f" * 64, "target": "Renda", "items": [{"id": "p-seg10", "mode": "purchase", "blocked": "", "before": ["c-seg"],
                                   "before_names": ["Seguros"], "label": "FS 5", "entity": "Seguradora Exemplo", "source": "Compra", "amount_cents": 60000}],
                                   "count": 1, "total_cents": 60000, "blocked": 0, "approval": [], "preserved": ["Valores, IVA, datas e documentos"]})
    if path == "/api/rubric-movements/apply":
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json={"applied": 1, "skipped": 0})
    if path.startswith("/api/"):
        return ns["intercept"](route)
    return route.continue_()


def audit(page):
    page.mouse.move(1, 1)
    page.wait_for_timeout(400)
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")
    wide = page.evaluate("""() => document.documentElement.scrollWidth > innerWidth ? [...document.querySelectorAll('body *')]
      .filter(e => e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('.mc-scroll, .table-wrap, .dash-plot'))
      .slice(0, 8).map(e => `${e.tagName}.${typeof e.className === 'string' ? e.className : ''} → ${Math.round(e.getBoundingClientRect().right)}`) : null""")
    assert not wide, f"A página transborda na horizontal: {wide}"


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 390)):
        requests.clear()
        writes.clear()
        state["maps"] = 0
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT", accept_downloads=True)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Mapas%20de%20gest%C3%A3o")
        kpis = page.get_by_role("region", name="Indicadores do período")
        expect(kpis).to_contain_text("Proveitos")
        # Ano e intervalo de meses: julho a outubro de 2026.
        toolbar = page.locator(".mc-toolbar")
        toolbar.get_by_label("Ano", exact=True).select_option("2026")
        toolbar.get_by_label("Mês inicial", exact=True).select_option("7")
        toolbar.get_by_label("Mês final", exact=True).select_option("10")
        page.wait_for_timeout(300)
        assert any(r.get("start") == "2026-07-01" and r.get("end") == "2026-10-31" and r.get("compare") == "true" for r in requests), requests
        # Indicadores: valor, variação face ao ano anterior e fórmula.
        for text in ("Proveitos", "Custos", "Margem de gestão estimada", "Compras de mercadorias", "Custos fixos", "vs. ano anterior", "dos proveitos"):
            expect(kpis).to_contain_text(text)
        expect(kpis.get_by_role("button", name="Como se calcula: Custos fixos")).to_be_visible()
        region = page.get_by_role("region", name="Proveitos, custos e margens por rubrica (tabela com deslocamento)")
        table = region.locator("table")
        for text in ("Proveitos (vendas sem IVA)", "Compras de mercadorias e investimentos", "Despesas operacionais", "Total de custos",
                     "Margem de gestão estimada", "Margem % (sobre proveitos)", "Pagamentos ao Estado (valor pago)", "IVA liquidado nas vendas"):
            expect(table).to_contain_text(text)
        # Valor de uma célula → movimentos dessa rubrica e desse mês.
        table.get_by_role("button", name="Seguros, Outubro 2026").click()
        panel = page.get_by_role("dialog", name="Movimentos da rubrica")
        expect(panel).to_contain_text("Movimentos · Seguros")
        last = [r for r in requests if r.get("movements")][-1]
        assert (last["start"], last["end"], last["view"]) == ("2026-10-01", "2026-10-31", "costs"), last
        # Correção da rubrica a partir do mapa: fica na auditoria com a origem «mapas_de_gestao» e o mapa recarrega.
        rows = panel.get_by_role("table", name="Movimentos da rubrica")
        rows.get_by_label("Selecionar FS 5").check()
        batch = panel.get_by_role("region", name="Alterar rubrica dos selecionados")
        batch.get_by_label("Nova rubrica").select_option("c-renda")
        batch.get_by_role("button", name="Pré-visualizar").click()
        review = panel.get_by_role("region", name="Pré-visualização da alteração de rubrica")
        review.get_by_label("Motivo da alteração").fill("Pagamento era da renda da loja")
        before = state["maps"]
        review.get_by_role("button", name="Aplicar a 1 movimento(s)").click()
        expect(panel.get_by_text("passaram para «Renda»", exact=False)).to_be_visible()
        assert writes[-1][0] == "/api/rubric-movements/apply" and writes[-1][1]["origin"] == "mapas_de_gestao", writes[-1]
        page.wait_for_timeout(300)
        assert state["maps"] > before, "O mapa tem de recarregar depois da correção."
        # Exportar os movimentos da rubrica.
        with page.expect_download() as download:
            panel.get_by_role("button", name="Exportar movimentos").click()
        assert download.value.suggested_filename.startswith("movimentos-seguros-2026-10-01"), download.value.suggested_filename
        page.keyboard.press("Escape")
        expect(panel).to_have_count(0)
        # Pesquisa: tabela e indicador de custos coerentes com o filtro.
        page.get_by_label("Pesquisar rubrica").fill("segu")
        expect(table).to_contain_text("Total das rubricas filtradas")
        expect(table).not_to_contain_text("Comunicações")
        expect(kpis).to_contain_text("Custos (filtro ativo)")
        page.get_by_role("button", name="Limpar filtros").click()
        expect(table).to_contain_text("Comunicações")
        page.get_by_label("Tipo de rubrica").select_option("fixed")
        expect(table).not_to_contain_text("Comunicações")
        expect(table).to_contain_text("Renda")
        page.get_by_label("Tipo de rubrica").select_option("")
        page.get_by_label("Rubricas sem movimentos").check()
        expect(table).to_contain_text("Luz")
        # Grupos: recolher e expandir.
        table.get_by_role("button", name="Despesas operacionais").click()
        expect(table).not_to_contain_text("Comunicações")
        table.get_by_role("button", name="Despesas operacionais").click()
        # Comparação com o ano anterior e com o orçamento.
        page.get_by_label("Comparar com o ano anterior").check()
        expect(table.get_by_role("columnheader", name="Ano anterior")).to_be_visible()
        page.get_by_label("Comparar com o orçamento").check()
        expect(table).to_contain_text("orç. 250,00")
        audit(page)
        page.screenshot(path=str(OUT / f"tabela-{theme}-{width}.png"), full_page=True)
        # Alertas: o duplicado abre só os dois registos; limites configuráveis.
        page.get_by_text("alerta(s) para rever").click()
        page.get_by_role("button", name="Possível duplicado", exact=False).click()
        panel = page.get_by_role("dialog", name="Movimentos da rubrica")
        expect(panel.get_by_role("table", name="Movimentos da rubrica").locator("tbody tr")).to_have_count(2)
        page.keyboard.press("Escape")
        page.get_by_role("button", name="Limites dos alertas").click()
        limits = page.get_by_role("dialog", name="Limites dos alertas")
        limits.get_by_label("Acima da média dos restantes períodos (%)").fill("80")
        limits.get_by_label("Valor mínimo da diferença (€)").fill("250,00")
        limits.get_by_role("button", name="Guardar limites").click()
        expect(limits).to_have_count(0)
        assert writes[-1] == ("/api/management/alert-settings", {"above_average_percent": 80, "month_change_percent": 50, "increase_percent": 25,
                                                                 "minimum_cents": 25000, "duplicate_days": 5, "revision": 0}), writes[-1]
        # Exportar a tabela filtrada (Excel).
        with page.expect_download() as download:
            page.get_by_role("button", name="Exportar Excel").click()
        content = Path(download.value.path()).read_text(encoding="utf-8-sig")
        assert "Total de custos" in content and "Margem de gestão estimada" in content and "8009,13" in content, content[:400]
        # Gráficos: barras clicáveis abrem os movimentos.
        page.get_by_role("button", name="Gráficos").click()
        charts = page.get_by_role("region", name="Gráficos")
        expect(charts).to_contain_text("Margem de gestão estimada")
        expect(charts).to_contain_text("Custos: período atual e ano anterior")
        charts.get_by_role("button", name="Seguros", exact=False).first.click()
        expect(page.get_by_role("dialog", name="Movimentos da rubrica")).to_contain_text("Movimentos · Seguros")
        page.keyboard.press("Escape")
        audit(page)
        page.screenshot(path=str(OUT / f"graficos-{theme}-{width}.png"), full_page=True)
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: proveitos, custos e margens (indicadores, tabela, filtros, comparação, orçamento, alertas e limites, movimentos com correção, "
      "gráficos, exportação), temas, larguras e contraste.")
print(json.dumps({"writes": len(writes)}))
