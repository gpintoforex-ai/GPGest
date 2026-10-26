import json
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import select
from app.models import BusinessEntity, DocumentPayment, ProcurementDocument, ProcurementLink, Purchase, StockEntry, PlannedMovement
from test_inventory import stock_case, product, view, move, login


@pytest.fixture
def case(stock_case, monkeypatch):
    monkeypatch.setattr('app.procurement.policy', lambda _: {'mode':'local','provider':'none'})
    with stock_case[2].begin() as db:
        s=BusinessEntity(name='Fornecedor Compras',tax_number='500000000',is_supplier=True,category_id=stock_case[1]['category'],email='fornecedor@example.com')
        db.add(s);db.flush();sid=s.id
    return stock_case,sid

def draft(case, kind='order', lines=None, **extra):
    stock,sid=case
    if lines is None:
        article=product(stock)
        lines=[{'article_id':article,'description':'Artigo teste','quantity':'20','price':'10','vat':'23','warehouse_id':stock[3][0]}]
    data={'kind':kind,'store_id':stock[4],'supplier_id':sid,'document':kind+'-'+uuid4().hex[:10],
        'document_date':date.today().isoformat(),'due_date':date.today().isoformat(),'category_id':stock[1]['category'],
        'request_key':str(uuid4()),'lines':lines,**extra}
    response=stock[0].post('/api/procurement',json=data)
    assert response.status_code==200,response.text
    return response.json()

def confirm(case, row, code=200, **extra):
    response=case[0][0].post('/api/procurement/'+row['id']+'/confirm',json={'revision':row['revision'],**extra})
    assert response.status_code==code,response.text
    return response.json()

def from_source(case, source, kind, quantity, **extra):
    line={k:v for k,v in source['lines'][0].items() if k in __import__('app.procurement',fromlist=['Line']).Line.model_fields}
    line.update(source_id=source['id'],source_line=0,quantity=str(quantity),rejected='0')
    line.update(extra)
    return draft(case,kind,lines=[line],document_type='NC' if kind=='credit' else 'FT')

def test_warehouse_removal_preserves_procurement_drafts(case):
    stock, _ = case
    document = draft(case)
    response = stock[0].post(
        f"/api/stock/warehouses/{stock[3][0]}/delete", json={"confirmed": True}
    )
    assert response.status_code == 409, response.text
    assert "documentos de compra" in response.json()["detail"]
    with stock[2]() as db:
        assert db.get(ProcurementDocument, document["id"]) is not None
    assert stock[3][0] in [w["id"] for w in stock[0].get("/api/stock/catalog").json()["warehouses"]]


def test_full_circuit_partial_receipts_credit_and_payment(case):
    stock,_=case;c=stock[0]
    order=confirm(case,draft(case));aid=order['lines'][0]['article_id']
    assert view(stock,aid)['quantity']=='0'
    r1=confirm(case,from_source(case,order,'receipt',12))
    repeat=confirm(case,r1)
    assert repeat['id']==r1['id'] and D(view(stock,aid)['quantity'])==12
    invoice=confirm(case,from_source(case,r1,'invoice',12))
    assert D(view(stock,aid)['quantity'])==12
    r2=confirm(case,from_source(case,order,'receipt',8))
    assert D(view(stock,aid)['quantity'])==20
    returned=confirm(case,from_source(case,r1,'return',2,reason='Defeito encontrado'))
    assert D(view(stock,aid)['quantity'])==18
    credit=confirm(case,from_source(case,returned,'credit',2))
    assert D(view(stock,aid)['quantity'])==18
    assert credit['purchase_id']!=invoice['purchase_id']
    payment=c.post('/api/document-payments',json={'store_id':stock[4],'payment_date':date.today().isoformat(),'amount_cents':5000,'direction':-1,
        'reference':'Pagamento parcial','request_key':str(uuid4()),'lines':[{'purchase_id':invoice['purchase_id'],'amount_cents':5000}]})
    assert payment.status_code==200,payment.text
    docs=c.get('/api/procurement').json()['documents']
    inv=next(d for d in docs if d['id']==invoice['id'])
    assert inv['paid_cents']==5000 and inv['remaining_cents']==9760 and inv['payment_status']=='partial'
    ord=next(d for d in docs if d['id']==order['id'])
    assert ord['status']=='received'
    planned=c.get('/api/planned-movements').json()
    plan=next(p for p in planned if p['purchase_id']==invoice['purchase_id'])
    assert plan['remaining_cents']==9760
    with stock[2]() as db:
        assert len(list(db.scalars(select(StockEntry).where(StockEntry.article_id==aid))))==3
        assert len(list(db.scalars(select(Purchase))))==2

