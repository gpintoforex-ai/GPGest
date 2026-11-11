"""Dashboard — versão nova lado a lado com a atual, com respostas sintéticas isoladas; nada é gravado.

Verifica: interruptor nos dois sentidos, cartões principais, separadores das áreas existentes, cartões da
Evolução (incluindo Vendas vs compras, Previsão de IVA e Alertas), «Ver tabela», personalização dos cartões,
visão de trabalho em «Encomendas e abastecimento», temas, larguras e contraste. Requer o Vite em 127.0.0.1:5174.
"""
import copy
import re
import runpy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "scripts/test-dashboard-scope-ui.py"), run_name="fixture")
ns = fixture["ns"]
OUT = ROOT / ".local/dashboard-modern"
OUT.mkdir(parents=True, exist_ok=True)
boot = copy.deepcopy(ns["boot"])
boot["user"]["role"] = "gestor"
boot["integration"] = {"mode": "local", "provider": "none"}
boot["company"]["id"] = "dashboard-modern-fixture"

labels = ["Set", "Out", "Nov"]
data = fixture["overview"](1234500)
data["permissions"]["costs"] = True
data["sales"].update(status="ok", vat_basis="sem IVA", unit="month", change_percent=12.4,
                     points=[{"label": label, "current_cents": v, "previous_cents": p} for label, v, p in zip(labels, [400000, 350000, 484500], [300000, 320000, 400000])],
                     meta={"definition": "Faturação do período", "formula": "Soma dos documentos", "source": "SAF-T de vendas importado"})
data["expenses"] = {"status": "ok", "vat_basis": "sem IVA dedutível", "unit": "month", "total_cents": 600000, "previous_cents": 500000, "change_percent": 20.0,
                    "unclassified_cents": 25000, "rows": [{"category_id": "c1", "name": "Mercadorias", "amount_cents": 575000, "previous_cents": 480000},
                                                          {"category_id": None, "name": "Por classificar", "amount_cents": 25000, "previous_cents": 20000}],
                    "points": [{"label": label, "amount_cents": v} for label, v in zip(labels, [200000, 180000, 220000])]}
