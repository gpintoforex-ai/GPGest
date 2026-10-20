"""Purchasing workflow. Stock and purchase ledgers remain the system of record."""
import hashlib
import io
import json
from datetime import date
from decimal import Decimal as D, ROUND_HALF_UP
from typing import Literal
from uuid import uuid4

from fastapi import Depends, HTTPException, Response
from pydantic import Field, model_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .db import db_session
from .schemas import Strict
from . import inventory as stock
from .models import (BusinessEntity, Category, DocumentERPLink, DocumentState,
    DriveDocumentAttachment, ERPDocument, ExpenseDraft, ProcurementDocument as Doc,
    ProcurementLink as Link, Purchase, PurchaseAttachment, PlannedAllocation, PlannedMovement, StockArticle, StockEntry,
    StockLot, StockWarehouse, Store, User, now)
from .document_identity import identity
from .operating_modes import policy
from .email_delivery import SendInput

ZERO = D(0)
KINDS = Literal['order', 'receipt', 'invoice', 'return', 'credit']
LABELS = {'order': 'Encomenda', 'receipt': 'Receção', 'invoice': 'Compra', 'return': 'Devolução', 'credit': 'Nota de crédito'}

def fail(message, status=422):
    raise HTTPException(status, message)

def money(value):
    return int((value * 100).quantize(D(1), rounding=ROUND_HALF_UP))

class Line(Strict):
    article_id: str | None = None
    description: str = Field(min_length=1, max_length=500)
    nature: Literal['goods', 'service', 'expense'] = 'goods'
    supplier_reference: str = Field(default='', max_length=100)
    unit: str = Field(default='un', min_length=1, max_length=20)
    quantity: D = Field(gt=0, le=10**9, decimal_places=6)
    rejected: D = Field(default=ZERO, ge=0, le=10**9, decimal_places=6)
    price: D = Field(default=ZERO, ge=0, le=10**9, decimal_places=6)
    discount: D = Field(default=ZERO, ge=0, le=100, decimal_places=4)
    vat: D = Field(default=D(23), ge=0, le=100, decimal_places=3)
    warehouse_id: str | None = None
    lot_id: str | None = None
    lot_number: str = Field(default='', max_length=100)
    expiry: date | None = None
    source_id: str | None = None
    source_line: int = Field(default=0, ge=0)
    # Rubrica da linha; vazia usa a rubrica por omissão do documento e, depois, a do fornecedor.
    category_id: str | None = Field(default=None, max_length=36)
    reason: str = Field(default='', max_length=2000)
    return_reason: Literal['defeito', 'erro_entrega', 'validade', 'outro'] = 'outro'

    @model_validator(mode='after')
    def valid(self):
        if not self.description.strip():
            raise ValueError('Indique a descrição.')
        if self.rejected > self.quantity:
            raise ValueError('A quantidade rejeitada excede a recebida.')
        if self.nature == 'goods' and not self.article_id:
            raise ValueError('Selecione o artigo de mercadoria.')
        return self

class Draft(Strict):
    kind: KINDS
    store_id: str
    supplier_id: str
    document: str = Field(default='', max_length=100)
    document_type: Literal['FT', 'FS', 'FR', 'ND', 'NC'] = 'FT'
    document_date: date = Field(default_factory=date.today)
    due_date: date | None = None
    expected_date: date | None = None
    contact: str = Field(default='', max_length=300)
    payment_terms: str = Field(default='', max_length=1000)
    notes: str = Field(default='', max_length=5000)
    attachments: list[str] = Field(default_factory=list, max_length=20)
    purchase_id: str | None = None
    erp_id: str | None = None
    category_id: str | None = None
    lines: list[Line] = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=8, max_length=100)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def valid(self):
        if self.kind != 'order' and not self.document.strip():
            raise ValueError('Indique o documento do fornecedor.')
        if self.kind in ('invoice', 'credit') and (not self.due_date or self.due_date < self.document_date):
            raise ValueError('Indique um vencimento igual ou posterior à emissão.')
        if self.kind != 'credit' and self.document_type == 'NC':
            raise ValueError('Utilize a opção Nota de crédito.')
        for url in self.attachments:
            if not url.startswith('https://') or any(c in url for c in '\r\n'):
                raise ValueError('Os anexos devem ser ligações HTTPS; os ficheiros permanecem na origem.')
        return self

class Confirm(Strict):
    revision: int = Field(ge=0)
    allow_overdelivery: bool = False
    acknowledge_differences: bool = False

class Cancel(Strict):
    revision: int
    reason: str = Field(min_length=3, max_length=2000)

class EmailPreview(Strict):
    revision: int
    to: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=10000)

def accepted(line):
    return D(line['quantity']) - D(line.get('rejected', 0))

def net_price(line):
    return D(line['price']) * (1 - D(line.get('discount', 0)) / 100)