def test_direct_goods_and_service_no_stock(case):
    stock,_=case
    inv=confirm(case,draft(case,'invoice'))
    aid=inv['lines'][0]['article_id']
    assert D(view(stock,aid)['quantity'])==20
    docs=stock[0].get('/api/procurement').json()['documents']
    receipt=next(d for d in docs if d['id']==inv['direct_receipt_id'])
    assert receipt['lines'][0]['invoiced']=='20.000000'
    confirm(case,from_source(case,receipt,'return',2,reason='Erro de entrega'))
    assert D(view(stock,aid)['quantity'])==18
    with stock[2]() as db: before=len(list(db.scalars(select(StockEntry))))
    service=confirm(case,draft(case,'invoice',lines=[{'description':'Serviço técnico','nature':'service','unit':'h','quantity':'2','price':'30','vat':'23'}]))
    assert service['remaining_cents']==7380
    with stock[2]() as db: assert len(list(db.scalars(select(StockEntry))))==before

def test_overdelivery_rejected_and_conflicting_sources(case):
    stock,_=case
    order=confirm(case,draft(case))
    r=from_source(case,order,'receipt',22)
    confirm(case,r,409)
    assert D(view(stock,order['lines'][0]['article_id'])['quantity'])==0
    confirm(case,r,allow_overdelivery=True)
    r2=from_source(case,order,'receipt',1)
    confirm(case,r2,409)
    r3=from_source(case,order,'receipt',4,rejected='2')
    confirm(case,r3,allow_overdelivery=True,acknowledge_differences=True)
    assert D(view(stock,order['lines'][0]['article_id'])['quantity'])==24

def test_lots_failures_atomic_and_duplicate_document(case):
    stock,_=case;aid=product(stock,lots=True,expiry=True)
    line={'article_id':aid,'description':'Lote obrigatório','quantity':'4','price':'3','vat':'23','warehouse_id':stock[3][0]}
    bad=draft(case,'receipt',lines=[line]);confirm(case,bad,422)
    assert D(view(stock,aid)['quantity'])==0
    line.update(lot_number='LOTE-A',expiry=(date.today()+timedelta(days=50)).isoformat())
    receipt=confirm(case,draft(case,'receipt',lines=[line]));line['lot_number']='LOTE-B'
    other=confirm(case,draft(case,'receipt',lines=[line]))
    assert D(view(stock,aid)['quantity'])==8
    duplicate=draft(case,'receipt',lines=[line],document=other['document']);confirm(case,duplicate,409)
    ret=from_source(case,receipt,'return',5,reason='Devolução excessiva');confirm(case,ret,409)
    assert D(view(stock,aid)['quantity'])==8
    with stock[2]() as db: assert len(list(db.scalars(select(StockEntry).where(StockEntry.article_id==aid))))==2

def test_failure_rolls_back_all_lines_and_repeated_requests(case,monkeypatch):
    stock,_=case;aid=product(stock)
    line={'article_id':aid,'description':'Artigo','quantity':'2','price':'3','warehouse_id':stock[3][0]}
    receipt=draft(case,'receipt',lines=[line,dict(line)])
    from app import inventory
    original=inventory.post;calls=[]
    def failing(*args,**kwargs):
        calls.append(1)
        if len(calls)==2: raise __import__('fastapi').HTTPException(409,'Falha simulada')
        return original(*args,**kwargs)
    monkeypatch.setattr(inventory,'post',failing)
    confirm(case,receipt,409)
    assert D(view(stock,aid)['quantity'])==0
    monkeypatch.setattr(inventory,'post',original)
    confirm(case,receipt);confirm(case,receipt)
    assert D(view(stock,aid)['quantity'])==4

