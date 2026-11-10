"""Movimentos de stock: indicadores, filtros, grelha, detalhe e cartões, com APIs intercetadas; nada é gravado.

Requer o Vite em 127.0.0.1:5174.
"""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from playwright.sync_api import expect, sync_playwright

root = Path(__file__).resolve().parents[1]
fixture = root / 'scripts/test-visual-template-ui.py'
ns = {'__file__': str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('with sync_playwright() as playwright:')[0], ns)
inv = {'__file__': str(root / 'scripts/test-inventory-ui.py')}
exec(Path(inv['__file__']).read_text(encoding='utf-8').split('with sync_playwright() as p:')[0], inv)
catalog = inv['catalog']
catalog['warehouses'][0]['store_id'] = 'demo'
ns['boot']['categories'] = [{'id': 'cat', 'name': 'Compras'}]
warehouse = catalog['warehouses'][0]['name']
receipt = {'id': 'rec-1', 'kind': 'receipt', 'store_id': 'demo', 'document': 'FT 13/314251', 'document_date': '2026-10-26',
    'supplier_name': 'Fornecedor de teste', 'supplier_nif': '500000000', 'status': 'confirmed', 'revision': 1, 'confirmed_at': '2026-11-07T07:54:00Z',
    'total_cents': 4920, 'lines': [], 'email_history': [], 'attachments': []}
document_row = {'group': 'rec-1', 'occurred_on': '2026-10-26', 'created_at': '2026-11-07T07:54:32+00:00', 'lines': 2, 'articles': 2,
    'inbound': '12.000000', 'outbound': '0', 'value': '40.00', 'kinds': ['purchase'], 'warehouse_name': warehouse,
    'document': 'FT 13/314251', 'origin': 'procurement', 'procurement_id': 'rec-1', 'user_name': 'Gestor',
    'origin_label': 'Compras', 'document_type': 'Receção de mercadoria', 'entity_name': 'Fornecedor de teste'}
line_row = {'id': 'e1', 'group': 'rec-1', 'article_id': 'p1', 'warehouse_id': catalog['warehouses'][0]['id'], 'kind': 'purchase',
    'quantity': '10.000000', 'occurred_on': '2026-10-26', 'created_at': '2026-11-07T07:54:32+00:00', 'unit_cost': '3.00000000',
    'unit': 'un', 'document': 'FT 13/314251', 'origin': 'procurement', 'reason': 'Receção confirmada', 'procurement_id': 'rec-1',
    'article_name': inv['article']['name'], 'article_code': inv['article']['code'], 'warehouse_name': warehouse, 'lot_number': '',
    'expiry': '', 'user_name': 'Gestor', 'value': '30.00', 'value_delta': None, 'origin_label': 'Compras',
    'document_type': 'Receção de mercadoria', 'entity_name': 'Fornecedor de teste', 'operation_id': 'procurement:rec-1:0',
    'photo': ''}
line_row.update(lot_number='L-26', expiry='2027-03-31', stock_before='0.000000', stock_after='10.000000')
sale_row = {**line_row, 'id': 'e2', 'group': 'Wintouch|FS 1', 'kind': 'sale', 'quantity': '-2.000000', 'value': '-6.00', 'document': 'FS 1',
    'origin': 'Wintouch', 'origin_label': 'Wintouch', 'document_type': 'Venda Wintouch', 'entity_name': 'Cliente final',
    'procurement_id': None, 'reason': 'Venda Wintouch', 'operation_id': 'wt-stock:1', 'stock_before': '10.000000', 'stock_after': '8.000000'}
summary = {'count': 2, 'documents': 2, 'articles': 1, 'warehouses': [warehouse], 'transfers': 0, 'inbound': {'un': '10.000000'},
    'outbound': {'un': '2.000000'}, 'inbound_value': '30.00', 'outbound_value': '6.00', 'net_value': '24.00', 'unknown_cost': 0}
requests = []


