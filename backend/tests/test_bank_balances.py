from datetime import date, datetime, timezone
from uuid import uuid4
import importlib
import pytest
from sqlalchemy import select,func,text,inspect
from app.models import (BankAccount,BankBookEntry,BankTransaction,AccountBalanceReference,Movement,Register,User,ReconciliationLine,Reconciliation,DocumentPayment,PaymentDocumentLine,Purchase)

@pytest.fixture
def case(setup):
    client,ids,factory=setup
    with factory.begin() as db:
        store=db.get(Register,ids['registers'][0]).store_id
        user=db.scalar(select(User).where(User.email=='admin@test.pt'))
        account=BankAccount(store_id=store,name='Conta A',bank='Banco teste',iban_last4='1234',currency='EUR')
        other=BankAccount(store_id=store,name='Conta B',bank='Banco teste',iban_last4='5678',currency='EUR')
        db.add_all([account,other]);db.flush()
        for obj,amount in [(account,100000),(other,200000)]:
            db.add(AccountBalanceReference(target='bank:'+obj.id,store_id=store,amount_cents=amount,reference_date='2026-01-01',currency='EUR',user_id=user.id))
        values={'account':account.id,'other':other.id,'store':store,'user':user.id,**ids}
    return client,values,factory

def bank(case,amount,balance=125000,stamp='2026-01-31',account=None,description='Movimento banco'):
    _,ids,factory=case
    with factory.begin() as db:
        row=BankTransaction(account_id=account or ids['account'],movement_date=stamp,value_date=stamp,description=description,
            amount_cents=amount,balance_cents=balance,fingerprint=uuid4().hex,source_account='',exported_at=stamp+'T12:00:00',source_position=0)
        db.add(row);db.flush();return row.id

def movement(case,amount,stamp='2026-01-31',payment='transferencia'):
    _,ids,factory=case
    with factory.begin() as db:
        row=Movement(bank_account_id=ids['account'],user_id=ids['user'],category_id=ids['category'],kind='cliente' if amount>0 else 'fornecedor',
            direction=1 if amount>0 else -1,amount_cents=abs(amount),payment=payment,occurred_at=datetime.fromisoformat(stamp+'T12:00:00+00:00'),request_key=str(uuid4()))
        db.add(row);db.flush();return row.id

def summary(case,end='2026-01-31',account=None):
    c,ids,_=case
    response=c.get('/api/bank-balances',params={'start':'2026-01-01','end':end,'account_id':account or ids['account']})
    assert response.status_code==200,response.text
    return response.json()['accounts'][0]

def match(case,bank_id,source_id,amount,source='movement',**extra):
    response=case[0].post('/api/reconciliation/confirm',json={'lines':[{'source':'bank','source_id':bank_id,'amount_cents':amount},{'source':source,'source_id':source_id,'amount_cents':extra.pop('internal_amount',amount)}],**extra})
    assert response.status_code==200,response.text
    return response.json()['id']

def preview(case,bank_id,**extra):
    data={'kind':'payment','description':'Registo aprovado','reference':'TESTE',**extra}
    response=case[0].post('/api/bank-transactions/'+bank_id+'/resolution-preview',json=data)
    assert response.status_code==200,response.text
    return data,response.json()

def test_balances_and_confirmations_preserve_book_and_statement(case):
    incoming=movement(case,50000);outgoing=movement(case,-20000)
    b1=bank(case,50000,balance=150000,stamp='2026-01-30');b2=bank(case,-20000)
    before=summary(case)
    assert before['book_balance_cents']==130000 and before['bank_balance_cents']==125000
    assert before['difference_cents']==5000 and before['pending_application']['count']==2 and before['pending_bank']['count']==2
    match(case,b1,incoming,50000);match(case,b2,outgoing,20000)
    after=summary(case)
    assert (after['book_balance_cents'],after['bank_balance_cents'],after['difference_cents'])==(130000,125000,5000)
    assert after['pending_application']['count']==0 and after['pending_bank']['count']==0
    assert after['reconciled']['incoming_cents']==50000 and after['reconciled']['outgoing_cents']==20000 and after['reconciled']['net_cents']==30000
    with case[2]() as db:assert db.scalar(select(func.count()).select_from(BankBookEntry))==0

