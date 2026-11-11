"""Efeito financeiro das receções com tipo de documento (FC, FCD, NCC, NCCD…).

Ao confirmar a receção (nunca em rascunho), valor = total do documento × sinal:
- dívida (crédito, FC/NCC): compra em dívida e movimento previsto no vencimento; uma NC pode
  abater faturas em aberto do mesmo fornecedor (compensação sem dinheiro) e o resto fica previsto;
- dinheiro (FCD/NCCD): movimento real na data do documento — numerário na folha de caixa aberta da
  loja, outros meios na conta bancária do meio — e a compra fica paga (sem dívida).
Reutiliza a estrutura existente: Compra = documento em dívida, pagamentos de documentos = abates,
previsto → real pela liquidação de previstos. O efeito é o mesmo no modo integrado: o previsto local
vale até o ERP ter o documento (ver erp_covered); depois o valor em aberto vem da Wintouch. A
sincronização não importa pagamentos de compras como movimentos de caixa, por isso o movimento real
de um documento a dinheiro não se duplica.
Cada registo guarda a referência à receção, ao documento e ao tipo (rastreabilidade nos dois sentidos).
"""

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .document_identity import identity, number_key
from .models import (
    ArchiveDocument,
    ArchiveLink,
    BusinessEntity,
    CashSession,
    Category,
    DocumentERPLink,
    DocumentPayment,
    DocumentState,
    DriveDocumentAttachment,
    ERPDocument,
    ExpenseDraft,
    Movement,
    PaymentDocumentLine,
    PlannedAllocation,
    PlannedMovement,
    PlannedSettlement,
    Purchase,
    PurchaseAttachment,
    PurchaseDocumentType,
    Register,
    WintouchSaleRecord,
    now,
    uid,
)
from .purchase_document_types import ensure_defaults, purchase_code

LISBON = ZoneInfo("Europe/Lisbon")
DEFAULT_CATEGORY = "Compras de mercadoria"


def fail(message, status=422):
    raise HTTPException(status, message)


def document_type(db, identifier, *, usable=True):
    ensure_defaults(db)
    row = db.get(PurchaseDocumentType, identifier) if identifier else None
    if row is None:
        fail("Escolha um tipo de documento válido.")
    if usable and not row.active:
        fail(f"O tipo de documento «{row.code}» está inativo e não pode ser usado em novas receções.", 409)
    return row


def outstanding(db, purchase):
    from .procurement import paid_total

    return abs(purchase.amount_cents) - paid_total(db, purchase.id)


def open_invoices(db, supplier):
    """Faturas a crédito em aberto do fornecedor (para associar notas de crédito)."""
    rows = db.scalars(select(Purchase).where(Purchase.supplier_nif == supplier.tax_number, Purchase.amount_cents > 0))
    return [
        {"id": p.id, "document": p.document, "document_date": p.document_date, "amount_cents": p.amount_cents,
         "remaining_cents": outstanding(db, p)}
        for p in sorted(rows, key=lambda p: (p.document_date, p.document))
        if outstanding(db, p) > 0
    ]


def validate_receipt(db, payload, supplier):
    """Campos financeiros da receção (chamado no rascunho e ao confirmar)."""
    if not payload.document_type_id:
        return None
    kind = document_type(db, payload.document_type_id)
    if payload.kind != "receipt":
        fail("O tipo de documento aplica-se às receções de mercadoria.")
    if kind.creates_cash_movement and not (payload.payment_method or kind.default_payment_method):
        fail("Indique o meio de pagamento do documento a dinheiro.")
    if kind.creates_debt and payload.due_date and payload.due_date < payload.document_date:
        fail("O vencimento não pode ser anterior à data do documento.")
    if payload.credit_note_purchase_ids and kind.nature != "nota_credito_compra":
        fail("Só uma nota de crédito pode abater faturas.")
    for identifier in payload.credit_note_purchase_ids:
        invoice = db.get(Purchase, identifier)
        if not invoice or invoice.supplier_nif != supplier.tax_number or invoice.amount_cents <= 0:
            fail("As faturas a abater têm de ser do mesmo fornecedor.")
    return kind


