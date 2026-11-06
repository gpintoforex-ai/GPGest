"""Grelha da reconciliação: identificação da entidade e aceitação de sugestões em conjunto.
APIs simuladas: nenhum dado real é lido ou alterado."""
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
fixture = ROOT / 'scripts/test-visual-template-ui.py'
ns = {'__file__': str(fixture)}
exec(fixture.read_text(encoding='utf-8').split('with sync_playwright() as playwright:')[0], ns)
OUT = ROOT / '.local/reconciliation-batch'
OUT.mkdir(exist_ok=True)
boot = ns['boot']
account = {'id': 'demo-account', 'store_id': 'demo', 'name': 'Conta de demonstração', 'bank': 'Banco Demo', 'iban_last4': '1234', 'currency': 'EUR'}
boot['accounts'] = [account]
common = {'store_id': 'demo', 'date': '2026-10-10', 'currency': 'EUR', 'account_id': account['id'], 'account': account['name'],
          'associated_cents': 0, 'payment': 'transferencia', 'transfer': False, 'status': 'pendente'}
banks = [{**common, 'id': f'b{i}', 'source': 'bank', 'description': f'TRF RECEBIDA {i}', 'reference': '', 'entity': '',
          'amount_cents': 10000, 'available_cents': 10000, 'match_classification': 'provavel'} for i in range(4)]
internals = [{**common, 'id': f'i{i}', 'source': 'payment', 'description': 'recebimento', 'reference': f'REC {10 + i}',
              'entity': f'Cliente {name}', 'amount_cents': 10000, 'available_cents': 10000} for i, name in enumerate(['Alfa', 'Beta', 'Gama'])]


def single(bank, internal, score, classification='provavel'):
    return {'proposal_id': (bank + internal) * 16, 'bank_id': bank, 'source': 'payment', 'source_id': internal, 'amount_cents': 10000,
            'score': score, 'confidence': 'Alta' if score >= 80 else 'Média', 'classification': classification,
            'bank_lines': [{'source': 'bank', 'source_id': bank, 'amount_cents': 10000}], 'reasons': ['Montante disponível igual']}


group = {'proposal_id': 'c' * 64, 'bank_id': 'b3', 'bank_lines': [{'source': 'bank', 'source_id': 'b3', 'amount_cents': 10000}],
         'lines': [{'source': 'payment', 'source_id': 'i2', 'amount_cents': 10000}], 'net_cents': 10000, 'gross_cents': 10000,
         'commission_cents': 0, 'kind': 'pagamento', 'confidence': 'Média', 'score': 55, 'classification': 'provavel', 'reasons': ['Agrupamento']}
data = {'banks': banks, 'internals': internals, 'groups': [], 'card_suggestions': [group],
        # b0: exata e única (fiável); b1: duas alternativas próximas; b2: o mesmo documento que b1.
        'suggestions': [single('b0', 'i0', 95, 'exata'), single('b1', 'i1', 85), single('b1', 'i2', 80), single('b2', 'i1', 65)]}

