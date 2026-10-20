"""Account-specific book balances, dated statement balances and explicit missing entries."""
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .models import (AccountBalanceReference, BankAccount, BankBookEntry, BankDeposit,
    BankTransaction, DepositCredit, DocumentPayment, LiquiditySettlement, Movement,
    PaymentDocumentLine, Purchase, PurchaseAllocation, Reconciliation, ReconciliationLine, User,
    CashSession, PaymentTerminal, Register, WintouchSaleRecord)
from .schemas import Strict
from .cash_sheet import day as local_day, payment_key
from .reconciliation import used, valid_payment_origin, normalized
from .operating_modes import policy

router=APIRouter()

def fail(message,status=422): raise HTTPException(status,message)

def account_for(db,user,identifier):
    account=db.get(BankAccount,identifier)
    if not account:fail('Conta não encontrada nesta empresa.',404)
    authorize(user,db,account.store_id)
    return account

def allocations(db):
    values={(source,key):int(amount) for source,key,amount in db.execute(select(ReconciliationLine.source,ReconciliationLine.source_id,func.sum(ReconciliationLine.amount_cents))
        .join(Reconciliation).where(Reconciliation.undone_at.is_(None)).group_by(ReconciliationLine.source,ReconciliationLine.source_id))}
    for transaction,amount in db.execute(select(PurchaseAllocation.transaction_id,func.sum(PurchaseAllocation.amount_cents)).group_by(PurchaseAllocation.transaction_id)):
        values['bank',transaction]=values.get(('bank',transaction),0)+int(amount)
    for transaction,amount in db.execute(select(DepositCredit.transaction_id,func.sum(DepositCredit.amount)).where(DepositCredit.undone_at.is_(None)).group_by(DepositCredit.transaction_id)):
        values['bank',transaction]=values.get(('bank',transaction),0)+int(amount*100)
    for p in db.scalars(select(DocumentPayment).where(DocumentPayment.source.in_(['movement','wintouch','book']))):
        combined=values.get((p.source,p.source_id),0)+values.get(('payment',p.id),0)
        values[p.source,p.source_id]=combined;values['payment',p.id]=combined
    return values

def settlement_dates(db):
    """Data do crédito bancário a que cada movimento foi associado (liquidação TPA)."""
    from .models import BankTransaction as Bank
    groups=defaultdict(list)
    for group,source,key in db.execute(select(ReconciliationLine.reconciliation_id,ReconciliationLine.source,ReconciliationLine.source_id)
        .join(Reconciliation).where(Reconciliation.undone_at.is_(None))):
        groups[group].append((source,key))
    bank_dates={row.id:row.movement_date for row in db.scalars(select(Bank).where(Bank.id.in_([k for lines in groups.values() for s,k in lines if s=='bank'])))} if groups else {}
    result={}
    for lines in groups.values():
        stamps=[bank_dates[k] for s,k in lines if s=='bank' and k in bank_dates]
        if stamps:
            for s,k in lines:
                if s=='movement':result[k]=max(stamps+[result.get(k,'')])
    return result

