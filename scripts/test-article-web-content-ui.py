"""Descritivo dos artigos para o site com IA: na ficha, gerar a proposta (sem alterar a ficha), ver as frases a
confirmar e usar a proposta no formulário; no separador «Descritivos do site», gerar em lote com progresso, rever,
editar e aprovar. A API é simulada (reaproveita o teste de artigos); nenhum pedido sai para a Anthropic.
Requer o Vite em 127.0.0.1:5174.
"""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-inventory-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], ns)
OUT = ROOT / ".local/article-web-content"
OUT.mkdir(parents=True, exist_ok=True)

FIELDS = {"description": "Suplemento em cápsulas, embalagem de 60 unidades.\n\nTomar com água.", "web_summary": "Suplemento em cápsulas, 60 unidades.",
          "web_highlights": ["60 cápsulas", "Embalagem reciclável"], "seo_title": "Suplemento de teste 60 cápsulas", "seo_description": "Suplemento em cápsulas, 60 unidades."}
PROPOSAL = {"id": "pr1", "status": "pendente", "fields": FIELDS, "needs_review": ["«Contribui para a redução do cansaço» (rótulo)"],
            "missing_information": ["Modo de utilização"], "sources_used": ["ficha", "imagem"], "images_used": True, "model": "claude-opus-5-5",
            "created_at": "2026-11-12T10:00:00+00:00"}
QUEUE = [{"id": f"q{i}", "code": f"A{i}", "name": f"Artigo do lote {i}", "brand": "Marca", "family": "Família", "photo": "", "has_description": False,
          "has_images": i != 2, "proposal_status": None} for i in range(1, 4)]
state = {"generated": set(), "approved": None}
calls = []


def intercept(route):
    url = urlsplit(route.request.url)
    path, method = url.path, route.request.method
    match = re.match(r"^/api/stock/articles/([^/]+)/web-content(/approve|/discard)?$", path)
    if match:
        article, action = match.group(1), match.group(2) or ""
        calls.append((method, path, route.request.post_data_json if method == "POST" else None))
        if method == "POST" and not action:
            state["generated"].add(article)
        if action == "/approve":
            state["approved"] = route.request.post_data_json
        status = "aprovada" if action == "/approve" else "descartada" if action == "/discard" else "pendente"
        proposal = {**PROPOSAL, "status": status} if article in state["generated"] or action else None
        return route.fulfill(json={"article_id": article, "revision": 3, "configured": True,
                                   "current": {k: "" if k != "web_highlights" else [] for k in FIELDS}, "proposal": proposal})
    if path == "/api/stock/web-content":
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        rows = [{**r, "proposal_status": "pendente" if r["id"] in state["generated"] else None} for r in QUEUE]
        rows = [r for r in rows if (r["proposal_status"] == "pendente") == (params.get("state") == "por_rever")]
        return route.fulfill(json={"rows": rows, "total": len(rows), "configured": True})
    return ns["mock"](route)


def audit(page):
    page.mouse.move(1, 1)
    page.wait_for_timeout(400)
    result = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
    assert not result.get("failures"), result.get("failures")


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in ("light", "dark"):
        state.update(generated=set(), approved=None)
        calls.clear()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos")
        # Ficha do artigo: gerar a proposta e usá-la no formulário (a ficha só muda ao gravar).
        page.get_by_label("Mais ações para Suplemento de teste", exact=True).click()
        page.get_by_role("menuitem", name="Editar Suplemento de teste", exact=True).click()
        dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
        dialog.get_by_role("tab", name="Descrição e anexos").click()
        assistant = dialog.get_by_role("region", name="Descritivo com IA")
        assistant.get_by_role("button", name="Gerar descritivo com IA").click()
        expect(assistant).to_contain_text("Confirme antes de publicar")
        expect(assistant).to_contain_text("Contribui para a redução do cansaço")
        assert [c[0] for c in calls if c[1].endswith("/web-content")][-1] == "POST"
        expect(dialog.locator("textarea[name='description']")).to_have_value("Artigo fictício")  # a proposta não altera a ficha
        assistant.get_by_role("button", name="Usar no formulário").click()
        expect(dialog.locator("textarea[name='description']")).to_have_value(FIELDS["description"])
        expect(dialog.locator("textarea[name='web_summary']")).to_have_value(FIELDS["web_summary"])
        expect(dialog.locator("textarea[name='web_highlights']")).to_have_value("60 cápsulas\nEmbalagem reciclável")
        expect(dialog.locator("input[name='seo_title']")).to_have_value(FIELDS["seo_title"])
        audit(page)
        dialog.screenshot(path=str(OUT / f"ficha-{theme}.png"))
        page.keyboard.press("Escape")
        if page.get_by_role("alertdialog").count():
            page.get_by_role("button", name="Descartar alterações").click()
        # Lote: gerar propostas com progresso, rever, editar e aprovar.
        page.get_by_role("navigation", name="Artigos e stock").get_by_role("button", name="Descritivos do site").click()
        queue = page.get_by_role("region", name="Descritivos do site")
        table = queue.get_by_role("table", name="Artigos para o descritivo do site")
        expect(table.locator("tbody tr")).to_have_count(3)
        expect(table).to_contain_text("Sem imagem: texto mais genérico")
        table.get_by_label("Selecionar Artigo do lote 1").check()
        table.get_by_label("Selecionar Artigo do lote 3").check()
        queue.get_by_role("button", name="Gerar propostas (2)").click()
        expect(page.get_by_text("2 proposta(s) geradas e por rever.").first).to_be_visible()
        queue.get_by_role("button", name="Propostas por rever").click()
        expect(table.locator("tbody tr")).to_have_count(2)
        table.get_by_role("button", name="Rever").first.click()
        review = page.get_by_role("dialog", name="Descritivo do site: Artigo do lote 1")
        review.get_by_label("Resumo").fill("Resumo revisto pela loja.")
        audit(page)
        review.get_by_role("button", name="Aprovar e gravar na ficha").click()
        expect(review).to_have_count(0)
        assert state["approved"] == {**FIELDS, "web_summary": "Resumo revisto pela loja.", "revision": 3}, state["approved"]
        expect(page.get_by_text("aprovado e publicado na ficha", exact=False).first).to_be_visible()  # notificação global
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: descritivo do site com IA (ficha: gerar, confirmar alegações, usar no formulário; lote: gerar com progresso, rever, editar, aprovar), temas e contraste.")
print(json.dumps({"calls": len(calls)}))