def default_category(db, supplier, payload):
    category = payload.get("category_id") or supplier.category_id
    if category:
        return category
    db.execute(insert(Category).values(id=uid(), name=DEFAULT_CATEGORY).on_conflict_do_nothing(index_elements=[Category.name]))
    return db.scalar(select(Category.id).where(Category.name == DEFAULT_CATEGORY))


def rubric_split(payload, fallback_category, total_cents):
    """Repartição do documento pelas rubricas das linhas (rubrica da linha, do documento ou a de recurso),
    IVA incluído. A diferença de cêntimos para o total (desconto global, acerto) vai para a maior rubrica,
    para a repartição somar sempre o total do documento. Devolve [(rubrica, cêntimos)], da maior para a menor."""
    from .procurement import allocations  # importação tardia: procurement importa este módulo

    split = [[category or fallback_category, amount] for category, amount in allocations(payload, fallback_category)]
    merged = {}
    for category, amount in split:
        merged[category] = merged.get(category, 0) + amount
    rows = sorted(([c, a] for c, a in merged.items() if a > 0), key=lambda item: (-item[1], item[0] or ""))
    if not rows:
        return [(fallback_category, total_cents)]
    rows[0][1] += total_cents - sum(amount for _, amount in rows)
    return [(c, a) for c, a in rows if a > 0] or [(fallback_category, total_cents)]


def expense_lines(db, payload):
    """Linhas da proposta de despesa, com a rubrica de cada uma (como na compra direta)."""
    from .procurement import money, net_price

    names = {c.id: c.name for c in db.scalars(select(Category))}
    result = []
    for line in payload["lines"]:
        net = D(line["quantity"]) * net_price(line)
        category = line.get("category_id") or payload.get("category_id")
        result.append({"category": names.get(category, "Por classificar") if category else "Por classificar",
                       "description": line.get("description", ""), "base": str(D(money(net)) / 100),
                       "rate": str(line.get("vat", "0")), "tax": str(D(money(net * D(line.get("vat", 0)) / 100)) / 100)})
    return result


def due_date_of(payload, kind):
    if payload.get("due_date") and payload["due_date"] != payload["document_date"]:
        return payload["due_date"]
    days = kind.default_due_days or 0
    return (date.fromisoformat(payload["document_date"]) + timedelta(days=days)).isoformat()