def totals(lines):
    base = sum((money(D(line['quantity']) * net_price(line)) for line in lines), 0)
    tax = sum((money(D(line['quantity']) * net_price(line) * D(line['vat']) / 100) for line in lines), 0)
    return {'base_cents': base, 'tax_cents': tax, 'total_cents': base + tax}

def line_total(line):
    net = D(line['quantity']) * net_price(line)
    return money(net) + money(net * D(line['vat']) / 100)

def allocations(payload, default_category):
    """Total por rubrica, a partir das linhas (rubrica da linha, do documento ou do fornecedor).

    Devolve [(rubrica, cêntimos)] da maior para a menor; None indica linhas sem rubrica."""
    result = {}
    for line in payload['lines']:
        category = line.get('category_id') or payload.get('category_id') or default_category
        result[category] = result.get(category, 0) + line_total(line)
    return sorted(((category, amount) for category, amount in result.items() if amount), key=lambda item: (-item[1], item[0] or ''))

def effective_quantity(doc, line):
    return accepted(line) if doc.kind == 'receipt' else D(line['quantity'])

def used(db, doc_id, index, kind):
    return sum((row.quantity for row in db.scalars(select(Link).join(Doc, Doc.id == Link.document_id).where(
        Link.source_id == doc_id, Link.source_line == index, Doc.kind == kind, Doc.confirmed_at.is_not(None)))), ZERO)

def order_status(db, row, usage=None):
    if not row.confirmed_at or row.status == 'cancelled':
        return row.status
    lines = json.loads(row.data)['lines']
    received = [usage.get((row.id,i,'receipt' if line['nature']=='goods' else 'invoice'),ZERO) if usage is not None else used(db, row.id, i, 'receipt' if line['nature']=='goods' else 'invoice') for i,line in enumerate(lines)]
    return 'received' if all(q >= D(line['quantity']) for q, line in zip(received, lines)) else 'partial' if any(received) else 'confirmed'

def document_view(db, row, cost_visible=True, usage=None):
    payload = json.loads(row.data)
    for i, line in enumerate(payload['lines']):
        line['accepted'] = str(accepted(line))
        for kind, field in [('receipt','received'), ('invoice','invoiced'), ('return','returned'), ('credit','credited')]:
            line[field] = str(usage.get((row.id,i,kind),ZERO) if usage is not None else used(db, row.id, i, kind))
        if row.kind=='order' and line['nature']!='goods':line['received']=line['invoiced']
        line['pending'] = str(max(ZERO, effective_quantity(row, line) - D(line['received'] if row.kind == 'order' else line['invoiced'] if row.kind == 'receipt' else line['credited'] if row.kind == 'return' else 0)))
    supplier = db.get(BusinessEntity, row.supplier_id)
    paid_cents = paid_total(db, row.purchase_id) if row.purchase_id else 0
    purchase = db.get(Purchase, row.purchase_id) if row.purchase_id else None
    has_attachment = bool(payload.get('attachments')) or bool(purchase and (
        db.scalar(select(PurchaseAttachment.id).where(PurchaseAttachment.purchase_id == purchase.id).limit(1)) or
        db.scalar(select(DriveDocumentAttachment.id).where(DriveDocumentAttachment.purchase_id == purchase.id).limit(1))))
    erp = db.scalar(select(DocumentERPLink.id).where(DocumentERPLink.purchase_id == row.purchase_id, DocumentERPLink.undone_at.is_(None))) if row.purchase_id else None
    frozen_supplier=payload.get('supplier_snapshot',{})
    result = {**payload, 'id': row.id, 'kind': row.kind, 'store_id': row.store_id, 'supplier_id': row.supplier_id,
        'supplier_name': frozen_supplier.get('name',supplier.name if supplier else ''), 'supplier_nif': frozen_supplier.get('nif',supplier.tax_number if supplier else ''),
        'purchase_id': row.purchase_id, 'revision': row.revision, 'status': order_status(db, row, usage) if row.kind == 'order' else row.status,
        'created_at': row.created_at, 'confirmed_at': row.confirmed_at,
        'paid_cents': paid_cents, 'remaining_cents': max(0, abs(purchase.amount_cents) - paid_cents) if purchase else None,
        'payment_status': 'paid' if purchase and paid_cents >= abs(purchase.amount_cents) else 'partial' if paid_cents else 'unpaid',
        'attachment_status': 'present' if has_attachment else 'missing', 'erp_status': 'linked' if erp else 'not_linked',
        'check_status': 'checked' if purchase and purchase.validated else 'pending'}
    from .models import EmailDispatch
    result['email_history'] = [{'id': e.id, 'status': e.status, 'started_at': e.started_at, 'created_at': e.created_at,
        'error': e.error, 'uncertain': e.uncertain} for identifier in payload.get('email_ids', []) if (e := db.get(EmailDispatch, identifier))]
    if not cost_visible:
        for key in ('base_cents','tax_cents','total_cents','paid_cents','remaining_cents'):
            result.pop(key, None)
        for line in payload['lines']:
            for key in ('price', 'discount', 'valuation'):
                line.pop(key, None)
    return result

