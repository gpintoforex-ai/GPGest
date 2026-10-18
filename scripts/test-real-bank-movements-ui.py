"""Verify bank statements in real movements with mock responses only."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

fixture = Path(__file__).with_name('test-visual-template-ui.py')
ns = {'__file__': str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('with sync_playwright() as playwright:')[0], ns)
ns['boot']['accounts'] = [{'id': 'bank-test', 'store_id': 'demo', 'name': 'Conta teste', 'bank': 'Banco teste'}]

def intercept(route):
    path = urlsplit(route.request.url).path
    if path == '/api/accounts/bank-test/transactions':
        route.fulfill(content_type='application/json', body=json.dumps([
            {'id': 'credit', 'movement_date': '2026-10-18', 'amount_cents': 12345, 'description': 'Credito bancario teste'},
            {'id': 'debit', 'movement_date': '2026-10-17', 'amount_cents': -2500, 'description': 'Debito bancario teste'},
        ]))
    else:
        ns['intercept'](route)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.route('**/api/**', intercept)
    page.goto('http://127.0.0.1:5174/')
    page.get_by_role('button', name='Tesouraria', exact=True, include_hidden=True).first.evaluate('(e)=>e.click()')
    page.get_by_role('button', name='Movimentos reais', exact=True, include_hidden=True).evaluate('(e)=>e.click()')
    expect(page.get_by_text('Credito bancario teste', exact=True).first).to_be_visible()
    expect(page.get_by_text('Debito bancario teste', exact=True).first).to_be_visible()
    page.get_by_label('Caixas / bancos', exact=True).select_option('bank:bank-test')
    expect(page.locator('tbody tr').filter(has_text='Credito bancario teste')).to_have_count(1)
    expect(page.locator('tbody tr').filter(has_text='Debito bancario teste')).to_have_count(1)
    expect(page.locator('tbody tr').filter(has_text='Credito bancario teste').get_by_role('button', name='Estornar')).to_have_count(0)
    expect(page.get_by_text('Extrato bancário', exact=True)).to_have_count(2)
    browser.close()
print('Bank statement credits, debits and account filter: OK')