def intercept(route):
    url = urlsplit(route.request.url)
    path = url.path.removeprefix('/api')
    if path == '/procurement':
        result = {'documents': [receipt], 'can_write': True, 'mode': {'mode': 'local'}, 'integration_note': 'Circuito local.'}
    elif path == '/stock/catalog':
        result = catalog
    elif path in ('/entities', '/purchases', '/documents/erp-catalog'):
        result = []
    elif path == '/procurement/expiry-returns':
        result = {'items': []}
    elif path == '/integrations/wintouch/sales/stock-status':
        result = {'available': True, 'can_sync': True, 'running': False, 'next_start': '2026-11-01', 'next_end': '2026-11-08',
            'control_start': '2026-11-01', 'last': {'status': 'concluida', 'finished_at': '2026-11-01T23:52:44+00:00', 'error': '',
            'period': {'start': '2026-11-01', 'end': '2026-11-01', 'store_id': 'demo'}, 'stock_status': 'aplicado',
            'stock_message': '', 'applied': 1, 'pending_count': 2, 'conflict_count': 0}}
    elif path == '/integrations/wintouch/sales/running':
        result = None
    elif path == '/procurement-ledger':
        params = {k: v[0] for k, v in parse_qs(url.query).items()}
        requests.append(params)
        documents = params.get('view') == 'documents' and not params.get('group')
        result = {'rows': [document_row] if documents else [line_row, sale_row], 'more': False, 'summary': summary}
    else:
        return ns['intercept'](route)
    route.fulfill(content_type='application/json', body=json.dumps(result))


