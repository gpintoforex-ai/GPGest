"""Assistente IAZELA: painel no cabeçalho, sugestões, envio, resposta formatada, fontes, estado sem chave,
cartão de confirmação de preço (nada é gravado sem «Confirmar») e gráficos na resposta.

Só APIs intercetadas (reaproveita a preparação do teste visual); nada sai para a Anthropic.
Requer o Vite em 127.0.0.1:5174.
"""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "scripts/test-visual-template-ui.py"
ns = {"__file__": str(source)}
exec(source.read_text(encoding="utf-8").split("with sync_playwright() as playwright:")[0], ns)
OUT = ROOT / ".local" / "assistant-ui"
OUT.mkdir(parents=True, exist_ok=True)
REPLY = ("Nos próximos 60 dias terminam a validade **2 lotes**:\n- **Chá verde** (lote L1): 4 un., 20/11/2026\n"
         "- Mel biológico (lote M7): 2 un., 30/12/2026\n\nFonte: lotes em stock nos armazéns autorizados.")
sent, available, saved = [], [True], []
ACTION = {"type": "price", "article_id": "a1", "code": "OC500", "name": "Óleo de coco biológico 500 ml", "origin": "local",
          "vat_rate": "23", "before_gross": "6.15", "after_gross": "8.90", "channels": [],
          "request": {"path": "/stock/articles/a1/price", "body": {"revision": 3, "sale_net": "7.235772"}}}
