"""Receção a partir de fatura: «Confirmar receção» fica disponível e leva à informação a corrigir.

Valida no ecrã (tipo de documento, lote num artigo sem lotes, entrega superior) e encaminha os erros do
servidor («Linha N: …») para a linha. Só APIs intercetadas (reaproveita o teste da ligação à encomenda).
Requer o Vite em 127.0.0.1:5174.
"""

from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "scripts/test-invoice-order-link-ui.py"
ns = {"__file__": str(source)}
exec(source.read_text(encoding="utf-8").rsplit("\nwith sync_playwright() as p:", 1)[0], ns)
OUT = ROOT / ".local" / "invoice-receiving-guidance-ui"
OUT.mkdir(parents=True, exist_ok=True)
sent, refuse = [], [True]


def intercept(route):
    path = unquote(urlsplit(route.request.url).path).removeprefix("/api")
    if path == "/purchase-receiving/d1/confirm":
        sent.append(route.request.post_data_json)
        if refuse[0]:
            refuse[0] = False
            return route.fulfill(status=422, json={"detail": "Linha 2: a quantidade não respeita as casas decimais de «Ómega 3»."})
        return route.fulfill(json={"receipt_id": "r1", "lines": 2, "created_articles": 1, "learned": 0})
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for width in (1440, 390):
        sent.clear()
        refuse[0] = True
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
        page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
        dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
        dialog.get_by_role("row").filter(has_text="Fornecedor Natural").get_by_role("button", name="Ler fatura").click()
        confirm = dialog.get_by_role("button", name="Confirmar receção (2)")
        # Sem tipo de documento: o botão está disponível e leva ao campo, com a mensagem.
        expect(confirm).to_be_enabled()
        confirm.click()
        expect(dialog.get_by_text("Escolha o tipo de documento", exact=False).first).to_be_visible()
        document_type = dialog.get_by_label("Tipo de documento", exact=True)
        expect(document_type).to_be_focused()
        expect(document_type).to_have_class("needs-attention")
        document_type.select_option("t-fc")
        # Lote num artigo sem controlo de lotes: leva ao interruptor «Ativar …» da linha.
        confirm.click()
        expect(dialog.get_by_text("Linha 1: «ARTHRON 500» não controla lotes.", exact=False).first).to_be_visible()
        toggle = dialog.locator("fieldset.invoice-receiving-line").first.locator(".invoice-receiving-lots input[type=checkbox]")
        expect(toggle).to_be_focused()
        # A mensagem repete-se na própria linha (o topo do formulário pode ficar fora de vista).
        expect(dialog.locator("fieldset.invoice-receiving-line").first).to_contain_text("Ative «Controlar lotes e validades»")
        page.wait_for_timeout(700)
        dialog.screenshot(path=str(OUT / f"lots-{width}.png"))
        toggle.evaluate("el => el.click()")
        # Entrega superior por autorizar: leva ao quadro da encomenda.
        dialog.locator("fieldset.invoice-receiving-line").first.get_by_label("Quantidade", exact=True).fill("5")
        confirm.click()
        over = dialog.get_by_label("Aceitar as quantidades acima do encomendado (entrega superior)")
        expect(over).to_be_focused()
        assert not sent
        over.evaluate("el => el.click()")
        # Erro do servidor numa linha: a mensagem fica visível e o foco vai para essa linha.
        confirm.click()
        expect(dialog.get_by_text("Linha 2: a quantidade não respeita", exact=False).first).to_be_visible()
        second = dialog.locator("fieldset.invoice-receiving-line").nth(1)
        expect(second.locator(":focus")).to_have_count(1)
        expect(second).to_contain_text("Linha 2: a quantidade não respeita")
        expect(dialog.locator("fieldset.invoice-receiving-line").first).not_to_contain_text("Ative «Controlar lotes e validades» para este artigo")
        assert len(sent) == 1
        confirm.click()
        expect(page.get_by_text("Receção registada: 2 linhas em stock", exact=False)).to_be_visible()
        assert len(sent) == 2 and sent[1]["lines"][0]["enable_lots"] is True
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: confirmar a receção a partir de fatura leva à informação a corrigir (ecrã e servidor)")
