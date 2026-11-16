"""Preços de mercado lidos da página da loja: «Ler preço da ligação» preenche o registo e «Atualizar preços das
lojas» mostra o resultado por loja (alterado, igual, KuantoKusta não lido, erro). APIs simuladas; nada sai para
as lojas. Requer o Vite em 127.0.0.1:5174."""

from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
source = root / 'scripts/test-inventory-ui.py'
ns = {'__file__': str(source)}
exec(source.read_text(encoding='utf-8').split('with sync_playwright() as p:')[0], ns)
ns['market'][:] = [
    {'id': 'n1', 'store_name': 'natura24', 'price_cents': 2348, 'observed_on': '2026-11-15', 'source': 'outro',
     'url': 'https://www.natura24.pt/cerebro', 'notes': '', 'user_name': 'Teste', 'current': True},
    {'id': 'k1', 'store_name': 'Loja KK', 'price_cents': 2000, 'observed_on': '2026-11-15', 'source': 'kuantokusta',
     'url': 'https://www.kuantokusta.pt/p/1', 'notes': '', 'user_name': 'Teste', 'current': True},
]
output = root / '.local/market-price-qa'
output.mkdir(parents=True, exist_ok=True)
calls = []
REFRESH = [
    {'store_name': 'natura24', 'state': 'updated', 'previous_cents': 2348, 'price_cents': 2290, 'error': ''},
    {'store_name': 'Loja KK', 'state': 'skipped', 'previous_cents': 2000, 'price_cents': None,
     'error': 'O KuantoKusta não permite leitura automática.'},
]


def intercept(route):
    path, method = urlsplit(route.request.url).path, route.request.method
    if path.endswith('/market-prices/read') and method == 'POST':
        calls.append(('read', route.request.post_data_json))
        return route.fulfill(json={'price': '19.82', 'store_name': 'peixe verde', 'url': route.request.post_data_json['url']})
    if path.endswith('/market-prices/refresh') and method == 'POST':
        calls.append(('refresh', None))
        return route.fulfill(json={**ns['market_view'](), 'refresh': REFRESH})
    return ns['mock'](route)


with sync_playwright() as p:
    browser = p.chromium.launch()
    for width in (1440, 390):
        calls.clear()
        page = browser.new_page(viewport={'width': width, 'height': 1000}, locale='pt-PT')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', intercept)
        page.goto('http://127.0.0.1:5174/?pagina=Artigos%20e%20validades&area.stock=Artigos')
        page.get_by_label('Mais ações para Suplemento de teste', exact=True).click()
        page.get_by_role('menuitem', name='Editar Suplemento de teste', exact=True).click()
        dialog = page.get_by_role('dialog', name='Editar artigo', exact=True)
        dialog.get_by_role('tab', name='Preços e rentabilidade', exact=True).click()
        market = dialog.locator('.market-prices')
        # Atualizar: resultado por loja.
        market.get_by_role('button', name='Atualizar preços das lojas').click()
        status = market.locator('.market-refresh')
        expect(status).to_contain_text('1 preço(s) alterado(s).')
        expect(status).to_contain_text('23,48 € → 22,90 €')
        expect(status).to_contain_text('O KuantoKusta não permite leitura automática.')
        # Ler preço da ligação: preenche o preço e a loja (sem gravar).
        form = market.locator('.market-prices-form')
        read = form.get_by_role('button', name='Ler preço da ligação')
        expect(read).to_be_disabled()
        form.get_by_label('Ligação (HTTPS, opcional)').fill('https://peixeverde.pt/produto')
        read.click()
        expect(form.get_by_label('Preço com IVA (€)')).to_have_value('19.82')
        expect(form.get_by_label('Loja')).to_have_value('peixe verde')
        expect(form.get_by_label('Origem')).to_have_value('outro')
        assert calls == [('refresh', None), ('read', {'url': 'https://peixeverde.pt/produto'})], calls
        market.screenshot(path=str(output / f'read-{width}.png'))
        assert not errors, errors
        page.close()
    browser.close()
print('PASS: preços de mercado lidos da página da loja (atualizar todas, ler a ligação), ecrã largo e telemóvel.')
