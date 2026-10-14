"""Explicit reconciliation only: never creates revenue or expense movements."""

import json
import re
import unicodedata
from datetime import date
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .cash_sheet import day as local_day
from .cash_sheet import payment_key
from .db import db_session
from .models import (
    Audit,
    BankAccount,
    BankTransaction,
    DepositCredit,
    CashSession,
    DocumentPayment,
    Movement,
    PaymentDocumentLine,
    Purchase,
    PurchaseAllocation,
    Reconciliation,
    ReconciliationIgnore,
    ReconciliationLine,
    Register,
    WintouchSaleRecord,
    now,
)
from .schemas import Strict

Source = Literal["bank", "purchase", "movement", "wintouch", "payment"]
MODELS = {
    "bank": BankTransaction,
    "purchase": Purchase,
    "movement": Movement,
    "wintouch": WintouchSaleRecord,
    "payment": DocumentPayment,
}


class Line(Strict):
    source: Source
    source_id: str
    amount_cents: int = Field(strict=True, gt=0, le=10**15)


class Confirmation(Strict):
    lines: list[Line] = Field(min_length=2, max_length=200)
    kind: Literal["pagamento", "cartao", "transferencia"] = "pagamento"
    commission_cents: int = Field(strict=True, ge=0, le=10**15, default=0)
    difference_cents: int = Field(strict=True, ge=-(10**15), le=10**15, default=0)
    reason: str = Field(max_length=2000, default="")


class Justification(Strict):
    reason: str = Field(min_length=3, max_length=2000)


def used(db, source, source_id):
    if source == "payment":
        payment = db.get(DocumentPayment, source_id)
        if payment and payment.source in {"movement", "wintouch"}:
            return used(db, payment.source, payment.source_id)
    total = int(
        db.scalar(
            select(func.sum(ReconciliationLine.amount_cents))
            .join(Reconciliation, Reconciliation.id == ReconciliationLine.reconciliation_id)
            .where(
                Reconciliation.undone_at.is_(None),
                ReconciliationLine.source == source,
                ReconciliationLine.source_id == source_id,
            )
        )
        or 0
    )
    if source == "bank":
        total += int((db.scalar(select(func.sum(DepositCredit.amount)).where(DepositCredit.transaction_id == source_id, DepositCredit.undone_at.is_(None))) or 0) * 100)
    if source in {"bank", "purchase"}:
        column = (
            PurchaseAllocation.transaction_id
            if source == "bank"
            else PurchaseAllocation.purchase_id
        )
        total += int(
            db.scalar(select(func.sum(PurchaseAllocation.amount_cents)).where(column == source_id))
            or 0
        )
    if source == "purchase":
        total += int(
            db.scalar(
                select(func.sum(PaymentDocumentLine.amount_cents)).where(
                    PaymentDocumentLine.purchase_id == source_id
                )
            )
            or 0
        )
    if source in {"movement", "wintouch"}:
        total += int(
            db.scalar(
                select(func.sum(ReconciliationLine.amount_cents))
                .join(Reconciliation)
                .join(DocumentPayment, DocumentPayment.id == ReconciliationLine.source_id)
                .where(
                    Reconciliation.undone_at.is_(None),
                    ReconciliationLine.source == "payment",
                    DocumentPayment.source == source,
                    DocumentPayment.source_id == source_id,
                )
            )
            or 0
        )
    return total


def identified(db, source, source_id):
    """An allocated real payment also protects its source before bank matching."""
    return bool(
        db.scalar(
            select(DocumentPayment.id)
            .join(PaymentDocumentLine)
            .where(DocumentPayment.source == source, DocumentPayment.source_id == source_id)
            .limit(1)
        )
    )


def valid_payment_origin(db, payment):
    if payment.source == "manual":
        return True
    row = db.get(MODELS[payment.source], payment.source_id)
    if not row:
        return False
    if payment.source == "movement":
        if (
            row.reversed_by
            or row.reversal_of
            or row.transfer_id
            or row.kind in {"deposito", "reforco", "transferencia_caixa"}
        ):
            return False
        session = db.get(CashSession, row.session_id)
        store = db.get(Register, session.register_id).store_id
        amount, direction, method, day = (
            row.amount_cents,
            row.direction,
            row.payment,
            local_day(row.occurred_at),
        )
    else:
        if not row.active or row.kind != "real":
            return False
        data = json.loads(row.data)
        method, _ = payment_key(data)
        store, amount, direction = row.store_id, data.get("amount_cents"), data.get("direction")
        day = local_day(data.get("occurred_at") or row.document_date)
    return (store, amount, direction, method, day) == (
        payment.store_id,
        payment.amount_cents,
        payment.direction,
        payment.method,
        payment.payment_date,
    )