# Margem parcial: 35,5 % sobre 9.888,00 € com custo de 12.345,00 € de vendas de mercadorias (cobertura 80,1 %).
data["margin"] = {
    "status": "ok", "source": "saft", "sales_cents": 1234500, "covered_cents": 988800, "uncovered_cents": 245700, "cost_cents": 637776,
    "cost_coverage_percent": 80.1, "gross_margin_cents": 351024, "gross_margin_percent": 35.5, "level": "partial", "partial": True,
    "estimated_cents": 120000, "zero_cost_cents": 5000, "services_cents": 30000, "service_lines": 2, "review_articles": 3, "negative_articles": 1,
    "incomplete_documents": 0, "cost_restricted": False, "unit": "month",
    "reasons": [{"reason": "no_cost", "label": "Artigo sem custo registado", "sales_cents": 200000, "articles": 2},
                {"reason": "zero_cost", "label": "Custo igual a zero", "sales_cents": 5000, "articles": 1}],
    "points": [{"label": label, "sales_cents": s, "covered_cents": c, "margin_cents": m, "margin_percent": mp, "coverage_percent": cp}
               for label, s, c, m, mp, cp in zip(labels, [400000, 350000, 484500], [320000, 280000, 388800], [112000, 98000, 141024], [35.0, 35.0, 36.3], [80.0, 80.0, 80.2])],
    "rows": [{"name": "Suplementos", "sales_cents": 900000, "articles": 12, "covered_cents": 800000, "cost_coverage_percent": 89, "margin_cents": 300000}],
    "article_margins": [{"key": "a1", "code": "A1", "name": "Ómega 3", "sales_cents": 300000, "covered_cents": 300000, "margin_cents": 120000, "margin_percent": 40.0}],
    "negative": [{"key": "a9", "code": "A9", "name": "Artigo em promoção", "sales_cents": 10000, "margin_cents": -1500, "margin_percent": -15.0}],
    "meta": {"definition": "Margem bruta das vendas de mercadorias", "formula": "Vendas com custo − custo das mercadorias vendidas", "source": "SAF-T e movimentos de stock"},
}
ARTICLES = {"source": "saft", "reasons": {"no_cost": "Artigo sem custo registado", "zero_cost": "Custo igual a zero"},
            "sources": {"stock": "Saída de stock", "estimated": "Custo médio reconstituído"},
            "summary": {k: data["margin"][k] for k in ("sales_cents", "covered_cents", "uncovered_cents", "cost_coverage_percent", "gross_margin_cents", "gross_margin_percent", "level")},
            "rows": [
                {"key": "a2", "article_id": "a2", "code": "B2", "name": "Gel de aloé", "family": "Cosmética", "quantity": "10", "sales_cents": 150000, "covered_cents": 0, "uncovered_cents": 150000,
                 "cost_cents": 0, "unit_cost": None, "cost_source": None, "margin_cents": None, "margin_percent": None, "reasons": ["no_cost"], "needs_review": True, "erp_status": "Artigo local"},
                {"key": "a3", "article_id": "a3", "code": "C3", "name": "Amostra oferta", "family": "Suplementos", "quantity": "5", "sales_cents": 5000, "covered_cents": 5000, "uncovered_cents": 0,
                 "cost_cents": 0, "unit_cost": "0.0000", "cost_source": "stock", "margin_cents": 5000, "margin_percent": 100.0, "reasons": ["zero_cost"], "needs_review": True, "erp_status": "Artigo local"},
                {"key": "a1", "article_id": "a1", "code": "A1", "name": "Ómega 3", "family": "Suplementos", "quantity": "30", "sales_cents": 300000, "covered_cents": 300000, "uncovered_cents": 0,
                 "cost_cents": 180000, "unit_cost": "60.0000", "cost_source": "stock", "margin_cents": 120000, "margin_percent": 40.0, "reasons": [], "needs_review": False, "erp_status": "Artigo local"},
            ]}
data["vat"] = {"status": "ok", "balance_cents": 103000, "output_cents": 283000, "input_cents": 200000, "deductible_cents": 180000,
               "sales_documents": 40, "purchase_documents": 12, "period": "trimestral",
               "meta": {"definition": "Saldo estimado de IVA", "formula": "IVA liquidado − IVA dedutível", "source": "SAF-T de vendas e documentos de compra do Mapa de IVA"}}
data["treasury"].update(status="ok", accounts=[{"name": "Banco", "kind": "bank", "balance_cents": 50000, "stale": False}],
                        flows=[{"label": label, "in_cents": v, "out_cents": o} for label, v, o in zip(labels, [300000, 280000, 350000], [250000, 200000, 260000])])
data["top_articles"] = {"status": "ok", "rows": [{"code": "P001", "name": "Artigo de teste", "quantity": "5", "sales_cents": 5000}]}
data["attention"] = [
    {"key": "overdue", "severity": "critical", "fact": "2 pagamentos vencidos", "values": "1.200,00 €", "impact": "Juros", "action": "Pagar", "page": "Movimentos previstos"},
    {"key": "unclassified", "severity": "warning", "fact": "3 compras sem rubrica", "values": "250,00 €", "impact": "IVA", "action": "Classificar", "page": "Despesas por rubrica"},
]
writes = []


