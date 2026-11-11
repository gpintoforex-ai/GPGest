"""Centro de Conferência: secções por resolver, abrir o módulo de origem, aprovar as seguras e automatizações.

Respostas simuladas; só as escritas esperadas são aceites e verificadas. Requer o Vite em 127.0.0.1:5174.
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
OUT = ROOT / ".local/conference-ui"
OUT.mkdir(parents=True, exist_ok=True)
boot = copy.deepcopy(ns["boot"])
boot["user"]["role"] = "administrador"
boot["company"]["id"] = "conference-fixture"


def section(key, title, count, tone, page, items=(), amount=None, params=None, note="", safe=None):
    return {"key": key, "title": title, "count": count, "amount_cents": amount, "tone": tone if count else "success", "page": page,
            "params": params or {}, "items": list(items), "note": note, "safe": safe}


item = {"label": "Elétrica Lusa · FT E/11", "date": "2026-11-09", "amount_cents": 21000, "detail": "Eletricidade (99 %)"}
safe_budget = {"count": 2, "endpoint": "/budgets/b1/links/batch", "threshold": 85,
               "body": {"items": [{"source_kind": "purchase", "source_id": "p1", "category_id": "c", "line_id": "l1", "rule_date": "2026-11-05", "confidence": 99, "reasons": []}]}}
overview = {"start": "2026-08-12", "end": "2026-11-10", "total": 9, "notes": ["Pagamentos, reconciliação bancária, lançamentos e escritas no ERP nunca são automáticos."],
            "sections": [section("novos", "Documentos novos (7 dias)", 2, "info", "Rastreabilidade", [item]),
                         section("diferencas", "Diferenças de valores", 1, "danger", "Rastreabilidade", [{**item, "detail": "Diferenças: Total"}]),
                         section("rubricas", "Rubricas por classificar", 0, "warning", "Classificação de rubricas"),
                         section("orcamento", "Despesas por associar ao orçamento", 3, "info", "Orçamento", [item], params={"area.orcamento": "associacoes"}, safe=safe_budget),
                         section("reconciliacao", "Movimentos bancários por reconciliar", 3, "warning", "Reconciliação automática",
                                 [{"label": "TRF FORNECEDOR", "date": "2026-10-20", "amount_cents": -5000, "detail": "Por reconciliar 50,00 €"}], amount=15000,
                                 note="Nunca são reconciliados automaticamente")],
            "automation": {}}
automation = {"frequency": "manual", "classification": {"enabled": False, "min_confidence": 95}, "budget_links": {"enabled": False, "min_confidence": 99},
              "batch_classification": 85, "batch_budget_links": 85, "revision": 0, "authorized_by": None, "last_run_at": None, "last_result": {}}
writes = []


def intercept(route):
    path, method = urlsplit(route.request.url).path.removeprefix("/api"), route.request.method
    if path == "/bootstrap":
        return route.fulfill(json=boot)
    if path == "/conference/overview":
        return route.fulfill(json=overview)
    if path == "/conference/automation" and method == "GET":
        return route.fulfill(json=automation)
    if (path == "/conference/automation" and method == "PUT") or path == "/budgets/b1/links/batch" or path == "/conference/automation/run":
        writes.append((method, path, route.request.post_data_json))
        if path == "/conference/automation":
            return route.fulfill(json={**automation, **route.request.post_data_json, "revision": 1, "authorized_by": "Administrador Demo"})
        if path.endswith("/run"):
            return route.fulfill(json={"classified": 1, "linked": 2, "errors": []})
        return route.fulfill(json={"ok": True, "confirmed": 2})
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
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Centro%20de%20Confer%C3%AAncia")
        expect(page.get_by_role("heading", name="Centro de Conferência", exact=True)).to_be_visible()
        expect(page.get_by_text("9 situação(ões) por resolver")).to_be_visible()
        budget = page.get_by_role("region", name="Despesas por associar ao orçamento")
        expect(budget).to_contain_text("Elétrica Lusa · FT E/11")
        expect(page.get_by_role("region", name="Rubricas por classificar")).to_contain_text("Nada por resolver.")
        expect(page.get_by_role("region", name="Movimentos bancários por reconciliar")).to_contain_text("150,00 €")
        audit(page)
        page.screenshot(path=str(OUT / f"pendentes-{theme}-{width}.png"), full_page=True)
        budget.get_by_role("button", name="Aprovar as seguras: Despesas por associar ao orçamento").click()
        dialog = page.get_by_role("dialog", name="Aprovar as seguras")
        expect(dialog).to_contain_text("se alguma falhar, nenhuma é aplicada")
        dialog.get_by_role("button", name="Aprovar 2").click()
        expect(page.get_by_text("2 aprovação(ões) aplicada(s)")).to_be_visible()
        assert writes[-1] == ("POST", "/budgets/b1/links/batch", safe_budget["body"]), writes[-1]
        # Automatizações: desligadas por omissão; ativar, guardar e executar.
        page.get_by_role("navigation", name="Áreas do centro de conferência").get_by_role("button", name="Automatizações").click()
        panel = page.get_by_role("region", name="Automatizações")
        expect(panel).to_contain_text("Nunca automático")
        expect(panel.get_by_role("button", name="Executar agora")).to_be_disabled()
        panel.get_by_label("Aplicar a classificação de rubricas sugerida por uma regra aprovada").check()
        panel.get_by_label("Execução").select_option("diaria")
        audit(page)
        page.screenshot(path=str(OUT / f"automatizacoes-{theme}-{width}.png"), full_page=True)
        panel.get_by_role("button", name="Guardar").click()
        expect(page.get_by_text("Automatizações guardadas.")).to_be_visible()
        method, path, body = writes[-1]
        assert (method, path) == ("PUT", "/conference/automation") and body["frequency"] == "diaria" and body["classification"] == {"enabled": True, "min_confidence": 95}, writes[-1]
        panel.get_by_role("button", name="Executar agora").click()
        expect(page.get_by_text("Automatizações executadas: 1 classificação(ões) e 2 associação(ões) ao orçamento.")).to_be_visible()
        # Abrir o módulo de origem com o separador certo.
        page.get_by_role("navigation", name="Áreas do centro de conferência").get_by_role("button", name="Por resolver").click()
        page.get_by_role("region", name="Despesas por associar ao orçamento").get_by_role("button", name="Abrir Despesas por associar ao orçamento").click()
        expect(page).to_have_url(re.compile(r"pagina=Or%C3%A7amento.*area\.orcamento=associacoes"))
        assert not [w for w in writes if w[0] == "inesperada"], writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: centro de conferência (secções, aprovar as seguras pela rota de lote, automatizações guardadas e executadas, abrir o módulo de origem), temas e larguras.")