def book_rows(db, account, allocated=None):
    """Read existing economic records once; association alone never creates a book row.

    Recebimentos TPA (e MB WAY com liquidação posterior) aguardam a liquidação do prestador:
    só a parte associada a um crédito bancário entra no saldo, na data dessa liquidação.
    """
    from .billing_conference import deferred_settlement
    rows=[]
    allocated=allocations(db) if allocated is None else allocated
    movement_ids=set()
    terminals=list(db.scalars(select(PaymentTerminal).where(PaymentTerminal.active.is_(True))))
    settled=None
    for move in db.scalars(select(Movement).where(Movement.bank_account_id==account.id)):
        if move.origin=='deposito_modulo':continue
        if move.payment=='numerario' and move.kind not in ('deposito','reforco'):continue
        sign=-move.direction if move.kind in ('deposito','reforco') else move.direction
        rows.append({'source':'movement','id':move.id,'date':local_day(move.occurred_at),'amount_cents':move.amount_cents*sign,
            'description':move.notes or move.entity or move.kind,'reference':move.reference or move.document,'kind':move.kind,
            'transfer':bool(move.transfer_id) or move.kind in ('deposito','reforco'), 'origin':'Aplicação', 'created_at':move.created_at})
        if (move.direction>0 and move.kind in ('venda','cliente') and not move.transfer_id and not move.reversal_of
                and deferred_settlement(db,move,terminals)):
            settled=settlement_dates(db) if settled is None else settled
            confirmed=min(move.amount_cents,allocated.get(('movement',move.id),0))
            rows[-1].update(settlement='tpa',sale_date=rows[-1]['date'],book_amount_cents=confirmed,
                pending_settlement_cents=move.amount_cents-confirmed,origin='Aplicação · TPA por liquidar' if confirmed<move.amount_cents else 'Aplicação · TPA liquidado')
            if confirmed and move.id in settled:rows[-1]['date']=settled[move.id]
        if move.reversed_by:
            inverse=db.get(Movement,move.reversed_by)
            rows[-1]['closed_on']=local_day(inverse.occurred_at) if inverse else None
        elif move.reversal_of:rows[-1]['closed_on']=local_day(move.occurred_at)
        movement_ids.add(move.id)
    for link,payment in db.execute(select(LiquiditySettlement,DocumentPayment).join(DocumentPayment,DocumentPayment.id==LiquiditySettlement.payment_id).where(LiquiditySettlement.target=='bank:'+account.id)):
        if link.movement_id in movement_ids:continue
        if payment.source in ('movement','wintouch','book') and not valid_payment_origin(db,payment):continue
        rows.append({'source':'payment','id':payment.id,'date':payment.payment_date,'amount_cents':payment.amount_cents*payment.direction,
            'description':payment.entity or payment.kind,'reference':payment.reference,'kind':payment.kind,'transfer':False,'origin':'Liquidação local','created_at':payment.created_at})
    for entry in db.scalars(select(BankBookEntry).where(BankBookEntry.account_id==account.id)):
        rows.append({'source':'book','id':entry.id,'date':entry.movement_date,'amount_cents':entry.amount_cents,
            'description':entry.description,'reference':entry.reference,'kind':entry.kind,'transfer':bool(entry.transfer_id),
            'origin':'Registo bancário aprovado','created_at':entry.created_at,'original_id':entry.original_id})
        inverse=db.scalar(select(BankBookEntry).where(BankBookEntry.original_id==entry.id))
        if entry.original_id or inverse:rows[-1]['closed_on']=inverse.movement_date if inverse else entry.movement_date
    for deposit in db.scalars(select(BankDeposit).where(BankDeposit.account_id==account.id,BankDeposit.delivery_date.is_not(None),BankDeposit.status.in_(['entregue','parcialmente_reconciliado','reconciliado','cancelado']))):
        inverse=db.get(Movement,deposit.reversal_id) if deposit.reversal_id else None
        if deposit.status=='cancelado' and not inverse:continue
        associated=sum((int(credit.amount*100) for credit in db.scalars(select(DepositCredit).where(DepositCredit.deposit_id==deposit.id,DepositCredit.undone_at.is_(None)))),0)
        rows.append({'source':'deposit','id':deposit.id,'date':deposit.delivery_date,'amount_cents':int((deposit.cash_amount+deposit.cheque_amount)*100),
            'description':'Depósito entregue ao banco','reference':deposit.number,'kind':'deposito','transfer':True,'origin':'Módulo de depósitos',
            'associated_cents':associated,'created_at':deposit.created_at})
        if inverse:
            stamp=local_day(inverse.occurred_at);rows[-1]['closed_on']=stamp
            rows.append({'source':'deposit','id':'reversal:'+deposit.id,'date':stamp,'amount_cents':-int((deposit.cash_amount+deposit.cheque_amount)*100),
                'description':'Estorno de depósito entregue','reference':deposit.number,'kind':'reversal','transfer':True,'origin':'Módulo de depósitos',
                'associated_cents':0,'created_at':inverse.created_at,'closed_on':stamp})
    for row in rows:
        row['account_id']=account.id
        row['currency']=account.currency
        row['associated_cents']=min(abs(row['amount_cents']),row.get('associated_cents',allocated.get((row['source'],row['id']),0)))
        row['remaining_cents']=max(0,abs(row['amount_cents'])-row['associated_cents'])
    return sorted(rows,key=lambda r:(r['date'],str(r['created_at']),r['id']),reverse=True)