def test_valuation_changes_cost_not_past_margins_or_quantity(case):
    stock,_=case
    receipt=confirm(case,draft(case,'receipt'));aid=receipt['lines'][0]['article_id']
    sale=move(stock,aid,'sale','5',net_sales='100')
    inv=from_source(case,receipt,'invoice',20,price='12')
    confirm(case,inv,409)
    invoice=confirm(case,inv,acknowledge_differences=True)
    assert D(view(stock,aid)['quantity'])==15
    assert D(view(stock,aid)['average_cost'])==12
    assert D(invoice['lines'][0]['valuation']['inventory_value_delta'])==30
    with stock[2]() as db:
        assert db.get(StockEntry,sale[0]).unit_cost==10
        assert db.scalar(select(StockEntry).where(StockEntry.kind=='valuation')).quantity==0

def test_existing_purchase_link_and_duplicate_invoice(case):
    stock,sid=case
    r=confirm(case,draft(case,'receipt'))
    inv=from_source(case,r,'invoice',20)
    with stock[2].begin() as db:
        supplier=db.get(BusinessEntity,sid)
        p=Purchase(store_id=stock[4],supplier_nif=supplier.tax_number,supplier_name=supplier.name,document=inv['document'],document_type='FT',
            document_date=date.today().isoformat(),amount_cents=24600,origin='ficheiro')
        db.add(p);db.flush();pid=p.id
    confirm(case,inv,409)
    raw={k:v for k,v in inv.items() if k in __import__('app.procurement',fromlist=['Draft']).Draft.model_fields}
    raw['lines']=[{k:v for k,v in l.items() if k in __import__('app.procurement',fromlist=['Line']).Line.model_fields} for l in raw['lines']]
    raw['purchase_id']=pid
    response=stock[0].put('/api/procurement/'+inv['id'],json=raw)
    assert response.status_code==200,response.text
    result=confirm(case,response.json());assert result['purchase_id']==pid
    assert D(view(stock,r['lines'][0]['article_id'])['quantity'])==20

def test_permissions_and_source_isolation(case):
    stock,_=case;c=stock[0]
    order=confirm(case,draft(case))
    login(c,'reader')
    assert c.post('/api/procurement/'+order['id']+'/confirm',json={'revision':1}).status_code==403
    assert c.get('/api/procurement').json()['can_write'] is False
    login(c,'admin')
    bad=from_source(case,order,'receipt',1)
    with stock[2].begin() as db:
        source=db.get(ProcurementDocument,order['id']);source.store_id=db.get(__import__('app.models',fromlist=['StockWarehouse']).StockWarehouse,stock[3][2]).store_id
    confirm(case,bad,422)

def test_pdf_and_confirmed_documents_immutable(case):
    stock,_=case;order=confirm(case,draft(case))
    pdf=stock[0].get('/api/procurement/'+order['id']+'/pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    assert stock[0].put('/api/procurement/'+order['id'],json={}).status_code==422
    receipt=confirm(case,from_source(case,order,'receipt',12))
    assert stock[0].post('/api/procurement/'+receipt['id']+'/cancel',json={'revision':1,'reason':'Cancelar receção'}).status_code==409
    assert stock[0].post('/api/procurement/'+order['id']+'/cancel',json={'revision':1,'reason':'Cancelar encomenda'}).status_code==409

def test_email_requires_preview_confirm_and_reports_actual_smtp(case,monkeypatch):
    from types import SimpleNamespace
    from app.email_settings import EmailConfigInput
    from app import email_transport
    stock,_=case;c=stock[0];order=confirm(case,draft(case))
    config=EmailConfigInput(enabled=True,host='smtp.example.com',sender_email='compras@example.com').model_dump()
    configuration=lambda _: (SimpleNamespace(revision=1,password_ciphertext=''),config)
    monkeypatch.setattr('app.email_settings.configuration',configuration)
    monkeypatch.setattr('app.email_delivery.configuration',configuration)
    calls=[]
    monkeypatch.setattr(email_transport,'connect',lambda *_:(object(),None))
    monkeypatch.setattr(email_transport,'disconnect',lambda *_:None)
    def submit(client,sender,recipients,raw,before_data):
        before_data();calls.append(raw)
    monkeypatch.setattr(email_transport,'submit',submit)
    base='/api/procurement/'+order['id']
    preview=c.post(base+'/email-preview',json={'revision':1,'to':'fornecedor@example.com','subject':'Encomenda teste','message':'Segue a encomenda.'})
    assert preview.status_code==200,preview.text
    dispatch=preview.json();assert dispatch['status']=='prepared' and not calls
    send=c.post(base+'/email-send/'+dispatch['id'],json={'revision':dispatch['revision'],'request_key':str(uuid4()),'confirmed':True})
    assert send.status_code==200,send.text
    assert send.json()['status']=='accepted' and len(calls)==1
    repeat=c.post(base+'/email-send/'+dispatch['id'],json={'revision':dispatch['revision'],'request_key':str(uuid4()),'confirmed':True})
    assert repeat.status_code==200 and len(calls)==1

def test_concurrent_confirm_only_posts_stock_once(case):
    from concurrent.futures import ThreadPoolExecutor
    stock,_=case;receipt=draft(case,'receipt');c=stock[0]
    def post():return c.post('/api/procurement/'+receipt['id']+'/confirm',json={'revision':0})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:post(),range(2)))
    assert [r.status_code for r in responses]==[200,200]
    assert D(view(stock,receipt['lines'][0]['article_id'])['quantity'])==20