def intercept(route):
    path = urlsplit(route.request.url).path
    if route.request.method != "GET":
        writes.append(path)
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
    if path == "/api/bootstrap":
        return route.fulfill(json=boot)
    if path == "/api/dashboard/overview":
        return route.fulfill(json=data)
    if path == "/api/order-cycle/analytics":
        return route.fulfill(json=fixture["fixture"]["analytics"])
    if path == "/api/dashboard/margin-articles":
        return route.fulfill(json=ARTICLES)
    if path == "/api/stock/articles/a2/details":
        return route.fulfill(json={"current": {"average_cost": None, "last_cost": None}})
    if path == "/api/purchase-receiving/cost-history":
        return route.fulfill(json={"articles": {"a2": []}, "settings": {}})
    if path == "/api/stock/movements":
        return route.fulfill(json={"rows": [{"id": "m1", "occurred_on": "2026-11-02", "kind": "sale", "quantity": "-10", "unit_cost": None}], "more": False})
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 1440), ("light", 1024), ("dark", 390)):
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Dashboard")
        # A versão atual abre por omissão; o interruptor leva à nova.
        expect(page.locator(".dash-kpi").filter(has_text="Previsão de IVA")).to_be_visible()
        # Aviso bem visível no topo do Dashboard atual, com o acesso à nova versão.
        banner = page.get_by_role("region", name="Nova versão do Dashboard")
        expect(banner).to_be_visible()
        assert banner.bounding_box()["y"] < page.locator(".priority-heading").bounding_box()["y"]
        page.screenshot(path=str(OUT / f"atual-{theme}-{width}.png"))
        banner.get_by_role("button", name="Abrir a nova versão").click()
        expect(page.get_by_role("heading", name="Dashboard", level=1)).to_be_visible()
        expect(banner).to_have_count(0)
        hero = page.get_by_role("group", name="Indicadores principais")
        for text in ("Vendas (sem IVA)", "12.345,00", "+12,4%", "Custos operacionais", "6.000,00", "Tesouraria", "500,00",
                     "Margem bruta apurada (parcial)", "35,5 %", "Margem parcial", "Lucro bruto 3.510,24", "9.888,00", "Cobertura de custos 80,1 %", "sem custo 2.457,00"):
            expect(hero).to_contain_text(text)
        expect(hero.get_by_role("progressbar", name="Cobertura de custos")).to_have_attribute("aria-valuenow", "80.1")
        expect(hero.get_by_role("button", name="Ver custos e proveitos")).to_be_visible()
        tabs = page.get_by_role("tablist", name="Áreas do dashboard")
        for name in ("Resumo do negócio", "Evolução", "Margens e custos", "Artigos mais vendidos", "O que precisa da sua atenção", "Ações rápidas", "Operação de caixa", "Encomendas e abastecimento"):
            expect(tabs.get_by_role("tab", name=name)).to_be_visible()
        # Margens e custos: indicadores, aviso de margem parcial, motivos, evolução e margem negativa.
        tabs.get_by_role("tab", name="Margens e custos").click()
        panel = page.locator(".margin-panel")
        for text in ("Margem bruta", "35,5 %", "Lucro bruto", "3.510,24", "Cobertura de custos", "80,1 %", "Vendas sem custo conhecido", "2.457,00", "19,9 %",
                     "Artigos que necessitam de revisão", "Não representa a margem global", "Artigo sem custo registado", "Serviços fora da margem: 300,00",
                     "Evolução da margem e da cobertura de custos", "Margem por artigo", "Artigo em promoção", "-15,00"):
            expect(panel).to_contain_text(text)
        page.screenshot(path=str(OUT / f"margens-{theme}-{width}.png"), full_page=True)
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        # Grelha dos artigos sem custo: por omissão só os que precisam de revisão; filtros, ordenação e correção assistida.
        panel.get_by_role("button", name="Ver artigos sem custo").click()
        grid_dialog = page.get_by_role("dialog", name="Artigos vendidos e custos")
        table = grid_dialog.get_by_role("table", name="Artigos vendidos com custo e margem")
        expect(table.locator("tbody tr")).to_have_count(2)
        expect(table).to_contain_text("Artigo sem custo registado")
        expect(table).to_contain_text("Custo igual a zero")
        grid_dialog.get_by_label("Mostrar").select_option("all")
        expect(table.locator("tbody tr")).to_have_count(3)
        grid_dialog.get_by_role("button", name="Vendas sem IVA").click()
        expect(table.locator("tbody tr").first).to_contain_text("Ómega 3")
        grid_dialog.get_by_label("Pesquisar").fill("aloé")
        expect(table.locator("tbody tr")).to_have_count(1)
        table.get_by_role("button", name="Corrigir").click()
        expect(table).to_contain_text("Registe a compra por uma receção")
        expect(table).to_contain_text("Sem compras registadas.")
        expect(table).to_contain_text("Venda")
        page.screenshot(path=str(OUT / f"artigos-sem-custo-{theme}-{width}.png"), full_page=True)
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        table.get_by_role("button", name="Ocultar").click()
        expect(table).not_to_contain_text("Registe a compra por uma receção")
        grid_dialog.locator(".ui-dialog-actions").get_by_role("button", name="Fechar").click()
        expect(grid_dialog).to_have_count(0)
        # «Ver custos e proveitos» abre os Mapas de gestão no separador Custos e proveitos, com o período do painel.
        hero.get_by_role("button", name="Ver custos e proveitos").click()
        expect(page).to_have_url(re.compile(r"pagina=Mapas"))
        expect(page).to_have_url(re.compile(r"area\.mapas=costs"))
        page.go_back()
        expect(page.get_by_role("tablist", name="Áreas do dashboard")).to_be_visible()
        # O Resumo do negócio mantém os indicadores atuais.
        expect(page.locator(".dash-kpi").filter(has_text="Previsão de IVA")).to_contain_text("1.030,00")
        tabs.get_by_role("tab", name="Evolução").click()
        grid = page.locator(".dash-cards")
        for title in ("Faturação por mês", "Recebimentos e pagamentos (com IVA)", "A receber e a pagar por vencimento", "Custos e despesas operacionais", "Evolução de vendas e custos operacionais", "Alertas e tarefas"):
            expect(grid).to_contain_text(title)
        vat = page.get_by_label("Previsão de IVA", exact=True)
        for text in ("Saldo estimado a pagar", "1.030,00", "Periodicidade configurada: trimestral", "2.830,00", "1.800,00", "200,00", "40 de venda · 12 de compra", "Previsão provisória", "não o apuramento da declaração periódica"):
            expect(vat).to_contain_text(text)
        versus = page.locator(".dash-card-slot.is-versus")
        versus.get_by_role("button", name="Ver tabela").click()
        expect(versus.get_by_role("row").filter(has_text="Nov")).to_contain_text("2.200,00")
        page.screenshot(path=str(OUT / f"evolucao-{theme}-{width}.png"), full_page=True)
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."
        # Personalizar: ocultar um cartão da Evolução e repor.
        page.get_by_role("button", name="Personalizar").click()
        page.get_by_label("Personalizar o painel").get_by_role("checkbox", name="Evolução de vendas e custos operacionais", exact=True).uncheck()
        expect(page.locator(".dash-card-slot.is-versus")).to_have_count(0)
        page.get_by_label("Personalizar o painel").get_by_role("checkbox", name="Evolução de vendas e custos operacionais", exact=True).check()
        expect(page.locator(".dash-card-slot.is-versus")).to_have_count(1)
        page.get_by_role("button", name="Personalizar").click()
        # Alertas: «Ver todos» abre a área completa, com impacto e ação.
        page.locator(".dash-alerts").get_by_role("button", name="Ver todos com impacto e ação recomendada").click()
        expect(page.locator(".dash-note").first).to_contain_text("Impacto:")
        tabs.get_by_role("tab", name="Encomendas e abastecimento").click()
        expect(page.get_by_role("heading", name="Pedidos, abastecimento e trabalho por área")).to_be_visible()
        page.screenshot(path=str(OUT / f"encomendas-{theme}-{width}.png"), full_page=True)
        # De volta à versão atual, que se mantém.
        page.get_by_role("button", name="Voltar à versão atual").click()
        expect(page.locator(".priority-heading h1")).to_be_visible()
        page.get_by_role("tab", name="Resumo do negócio").click()
        expect(page.locator(".dash-kpi").filter(has_text="Previsão de IVA")).to_be_visible()
        expect(page.get_by_role("button", name="Experimentar a nova versão")).to_be_visible()
        assert not errors, errors
        page.close()
    browser.close()
assert not writes, writes
print("PASS: versão nova do dashboard com os mesmos dados (cartões, separadores, Evolução, IVA, alertas, encomendas), personalização, temas e larguras; versão atual intacta; sem escritas.")