CHART = {"title": "Faturação por mês, 2026 vs 2025", "kind": "colunas", "unit": "euros", "categories": ["set", "out", "nov"],
         "series": [{"name": "2026", "values": [1234500, 1520000, 980000]}, {"name": "2025", "values": [1100000, 1310000, None]}],
         "note": "Vendas SAF-T, com IVA"}


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/assistente/estado":
        return route.fulfill(json={"available": available[0], "model": "claude-opus-5-5",
                                   "message": "" if available[0] else "O assistente não está configurado: indique ANTHROPIC_API_KEY no ficheiro .env do servidor e reinicie a API."})
    if path == "/stock/articles/a1/price":
        saved.append(route.request.post_data_json)
        return route.fulfill(json={"saved": True})
    if path == "/assistente/mensagens":
        sent.append(route.request.post_data_json)
        question = sent[-1]["messages"][-1]["content"]
        if "óleo de coco" in question:
            return route.fulfill(json={"reply": "Proposta pronta: confirme no cartão.", "tools": [{"name": "procurar_artigos"}, {"name": "propor_alteracao_preco"}],
                                       "actions": [ACTION], "charts": [], "usage": {}, "model": "claude-opus-5-5"})
        if "gráfico" in question:
            return route.fulfill(json={"reply": "Em outubro faturou **mais 16 %** do que em 2025.", "tools": [{"name": "comparativo_vendas"}, {"name": "mostrar_grafico"}],
                                       "actions": [], "charts": [CHART], "usage": {}, "model": "claude-opus-5-5"})
        return route.fulfill(json={"reply": REPLY, "tools": [{"name": "artigos_a_terminar_validade", "input": {"dias": 60}}],
                                   "usage": {"input_tokens": 1, "output_tokens": 1}, "model": "claude-opus-5-5"})
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for width, theme in ((1440, "light"), (390, "dark")):
        sent.clear()
        available[0] = True
        page = browser.new_page(viewport={"width": width, "height": 900}, locale="pt-PT")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174")
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        page.get_by_role("button", name="Assistente IAZELA", exact=True).click()
        dialog = page.get_by_role("dialog", name="Assistente IAZELA")
        expect(dialog.get_by_text("enviados à Anthropic", exact=False)).to_be_visible()
        suggestions = dialog.get_by_role("group", name="Perguntas sugeridas")
        expect(suggestions.get_by_role("button")).to_have_count(10)
        # Sugestão com «…»: vai para a caixa de texto, para completar (não é enviada).
        suggestions.get_by_role("button", name="Que produtos o cliente … já comprou?").click()
        box = dialog.get_by_label("Pergunta ao assistente", exact=True)
        expect(box).to_have_value("Que produtos o cliente … já comprou?")
        expect(box).to_be_focused()
        assert not sent
        box.fill("")
        suggestions.get_by_role("button", name="Que artigos estão a terminar a validade nos próximos 60 dias?").click()
        answer = dialog.locator(".assistant-turn.is-assistant").first
        expect(answer).to_contain_text("terminam a validade")
        expect(answer.locator("strong").first).to_have_text("2 lotes")
        expect(answer.locator("li")).to_have_count(2)
        expect(answer).to_contain_text("Consultou: Lotes e validades")
        assert sent[0]["messages"] == [{"role": "user", "content": "Que artigos estão a terminar a validade nos próximos 60 dias?"}]
        # Seguimento: o histórico segue com a pergunta nova (Enter envia).
        box.fill("E quais são do armazém principal?")
        box.press("Enter")
        expect(dialog.locator(".assistant-turn.is-assistant")).to_have_count(2)
        assert [m["role"] for m in sent[1]["messages"]] == ["user", "assistant", "user"]
        dialog.screenshot(path=str(OUT / f"conversation-{width}-{theme}.png"))
        dialog.get_by_role("button", name="Nova conversa").click()
        expect(suggestions).to_be_visible()
        # Proposta de preço: cartão com o antes/depois; nada é gravado até «Confirmar».
        saved.clear()
        box.fill("Põe o óleo de coco 500 ml a 8,90 €")
        box.press("Enter")
        card = dialog.get_by_role("group", name="Alterar preço de Óleo de coco biológico 500 ml")
        expect(card).to_contain_text("6,15 €")
        expect(card).to_contain_text("8,90 €")
        assert not saved
        dialog.screenshot(path=str(OUT / f"price-card-{width}-{theme}.png"))
        card.get_by_role("button", name="Confirmar").click()
        expect(card).to_contain_text("Preço gravado")
        assert saved == [{"revision": 3, "sale_net": "7.235772"}]
        # O estado da proposta segue no histórico da pergunta seguinte.
        box.fill("Mostra num gráfico a faturação por mês")
        box.press("Enter")
        expect(dialog.locator(".assistant-chart")).to_have_count(1)
        assert "Confirmado pelo utilizador e gravado" in sent[-1]["messages"][1]["content"]
        chart = dialog.locator(".assistant-chart")
        expect(chart).to_contain_text("Faturação por mês, 2026 vs 2025")
        expect(chart.locator("svg[role=img]")).to_have_count(1)
        chart.scroll_into_view_if_needed()
        dialog.screenshot(path=str(OUT / f"chart-{width}-{theme}.png"))
        chart.get_by_role("button", name="Ver tabela").click()
        expect(chart.locator("tbody tr")).to_have_count(3)
        expect(chart.locator("tbody tr").first).to_contain_text("12.345,00 €")
        # Cancelar não grava nada.
        dialog.get_by_role("button", name="Nova conversa").click()
        saved.clear()
        box.fill("Põe o óleo de coco 500 ml a 8,90 €")
        box.press("Enter")
        card = dialog.get_by_role("group", name="Alterar preço de Óleo de coco biológico 500 ml")
        card.get_by_role("button", name="Cancelar").click()
        expect(card).to_contain_text("nada foi alterado")
        assert not saved
        dialog.get_by_role("button", name="Nova conversa").click()
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        # Sem chave da API: o painel explica como configurar e não deixa enviar.
        available[0] = False
        page.reload()
        page.get_by_role("button", name="Assistente IAZELA", exact=True).click()
        dialog = page.get_by_role("dialog", name="Assistente IAZELA")
        expect(dialog.get_by_text("ANTHROPIC_API_KEY", exact=False)).to_be_visible()
        expect(dialog.get_by_label("Pergunta ao assistente", exact=True)).to_be_disabled()
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: assistente (painel, sugestões, envio, resposta formatada, fontes, estado sem chave, confirmação de preço e gráficos)")