def process(db, user, row, payload, audit):
    """Gera a compra e os movimentos da receção confirmada. Devolve o resumo guardado na receção."""
    kind = document_type(db, payload["document_type_id"], usable=False)
    supplier = db.get(BusinessEntity, row.supplier_id)
    total = payload["total_cents"]
    if total <= 0:
        fail("O total do documento tem de ser positivo.")
    signed = total * kind.sign
    code = purchase_code(kind)
    category = default_category(db, supplier, payload)
    # A receção com documento é um lançamento de compra: a repartição pelas rubricas das linhas alimenta
    # o previsto (PlannedAllocation), a proposta de despesa e as Despesas por rubrica, como na fatura.
    split = rubric_split(payload, category, total)
    payload["allocations"] = [{"category_id": c, "amount_cents": a} for c, a in split]
    category = split[0][0]
    summary = {"document_type_id": kind.id, "document_type_code": kind.code, "amount_cents": signed, "payment_ids": []}
    # Compra (documento em dívida ou já pago): reutiliza a importada (e-Fatura, ERP) com a mesma identidade.
    target = identity(supplier.tax_number, code, payload["document"])
    purchase = next((p for p in db.scalars(select(Purchase).where(Purchase.supplier_nif == supplier.tax_number))
                     if identity(p.supplier_nif, p.document_type, p.document) == target), None)
    if purchase is not None:
        if purchase.amount_cents != signed:
            fail(f"Já existe o documento {payload['document']} deste fornecedor com outro total. Reveja o número ou o total.", 409)
        summary["reused_purchase"] = True
    else:
        purchase = Purchase(
            store_id=row.store_id, supplier_nif=supplier.tax_number, supplier_name=supplier.name,
            document=payload["document"].strip(), document_type=code, document_date=payload["document_date"],
            amount_cents=signed, base_cents=payload["base_cents"] * kind.sign, tax_cents=payload["tax_cents"] * kind.sign,
            origin="rececao", notes=f"{kind.code} · receção {row.id}", validated=False, document_type_id=kind.id,
        )
        db.add(purchase)
        db.flush()
        db.add(DocumentState(purchase_id=purchase.id))
        db.add(ExpenseDraft(purchase_id=purchase.id, status="preparado", payload=json.dumps({
            "supplier_nif": supplier.tax_number, "supplier_name": supplier.name, "document": purchase.document,
            "document_type": code, "document_date": purchase.document_date, "total": f"{abs(signed) / 100:.2f}",
            "reason": f"Receção de mercadoria confirmada ({kind.code}); proposta por rever", "lines": expense_lines(db, payload),
        }, ensure_ascii=False)))
    row.purchase_id = purchase.id
    summary["purchase_id"] = purchase.id
    restore_attachments(db, row, purchase)
    attached = attach_originals(db, user, payload, purchase)
    if attached:
        summary["attached_archive_ids"] = attached
    # Documento da Wintouch já importado: associa (sem duplicar compras).
    erp = next((e for e in db.scalars(select(ERPDocument).where(ERPDocument.supplier_nif == supplier.tax_number, ERPDocument.cancelled.is_(False)))
                if identity(e.supplier_nif, e.document_type, e.document) == target and e.amount_cents == signed), None)
    if erp and not db.scalar(select(DocumentERPLink).where(DocumentERPLink.erp_id == erp.id, DocumentERPLink.undone_at.is_(None))):
        db.add(DocumentERPLink(purchase_id=purchase.id, erp_id=erp.id, reason=f"Receção confirmada ({kind.code}): identidade e total coincidem"))
    remaining = total
    if kind.creates_debt and payload.get("credit_note_purchase_ids"):
        # NC que abate faturas em aberto: compensação sem dinheiro (fatura e NC ficam saldadas pelo valor).
        lines = []
        for identifier in payload["credit_note_purchase_ids"]:
            invoice = db.get(Purchase, identifier)
            amount = min(remaining, outstanding(db, invoice))
            if amount > 0:
                lines.append((invoice, amount))
                remaining -= amount
        if lines:
            used = sum(amount for _, amount in lines)
            payment = DocumentPayment(
                store_id=row.store_id, payment_date=payload["document_date"], amount_cents=used, direction=-1,
                entity=supplier.name, reference=f"{kind.code} {payload['document']}", kind="nota_credito", method="outro",
                source="compensacao", source_id=row.id, request_key=f"rececao-nc:{row.id}:{payload.get('edition', 0)}",
            )
            db.add(payment)
            db.flush()
            for invoice, amount in lines:
                db.add(PaymentDocumentLine(payment_id=payment.id, purchase_id=invoice.id, amount_cents=amount))
            db.add(PaymentDocumentLine(payment_id=payment.id, purchase_id=purchase.id, amount_cents=used))
            summary["payment_ids"].append(payment.id)
            summary["compensated"] = [{"purchase_id": i.id, "document": i.document, "amount_cents": a} for i, a in lines]
    if kind.creates_debt and remaining > 0 and not kind.creates_cash_movement:
        planned = PlannedMovement(
            purchase_id=purchase.id, store_id=row.store_id, category_id=category, entity=supplier.name,
            document=f"{kind.code} {payload['document']}", due_date=due_date_of(payload, kind),
            amount_cents=total, direction=-kind.sign,
        )
        db.add(planned)
        db.flush()
        for category_id, amount in split:
            db.add(PlannedAllocation(planned_id=planned.id, category_id=category_id, amount_cents=amount))
        summary["planned_id"] = planned.id
    if kind.creates_cash_movement:
        movement = real_movement(db, user, row, payload, kind, supplier, category, total)
        summary["movement_id"] = movement.id
        payment = DocumentPayment(
            store_id=row.store_id, payment_date=payload["document_date"], amount_cents=total, direction=-kind.sign,
            entity=supplier.name, reference=f"{kind.code} {payload['document']}", kind="pagamento",
            method=movement.payment if movement.payment in ("numerario", "transferencia", "cartao") else "outro",
            source="movement", source_id=movement.id, request_key=f"rececao-pag:{movement.id}",
        )
        db.add(payment)
        db.flush()
        db.add(PaymentDocumentLine(payment_id=payment.id, purchase_id=purchase.id, amount_cents=total))
        summary["payment_ids"].append(payment.id)
    audit(db, user, "rececao_movimentos_financeiros", row.id, summary, row.store_id)
    return summary


