import importlib
import json

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select, text

from app.models import Audit, BankAccount, BankReconciliationPeriod, BankTransaction, DocumentImport, Movement
from app.statement_import import Mapping, MappingError, amount, parse_camt, parse_mapped
from app.reconciliation_matching import grouped, classify
from test_bank_balances import case, bank, movement, match


def period(case):
    response = case[0].get(f'/api/reconciliation/accounts/{case[1]["account"]}/period', params={'start': '2026-01-01', 'end': '2026-01-31'})
    assert response.status_code == 200, response.text
    return response.json()


def close(case, state=None, **extra):
    return case[0].post(f'/api/reconciliation/accounts/{case[1]["account"]}/close', json={
        'start': '2026-01-01', 'end': '2026-01-31', 'signature': (state or period(case))['signature'], 'confirmed': True, **extra})


def test_closing_snapshot_stale_undo_guard_and_audited_reopen(case):
    m = movement(case, 25000); b = bank(case, 25000)
    group = match(case, b, m, 25000)
    state = period(case)
    assert not state['closing_blockers'] and state['statement_opening_cents'] == 100000
    assert state['signature'] == period(case)['signature']
    response = close(case, state)
    assert response.status_code == 200, response.text
    identifier = response.json()['id']
    assert close(case).status_code == 409
    assert not period(case)['closings'][0]['stale']
    assert case[0].post('/api/reconciliation/' + group + '/undo', json={'reason': 'Correção'}).status_code == 409
    with case[2].begin() as db:
        db.get(Movement, m).amount_cents = 26000  # External source change invalidates the review.
    assert period(case)['closings'][0]['stale']
    path = f'/api/reconciliation/accounts/{case[1]["account"]}/report'
    params = {'start': '2026-01-01', 'end': '2026-01-31', 'closing_id': identifier, 'format': 'csv'}
    report = case[0].get(path, params=params)
    assert report.status_code == 200 and '1250.00' in report.content.decode('utf-8-sig')
    params['format'] = 'pdf'
    assert case[0].get(path, params=params).content.startswith(b'%PDF')
    assert case[0].get(path.replace(case[1]['account'], case[1]['other']), params=params).status_code == 404
    response = case[0].post(f'/api/reconciliation/accounts/{case[1]["account"]}/periods/{identifier}/reopen', json={'reason': 'Rever alteração externa'})
    assert response.status_code == 200, response.text
    assert case[0].post('/api/reconciliation/' + group + '/undo', json={'reason': 'Correção após reabertura'}).status_code == 200
    with case[2]() as db:
        assert db.get(BankReconciliationPeriod, identifier).reopened_at
        assert db.scalar(select(Audit.id).where(Audit.action == 'reconciliacao_periodo_reaberto'))


def test_close_blocks_pending_unknown_and_changed_data(case):
    b = bank(case, 25000)
    state = period(case)
    assert state['closing_blockers']
    assert close(case, state).status_code == 409
    assert close(case, state, confirmed=False).status_code == 422
    movement(case, 25000)
    assert close(case, state, authorized_exception=True, reason='Pendência bancária comprovada').status_code == 409
    response = close(case, authorized_exception=True, reason='Pendência bancária comprovada')
    assert response.status_code == 200, response.text
    with case[2].begin() as db:
        db.get(BankTransaction, b).balance_cents = None
    assert period(case)['bank_balance_cents'] is None
    assert period(case)['closings'][0]['stale']


def test_rules_rejection_has_no_financial_effect(case):
    m = movement(case, 25000); b = bank(case, 25000)
    c = case[0]
    path = f'/api/reconciliation/accounts/{case[1]["account"]}/rules'
    assert c.put(path, json={'date_tolerance_days': 91, 'grouped_proposals': True}).status_code == 422
    assert c.put(path, json={'date_tolerance_days': 0, 'grouped_proposals': False}).status_code == 200
    data = c.get('/api/reconciliation').json()
    proposal = next(p for p in data['suggestions'] if p['bank_id'] == b and p['source_id'] == m)
    assert c.post('/api/reconciliation/proposals/reject', json={'proposal_id': proposal['proposal_id'], 'reason': 'Referência não corresponde'}).status_code == 200
    current = c.get('/api/reconciliation').json()
    assert not any(p['proposal_id'] == proposal['proposal_id'] for p in current['suggestions'])
    assert next(r for r in current['banks'] if r['id'] == b)['available_cents'] == 25000
    assert c.post('/api/reconciliation/proposals/reject', json={'proposal_id': proposal['proposal_id'], 'reason': 'Repetido'}).status_code == 409