def test_dates_missing_information_currency_and_period_distinction(case):
    movement(case,50000,stamp='2026-01-10');bank(case,50000,stamp='2026-01-30')
    row=summary(case)
    assert row['bank_date']=='2026-01-30' and row['difference_cents'] is None and not row['comparable']
    assert summary(case,end='2026-01-30')['difference_cents']==25000
    with case[2].begin() as db:
        ref=db.get(AccountBalanceReference,'bank:'+case[1]['account']);ref.currency='USD'
    row=summary(case)
    assert row['book_balance_cents'] is None and row['difference_cents'] is None
    empty=summary(case,account=case[1]['other'])
    assert empty['bank_balance_cents'] is None and empty['book_balance_cents']==200000
    response=case[0].get('/api/bank-balances',params={'account_id':case[1]['account'],'start':'2026-01-20','end':'2026-01-31'})
    assert response.json()['accounts'][0]['pending_application']['count']==0

def test_partial_reconciliation_and_undo_preserve_originals(case):
    m=movement(case,-20000);b=bank(case,-20000)
    group=match(case,b,m,10000)
    row=summary(case)
    assert row['pending_application']['outgoing_cents']==10000 and row['pending_bank']['outgoing_cents']==10000
    assert row['reconciled']['net_cents']==-10000
    assert case[0].post('/api/reconciliation/'+group+'/undo',json={'reason':'Rever associação parcial'}).status_code==200
    row=summary(case);assert row['pending_bank']['outgoing_cents']==20000 and row['book_balance_cents']==80000
    with case[2]() as db:assert db.get(Movement,m) is not None and db.get(BankTransaction,b) is not None

def test_explicit_commission_posts_only_when_approved(case):
    m=movement(case,10300,payment='cartao');b=bank(case,10000,balance=110000)
    match(case,b,m,10000,internal_amount=10300,commission_cents=300,reason='Comissão comprovada',post_commission=True)
    row=summary(case)
    assert row['book_balance_cents']==110000 and row['bank_balance_cents']==110000
    assert row['pending_application']['count']==0
    with case[2]() as db:
        fee=db.scalar(select(BankBookEntry));assert fee.kind=='fee' and fee.amount_cents==-300

def test_missing_movement_preview_duplicate_warning_and_retry(case):
    m=movement(case,-20000);b=bank(case,-20000)
    data,p=preview(case,b)
    assert p['candidates'] and summary(case)['book_balance_cents']==80000
    body={**data,'preview_token':p['preview_token'],'request_key':str(uuid4()),'confirmed':True}
    assert case[0].post('/api/bank-transactions/'+b+'/resolve',json=body).status_code==409
    body['acknowledge_existing']=True
    result=case[0].post('/api/bank-transactions/'+b+'/resolve',json=body)
    assert result.status_code==200,result.text
    assert case[0].post('/api/bank-transactions/'+b+'/resolve',json=body).status_code==200
    assert summary(case)['book_balance_cents']==60000
    with case[2]() as db:assert db.scalar(select(func.count()).select_from(BankBookEntry))==1

def test_internal_transfer_records_two_legs_without_income_or_expense(case):
    outgoing=bank(case,-40000,balance=60000)
    incoming=bank(case,40000,balance=240000,account=case[1]['other'])
    data,p=preview(case,outgoing,kind='transfer',target_account_id=case[1]['other'])
    result=case[0].post('/api/bank-transactions/'+outgoing+'/resolve',json={**data,'preview_token':p['preview_token'],'request_key':str(uuid4()),'confirmed':True})
    assert result.status_code==200,result.text
    assert summary(case)['book_balance_cents']==60000 and summary(case,account=case[1]['other'])['book_balance_cents']==240000
    with case[2]() as db:
        entries=list(db.scalars(select(BankBookEntry)))
        assert len(entries)==2 and sum(e.amount_cents for e in entries)==0 and all(e.kind=='transfer' for e in entries)
        other=next(e for e in entries if e.account_id==case[1]['other'])
        assert db.scalar(select(func.count()).select_from(Movement))==0
    match(case,incoming,other.id,40000,source='book',kind='transferencia',reason='Transferência própria')
    assert summary(case,account=case[1]['other'])['pending_bank']['count']==0

