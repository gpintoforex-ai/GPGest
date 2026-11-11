"""Isolated demonstration fixtures: no live data is read or changed."""
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / 'scripts/test-visual-template-ui.py'
ns = {'__file__': str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('with sync_playwright() as playwright:')[0], ns)
OUT = ROOT / '.local/reconciliation-center'
OUT.mkdir(exist_ok=True)
boot = ns['boot']
account = {'id': 'demo-account', 'store_id': 'demo', 'name': 'Conta de demonstração', 'bank': 'Banco Demo', 'iban_last4': '1234', 'currency': 'EUR'}
boot['accounts'] = [account]
common = {'store_id': 'demo', 'date': '2026-10-10', 'currency': 'EUR', 'account_id': account['id'], 'account': account['name'], 'associated_cents': 0, 'reference': 'DEMO/1', 'payment': 'transferencia', 'entity': 'Entidade de demonstração'}
banks = [{**common, 'id': f'b{i}', 'source': 'bank', 'description': f'Movimento de demonstração {i}', 'amount_cents': 10000, 'available_cents': 10000, 'status': 'pendente', 'match_classification': 'provavel' if i < 2 else 'sem_correspondencia'} for i in range(3)]
banks.append({**banks[0], 'id': 'b3', 'status': 'reconciliado', 'available_cents': 0, 'match_classification': 'conciliado'})
internals = [{**common, 'id': f'm{i}', 'source': 'movement', 'description': f'Registo de demonstração {i}', 'amount_cents': 5000, 'available_cents': 5000, 'status': 'pendente'} for i in range(2)]
group = {'proposal_id': 'a'*64, 'bank_id': 'b0', 'bank_lines': [{'source': 'bank', 'source_id': 'b0', 'amount_cents': 10000}], 'lines': [{'source': 'movement', 'source_id': r['id'], 'amount_cents': 5000} for r in internals], 'net_cents': 10000, 'gross_cents': 10000, 'commission_cents': 0, 'kind': 'pagamento', 'confidence': 'Média', 'score': 55, 'classification': 'provavel', 'reasons': ['Soma dos dois registos de demonstração']}
# Proposta com valor diferente (90,00 € para um movimento de 100,00 €): fica de fora do filtro «Só valores iguais».
partial = {**common, 'id': 'm9', 'source': 'movement', 'description': 'Registo parcial de demonstração', 'amount_cents': 9000, 'available_cents': 9000, 'status': 'pendente'}
different = {'proposal_id': 'c'*64, 'bank_id': 'b1', 'source': 'movement', 'source_id': 'm9', 'amount_cents': 9000, 'score': 45, 'confidence': 'Baixa',
             'classification': 'provavel', 'reasons': ['Possível pagamento parcial'], 'bank_lines': [{'source': 'bank', 'source_id': 'b1', 'amount_cents': 9000}]}
data = {'banks': banks, 'internals': [*internals, partial], 'suggestions': [different], 'card_suggestions': [group], 'groups': []}
state = {'name': account['name'], 'currency': 'EUR', 'book_balance_cents': 10000, 'bank_balance_cents': 10000, 'opening_cents': 0, 'statement_opening_cents': 0, 'statement_incoming_cents': 10000, 'statement_outgoing_cents': 0, 'justified_difference_cents': 0, 'unresolved_difference_cents': 0, 'comparable': True, 'pending_bank': {'count': 3}, 'pending_application': {'count': 2}, 'card_pending_settlement': {'count': 0, 'amount_cents': 0}, 'closing_blockers': ['Existem movimentos por reconciliar.'], 'closings': [], 'signature': 'b'*64}

