"""Assistente IAZELA: painel no cabeçalho, sugestões, envio, resposta formatada, fontes e estado sem chave.

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
sent, available = [], [True]


def intercept(route):
    path = urlsplit(route.request.url).path.removeprefix("/api")
    if path == "/assistente/estado":
        return route.fulfill(json={"available": available[0], "model": "claude-opus-5-5",
                                   "message": "" if available[0] else "O assistente não está configurado: indique ANTHROPIC_API_KEY no ficheiro .env do servidor e reinicie a API."})
    if path == "/assistente/mensagens":
        sent.append(route.request.post_data_json)
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
        expect(suggestions.get_by_role("button")).to_have_count(8)
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
print("PASS: assistente (painel, sugestões, envio, resposta formatada, fontes e estado sem chave)")