def paid_total(db, purchase_id):
    from .purchases import paid
    return paid(db, purchase_id)

def supplier(db, identifier):
    row = db.get(BusinessEntity, identifier)
    if not row or not row.is_supplier or row.removed or not row.active:
        fail('Selecione um fornecedor ativo desta empresa.')
    return row

def validate(db, user, payload, authorize):
    authorize(user, db, payload.store_id, ['administrador', 'gestor'])
    stock.require(user, stock.settings(db), 'view_cost')
    supplier(db, payload.supplier_id)
    if payload.category_id and not db.get(Category, payload.category_id):
        fail('Rubrica inválida.')
    if any(line.category_id and not db.get(Category, line.category_id) for line in payload.lines):
        fail('Rubrica inválida numa das linhas.')
    for line in payload.lines:
        if line.nature == 'goods':
            article = stock.article(db, line.article_id)
            meta = json.loads(article.data)
            if line.unit != meta.get('unit', 'un'):
                fail('Utilize a unidade base do artigo. Conversões não configuradas não são aceites.')
            if line.quantity != line.quantity.quantize(D(1).scaleb(-meta.get('precision', 0))):
                fail('A quantidade não respeita a precisão do artigo.')
            if not line.warehouse_id:
                fail('Selecione o armazém das mercadorias.')
            wh = stock.warehouse(db, user, line.warehouse_id, authorize)
            if wh.store_id != payload.store_id:
                fail('O armazém pertence a outro estabelecimento.')
        elif line.source_id and payload.kind in ('receipt', 'return'):
            fail('Receções e devoluções físicas exigem mercadorias.')
    if payload.purchase_id:
        purchase = db.get(Purchase, payload.purchase_id)
        if not purchase or purchase.store_id != payload.store_id:
            fail('Documento importado indisponível neste estabelecimento.')
    if payload.erp_id:
        erp=db.get(ERPDocument,payload.erp_id)
        if not erp or erp.store_id!=payload.store_id or erp.cancelled:
            fail('Documento ERP indisponível ou anulado neste estabelecimento.')

def resolve_source(db, row, line, allowed):
    source = db.get(Doc, line['source_id'])
    if not source or source.kind not in allowed or not source.confirmed_at or source.status == 'cancelled':
        fail('A origem deve ser um documento confirmado do tipo adequado.')
    if source.store_id != row.store_id or source.supplier_id != row.supplier_id:
        fail('Não pode combinar fornecedores ou estabelecimentos distintos.')
    lines = json.loads(source.data)['lines']
    if line['source_line'] >= len(lines):
        fail('Linha de origem inexistente.')
    original = lines[line['source_line']]
    if (line.get('article_id'), line['nature'], line['unit']) != (original.get('article_id'), original['nature'], original['unit']):
        fail('O artigo, natureza e unidade devem corresponder à linha de origem.')
    return source, original

def valuation(db, user, original_entry, quantity, invoice_price, invoice_vat, document, index):
    """Append a cost correction, splitting residual stock and consumed cost; freeze past sales."""
    if original_entry.unit_cost is None:
        fail('O custo provisório não está disponível para ajustar automaticamente.')
    price = invoice_price
    if not stock.settings(db)['recoverable_vat']:
        price *= 1 + D(invoice_vat) / 100
    delta = (price - original_entry.unit_cost) * quantity
    if not delta:
        return None
    pool, exposure, started = ZERO, ZERO, False
    for entry in stock.entries(db, original_entry.article_id):
        if entry.kind in ('transfer_in', 'transfer_out', 'valuation'):
            continue
        if entry.id == original_entry.id:
            exposure, started = quantity, True
        if started and entry.quantity < 0 and pool > 0:
            exposure *= max(ZERO, pool + entry.quantity) / pool
        pool += entry.quantity
    residual = (price - original_entry.unit_cost) * max(ZERO, exposure)
    adjustment = StockEntry(article_id=original_entry.article_id, warehouse_id=original_entry.warehouse_id,
        lot_id=original_entry.lot_id, user_id=user.id, kind='valuation', quantity=ZERO,
        unit_cost=price, net_sales=ZERO, direct_cost=ZERO, occurred_on=date.today().isoformat(),
        request_key=f'valuation:{document.id}:{index}', sequence=0,
        original_id=original_entry.id, data=stock.dump({'origin': 'procurement', 'document': json.loads(document.data)['document'],
            'procurement_id': document.id, 'reason': 'Ajuste entre custo provisório da receção e custo faturado',
            'value_delta': str(delta), 'inventory_value_delta': str(residual), 'consumed_value_delta': str(delta-residual)}))
    db.add(adjustment)
    return {'value_delta': str(delta), 'inventory_value_delta': str(residual), 'consumed_value_delta': str(delta-residual)}