def summary(db,account,start,end,allocated=None):
    allocated=allocations(db) if allocated is None else allocated
    rows=book_rows(db,account,allocated)
    opening=db.get(AccountBalanceReference,'bank:'+account.id)
    known=bool(opening and opening.reference_date<=end and opening.currency==account.currency)
    accumulated=[r for r in rows if known and opening.reference_date<=r['date']<=end]
    # TPA por liquidar não está no banco: só conta a parte já associada à liquidação.
    def booked(r):return r.get('book_amount_cents',r['amount_cents'])
    book=opening.amount_cents+sum(booked(r) for r in accumulated) if known else None
    transactions=list(db.scalars(select(BankTransaction).where(BankTransaction.account_id==account.id,BankTransaction.movement_date<=end)
        .order_by(BankTransaction.movement_date.desc(),BankTransaction.exported_at.desc(),BankTransaction.source_position.asc(),BankTransaction.created_at.desc())))
    last=transactions[0] if transactions else None
    bank_rows=[]
    for row in transactions:
        if row.movement_date<start:continue
        associated=min(abs(row.amount_cents),allocated.get(('bank',row.id),0))
        bank_rows.append({'id':row.id,'date':row.movement_date,'description':row.description,'amount_cents':row.amount_cents,
            'associated_cents':associated,'remaining_cents':abs(row.amount_cents)-associated})
    period=[r for r in rows if start<=r['date']<=end]
    for row in period:
        if row.get('closed_on') and row['closed_on']<=end:
            row['remaining_cents']=0;row['status']='estornado'
    pending_app=[r for r in period if r['remaining_cents']]
    pending_bank=[r for r in bank_rows if r['remaining_cents']]
    def pending(values):return {'count':len(values),'incoming_cents':sum(r['remaining_cents'] for r in values if r['amount_cents']>0),
        'outgoing_cents':sum(r['remaining_cents'] for r in values if r['amount_cents']<0),'absolute_cents':sum(r['remaining_cents'] for r in values)}
    incoming=sum(r['associated_cents'] for r in bank_rows if r['amount_cents']>0)
    outgoing=sum(r['associated_cents'] for r in bank_rows if r['amount_cents']<0)
    comparable=book is not None and last is not None and last.movement_date==end
    return {'account_id':account.id,'name':account.name,'bank':account.bank,'store_id':account.store_id,'currency':account.currency,'as_of':end,
        'period_start':start,'period_end':end,'book_balance_cents':book,'book_origin':'Saldo pelos registos da aplicação',
        'book_date':end if known else None,'opening_cents':opening.amount_cents if known else None,'opening_date':opening.reference_date if opening else None,
        'opening_note':'Saldo inicial no início da data de referência; movimentos desse dia são incluídos uma única vez.',
        'book_incoming_cents':sum(booked(r) for r in accumulated if r['amount_cents']>0) if known else None,
        'book_outgoing_cents':-sum(r['amount_cents'] for r in accumulated if r['amount_cents']<0) if known else None,
        'bank_balance_cents':last.balance_cents if last else None,'bank_date':last.movement_date if last else None,
        'bank_imported_at':last.created_at if last else None,'bank_origin':'Último saldo do extrato importado' if last else 'Sem informação',
        'bank_age_days':(date.fromisoformat(end)-date.fromisoformat(last.movement_date)).days if last else None,
        'comparable':comparable,'difference_cents':book-last.balance_cents if comparable else None,
        'comparison_reason':'Mesma conta, moeda e data' if comparable else 'Comparação indisponível: falta saldo inicial válido, extrato ou coincidência de datas.',
        'pending_application':pending(pending_app),'pending_bank':pending(pending_bank),
        'card_pending_settlement':{'count':sum(bool(r.get('pending_settlement_cents')) for r in rows if r.get('sale_date',r['date'])<=end),
            'amount_cents':sum(r.get('pending_settlement_cents',0) for r in rows if r.get('sale_date',r['date'])<=end),
            'basis':'Recebimentos TPA/MB WAY registados que aguardam liquidação do prestador; não estão incluídos no saldo pelos registos até serem associados ao crédito bancário.'},
        'reconciled':{'incoming_cents':incoming,'outgoing_cents':outgoing,'net_cents':incoming-outgoing,
            'count':sum(bool(r['associated_cents']) for r in bank_rows),'basis':'Valores confirmados no extrato, pela data do movimento bancário, incluindo associações parciais. Não é saldo disponível.'},
        'application_rows':period,'bank_rows':bank_rows}