def test_resolved_payment_links_existing_purchase_once(case):
    c,ids,factory=case;b=bank(case,-10000)
    with factory.begin() as db:
        p=Purchase(store_id=ids['store'],supplier_nif='500000001',supplier_name='Fornecedor',document='FT TESTE/1',document_date='2026-01-31',amount_cents=25000)
        db.add(p);db.flush();pid=p.id
    data,p=preview(case,b,purchase_id=pid)
    response=c.post('/api/bank-transactions/'+b+'/resolve',json={**data,'preview_token':p['preview_token'],'request_key':str(uuid4()),'confirmed':True})
    assert response.status_code==200,response.text
    overview=c.get('/api/reconciliation').json()
    assert not any(r['source']=='book' for r in overview['internals'])
    payment=next(r for r in overview['internals'] if r['source']=='payment')
    assert payment['available_cents']==0 and payment['account_id']==ids['account']
    with factory() as db:assert db.scalar(select(PaymentDocumentLine).where(PaymentDocumentLine.purchase_id==pid)).amount_cents==10000

def test_account_scope_and_cross_currency_reconciliation(case):
    c,ids,factory=case;m=movement(case,50000);b=bank(case,50000)
    with factory.begin() as db:db.get(BankAccount,ids['account']).currency='USD'
    other_bank=bank(case,50000,account=ids['other'])
    response=c.post('/api/reconciliation/confirm',json={'lines':[{'source':'bank','source_id':other_bank,'amount_cents':50000},{'source':'movement','source_id':m,'amount_cents':50000}]})
    assert response.status_code==422
    with factory.begin() as db:
        inaccessible=BankAccount(store_id=db.get(Register,ids['registers'][2]).store_id,name='Privada',bank='BPI',iban_last4='0000')
        db.add(inaccessible);db.flush();private=inaccessible.id
    assert c.post('/api/auth/login',json={'email':'reader@test.pt','password':'PasswordTest123!'}).status_code==200
    assert c.get('/api/bank-balances',params={'account_id':private}).status_code==403
    assert c.post('/api/bank-transactions/'+b+'/resolution-preview',json={'kind':'receipt','description':'Sem permissão'}).status_code==403

def test_migration_and_opening_not_counted_as_transaction(case,monkeypatch):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration=importlib.import_module('migrations.versions.0047_bank_book');helper=importlib.import_module('migrations.versions.0006_cash_sheet')
    with case[2].kw['bind'].begin() as connection:
        schema=connection.scalar(text('SELECT current_schema()'));monkeypatch.setattr(helper,'schemas',lambda _:[schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade();migration.upgrade();migration.upgrade()
        assert 'bank_book_entries' in inspect(connection).get_table_names(schema=schema)
    row=summary(case)
    assert row['book_balance_cents']==100000 and row['book_incoming_cents']==0 and row['book_outgoing_cents']==0

def test_statement_import_deduplicates_without_creating_book_records(case):
    from test_bpi_import import sample
    c,ids,factory=case
    path='/api/accounts/'+ids['account']+'/import-statement?confirm=true'
    for _ in range(2):
        response=c.post(path,files={'file':('extrato.xlsx',sample())})
        assert response.status_code==200,response.text
    assert response.json()['new_count']==0 and response.json()['duplicate_count']==1
    row=summary(case,end='2026-10-10')
    assert row['book_balance_cents']==100000 and row['bank_balance_cents']==1126717
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BankBookEntry))==0
        assert db.scalar(select(func.count()).select_from(Movement))==0
        assert db.scalar(select(func.count()).select_from(BankTransaction))==1

def test_book_reversal_is_dated_and_preserves_original(case):
    c,ids,factory=case;b=bank(case,-10000,stamp='2026-01-10')
    data,p=preview(case,b)
    result=c.post('/api/bank-transactions/'+b+'/resolve',json={**data,'preview_token':p['preview_token'],'request_key':str(uuid4()),'confirmed':True}).json()
    assert c.post('/api/reconciliation/'+result['reconciliation_id']+'/undo',json={'reason':'Corrigir registo bancário'}).status_code==200
    response=c.post('/api/bank-ledger/'+result['id']+'/reverse',json={'reason':'Registo incorreto, estorno explícito','movement_date':'2026-01-20','request_key':str(uuid4())})
    assert response.status_code==200,response.text
    assert summary(case,end='2026-01-15')['book_balance_cents']==90000
    row=summary(case)
    assert row['book_balance_cents']==100000 and row['pending_application']['count']==0
    with factory() as db:assert db.get(BankBookEntry,result['id']) is not None and db.scalar(select(func.count()).select_from(BankBookEntry))==2