def confirm_document(db, user, row, options, authorize, audit):
    if row.confirmed_at:
        return row
    if row.status != 'draft' or row.revision != options.revision:
        fail('O documento mudou. Atualize antes de confirmar.', 409)
    payload = json.loads(row.data)
    if row.kind != 'order' and payload['document_date'] > date.today().isoformat():
        fail('Não pode confirmar movimentos/documentos futuros.')
    if row.kind in ('receipt','return') and policy(db).get('authority',{}).get('stock')=='erp':
        fail('O stock desta empresa é controlado pelo ERP. O conector não suporta escrever estes movimentos.',409)
    # Serialize with imports and stock writers, before stock/duplicate checks.
    differences = []
    # A direct goods invoice creates an explicit, traceable receipt first; invoice posting
    # then follows the same path as an invoice linked to an earlier delivery.
    direct = [(i, line) for i, line in enumerate(payload['lines']) if row.kind=='invoice' and line['nature']=='goods' and not line.get('source_id')]
    if direct:
        if payload.get('purchase_id') or payload.get('erp_id') or policy(db)['mode']!='local':
            fail('Uma compra importada/integrada exige uma receção local explícita para evitar duplicar stock.')
        receipt_payload={**payload,'kind':'receipt','purchase_id':None,'document':'REC/'+payload['document'],
            'request_key':'direct:'+row.id,'lines':[dict(line) for _,line in direct], 'direct_invoice_id':row.id}
        receipt_payload.update(totals(receipt_payload['lines']))
        receipt=Doc(kind='receipt',store_id=row.store_id,supplier_id=row.supplier_id,user_id=user.id,
            request_key='direct:'+row.id,data=stock.dump(receipt_payload))
        db.add(receipt);db.flush()
        confirm_document(db,user,receipt,Confirm(revision=0,acknowledge_differences=True),authorize,audit)
        for source_index,(i,line) in enumerate(direct):
            line['source_id']=receipt.id;line['source_line']=source_index
        payload['direct_receipt_id']=receipt.id
    if row.kind in ('receipt', 'return'):
        for other in db.scalars(select(Doc).where(Doc.supplier_id == row.supplier_id, Doc.kind == row.kind, Doc.id != row.id, Doc.confirmed_at.is_not(None))):
            if stock.document_key(json.loads(other.data)['document']) == stock.document_key(payload['document']):
                fail('Este documento físico do fornecedor já foi confirmado.',409)
    for i, line in enumerate(payload['lines']):
        q = effective_quantity(row, line)
        source, original = None, None
        if line.get('source_id'):
            allowed = {'receipt': ['order'], 'invoice': ['receipt'] if line['nature']=='goods' else ['order'], 'return': ['receipt'], 'credit': ['return']}.get(row.kind, [])
            source, original = resolve_source(db, row, line, allowed)
            already = used(db, source.id, line['source_line'], row.kind)
            # Include multiple allocations to the same source within this submission.
            already += sum((effective_quantity(row, prev) for prev in payload['lines'][:i]
                if prev.get('source_id') == source.id and prev['source_line'] == line['source_line']), ZERO)
            limit = effective_quantity(source, original)
            compared_quantity=D(line['quantity']) if row.kind=='receipt' else q
            if already + compared_quantity > limit and not (row.kind == 'receipt' and options.allow_overdelivery):
                fail('A quantidade excede o saldo da linha de origem. Uma entrega superior exige autorização explícita.', 409)
            if net_price(line) != net_price(original) or D(line['vat'])!=D(original['vat']) or D(line['rejected']):
                differences.append(f'Linha {i+1}: preço ou mercadoria divergente da origem')
            db.add(Link(document_id=row.id, line_index=i, source_id=source.id, source_line=line['source_line'], quantity=q))
        if row.kind == 'return' and not source:
            fail('Associe cada devolução à receção original.')
        if row.kind in ('receipt', 'return') and line['nature'] != 'goods':
            fail('As receções e devoluções físicas só podem conter mercadorias.')
        if row.kind == 'return' and len(line['reason'].strip()) < 3:
            fail('Indique o motivo da devolução.')
        if row.kind == 'return' and original['warehouse_id'] != line['warehouse_id']:
            fail('A devolução deve usar o armazém da receção original. Registe primeiro uma transferência inversa se necessário.')
        if row.kind in ('receipt', 'return') and q > 0 or row.kind == 'invoice' and line['nature'] == 'goods' and not source:
            if row.kind == 'invoice' and (payload.get('purchase_id') or policy(db)['mode'] != 'local'):
                fail('Uma compra importada/integrada exige ligação a uma receção local explícita, para evitar duplicar stock.')
            original_entry = original.get('stock_entry_ids', [None])[0] if original and row.kind == 'return' else None
            entry_price=net_price(line)
            if row.kind!='return' and not stock.settings(db)['recoverable_vat']:
                meta=json.loads(db.get(StockArticle,line['article_id']).data)
                entry_price=entry_price*(1+D(line['vat'])/100)/(1+D(meta.get('vat_rate',0))/100)
            entries = stock.post(db, user, stock.MovementInput(
                article_id=line['article_id'], warehouse_id=line['warehouse_id'], kind='supplier_return' if row.kind == 'return' else 'purchase',
                quantity=q, unit_cost=entry_price.quantize(D('0.00000001')) if row.kind != 'return' else None,
                occurred_on=payload['document_date'], document=payload['document'] or row.id,
                reason=line['reason'] or LABELS[row.kind] + ' confirmada', request_key=f'procurement:{row.id}:{i}',
                supplier_id=row.supplier_id, lot_id=line.get('lot_id'), lot_number=line.get('lot_number',''),
                expiry=line.get('expiry'), original_id=original_entry, override_reason=line['reason'] if row.kind=='return' else ''), authorize, source='procurement')
            line['stock_entry_ids'] = [entry.id for entry in entries]
            for entry in entries:
                detail=json.loads(entry.data);detail['procurement_id']=row.id;entry.data=stock.dump(detail)
        if row.kind == 'invoice' and line['nature']=='goods' and source and q:
            ids = original.get('stock_entry_ids', [])
            if len(ids) != 1:
                fail('A receção não tem um movimento de stock inequívoco para valorizar.')
            line['valuation'] = valuation(db, user, db.get(StockEntry, ids[0]), q, net_price(line), line['vat'], row, i)
    if differences and not options.acknowledge_differences:
        fail('Existem divergências de preço/mercadoria. Reveja e confirme a aceitação das divergências.', 409)
    if row.kind in ('invoice', 'credit'):
        db.scalar(select(Store).order_by(Store.id).limit(1).with_for_update())
        entity = supplier(db, row.supplier_id)
        payload['category_id'] = payload.get('category_id') or entity.category_id
        if payload['total_cents'] <= 0:
            fail('A compra deve ter um total positivo.')
        split = allocations(payload, entity.category_id)
        if policy(db)['mode']=='local' and any(category is None for category, _ in split):
            fail('Indique a rubrica de cada linha (ou a rubrica por omissão da compra) para alimentar o plano de pagamentos.')
        payload['allocations'] = [{'category_id': category, 'amount_cents': amount} for category, amount in split if category]
        if len(entity.tax_number) != 9 or not entity.tax_number.isdigit():
            fail('Preencha um NIF de 9 dígitos na ficha do fornecedor.')
        target_identity = identity(entity.tax_number, 'NC' if row.kind == 'credit' else payload['document_type'], payload['document'])
        matches = [p for p in db.scalars(select(Purchase)) if identity(p.supplier_nif,p.document_type,p.document) == target_identity]
        purchase = db.get(Purchase, payload['purchase_id']) if payload.get('purchase_id') else None
        if matches and (not purchase or any(p.id != purchase.id for p in matches)):
            fail('Esta fatura já existe. Associe o documento existente em vez de o voltar a criar.', 409)
        amount = payload['total_cents'] * (-1 if row.kind == 'credit' else 1)
        if purchase:
            if identity(purchase.supplier_nif,purchase.document_type,purchase.document) != target_identity or purchase.amount_cents != amount:
                fail('O fornecedor, número, tipo ou total divergem do documento importado.', 409)
            if db.scalar(select(Doc.id).where(Doc.purchase_id == purchase.id, Doc.id != row.id)):
                fail('Este documento já está associado a uma compra.', 409)
        else:
            purchase = Purchase(store_id=row.store_id, supplier_nif=entity.tax_number, supplier_name=entity.name,
                document=payload['document'].strip(), document_type='NC' if row.kind == 'credit' else payload['document_type'],
                document_date=payload['document_date'], amount_cents=amount, base_cents=payload['base_cents'],
                tax_cents=payload['tax_cents'], origin='procurement', notes=payload['notes'], validated=False)
            db.add(purchase); db.flush()
        row.purchase_id = purchase.id
        if payload.get('erp_id'):
            erp=db.get(ERPDocument,payload['erp_id'])
            if identity(erp.supplier_nif,erp.document_type,erp.document)!=target_identity or erp.amount_cents!=amount or erp.document_date!=purchase.document_date:
                fail('A identidade, data ou total não coincidem com o documento ERP. Reveja a correspondência.',409)
            link=db.scalar(select(DocumentERPLink).where(DocumentERPLink.erp_id==erp.id,DocumentERPLink.undone_at.is_(None)))
            current_link=db.scalar(select(DocumentERPLink).where(DocumentERPLink.purchase_id==purchase.id,DocumentERPLink.undone_at.is_(None)))
            if link and link.purchase_id!=purchase.id or current_link and current_link.erp_id!=erp.id:
                fail('Já existe outra associação ERP ativa para este documento.',409)
            if not link:
                db.add(DocumentERPLink(purchase_id=purchase.id,erp_id=erp.id,reason='Correspondência confirmada no circuito de compras: NIF, tipo, número, data e total coincidem'))
        if payload['allocations'] and policy(db)['mode']=='local' and not db.scalar(select(PlannedMovement.id).where(PlannedMovement.purchase_id==purchase.id)):
            # Um só pagamento por fatura: o previsto fica com a rubrica de maior valor e guarda a repartição.
            planned = PlannedMovement(purchase_id=purchase.id, store_id=row.store_id, category_id=payload['allocations'][0]['category_id'],
                entity=entity.name, document=purchase.document, due_date=payload['due_date'], amount_cents=abs(amount), direction=1 if row.kind=='credit' else -1)
            db.add(planned); db.flush()
            for item in payload['allocations']:
                db.add(PlannedAllocation(planned_id=planned.id, category_id=item['category_id'], amount_cents=item['amount_cents']))
        if not db.get(DocumentState, purchase.id):
            db.add(DocumentState(purchase_id=purchase.id))
        # Existing expense proposal/financial workflow, not a second payable ledger.
        if not db.get(ExpenseDraft, purchase.id):
            db.add(ExpenseDraft(purchase_id=purchase.id, status='preparado', payload=stock.dump({
                'supplier_nif': entity.tax_number, 'supplier_name': entity.name, 'document': purchase.document,
                'document_type': purchase.document_type, 'document_date': purchase.document_date,
                'due_date': payload['due_date'], 'total': str(D(abs(amount))/100), 'reason': 'Compra confirmada; proposta por rever',
                'lines': [{'category': (lambda c: db.get(Category, c).name if c else 'Por classificar')(l.get('category_id') or payload.get('category_id')),
                    'description': l['description'], 'base': str(D(money(D(l['quantity'])*net_price(l)))/100), 'rate': str(l['vat']),
                    'tax': str(D(money(D(l['quantity'])*net_price(l)*D(l['vat'])/100))/100)} for l in payload['lines']]})))
    row.status = 'confirmed'
    row.confirmed_at = now()
    row.revision += 1
    payload['differences'] = differences
    entity=supplier(db,row.supplier_id)
    payload['supplier_snapshot']={'name':entity.name,'nif':entity.tax_number}
    row.data = stock.dump(payload)
    audit(db, user, 'compra_circuito_confirmada', row.id, {'kind': row.kind, 'purchase_id': row.purchase_id, 'differences': differences}, row.store_id)
    db.flush()
    return row