@router.get('/api/bank-balances')
def balances(start:date|None=None,end:date|None=None,account_id:str='',store_id:str='',user=Depends(current),db:Session=Depends(db_session)):
    end=end or date.today();start=start or end.replace(day=1)
    if start>end or end>date.today():fail('Selecione um período válido, sem data final futura.')
    stores=scope(user,db)
    if store_id:authorize(user,db,store_id);stores=[store_id]
    if account_id:account_for(db,user,account_id)
    accounts=list(db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores)).order_by(BankAccount.name)))
    if account_id:accounts=[a for a in accounts if a.id==account_id]
    allocated=allocations(db)
    return {'accounts':[summary(db,a,str(start),str(end),allocated) for a in accounts], 'mode':policy(db),
        'warnings':['Saldos baseados nos registos locais com conta identificada. Movimentos sem conta associada e saldos contabilísticos oficiais do ERP não são inferidos a partir do extrato.',
            'A diferença entre saldos pode incluir diferenças temporais, saldos iniciais ou movimentos em falta; não equivale necessariamente ao líquido por reconciliar.']}

class Resolution(Strict):
    kind:Literal['receipt','payment','fee','interest','transfer']
    description:str=Field(min_length=3,max_length=1000)
    reference:str=Field(default='',max_length=200)
    target_account_id:str|None=None
    purchase_id:str|None=None
    @field_validator('description')
    @classmethod
    def meaningful_description(cls,value):
        if len(value.strip())<3:raise ValueError('Indique uma descrição/motivo com pelo menos três caracteres.')
        return value.strip()

class Resolve(Resolution):
    preview_token:str=Field(min_length=64,max_length=64)
    request_key:str=Field(min_length=8,max_length=100)
    confirmed:Literal[True]
    acknowledge_existing:bool=False

