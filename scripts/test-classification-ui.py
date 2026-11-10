"""Classificação de rubricas: sugestões, aceitação individual e em lote, janela de repartição e regras.

Respostas simuladas; só os pedidos de escrita esperados são aceites e verificados. Requer o Vite em 127.0.0.1:5174.
"""
import copy
import runpy
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / "scripts/test-dashboard-scope-ui.py"), run_name="fixture")
ns = fixture["ns"]
OUT = ROOT / ".local/classification-ui"
OUT.mkdir(parents=True, exist_ok=True)
boot = copy.deepcopy(ns["boot"])
boot["user"]["role"] = "gestor"
boot["company"]["id"] = "classification-fixture"
CATS = [{"id": "c-en", "name": "Energia"}, {"id": "c-ma", "name": "Manutenção"}, {"id": "c-fu", "name": "Combustíveis"}]


def share(cid, name, percent, cents):
    return {"category_id": cid, "category_name": name, "percent": percent, "amount_cents": cents}


history = {"key": "h", "source": "historico", "confidence": 75, "rule_id": None, "reasons": ["Histórico do fornecedor: 2 de 3 documento(s) com esta classificação"],
           "shares": [share("c-en", "Energia", "70.00", 8611), share("c-ma", "Manutenção", "30.00", 3690)]}
supplier = {"key": "s", "source": "fornecedor", "confidence": 65, "rule_id": None, "reasons": ["Rubrica definida na ficha do fornecedor"],
            "shares": [share("c-fu", "Combustíveis", "100", 12301)]}
rule = {"key": "r", "source": "regra", "confidence": 92, "rule_id": "rule-1", "reasons": ["Regra aprovada «Gasóleo»"], "shares": [share("c-fu", "Combustíveis", "100", 5000)]}


def doc(id, name, number, total, state, suggestions, kind="purchase", current=()):
    return {"kind": kind, "id": id, "store_id": "s1", "store": "Loja", "supplier_nif": "510774318", "supplier_name": name, "document_type": "FT", "document": number,
            "document_date": "2026-10-10", "amount_cents": total, "total_cents": total, "base_cents": total - 2300, "tax_cents": 2300, "target": "lines" if state == "linhas" else "purchase",
            "current": list(current), "state": state, "suggestions": suggestions, "safe": bool(suggestions and suggestions[0]["confidence"] >= 85)}


rows = [doc("p2", "Galp", "FT G/9", 5000, "por_classificar", [rule]),
        doc("p1", "Elétrica", "FT N/1", 12301, "fornecedor", [history, supplier], current=[{"category_id": "c-fu", "category_name": "Combustíveis", "amount_cents": 12301}]),
        doc("p3", "Armazém", "FT A/3", 9000, "linhas", [])]
pending = {"start": "2026-08-01", "end": "2026-11-10", "rows": rows, "safe_threshold": 85, "categories": CATS,
           "summary": {"por_classificar": 1, "fornecedor": 1, "seguras": 1, "regras": 1}, "notes": ["Classificação financeira (rubricas de gestão)."]}
rules = [{"id": "rule-1", "name": "Gasóleo", "kind": "financeira", "supplier_nif": "500000000", "supplier_name": "Galp", "document_type": None, "keyword": "gasóleo",
          "store_id": None, "store": "", "shares": [{"category_id": "c-fu", "percent": "100", "category_name": "Combustíveis"}], "active": True, "hits": 4,
          "last_used_at": "2026-11-01T10:00:00+00:00", "revision": 0}]
writes = []


