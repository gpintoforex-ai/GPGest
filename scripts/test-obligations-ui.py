"""Obrigações, Contratos e Prazos: painel, detalhe dos indicadores, ações e nova obrigação (respostas simuladas).

Só os pedidos esperados de escrita são aceites e verificados. Requer o Vite em 127.0.0.1:5174.
"""
import copy
import runpy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "scripts/test-dashboard-scope-ui.py"), run_name="fixture")
ns = fixture["ns"]
OUT = ROOT / ".local/obligations-ui"
OUT.mkdir(parents=True, exist_ok=True)
boot = copy.deepcopy(ns["boot"])
boot["user"]["role"] = "gestor"
boot["company"]["id"] = "obligations-fixture"
boot["categories"] = [{"id": "cat-tel", "name": "Comunicações", "active": True}]


def occurrence(id, title, category, due, days, state, expected=None, **extra):
    return {"id": id, "obligation_id": "ob-" + id, "title": title, "category": category, "kind": extra.pop("kind", ""), "entity_name": extra.pop("entity", ""),
            "store_id": None, "rule_date": due, "due_date": due, "days": days, "state": state, "expected_cents": expected, "actual_cents": None,
            "status": "pendente", "note": "", "date_origin": "", "revision": 0, "periodicity": extra.pop("periodicity", "Anual"),
            "date_source": "manual", "date_validated": extra.pop("validated", True), "responsible": "Gestora", "responsible_id": "u1",
            "completed_at": None, "completed_by": None, "read": False, "alert_due": False, **extra}