def test_csv_mapping_original_dedup_and_wrong_currency(case):
    c, ids, factory = case
    path = f'/api/accounts/{ids["account"]}/import-statement'
    raw = 'Data;Descrição;Valor;Referência\n31/01/2026;Pagamento teste;-25,00;FT 123\n31/01/2026;Pagamento teste;-25,00;FT 123\n'.encode()
    def upload(data=raw, confirm=False, mapping=None):
        return c.post(path, params={'confirm': confirm}, files={'file': ('extrato.csv', data)}, data={'mapping': json.dumps(mapping)} if mapping else {})
    result = upload()
    assert result.status_code == 200, result.text
    assert result.json()['new_count'] == 2
    assert result.json()['rows'][0]['balance_cents'] is None
    assert len(c.get(f'/api/accounts/{ids["account"]}/transactions').json()) == 0
    imported = upload(confirm=True)
    assert imported.status_code == 200, imported.text
    assert all(r['original_available'] for r in c.get(f'/api/accounts/{ids["account"]}/transactions').json())
    from app.models import BankInstitution
    with factory.begin() as db:
        db.add(BankInstitution(name='Instituição de demonstração'))
    assert c.get('/api/banks').status_code == 200
    assert upload(confirm=True).json()['duplicate_count'] == 2
    original = c.get(imported.json()['original_url'])
    assert original.content == raw
    assert c.get(imported.json()['original_url'].replace(ids['account'], ids['other'])).status_code == 404
    with factory() as db:
        batch = db.get(DocumentImport, imported.json()['import_id'])
        assert batch.file_ciphertext != raw
    unknown = b'Quando;Texto;Total\n2026-01-31;Teste;-2.50\n'
    assert upload(unknown).json()['mapping_required']
    mapping = Mapping(date='Quando', description='Texto', amount='Total', decimal_separator='.').model_dump()
    assert upload(unknown, mapping=mapping).json()['rows'][0]['amount_cents'] == -250
    mapping['decimal_separator'] = ','
    assert upload(unknown, mapping=mapping).json()['mapping_required']
    assert upload(unknown, True, mapping).status_code == 422
    bad = 'Data;Descrição;Valor;Moeda\n31/01/2026;Teste;10,00;USD\n'.encode()
    mapping = Mapping(date='Data', description='Descrição', amount='Valor', currency='Moeda').model_dump()
    assert upload(bad, mapping=mapping).status_code == 422
    assert upload(bad).status_code == 422  # Currency columns are detected even without manual mapping.


@pytest.mark.parametrize('raw,separator,expected', [('1.234,56', ',', 123456), ('(1,234.56)', '.', -123456), ('-0,01', ',', -1)])
def test_decimal_normalization(raw, separator, expected):
    assert amount(raw, separator) == expected


def test_invalid_decimal_debit_credit_and_xml():
    with pytest.raises(ValueError):
        amount('1.23', ',')
    with pytest.raises(MappingError):
        parse_mapped('Data;Descrição;Débito;Crédito\n31/01/2026;Teste;2,00;3,00'.encode(), 'test.csv')
    with pytest.raises(ValueError):
        parse_camt(b'<!DOCTYPE foo [<!ENTITY a "x">]><foo/>')
    xml = b'<Document><BkToCstmrStmt><Stmt><Acct><Id><IBAN>PT5001</IBAN></Id><Ccy>EUR</Ccy></Acct><Ntry><Amt Ccy="EUR">12.50</Amt><CdtDbtInd>DBIT</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts><BookgDt><Dt>2026-01-31</Dt></BookgDt><AddtlNtryInf>Teste</AddtlNtryInf></Ntry></Stmt></BkToCstmrStmt></Document>'
    row = parse_camt(xml)['rows'][0]
    assert row['amount_cents'] == -1250 and row['balance_cents'] is None and row['mapped_account'] == 'PT5001'


def test_grouped_sums_conflicts_and_exact_requires_identified_account():
    common = {'store_id': 's', 'account_id': 'a', 'currency': 'EUR', 'date': '2026-01-10', 'status': 'pendente', 'description': 'FT DEMO/1', 'reference': 'FT DEMO/1'}
    b = {**common, 'id': 'b', 'source': 'bank', 'amount_cents': -10000, 'available_cents': 10000}
    internal = [{**common, 'id': str(i), 'source': 'movement', 'amount_cents': -value, 'available_cents': value} for i, value in enumerate([6000, 4000])]
    proposals, _ = grouped([b], internal, {})
    assert len(proposals) == 1 and len(proposals[0]['lines']) == 2
    singles, groups = classify([b], internal, [], proposals)
    assert groups[0]['classification'] == 'provavel' and not singles
    internal[1]['currency'] = 'USD'
    assert not grouped([b], internal, {})[0]
    internal = [{**common, 'id': 'm', 'source': 'movement', 'amount_cents': -10000, 'available_cents': 10000}]
    single = {'bank_id': 'b', 'source': 'movement', 'source_id': 'm', 'amount_cents': 10000, 'reasons': ['Valor e referência']}
    assert classify([b], internal, [single], [])[0][0]['classification'] == 'exata'
    internal[0]['account_id'] = None
    assert classify([b], internal, [single], [])[0][0]['classification'] == 'provavel'
    b2 = {**b, 'id': 'b2'}
    assert all(p['classification'] == 'conflito' for p in classify([b, b2], internal, [single, {**single, 'bank_id': 'b2'}], [])[0])


