"""Loja online no catálogo de artigos: coluna com o estado no site, filtro, «Marcar para o site» e «Retirar do
site» no menu da linha e na seleção, e o bloco «Loja online» na ficha. API simulada (reaproveita o teste de
artigos); nenhum pedido sai para a loja. Requer o Vite em 127.0.0.1:5174.
"""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-inventory-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], ns)
OUT = ROOT / ".local/article-webshop"
OUT.mkdir(parents=True, exist_ok=True)

SYNCED = {"id": "pub-erp", "connection_id": "c1", "connection": "Raizpura", "state": "published", "status": "synced",
          "status_label": "Sincronizado", "sku": "W100", "price_gross": "5.54", "channel_price": False, "blockers": [],
          "error": "", "synced_at": "2026-11-15T10:00:00+00:00", "retry_at": None, "woo_product_id": 812,
          "woo_status": "publish", "food_missing": [], "revision": 2}
ns["catalog"]["webshop_connections"] = [{"id": "c1", "name": "Raizpura"}]
ns["article"]["web"] = []
ns["erp_article"]["web"] = [dict(SYNCED)]
calls, queries = [], []


def sheet(article_id):
    pub = next(iter(a["web"] for a in ns["catalog"]["articles"] if a["id"] == article_id), [])
    pub = pub[0] if pub else None
    return {"article_id": article_id, "connections": [{"connection_id": "c1", "connection": "Raizpura", "active": True,
                                                       "can_manage": True, "publication": pub, "blockers": []}]}


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path, route.request.method
    if path == "/api/stock/catalog":
        queries.append({k: v[0] for k, v in parse_qs(url.query).items()})
    if path == "/api/webshop/publications" and method == "POST":
        body = route.request.post_data_json
        calls.append(("mark", body))
        target = next(a for a in ns["catalog"]["articles"] if a["id"] == body["article_id"])
        target["web"] = [{**SYNCED, "id": "pub-" + target["id"], "status": "pending", "status_label": "A aguardar a loja",
                          "synced_at": None, "woo_product_id": None, "revision": 0}]
        return route.fulfill(json={"id": "pub-" + target["id"]})
    match = re.match(r"^/api/webshop/publications/([^/]+)/(withdraw|publish|resync)$", path)
    if match:
        calls.append((match.group(2), route.request.post_data_json))
        for a in ns["catalog"]["articles"]:
            for state in a["web"]:
                if state["id"] == match.group(1) and match.group(2) == "withdraw":
                    state.update(state="withdrawn", status="pending", status_label="A aguardar a loja")
        return route.fulfill(json={})
    match = re.match(r"^/api/webshop/article-publications/([^/]+)$", path)
    if match:
        return route.fulfill(json=sheet(match.group(1)))
    return ns["mock"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos")
    grid = page.get_by_role("table", name="Artigos")
    expect(grid.get_by_role("columnheader", name="Loja online")).to_be_visible()
    supplement = grid.locator("tr", has_text="Suplemento de teste")
    tea = grid.locator("tr", has_text="Chá da Wintouch")
    expect(supplement.locator(".article-col-web")).to_contain_text("Fora da loja")
    expect(tea.locator(".article-col-web")).to_contain_text("No site")
    page.get_by_role("region", name="Catálogo de artigos").screenshot(path=str(OUT / "grelha.png"))
    # Menu da linha: marcar um artigo fora da loja.
    page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
    page.get_by_role("menuitem", name="Marcar para o site").click()
    expect(page.get_by_text("1 artigo(s) marcados para o site.", exact=False).first).to_be_visible()
    assert calls[-1] == ("mark", {"connection_id": "c1", "article_id": "p1"}), calls
    expect(supplement.locator(".article-col-web")).to_contain_text("A enviar")
    # Menu da linha: retirar um artigo publicado (com confirmação).
    page.get_by_label("Mais ações para Chá da Wintouch", exact=True).click()
    page.get_by_role("menuitem", name="Retirar do site").click()
    page.get_by_role("alertdialog").get_by_role("button", name="Retirar da loja").click()
    expect(page.get_by_text("1 artigo(s) retirados do site", exact=False).first).to_be_visible()
    assert calls[-1] == ("withdraw", {"revision": 2}), calls
    # Filtro «Loja online».
    page.get_by_text("Filtros avançados").click()
    page.get_by_label("Loja online", exact=True).select_option("none")
    page.wait_for_timeout(400)
    assert queries[-1].get("web") == "none", queries[-1]
    page.get_by_role("button", name="Limpar filtros").click()
    # Seleção: marcar vários de uma vez (os já marcados são ignorados).
    ns["article"]["web"] = []
    page.reload()
    page.get_by_label("Selecionar Suplemento de teste").check()
    page.get_by_role("region", name="Ações da seleção").get_by_role("button", name="Marcar para o site").click()
    expect(page.get_by_text("1 artigo(s) marcados para o site.", exact=False).first).to_be_visible()
    # Ficha: bloco «Loja online» com o estado e as ações.
    ns["article"]["web"] = []
    page.reload()
    page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
    page.get_by_role("menuitem", name="Editar Suplemento de teste", exact=True).click()
    dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
    block = dialog.locator(".article-webshop")
    expect(block).to_contain_text("Fora da loja")
    block.get_by_role("button", name="Marcar para o site").click()
    expect(block).to_contain_text("A aguardar a loja")
    expect(block).to_contain_text("Artigo marcado para o site.")
    block.screenshot(path=str(OUT / "ficha.png"))
    expect(block.get_by_role("button", name="Retirar do site")).to_be_visible()
    assert [c[0] for c in calls] == ["mark", "withdraw", "mark", "mark"], calls
    assert not errors, errors
    browser.close()
print("PASS: loja online no catálogo (coluna, filtro, marcar/retirar na linha e na seleção) e na ficha.")
