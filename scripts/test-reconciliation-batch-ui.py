"""Grelha da reconciliação: identificação da entidade e aceitação de sugestões em conjunto.
APIs simuladas: nenhum dado real é lido ou alterado."""
import json
import re
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
                               'kind': 'pagamento', 'commission_cents': 0, 'difference_cents': 0, 'withholding_cents': 0, 'discount_cents': 0, 'reason': '',
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

    # Retenção na fonte (dados fictícios): fatura de 184,50 € (base 150,00 €) paga com 167,25 € = retenção de 11,5 %.
    withheld_bank = {**common, 'id': 'w0', 'source': 'bank', 'description': 'TRF CR SEPA+ 0001 P/ JOAO EXEMPLO', 'reference': '', 'entity': '',
                     'amount_cents': -16725, 'available_cents': 16725, 'match_classification': 'provavel'}
    invoice = {**common, 'id': 'p0', 'source': 'purchase', 'description': 'FACT 1/450', 'reference': 'FACT 1/450', 'entity': 'Joao Exemplo Silva',
               'amount_cents': -18450, 'available_cents': 18450, 'base_cents': 15000}
    proposal = {'proposal_id': 'd' * 64, 'bank_id': 'w0', 'source': 'purchase', 'source_id': 'p0', 'amount_cents': 16725, 'score': 70,
                'confidence': 'Média', 'classification': 'provavel', 'bank_lines': [{'source': 'bank', 'source_id': 'w0', 'amount_cents': 16725}],
                'reasons': ['Retenção na fonte de 11,5 % sobre 150,00 €: 17,25 € (taxa habitual: confirme)'],
                'withholding': {'cents': 1725, 'basis_points': 1150, 'base_cents': 15000, 'configured': False}}
    # Desconto financeiro (dados fictícios): 112,56 € (base 91,51 €) pagos com 109,81 € = 3 % da base.
    discount_bank = {**withheld_bank, 'id': 'w1', 'description': 'TRF CR SEPA+ 0002 P/ SAUDE EXEMPLO', 'amount_cents': -10981, 'available_cents': 10981}
    discount_invoice = {**invoice, 'id': 'p1', 'description': 'FT 2/2136', 'reference': 'FT 2/2136', 'entity': 'Saude Exemplo Lda',
                        'amount_cents': -11256, 'available_cents': 11256, 'base_cents': 9151}
    discount = {**proposal, 'proposal_id': 'e' * 64, 'bank_id': 'w1', 'source_id': 'p1', 'amount_cents': 10981,
                'bank_lines': [{'source': 'bank', 'source_id': 'w1', 'amount_cents': 10981}],
                'reasons': ['Possível desconto financeiro de 3 % sobre a base de 91,51 €: 2,75 € (confirme)'],
                'discount': {'cents': 275, 'basis_points': 300, 'on': 'base', 'basis_cents': 9151}}
    del discount['withholding']
    data.update(banks=[withheld_bank, discount_bank], internals=[invoice, discount_invoice], suggestions=[proposal, discount], card_suggestions=[])
    confirms = []
    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='pt-PT')
    page.clock.install(time=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
    page.route('**/api/**', intercept)
    page.goto('http://127.0.0.1:5174/?pagina=' + quote('Reconciliação automática'))
    row = page.locator('.reconciliation-center tbody tr', has_text='JOAO EXEMPLO')
    expect(row.locator('.reconciliation-value-match')).to_have_text('Retenção na fonte 11,5 %: 17,25 €')
    expect(row.locator('.reconciliation-value-match')).to_have_class(re.compile('is-withholding'))
    other = page.locator('.reconciliation-center tbody tr', has_text='SAUDE EXEMPLO').locator('.reconciliation-value-match')
    expect(other).to_have_text('Desconto financeiro 3 %: 2,75 €')
    expect(other).to_have_class(re.compile('is-discount'))
    expect(page.locator('.reconciliation-equal-filter')).to_contain_text('Só valores iguais (2)')
    page.get_by_label('Selecionar movimentos visíveis', exact=True).check()
    page.get_by_role('button', name='Aceitar sugestões (2)', exact=True).click()
    dialog = page.get_by_role('dialog', name='Aceitar sugestões em conjunto')
    expect(dialog.locator('tbody tr', has_text='JOAO EXEMPLO')).to_contain_text('Retenção na fonte 11,5 %: 17,25 €')
    expect(dialog.locator('tbody tr', has_text='SAUDE EXEMPLO')).to_contain_text('Desconto financeiro 3 %: 2,75 €')
    dialog.get_by_role('button', name='Aceitar 2 correspondência(s)', exact=True).click()
    expect(dialog).to_be_hidden()
    by_bank = {c['lines'][0]['source_id']: c for c in confirms}
    assert by_bank['w0'] == {'lines': [{'source': 'bank', 'source_id': 'w0', 'amount_cents': 16725}, {'source': 'purchase', 'source_id': 'p0', 'amount_cents': 18450}],
                             'kind': 'pagamento', 'commission_cents': 0, 'difference_cents': 1725, 'withholding_cents': 1725, 'discount_cents': 0,
                             'reason': 'Retenção na fonte de 11,5 % sobre 150,00 € (a entregar ao Estado)', 'post_commission': False}, confirms
    assert by_bank['w1'] == {'lines': [{'source': 'bank', 'source_id': 'w1', 'amount_cents': 10981}, {'source': 'purchase', 'source_id': 'p1', 'amount_cents': 11256}],
                             'kind': 'pagamento', 'commission_cents': 0, 'difference_cents': 275, 'withholding_cents': 0, 'discount_cents': 275,
                             'reason': 'Desconto financeiro de 3 % sobre a base de 91,51 €', 'post_commission': False}, confirms
    # Retenções na fonte: total do mês, prazo de entrega e valor previsto da obrigação.
    forecasts = []
    months = {'months': [{'kind': 'a_entregar', 'month': '2026-10', 'total_cents': 1725, 'count': 1, 'due_date': '2026-11-20',
                          'items': [{'reconciliation_id': 'g1', 'date': '2026-10-10', 'withholding_cents': 1725, 'bank': 'TRF',
                                     'documents': [{'document': 'FACT 1/450', 'entity': 'Joao Exemplo Silva'}]}],
                          'occurrence': {'id': 'o1', 'title': 'Retenções na fonte (IRS/IRC) — pagamento', 'status': 'pendente',
                                         'expected_cents': None, 'actual_cents': None, 'revision': 0}}], 'has_obligation': True}

    def withholdings(route):
        path = urlsplit(route.request.url).path.removeprefix('/api')
        if path == '/withholdings':
            return route.fulfill(json=months)
        if path == '/withholdings/forecast':
            forecasts.append(json.loads(route.request.post_data))
            months['months'][0]['occurrence'].update(expected_cents=1725, revision=1)
            return route.fulfill(json={'occurrence_id': 'o1', 'expected_cents': 1725, 'revision': 1})
        return intercept(route)
    page.unroute('**/api/**')
    page.route('**/api/**', withholdings)
    page.get_by_text('Retenções na fonte e descontos financeiros', exact=True).click()
    table = page.get_by_role('table', name='Retenções na fonte por mês')
    expect(table).to_contain_text('A entregar ao Estado')
    expect(table).to_contain_text('Até 20/11/2026')
    expect(table).to_contain_text('FACT 1/450 (Joao Exemplo Silva)')
    table.get_by_role('button', name='Usar 17,25 € como valor previsto').click()
    expect(page.get_by_text('Valor previsto da obrigação de 20/11/2026 atualizado', exact=False)).to_be_visible()
    assert forecasts == [{'month': '2026-10', 'occurrence_id': 'o1', 'revision': 0}], forecasts
    expect(table.get_by_role('button', name='Usar 17,25 € como valor previsto')).to_have_count(0)
    page.screenshot(path=str(OUT / 'withholding-1440.png'), full_page=True)
    page.close()
    browser.close()
print('Reconciliação em lote: identificação e aceitação em conjunto OK')