def loose_number(value):
    """Número do documento sem o código do tipo à frente («FT 2026A/12» e «2026A/12» coincidem)."""
    return re.sub(r"^[A-Z]{1,4}\s+(?=\S*\d)", "", number_key(value or ""))


def erp_covered(db, purchase_ids):
    """Compras nascidas de receções que o ERP já tem: o previsto local deixa de contar.

    Coberta quando há associação ativa ao documento ERP, quando o documento de compra importado tem a
    mesma identidade e total, ou quando a Wintouch lista um valor em aberto do mesmo fornecedor com o
    mesmo número. Desfeita a associação (ou sem documento no ERP), o previsto local volta a contar.
    """
    purchases = [p for p in db.scalars(select(Purchase).where(Purchase.id.in_(set(purchase_ids) or [""])))
                 if p.origin == "rececao"]
    covered = set(db.scalars(select(DocumentERPLink.purchase_id).where(
        DocumentERPLink.purchase_id.in_([p.id for p in purchases] or [""]), DocumentERPLink.undone_at.is_(None))))
    pending = [p for p in purchases if p.id not in covered]
    if not pending:
        return covered
    nifs = {p.supplier_nif for p in pending}
    erp = {(identity(e.supplier_nif, e.document_type, e.document), e.amount_cents)
           for e in db.scalars(select(ERPDocument).where(ERPDocument.supplier_nif.in_(nifs), ERPDocument.cancelled.is_(False)))}
    open_items = set()
    for record in db.scalars(select(WintouchSaleRecord).where(
            WintouchSaleRecord.kind == "previsto", WintouchSaleRecord.active.is_(True),
            WintouchSaleRecord.data.contains('"document_group": "compras"'))):
        data = json.loads(record.data)
        if data.get("entity_nif") in nifs and data.get("document_reference"):
            open_items.add((data["entity_nif"], loose_number(data["document_reference"])))
    for p in pending:
        if (identity(p.supplier_nif, p.document_type, p.document), p.amount_cents) in erp \
                or (p.supplier_nif, loose_number(p.document)) in open_items:
            covered.add(p.id)
    return covered


def real_movement(db, user, row, payload, kind, supplier, category, amount):
    """Movimento real na data do documento: numerário na folha de caixa aberta; outros meios na conta do meio."""
    from . import cash_posting
    from .main import balance

    method = payload.get("payment_method") or kind.default_payment_method or "numerario"
    direction = -kind.sign  # FCD sai da caixa; NCCD (reembolso do fornecedor) entra.
    occurred = datetime.combine(date.fromisoformat(payload["document_date"]), time(12), tzinfo=LISBON)
    values = dict(
        user_id=user.id, category_id=category, kind="fornecedor", amount_cents=amount, direction=direction,
        occurred_at=occurred, origin="rececao", reference=row.id, document=f"{kind.code} {payload['document']}",
        entity=supplier.name, notes=f"{kind.description} · receção de mercadoria",
        request_key=f"rececao:{row.id}:{payload.get('edition', 0)}",
    )
    if method == "numerario":
        session = db.scalar(
            select(CashSession).join(Register, Register.id == CashSession.register_id)
            .where(Register.store_id == row.store_id, CashSession.closed_at.is_(None))
            .order_by(CashSession.opened_at.desc()).with_for_update(of=CashSession)
        )
        if session is None:
            fail("Para um documento a dinheiro em numerário, abra a folha de caixa desta loja antes de confirmar a receção.", 409)
        if direction < 0 and amount > balance(db, session):
            fail("Numerário insuficiente na caixa aberta para pagar este documento.", 409)
        movement = Movement(session_id=session.id, payment="numerario", bank_account_id=None, **values)
    else:
        account = cash_posting.default_bank(db, row.store_id, method)
        if not account:
            fail(f"Associe uma conta bancária ao meio de pagamento «{method}» desta loja antes de confirmar.", 409)
        movement = Movement(session_id=None, payment=method, payment_method_id=method, bank_account_id=account, **values)
    db.add(movement)
    db.flush()
    return movement


