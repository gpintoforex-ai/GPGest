"""Explicit reconciliation only: never creates revenue or expense movements."""

import json
import re
import unicodedata
from functools import lru_cache
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
    BankBookEntry,
    BankDeposit,
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
    User,
    now,
)
from .schemas import Strict

Source = Literal["bank", "purchase", "movement", "wintouch", "payment", "book"]
MODELS = {
    "bank": BankTransaction,
    "book": BankBookEntry,
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
    post_commission: bool = False


class Justification(Strict):
    reason: str = Field(min_length=3, max_length=2000)


class ProposalDecision(Justification):
    proposal_id: str = Field(min_length=64, max_length=64)


def used(db, source, source_id):
    if source == "payment":
        payment = db.get(DocumentPayment, source_id)
        if payment and payment.source in {"movement", "wintouch", "book"}:
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
    if source == "movement":
        deposit = db.scalar(select(BankDeposit).where(BankDeposit.movement_id == source_id))
        if deposit:
            total += int((db.scalar(select(func.sum(DepositCredit.amount)).where(DepositCredit.deposit_id == deposit.id, DepositCredit.kind == "credito", DepositCredit.undone_at.is_(None))) or 0) * 100)
    if source in {"movement", "wintouch", "book"}:
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
    if payment.source == "book":
        account=db.get(BankAccount,row.account_id)
        return bool(account and (account.store_id,abs(row.amount_cents),1 if row.amount_cents>0 else -1,row.movement_date)==
            (payment.store_id,payment.amount_cents,payment.direction,payment.payment_date))
    if payment.source == "movement":
        if (
            row.reversed_by
            or row.reversal_of
            or row.transfer_id
            or row.kind in {"deposito", "reforco", "transferencia_caixa"}
        ):
            return False
        session = db.get(CashSession, row.session_id) if row.session_id else None
        account=db.get(BankAccount,row.bank_account_id) if row.bank_account_id else None
        if not session and not account:return False
        store = account.store_id if account else db.get(Register, session.register_id).store_id
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


# Palavras que não identificam uma entidade: formas jurídicas, termos genéricos e processadores de pagamento.
GENERIC_TOKENS = {
    "lda", "limitada", "unipessoal", "sociedade", "comercio", "comercial", "produtos", "portugal",
    "grupo", "group", "servicos", "service", "distribuicao", "import", "export", "industria",
    "farmacia", "parafarmacia", "laboratorio", "laboratorios", "natural", "naturais", "saude",
    "internacional", "nacional", "cliente", "fornecedor", "consumidor", "final",
    "sepa", "pagserv", "ifthenpay", "hipay", "easypay", "eupago", "paypal", "stripe", "sibs",
    "multibanco", "transferencia", "pagamento", "compra", "debito", "credito",
    "trf", "the", "dos", "das", "and",
}


# Textos curtos (descrições, entidades, referências) repetidos em cada par movimento/registo.
short_normalized = lru_cache(maxsize=50000)(normalized)


@lru_cache(maxsize=50000)
def words_of(text):
    return frozenset(text.split())


@lru_cache(maxsize=20000)
def iso_day(value):
    return date.fromisoformat(value)


@lru_cache(maxsize=20000)
def entity_tokens(value):
    words = short_normalized(value or "").split()
    tokens = {t for t in words if len(t) >= 4 and t not in GENERIC_TOKENS}
    # Marcas curtas no início do nome (MEO, EDP, NOS, CTT) também identificam a entidade.
    if words and len(words[0]) == 3 and words[0].isalpha() and words[0] not in GENERIC_TOKENS:
        tokens.add(words[0])
    return frozenset(tokens)


def day_label(value):
    return "/".join(reversed(value.split("-")))


def suggestion(bank, internal, tolerance=10, named=frozenset(), amount_tolerance=0, supplier_rule=None):
    """`named`: palavras de entidades conhecidas que a descrição bancária contém. Quando a descrição
    refere uma entidade e o registo é de outra, não há proposta (salvo referência do documento).
    `amount_tolerance`: diferença (cêntimos) tratada como o mesmo valor (comissões, arredondamentos), configurada
    nas regras da conta; o valor proposto é sempre o menor dos dois, nunca se considera pago o que não foi."""
    if (
        bank["store_id"] != internal["store_id"]
        or bank["amount_cents"] * internal["amount_cents"] <= 0
        or bank.get("currency") and internal.get("currency") and bank["currency"]!=internal["currency"]
        or internal.get("account_id") and bank.get("account_id") and bank["account_id"]!=internal["account_id"]
    ):
        return None
    reasons, score = [], 0
    difference = abs(bank["available_cents"] - internal["available_cents"])
    within = 0 < difference <= amount_tolerance
    if bank["available_cents"] == internal["available_cents"]:
        score += 45
        reasons.append("Montante disponível igual")
    elif within:
        score += 35
        reasons.append(f"Diferença de {difference / 100:.2f} € dentro da tolerância da conta".replace(".", ","))
    elif bank["available_cents"] < internal["available_cents"]:
        score += 10
        reasons.append("Possível pagamento parcial ou comissão")
    elapsed = (iso_day(bank["date"]) - iso_day(internal["date"])).days
    days = abs(elapsed)
    # Exclusão rápida antes de comparar textos (a consulta avalia todos os pares movimento/registo).
    late = days > tolerance
    if late and not (internal["source"] == "purchase" and 0 < elapsed <= 366
                     and (bank["available_cents"] == internal["available_cents"] or within)):
        return None
    description = short_normalized(bank["description"] + " " + bank.get("reference", ""))
    reference = short_normalized(internal["reference"] or "")
    entity = short_normalized(internal["entity"] or "")
    referenced = len(reference) >= 4 and reference in description
    identified = len(entity) >= 4 and entity in description or bool(entity_tokens(entity) & words_of(description))
    if late:
        # Faturas de fornecedor pagas mais tarde: fora da tolerância só com valor igual, entidade ou
        # referência na descrição e pagamento depois da fatura (até um ano).
        if not (referenced or identified):
            return None
        if elapsed <= 45:
            score += 10  # Débito direto ou pagamento a 30 dias: a fatura mais recente é a provável.
        reasons.append(f"Fatura de {day_label(internal['date'])}, paga {elapsed} dias depois")
    elif days <= 3:
        score += 20
        reasons.append(f"Datas próximas ({days} dias)")
    elif days <= 10:
        score += 10
        reasons.append(f"Datas a {days} dias")
    if referenced:
        score += 25
        reasons.append("Referência encontrada na descrição bancária")
    if identified:
        score += 20
        reasons.append("Entidade encontrada na descrição bancária")
    elif named and not referenced:
        return None
    # Valor diferente só com datas próximas não identifica nada: exige referência ou entidade.
    if bank["available_cents"] != internal["available_cents"] and not within and not referenced and not identified:
        return None
    if supplier_rule:
        reasons.append(f"Regra do fornecedor «{supplier_rule}»")
    tokens = {t for t in short_normalized(internal["description"] or "").split() if len(t) >= 4}
    if tokens & words_of(description):
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


def card_suggestions(banks, internals, rules=None):
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
            batch=[i for i in batch if (not i.get('account_id') or i.get('account_id')==bank.get('account_id')) and (not i.get('currency') or not bank.get('currency') or i['currency']==bank['currency'])]
            days = (date.fromisoformat(bank["date"]) - date.fromisoformat(day)).days
            tolerance = (rules or {}).get(bank.get("account_id"), {}).get("date_tolerance_days", 10)
            if store != bank["store_id"] or not 0 <= days <= tolerance or not 2 <= len(batch) <= 199:
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
        account = None
        projected = None
        if source == "bank":
            account = (
                context["accounts"][row.account_id]
                if context
                else db.get(BankAccount, row.account_id)
            )
            store, amount, day = account.store_id, row.amount_cents, row.movement_date
            description, entity, reference, payment = row.description, "", row.reference or "", ""
        elif source == "book":
            reversed_books = (context or {}).get("reversed_books")
            if row.original_id or (row.id in reversed_books if reversed_books is not None else
                                   db.scalar(select(BankBookEntry.id).where(BankBookEntry.original_id==row.id))):
                return None
            account = db.get(BankAccount, row.account_id)
            store, amount, day = account.store_id, row.amount_cents, row.movement_date
            description, entity, reference, payment = row.description, "", row.reference, "transferencia"
        elif source == "purchase":
            if row.origin == "efatura_testes":
                return None
            from .models import DocumentState, DocumentERPLink, ERPDocument
            treatments = (context or {}).get("document_states")
            if treatments is not None:
                treatment = treatments.get(row.id)
            else:
                state = db.get(DocumentState, row.id)
                treatment = state.treatment if state else None
            if treatment in {"anulado", "anulada", "cancelado", "cancelada"}:
                return None
            cancelled = (context or {}).get("cancelled_erp_purchases")
            if (row.id in cancelled) if cancelled is not None else db.scalar(
                    select(ERPDocument.id).join(DocumentERPLink, DocumentERPLink.erp_id == ERPDocument.id).where(
                    DocumentERPLink.purchase_id == row.id, DocumentERPLink.undone_at.is_(None), ERPDocument.cancelled.is_(True)).limit(1)):
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
            if row.source in {"movement", "wintouch", "book"}:
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
                # Depósito registado no banco: entrada na conta de destino, por reconciliar com o extrato.
                deposit = (context or {}).get("deposits", {}).get(row.id) if context else None
                if deposit is None:
                    deposit = db.scalar(select(BankDeposit).where(BankDeposit.movement_id == row.id))
                if not deposit or row.reversed_by or row.reversal_of or deposit.status not in {"entregue", "parcialmente_reconciliado", "reconciliado"}:
                    return None
                account = db.get(BankAccount, deposit.account_id)
                register = db.get(Register, deposit.register_id)
                return {
                    "id": row.id, "source": "movement", "store_id": deposit.store_id,
                    "amount_cents": int((deposit.cash_amount + deposit.cheque_amount) * 100),
                    "date": deposit.delivery_date or deposit.exit_date,
                    "description": f"Depósito {deposit.number}" + (f" · {register.name}" if register else ""),
                    "entity": "", "reference": deposit.number, "payment": row.payment, "transfer": True,
                    "account": account.name if account else "", "account_id": deposit.account_id,
                    "currency": account.currency if account else "EUR",
                    "origin_source": "movement", "origin_source_id": row.id,
                    "deposit_id": deposit.id,
                }
            if (
                row.reversed_by
                or row.reversal_of
                or (row.transfer_id and not row.bank_account_id)
                or (row.payment == "numerario" and row.kind not in {"deposito", "reforco"})
            ):
                return None
            session = (
                context["sessions"].get(row.session_id)
                if context
                else db.get(CashSession, row.session_id) if row.session_id else None
            )
            register = (context["registers"].get(session.register_id) if context else db.get(Register, session.register_id)) if session else None
            account = db.get(BankAccount, row.bank_account_id) if row.bank_account_id else None
            if not account and not register:
                return None
            store = account.store_id if account else register.store_id
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
            "transfer": (source == "movement" and (bool(row.transfer_id) or row.kind in {"deposito", "retirada_deposito", "reforco"})) or (source == "book" and bool(row.transfer_id)),
            "account": account.name if account else projected.get("account", "") if projected else "",
            "account_id": account.id if account else projected.get("account_id") if projected else None,
            "currency": account.currency if account else projected.get("currency") if projected else "EUR",
            "origin_source": row.source if source=="payment" else source,
            "origin_source_id": row.source_id if source=="payment" else row.id,

        }

    def get_source(db, user, source, source_id):
        if source == "payment":
            alias = db.get(DocumentPayment, source_id)
            if alias and alias.source in {"movement", "wintouch", "book"}:
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
        if source == "bank":
            from .reconciliation_center import assert_open
            assert_open(db, row.account_id, row.movement_date)
        return projection

    def ignored(db, source, source_id):
        result = db.scalar(
            select(ReconciliationIgnore).where(
                ReconciliationIgnore.source == source, ReconciliationIgnore.source_id == source_id
            )
        )
        if not result and source == "payment":
            alias = db.get(DocumentPayment, source_id)
            if alias and alias.source in {"movement", "wintouch", "book"}:
                return ignored(db, alias.source, alias.source_id)
        if not result and source in {"movement", "wintouch", "book"}:
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
        for bank_id,amount in db.execute(select(DepositCredit.transaction_id,func.sum(DepositCredit.amount)).where(DepositCredit.undone_at.is_(None)).group_by(DepositCredit.transaction_id)):
            totals['bank',bank_id]=totals.get(('bank',bank_id),0)+int(amount*100)
        # Créditos feitos no próprio ecrã de Depósitos também contam no movimento do depósito.
        for movement_id, amount in db.execute(
            select(BankDeposit.movement_id, func.sum(DepositCredit.amount))
            .join(DepositCredit, DepositCredit.deposit_id == BankDeposit.id)
            .where(DepositCredit.undone_at.is_(None), DepositCredit.kind == "credito", BankDeposit.movement_id.is_not(None))
            .group_by(BankDeposit.movement_id)
        ):
            totals["movement", movement_id] = totals.get(("movement", movement_id), 0) + int(amount * 100)
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
        # Lidos uma vez (antes: uma consulta por registo do livro bancário e por compra).
        from .models import DocumentERPLink, DocumentState, ERPDocument
        context["reversed_books"] = set(db.scalars(select(BankBookEntry.original_id).where(BankBookEntry.original_id.is_not(None))))
        context["document_states"] = dict(db.execute(select(DocumentState.purchase_id, DocumentState.treatment)).all())
        context["cancelled_erp_purchases"] = set(db.scalars(
            select(DocumentERPLink.purchase_id).join(ERPDocument, DocumentERPLink.erp_id == ERPDocument.id).where(
                DocumentERPLink.undone_at.is_(None), ERPDocument.cancelled.is_(True))))
        queries = {
            "bank": select(BankTransaction)
            .join(BankAccount)
            .where(BankAccount.store_id.in_(stores)),
            "book": select(BankBookEntry).join(BankAccount).where(BankAccount.store_id.in_(stores)),
            "purchase": select(Purchase).where(Purchase.store_id.in_(stores)),
            "movement": select(Movement)
            .outerjoin(CashSession)
            .outerjoin(Register)
            .where(Register.store_id.in_(stores) | Movement.bank_account_id.in_(select(BankAccount.id).where(BankAccount.store_id.in_(stores)))
                   | Movement.id.in_(select(BankDeposit.movement_id).where(BankDeposit.store_id.in_(stores), BankDeposit.movement_id.is_not(None)))),
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
                select(DocumentPayment).where(DocumentPayment.source.in_(["movement", "wintouch", "book"]))
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
                if source == "payment" and row.source in {"movement", "wintouch", "book"}:
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
        from .reconciliation_center import account_rules
        from .reconciliation_center import effective as rule_effective
        rules = {identifier: account_rules(account) for identifier, account in context["accounts"].items()}

        effective_cache = {}

        def effective_policy(policy, internal, named):
            # Uma vez por conta e registo (a consulta avalia todos os pares movimento × registo).
            key = (id(policy), internal["source"], internal["id"])
            if key not in effective_cache:
                effective_cache[key] = rule_effective(policy, internal)
            date_tolerance, amount_tolerance, supplier = effective_cache[key]
            return date_tolerance, named, amount_tolerance, supplier
        suggestions = []
        known = set().union(*(entity_tokens(i["entity"]) for i in available))
        named_by_bank = {}
        # suggestion() só propõe registos dentro da tolerância de datas ou faturas de compra até um ano antes com o
        # mesmo valor (ou dentro da tolerância de valor). Os índices por data e por valor evitam avaliar os restantes
        # pares (antes: todos os movimentos × todos os registos); os avaliados seguem a ordem original.
        from bisect import bisect_left, bisect_right
        ordinals = [iso_day(i["date"]).toordinal() for i in available]
        by_day = sorted(range(len(available)), key=ordinals.__getitem__)
        sorted_days = [ordinals[n] for n in by_day]
        purchase_index = sorted((n for n, i in enumerate(available) if i["source"] == "purchase"),
                                key=lambda n: available[n]["available_cents"])
        purchase_amounts = [available[n]["available_cents"] for n in purchase_index]
        limits = {}

        def window(policy):
            if not policy:
                return 10, 0  # Sem regras da conta: tolerância de datas de 10 dias e de valor nula.
            if id(policy) not in limits:
                values = [effective_policy(policy, i, frozenset()) for i in available]
                limits[id(policy)] = (max((v[0] for v in values), default=10), max((v[2] for v in values), default=0))
            return limits[id(policy)]

        for bank in banks:
            if bank["available_cents"] and bank["status"] != "ignorado":
                named = known & set(normalized(bank["description"] + " " + bank.get("reference", "")).split())
                # Fora do próprio movimento: o identificador das propostas (e as rejeições) depende dele.
                named_by_bank[bank["id"]] = named
                policy = rules.get(bank.get("account_id"), {})
                days, cents = window(policy)
                day, value = iso_day(bank["date"]).toordinal(), bank["available_cents"]
                picked = set(by_day[bisect_left(sorted_days, day - days):bisect_right(sorted_days, day + days)])
                picked.update(purchase_index[bisect_left(purchase_amounts, value - cents):bisect_right(purchase_amounts, value + cents)])
                matches = [s for n in sorted(picked) if (s := suggestion(bank, available[n], *effective_policy(policy, available[n], named)))
                           and s["score"] >= policy.get("min_score", 30)]
                suggestions.extend(sorted(matches, key=lambda s: -s["score"])[:5])
        details = {}
        undo_reasons = {}
        actors = {}
        undo_actors = {}
        for audit in db.scalars(
            select(Audit).where(
                Audit.store_id.in_(stores),
                Audit.action.in_(["reconciliacao_confirmada", "reconciliacao_desfeita"]),
            )
        ):
            if audit.action == "reconciliacao_confirmada":
                details[audit.target_id] = json.loads(audit.detail)
                actor=db.get(User,audit.user_id)
                actors[audit.target_id]={'id':audit.user_id,'name':actor.name if actor else 'Utilizador registado'}
            else:
                undo_reasons[audit.target_id] = json.loads(audit.detail).get("reason", "")
                actor=db.get(User,audit.user_id)
                undo_actors[audit.target_id]={'id':audit.user_id,'name':actor.name if actor else 'Utilizador registado'}
        groups = []
        group_rows = list(db.scalars(
            select(Reconciliation)
            .where(Reconciliation.store_id.in_(stores))
            .order_by(Reconciliation.created_at.desc())
        ))
        group_ids = [r.id for r in group_rows]
        group_lines, group_currency = {}, {}
        if group_ids:
            for link in db.scalars(select(ReconciliationLine).where(ReconciliationLine.reconciliation_id.in_(group_ids))):
                group_lines.setdefault(link.reconciliation_id, []).append(link)
            for group_id, currency in db.execute(
                    select(ReconciliationLine.reconciliation_id, BankAccount.currency)
                    .join(BankTransaction, ReconciliationLine.source_id == BankTransaction.id)
                    .join(BankAccount, BankTransaction.account_id == BankAccount.id)
                    .where(ReconciliationLine.reconciliation_id.in_(group_ids), ReconciliationLine.source == "bank")):
                group_currency.setdefault(group_id, currency)
        for r in group_rows:
            groups.append(
                {
                    **serial(r),
                    "currency": group_currency.get(r.id),
                    "gross_cents": details.get(r.id, {}).get("gross_cents", 0),
                    "net_cents": details.get(r.id, {}).get("net_cents", 0),
                    "undo_reason": undo_reasons.get(r.id, ""),
                    "created_by": actors.get(r.id),
                    "undone_by": undo_actors.get(r.id),
                    "lines": [serial(link) for link in group_lines.get(r.id, [])],
                }
            )
        from .reconciliation_matching import grouped, classify
        grouped_banks = [b for b in banks if b["available_cents"] and rules.get(b.get("account_id"), {}).get("grouped_proposals", True)]
        proposals, limited = grouped(grouped_banks, available, rules, named_by_bank)
        cards = [p for p in card_suggestions(banks, internals, rules) if any(b["id"] == p["bank_id"] and rules.get(b.get("account_id"), {}).get("grouped_proposals", True) for b in banks)]
        rejected = set(db.scalars(select(Audit.target_id).where(Audit.store_id.in_(stores), Audit.action == "reconciliacao_proposta_rejeitada")))
        suggestions, cards = classify(banks, internals, suggestions, [*cards, *proposals], rejected)
        from .bank_rules import suggestions as rule_suggestions
        return {
            # Movimentos sem documento (impostos, vencimentos…): tratamento proposto pelas regras por descrição.
            "rule_suggestions": rule_suggestions(db, user, banks),
            "banks": sorted(banks, key=lambda r: r["date"], reverse=True),
            "internals": sorted(internals, key=lambda r: r["date"], reverse=True),
            "suggestions": suggestions,
            "card_suggestions": cards,
            "group_search_limited": limited,
            "rules": rules,
            "groups": groups,
        }

    @app.post("/api/reconciliation/proposals/reject")
    def reject_proposal(data: ProposalDecision, user=Depends(current), db: Session = Depends(db_session)):
        if len(data.reason.strip()) < 3:
            raise HTTPException(422, "Indique o motivo da rejeição.")
        current_data = overview(user, db)
        proposal = next((p for p in [*current_data["suggestions"], *current_data["card_suggestions"]] if p["proposal_id"] == data.proposal_id), None)
        if not proposal:
            raise HTTPException(409, "A proposta mudou ou já foi rejeitada. Atualize a consulta.")
        bank = get_source(db, user, "bank", proposal["bank_id"])
        record(db, user, "reconciliacao_proposta_rejeitada", data.proposal_id,
            {"reason": data.reason.strip(), "bank_id": proposal["bank_id"]}, bank["store_id"])
        db.commit()
        return {"ok": True}

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
                if row and row.source in {"movement", "wintouch", "book"}
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
        from .reconciliation_center import assert_open
        for link in bank_lines:
            item = items[link.source, link.source_id]
            assert_open(db, item["account_id"], item["date"])
        internal_lines = [link for link in ordered if link.source != "bank"]
        if len({i.get("currency") for i in items.values() if i.get("currency")})>1:
            raise HTTPException(422,"Não pode reconciliar movimentos em moedas diferentes.")
        bank_accounts={items[link.source,link.source_id].get("account_id") for link in bank_lines}
        if any(items[link.source,link.source_id].get("account_id") and items[link.source,link.source_id]["account_id"] not in bank_accounts for link in internal_lines):
            raise HTTPException(422,"O registo da aplicação pertence a outra conta bancária.")
        if len(bank_accounts)>1 and not data.commission_cents and not data.difference_cents:
            for account_id in bank_accounts:
                bank_amount=sum(link.amount_cents for link in bank_lines if items[link.source,link.source_id].get('account_id')==account_id)
                app_amount=sum(link.amount_cents for link in internal_lines if items[link.source,link.source_id].get('account_id')==account_id)
                if app_amount>bank_amount:
                    raise HTTPException(422,'O valor atribuído à conta da aplicação excede o selecionado no seu extrato. Separe a associação por conta.')
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
        # Um depósito registado já identifica a transferência (talão); só a comissão ou diferença pedem justificação.
        deposits_only = bool(internal_lines) and all(items[link.source, link.source_id].get("deposit_id") for link in internal_lines)
        if (data.commission_cents or data.difference_cents or (data.kind == "transferencia" and not deposits_only)) and len(
            data.reason.strip()
        ) < 3:
            raise HTTPException(
                422, "Indique a justificação da comissão, diferença ou transferência."
            )
        if data.post_commission and (not data.commission_cents or len(bank_accounts)!=1):
            raise HTTPException(422,"O lançamento da comissão exige uma comissão positiva e uma única conta bancária.")
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
        if data.post_commission:
            account_id=next(iter(bank_accounts))
            stamp=max(db.get(BankTransaction,link.source_id).movement_date for link in bank_lines)
            fee=BankBookEntry(account_id=account_id,user_id=user.id,movement_date=stamp,amount_cents=-data.commission_cents,
                kind="fee",description="Comissão aprovada: "+data.reason[:970],reference=group.id,request_key="reconciliation-fee:"+group.id)
            db.add(fee);db.flush()
            db.add(DocumentPayment(store_id=group.store_id,payment_date=stamp,amount_cents=data.commission_cents,direction=-1,
                kind='comissao',method='transferencia',entity=fee.description[:300],reference=group.id,source='book',source_id=fee.id,request_key='fee-payment:'+fee.id))
            db.add(ReconciliationLine(reconciliation_id=group.id,source="book",source_id=fee.id,amount_cents=data.commission_cents))
            record(db,user,"banco_comissao_registada",fee.id,{"reconciliation_id":group.id,"amount_cents":fee.amount_cents},group.store_id)
        record(
            db,
            user,
            "reconciliacao_confirmada",
            group.id,
            {**data.model_dump(), "gross_cents": internal_total, "net_cents": bank_total},
            group.store_id,
        )
        db.flush()
        from .deposits import refresh_after_reconciliation
        refresh_after_reconciliation(db, [link.source_id for link in data.lines if link.source == "movement"])
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
            if link.source == "bank":
                from .reconciliation_center import assert_open
                bank = db.get(BankTransaction, link.source_id)
                assert_open(db, bank.account_id, bank.movement_date)
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
        db.flush()
        from .deposits import refresh_after_reconciliation
        refresh_after_reconciliation(db, [link.source_id for link in lines if link.source == "movement"])
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