upcoming = [
    occurrence("o0", "Seguro de acidentes de trabalho", "seguro", "2026-11-05", -5, "vencido", 18000),
    occurrence("o1", "Entrega do IVA (outubro)", "fiscal", "2026-11-15", 5, "urgente", None, entity="Autoridade Tributária", periodicity="Mensal", validated=False),
    occurrence("o2", "IUC — Mercedes 180", "veiculo", "2026-11-20", 10, "a_aproximar", 12000, entity="AA-00-BB", kind="IUC"),
    occurrence("o3", "Renovação — MEO (TV/Net/Telefone)", "contrato", "2026-11-30", 20, "a_aproximar", 61200, entity="MEO"),
    occurrence("o4", "Inspeção periódica", "inspecao", "2026-12-10", 30, "a_aproximar", 3500, entity="Mercedes 180"),
]
overview = {
    "today": "2026-11-10",
    "kpis": {"overdue": 1, "next_7": 1, "next_30": 4, "next_90": 9, "up_to_date": 4, "total": 5, "done_year": 7, "commitments_30_cents": 76700, "commitments_90_cents": 140000},
    "upcoming": upcoming,
    "calendar": {"month": "2026-11", "days": {"2026-11-05": [{"id": "o0", "title": "Seguro", "category": "seguro", "state": "vencido"}],
                                              "2026-11-15": [{"id": "o1", "title": "Entrega do IVA", "category": "fiscal", "state": "urgente"}],
                                              "2026-11-20": [{"id": "o2", "title": "IUC — Mercedes 180", "category": "veiculo", "state": "a_aproximar"}]}},
    "categories": [{"key": "fiscal", "label": "Fiscal", "count": 1}, {"key": "contrato", "label": "Contrato", "count": 1}, {"key": "veiculo", "label": "Veículo", "count": 1},
                   {"key": "inspecao", "label": "Inspeção", "count": 1}, {"key": "seguro", "label": "Seguro", "count": 1}, {"key": "licenca", "label": "Licença", "count": 0},
                   {"key": "outro", "label": "Outro", "count": 0}],
    "per_month": [{"month": m, "count": c} for m, c in zip(["2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04"], [8, 10, 12, 9, 11, 9])],
    "fiscal": [{"id": "ob-o1", "title": "IVA", "periodicity_label": "Mensal", "date_validated": False, "next": upcoming[1]}],
    "contracts": [upcoming[3]],
    "labels": {"categories": {}, "states": {}, "date_sources": {"manual": "Indicada manualmente", "regra_legal": "Regra legal"},
               "periodicities": {"unica": "Única", "mensal": "Mensal", "anual": "Anual", "personalizada": "Personalizada (de N em N meses)"}},
    "users": [{"id": "u1", "name": "Gestora"}],
}
writes = []


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    method = route.request.method
    if path == "/bootstrap":
        return route.fulfill(json=boot)
    if path == "/obligations/overview":
        return route.fulfill(json=overview)
    if path == "/obligations/occurrences":
        return route.fulfill(json=upcoming)
    if path.startswith("/obligations/occurrences/") and method == "POST":
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json={"ok": True})
    if path == "/obligations" and method == "POST":
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json={"id": "new", **route.request.post_data_json, "occurrences": []})
    if method != "GET":
        writes.append(("inesperada", path))
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
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
        page.goto("http://127.0.0.1:5174/?pagina=Obriga%C3%A7%C3%B5es%20e%20Prazos")
        expect(page.get_by_role("heading", name="Obrigações, Contratos e Prazos")).to_be_visible()
        kpis = page.get_by_role("group", name="Indicadores de prazos")
        for text in ("Vencidas", "Prazos a expirar", "≤ 7 dias", "Próximos 30 dias", "767,00", "Próximos 90 dias", "Em dia", "7 concluída(s) este ano", "Total de registos"):
            expect(kpis).to_contain_text(text)
        upcoming_list = page.locator(".obl-upcoming")
        expect(upcoming_list.locator("li").first).to_contain_text("Seguro de acidentes de trabalho")  # Vencidos primeiro.
        expect(upcoming_list).to_contain_text("5 dia(s) em atraso")
        expect(upcoming_list).to_contain_text("IUC — Mercedes 180")
        expect(page.get_by_role("heading", name="Novembro 2026")).to_be_visible()
        expect(page.get_by_role("gridcell", name="15: Entrega do IVA")).to_be_visible()
        expect(page.get_by_role("img", name="5 prazos por categoria")).to_be_visible()
        expect(page.locator(".obl-overview")).to_contain_text("data a validar")
        expect(page.locator(".obl-overview")).to_contain_text("Renovação — MEO")
        page.screenshot(path=str(OUT / f"visao-{theme}-{width}.png"), full_page=True)
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."
        # Concluir com valor efetivo: só por confirmação do utilizador.
        upcoming_list.locator("li").filter(has_text="IUC — Mercedes 180").locator("summary").click()
        page.get_by_role("button", name="Concluir").first.click()
        dialog = page.get_by_role("dialog", name="Concluir prazo")
        expect(dialog).to_contain_text("Um movimento financeiro semelhante não conclui o prazo")
        dialog.get_by_label("Valor efetivo (€)").fill("118,50")
        dialog.get_by_role("button", name="Concluir prazo").click()
        expect(page.get_by_text("Prazo atualizado.")).to_be_visible()
        assert writes[-1] == ("/obligations/occurrences/o2/complete", {"revision": 0, "reason": "", "actual_cents": 11850}), writes
        # Indicador → lista filtrada.
        kpis.get_by_role("button", name="Vencidas: 1. Ver prazos").click()
        grid = page.get_by_role("table", name="Prazos")
        expect(grid.locator("tbody tr")).to_have_count(1)
        expect(grid).to_contain_text("Seguro de acidentes de trabalho")
        page.get_by_label("Estado").select_option("")
        expect(grid.locator("tbody tr")).to_have_count(5)
        expect(grid).to_contain_text("Data a validar")
        page.screenshot(path=str(OUT / f"lista-{theme}-{width}.png"), full_page=True)
        # Novo prazo fiscal: a data fica «a validar», com a origem indicada.
        page.get_by_role("button", name="Novo prazo").click()
        drawer = page.get_by_role("dialog", name="Novo prazo")
        drawer.get_by_label("Categoria").select_option("fiscal")
        expect(drawer).to_contain_text("valide a data com o calendário oficial")
        drawer.get_by_label("Tipo").fill("IVA — declaração periódica")
        drawer.get_by_label("Título").fill("IVA mensal")
        drawer.get_by_label("Periodicidade").select_option("mensal")
        drawer.get_by_label("Primeira data-limite").fill("2026-12-15")
        drawer.get_by_label("Referência ou nota").fill("Calendário fiscal da AT, a confirmar com o contabilista")
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        page.screenshot(path=str(OUT / f"novo-{theme}-{width}.png"), full_page=True)
        drawer.get_by_role("button", name="Criar prazo").click()
        expect(page.get_by_text("Prazo criado.")).to_be_visible()
        created = writes[-1][1]
        assert writes[-1][0] == "/obligations" and created["category"] == "fiscal" and created["periodicity"] == "mensal", writes[-1]
        assert created["date_source"] == "regra_legal" and created["date_validated"] is False and created["amount_cents"] is None, created
        assert not [w for w in writes if w[0] == "inesperada"], writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: obrigações e prazos (indicadores com detalhe, próximos prazos, calendário, categorias, conclusão confirmada, novo prazo fiscal a validar), temas e larguras.")