CARRIED = "receipt_purchase_attachments"


def attach_originals(db, user, payload, purchase):
    """O original da receção (fatura lida do arquivo e PDF anexados) fica anexo da compra e associado no
    Arquivo digital, como na associação confirmada à mão. Associações rejeitadas ou anuladas mantêm-se."""
    from .archive import confirm as confirm_link

    attached = []
    for identifier in dict.fromkeys(i for i in [payload.get("archive_document_id"), *payload.get("archive_attachment_ids", [])] if i):
        if not db.get(ArchiveDocument, identifier):
            continue
        link = db.scalar(select(ArchiveLink).where(
            ArchiveLink.document_id == identifier, ArchiveLink.target_kind == "purchase", ArchiveLink.target_id == purchase.id))
        if link is not None and link.status != "proposta":
            continue
        if link is None:
            link = ArchiveLink(document_id=identifier, target_kind="purchase", target_id=purchase.id, status="proposta",
                               score=100, strength="forte", method="rececao_mercadoria",
                               reasons=json.dumps(["Documento de origem da receção de mercadoria"], ensure_ascii=False))
            db.add(link)
            db.flush()
        confirm_link(db, link, user, "Receção de mercadoria: documento de origem da compra")
        attached.append(identifier)
    db.flush()
    return attached


def archive_copy(db, model, values, links):
    """Anexo que é só uma cópia de um documento guardado no Arquivo digital (pode sair sem perda)."""
    for link in links:
        doc = db.get(ArchiveDocument, link.document_id) if link.status == "confirmada" else None
        if doc is None:
            continue
        if model is DriveDocumentAttachment and doc.storage == "drive" and values["drive_id"] == doc.storage_ref:
            return True
        if model is PurchaseAttachment and doc.storage != "drive" and doc.storage_ref != f"purchase_attachments:{values['id']}":
            from .archive import read_bytes

            if hashlib.sha256(values["data"]).hexdigest() == (doc.sha256 or hashlib.sha256(read_bytes(db, doc)[0]).hexdigest()):
                return True
    return False


def carry_attachments(db, row, purchase):
    """A compra desta receção sai (edição ou anulação): guarda os anexos e as associações do arquivo para a
    compra que a substitui; sem substituta, `settle_attachments` decide."""
    links = list(db.scalars(select(ArchiveLink).where(ArchiveLink.target_kind == "purchase", ArchiveLink.target_id == purchase.id)))
    items = []
    for model in (PurchaseAttachment, DriveDocumentAttachment):
        for item in db.scalars(select(model).where(model.purchase_id == purchase.id)):
            values = {column.key: getattr(item, column.key) for column in model.__table__.columns}
            items.append((model, values, archive_copy(db, model, values, links)))
            db.delete(item)
    state = db.get(DocumentState, purchase.id)
    db.flush()
    if items or links:
        db.info.setdefault(CARRIED, {})[row.id] = {
            "items": items, "links": [link.id for link in links], "availability": state.availability if state else None,
        }


def restore_attachments(db, row, purchase):
    """Edição da receção: os anexos e as associações da compra anterior passam para a nova."""
    carried = db.info.get(CARRIED, {}).pop(row.id, None)
    if not carried:
        return
    for model, values, _ in carried["items"]:
        db.add(model(**{**values, "purchase_id": purchase.id}))
    for link in (db.get(ArchiveLink, identifier) for identifier in carried["links"]):
        duplicate = db.scalar(select(ArchiveLink.id).where(
            ArchiveLink.document_id == link.document_id, ArchiveLink.target_kind == "purchase", ArchiveLink.target_id == purchase.id))
        if duplicate:
            db.delete(link)
        else:
            link.target_id = purchase.id
    db.flush()
    state = db.get(DocumentState, purchase.id)
    if state is not None and carried["availability"] and carried["items"]:
        state.availability = carried["availability"]


