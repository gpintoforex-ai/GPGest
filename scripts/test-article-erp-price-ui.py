"""PVP de um artigo da Wintouch na ficha: «Alterar preço» leva ao campo, o preço sem IVA e a margem acompanham o
valor, «Guardar» envia primeiro à Wintouch (recusa mostrada sem gravar a ficha; confirmado, grava a ficha e avisa a
loja online). API simulada (reaproveita o teste de artigos); nenhum pedido sai para a Wintouch.
Requer o Vite em 127.0.0.1:5174.
"""
import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / "scripts/test-inventory-ui.py"
ns = {"__file__": str(fixture)}
exec(fixture.read_text(encoding="utf-8").split("with sync_playwright() as p:")[0], ns)
OUT = ROOT / ".local/article-erp-price"
OUT.mkdir(parents=True, exist_ok=True)

state = {"gross": "5.5400", "outcome": "failed"}
calls = []
CHANNELS = [{"connection": "Raizpura", "state": "published", "own_price": False, "price_net": None}]


def price_state():
    return {"available": True, "reason": "", "net": "4.5000", "gross": state["gross"], "vat_rate": "23.0000", "channels": CHANNELS}


def intercept(route):
    path, method = urlsplit(route.request.url).path, route.request.method
    if re.match(r"^/api/stock/articles/erp1/erp-price$", path):
        if method == "POST":
            body = route.request.post_data_json
            calls.append(("POST", body))
            if state["outcome"] == "failed":
                return route.fulfill(json={"state": "failed", "error": "Preço inválido na Wintouch", **price_state()})
            state["gross"] = body["gross"]
            return route.fulfill(json={"state": "confirmed", "error": "", **price_state()})
        return route.fulfill(json=price_state())
    if path == "/api/stock/articles/erp1" and method == "PUT":
        calls.append(("PUT", None))
        return route.fulfill(json={"id": "erp1"})
    return ns["mock"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-PT")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", intercept)
    page.goto("http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos")
    page.get_by_label("Mais ações para Chá da Wintouch", exact=True).click()
    page.get_by_role("menuitem", name="Editar Chá da Wintouch", exact=True).click()
    dialog = page.get_by_role("dialog", name="Editar artigo", exact=True)
    dialog.get_by_role("button", name="Alterar preço").click()
    field = dialog.locator("input[name='erp_gross']")
    expect(field).to_be_focused()
    expect(field).to_have_value("5.54")
    field.fill("6.15")
    expect(dialog).to_contain_text("5,00 € (novo)")
    expect(dialog).to_contain_text("A loja online recebe o novo preço na sincronização seguinte.")
    dialog.screenshot(path=str(OUT / "precos.png"))
    # Recusa da Wintouch: a ficha não é gravada.
    dialog.get_by_role("button", name="Guardar", exact=True).click()
    expect(dialog).to_contain_text("A Wintouch recusou o preço: Preço inválido na Wintouch")
    assert calls == [("POST", {"gross": "6.15", "expected_gross": "5.5400"})], calls
    # Confirmada: grava a ficha e avisa que a loja recebe o preço.
    state["outcome"] = "confirmed"
    dialog.get_by_role("button", name="Guardar", exact=True).click()
    expect(page.get_by_text("PVP de 6,15 € gravado na Wintouch.", exact=False).first).to_be_visible()
    assert [c[0] for c in calls] == ["POST", "POST", "PUT"], calls
    assert not errors, errors
    browser.close()
print("PASS: PVP Wintouch na ficha (alterar preço, recusa sem gravar, confirmação, aviso da loja online).")