def register_routes(app, current, authorize, scope, audit):
    def get(db, user, identifier, write=False):
        row = db.get(Doc, identifier)
        if not row:
            fail('Documento não encontrado nesta empresa.',404)
        authorize(user, db, row.store_id, ['administrador','gestor'] if write else None)
        if write: stock.require(user,stock.settings(db),'view_cost')
        return row

    @app.get('/api/procurement')
    def listing(user=Depends(current), db: Session=Depends(db_session)):
        visible = stock.permissions(user,stock.settings(db))['view_cost']
        rows=list(db.scalars(select(Doc).where(Doc.store_id.in_(scope(user,db))).order_by(Doc.created_at.desc())))
        usage={(source,index,kind):quantity for source,index,kind,quantity in db.execute(select(Link.source_id,Link.source_line,Doc.kind,func.sum(Link.quantity))
            .join(Doc,Doc.id==Link.document_id).where(Link.source_id.in_([r.id for r in rows]),Doc.confirmed_at.is_not(None))
            .group_by(Link.source_id,Link.source_line,Doc.kind))}
        docs = [document_view(db,r,visible,usage) for r in rows]
        return {'documents': docs, 'mode': policy(db), 'can_write': user.role in ('administrador','gestor') and visible,
            'integration_note': 'Circuito local. Não envia encomendas, compras ou stock ao ERP. Artigos controlados pelo ERP permanecem apenas de leitura.'}

    @app.get('/api/procurement-ledger')
    def ledger(store_id:str='',start:date|None=None,end:date|None=None,article_id:str='',kind:str='',page:int=1,user=Depends(current),db:Session=Depends(db_session)):
        if page<1 or start and end and start>end:fail('Filtros de período/página inválidos.')
        stores=scope(user,db)
        if store_id:
            authorize(user,db,store_id);stores=[store_id]
        query=select(StockEntry).join(StockWarehouse).where(StockWarehouse.store_id.in_(stores))
        if start:query=query.where(StockEntry.occurred_on>=str(start))
        if end:query=query.where(StockEntry.occurred_on<=str(end))
        if article_id:query=query.where(StockEntry.article_id==article_id)
        if kind:query=query.where(StockEntry.kind==kind)
        entries=list(db.scalars(query.order_by(StockEntry.created_at.desc(),StockEntry.id.desc()).offset((page-1)*50).limit(51)))
        visible=stock.permissions(user,stock.settings(db))['view_cost']
        rows=[]
        for entry in entries[:50]:
            article=db.get(StockArticle,entry.article_id);wh=db.get(StockWarehouse,entry.warehouse_id);lot=db.get(StockLot,entry.lot_id) if entry.lot_id else None
            rows.append({**stock.entry_view(entry,visible),'article_name':article.name,'article_code':article.code,
                'warehouse_name':wh.name,'lot_number':lot.number if lot else '', 'expiry':lot.expiry if lot else '',
                'user_name':db.get(User,entry.user_id).name,'value':str(entry.quantity*entry.unit_cost) if visible and entry.unit_cost is not None else None})
        return {'rows':rows,'more':len(entries)>50}

    @app.post('/api/procurement')
    def create(data: Draft, user=Depends(current), db: Session=Depends(db_session)):
        stock.lock(db)
        validate(db,user,data,authorize)
        raw = data.model_dump(mode='json')
        digest = hashlib.sha256(stock.dump(raw).encode()).hexdigest()
        prior = db.scalar(select(Doc).where(Doc.request_key == data.request_key))
        if prior:
            authorize(user,db,prior.store_id,['administrador','gestor'])
            if json.loads(prior.data).get('creation_digest') != digest:
                fail('A chave de submissão já foi utilizada noutro documento.',409)
            return document_view(db,prior)
        raw.update(totals(raw['lines']))
        raw['creation_digest'] = digest
        row = Doc(kind=data.kind,store_id=data.store_id,supplier_id=data.supplier_id,user_id=user.id,request_key=data.request_key,data=stock.dump(raw))
        db.add(row);db.flush()
        audit(db,user,'compra_circuito_rascunho',row.id,{'kind':row.kind},row.store_id)
        db.commit()
        return document_view(db,row)

    @app.put('/api/procurement/{identifier}')
    def edit(identifier: str, data: Draft, user=Depends(current), db: Session=Depends(db_session)):
        stock.lock(db)
        row=get(db,user,identifier,True)
        if row.status!='draft' or row.revision!=data.revision or row.kind!=data.kind or row.store_id!=data.store_id:
            fail('Só pode editar um rascunho atualizado do mesmo tipo/estabelecimento.',409)
        validate(db,user,data,authorize)
        raw=data.model_dump(mode='json'); raw.update(totals(raw['lines']))
        raw['creation_digest']=json.loads(row.data).get('creation_digest')
        row.supplier_id=data.supplier_id; row.data=stock.dump(raw);row.revision+=1
        audit(db,user,'compra_circuito_editada',row.id,{'revision':row.revision},row.store_id);db.commit()
        return document_view(db,row)

    @app.post('/api/procurement/{identifier}/confirm')
    def confirm(identifier: str, data: Confirm, user=Depends(current), db: Session=Depends(db_session)):
        stock.lock(db)
        row=get(db,user,identifier,True)
        # Unknown snapshot-only keys must not be passed to the strict input schema.
        if not row.confirmed_at:
            raw=json.loads(row.data)
            editable={k:v for k,v in raw.items() if k in Draft.model_fields}
            for line in editable['lines']:
                for key in list(line):
                    if key not in Line.model_fields: line.pop(key)
            validate(db,user,Draft(**editable),authorize)
        result=confirm_document(db,user,row,data,authorize,audit)
        db.commit()
        return document_view(db,result)

    @app.post('/api/procurement/{identifier}/cancel')
    def cancel(identifier:str,data:Cancel,user=Depends(current),db:Session=Depends(db_session)):
        stock.lock(db);row=get(db,user,identifier,True)
        if row.revision!=data.revision or row.status=='cancelled':fail('Atualize o documento.',409)
        if row.confirmed_at and row.kind!='order':fail('Documentos físicos confirmados exigem devolução/inversão relacionada; não podem ser apagados.',409)
        if row.kind=='order' and any(used(db,row.id,i,'receipt') for i in range(len(json.loads(row.data)['lines']))):fail('Não pode cancelar uma encomenda com receções. As quantidades pendentes permanecem abertas.',409)
        row.status='cancelled';row.revision+=1
        audit(db,user,'compra_circuito_cancelada',row.id,{'reason':data.reason},row.store_id);db.commit()
        return document_view(db,row)

    @app.get('/api/procurement/{identifier}/pdf')
    def pdf(identifier:str,user=Depends(current),db:Session=Depends(db_session)):
        row=get(db,user,identifier)
        stock.require(user,stock.settings(db),'view_cost')
        return Response(pdf_bytes(db,row),media_type='application/pdf',headers={'Content-Disposition':f'inline; filename="compra-{row.id}.pdf"'})

    @app.post('/api/procurement/{identifier}/email-preview')
    def email_preview(identifier:str,data:EmailPreview,user=Depends(current),db:Session=Depends(db_session)):
        from .email_settings import configuration, email_address
        from .email_delivery import dispatch_view, preview_plan
        from .models import EmailDispatch
        from .dossier_package import zip_bytes
        from .platform import cipher
        stock.lock(db);row=get(db,user,identifier,True)
        stock.require(user,stock.settings(db),'view_cost')
        if row.kind!='order' or not row.confirmed_at or row.status=='cancelled':fail('Confirme a encomenda antes de preparar o envio.')
        if row.revision!=data.revision:fail('A encomenda mudou. Atualize a pré-visualização.',409)
        try: recipient=email_address(data.to)
        except ValueError as error: fail(str(error))
        config_row,config=configuration(db)
        if not config_row or not config['enabled']:fail('Ative o SMTP nas configurações de Email.')
        payload=json.loads(row.data)
        for email_id in payload.get('email_ids',[]):
            prior=db.get(EmailDispatch,email_id)
            if prior and prior.status in ('sending','accepted','received') or prior and prior.uncertain:
                fail('Já existe um envio aceite, em curso ou de resultado incerto. Consulte o histórico antes de repetir.',409)
        raw=pdf_bytes(db,row);key=f'encomenda-{row.id}.pdf';entity=supplier(db,row.supplier_id)
        dispatch=EmailDispatch(id=str(uuid4()),kind='supplier_order',request_key=str(uuid4()),created_by=user.id,
            created_at=now(),start_date=payload['document_date'],end_date=payload['document_date'],store_ids=stock.dump([row.store_id]),
            config_revision=config_row.revision,payload_ciphertext=cipher().encrypt(zip_bytes({key:raw})),
            draft=stock.dump({'to':[recipient],'cc':[],'subject':data.subject,'message':data.message,'selected_keys':[key],'format':'individual','allow_split':False}),
            manifest=stock.dump({'files':[{'key':key,'sha256':hashlib.sha256(raw).hexdigest(),'supplier':entity.name,'nif':entity.tax_number,
                'document':payload['document'] or row.id,'date':payload['document_date'],'amount_cents':payload['total_cents'],'original_name':key}]}))
        db.add(dispatch);db.flush();preview_plan(db,dispatch,json.loads(dispatch.draft),config)
        payload.setdefault('email_ids',[]).append(dispatch.id);row.data=stock.dump(payload);row.revision+=1
        audit(db,user,'encomenda_email_preparado',row.id,{'dispatch_id':dispatch.id},row.store_id);db.commit()
        return dispatch_view(db,dispatch)

    @app.post('/api/procurement/{identifier}/email-send/{dispatch_id}')
    def email_send(identifier:str,dispatch_id:str,data:SendInput,user=Depends(current),db:Session=Depends(db_session)):
        from .email_delivery import get_dispatch, submit_dispatch
        row=get(db,user,identifier,True)
        stock.require(user,stock.settings(db),'view_cost')
        if dispatch_id not in json.loads(row.data).get('email_ids',[]) or row.status=='cancelled':fail('Este envio não pertence à encomenda ativa.',409)
        return submit_dispatch(db,get_dispatch(db,dispatch_id,True),data,user,audit)