def proposal(db,user,transaction_id,data):
    transaction=db.get(BankTransaction,transaction_id)
    if not transaction:fail('Movimento bancário não encontrado.',404)
    account=account_for(db,user,transaction.account_id)
    authorize(user,db,account.store_id,['administrador','gestor'])
    if transaction.movement_date>date.today().isoformat():fail('Não pode registar movimentos realizados no futuro.')
    remaining=abs(transaction.amount_cents)-used(db,'bank',transaction.id)
    if remaining<=0:fail('Este movimento já está totalmente reconciliado.',409)
    sign=1 if transaction.amount_cents>0 else -1
    if data.kind=='receipt' and sign<0 or data.kind in ('payment','fee') and sign>0:fail('O tipo de registo não corresponde ao sentido do movimento bancário.')
    if data.kind=='transfer':
        target=account_for(db,user,data.target_account_id)
        authorize(user,db,target.store_id,['administrador','gestor'])
        if target.id==account.id or target.currency!=account.currency:fail('Escolha outra conta própria na mesma moeda, sem conversão cambial automática.')
    elif data.target_account_id:fail('O destino de transferência só se aplica a transferências próprias.')
    candidates=[]
    seen=set()
    def candidate(source,identifier,stamp,description,reference,amount,available=None):
        description=str(description or '');reference=str(reference or '')
        if (source,identifier) in seen or (amount>0)!=(sign>0):return
        available=max(0,abs(amount)-used(db,source,identifier)) if available is None else available
        reference_match=len(normalized(reference))>=4 and normalized(reference) in normalized(transaction.description)
        near=abs((date.fromisoformat(stamp)-date.fromisoformat(transaction.movement_date)).days)<=7
        if available and (reference_match or available==remaining and near):
            seen.add((source,identifier));candidates.append({'source':source,'id':identifier,'date':stamp,'description':description,
                'reference':reference,'amount_cents':amount,'remaining_cents':available})
    for row in book_rows(db,account):
        if not row.get('closed_on'):candidate(row['source'],row['id'],row['date'],row['description'],row['reference'],row['amount_cents'],row['remaining_cents'])
    # A payment can exist without a bank account yet. Warn about it instead of
    # creating a second payment merely because its account has not been assigned.
    first=str(date.fromisoformat(transaction.movement_date)-timedelta(days=31))
    last=str(date.fromisoformat(transaction.movement_date)+timedelta(days=31))
    if account.currency=='EUR':
        for row in db.scalars(select(Movement).join(CashSession).join(Register).where(Register.store_id==account.store_id,
            Movement.bank_account_id.is_(None),Movement.reversed_by.is_(None),Movement.reversal_of.is_(None),Movement.transfer_id.is_(None),
            Movement.payment!='numerario',func.date(Movement.occurred_at)>=date.fromisoformat(first),func.date(Movement.occurred_at)<=date.fromisoformat(last))):
            candidate('movement',row.id,local_day(row.occurred_at),row.notes or row.entity or row.kind,row.reference or row.document,row.amount_cents*row.direction)
        for row in db.scalars(select(DocumentPayment).where(DocumentPayment.store_id==account.store_id,DocumentPayment.source=='manual',
            DocumentPayment.method!='numerario',DocumentPayment.payment_date>=first,DocumentPayment.payment_date<=last)):
            target=db.scalar(select(LiquiditySettlement.target).where(LiquiditySettlement.payment_id==row.id))
            if not target or target=='bank:'+account.id:
                candidate('payment',row.id,row.payment_date,row.entity or row.kind,row.reference,row.amount_cents*row.direction)
    for row in db.scalars(select(WintouchSaleRecord).where(WintouchSaleRecord.store_id==account.store_id,WintouchSaleRecord.kind=='real',
        WintouchSaleRecord.active.is_(True),WintouchSaleRecord.document_date>=first,WintouchSaleRecord.document_date<=last)):
        values=json.loads(row.data);method,_=payment_key(values)
        if method=='numerario' or (values.get('currency') or 'EUR')!=account.currency:continue
        candidate('wintouch',row.id,local_day(values.get('occurred_at') or row.document_date),values.get('notes') or values.get('entity') or 'Movimento ERP',
            values.get('reference') or values.get('document',''),int(values.get('amount_cents',0))*int(values.get('direction',1)))
    if sign>0 and data.kind=='receipt':
        # Uma liquidação TPA regulariza vendas já registadas: nunca é uma segunda receita.
        from .billing_conference import deferred_settlement
        terminals=list(db.scalars(select(PaymentTerminal).where(PaymentTerminal.active.is_(True))))
        batches=defaultdict(list)
        stamp=date.fromisoformat(transaction.movement_date)
        for move in db.scalars(select(Movement).join(CashSession).join(Register).where(Register.store_id==account.store_id,
            Movement.payment.in_(('cartao','mbway')),Movement.direction==1,Movement.kind.in_(('venda','cliente')),
            Movement.reversed_by.is_(None),Movement.reversal_of.is_(None),Movement.transfer_id.is_(None))):
            sale=date.fromisoformat(local_day(move.occurred_at))
            available=move.amount_cents-used(db,'movement',move.id)
            if available>0 and 0<=(stamp-sale).days<=10 and deferred_settlement(db,move,terminals):
                batches[str(sale)].append((move,available))
        for sale,group in sorted(batches.items()):
            gross=sum(a for _,a in group)
            if 0<=gross-remaining<=gross//10:
                candidates.append({'source':'tpa_batch','id':sale,'date':sale,'description':f'{len(group)} venda(s) TPA de {sale} por liquidar',
                    'reference':'Liquidação TPA: associe na reconciliação (com comissão, se existir) em vez de criar um recebimento',
                    'amount_cents':gross,'remaining_cents':gross})
    if data.purchase_id:
        if account.currency!='EUR':fail('Os documentos deste módulo estão em EUR. Não existe conversão cambial automática para os associar a esta conta.')
        purchase=db.get(Purchase,data.purchase_id)
        if not purchase or purchase.store_id!=account.store_id:fail('Documento não encontrado neste estabelecimento.')
        if purchase.origin=='efatura_testes':fail('Um documento de testes e-Fatura não pode justificar um movimento bancário real.')
        if data.kind not in ('receipt','payment') or (-1 if purchase.amount_cents>0 else 1)!=sign:fail('O documento não corresponde ao recebimento/pagamento.')
        if remaining>abs(purchase.amount_cents)-used(db,'purchase',purchase.id):fail('O valor excede o saldo do documento.')
    token=hashlib.sha256(json.dumps({'data':data.model_dump(mode='json',include=Resolution.model_fields.keys()),
        'bank_id':transaction.id,'remaining':remaining,'candidates':candidates},sort_keys=True,default=str).encode()).hexdigest()
    entries=[{'account_id':account.id,'account_name':account.bank+' · '+account.name,'amount_cents':remaining*sign}]
    if data.kind=='transfer':entries.append({'account_id':target.id,'account_name':target.bank+' · '+target.name,'amount_cents':-remaining*sign})
    return {'account_id':account.id,'currency':account.currency,'date':transaction.movement_date,'amount_cents':remaining*sign,'entries':entries,
        'kind':data.kind,'description':data.description,'reference':data.reference,'target_account_id':data.target_account_id,
        'candidates':candidates,'preview_token':token,'effect':'Cria registo(s) explícito(s) na aplicação e associa o remanescente do extrato. Transferências não criam receita ou despesa.'}