def test_period_write_permissions_and_scope(case):
    c, ids, factory = case
    b = bank(case, 25000)
    c.post('/api/auth/logout')
    assert c.post('/api/auth/login', json={'email': 'reader@test.pt', 'password': 'PasswordTest123!'}).status_code == 200
    path = f'/api/reconciliation/accounts/{ids["account"]}'
    assert c.put(path + '/rules', json={'date_tolerance_days': 0, 'grouped_proposals': False}).status_code == 403
    assert close(case, authorized_exception=True, reason='Não autorizado a fechar').status_code == 403
    assert c.get(path + '/period', params={'start': '2026-01-01', 'end': '2026-01-31'}).status_code == 200
    from app.models import Register
    with factory.begin() as db:
        db.get(BankAccount, ids['account']).store_id = db.get(Register, ids['registers'][2]).store_id
    assert c.get(path + '/rules').status_code == 403


def test_cancelled_documents_and_inactive_erp_sources_cannot_be_confirmed(setup):
    from app.models import DocumentERPLink, ERPDocument, Purchase, WintouchSaleRecord
    from test_reconciliation import seed, line, confirm
    c, factory, store, banks, purchases, _ = seed(setup)
    with factory.begin() as db:
        purchase = db.get(Purchase, purchases[0].id)
        erp = ERPDocument(store_id=store, enterprise_id='demo', external_id='cancelled', supplier_nif=purchase.supplier_nif,
            supplier_name=purchase.supplier_name, document=purchase.document, internal_document='', document_type='FT',
            document_date=purchase.document_date, amount_cents=purchase.amount_cents, cancelled=True)
        db.add(erp); db.flush()
        db.add(DocumentERPLink(purchase_id=purchase.id, erp_id=erp.id, reason='Documento anulado no ERP'))
        cached = WintouchSaleRecord(enterprise_id='demo', external_id='inactive', store_id=store, kind='real', document_date='2026-10-10', active=False,
            data=json.dumps({'amount_cents': 10000, 'direction': -1, 'payment': 'transferencia'}))
        db.add(cached); db.flush(); cached_id = cached.id
    rows = c.get('/api/reconciliation').json()['internals']
    assert not any(r['source'] == 'purchase' and r['id'] == purchases[0].id for r in rows)
    assert not any(r['source'] == 'wintouch' and r['id'] == cached_id for r in rows)
    assert confirm(c, [line('bank', banks[0], 10000), line('purchase', purchases[0], 10000)]).status_code == 404


def test_justified_difference_and_concurrent_closing(case):
    from concurrent.futures import ThreadPoolExecutor
    m = movement(case, 25300); b = bank(case, 25000)
    match(case, b, m, 25000, internal_amount=25300, difference_cents=-300, reason='Diferença verificada no comprovativo')
    state = period(case)
    assert state['justified_difference_cents'] == -300 and state['unresolved_difference_cents'] == 0
    assert not state['closing_blockers']
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: close(case, state).status_code, range(2)))
    assert sorted(statuses) == [200, 409]
    raw = 'Data;Descrição;Valor\n31/01/2026;Novo lançamento;-2,50\n'.encode()
    result = case[0].post(f'/api/accounts/{case[1]["account"]}/import-statement?confirm=true', files={'file': ('novo.csv', raw)})
    assert result.status_code == 409


@pytest.mark.fresh_schema
def test_migration_preserves_existing_balances_and_is_repeatable(setup, monkeypatch):
    engine = setup[2].kw['bind']
    with engine.begin() as connection:
        schema = connection.scalar(text('SELECT current_schema()'))
        assert schema.startswith('test_')
        BankReconciliationPeriod.__table__.drop(connection)
        connection.execute(text('ALTER TABLE bank_transactions DROP COLUMN reference'))
        connection.execute(text('ALTER TABLE bank_transactions ALTER COLUMN balance_cents SET NOT NULL'))
        connection.execute(text('ALTER TABLE document_imports DROP COLUMN file_ciphertext'))
        monkeypatch.setattr(importlib.import_module('migrations.versions.0006_cash_sheet'), 'schemas', lambda _: [schema])
        module = importlib.import_module('migrations.versions.0075_reconciliation_center')
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade(); module.upgrade()
        inspector = inspect(connection)
        assert inspector.has_table('bank_reconciliation_periods', schema=schema)
        assert next(c for c in inspector.get_columns('bank_transactions', schema=schema) if c['name'] == 'balance_cents')['nullable']