def normalized(value):
    return " ".join(
        re.findall(
            r"[a-z0-9]+",
            "".join(
                c
                for c in unicodedata.normalize("NFD", value.lower())
                if not unicodedata.combining(c)
            ),
        )
    )


def suggestion(bank, internal):
    if (
        bank["store_id"] != internal["store_id"]
        or bank["amount_cents"] * internal["amount_cents"] <= 0
    ):
        return None
    reasons, score = [], 0
    if bank["available_cents"] == internal["available_cents"]:
        score += 45
        reasons.append("Montante disponível igual")
    elif bank["available_cents"] < internal["available_cents"]:
        score += 10
        reasons.append("Possível pagamento parcial ou comissão")
    days = abs((date.fromisoformat(bank["date"]) - date.fromisoformat(internal["date"])).days)
    if days <= 3:
        score += 20
        reasons.append(f"Datas próximas ({days} dias)")
    elif days <= 10:
        score += 10
        reasons.append(f"Datas a {days} dias")
    description = normalized(bank["description"])
    reference = normalized(internal["reference"])
    entity = normalized(internal["entity"])
    if len(reference) >= 4 and reference in description:
        score += 25
        reasons.append("Referência encontrada na descrição bancária")
    if len(entity) >= 4 and entity in description:
        score += 20
        reasons.append("Entidade encontrada na descrição bancária")
    tokens = {t for t in normalized(internal["description"]).split() if len(t) >= 4}
    if tokens & set(description.split()):
        score += 10
        reasons.append("Termos comuns na descrição")
    if score < 30:
        return None
    return {
        "bank_id": bank["id"],
        "source": internal["source"],
        "source_id": internal["id"],
        "score": min(score, 100),
        "confidence": "Alta" if score >= 80 else "Média" if score >= 50 else "Baixa",
        "reasons": reasons,
        "amount_cents": min(bank["available_cents"], internal["available_cents"]),
    }


def card_suggestions(banks, internals):
    batches = {}
    for item in internals:
        if (
            item["payment"] != "cartao"
            or item["amount_cents"] <= 0
            or not item["available_cents"]
            or item["status"] == "ignorado"
        ):
            continue
        batches.setdefault((item["store_id"], item["date"]), []).append(item)
    result = []
    for bank in banks:
        if bank["amount_cents"] <= 0 or not bank["available_cents"] or bank["status"] == "ignorado":
            continue
        for (store, day), batch in batches.items():
            days = (date.fromisoformat(bank["date"]) - date.fromisoformat(day)).days
            if store != bank["store_id"] or not 0 <= days <= 10 or not 2 <= len(batch) <= 199:
                continue
            gross = sum(i["available_cents"] for i in batch)
            commission = gross - bank["available_cents"]
            if not 0 <= commission <= gross // 10:
                continue
            result.append(
                {
                    "bank_id": bank["id"],
                    "date": day,
                    "gross_cents": gross,
                    "net_cents": bank["available_cents"],
                    "commission_cents": commission,
                    "score": 65 if commission == 0 else 45,
                    "confidence": "Média" if commission == 0 else "Baixa",
                    "reasons": [
                        f"{len(batch)} recebimentos de cartão do mesmo dia",
                        f"Crédito {days} dias depois",
                        "Soma igual ao líquido"
                        if not commission
                        else "Diferença compatível com comissão; confirme no comprovativo do banco",
                    ],
                    "lines": [
                        {
                            "source": i["source"],
                            "source_id": i["id"],
                            "amount_cents": i["available_cents"],
                        }
                        for i in batch
                    ],
                }
            )
    return result