@router.post('/api/bank-transactions/{identifier}/resolution-preview')
def preview(identifier:str,data:Resolution,user=Depends(current),db:Session=Depends(db_session)):
    return proposal(db,user,identifier,data)

@router.post('/api/bank-transactions/{identifier}/resolve')
def resolve(identifier:str,data:Resolve,user=Depends(current),db:Session=Depends(db_session)):
    bank=db.scalar(select(BankTransaction).where(BankTransaction.id==identifier).with_for_update())
    if not bank:fail('Movimento não encontrado.',404)
    account=account_for(db,user,bank.account_id);authorize(user,db,account.store_id,['administrador','gestor'])
    prior=db.scalar(select(BankBookEntry).where(BankBookEntry.request_key==data.request_key))
    if prior:
        if prior.source_bank_id!=identifier or (prior.kind,prior.description,prior.reference)!=(data.kind,data.description,data.reference):fail('Referência de submissão já utilizada.',409)
        return {'id':prior.id,'message':'Registo já criado; não foi duplicado.'}
    for prior_source in db.scalars(select(BankBookEntry).where(BankBookEntry.source_bank_id==identifier)):
        if not db.scalar(select(BankBookEntry.id).where(BankBookEntry.original_id==prior_source.id)):
            fail('Este extrato já originou um registo na aplicação. Associe o existente.',409)
    if data.purchase_id:
        db.scalar(select(Purchase).where(Purchase.id==data.purchase_id).with_for_update())
    preview=proposal(db,user,identifier,data)
    if preview['candidates'] and not data.acknowledge_existing:
        fail('Foram encontrados registos existentes. Associe-os ou confirme explicitamente a criação de um movimento distinto.',409)
    if preview['preview_token']!=data.preview_token:fail('A pré-visualização mudou. Reveja os registos existentes e confirme novamente.',409)
    transfer_id=str(uuid4()) if data.kind=='transfer' else None
    entry=BankBookEntry(account_id=account.id,user_id=user.id,movement_date=bank.movement_date,amount_cents=preview['amount_cents'],
        kind=data.kind,description=data.description,reference=data.reference,request_key=data.request_key,source_bank_id=bank.id,transfer_id=transfer_id)
    db.add(entry);db.flush()
    if transfer_id:
        db.add(BankBookEntry(account_id=data.target_account_id,user_id=user.id,movement_date=bank.movement_date,amount_cents=-entry.amount_cents,
            kind='transfer',description=data.description,reference=data.reference,request_key=data.request_key+':counterpart',transfer_id=transfer_id))
    group=Reconciliation(store_id=account.store_id,kind='transferencia' if transfer_id else 'pagamento',reason='Registo aprovado após pré-visualização: '+data.description)
    db.add(group);db.flush()
    db.add_all([ReconciliationLine(reconciliation_id=group.id,source=source,source_id=source_id,amount_cents=abs(entry.amount_cents))
        for source,source_id in [('bank',bank.id),('book',entry.id)]])
    if not transfer_id:
        payment=DocumentPayment(store_id=account.store_id,payment_date=entry.movement_date,amount_cents=abs(entry.amount_cents),direction=1 if entry.amount_cents>0 else -1,
            entity=data.description[:300],reference=data.reference,kind='pagamento' if data.purchase_id else 'comissao' if data.kind=='fee' else 'juros' if data.kind=='interest' else 'sem_fatura',method='transferencia',source='book',source_id=entry.id,request_key='book:'+entry.id)
        db.add(payment);db.flush()
        if data.purchase_id:db.add(PaymentDocumentLine(payment_id=payment.id,purchase_id=data.purchase_id,amount_cents=abs(entry.amount_cents)))
    record(db,user,'banco_movimento_registado',entry.id,{'preview':preview,'kind':data.kind,'purchase_id':data.purchase_id},account.store_id)
    record(db,user,'reconciliacao_confirmada',group.id,{'gross_cents':entry.amount_cents,'net_cents':entry.amount_cents,'created_entry_id':entry.id},account.store_id)
    db.commit()
    return {'id':entry.id,'reconciliation_id':group.id,'message':'Movimento registado e associado. O extrato original foi preservado.'}