with sync_playwright() as p:
    expect.set_options(timeout=20000)
    browser = p.chromium.launch()
    for width in (1440, 768, 390):
        errors, writes = [], []
        page = browser.new_page(viewport={'width': width, 'height': 1000}, locale='pt-PT')
        page.clock.install(time=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
        page.on('pageerror', lambda e: errors.append(str(e)))
        def intercept(route):
            path = urlsplit(route.request.url).path.removeprefix('/api')
            if route.request.method != 'GET':
                writes.append(path)
                return route.fulfill(status=400, json={'detail': 'Nenhuma escrita autorizada no teste visual'})
            responses = {'/bootstrap': boot, '/reconciliation': data, f'/reconciliation/accounts/{account["id"]}/period': state,
                f'/reconciliation/accounts/{account["id"]}/rules': {'date_tolerance_days': 10, 'grouped_proposals': True},
                f'/accounts/{account["id"]}/transactions': [], '/bank-balances': {'accounts': []}}
            if path in responses:
                return route.fulfill(json=responses[path])
            return ns['intercept'](route)
        page.route('**/api/**', intercept)
        page.goto('http://127.0.0.1:5174/?pagina=' + quote('Reconciliação automática'))
        expect(page.get_by_role('heading', name='Reconciliação bancária', exact=True)).to_be_visible()
        expect(page.locator('.reconciliation-center tbody tr')).to_have_count(4)
        page.get_by_label('Conta da reconciliação', exact=True).select_option(account['id'])
        expect(page.locator('.reconciliation-summary-strip')).to_be_visible()
        expect(page.get_by_role('img', name='Progresso da reconciliação: 25%', exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name='Ações rápidas', exact=True)).to_be_visible()
        if width == 1440:
            grid = page.locator('.reconciliation-statement-panel').bounding_box()
            assert grid['y'] < 570, f'The statement grid should be visible near the top: {grid}'
            cards = page.locator('.reconciliation-center-bottom > section').all()
            assert max(c.bounding_box()['y'] for c in cards) - min(c.bounding_box()['y'] for c in cards) < 2
        page.get_by_role('button', name='Por reconciliar (3)', exact=True).click()
        expect(page.locator('.reconciliation-center tbody tr')).to_have_count(3)
        equal = page.locator('.reconciliation-equal-filter')
        expect(equal).to_contain_text('Só valores iguais (1)')
        equal.locator('input').check()
        expect(page.locator('.reconciliation-center tbody tr')).to_have_count(1)
        expect(page.locator('.reconciliation-center tbody tr')).to_contain_text('Movimento de demonstração 0')
        equal.locator('input').uncheck()
        expect(page.locator('.reconciliation-center tbody tr')).to_have_count(3)
        page.get_by_label('Pesquisar movimento', exact=True).fill('demonstração 0')
        expect(page.locator('.reconciliation-center tbody tr')).to_have_count(1)
        page.get_by_role('button', name='Ver propostas (1)', exact=True).click()
        expect(page.get_by_role('button', name='Rever e aceitar', exact=True)).to_be_visible()
        page.get_by_label('Pesquisar movimento', exact=True).fill('')
        page.get_by_role('button', name='Ocultar propostas', exact=True).click()
        page.screenshot(path=str(OUT/f'dashboard-{width}.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}'
        page.get_by_role('button', name='Regras, relatórios e fecho', exact=True).click()
        expect(page.get_by_role('heading', name='Fecho e relatório da reconciliação', exact=True)).to_be_visible()
        page.get_by_label('Ordenar relatório PDF', exact=True).select_option('amount:desc')
        page.get_by_label('Incluir anexo técnico no PDF', exact=True).check()
        pdf_link = page.get_by_role('link', name='PDF de consulta', exact=True)
        expect(pdf_link).to_have_attribute('href', re.compile(r'sort_by=amount.*descending=true.*audit=true'))
        page.get_by_role('button', name='Rever propostas', exact=True).click()
        expect(page.get_by_text('Correspondência agrupada', exact=False)).to_be_visible()
        page.get_by_role('button', name='Selecionar agrupamento', exact=True).click()
        expect(page.get_by_role('heading', name='Confirmar associação', exact=True)).to_be_visible()
        expect(page.locator('.reconciliation-selection-bar')).to_contain_text('3')
        page.get_by_role('button', name='Movimentos do extrato', exact=True).click()
        page.get_by_label('Selecionar movimentos visíveis', exact=True).check()
        expect(page.locator('.reconciliation-grid-selection')).to_contain_text('3 movimento(s)')
        page.get_by_role('button', name='Rever seleção', exact=True).click()
        expect(page.locator('.reconciliation-selection-bar')).to_contain_text('3')
        if width < 1000:
            page.get_by_role('button', name='Abrir menu', exact=True).click()
        page.get_by_role('navigation', name='Navegação principal', exact=True).get_by_role('button', name='Reconciliação bancária', exact=True).click()
        expect(page.locator('.reconciliation-dashboard')).to_be_visible()
        assert 'area.reconciliacao-modo=grelha' in page.url
        assert not writes, writes  # Reviewing never confirms financial changes.
        assert not errors, errors
        page.close()
    banks.extend({**banks[2], 'id': f'large-{i}', 'description': f'Movimento adicional de demonstração {i}'} for i in range(60))
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='pt-PT')
    page.clock.install(time=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
    page.route('**/api/**', intercept)
    page.goto('http://127.0.0.1:5174/?pagina=' + quote('Reconciliação automática'))
    expect(page.locator('.reconciliation-dashboard tbody tr')).to_have_count(50)
    table = page.locator('.reconciliation-statement-panel .table-wrap').bounding_box()
    assert table['height'] <= 422, f'The statement must scroll inside the panel: {table}'
    bottom = page.locator('.reconciliation-center-bottom').bounding_box()['y']
    assert bottom < 1100, bottom
    page.screenshot(path=str(OUT/'dashboard-64-movements.png'), full_page=True)
    browser.close()
print('PASS: dashboard, account/period, search, filters, grouped review, no automatic writes, desktop/tablet/mobile.')
