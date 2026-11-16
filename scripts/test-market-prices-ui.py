"""Comparação com o PVP Wintouch e alterações em edição; apenas APIs simuladas."""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
source = root / 'scripts/test-inventory-ui.py'
ns = {'__file__': str(source)}
exec(source.read_text(encoding='utf-8').split('with sync_playwright() as p:')[0], ns)
ns['erp_article'].update(erp_price='26.95', erp_price_net='21.9106', erp_vat_rate='23', erp_price_basis='Com IVA')
ns['market'][:] = [
    {'id': 'current', 'store_name': 'peixe verde', 'price_cents': 1982, 'observed_on': '2026-11-16',
     'source': 'outro', 'url': '', 'notes': '', 'user_name': 'Teste', 'current': True},
    {'id': 'old', 'store_name': 'peixe verde', 'price_cents': 1000, 'observed_on': '2026-11-01',
     'source': 'outro', 'url': '', 'notes': '', 'user_name': 'Teste', 'current': False},
]
output = root / '.local/market-price-qa'
output.mkdir(parents=True, exist_ok=True)


def intercept(route):
    path = urlsplit(route.request.url).path
    assert route.request.method == 'GET', 'A comparação não deve gravar ou enviar preços.'
    if path.endswith('/erp-price'):
        return route.fulfill(json={'available': True, 'gross': '26.9500', 'net': '21.9106', 'vat_rate': '23', 'channels': []})
    if path.endswith('/market-prices'):
        data = ns['market_view']()
        data['summary'].update(own_cents=None, stores=1, lowest_cents=1982, highest_cents=1982,
                               average_cents=1982, cheaper_stores=None, lowest_store='peixe verde')
        return route.fulfill(json=data)
    return ns['mock'](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for width in (1440, 390):
        page = browser.new_page(viewport={'width': width, 'height': 1000}, locale='pt-PT')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', intercept)
        page.goto('http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos')
        page.get_by_label('Mais ações para Chá da Wintouch', exact=True).click()
        page.get_by_role('menuitem', name='Editar Chá da Wintouch', exact=True).click()
        dialog = page.get_by_role('dialog', name='Editar artigo', exact=True)
        dialog.get_by_role('tab', name='Preços e rentabilidade', exact=True).click()
        market = dialog.locator('.market-prices')
        own = market.get_by_label('Comparação com o mercado').locator('.article-fact').first
        row = market.get_by_role('row').filter(has_text='16/11/2026')
        expect(own).to_contain_text('26,95 €')
        expect(row).to_contain_text('−7,13 € (−26,5 %)')
        expect(market).to_contain_text('1 de 1 loja(s) mais baratas')
        dialog.locator('input[name="erp_gross"]').fill('20.00')
        expect(own).to_contain_text('20,00 €')
        expect(row).to_contain_text('−0,18 € (−0,9 %)')
        dialog.locator('input[name="erp_gross"]').fill('19.82')
        expect(row).to_contain_text('Igual ao seu')
        dialog.locator('input[name="erp_gross"]').fill('18.00')
        expect(row).to_contain_text('+1,82 € (+10,1 %)')
        expect(market).to_contain_text('Mais barato ou igual em 1 loja(s)')
        expect(market.get_by_text('Todas as lojas registadas vendem abaixo do seu preço.', exact=True)).to_have_count(0)
        page.screenshot(path=str(output / f'comparison-{width}.png'))
        assert not errors, errors
        page.close()
    browser.close()
print('PASS: PVP ERP, diferença absoluta/percentual, igualdade, preço em edição e histórico; sem escritas.')