with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme, width in [('current', 1366), ('dark', 1300), ('current', 1024), ('dark', 390)]:
        requests.clear()
        page = browser.new_page(viewport={'width': width, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('iazela-theme','{theme}')")
        page.route('**/api/**', intercept)
        page.goto('http://127.0.0.1:5174/?pagina=' + quote('Movimentos de stock'))
        desktop = width >= 1300
        expect(page.get_by_role('heading', name='Movimentos de stock', level=1)).to_be_visible()
        # A página de movimentos não mostra o acompanhamento de compras.
        expect(page.get_by_text('Acompanhamento de compras')).to_have_count(0)
        kpis = page.get_by_label('Indicadores dos movimentos filtrados')
        expect(kpis).to_contain_text('24,00')
        expect(kpis).to_contain_text('+10,00 un')
        expect(kpis).to_contain_text('−2,00 un')
        expect(kpis).to_contain_text(warehouse)
        # Abre por documento: a receção registada em novembro com fatura de outubro.
        if desktop:
            row = page.locator('.stock-ledger-table tbody tr').filter(has_text='FT 13/314251')
            expect(row).to_contain_text('Receção de mercadoria')
            expect(row).to_contain_text('Registo 07/11 07:54')
        else:
            expect(page.locator('.stock-ledger-table')).to_be_hidden()
            expect(page.locator('.stock-ledger-card').first).to_contain_text('FT 13/314251')
        assert requests[-1]['view'] == 'documents', requests[-1]
        expect(page.locator('.stock-ledger-sync')).to_contain_text('2 linha(s) por rever')
        output = root / '.local/stock-ledger-ui'
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f'documents-{theme}-{width}.png'), full_page=True)
        audit = page.evaluate((root / 'scripts/contrast-audit.js').read_text(encoding='utf-8'))
        assert not audit.get('failures'), audit.get('failures')
        assert not audit.get('overflow'), 'A página transborda na horizontal.'
        # Filtros avançados: critério de data, origem e documento entram no pedido.
        page.get_by_role('button', name=re.compile('^Filtros')).click()
        advanced = page.get_by_role('group', name='Filtros avançados')
        advanced.get_by_label('Critério de data').select_option('document')
        expect(page.get_by_text('O período usa a data do documento')).to_be_visible()
        page.wait_for_timeout(200)
        assert requests[-1].get('date_basis') == 'document', requests[-1]
        advanced.get_by_label('Origem').select_option('Wintouch')
        advanced.get_by_label('Documento').fill('FS')
        page.wait_for_timeout(700)
        assert requests[-1].get('origin') == 'Wintouch' and requests[-1].get('q') == 'FS', requests[-1]
        expect(page.get_by_role('button', name='Filtros (3)')).to_be_visible()
        advanced.get_by_role('button', name='Limpar filtros').click()
        page.wait_for_timeout(700)
        assert not requests[-1].get('origin') and not requests[-1].get('q') and requests[-1].get('date_basis') == 'both', requests[-1]
        # Filtro de artigo: escrever parte do nome filtra a lista e escolhe o artigo.
        search = page.get_by_role('combobox', name='Artigo', exact=True)
        search.fill(inv['article']['name'].split()[0][:4])
        page.get_by_role('listbox').get_by_role('option').filter(has_text=inv['article']['name']).first.click()
        page.wait_for_timeout(300)
        assert requests[-1].get('article_id') == 'p1', requests[-1]
        search.fill('')
        page.wait_for_timeout(300)
        assert not requests[-1].get('article_id'), requests[-1]
        # Por linha: tipo com cor, quantidade com sinal e detalhe lateral.
        page.get_by_role('button', name='Por linha', exact=True).click()
        expect(page.locator('.stock-ledger-footer')).to_contain_text('de 2 movimento(s)')
        if desktop:
            sale = page.locator('.stock-ledger-table tbody tr').filter(has_text='FS 1')
            expect(sale).to_contain_text('Venda')
            expect(sale.locator('.stock-ledger-qty')).to_have_text('−2,00 un')
            receipt_row = page.locator('.stock-ledger-table tbody tr').filter(has_text='FT 13/314251')
            expect(receipt_row.locator('.stock-ledger-qty')).to_have_text('+10,00 un')
            expect(receipt_row.locator('.stock-ledger-balance')).to_have_text('Stock 0,00 → 10,00')
            expect(sale.locator('.stock-ledger-balance')).to_have_text('Stock 10,00 → 8,00')
            sale.locator('summary').click()
            sale.get_by_role('button', name='Ver detalhe').click()
        else:
            page.locator('.stock-ledger-card').filter(has_text='FS 1').click()
        detail = page.get_by_role('dialog', name='Movimento de stock')
        expect(detail).to_contain_text('Venda Wintouch FS 1')
        expect(detail).to_contain_text('Importado da Wintouch')
        expect(detail).to_contain_text('L-26')
        expect(detail).to_contain_text('31/03/2027')
        expect(detail).to_contain_text('10,00 → 8,00 un')
        page.screenshot(path=str(output / f'detail-{theme}-{width}.png'))
        detail.get_by_role('button', name='Ver linhas do documento').click()
        expect(detail).to_be_hidden()
        expect(page.get_by_text('Linhas do documento')).to_be_visible()
        assert requests[-1].get('group') == 'Wintouch|FS 1' and requests[-1]['view'] == 'lines', requests[-1]
        page.screenshot(path=str(output / f'lines-{theme}-{width}.png'), full_page=True)
        audit = page.evaluate((root / 'scripts/contrast-audit.js').read_text(encoding='utf-8'))
        assert not audit.get('failures'), audit.get('failures')
        assert not audit.get('overflow'), 'A página transborda na horizontal.'
        page.get_by_role('button', name='Voltar aos documentos').click()
        expect(page.get_by_role('button', name='Por documento')).to_have_attribute('aria-pressed', 'true')
        # Sincronização das vendas Wintouch com o período seguinte proposto.
        page.get_by_role('button', name='Sincronizar vendas').click()
        dialog = page.get_by_role('dialog', name='Sincronizar movimentos Wintouch')
        expect(dialog.get_by_label('De', exact=True)).to_have_value('2026-11-01')
        expect(dialog.get_by_label('Até', exact=True)).to_have_value('2026-11-08')
        dialog.get_by_role('button', name='Fechar', exact=True).last.click()
        expect(dialog).to_be_hidden()
        assert errors == [], errors
        page.close()
    browser.close()
print('Movimentos de stock: OK')