def intercept(route):
    path, method = urlsplit(route.request.url).path.removeprefix("/api"), route.request.method
    if path == "/bootstrap":
        return route.fulfill(json=boot)
    if path == "/classification/pending":
        return route.fulfill(json=pending)
    if path == "/classification/suggest":
        return route.fulfill(json={**rows[1], "suggestions": [history, supplier], "safe_threshold": 85, "categories": CATS})
    if path == "/classification/rules" and method == "GET":
        return route.fulfill(json=rules)
    if path.startswith("/classification/") and method in ("POST", "PUT"):
        writes.append((method, path, route.request.post_data_json))
        return route.fulfill(json={"ok": True, "id": "new", "applied": 1, "shares": [], "rule_id": None})
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
        page.goto("http://127.0.0.1:5174/?pagina=Classifica%C3%A7%C3%A3o%20de%20rubricas")
        expect(page.get_by_role("heading", name="Classificação de rubricas")).to_be_visible()
        expect(page.get_by_role("group", name="Resumo da classificação")).to_contain_text("Sugestões seguras")
        grid = page.get_by_role("table", name="Documentos a classificar")
        expect(grid.locator("tbody tr")).to_have_count(3)
        expect(grid).to_contain_text("Regra · 92 %")
        expect(grid).to_contain_text("Histórico · 75 %")
        expect(grid).to_contain_text("Classifique nas linhas, em Compras.")
        audit(page)
        page.screenshot(path=str(OUT / f"pendentes-{theme}-{width}.png"), full_page=True)
        # Lote: só as sugestões seguras selecionadas; o servidor recebe a repartição da sugestão.
        page.get_by_role("button", name="Selecionar as seguras").click()
        page.get_by_role("button", name="Aceitar 1 sugestão(ões) selecionada(s)").click()
        expect(page.get_by_text("Classificação aplicada.")).to_be_visible()
        assert writes[-1] == ("POST", "/classification/apply", {"kind": "purchase", "id": "p2", "mode": "percent", "rule_id": "rule-1",
                                                               "shares": [{"category_id": "c-fu", "value": "100"}]}), writes[-1]
        # Janela: usar o histórico, passar para valores, guardar como regra.
        grid.get_by_role("button", name="Classificar FT N/1").click()
        dialog = page.get_by_role("dialog", name="Classificar documento")
        expect(dialog.get_by_role("region", name="Sugestões")).to_contain_text("Histórico · 75 %")
        dialog.get_by_role("listitem").filter(has_text="Histórico").get_by_role("button", name="Usar").click()
        expect(dialog).to_contain_text("Repartição completa")
        dialog.get_by_role("button", name="Valor").click()
        expect(dialog.get_by_label("Valor (€)").first).to_have_value("86,11")
        expect(dialog).to_contain_text("Repartição completa")
        dialog.get_by_label("Valor (€)").nth(1).fill("30,00")
        expect(dialog).to_contain_text("Falta repartir 6,90 €")
        dialog.get_by_label("Valor (€)").nth(1).fill("36,90")
        dialog.get_by_label("Guardar como regra aprovada").check()
        dialog.get_by_label("Palavra-chave (opcional)").fill("eletricidade")
        audit(page)
        page.screenshot(path=str(OUT / f"janela-{theme}-{width}.png"), full_page=True)
        dialog.get_by_role("button", name="Aplicar classificação").click()
        expect(page.get_by_text("Classificação aplicada e regra guardada.")).to_be_visible()
        method, path, body = writes[-1]
        assert (method, path) == ("POST", "/classification/apply") and body["mode"] == "amount", writes[-1]
        assert body["shares"] == [{"category_id": "c-en", "value": "86.11"}, {"category_id": "c-ma", "value": "36.90"}], body
        assert body["save_rule"] == {"supplier": True, "document_type": False, "store": False, "keyword": "eletricidade"} and body["rule_name"] == "Elétrica", body
        # Regras.
        page.get_by_role("navigation", name="Áreas da classificação").get_by_role("button", name="Regras aprovadas").click()
        table = page.get_by_role("table", name="Lista de regras")
        expect(table).to_contain_text("Gasóleo")
        expect(table).to_contain_text("Fornecedor Galp · «gasóleo»")
        page.get_by_role("button", name="Nova regra").click()
        rule_dialog = page.get_by_role("dialog", name="Nova regra")
        rule_dialog.get_by_label("Nome").fill("Portagens")
        rule_dialog.get_by_label("Palavra-chave").fill("via verde")
        rule_dialog.get_by_label("Rubrica").select_option("c-fu")
        audit(page)
        rule_dialog.get_by_role("button", name="Criar regra").click()
        expect(page.get_by_text("Regra criada.")).to_be_visible()
        assert writes[-1][:2] == ("POST", "/classification/rules") and writes[-1][2]["keyword"] == "via verde" and writes[-1][2]["shares"] == [{"category_id": "c-fu", "value": "100"}], writes[-1]
        assert not [w for w in writes if w[0] == "inesperada"], writes
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: classificação de rubricas (sugestões, lote só com as selecionadas, repartição por valor e percentagem, regra guardada, regras), temas e larguras.")