def test_existing_plan_payment_does_not_create_second_cash_exit(case):
    from conftest import open_cash
    from test_planned import payment
    from app.models import Movement, PaymentDocumentLine
    stock,_=case;c=stock[0]
    invoice=confirm(case,draft(case,'invoice',lines=[{'description':'Serviço','nature':'service','quantity':'1','price':'100','vat':'23'}]))
    plan=next(p for p in c.get('/api/planned-movements').json() if p['purchase_id']==invoice['purchase_id'])
    session=open_cash(c,stock[1]['registers'][0]).json()['id']
    payload=payment(session,'25')
    for _ in range(2):
        response=c.post('/api/planned-movements/'+plan['id']+'/settle',json=payload)
        assert response.status_code==200,response.text
        assert response.json()['remaining_cents']==9800
    inv=next(d for d in c.get('/api/procurement').json()['documents'] if d['id']==invoice['id'])
    assert inv['paid_cents']==2500 and inv['remaining_cents']==9800
    with stock[2]() as db:
        assert len(list(db.scalars(select(Movement))))==1
        assert len(list(db.scalars(select(PaymentDocumentLine))))==1
    change={'store_id':stock[4],'category_id':stock[1]['category'],'entity':plan['entity'],'document':plan['document'],
        'direction':-1,'due_date':date.today().isoformat(),'amount':'99'}
    assert c.post('/api/planned-movements/'+plan['id']+'/edit',json=change).status_code==409

def test_procurement_migration_recreates_tables_and_purchase_link(case,monkeypatch):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect,text
    stock,_=case
    migration=importlib.import_module('migrations.versions.0046_procurement')
    # Current metadata includes customer-order links to purchasing documents.
    # Roll back that dependent migration before exercising the older one.
    customer_migration=importlib.import_module('migrations.versions.0057_customer_orders')
    cycle_migration=importlib.import_module('migrations.versions.0058_order_cycle')
    helper=importlib.import_module('migrations.versions.0006_cash_sheet')
    with stock[2].kw['bind'].begin() as conn:
        schema=conn.scalar(text('SELECT current_schema()'))
        monkeypatch.setattr(helper,'schemas',lambda _:[schema])
        before=conn.scalar(text('SELECT count(*) FROM users'))
        with Operations.context(MigrationContext.configure(conn)):
            cycle_migration.downgrade()
            customer_migration.downgrade()
            migration.downgrade();migration.upgrade();migration.upgrade()
            customer_migration.upgrade()
            cycle_migration.upgrade()
        assert {'procurement_documents','procurement_links'} <= set(inspect(conn).get_table_names(schema=schema))
        assert 'purchase_id' in {c['name'] for c in inspect(conn).get_columns('planned_movements',schema=schema)}
        assert conn.scalar(text('SELECT count(*) FROM users'))==before