def pdf_bytes(db,row):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    data=json.loads(row.data);entity=db.get(BusinessEntity,row.supplier_id)
    supplier_name=data.get('supplier_snapshot',{}).get('name',entity.name)
    supplier_nif=data.get('supplier_snapshot',{}).get('nif',entity.tax_number)
    out=io.BytesIO();styles=getSampleStyleSheet()
    content=[Paragraph(escape(f'{LABELS[row.kind]} · {data["document"] or row.id[:8]}'),styles['Title']),
        Paragraph(escape(f'{supplier_name} · NIF {supplier_nif}'),styles['Normal']),
        Paragraph(escape(f'Data: {data["document_date"]} · Estado: {row.status} · Contacto: {data.get("contact","")}'),styles['Normal']),Spacer(1,16)]
    table=[['Artigo / descrição','Quantidade','Unidade','Preço','IVA']]
    for line in data['lines']:
        table.append([Paragraph(escape(line['description']),styles['Normal']),str(line['quantity']),line['unit'],str(line['price']),str(line['vat'])+'%'])
    t=Table(table,colWidths=[230,70,55,80,60],repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e5f7f3')),('GRID',(0,0),(-1,-1),0.4,colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP')]))
    content.extend([t,Spacer(1,16),Paragraph(f'Total: {data["total_cents"]/100:.2f} EUR',styles['Heading2']),
        Paragraph(escape(data.get('payment_terms','')),styles['Normal']),Paragraph(escape(data.get('notes','')),styles['Normal']),
        Paragraph('Documento de gestão interna. Não é documento fiscal.',styles['Normal'])])
    SimpleDocTemplate(out).build(content)
    return out.getvalue()
