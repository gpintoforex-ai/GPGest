"""Receção a partir de fatura: margem, variação do custo, bónus e benefícios comerciais. Só fixtures, nada é gravado.

Reutiliza as respostas simuladas de test-invoice-receiving-ui.py e acrescenta PVP, famílias e o histórico de custos.
Requer o Vite em 127.0.0.1:5174.
"""
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "scripts/test-invoice-receiving-ui.py").read_text(encoding="utf-8")
ns = {"__file__": str(ROOT / "scripts/test-invoice-receiving-ui.py")}
exec(source.split("\nwith sync_playwright() as p:\n")[0], ns)
OUT = ROOT / ".local" / "receiving-insights-ui"
OUT.mkdir(parents=True, exist_ok=True)

# PVP sem IVA e famílias (a3: família com margem mínima própria de 40 %).
ns["ARTICLES"][0].update(sale_net="40", revision=3, vat_rate="23", unit="un", family="Articulações")
ns["ARTICLES"][2].update(sale_net="6", vat_rate="23", unit="un", family="Suplementos")
HISTORY = {
    "articles": {
        "a1": [{"date": "2026-10-01", "supplier_name": "Fornecedor Natural", "document": "FT 1/1", "quantity": "6", "unit_cost": "20.000000",
                "reference_cost": "20.00000000"}],
        "a3": [{"date": "2026-09-15", "supplier_name": "Fornecedor Natural", "document": "FT 0/9", "quantity": "2", "unit_cost": "0",
                "reference_cost": "0E-8"}],
    },
    "settings": {"margin_minimum": "30", "margin_by_family": {"Suplementos": "40"}, "cost_alert_percent": "5", "recoverable_vat": True},
}
requests, writes, price_writes = [], [], []