def test_multiple_orders_one_receipt_and_one_invoice(case):
    stock,_=case
    o1=confirm(case,draft(case));aid=o1['lines'][0]['article_id']
    raw={k:v for k,v in o1['lines'][0].items() if k in __import__('app.procurement',fromlist=['Line']).Line.model_fields}
    raw['quantity']='8'
    o2=confirm(case,draft(case,lines=[raw]))
    l1={**raw,'source_id':o1['id'],'source_line':0,'quantity':'12'}
    l2={**raw,'source_id':o2['id'],'source_line':0,'quantity':'8'}
    receipt=confirm(case,draft(case,'receipt',lines=[l1,l2]))
    invoice_lines=[{**raw,'source_id':receipt['id'],'source_line':i,'quantity':q} for i,q in enumerate(['12','8'])]
    confirm(case,draft(case,'invoice',lines=invoice_lines))
    assert D(view(stock,aid)['quantity'])==20
    docs=stock[0].get('/api/procurement').json()['documents']
    assert next(d for d in docs if d['id']==o1['id'])['status']=='partial'
    assert next(d for d in docs if d['id']==o2['id'])['status']=='received'

def test_explicit_erp_identity_and_no_unsupported_stock_write(case,monkeypatch):
    from app.models import ERPDocument,DocumentERPLink
    stock,_=case;receipt=confirm(case,draft(case,'receipt'))
    invoice=from_source(case,receipt,'invoice',20)
    with stock[2].begin() as db:
        erp=ERPDocument(store_id=stock[4],enterprise_id='demo',external_id='erp-purchase',supplier_nif='500000000',supplier_name='Fornecedor Compras',
            document=invoice['document'],internal_document='FC 1',document_type='FT',document_date=date.today().isoformat(),amount_cents=24600)
        db.add(erp);db.flush();erp_id=erp.id
    raw={k:v for k,v in invoice.items() if k in __import__('app.procurement',fromlist=['Draft']).Draft.model_fields}
    raw['lines']=[{k:v for k,v in l.items() if k in __import__('app.procurement',fromlist=['Line']).Line.model_fields} for l in raw['lines']]
    raw['erp_id']=erp_id
    updated=stock[0].put('/api/procurement/'+invoice['id'],json=raw)
    assert updated.status_code==200,updated.text
    result=confirm(case,updated.json());assert result['erp_status']=='linked'
    with stock[2]() as db:assert db.scalar(select(DocumentERPLink).where(DocumentERPLink.purchase_id==result['purchase_id'])).erp_id==erp_id
    blocked=draft(case,'receipt')
    monkeypatch.setattr('app.procurement.policy',lambda _:{'mode':'integrated','provider':'wintouch','authority':{'stock':'erp'}})
    confirm(case,blocked,409)

def test_nonrecoverable_vat_uses_document_rate_and_quantity_stays_fixed(case):
    from test_inventory import configure
    stock,_=case;configure(stock,recoverable_vat=False)
    aid=product(stock,vat_rate='23')
    receipt=confirm(case,draft(case,'receipt',lines=[{'article_id':aid,'description':'Mercadoria','quantity':'10','price':'10','vat':'6','warehouse_id':stock[3][0]}]))
    assert D(view(stock,aid)['average_cost'])==D('10.6')
    invoice=from_source(case,receipt,'invoice',10,price='12',vat='13')
    confirm(case,invoice,acknowledge_differences=True)
    assert D(view(stock,aid)['average_cost'])==D('13.56')
    assert D(view(stock,aid)['quantity'])==10


