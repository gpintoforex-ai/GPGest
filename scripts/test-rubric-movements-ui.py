"""Movimentos da rubrica a partir do Dashboard: painel com pesquisa e filtros, alteração da rubrica em lote com
pré-visualização e motivo (administrador), pedido de aprovação para pagamentos (gestor), histórico com reversão,
centro de custo e observações, consulta do documento, atualização do Dashboard; temas, larguras e contraste.

Reutiliza os dados simulados de test-dashboard-modern-ui.py e dados fictícios; só as escritas esperadas são aceites.
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
OUT = ROOT / ".local/rubric-movements-ui"
OUT.mkdir(parents=True, exist_ok=True)

CATEGORIES = [{"id": "c1", "name": "Mercadorias", "parent_id": None, "color": "#245fc5", "icon": "package"},
              {"id": "c2", "name": "Seguros", "parent_id": None, "color": "#176b45", "icon": "shield"},
              {"id": "c3", "name": "Outros", "parent_id": None, "color": "#805400", "icon": "tag"}]


def share(category, name, base, tax):
    return {"category_id": category, "name": name, "base_cents": base, "tax_cents": tax, "amount_cents": base + tax}


def movement(identifier, label, entity, origin, origin_label, category, name, base, tax, mode, state, account=None, editable=True, blocked=""):
    return {"id": identifier, "date": "2026-11-05", "payment_date": "2026-11-20" if state == "pago" else None, "label": label, "entity": entity,
            "source": origin_label, "origin": origin, "origin_label": origin_label, "shares": [share(category, name, base, tax)],
            "amount_cents": base + tax, "base_cents": base, "tax_cents": tax, "category_amount_cents": base + tax, "mode": mode,
            "editable": editable, "blocked": blocked, "state": state, "account_id": account and "a1", "account": account or "",
            "annotation": {"cost_center_id": None, "notes": "", "revision": 0}}


ROWS = [
    movement("p1", "FT 1/10", "Seguradora Exemplo", "purchase", "Compra", "c1", "Mercadorias", 10000, 2300, "purchase", "pago"),
    movement("erp:e1", "FT 2/20", "Grossista Exemplo", "erp", "ERP", "c1", "Mercadorias", 50000, 11500, "erp", "por_pagar"),
    movement("bank:b1", "Movimento bancário", "PAGAMENTO SEGURO AUTO", "bank", "Banco", "c1", "Mercadorias", 15000, 0, "bank", "por_reconciliar",
             account="BPI · Conta"),
    movement("p2", "FT 3/30", "Fornecedor Linhas", "purchase", "Compra", "c1", "Mercadorias", 8000, 1840, "lines", "por_pagar", editable=False,
             blocked="Compra com linhas lançadas: a rubrica vem das linhas e corrige-se em Compras."),
]
state = {"role": "administrador", "applied": False, "overviews": 0}
writes = []


def listing():
    rows = copy.deepcopy(ROWS)
    if state["applied"]:
        for row in rows[:2]:
            row["shares"] = [share("c2", "Seguros", row["base_cents"], row["tax_cents"])]
    if state["role"] != "administrador":
        rows[2].update(editable=False, blocked="Só o administrador corrige pagamentos do banco e da caixa: peça aprovação.")
    return {"rows": rows, "total_cents": sum(r["amount_cents"] for r in rows), "can_edit_payments": state["role"] == "administrador",
            "categories": CATEGORIES, "cost_centers": [{"id": "cc1", "name": "LJ1 · Loja 1"}], "accounts": [{"id": "a1", "name": "BPI · Conta"}]}


def preview(body):
    gestor = state["role"] != "administrador"
    items = []
    for identifier in body["ids"]:
        row = next(r for r in ROWS if r["id"] == identifier)
        blocked = "Só o administrador corrige pagamentos do banco e da caixa: peça aprovação." if gestor and identifier.startswith("bank:") else row["blocked"]
        items.append({"id": identifier, "mode": row["mode"], "blocked": blocked, "before": ["c1"], "before_names": ["Mercadorias"],
                      "label": row["label"], "entity": row["entity"], "source": row["origin_label"], "amount_cents": row["amount_cents"]})
    ready = [i for i in items if not i["blocked"]]
    return {"fingerprint": "f" * 64, "target": "Seguros", "items": items, "count": len(ready), "total_cents": sum(i["amount_cents"] for i in ready),
            "blocked": len(items) - len(ready), "approval": [i["id"] for i in items if i["blocked"].startswith("Só o administrador")],
            "preserved": ["Valores, IVA, datas e documentos (nada é alterado no banco, na caixa nem no ERP)", "Tesouraria e saldos bancários"]}


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path, route.request.method
    if path == "/api/bootstrap":
        boot = copy.deepcopy(ns["boot"])
        boot["user"]["role"] = state["role"]
        return route.fulfill(json=boot)
    if path == "/api/dashboard/overview":
        state["overviews"] += 1
        return ns["intercept"](route)
    if path == "/api/rubric-movements" and method == "GET":
        return route.fulfill(json=listing())
    if path == "/api/rubric-movements/preview":
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json=preview(route.request.post_data_json))
    if path == "/api/rubric-movements/apply":
        writes.append((path, route.request.post_data_json))
        state["applied"] = True
        return route.fulfill(json={"applied": 2, "skipped": 1})
    if path == "/api/rubric-movements/history":
        return route.fulfill(json={"entries": [{"id": "au1", "action": "classificacao_rubricas", "user": "Administrador", "date": "2026-11-11T10:00:00+00:00",
                                                "reason": "Seguro estava em outras despesas", "origin": "", "before": ["Rubrica do fornecedor"], "after": ["Seguros"],
                                                "revertible": True}]})
    if path in ("/api/rubric-movements/revert", "/api/rubric-movements/annotation", "/api/rubric-nature/requests"):
        writes.append((path, route.request.post_data_json))
        return route.fulfill(json={"reverted": "au1"} if path.endswith("revert") else {"revision": 1} if path.endswith("annotation") else {"id": "q1"})
    if path == "/api/dashboard/expenses/drilldown":
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        writes.append((path, params))
        return route.fulfill(json={"vat_basis": "sem IVA dedutível", "document": {"id": params.get("document_id"), "label": "FT 1/10", "amount_cents": 12300,
                                   "date": "2026-11-05", "entity": "Seguradora Exemplo", "source": "Compra",
                                   "allocations": [{"label": "Seguros", "base_cents": 10000, "tax_cents": 2300, "amount_cents": 12300}]},
                                   "lines": [], "originals": ["doc-1"], "related": []})
    return ns["intercept"](route)


def audit(page):
    page.mouse.move(1, 1)  # sem estado de passagem do rato por cima de botões
    page.wait_for_timeout(400)  # fim das transições de cor (botões que acabaram de ficar ativos)
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), "A página transborda na horizontal."


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in (("light", 1440), ("dark", 390)):
        writes.clear()
        state.update(role="administrador", applied=False, overviews=0)
        page = browser.new_page(viewport={"width": width, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}'); localStorage.setItem('iazela-dashboard-versao:dashboard-modern-fixture','nova')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Dashboard")
        banner = page.get_by_role("region", name="Nova versão do Dashboard")
        if banner.count():
            banner.get_by_role("button", name="Abrir a nova versão").click()
        page.get_by_role("tablist", name="Áreas do dashboard").get_by_role("tab", name="Evolução").click()
        page.get_by_role("button", name="Mercadorias").first.click()
        panel = page.get_by_role("dialog", name="Movimentos da rubrica")
        expect(panel).to_contain_text("Movimentos · Mercadorias")
        table = panel.get_by_role("table", name="Movimentos da rubrica")
        expect(table.locator("tbody tr")).to_have_count(4)
        # Pesquisa por texto e por valor; filtros por origem.
        panel.get_by_label("Pesquisar descrição, fornecedor, documento ou valor").fill("seguro auto")
        expect(table.locator("tbody tr")).to_have_count(1)
        panel.get_by_label("Pesquisar descrição, fornecedor, documento ou valor").fill("615,00")
        expect(table.locator("tbody tr")).to_have_count(1)
        expect(table).to_contain_text("FT 2/20")
        panel.get_by_label("Pesquisar descrição, fornecedor, documento ou valor").fill("")
        panel.get_by_label("Origem").select_option("bank")
        expect(table.locator("tbody tr")).to_have_count(1)
        panel.get_by_label("Origem").select_option("")
        expect(table.get_by_label("Selecionar FT 3/30")).to_be_disabled()  # Compra com linhas: corrige-se em Compras.
        # Lote: dois documentos e o pagamento bancário para «Seguros».
        for label in ("FT 1/10", "FT 2/20", "Movimento bancário"):
            table.get_by_label(f"Selecionar {label}").check()
        batch = panel.get_by_role("region", name="Alterar rubrica dos selecionados")
        expect(batch).to_contain_text("3 selecionado(s)")
        batch.get_by_label("Nova rubrica").select_option("c2")
        batch.get_by_role("button", name="Pré-visualizar").click()
        assert writes[-1] == ("/api/rubric-movements/preview", {"ids": ["p1", "erp:e1", "bank:b1"], "category_id": "c2"}), writes[-1]
        review = panel.get_by_role("region", name="Pré-visualização da alteração de rubrica")
        expect(review).to_contain_text("Passar 3 movimento(s)")
        expect(review).to_contain_text("nada é alterado no banco, na caixa nem no ERP")
        review.get_by_role("button", name="Seguro estava em outras despesas").click()
        audit(page)
        page.screenshot(path=str(OUT / f"painel-{theme}-{width}.png"), full_page=True)
        before = state["overviews"]
        review.get_by_role("button", name="Aplicar a 3 movimento(s)").click()
        expect(panel.get_by_text("passaram para «Seguros»", exact=False)).to_be_visible()
        assert writes[-1] == ("/api/rubric-movements/apply", {"ids": ["p1", "erp:e1", "bank:b1"], "category_id": "c2", "fingerprint": "f" * 64,
                                                             "reason": "Seguro estava em outras despesas"}), writes[-1]
        # Os documentos corrigidos saem de «Mercadorias» e aparecem em «Seguros».
        expect(table.locator("tbody tr")).to_have_count(2)
        panel.locator(".rm-filters select").first.select_option("")
        expect(table.locator("tbody tr", has_text="FT 1/10")).to_contain_text("Seguros")
        assert state["overviews"] > before, "O Dashboard tem de recarregar depois da correção."
        # Editar: histórico com reversão, centro de custo e observações.
        table.get_by_role("button", name="Editar FT 1/10").click()
        edit = page.get_by_role("dialog", name="Editar FT 1/10")
        expect(edit).to_contain_text("Rubrica do fornecedor → Seguros")
        edit.get_by_label("Centro de custo").select_option("cc1")
        edit.get_by_label("Observações").fill("Apólice da viatura")
        edit.get_by_role("button", name="Guardar centro de custo e observações").click()
        assert writes[-1] == ("/api/rubric-movements/annotation", {"id": "p1", "cost_center_id": "cc1", "notes": "Apólice da viatura", "revision": 0}), writes[-1]
        table.get_by_role("button", name="Editar FT 1/10").click()
        edit = page.get_by_role("dialog", name="Editar FT 1/10")
        expect(edit.get_by_role("button", name="Reverter")).to_be_disabled()
        edit.get_by_role("button", name="Classificação anterior estava correta").click()
        edit.get_by_role("button", name="Reverter").click()
        assert writes[-1] == ("/api/rubric-movements/revert", {"id": "p1", "audit_id": "au1", "reason": "Classificação anterior estava correta"}), writes[-1]
        expect(panel.get_by_text("Classificação revertida", exact=False)).to_be_visible()
        # Consultar o documento (originais no Arquivo digital).
        table.get_by_role("button", name="Consultar FT 1/10").click()
        detail = page.get_by_role("dialog", name="Detalhe do indicador")
        expect(detail).to_contain_text("Documentos originais no Arquivo digital")
        assert writes[-1][0] == "/api/dashboard/expenses/drilldown" and writes[-1][1]["document_id"] == "p1", writes[-1]
        page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        # Gestor: os pagamentos do banco ficam para aprovação do administrador.
        state.update(role="gestor", applied=False)
        page.reload()
        page.get_by_role("tablist", name="Áreas do dashboard").get_by_role("tab", name="Evolução").click()
        page.get_by_role("button", name="Mercadorias").first.click()
        panel = page.get_by_role("dialog", name="Movimentos da rubrica")
        table = panel.get_by_role("table", name="Movimentos da rubrica")
        expect(table.get_by_label("Selecionar Movimento bancário")).to_be_disabled()
        table.get_by_role("button", name="Editar Movimento bancário").click()
        edit = page.get_by_role("dialog", name="Editar Movimento bancário")
        expect(edit).to_contain_text("Só o administrador corrige pagamentos do banco e da caixa")
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: movimentos da rubrica (pesquisa, filtros, lote com pré-visualização e motivo, histórico e reversão, centro de custo e observações, "
      "consulta do documento, recarga do Dashboard; gestor sem corrigir pagamentos), temas, larguras e contraste.")
print(json.dumps({"writes": len(writes)}))