class Reversal(Strict):
    reason:str=Field(min_length=3,max_length=1000)
    movement_date:date=Field(default_factory=date.today)
    request_key:str=Field(min_length=8,max_length=100)

@router.post('/api/bank-ledger/{identifier}/reverse')
def reverse(identifier:str,data:Reversal,user=Depends(current),db:Session=Depends(db_session)):
    entry=db.scalar(select(BankBookEntry).where(BankBookEntry.id==identifier).with_for_update())
    if not entry:fail('Registo não encontrado.',404)
    account=account_for(db,user,entry.account_id);authorize(user,db,account.store_id,['administrador','gestor'])
    if entry.transfer_id:fail('Reverta a transferência com uma transferência inversa entre as duas contas, nunca uma perna isolada.',409)
    if used(db,'book',entry.id) or db.scalar(select(PaymentDocumentLine.id).join(DocumentPayment).where(DocumentPayment.source=='book',DocumentPayment.source_id==entry.id)):
        fail('Desfaça as associações e a distribuição pelos documentos antes de estornar.',409)
    from .billing_conference import linked
    if linked(db,'book',entry.id):fail('Desfaça a conferência de faturação deste recebimento antes de estornar.',409)
    if entry.original_id or db.scalar(select(BankBookEntry.id).where(BankBookEntry.original_id==entry.id)):fail('Este registo já tem um estorno ou é um estorno.',409)
    if str(data.movement_date)<entry.movement_date or data.movement_date>date.today():fail('Data de estorno inválida.')
    inverse=BankBookEntry(account_id=entry.account_id,user_id=user.id,movement_date=str(data.movement_date),amount_cents=-entry.amount_cents,
        kind='reversal',description=data.reason,reference=entry.reference,request_key=data.request_key,original_id=entry.id)
    db.add(inverse);db.flush()
    payment=db.scalar(select(DocumentPayment).where(DocumentPayment.source=='book',DocumentPayment.source_id==entry.id))
    if payment:
        db.add(DocumentPayment(store_id=account.store_id,payment_date=str(data.movement_date),amount_cents=abs(inverse.amount_cents),
            direction=1 if inverse.amount_cents>0 else -1,kind=payment.kind,method=payment.method,entity=data.reason[:300],reference=entry.id,
            source='book',source_id=inverse.id,request_key='book-reversal:'+inverse.id))
    record(db,user,'banco_movimento_estornado',entry.id,{'inverse_id':inverse.id,'reason':data.reason},account.store_id);db.commit()
    return {'id':inverse.id}