def test_line_rubrics_split_the_planned_payment_without_splitting_the_payment(case):
    from app.models import Category, ExpenseDraft, PlannedAllocation
    stock, sid = case
    with stock[2].begin() as db:
        services = Category(name='Serviços técnicos ' + uuid4().hex[:6]); db.add(services); db.flush(); services_id = services.id
    lines = [{'description': 'Material', 'nature': 'expense', 'unit': 'un', 'quantity': '1', 'price': '100', 'vat': '23'},
             {'description': 'Montagem', 'nature': 'service', 'unit': 'h', 'quantity': '2', 'price': '20', 'vat': '23', 'category_id': services_id}]
    invoice = confirm(case, draft(case, 'invoice', lines=lines))
    assert invoice['allocations'] == [{'category_id': stock[1]['category'], 'amount_cents': 12300}, {'category_id': services_id, 'amount_cents': 4920}]
    with stock[2]() as db:
        planned = db.scalar(select(PlannedMovement).where(PlannedMovement.purchase_id == invoice['purchase_id']))
        assert planned.amount_cents == 17220 and planned.category_id == stock[1]['category']
        assert sorted(a.amount_cents for a in db.scalars(select(PlannedAllocation).where(PlannedAllocation.planned_id == planned.id))) == [4920, 12300]
        expense = json.loads(db.get(ExpenseDraft, invoice['purchase_id']).payload)
        assert [line['category'] for line in expense['lines']][1].startswith('Serviços técnicos')
    summary = next(row for row in stock[0].get('/api/planned-movements').json() if row['id'] == planned.id)
    assert [item['amount_cents'] for item in summary['allocations']] == [12300, 4920]
    edit = stock[0].post(f'/api/planned-movements/{planned.id}/edit', json={'store_id': planned.store_id, 'category_id': services_id, 'entity': planned.entity,
        'document': planned.document, 'due_date': planned.due_date, 'amount': '172,20', 'direction': -1})
    assert edit.status_code == 409 and 'várias rubricas' in edit.json()['detail']


def test_lines_without_any_rubric_are_refused_in_local_mode(case):
    stock, sid = case
    with stock[2].begin() as db:
        db.get(BusinessEntity, sid).category_id = None
    row = draft(case, 'invoice', category_id=None, lines=[{'description': 'Serviço', 'nature': 'service', 'unit': 'h', 'quantity': '1', 'price': '10', 'vat': '23'}])
    response = stock[0].post('/api/procurement/' + row['id'] + '/confirm', json={'revision': row['revision']})
    assert response.status_code == 422 and 'rubrica de cada linha' in response.json()['detail']


def test_confirmed_receipt_is_edited_in_place_and_regenerates_stock(case):
    stock, _ = case
    c = stock[0]
    order = confirm(case, draft(case))
    aid = order['lines'][0]['article_id']
    receipt = confirm(case, from_source(case, order, 'receipt', 12))
    fields = __import__('app.procurement', fromlist=['Draft']).Draft.model_fields
    line_fields = __import__('app.procurement', fromlist=['Line']).Line.model_fields

    def edit(doc, quantity, price='10'):
        body = {k: v for k, v in doc.items() if k in fields}
        body['lines'] = [{**{k: v for k, v in doc['lines'][0].items() if k in line_fields}, 'quantity': quantity, 'price': price, 'rejected': '0'}]
        return c.put(f"/api/procurement/{doc['id']}/confirmed", json=body)

    # Sem movimentos posteriores: a entrada antiga é apagada e a nova gerada (mesma receção).
    first = edit(receipt, '10')
    assert first.status_code == 200, first.text
    edited = first.json()
    assert edited['id'] == receipt['id'] and edited['status'] == 'confirmed' and D(view(stock, aid)['quantity']) == 10
    with stock[2]() as db:
        assert len(list(db.scalars(select(StockEntry).where(StockEntry.article_id == aid)))) == 1
    docs = c.get('/api/procurement').json()['documents']
    assert next(d for d in docs if d['id'] == order['id'])['status'] == 'partial'
    # Com uma saída posterior, a entrada não se apaga: movimento inverso ao custo original.
    sale = c.post('/api/stock/movements', json={'article_id': aid, 'warehouse_id': stock[3][0], 'kind': 'sale', 'quantity': '2',
                                                'reason': 'Venda de teste', 'request_key': str(uuid4())})
    assert sale.status_code == 200, sale.text
    second = edit(edited, '9', price='11')
    assert second.status_code == 200, second.text
    assert D(view(stock, aid)['quantity']) == 7
    with stock[2]() as db:
        kinds = sorted(e.kind for e in db.scalars(select(StockEntry).where(StockEntry.article_id == aid)))
        assert kinds == ['adjustment', 'purchase', 'purchase', 'sale']
    # Revisão desatualizada e receção já faturada não se editam.
    assert edit(edited, '8').status_code == 409
    current = second.json()
    confirm(case, from_source(case, current, 'invoice', 9))
    refreshed = next(d for d in c.get('/api/procurement').json()['documents'] if d['id'] == current['id'])
    blocked = edit(refreshed, '8')
    assert blocked.status_code == 409 and 'devolução' in blocked.json()['detail']