with sync_playwright() as p:
    expect.set_options(timeout=20000)
    browser = p.chromium.launch()
    for width in (1440, 390):
        errors, confirms = [], []
        page = browser.new_page(viewport={'width': width, 'height': 1000}, locale='pt-PT')
        page.clock.install(time=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
        page.on('pageerror', lambda e: errors.append(str(e)))

        def intercept(route):
            path = urlsplit(route.request.url).path.removeprefix('/api')
            if route.request.method != 'GET':
                if path == '/reconciliation/confirm':
                    body = json.loads(route.request.post_data)
                    confirms.append(body)
                    if body['lines'][0]['source_id'] == 'b1':
                        return route.fulfill(status=409, json={'detail': 'O valor excede o montante disponível. Atualize os movimentos.'})
                    return route.fulfill(json={'id': f'g{len(confirms)}'})
                return route.fulfill(status=400, json={'detail': 'Nenhuma outra escrita autorizada no teste'})
            responses = {'/bootstrap': boot, '/reconciliation': data, f'/reconciliation/accounts/{account["id"]}/period': {},
                         f'/reconciliation/accounts/{account["id"]}/rules': {'date_tolerance_days': 10, 'grouped_proposals': True},
                         f'/accounts/{account["id"]}/transactions': [], '/bank-balances': {'accounts': []}}
            if path in responses:
                return route.fulfill(json=responses[path])
            return ns['intercept'](route)

        page.route('**/api/**', intercept)
        page.goto('http://127.0.0.1:5174/?pagina=' + quote('Reconciliação automática'))
        rows = page.locator('.reconciliation-statement-panel tbody tr')
        expect(rows).to_have_count(4)
        # Filtro por valor: pesquisa pelo montante e intervalo mínimo/máximo (valor absoluto).
        search = page.get_by_label('Pesquisar movimento', exact=True)
        search.fill('100,00')
        expect(rows).to_have_count(4)
        search.fill('99,99')
        expect(rows).to_have_count(0)
        search.fill('')
        page.get_by_label('Valor mínimo (€)', exact=True).fill('150')
        expect(rows).to_have_count(0)
        page.get_by_label('Valor mínimo (€)', exact=True).fill('')
        page.get_by_label('Valor máximo (€)', exact=True).fill('100')
        expect(rows).to_have_count(4)
        page.get_by_label('Valor máximo (€)', exact=True).fill('')
        # A correspondência identifica o documento e a entidade.
        first = rows.filter(has_text='TRF RECEBIDA 0')
        expect(first.locator('.reconciliation-identity')).to_contain_text('REC 10')
        expect(first.locator('.reconciliation-identity')).to_contain_text('Cliente Alfa')
        # Proposta agrupada: mostra a entidade e cada documento, não apenas o número de registos.
        grouped_row = rows.filter(has_text='TRF RECEBIDA 3').locator('.reconciliation-identity')
        expect(grouped_row).to_contain_text('Cliente Gama')
        expect(grouped_row).to_contain_text('Pagamento REC 12 · 10/10/2026 · 100,00 €')
        expect(grouped_row.locator('.reconciliation-identity-total')).to_have_text('Total 100,00 €')
        second = rows.filter(has_text='TRF RECEBIDA 1')
        second.get_by_role('button', name='Ver propostas (2)', exact=True).click()
        expect(second.locator('.reconciliation-proposal').nth(1)).to_contain_text('Cliente Gama')
        expect(second.locator('.reconciliation-proposal').nth(1)).to_contain_text('80/100')
        second.get_by_role('button', name='Ocultar propostas', exact=True).click()

        # Fiáveis: só a exata sem alternativa.
        expect(page.get_by_text('1 sugestão(ões) fiável(eis)', exact=False)).to_be_visible()
        page.get_by_role('button', name='Aceitar sugestões fiáveis', exact=True).click()
        dialog = page.get_by_role('dialog', name='Aceitar sugestões em conjunto')
        expect(dialog.locator('tbody tr')).to_have_count(1)
        page.screenshot(path=str(OUT / f'reliable-{width}.png'))
        dialog.get_by_role('button', name='Aceitar 1 correspondência(s)', exact=True).click()
        expect(dialog).to_be_hidden()
        expect(page.get_by_text('1 correspondência(s) aceite(s) e registada(s) na auditoria.', exact=True)).to_be_visible()
        assert confirms[0] == {'lines': [{'source': 'bank', 'source_id': 'b0', 'amount_cents': 10000},
                                         {'source': 'payment', 'source_id': 'i0', 'amount_cents': 10000}],
                               'kind': 'pagamento', 'commission_cents': 0, 'difference_cents': 0, 'reason': '',
                               'post_commission': False}, confirms[0]

        # Seleção manual: b0, b1 e b2 são aceitáveis; b2 repete o documento de b1 e o agrupamento fica de fora.
        page.get_by_label('Selecionar movimentos visíveis', exact=True).check()
        page.get_by_role('button', name='Aceitar sugestões (4)', exact=True).click()
        expect(dialog.locator('tbody tr')).to_have_count(4)
        expect(dialog.locator('tbody tr', has_text='TRF RECEBIDA 2')).to_contain_text('O mesmo documento está proposto para outro movimento selecionado')
        # O agrupamento sem comissão também é aceite em conjunto, com todas as suas faturas.
        expect(dialog.get_by_label('Aceitar TRF RECEBIDA 3', exact=True)).to_be_enabled()
        expect(dialog.locator('tbody tr', has_text='TRF RECEBIDA 3')).to_contain_text('Total 100,00 €')
        expect(dialog.locator('tbody tr', has_text='TRF RECEBIDA 1')).to_contain_text('1 alternativa(s) por conferir')
        expect(dialog.get_by_label('Aceitar TRF RECEBIDA 2', exact=True)).to_be_disabled()
        page.screenshot(path=str(OUT / f'selection-{width}.png'))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}'
        dialog.get_by_role('button', name='Aceitar 3 correspondência(s)', exact=True).click()
        expect(dialog).to_be_hidden()
        assert [c['lines'][0]['source_id'] for c in confirms[1:]] == ['b0', 'b1', 'b3'], confirms
        assert confirms[-1]['lines'] == [{'source': 'bank', 'source_id': 'b3', 'amount_cents': 10000},
                                         {'source': 'payment', 'source_id': 'i2', 'amount_cents': 10000}] and confirms[-1]['kind'] == 'pagamento'
        expect(page.get_by_text('2 correspondência(s) aceite(s) e registada(s) na auditoria.', exact=True)).to_be_visible()
        expect(page.get_by_text('Por aceitar: TRF RECEBIDA 1: O valor excede', exact=False)).to_be_visible()
        assert not errors, errors
        page.close()
    browser.close()
print('Reconciliação em lote: identificação e aceitação em conjunto OK')