def intercept(route):
    path = unquote(urlsplit(route.request.url).path).removeprefix("/api")
    if path == "/stock/catalog":
        return route.fulfill(json={"articles": ns["ARTICLES"], "warehouses": [
            {"id": "w1", "store_id": "store-1", "name": "Armazém principal"}], "suppliers": [],
            "permissions": {"change_price": True}})
    if path == "/stock/articles/a1/price":
        price_writes.append(route.request.post_data_json)
        return route.fulfill(json={"saved": True})
    if path == "/purchase-receiving/cost-history":
        requests.append(route.request.url)
        ids = re.search(r"article_ids=([^&]*)", route.request.url).group(1).replace("%2C", ",").split(",")
        return route.fulfill(json={"articles": {i: HISTORY["articles"].get(i, []) for i in ids if i}, "settings": HISTORY["settings"]})
    if path.endswith("/confirm"):
        writes.append(path)
        return route.fulfill(status=500, json={"detail": "Escrita inesperada"})
    return ns["intercept"](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in ("light", "dark"):
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route("**/api/**", intercept)
        page.goto("http://127.0.0.1:5174/?pagina=Rece%C3%A7%C3%B5es%20de%20mercadoria")
        page.get_by_role("button", name="Receção a partir de fatura", exact=True).click()
        dialog = page.get_by_role("dialog", name="Receção a partir de fatura")
        dialog.get_by_role("row").filter(has_text="Fornecedor Natural").first.get_by_role("button", name="Ler fatura").click()
        expect(dialog.get_by_role("list", name="Leitura da fatura")).to_be_visible()
        # Arthron: 3 × 29,08 × (1 − 20 %) = 69,792 € ÷ 3 = 23,264 €/un.; PVP 40 € → margem 41,8 % (verde, mínimo 30 %).
        # Último custo 20,00 € → +16,3 %: acima do alerta de 5 %.
        arthron = dialog.get_by_label("Rentabilidade de ARTHRON 500")
        expect(arthron).to_contain_text("Margem comercial 41,8 % · 16,74 €/un.")
        expect(arthron.locator(".status-badge").first).to_have_attribute("data-tone", "success")
        expect(arthron).to_contain_text("+16,3 % vs último (20,00 €)")
        expect(arthron.get_by_role("status")).to_contain_text("último 20,00 € · atual 23,264 € · 16,3 %")
        arthron.get_by_text("Ver cálculos e últimas compras").click()
        detail = page.get_by_role("dialog", name="Detalhe do artigo na receção")
        detail.get_by_role("button", name="Últimas compras", exact=True).click()
        expect(detail.get_by_role("table", name="Últimas compras do artigo")).to_contain_text("FT 1/1")
        detail.get_by_role("button", name="Artigo seguinte").click()
        expect(detail).to_contain_text("Ómega 3 MaxPower 60caps")
        detail.get_by_role("button", name="Artigo anterior").click()
        expect(detail).to_contain_text("ARTHRON 500")
        detail.get_by_role("button", name="Fechar", exact=True).click()
        # Ómega (artigo novo, com bónus): sem PVP nem histórico; bónus 1 un. × 11,144 € = 11,14 € (50 %).
        omega = dialog.get_by_label("Rentabilidade de Ómega 3 MaxPower 60caps")
        expect(omega).to_contain_text("Margem indisponível: sem PVP")
        expect(omega.locator(".status-badge").first).to_have_attribute("data-tone", "neutral")
        expect(omega).to_contain_text("Artigo novo: sem histórico de custo")
        expect(omega).to_contain_text("Bónus 1,00 un · 11,14")
        expect(omega).to_contain_text("(50,0 %)")
        # Benefícios comerciais (linhas incluídas): desconto global 17,448 + 2,786 = 20,23 €; bónus 11,14 €;
        # benefício 31,378 ÷ 112,314 = 27,9 %. O resumo fiscal mantém-se.
        benefits = dialog.get_by_label("Benefícios comerciais")
        for text in ("Desconto global", "20,23 €", "Valor comercial dos bónus", "11,14 €", "27,9 %", "não mudam"):
            expect(benefits).to_contain_text(text)
        expect(dialog.get_by_label("Resumo do documento")).to_contain_text("Total calculado")
        page.screenshot(path=str(OUT / f"linhas-{theme}.png"), full_page=True)
        audit = page.evaluate((ROOT / "scripts/contrast-audit.js").read_text(encoding="utf-8"))
        assert not audit.get("failures"), audit.get("failures")
        # Associar o Ómega a um artigo com PVP 6 € (família com mínimo 40 %). Regra dos bónus: a margem comercial
        # usa o custo de referência (11,144 €, sem repartir o bónus) → -85,7 % (vermelho); a efetiva da receção
        # (5,572 €/un., com o bónus) → 7,1 %, só como ganho adicional. O último custo foi 0 €: variação não calculável.
        dialog.get_by_label("Artigo de Ómega 3 MaxPower 60caps", exact=True).select_option("a3")
        omega = dialog.get_by_label("Rentabilidade de OMEGA 3 ULTRA 60CAPS")
        expect(omega).to_contain_text("Margem comercial -85,7 %")
        expect(omega.locator(".status-badge").first).to_have_attribute("data-tone", "danger")
        expect(omega).to_contain_text("Último custo 0 €: percentagem indisponível")
        omega.get_by_text("Ver cálculos e últimas compras").click()
        expect(detail).to_contain_text("Margem efetiva da receção")
        expect(detail).to_contain_text("7,1 %")
        expect(detail.get_by_label("Margem sobre o custo (%)")).to_have_value("-56.93")
        detail.get_by_role("button", name="Últimas compras", exact=True).click()
        expect(detail).to_contain_text("+11,144 €")
        detail.get_by_role("button", name="Fechar", exact=True).click()
        # Sem o bónus, o custo de referência não muda: a margem comercial mantém-se; o bónus deixa de aparecer.
        omega_line = dialog.locator("fieldset.invoice-receiving-line").filter(has_text="Ómega 3 MaxPower 60caps")
        omega_line.get_by_label("Bónus", exact=True).fill("0")
        expect(omega).to_contain_text("Margem comercial -85,7 %")
        expect(omega.locator(".status-badge").first).to_have_attribute("data-tone", "danger")
        expect(omega).not_to_contain_text("Bónus")
        # Alterar quantidade e desconto atualiza logo: Arthron com desconto de linha de 10 % → 20,938 €/un., margem 47,7 %.
        arthron_line = dialog.locator("fieldset.invoice-receiving-line").filter(has_text="Arthron 500ml")
        arthron_line.get_by_label("Desconto (%)", exact=True).fill("10")
        expect(arthron).to_contain_text("Margem comercial 47,7 %")
        expect(benefits).to_contain_text("Descontos nas linhas")
        assert any("article_ids=" in r for r in requests), requests
        arthron.get_by_text("Ver cálculos e últimas compras").click()
        expect(detail).to_contain_text("49,20 €")
        simulation = detail.get_by_label("Simulação de preço de venda")
        markup = simulation.get_by_label("Margem sobre o custo (%)")
        expect(markup).to_have_value("37.55")
        expect(simulation).to_contain_text("40,00 €")
        expect(simulation).to_contain_text("49,20 €")
        expect(detail.get_by_role("heading", name="Comparação de custo")).to_have_count(0)
        expect(simulation.get_by_text("Margem bruta sobre a venda", exact=False)).to_have_count(0)
        expect(simulation.get_by_role("button", name="Atualizar preço de venda")).to_be_disabled()
        markup.fill("30")
        expect(simulation).to_contain_text("37,80 €")
        expect(simulation).to_contain_text("46,49 €")
        detail.get_by_label("Bónus", exact=True).fill("1")
        detail.get_by_label("Desconto (%)", exact=True).fill("15")
        expect(simulation).to_contain_text("37,80 €")
        expect(simulation).to_contain_text("46,49 €")
        detail.get_by_label("Preço unitário (€)", exact=True).fill("30")
        expect(simulation).to_contain_text("39,00 €")
        detail.get_by_label("Preço unitário (€)", exact=True).fill("29.08")
        previous_writes = len(price_writes)
        simulation.get_by_role("button", name="Atualizar preço de venda").click()
        confirmation = page.get_by_role("alertdialog", name="Atualizar preço de venda?")
        expect(confirmation).to_contain_text("37,80 €")
        confirmation.get_by_role("button", name="Cancelar").click()
        assert len(price_writes) == previous_writes
        simulation.get_by_role("button", name="Atualizar preço de venda").click()
        confirmation.get_by_role("button", name="Guardar preço de venda").click()
        expect(simulation).to_contain_text("Preço de venda atualizado.")
        expect(markup).to_have_value("29.99")
        assert price_writes[previous_writes:] == [{"sale_net": "37.80", "revision": 3}]
        expect(detail.get_by_text("37,80 €", exact=True).first).to_be_visible()
        detail.get_by_role("button", name="Fechar", exact=True).click()
        expect(arthron_line.get_by_label("Desconto (%)", exact=True)).to_have_value("15")
        expect(arthron_line.get_by_label("Bónus", exact=True)).to_have_value("1")
        assert not errors, errors
        page.close()
    browser.close()
assert not writes, writes
print("PASS: margem (verde/âmbar/vermelho/cinzento), variação do custo com alerta, bónus, benefícios comerciais e recálculo imediato; sem escritas.")