def settle_attachments(db, user, row):
    """Compra da receção retirada sem substituta (anulação, ou edição sem tipo de documento): saem as
    cópias de documentos do Arquivo digital (o original fica lá, com a associação anulada); anexos
    próprios da compra não se perdem em silêncio."""
    from .archive import event

    carried = db.info.get(CARRIED, {}).pop(row.id, None)
    if not carried:
        return
    own = [values["name"] for _, values, copy in carried["items"] if not copy]
    if own:
        fail(
            f"A compra desta receção tem anexos próprios ({', '.join(own)}), que se perderiam com ela. "
            "Mantenha o tipo de documento na receção, ou guarde esses anexos noutro documento, e volte a tentar.",
            409,
        )
    for link in (db.get(ArchiveLink, identifier) for identifier in carried["links"]):
        if link.status in ("proposta", "confirmada"):
            link.status, link.decided_by, link.decided_at = "anulada", getattr(user, "id", None), now()
            event(db, link.document_id, user, "associacao_anulada", {"alvo": "purchase", "id": link.target_id,
                                                                       "nota": "A compra da receção deixou de existir"})
    db.flush()


def undo(db, user, row, payload, audit, reason):
    """Desfaz o efeito financeiro (edição ou anulação): estorno dos movimentos reais, sem os apagar."""
    summary = payload.get("financial") or {}
    if not summary:
        return
    purchase = db.get(Purchase, summary.get("purchase_id")) if summary.get("purchase_id") else None
    ours = set(summary.get("payment_ids", []))
    if purchase is not None:
        others = db.scalar(
            select(func.count()).select_from(PaymentDocumentLine)
            .where(PaymentDocumentLine.purchase_id == purchase.id, PaymentDocumentLine.payment_id.not_in(ours or [""]))
        )
        if others:
            fail("O documento desta receção já tem pagamentos ou compensações registados: desfaça-os antes de alterar ou anular.", 409)
    planned = db.get(PlannedMovement, summary["planned_id"]) if summary.get("planned_id") else None
    if planned is not None and db.scalar(select(PlannedSettlement.id).where(PlannedSettlement.planned_id == planned.id).limit(1)):
        fail("O previsto desta receção já foi liquidado: desfaça a liquidação antes de alterar ou anular.", 409)
    for identifier in ours:
        payment = db.get(DocumentPayment, identifier)
        if payment is None:
            continue
        for line in db.scalars(select(PaymentDocumentLine).where(PaymentDocumentLine.payment_id == payment.id)):
            db.delete(line)
        db.flush()  # Sem relações declaradas: as linhas saem antes do pagamento.
        db.delete(payment)
        db.flush()
    if summary.get("movement_id"):
        original = db.get(Movement, summary["movement_id"])
        if original is not None and not original.reversed_by:
            reversal = Movement(
                session_id=original.session_id, user_id=user.id, category_id=original.category_id,
                bank_account_id=original.bank_account_id, payment_method_id=original.payment_method_id,
                kind=original.kind, payment=original.payment, amount_cents=original.amount_cents,
                direction=-original.direction, occurred_at=now(), origin="rececao", reference=row.id,
                document=original.document, entity=original.entity, notes=f"Estorno: {reason}",
                request_key=uid(), reversal_of=original.id,
            )
            db.add(reversal)
            db.flush()
            original.reversed_by = reversal.id
    if planned is not None:
        for allocation in db.scalars(select(PlannedAllocation).where(PlannedAllocation.planned_id == planned.id)):
            db.delete(allocation)
        db.delete(planned)
    db.flush()
    if purchase is not None:
        row.purchase_id = None
        db.flush()  # A receção deixa de apontar para a compra antes de a compra sair.
        if not summary.get("reused_purchase") and purchase.origin == "rececao":
            # A compra nasceu desta receção e não tem outros pagamentos: deixa de existir com ela.
            carry_attachments(db, row, purchase)
            for model in (DocumentState, ExpenseDraft):
                item = db.get(model, purchase.id)
                if item is not None:
                    db.delete(item)
            for link in db.scalars(select(DocumentERPLink).where(DocumentERPLink.purchase_id == purchase.id)):
                db.delete(link)
            db.flush()
            db.delete(purchase)
    audit(db, user, "rececao_movimentos_desfeitos", row.id, {**summary, "motivo": reason}, row.store_id)
    db.flush()