def register_routes(app, current, authorize, scope, record, serial):
    def source_row(db, source, row, context=None):
        if source == "bank":
            account = (
                context["accounts"][row.account_id]
                if context
                else db.get(BankAccount, row.account_id)
            )
            store, amount, day = account.store_id, row.amount_cents, row.movement_date
            description, entity, reference, payment = row.description, "", "", ""
        elif source == "purchase":
            if row.origin == "efatura_testes":
                return None
            store, amount, day = row.store_id, -row.amount_cents, row.document_date
            description, entity, reference, payment = (
                row.document,
                row.supplier_name,
                row.document,
                "",
            )
        elif source == "payment":
            if row.method == "numerario":
                return None
            if not valid_payment_origin(db, row):
                return None
            if row.source in {"movement", "wintouch"}:
                original = db.get(MODELS[row.source], row.source_id)
                projected = source_row(db, row.source, original, context) if original else None
                if (
                    not projected
                    or projected["store_id"] != row.store_id
                    or projected["amount_cents"] != row.amount_cents * row.direction
                ):
                    return None
            store, amount, day = row.store_id, row.amount_cents * row.direction, row.payment_date
            description, entity, reference, payment = (
                row.kind,
                row.entity,
                row.reference,
                row.method,
            )
        elif source == "movement":
            if row.origin == "deposito_modulo":
                return None
            if (
                row.reversed_by
                or row.reversal_of
                or row.transfer_id
                or (row.payment == "numerario" and row.kind not in {"deposito", "reforco"})
            ):
                return None
            session = (
                context["sessions"][row.session_id]
                if context
                else db.get(CashSession, row.session_id)
            )
            register = (
                context["registers"][session.register_id]
                if context
                else db.get(Register, session.register_id)
            )
            store = register.store_id
            amount, day = row.amount_cents * row.direction, local_day(row.occurred_at)
            if row.origin == "deposito_modulo":
                return None
            if row.kind in {"deposito", "reforco"}:
                amount = -amount  # Cash out is bank in, and cash reinforcement is bank out.
            description = {"deposito": "Depósito próprio", "reforco": "Reforço de caixa"}.get(
                row.kind, row.notes or row.kind
            )
            entity, reference, payment = row.entity, row.reference or row.document, row.payment
        else:
            if not row.active or row.kind != "real":
                return None
            values = json.loads(row.data)
            payment, _ = payment_key(values)
            if payment == "numerario":
                return None
            store, amount, day = (
                row.store_id,
                int(values["amount_cents"]) * int(values["direction"]),
                local_day(values["occurred_at"])
                if values.get("occurred_at")
                else row.document_date,
            )
            description = values.get("notes") or values.get("description") or "Recebimento Wintouch"
            entity, reference = (
                values.get("entity", ""),
                values.get("reference") or values.get("document", ""),
            )
        return {
            "id": row.id,
            "source": source,
            "store_id": store,
            "amount_cents": amount,
            "date": day,
            "description": description,
            "entity": entity,
            "reference": reference,
            "payment": payment,
            "transfer": source == "movement"
            and (bool(row.transfer_id) or row.kind in {"deposito", "retirada_deposito", "reforco"}),
            "account": account.name if source == "bank" else "",
        }

    def get_source(db, user, source, source_id):
        if source == "payment":
            alias = db.get(DocumentPayment, source_id)
            if alias and alias.source in {"movement", "wintouch"}:
                get_source(db, user, alias.source, alias.source_id)
        row = db.scalar(
            select(MODELS[source])
            .where(MODELS[source].id == source_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        projection = source_row(db, source, row) if row else None
        if not projection:
            raise HTTPException(404, "Movimento indisponível para reconciliação.")
        authorize(user, db, projection["store_id"], ["administrador", "gestor"])
        return projection

    def ignored(db, source, source_id):
        result = db.scalar(
            select(ReconciliationIgnore).where(
                ReconciliationIgnore.source == source, ReconciliationIgnore.source_id == source_id
            )
        )
        if not result and source == "payment":
            alias = db.get(DocumentPayment, source_id)
            if alias and alias.source in {"movement", "wintouch"}:
                return ignored(db, alias.source, alias.source_id)
        if not result and source in {"movement", "wintouch"}:
            alias = db.scalar(
                select(DocumentPayment).where(
                    DocumentPayment.source == source, DocumentPayment.source_id == source_id
                )
            )
            if alias:
                return db.scalar(
                    select(ReconciliationIgnore).where(
                        ReconciliationIgnore.source == "payment",
                        ReconciliationIgnore.source_id == alias.id,
                    )
                )
        return result

    @app.get("/api/reconciliation")
    def overview(user=Depends(current), db: Session = Depends(db_session)):
        stores = set(scope(user, db))
        banks, internals = [], []
        # Aggregate allocations once; the overview must remain usable with large imports.
        totals = {
            (s, i): int(n)
            for s, i, n in db.execute(
                select(
                    ReconciliationLine.source,
                    ReconciliationLine.source_id,
                    func.sum(ReconciliationLine.amount_cents),
                )
                .join(Reconciliation)
                .where(Reconciliation.undone_at.is_(None))
                .group_by(ReconciliationLine.source, ReconciliationLine.source_id)
            )
        }
        for p, b, n in db.execute(
            select(
                PurchaseAllocation.purchase_id,
                PurchaseAllocation.transaction_id,
                PurchaseAllocation.amount_cents,
            )
        ):
            for key in (("purchase", p), ("bank", b)):
                totals[key] = totals.get(key, 0) + n
        ignores = {
            (r.source, r.source_id): r.reason for r in db.scalars(select(ReconciliationIgnore))
        }
        context = {
            "accounts": {
                r.id: r
                for r in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores)))
            },
            "registers": {
                r.id: r for r in db.scalars(select(Register).where(Register.store_id.in_(stores)))
            },
            "sessions": {
                r.id: r
                for r in db.scalars(
                    select(CashSession).join(Register).where(Register.store_id.in_(stores))
                )
            },
        }
        queries = {
            "bank": select(BankTransaction)
            .join(BankAccount)
            .where(BankAccount.store_id.in_(stores)),
            "purchase": select(Purchase).where(Purchase.store_id.in_(stores)),
            "movement": select(Movement)
            .join(CashSession)
            .join(Register)
            .where(Register.store_id.in_(stores)),
            "wintouch": select(WintouchSaleRecord).where(
                WintouchSaleRecord.store_id.in_(stores),
                WintouchSaleRecord.active.is_(True),
                WintouchSaleRecord.kind == "real",
            ),
            "payment": select(DocumentPayment).where(DocumentPayment.store_id.in_(stores)),
        }
        wrapped = {
            (r.source, r.source_id): r
            for r in db.scalars(
                select(DocumentPayment).where(DocumentPayment.source.in_(["movement", "wintouch"]))
            )
        }
        document_claims = {}
        for allocation in db.scalars(select(PaymentDocumentLine)):
            source_key = ("purchase", allocation.purchase_id)
            totals[source_key] = totals.get(source_key, 0) + allocation.amount_cents
            document_claims[allocation.purchase_id] = (
                document_claims.get(allocation.purchase_id, 0) + allocation.amount_cents
            )
        for source_key, p in wrapped.items():
            total = totals.get(source_key, 0) + totals.get(("payment", p.id), 0)
            totals[source_key] = total
            totals["payment", p.id] = total
        for source, model in MODELS.items():
            for row in db.scalars(queries[source]):
                item = source_row(db, source, row, context)
                if (source, row.id) in wrapped:
                    continue
                if not item or item["store_id"] not in stores or not item["amount_cents"]:
                    continue
                n = totals.get((source, row.id), 0)
                reason = ignores.get((source, row.id), "")
                if source == "payment" and row.source in {"movement", "wintouch"}:
                    reason = reason or ignores.get((row.source, row.source_id), "")
                item.update(
                    associated_cents=n,
                    available_cents=max(0, abs(item["amount_cents"]) - n),
                    ignore_reason=reason,
                    status="ignorado"
                    if reason
                    else "pago"
                    if source == "purchase"
                    and document_claims.get(row.id)
                    and n >= abs(item["amount_cents"])
                    else "reconciliado"
                    if n >= abs(item["amount_cents"])
                    else "parcial"
                    if n
                    else "pendente",
                )
                (banks if source == "bank" else internals).append(item)
        available = [i for i in internals if i["available_cents"] and i["status"] != "ignorado"]
        suggestions = []
        for bank in banks:
            if bank["available_cents"] and bank["status"] != "ignorado":
                matches = [s for i in available if (s := suggestion(bank, i))]
                suggestions.extend(sorted(matches, key=lambda s: -s["score"])[:5])
        details = {}
        undo_reasons = {}
        for audit in db.scalars(
            select(Audit).where(
                Audit.store_id.in_(stores),
                Audit.action.in_(["reconciliacao_confirmada", "reconciliacao_desfeita"]),
            )
        ):
            if audit.action == "reconciliacao_confirmada":
                details[audit.target_id] = json.loads(audit.detail)
            else:
                undo_reasons[audit.target_id] = json.loads(audit.detail).get("reason", "")
        groups = []
        for r in db.scalars(
            select(Reconciliation)
            .where(Reconciliation.store_id.in_(stores))
            .order_by(Reconciliation.created_at.desc())
        ):
            groups.append(
                {
                    **serial(r),
                    "gross_cents": details.get(r.id, {}).get("gross_cents", 0),
                    "net_cents": details.get(r.id, {}).get("net_cents", 0),
                    "undo_reason": undo_reasons.get(r.id, ""),
                    "lines": [
                        serial(link)
                        for link in db.scalars(
                            select(ReconciliationLine).where(
                                ReconciliationLine.reconciliation_id == r.id
                            )
                        )
                    ],
                }
            )
        return {
            "banks": sorted(banks, key=lambda r: r["date"], reverse=True),
            "internals": sorted(internals, key=lambda r: r["date"], reverse=True),
            "suggestions": suggestions,
            "card_suggestions": card_suggestions(banks, internals),
            "groups": groups,
        }

    @app.post("/api/reconciliation/confirm")
    def confirm(data: Confirmation, user=Depends(current), db: Session = Depends(db_session)):
        keys = [(link.source, link.source_id) for link in data.lines]
        if len(keys) != len(set(keys)):
            raise HTTPException(422, "Selecione cada movimento apenas uma vez.")
        # All writers lock bank rows first, including the existing purchase allocation API.
        ordered = sorted(
            data.lines,
            key=lambda link: (0 if link.source == "bank" else 1, link.source, link.source_id),
        )
        items = {
            (link.source, link.source_id): get_source(db, user, link.source, link.source_id)
            for link in ordered
        }
        economic_keys = []
        for link in ordered:
            row = db.get(DocumentPayment, link.source_id) if link.source == "payment" else None
            economic_keys.append(
                (row.source, row.source_id)
                if row and row.source in {"movement", "wintouch"}
                else (link.source, link.source_id)
            )
        if len(set(economic_keys)) != len(economic_keys):
            raise HTTPException(409, "O mesmo pagamento de origem foi selecionado duas vezes.")
        stores = {i["store_id"] for i in items.values()}
        if len(stores) != 1:
            raise HTTPException(422, "Os movimentos devem pertencer à mesma loja.")
        for link in ordered:
            item = items[link.source, link.source_id]
            if ignored(db, link.source, link.source_id):
                raise HTTPException(409, "Reative os movimentos ignorados antes de os reconciliar.")
            if link.amount_cents > abs(item["amount_cents"]) - used(
                db, link.source, link.source_id
            ):
                raise HTTPException(
                    409, "O valor excede o montante disponível. Atualize os movimentos."
                )
        bank_lines = [link for link in ordered if link.source == "bank"]
        internal_lines = [link for link in ordered if link.source != "bank"]
        signature = sorted((link.source, link.source_id, link.amount_cents) for link in ordered)
        existing = db.scalars(
            select(Reconciliation.id)
            .join(ReconciliationLine)
            .where(
                Reconciliation.undone_at.is_(None),
                ReconciliationLine.source == "bank",
                ReconciliationLine.source_id.in_([link.source_id for link in bank_lines]),
            )
            .distinct()
        )
        for group_id in existing:
            previous = sorted(
                db.execute(
                    select(
                        ReconciliationLine.source,
                        ReconciliationLine.source_id,
                        ReconciliationLine.amount_cents,
                    ).where(ReconciliationLine.reconciliation_id == group_id)
                ).all()
            )
            if signature == previous:
                raise HTTPException(
                    409,
                    "Esta associação já está confirmada. Desfaça a reconciliação para corrigir os valores.",
                )
        bank_total = sum(
            link.amount_cents
            * (1 if items[link.source, link.source_id]["amount_cents"] > 0 else -1)
            for link in bank_lines
        )
        internal_total = sum(
            link.amount_cents
            * (1 if items[link.source, link.source_id]["amount_cents"] > 0 else -1)
            for link in internal_lines
        )
        if not bank_lines:
            raise HTTPException(422, "Selecione movimentos bancários.")
        if data.kind == "transferencia" and not internal_lines:
            if (
                len(bank_lines) < 2
                or bank_total != 0
                or data.commission_cents
                or data.difference_cents
            ):
                raise HTTPException(
                    422, "Uma transferência entre contas exige saída e entrada de igual valor."
                )
            accounts = {db.get(BankTransaction, link.source_id).account_id for link in bank_lines}
            if len(accounts) < 2:
                raise HTTPException(422, "Selecione contas diferentes para a transferência.")
        else:
            if not internal_lines:
                raise HTTPException(422, "Selecione movimentos internos ou faturas.")
            signs = {1 if i["amount_cents"] > 0 else -1 for i in items.values()}
            if len(signs) != 1:
                raise HTTPException(422, "Os pagamentos devem ter o mesmo sentido nos dois lados.")
            if bank_total != internal_total - data.commission_cents + data.difference_cents:
                raise HTTPException(
                    422, "O líquido bancário deve ser igual ao bruto menos comissão mais diferença."
                )
            if data.kind == "cartao" and (
                internal_total <= 0
                or any(
                    items[link.source, link.source_id]["payment"]
                    not in {"cartao", "multibanco", "tpa", "card"}
                    for link in internal_lines
                )
            ):
                raise HTTPException(422, "Selecione recebimentos de cartão para o agrupamento.")
            if data.kind == "transferencia" and any(
                not items[link.source, link.source_id]["transfer"] for link in internal_lines
            ):
                raise HTTPException(
                    422, "Selecione movimentos internos de depósito ou transferência."
                )
            if data.kind != "transferencia" and any(
                items[link.source, link.source_id]["transfer"] for link in internal_lines
            ):
                raise HTTPException(
                    422, "Depósitos e transferências devem ser tratados como transferências."
                )
        if (data.commission_cents or data.difference_cents or data.kind == "transferencia") and len(
            data.reason.strip()
        ) < 3:
            raise HTTPException(
                422, "Indique a justificação da comissão, diferença ou transferência."
            )
        group = Reconciliation(
            store_id=next(iter(stores)),
            kind=data.kind,
            commission_cents=data.commission_cents,
            difference_cents=data.difference_cents,
            reason=data.reason.strip(),
        )
        db.add(group)
        db.flush()
        for link in ordered:
            db.add(ReconciliationLine(reconciliation_id=group.id, **link.model_dump()))
        record(
            db,
            user,
            "reconciliacao_confirmada",
            group.id,
            {**data.model_dump(), "gross_cents": internal_total, "net_cents": bank_total},
            group.store_id,
        )
        db.commit()
        return {"id": group.id}

    @app.post("/api/reconciliation/{group_id}/undo")
    def undo(
        group_id: str, data: Justification, user=Depends(current), db: Session = Depends(db_session)
    ):
        # Acquire source locks before group lock, in the same order as confirmation.
        group = db.get(Reconciliation, group_id)
        if not group:
            raise HTTPException(404, "Reconciliação não encontrada.")
        authorize(user, db, group.store_id, ["administrador", "gestor"])
        lines = list(
            db.scalars(
                select(ReconciliationLine).where(ReconciliationLine.reconciliation_id == group_id)
            )
        )
        for link in sorted(
            lines, key=lambda link: (0 if link.source == "bank" else 1, link.source, link.source_id)
        ):
            db.scalar(
                select(MODELS[link.source])
                .where(MODELS[link.source].id == link.source_id)
                .with_for_update()
            )
        group = db.scalar(
            select(Reconciliation)
            .where(Reconciliation.id == group_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if group.undone_at:
            raise HTTPException(409, "Esta reconciliação já foi desfeita.")
        if len(data.reason.strip()) < 3:
            raise HTTPException(422, "Indique a justificação.")
        group.undone_at = now()
        record(
            db,
            user,
            "reconciliacao_desfeita",
            group.id,
            {"reason": data.reason.strip()},
            group.store_id,
        )
        db.commit()
        return {"ok": True}

    @app.post("/api/reconciliation/ignore/{source}/{source_id}")
    def ignore(
        source: Source,
        source_id: str,
        data: Justification,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        item = get_source(db, user, source, source_id)
        if used(db, source, source_id):
            raise HTTPException(409, "Desfaça as associações antes de ignorar o movimento.")
        if len(data.reason.strip()) < 3:
            raise HTTPException(422, "Indique a justificação.")
        row = ignored(db, source, source_id)
        if row:
            raise HTTPException(409, "O movimento já está ignorado.")
        db.add(ReconciliationIgnore(source=source, source_id=source_id, reason=data.reason.strip()))
        record(
            db,
            user,
            "movimento_ignorado_reconciliacao",
            source_id,
            {"source": source, "reason": data.reason.strip()},
            item["store_id"],
        )
        db.commit()
        return {"ok": True}

    @app.post("/api/reconciliation/restore/{source}/{source_id}")
    def restore(
        source: Source, source_id: str, user=Depends(current), db: Session = Depends(db_session)
    ):
        item = get_source(db, user, source, source_id)
        row = ignored(db, source, source_id)
        if not row:
            raise HTTPException(404, "O movimento não está ignorado.")
        record(
            db,
            user,
            "movimento_reativado_reconciliacao",
            source_id,
            {"source": source, "previous_reason": row.reason},
            item["store_id"],
        )
        db.delete(row)
        db.commit()
        return {"ok": True}
