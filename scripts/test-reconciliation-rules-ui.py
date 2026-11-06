"""Reconciliação: movimentos sem documento (Resolver, regras por descrição e aceitação em conjunto).
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
OUT = ROOT / '.local/reconciliation-rules'
OUT.mkdir(exist_ok=True)
boot = ns['boot']
account = {'id': 'demo-account', 'store_id': 'demo', 'name': 'Conta de demonstração', 'bank': 'Banco Demo', 'iban_last4': '1234', 'currency': 'EUR'}
boot['accounts'] = [account]
boot['categories'] = boot.get('categories') or [{'id': 'cat-ss', 'name': 'Encargos sociais', 'active': True}]
category = boot['categories'][0]
common = {'store_id': 'demo', 'date': '2026-10-10', 'currency': 'EUR', 'account_id': account['id'], 'account': account['name'],
          'associated_cents': 0, 'payment': 'transferencia', 'transfer': False, 'status': 'pendente', 'source': 'bank', 'reference': '', 'entity': ''}
banks = [{**common, 'id': 'b5', 'description': 'DD SEG SOCIAL 1234567', 'amount_cents': -25000, 'available_cents': 25000, 'match_classification': 'sem_correspondencia'},
         {**common, 'id': 'b6', 'description': 'PAG ESTADO IUC 998877', 'amount_cents': -4500, 'available_cents': 4500, 'match_classification': 'sem_correspondencia'}]
rule = {'id': 'r1', 'name': 'Segurança Social', 'pattern': 'seg social', 'account_id': None, 'account_name': 'Todas as contas', 'direction': -1,
        'kind': 'payment', 'kind_label': 'Pagamento', 'category_id': category['id'], 'category_name': category['name'], 'employee_id': None,
        'employee_name': '', 'target_account_id': None, 'target_account_name': '', 'description': 'Segurança Social', 'counts_as_cost': True, 'active': True}
data = {'banks': banks, 'internals': [], 'groups': [], 'card_suggestions': [], 'suggestions': [],
        'rule_suggestions': [{'bank_id': 'b5', 'rule': rule, 'alternatives': 0}]}


def preview_of(bank_id, body):
    bank = next(b for b in banks if b['id'] == bank_id)
    return {'account_id': account['id'], 'currency': 'EUR', 'date': bank['date'], 'amount_cents': bank['amount_cents'],
            'entries': [{'account_id': account['id'], 'account_name': 'Banco Demo · Conta', 'amount_cents': bank['amount_cents']}],
            'kind': body['kind'], 'description': body['description'], 'reference': body.get('reference', ''), 'target_account_id': None,
            'category_name': category['name'] if body.get('category_id') else '', 'employee_name': '',
            'counts_as_cost': body.get('counts_as_cost', False), 'rule': body.get('rule'), 'candidates': [],
            'preview_token': 'f' * 64, 'effect': 'Cria registo(s) explícito(s) na aplicação e associa o remanescente do extrato.'}


with sync_playwright() as p:
    expect.set_options(timeout=20000)
    browser = p.chromium.launch()
    for width in (1440, 390):
        errors, writes = [], []
        page = browser.new_page(viewport={'width': width, 'height': 1000}, locale='pt-PT')
        page.clock.install(time=datetime(2026, 10, 12, 12, tzinfo=timezone.utc))
        page.on('pageerror', lambda e: errors.append(str(e)))

        def intercept(route):
            path = urlsplit(route.request.url).path.removeprefix('/api')
            if route.request.method != 'GET':
                body = json.loads(route.request.post_data or '{}')
                writes.append((path, body))
                if path.endswith('/resolution-preview'):
                    return route.fulfill(json=preview_of(path.split('/')[2], body))
                if path.endswith('/resolve'):
                    return route.fulfill(json={'id': 'e' + str(len(writes))})
                if path == '/bank-rules':
                    return route.fulfill(json={'id': 'r2'})
                return route.fulfill(status=400, json={'detail': 'Nenhuma outra escrita autorizada no teste'})
            responses = {'/bootstrap': boot, '/reconciliation': data, f'/reconciliation/accounts/{account["id"]}/period': {},
                         f'/reconciliation/accounts/{account["id"]}/rules': {'date_tolerance_days': 10, 'grouped_proposals': True},
                         f'/accounts/{account["id"]}/transactions': [], '/bank-balances': {'accounts': []}, '/purchases': [],
                         '/employees': [{'id': 'm1', 'name': 'Ana Funcionária', 'active': True}], '/bank-rules': [rule]}
            if path in responses:
                return route.fulfill(json=responses[path])
            return ns['intercept'](route)

        page.route('**/api/**', intercept)
        page.goto('http://127.0.0.1:5174/?pagina=' + quote('Reconciliação automática'))
        rows = page.locator('.reconciliation-statement-panel tbody tr')
        expect(rows).to_have_count(2)
        social = rows.filter(has_text='DD SEG SOCIAL')
        expect(social.locator('.reconciliation-identity')).to_contain_text('Segurança Social')
        expect(social.locator('.reconciliation-identity')).to_contain_text(f"Rubrica {category['name']}")
        expect(page.get_by_role('button', name='Sugestões (1)', exact=True)).to_be_visible()

        # Registar pela regra: a janela «Resolver» vem preenchida e continua a exigir pré-visualização.
        social.get_by_role('button', name='Registar pela regra «Segurança Social»: DD SEG SOCIAL 1234567', exact=True).click()
        dialog = page.get_by_role('dialog', name='Resolver movimento bancário')
        expect(dialog.get_by_text('Proposto pela regra «Segurança Social».', exact=False)).to_be_visible()
        expect(dialog.locator('label', has_text='Rubrica').locator('select')).to_have_value(category['id'])
        expect(dialog.get_by_label('Conta como custo nos mapas de gestão e no Dashboard', exact=True)).to_be_checked()
        dialog.get_by_role('button', name='Pré-visualizar e procurar duplicações', exact=True).click()
        expect(dialog.get_by_text(f"Rubrica: {category['name']} · Conta como custo", exact=True)).to_be_visible()
        page.screenshot(path=str(OUT / f'rule-{width}.png'))
        dialog.get_by_role('button', name='Aprovar registo e associação', exact=True).click()
        page.get_by_role('alertdialog').get_by_role('button', name='Registar e associar', exact=True).click()
        expect(dialog).to_be_hidden()
        resolved = [b for path, b in writes if path == '/bank-transactions/b5/resolve'][-1]
        assert resolved['category_id'] == category['id'] and resolved['counts_as_cost'] is True and resolved['rule'] is None, resolved

        # Resolver sem regra: guardar como regra com o texto sugerido (até à primeira palavra com algarismos).
        rows.filter(has_text='PAG ESTADO').get_by_role('button', name='Resolver sem documento: PAG ESTADO IUC 998877', exact=True).click()
        dialog.get_by_label('Descrição / motivo', exact=True).fill('IUC da viatura')
        dialog.locator('label', has_text='Rubrica').locator('select').select_option(category['id'])
        dialog.get_by_label('Guardar como regra para os próximos movimentos com o mesmo texto', exact=True).check()
        expect(dialog.get_by_label('Texto na descrição do extrato', exact=True)).to_have_value('PAG ESTADO IUC')
        dialog.get_by_role('button', name='Pré-visualizar e procurar duplicações', exact=True).click()
        expect(dialog.get_by_text('Fica guardada a regra «IUC da viatura» para descrições com «PAG ESTADO IUC» nesta conta.', exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}'
        dialog.get_by_role('button', name='Manter pendente / fechar', exact=True).click()

        # Aceitação em conjunto: a regra passa pelo mesmo circuito (pré-visualização e confirmação).
        social.get_by_label('Selecionar DD SEG SOCIAL 1234567', exact=True).check()
        page.get_by_role('button', name='Aceitar sugestões (1)', exact=True).click()
        batch = page.get_by_role('dialog', name='Aceitar sugestões em conjunto')
        expect(batch.locator('tbody tr')).to_contain_text('Regra por descrição')
        before = len(writes)
        batch.get_by_role('button', name='Aceitar 1 correspondência(s)', exact=True).click()
        expect(batch).to_be_hidden()
        assert [path for path, _ in writes[before:]] == ['/bank-transactions/b5/resolution-preview', '/bank-transactions/b5/resolve'], writes[before:]
        expect(page.get_by_text('1 correspondência(s) aceite(s) e registada(s) na auditoria.', exact=True)).to_be_visible()

        # Gestão das regras.
        page.get_by_role('button', name='Regras por descrição', exact=True).click()
        rules = page.get_by_role('dialog', name='Regras por descrição')
        expect(rules.locator('tbody tr')).to_contain_text('seg social')
        rules.get_by_role('button', name='Nova regra', exact=True).click()
        rules.get_by_label('Nome', exact=True).fill('Vencimento Ana')
        rules.get_by_label('Texto na descrição do extrato', exact=True).fill('TRF VENCIMENTO')
        rules.get_by_label('Rubrica', exact=True).select_option(category['id'])
        rules.get_by_label('Funcionário (opcional)', exact=True).select_option('m1')
        page.screenshot(path=str(OUT / f'rules-{width}.png'))
        rules.get_by_role('button', name='Criar regra', exact=True).click()
        created = [b for path, b in writes if path == '/bank-rules'][-1]
        assert created['employee_id'] == 'm1' and created['counts_as_cost'] is True and created['direction'] == -1 and 'id' not in created, created
        assert not errors, errors
        page.close()
    browser.close()
print('Reconciliação: Resolver, regras por descrição e aceitação em conjunto OK')
