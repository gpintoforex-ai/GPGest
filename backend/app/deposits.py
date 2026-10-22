"""Company-local cash-to-bank transfers; statements remain the bank balance source."""

import io
import json
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, File, HTTPException, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import db as database
from .cash_sheet import daily_enterprise, day, is_real_cash_movement, payment_key, real_receipts
from .db import db_session
from .models import (
    AccountingPeriod,
    Audit,
    BankAccount,
    BankDeposit,
    BankTransaction,
    CashSession,
    Category,
    DepositAttachment,
    DepositCheque,
    DepositCredit,
    DocumentPayment,
    Movement,
    Reconciliation,
    ReconciliationLine,
    Register,
    Store,
    User,
    WintouchCashAccount,
    WintouchSaleRecord,
    now,
)
from .opening_fund import included_as_opening
from .platform_models import Company
from .schemas import Strict

Money = Decimal
DENOMS = [50000, 20000, 10000, 5000, 2000, 1000, 500, 200, 100, 50, 20, 10, 5, 2, 1]


def money(value):
    try:
        number = Decimal(str(value).replace(",", "."))
        if (
            not number.is_finite()
            or number < 0
            or number > Decimal("9999999999.99")
            or number != number.quantize(Decimal(".01"))
        ):
            raise ValueError()
        return number.quantize(Decimal(".01"))
    except Exception:
        raise HTTPException(
            422, "Indique um montante válido com até duas casas decimais."
        ) from None


def cents(value):
    return int(value * 100)


def euros(value):
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " €"


def cash_origin_resolver(db):
    accounts = {
        (a.enterprise_id, a.external_id): a.register_id
        for a in db.scalars(select(WintouchCashAccount))
    }
    stores = {}
    register_stores = {}
    for register in db.scalars(select(Register)):
        stores.setdefault(register.store_id, []).append(register.id)
        register_stores[register.id] = register.store_id
    owners = {
        (
            register_stores[sheet.register_id],
            sheet.wintouch_enterprise,
            sheet.sheet_day or day(sheet.opened_at),
        ): (sheet.register_id, sheet.id)
        for sheet in db.scalars(select(CashSession).where(CashSession.wintouch_scope.is_not(None)))
    }

    def resolve(store_id, enterprise, account_id, stamp):
        mapped = accounts.get((enterprise, account_id))
        owner = owners.get((store_id, enterprise, stamp))
        if mapped:
            return (
                (mapped, owner[1] if owner and owner[0] == mapped else None)
                if register_stores.get(mapped) == store_id
                else (None, None)
            )
        if owner:
            return owner
        candidates = stores.get(store_id, [])
        return (candidates[0], None) if len(candidates) == 1 else (None, None)

    return resolve


class ChequeInput(Strict):
    number: str = Field(min_length=1, max_length=100)
    bank: str = Field(min_length=1, max_length=100)
    amount: str
    cheque_date: date
    issuer: str = Field(default="", max_length=200)
    receipt_id: str | None = None
    source_receipt: str | None = Field(default=None, max_length=200)
    notes: str = Field(default="", max_length=2000)


class DepositInput(Strict):
    from_real_movements: bool = False
    use_cash_amount: bool = False
    register_id: str
    account_id: str
    preparation_date: date
    cash_mode: Literal["total", "denominations"] = "total"
    cash_amount: str = "0"
    counts: dict[str, int] = Field(default_factory=dict)
    cheques: list[ChequeInput] = Field(default_factory=list, max_length=100)
    reference: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    request_key: str = Field(min_length=10, max_length=100)


class Action(Strict):
    reason: str = Field(default="", max_length=2000)
    action_date: date = Field(
        default_factory=lambda: datetime.now(ZoneInfo("Europe/Lisbon")).date()
    )
    returned: bool = False
    allow_insufficient: bool = False
    transaction_id: str | None = None
    cheque_id: str | None = None
    amount: str = "0"
    request_key: str = Field(min_length=10, max_length=100)


def unlocked(db, dates):
    for stamp in filter(None, dates):
        period = db.scalar(
            select(AccountingPeriod)
            .where(
                AccountingPeriod.closed_at.is_not(None),
                AccountingPeriod.start <= stamp,
                AccountingPeriod.end >= stamp,
            )
            .with_for_update()
        )
        if period:
            raise HTTPException(
                409, "O período contabilístico está encerrado. Reabra-o antes desta operação."
            )


def general_reconciled_cents(db, movement_id):
    """Montante do depósito já associado ao extrato na reconciliação geral (Bancos › Reconciliação)."""
    if not movement_id:
        return 0
    return int(
        db.scalar(
            select(func.sum(ReconciliationLine.amount_cents))
            .join(Reconciliation, Reconciliation.id == ReconciliationLine.reconciliation_id)
            .where(
                Reconciliation.undone_at.is_(None),
                ReconciliationLine.source == "movement",
                ReconciliationLine.source_id == movement_id,
            )
        )
        or 0
    )


def credit_total(db, deposit):
    module = db.scalar(
        select(func.sum(DepositCredit.amount)).where(
            DepositCredit.deposit_id == deposit.id,
            DepositCredit.kind == "credito",
            DepositCredit.undone_at.is_(None),
        )
    ) or Decimal(0)
    return module + Decimal(general_reconciled_cents(db, deposit.movement_id)) / 100


def refresh_after_reconciliation(db, movement_ids):
    """Atualiza o estado dos depósitos cujos movimentos foram associados/desassociados em Bancos."""
    for movement_id in set(movement_ids):
        deposit = db.scalar(select(BankDeposit).where(BankDeposit.movement_id == movement_id))
        if deposit and deposit.status in {"entregue", "parcialmente_reconciliado", "reconciliado"}:
            state_after_credit(db, deposit)


def state_after_credit(db, deposit):
    matched = credit_total(db, deposit)
    deposit.status = (
        "reconciliado"
        if matched == deposit.cash_amount + deposit.cheque_amount
        else "parcialmente_reconciliado"
        if matched
        else "entregue"
    )


def projection(db, deposit):
    account = db.get(BankAccount, deposit.account_id)
    register = db.get(Register, deposit.register_id)
    total = deposit.cash_amount + deposit.cheque_amount
    matched = credit_total(db, deposit)
    fees = db.scalar(
        select(func.sum(DepositCredit.amount)).where(
            DepositCredit.deposit_id == deposit.id,
            DepositCredit.kind == "comissao",
            DepositCredit.undone_at.is_(None),
        )
    ) or Decimal(0)
    returned = db.scalar(
        select(func.sum(DepositCredit.amount)).where(
            DepositCredit.deposit_id == deposit.id,
            DepositCredit.kind == "devolucao",
            DepositCredit.undone_at.is_(None),
        )
    ) or Decimal(0)
    attachments = list(
        db.scalars(select(DepositAttachment).where(DepositAttachment.deposit_id == deposit.id))
    )
    from .document_scanning import deposit_scans
    cloud_scans = deposit_scans(db, deposit)
    result = {c.name: getattr(deposit, c.name) for c in deposit.__table__.columns}
    result.update(
        total=str(total),
        cash_amount=f"{deposit.cash_amount:.2f}",
        cheque_amount=f"{deposit.cheque_amount:.2f}",
        matched=str(matched),
        fees=str(fees),
        returned_amount=str(returned),
        bank_net=str(matched - fees - returned),
        remaining=str(total - matched),
        difference=str(matched - total),
        type="misto"
        if deposit.cash_amount and deposit.cheque_amount
        else "numerario"
        if deposit.cash_amount
        else "cheques",
        register_name=register.name,
        account_name=account.name,
        bank=account.bank,
        iban_last4=account.iban_last4,
        responsible=db.get(User, deposit.user_id).name,
        store_name=db.get(Store, deposit.store_id).name,
        details=json.loads(deposit.details),
        proof_status="validado"
        if any(a.validated_by for a in attachments) or any(a.validation_status == "validado" for a in cloud_scans)
        else "por_validar"
        if attachments or cloud_scans
        else "em_falta",
        attachments=[
            {"id": a.id, "name": a.name, "mime": a.mime, "validated": bool(a.validated_by)}
            for a in attachments
        ] + [{"id": "scan:"+a.id, "scan_id": a.id, "name": a.name, "mime": a.mime, "validated": a.validation_status == "validado", "view_url": "/api/scans/"+a.id+"/pdf"} for a in cloud_scans],
        cheques=[
            {
                **{c.name: getattr(row, c.name) for c in row.__table__.columns},
                "amount": str(row.amount),
            }
            for row in db.scalars(
                select(DepositCheque).where(DepositCheque.deposit_id == deposit.id)
            )
        ],
        credits=[
            {
                **{c.name: getattr(row, c.name) for c in row.__table__.columns},
                "amount": str(row.amount),
            }
            for row in db.scalars(
                select(DepositCredit).where(DepositCredit.deposit_id == deposit.id)
            )
        ],
        history=[
            {
                "action": row.action,
                "date": row.created_at,
                "user": db.get(User, row.user_id).name,
                "detail": json.loads(row.detail),
            }
            for row in db.scalars(
                select(Audit).where(Audit.target_id == deposit.id).order_by(Audit.created_at)
            )
        ],
    )
    return jsonable_encoder(result)


def deposit_pdf(db, deposit):
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, invariant=1)
    pdf.setTitle("Talão interno de depósito " + deposit.number)
    y = 790

    def line(value, bold=False):
        nonlocal y
        for segment in simpleSplit(str(value), "Helvetica-Bold" if bold else "Helvetica", 11, 490):
            if y < 65:
                pdf.showPage()
                y = 790
            pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 11)
            pdf.drawString(50, y, segment)
            y -= 19

    info = projection(db, deposit)
    company = db.info.get("company", {})
    line(company.get("name", "IAZELA"), True)
    line("NIF: " + str(company.get("nif") or "Não indicado"))
    line("Talão interno de depósito", True)
    line("Documento interno. Não é um comprovativo emitido pelo banco.")
    line("Número: " + deposit.number + " · Estado: " + deposit.status)
    line("Estabelecimento: " + info["store_name"] + " · Caixa: " + info["register_name"])
    line(
        "Destino: "
        + info["bank"]
        + " · "
        + info["account_name"]
        + " · IBAN final "
        + info["iban_last4"]
    )
    for label, value in [
        ("Preparação", deposit.preparation_date),
        ("Saída", deposit.exit_date),
        ("Entrega ao banco", deposit.delivery_date),
    ]:
        line(label + ": " + ("/".join(reversed(value.split("-"))) if value else "Por registar"))
    line("Responsável: " + info["responsible"])
    counts = info["details"].get("counts", {})
    for denomination in DENOMS:
        quantity = counts.get(str(denomination), 0)
        if quantity:
            line(
                f"{quantity} × {euros(Decimal(denomination) / 100)} = {euros(Decimal(denomination * quantity) / 100)}"
            )
    line("Numerário: " + euros(deposit.cash_amount), True)
    if info["details"].get("cash_mode") == "real_movements":
        line(
            "Folha de caixa de origem: "
            + info["register_name"]
            + " · "
            + deposit.preparation_date
            + " · "
            + ", ".join(info["details"].get("session_ids", []) or ["Movimentos reais do ERP"])
        )
        line("Movimentos reais que fundamentam o numerário:", True)
        for source in info["details"].get("sources", []):
            line(
                f"{source['date']} · {source['document']} · {euros(Decimal(source['amount']))} · {source['source']}:{source['id']}"
            )
    for cheque in info["cheques"]:
        if info["details"].get("cash_mode") == "real_movements":
            line(
                f"Recebimento por cheque: {cheque['number']} · {euros(Decimal(cheque['amount']))} · {cheque['source_receipt'] or cheque['receipt_id']}"
            )
            line(cheque["notes"])
            continue
        line(
            f"Cheque {cheque['number']} · {cheque['bank']} · {cheque['cheque_date']} · {cheque['issuer']} · {euros(Decimal(cheque['amount']))}"
        )
    line("Cheques: " + euros(deposit.cheque_amount))
    line("Total: " + euros(deposit.cash_amount + deposit.cheque_amount), True)
    line("Referência bancária: " + deposit.reference)
    line("Observações: " + deposit.notes)
    line("Assinatura de quem entrega: __________________________")
    line("Assinatura de quem recebe: __________________________")
    pdf.save()
    return stream.getvalue()


def period_condition(start, end):
    return (
        ((BankDeposit.preparation_date >= start) & (BankDeposit.preparation_date <= end))
        | ((BankDeposit.exit_date >= start) & (BankDeposit.exit_date <= end))
        | ((BankDeposit.delivery_date >= start) & (BankDeposit.delivery_date <= end))
        | select(DepositCredit.id)
        .join(BankTransaction, BankTransaction.id == DepositCredit.transaction_id)
        .where(
            DepositCredit.deposit_id == BankDeposit.id,
            BankTransaction.movement_date >= start,
            BankTransaction.movement_date <= end,
        )
        .exists()
    )


def dossier_files(db, start, end, store_ids=None):
    deposits = list(
        db.scalars(
            select(BankDeposit).where(period_condition(start, end), *([BankDeposit.store_id.in_(store_ids)] if store_ids is not None else [])).order_by(BankDeposit.number)
        )
    )
    files = {
        "depositos/indice.json": json.dumps(
            [projection(db, d) for d in deposits], ensure_ascii=False, indent=2
        ).encode()
    }
    missing = []
    for deposit in deposits:
        folder = "depositos/" + deposit.id + "/"
        files[folder + "talao-interno.pdf"] = deposit_pdf(db, deposit)
        from .document_scanning import deposit_scans
        proof = any(scan.validation_status == "validado" for scan in deposit_scans(db, deposit))
        for attachment in db.scalars(
            select(DepositAttachment).where(DepositAttachment.deposit_id == deposit.id)
        ):
            ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}[
                attachment.mime
            ]
            files[folder + attachment.id + ext] = attachment.data
            proof = proof or bool(attachment.validated_by)
        if not proof and deposit.status != "cancelado":
            missing.append(
                {
                    "id": deposit.id,
                    "numero": deposit.number,
                    "pendencia": "Comprovativo bancário em falta ou por validar",
                }
            )
        if deposit.status in {"entregue", "parcialmente_reconciliado"}:
            missing.append(
                {
                    "id": deposit.id,
                    "numero": deposit.number,
                    "pendencia": "Crédito bancário por reconciliar",
                    "montante": str(
                        deposit.cash_amount + deposit.cheque_amount - credit_total(db, deposit)
                    ),
                }
            )
    files["depositos/pendencias.json"] = json.dumps(missing, ensure_ascii=False, indent=2).encode()
    return files


def register_routes(app, current, authorize, scope, record):
    def get(db, user, deposit_id, write=False):
        deposit = db.scalar(
            select(BankDeposit)
            .where(BankDeposit.id == deposit_id, BankDeposit.store_id.in_(scope(user, db)))
            .with_for_update()
        )
        if not deposit:
            raise HTTPException(404, "Depósito não encontrado nesta empresa ou estabelecimento.")
        authorize(
            user, db, deposit.store_id, ["administrador", "gestor", "operador"] if write else None
        )
        return deposit

    def audit(db, user, deposit, action, detail):
        record(db, user, "deposito_" + action, deposit.id, detail, deposit.store_id)

    def save(db, user, data, deposit=None):
        register = db.get(Register, data.register_id)
        account = db.get(BankAccount, data.account_id)
        if not register or not account or account.store_id != register.store_id:
            raise HTTPException(422, "Selecione uma caixa e uma conta do mesmo estabelecimento.")
        authorize(user, db, register.store_id, ["administrador", "gestor", "operador"])
        unlocked(db, [data.preparation_date.isoformat()])
        db.scalar(select(Register).order_by(Register.id).limit(1).with_for_update())
        db.scalar(select(Register).where(Register.id == register.id).with_for_update())
        if deposit is None:
            previous = db.scalar(
                select(BankDeposit).where(BankDeposit.request_key == data.request_key)
            )
            if previous:
                return get(db, user, previous.id)
        origins = None
        if data.from_real_movements:
            origins = accumulated_sources(
                db, register, data.preparation_date.isoformat(), deposit.id if deposit else None
            )
            data = data.model_copy(
                update={
                    "cash_mode": "total",
                    "cash_amount": data.cash_amount if data.use_cash_amount else origins["cash_amount"],
                    "counts": {},
                    "cheques": [ChequeInput(**row) for row in origins["cheques"]],
                }
            )
        amount = money(data.cash_amount)
        if origins and amount > money(origins["cash_amount"]):
            raise HTTPException(422, "O numerário do depósito não pode exceder o saldo acumulado disponível.")
        if data.cash_mode == "denominations":
            if any(
                not key.isdigit() or int(key) not in DENOMS or quantity < 0 or quantity > 100000
                for key, quantity in data.counts.items()
            ):
                raise HTTPException(422, "Contagem de numerário inválida.")
            amount = (
                Decimal(sum(int(key) * quantity for key, quantity in data.counts.items())) / 100
            )
        if amount + sum((money(c.amount) for c in data.cheques), Decimal(0)) <= 0:
            raise HTTPException(422, "O depósito deve ter um valor superior a zero.")
        if deposit is None:
            # Register lock serializes creation and internal numbering across this company.
            db.scalar(select(Register).order_by(Register.id).limit(1).with_for_update())
            old = db.scalar(select(BankDeposit).where(BankDeposit.request_key == data.request_key))
            if old:
                return get(db, user, old.id)
            number = int(db.scalar(select(func.count()).select_from(BankDeposit)) or 0) + 1
            deposit = BankDeposit(
                number=f"DEP-{number:06d}",
                request_key=data.request_key,
                user_id=user.id,
                store_id=register.store_id,
            )
            db.add(deposit)
        elif deposit.status != "rascunho" and not (
            data.from_real_movements and deposit.status == "preparado"
        ):
            raise HTTPException(409, "Só pode editar depósitos em rascunho.")
        deposit.status = "rascunho"
        deposit.register_id, deposit.account_id = register.id, account.id
        deposit.preparation_date, deposit.cash_amount = data.preparation_date.isoformat(), amount
        deposit.reference, deposit.notes = data.reference.strip(), data.notes.strip()
        deposit.details = json.dumps(
            {
                "cash_mode": data.cash_mode,
                "counts": data.counts if data.cash_mode == "denominations" else {},
            }
        )
        if origins:
            deposit.details = json.dumps(
                {
                    "cash_mode": "real_movements",
                    "counts": {},
                    "session_id": origins["session_id"],
                    "session_ids": origins["session_ids"],
                    "cutoff": data.preparation_date.isoformat(),
                    "sources": origins["movements"],
                },
                ensure_ascii=False,
            )
        deposit.cheque_amount = sum((money(c.amount) for c in data.cheques), Decimal(0))
        db.flush()
        for previous in db.scalars(
            select(DepositCheque).where(
                DepositCheque.deposit_id == deposit.id, DepositCheque.active.is_(True)
            )
        ):
            db.delete(previous)
        db.flush()
        seen = set()
        for cheque in data.cheques:
            identity = cheque.source_receipt or (
                "payment:" + cheque.receipt_id
                if cheque.receipt_id
                else cheque.bank.strip().casefold() + ":" + cheque.number.strip().casefold()
            )
            if not cheque.bank.strip() or not cheque.number.strip() or money(cheque.amount) <= 0:
                raise HTTPException(422, "Indique banco, número e valor positivo de cada cheque.")
            if identity in seen or db.scalar(
                select(DepositCheque.id).where(
                    DepositCheque.identity == identity, DepositCheque.active.is_(True)
                )
            ):
                raise HTTPException(409, "Este cheque já está incluído num depósito ativo.")
            seen.add(identity)
            if cheque.receipt_id:
                receipt = db.get(DocumentPayment, cheque.receipt_id)
                if (
                    not receipt
                    or receipt.store_id != register.store_id
                    or receipt.method != "cheque"
                    or receipt.direction != 1
                    or receipt.amount_cents != cents(money(cheque.amount))
                ):
                    raise HTTPException(
                        422,
                        "O recebimento original não corresponde ao cheque deste estabelecimento.",
                    )
                if db.scalar(
                    select(DepositCheque.id).where(
                        DepositCheque.receipt_id == receipt.id, DepositCheque.active.is_(True)
                    )
                ):
                    raise HTTPException(409, "O recebimento já está reservado noutro depósito.")
            if cheque.source_receipt:
                source, separator, source_id = cheque.source_receipt.partition(":")
                receipt = (
                    db.get(WintouchSaleRecord, source_id)
                    if source == "wintouch" and separator
                    else None
                )
                received = json.loads(receipt.data) if receipt else {}
                if (
                    not receipt
                    or not receipt.active
                    or receipt.kind != "real"
                    or not is_real_cash_movement(received)
                    or receipt.store_id != register.store_id
                    or received.get("payment_code") != "CH"
                    or received.get("direction") != 1
                    or received.get("amount_cents") != cents(money(cheque.amount))
                ):
                    raise HTTPException(
                        422, "O recebimento ERP não corresponde ao cheque deste estabelecimento."
                    )
                if db.scalar(
                    select(DepositCheque.id).where(
                        DepositCheque.source_receipt == cheque.source_receipt,
                        DepositCheque.active.is_(True),
                    )
                ):
                    raise HTTPException(409, "O recebimento ERP já está reservado noutro depósito.")
            db.add(
                DepositCheque(
                    deposit_id=deposit.id,
                    identity=identity,
                    number=cheque.number.strip(),
                    bank=cheque.bank.strip(),
                    amount=money(cheque.amount),
                    cheque_date=cheque.cheque_date.isoformat(),
                    issuer=cheque.issuer,
                    receipt_id=cheque.receipt_id,
                    source_receipt=cheque.source_receipt,
                    notes=cheque.notes,
                )
            )
        db.flush()
        audit(db, user, deposit, "guardado", {"total": str(amount + deposit.cheque_amount)})
        return deposit

    @app.get("/api/deposits")
    def listing(user=Depends(current), db: Session = Depends(db_session)):
        return [
            projection(db, d)
            for d in db.scalars(
                select(BankDeposit)
                .where(BankDeposit.store_id.in_(scope(user, db)))
                .order_by(BankDeposit.created_at.desc())
            )
        ]

    @app.get("/api/deposits/cheque-receipts")
    def cheque_receipts(user=Depends(current), db: Session = Depends(db_session)):
        reserved = select(DepositCheque.receipt_id).where(
            DepositCheque.active.is_(True), DepositCheque.receipt_id.is_not(None)
        )
        result = [
            {
                "id": p.id,
                "store_id": p.store_id,
                "reference": p.reference,
                "entity": p.entity,
                "amount": str(Decimal(p.amount_cents) / 100),
                "date": p.payment_date,
            }
            for p in db.scalars(
                select(DocumentPayment).where(
                    DocumentPayment.store_id.in_(scope(user, db)),
                    DocumentPayment.method == "cheque",
                    DocumentPayment.direction == 1,
                    DocumentPayment.id.not_in(reserved),
                )
            )
        ]
        claimed = set(
            db.scalars(select(DepositCheque.source_receipt).where(DepositCheque.active.is_(True)))
        )
        for receipt in db.scalars(
            select(WintouchSaleRecord).where(
                WintouchSaleRecord.store_id.in_(scope(user, db)),
                WintouchSaleRecord.active.is_(True),
                WintouchSaleRecord.kind == "real",
            )
        ):
            data = json.loads(receipt.data)
            if not is_real_cash_movement(data):
                continue
            source_key = "wintouch:" + receipt.id
            if (
                source_key not in claimed
                and data.get("payment_code") == "CH"
                and data.get("direction") == 1
            ):
                result.append(
                    {
                        "id": source_key,
                        "source_receipt": source_key,
                        "store_id": receipt.store_id,
                        "reference": data.get("document", ""),
                        "entity": data.get("entity", ""),
                        "amount": str(Decimal(data["amount_cents"]) / 100),
                        "date": day(data["occurred_at"]),
                    }
                )
        return result

    @app.post("/api/deposits")
    def create(data: DepositInput, user=Depends(current), db: Session = Depends(db_session)):
        deposit = save(db, user, data)
        db.commit()
        return projection(db, deposit)

    @app.get("/api/deposits/{deposit_id}")
    def detail(deposit_id: str, user=Depends(current), db: Session = Depends(db_session)):
        return projection(db, get(db, user, deposit_id))

    @app.post("/api/deposits/{deposit_id}/save")
    def edit(
        deposit_id: str,
        data: DepositInput,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        deposit = get(db, user, deposit_id, True)
        unlocked(db, [deposit.preparation_date])
        save(db, user, data, deposit)
        db.commit()
        return projection(db, deposit)

    def available(db, deposit, session):
        cash, cheques = session.opening_cents, 0
        for move in db.scalars(select(Movement).where(Movement.session_id == session.id)):
            if move.origin == "deposito_modulo":
                origin = db.scalar(
                    select(BankDeposit).where(
                        (BankDeposit.movement_id == move.id) | (BankDeposit.reversal_id == move.id)
                    )
                )
                if origin:
                    cash += cents(origin.cash_amount) * move.direction
                    cheques += cents(origin.cheque_amount) * move.direction
            elif move.payment == "numerario":
                cash += move.amount_cents * move.direction
        enterprise = daily_enterprise(db, session)
        maps = {
            a.external_id: a.register_id
            for a in db.scalars(
                select(WintouchCashAccount).where(WintouchCashAccount.enterprise_id == enterprise)
            )
        }
        for item in real_receipts(
            db, deposit.store_id, session.sheet_day or day(session.opened_at), enterprise
        ):
            if maps.get(item.get("account_id")) != deposit.register_id or included_as_opening(
                session, item
            ):
                continue
            method, _ = payment_key(item)
            if method == "numerario":
                cash += item["amount_cents"] * item["direction"]
            elif item.get("payment_code") == "CH":
                cheques += item["amount_cents"] * item["direction"]
        for receipt in db.scalars(
            select(DocumentPayment).where(
                DocumentPayment.store_id == deposit.store_id,
                DocumentPayment.method == "cheque",
                DocumentPayment.source == "manual",
                DocumentPayment.payment_date == (session.sheet_day or day(session.opened_at)),
            )
        ):
            cheques += receipt.amount_cents * receipt.direction
        return cash, cheques

    def accumulated_sources(db, register, selected_date, exclude=None):
        from .models import AccountBalanceReference
        reference = db.get(AccountBalanceReference, "cash:" + register.id)
        if reference and reference.reference_date > selected_date:
            reference = None
        enterprise = None
        company_id = db.info.get("company", {}).get("id")
        if company_id:
            with database.next_platform_session() as core:
                company = core.get(Company, company_id)
                enterprise = company.api_enterprise if company else ""
        movements, cheques = [], []
        cash, cheque_balance = (reference.amount_cents - reference.fund_cents if reference else 0), 0
        if reference:
            movements.append({"source": "initial_balance", "id": reference.target, "document": "Saldo inicial de início do dia", "date": reference.reference_date, "amount": str(Decimal(cash) / 100), "session_id": None})
        claimed = list(
            db.scalars(
                select(DepositCheque).where(
                    DepositCheque.active.is_(True), DepositCheque.deposit_id != exclude
                )
                if exclude
                else select(DepositCheque).where(DepositCheque.active.is_(True))
            )
        )
        reserved = {row.source_receipt for row in claimed}
        query = (
            select(Movement, Category.name)
            .join(Category, Category.id == Movement.category_id)
            .outerjoin(CashSession, CashSession.id == Movement.session_id)
            .outerjoin(
                BankDeposit,
                (BankDeposit.movement_id == Movement.id) | (BankDeposit.reversal_id == Movement.id),
            )
            .where(func.coalesce(CashSession.register_id, BankDeposit.register_id) == register.id)
        )
        movement_descriptions = {
            "venda": "Venda",
            "reembolso": "Reembolso",
            "fornecedor": "Pagamento a fornecedor",
            "entrada_extra": "Crédito de caixa",
            "despesa": "Despesa",
            "diferenca_caixa": "Diferença de caixa",
        }
        for move, category_name in db.execute(query):
            if day(move.occurred_at) > selected_date or reference and day(move.occurred_at) < reference.reference_date:
                continue
            amount = move.amount_cents
            if move.origin == "deposito_modulo":
                deposit = db.scalar(
                    select(BankDeposit).where(
                        (BankDeposit.movement_id == move.id) | (BankDeposit.reversal_id == move.id)
                    )
                )
                amount = cents(deposit.cash_amount) if deposit else 0
                cheque_balance += cents(deposit.cheque_amount) * move.direction if deposit else 0
            elif move.payment != "numerario":
                continue
            cash += amount * move.direction
            movement_description = " · ".join(
                part
                for part in (
                    "Saída" if move.direction < 0 else "Entrada",
                    movement_descriptions.get(move.kind, move.kind.replace("_", " ").capitalize()),
                    category_name,
                    move.entity.strip() if move.entity else "",
                )
                if part
            )
            movements.append(
                {
                    "source": "movement",
                    "id": move.id,
                    "document": move.document or move.reference or move.kind,
                    "description": movement_description,
                    "date": day(move.occurred_at),
                    "amount": str(Decimal(amount * move.direction) / 100),
                    "session_id": move.session_id,
                }
            )
        resolve_origin = cash_origin_resolver(db)
        for record in db.scalars(
            select(WintouchSaleRecord).where(
                WintouchSaleRecord.store_id == register.store_id,
                WintouchSaleRecord.kind == "real",
                WintouchSaleRecord.active.is_(True),
            )
        ):
            if enterprise is not None and record.enterprise_id != enterprise:
                continue
            item = json.loads(record.data)
            if not is_real_cash_movement(item):
                continue
            stamp = day(item["occurred_at"])
            origin_register, origin_sheet = resolve_origin(
                record.store_id, record.enterprise_id, item.get("account_id"), stamp
            )
            if stamp > selected_date or origin_register != register.id or reference and stamp < reference.reference_date:
                continue
            method, _ = payment_key(item)
            if method == "numerario":
                cash += item["amount_cents"] * item["direction"]
                movements.append(
                    {
                        "source": "wintouch",
                        "id": record.id,
                        "document": item.get("document") or record.id,
                        "description": " · ".join(
                            part
                            for part in (
                                "Saída" if item["direction"] < 0 else "Entrada",
                                "Movimento Wintouch",
                                str(item.get("category_name") or "").strip(),
                                str(item.get("entity") or "").strip(),
                            )
                            if part
                        ),
                        "date": stamp,
                        "amount": str(Decimal(item["amount_cents"] * item["direction"]) / 100),
                        "session_id": origin_sheet,
                    }
                )
            if item.get("payment_code") == "CH":
                cheque_balance += item["amount_cents"] * item["direction"]
                if item["direction"] == 1 and "wintouch:" + record.id not in reserved:
                    cheques.append(
                        {
                            "number": str(item.get("document") or record.id)[:100],
                            "bank": "Não indicado na origem",
                            "amount": str(Decimal(item["amount_cents"]) / 100),
                            "cheque_date": stamp,
                            "issuer": str(item.get("entity") or "")[:200],
                            "source_receipt": "wintouch:" + record.id,
                            "notes": "Referência do recebimento original; número e banco do cheque não disponibilizados pela origem.",
                        }
                    )
        session_ids = sorted({row["session_id"] for row in movements if row.get("session_id")})
        return {
            "session_id": session_ids[-1] if len(session_ids) == 1 else None,
            "session_ids": session_ids,
            "cash_amount": str(Decimal(max(0, cash)) / 100),
            "cash_balance": str(Decimal(cash) / 100),
            "cheque_balance": str(Decimal(cheque_balance) / 100),
            "movements": movements,
            "cheques": cheques,
        }

    app.state.liquidity_cash_sources = accumulated_sources

    @app.get("/api/registers/{register_id}/deposit-balance")
    def deposit_balance(
        register_id: str,
        selected_date: date,
        exclude_deposit: str | None = None,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        register = db.get(Register, register_id)
        if not register:
            raise HTTPException(404, "Caixa não encontrada.")
        authorize(user, db, register.store_id)
        if exclude_deposit:
            existing = get(db, user, exclude_deposit)
            if existing.register_id != register_id:
                raise HTTPException(422, "O depósito pertence a outra caixa.")
        result = accumulated_sources(db, register, selected_date.isoformat(), exclude_deposit)
        return {
            **result,
            "available": True,
            "message": "Numerário dos movimentos reais até à data selecionada, descontando depósitos confirmados. O fundo de maneio de abertura permanece na caixa e não é somado como uma nova entrada.",
        }

    def transition(
        deposit_id: str,
        action: Literal[
            "register", "prepare", "exit", "deliver", "cancel", "reconcile", "undo", "fee", "cheque-return"
        ],
        data: Action,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        deposit = get(db, user, deposit_id, True)
        unlocked(db, [deposit.preparation_date, deposit.exit_date, data.action_date.isoformat()])
        if data.action_date > datetime.now(ZoneInfo("Europe/Lisbon")).date():
            raise HTTPException(422, "Não pode confirmar uma operação futura.")
        if data.action_date.isoformat() < deposit.preparation_date:
            raise HTTPException(422, "A operação não pode anteceder a preparação do depósito.")
        def step(action):
            if action == "prepare":
                if deposit.status == "rascunho":
                    deposit.status = "preparado"
                elif deposit.status != "preparado":
                    raise HTTPException(409, "O depósito já passou a preparação.")
            elif action == "exit":
                if deposit.movement_id:
                    return projection(db, deposit)
                if deposit.status != "preparado":
                    raise HTTPException(409, "Prepare o depósito antes de confirmar a saída.")
                db.scalar(select(Register).where(Register.id == deposit.register_id).with_for_update())
                session = db.scalar(
                    select(CashSession)
                    .where(
                        CashSession.register_id == deposit.register_id, CashSession.closed_at.is_(None)
                    )
                    .with_for_update()
                )
                snapshot = json.loads(deposit.details)
                if snapshot.get("cash_mode") == "real_movements":
                    current_sources = accumulated_sources(
                        db,
                        db.get(Register, deposit.register_id),
                        data.action_date.isoformat(),
                        deposit.id,
                    )
                    cash, cheques = (
                        cents(Decimal(current_sources["cash_balance"])),
                        cents(Decimal(current_sources["cheque_balance"])),
                    )
                else:
                    if not session:
                        raise HTTPException(
                            409,
                            "Atualize este depósito antigo para utilizar o acumulado dos movimentos reais.",
                        )
                    cash, cheques = available(db, deposit, session)
                snapshot = json.loads(deposit.details)
                if snapshot.get("cash_mode") == "real_movements":
                    fresh = current_sources

                    def signature(rows):
                        return sorted((row["source"], row["id"], row["amount"]) for row in rows)

                    stored_cheques = list(
                        db.scalars(
                            select(DepositCheque).where(
                                DepositCheque.deposit_id == deposit.id, DepositCheque.active.is_(True)
                            )
                        )
                    )
                    old_receipts = sorted(
                        (row.source_receipt or "payment:" + row.receipt_id, cents(row.amount))
                        for row in stored_cheques
                    )
                    new_receipts = sorted(
                        (
                            row.get("source_receipt") or "payment:" + row["receipt_id"],
                            cents(money(row["amount"])),
                        )
                        for row in fresh["cheques"]
                    )
                    if (
                        signature(fresh["movements"]) != signature(snapshot.get("sources", []))
                        or old_receipts != new_receipts
                    ):
                        raise HTTPException(
                            409,
                            "Os movimentos reais da caixa mudaram. Atualize o depósito e prepare-o novamente antes da saída.",
                        )
                deficit = (max(0, cents(deposit.cash_amount) - cash) if deposit.cash_amount else 0) + (
                    max(0, cents(deposit.cheque_amount) - cheques) if deposit.cheque_amount else 0
                )
                if deficit:
                    if not data.allow_insufficient:
                        raise HTTPException(
                            409,
                            "Saldo insuficiente. Falta: "
                            + euros(Decimal(deficit) / 100)
                            + ". Numerário disponível: "
                            + euros(Decimal(cash) / 100)
                            + "; cheques: "
                            + euros(Decimal(cheques) / 100),
                        )
                    authorize(user, db, deposit.store_id, ["administrador"])
                    if len(data.reason.strip()) < 5:
                        raise HTTPException(422, "Justifique a exceção ao saldo disponível.")
                category = db.scalar(select(Category).where(Category.name == "Transferências e fundos"))
                if not category:
                    category = Category(name="Transferências e fundos")
                    db.add(category)
                    db.flush()
                move = Movement(
                    session_id=session.id if session else None,
                    user_id=user.id,
                    category_id=category.id,
                    kind="deposito",
                    payment="numerario"
                    if not deposit.cheque_amount
                    else "cheque"
                    if not deposit.cash_amount
                    else "misto",
                    amount_cents=cents(deposit.cash_amount + deposit.cheque_amount),
                    direction=-1,
                    occurred_at=datetime.combine(
                        data.action_date, time(12), tzinfo=ZoneInfo("Europe/Lisbon")
                    ),
                    document=deposit.number,
                    reference=deposit.id,
                    origin="deposito_modulo",
                    request_key="deposit-exit:" + deposit.id,
                    notes="Transferência para banco; não é receita.",
                )
                db.add(move)
                db.flush()
                deposit.movement_id, deposit.exit_date, deposit.status = (
                    move.id,
                    data.action_date.isoformat(),
                    "em_transito",
                )
            elif action == "deliver":
                if deposit.status not in {
                    "em_transito",
                    "entregue",
                    "parcialmente_reconciliado",
                    "reconciliado",
                }:
                    raise HTTPException(409, "Confirme a saída antes da entrega.")
                if data.action_date.isoformat() < deposit.exit_date:
                    raise HTTPException(422, "A entrega não pode anteceder a saída.")
                if not deposit.delivery_date:
                    deposit.delivery_date = data.action_date.isoformat()
                    deposit.status = "entregue"
            elif action == "cancel":
                if deposit.status == "cancelado":
                    return projection(db, deposit)
                if len(data.reason.strip()) < 5:
                    raise HTTPException(422, "Justifique o cancelamento.")
                if db.scalar(
                    select(DepositCredit.id).where(
                        DepositCredit.deposit_id == deposit.id, DepositCredit.undone_at.is_(None)
                    )
                ):
                    raise HTTPException(409, "Desfaça as associações bancárias antes da reversão.")
                if general_reconciled_cents(db, deposit.movement_id):
                    raise HTTPException(409, "Desfaça a reconciliação deste depósito em Bancos antes da reversão.")
                if deposit.movement_id:
                    authorize(user, db, deposit.store_id, ["administrador", "gestor"])
                    if not data.returned:
                        raise HTTPException(
                            422, "Confirme que os valores regressaram fisicamente à caixa."
                        )
                    session = db.scalar(
                        select(CashSession)
                        .where(
                            CashSession.register_id == deposit.register_id,
                            CashSession.closed_at.is_(None),
                        )
                        .with_for_update()
                    )
                    original = db.get(Movement, deposit.movement_id)
                    reversal = Movement(
                        session_id=session.id if session else None,
                        user_id=user.id,
                        category_id=original.category_id,
                        kind="deposito",
                        payment=original.payment,
                        amount_cents=original.amount_cents,
                        direction=1,
                        occurred_at=datetime.combine(
                            data.action_date, time(12), tzinfo=ZoneInfo("Europe/Lisbon")
                        ),
                        document=deposit.number,
                        reference=deposit.id,
                        origin="deposito_modulo",
                        request_key="deposit-return:" + deposit.id,
                        reversal_of=original.id,
                        notes=data.reason,
                    )
                    db.add(reversal)
                    db.flush()
                    original.reversed_by = reversal.id
                    deposit.reversal_id = reversal.id
                deposit.status = "cancelado"
                for cheque in db.scalars(
                    select(DepositCheque).where(DepositCheque.deposit_id == deposit.id)
                ):
                    cheque.active = False
            elif action == "undo":
                authorize(user, db, deposit.store_id, ["administrador", "gestor"])
                if len(data.reason.strip()) < 5:
                    raise HTTPException(422, "Justifique a remoção da associação bancária.")
                for link in db.scalars(
                    select(DepositCredit).where(
                        DepositCredit.deposit_id == deposit.id, DepositCredit.undone_at.is_(None)
                    )
                ):
                    bank = db.get(BankTransaction, link.transaction_id)
                    unlocked(db, [bank.movement_date])
                    link.undone_at = now()
                db.flush()
                state_after_credit(db, deposit)
            else:
                authorize(user, db, deposit.store_id, ["administrador", "gestor"])
                if deposit.status not in {"entregue", "parcialmente_reconciliado", "reconciliado"}:
                    raise HTTPException(409, "Registe a entrega ao banco antes de associar o extrato.")
                old = db.scalar(
                    select(DepositCredit).where(DepositCredit.request_key == data.request_key)
                )
                if old:
                    if old.deposit_id != deposit.id:
                        raise HTTPException(409, "Este pedido pertence a outro depósito.")
                    return projection(db, deposit)
                bank = db.scalar(
                    select(BankTransaction)
                    .where(BankTransaction.id == data.transaction_id)
                    .with_for_update()
                )
                amount = money(data.amount)
                if not bank or bank.account_id != deposit.account_id or amount <= 0:
                    raise HTTPException(
                        422, "Selecione um movimento e um montante válido da conta de destino."
                    )
                unlocked(db, [bank.movement_date])
                from .reconciliation import used

                available_cents = abs(bank.amount_cents) - used(db, "bank", bank.id)
                if cents(amount) > available_cents:
                    raise HTTPException(409, "O montante excede o valor disponível no extrato.")
                kind = (
                    "credito"
                    if action == "reconcile"
                    else "comissao"
                    if action == "fee"
                    else "devolucao"
                )
                if (bank.amount_cents > 0) != (kind == "credito"):
                    raise HTTPException(
                        422, "Créditos, comissões e devoluções devem ser associados separadamente."
                    )
                if (
                    kind == "credito"
                    and amount > deposit.cash_amount + deposit.cheque_amount - credit_total(db, deposit)
                ):
                    raise HTTPException(409, "O crédito excede o montante por reconciliar.")
                cheque = db.get(DepositCheque, data.cheque_id) if data.cheque_id else None
                if kind == "devolucao":
                    if (
                        not cheque
                        or cheque.deposit_id != deposit.id
                        or cheque.returned_at
                        or amount != cheque.amount
                        or len(data.reason.strip()) < 5
                    ):
                        raise HTTPException(
                            422, "Indique o cheque devolvido, o seu valor e a justificação."
                        )
                    cheque.returned_at = now()
                elif cheque and cheque.deposit_id != deposit.id:
                    raise HTTPException(422, "Cheque de outro depósito.")
                if kind == "credito" and cheque:
                    allocated = db.scalar(
                        select(func.sum(DepositCredit.amount)).where(
                            DepositCredit.cheque_id == cheque.id,
                            DepositCredit.kind == "credito",
                            DepositCredit.undone_at.is_(None),
                        )
                    ) or Decimal(0)
                    if amount > cheque.amount - allocated:
                        raise HTTPException(409, "O montante excede o valor por creditar deste cheque.")
                if kind == "comissao" and len(data.reason.strip()) < 5:
                    raise HTTPException(422, "Justifique a comissão bancária.")
                db.add(
                    DepositCredit(
                        deposit_id=deposit.id,
                        transaction_id=bank.id,
                        cheque_id=data.cheque_id,
                        amount=amount,
                        kind=kind,
                        request_key=data.request_key,
                        user_id=user.id,
                        reason=data.reason,
                    )
                )
                db.flush()
                state_after_credit(db, deposit)
            return None

        if action == "register":
            # Registo num só passo (talão entregue no banco): prepara, confirma a saída da caixa e a
            # entrada na conta na mesma data. A reconciliação com o extrato faz-se depois, em Bancos.
            if deposit.status == "rascunho":
                step("prepare")
            if deposit.status == "preparado":
                step("exit")
            if deposit.status == "em_transito":
                step("deliver")
            if deposit.status not in {"entregue", "parcialmente_reconciliado", "reconciliado"}:
                raise HTTPException(409, "Este depósito não pode ser registado no banco.")
        else:
            early = step(action)
            if early is not None:
                return early
        audit(db, user, deposit, action, data.model_dump(mode="json"))
        db.commit()
        return projection(db, deposit)

    def action_endpoint(name):
        def endpoint(
            deposit_id: str, data: Action, user=Depends(current), db: Session = Depends(db_session)
        ):
            return transition(deposit_id, name, data, user, db)

        endpoint.__name__ = "deposit_" + name.replace("-", "_")
        return endpoint

    for action_name in [
        "register",
        "prepare",
        "exit",
        "deliver",
        "cancel",
        "reconcile",
        "undo",
        "fee",
        "cheque-return",
    ]:
        app.add_api_route(
            "/api/deposits/{deposit_id}/" + action_name,
            action_endpoint(action_name),
            methods=["POST"],
        )

    @app.get("/api/deposits/{deposit_id}/candidates")
    def candidates(deposit_id: str, user=Depends(current), db: Session = Depends(db_session)):
        deposit = get(db, user, deposit_id)
        from .reconciliation import used

        result = []
        for bank in db.scalars(
            select(BankTransaction)
            .where(BankTransaction.account_id == deposit.account_id)
            .order_by(BankTransaction.movement_date.desc())
        ):
            available_amount = Decimal(abs(bank.amount_cents) - used(db, "bank", bank.id)) / 100
            if available_amount <= 0:
                continue
            gap = abs(
                (
                    date.fromisoformat(bank.movement_date)
                    - date.fromisoformat(deposit.delivery_date or deposit.preparation_date)
                ).days
            )
            reasons = []
            if (
                bank.amount_cents > 0
                and available_amount
                == deposit.cash_amount + deposit.cheque_amount - credit_total(db, deposit)
            ):
                reasons.append("Montante igual")
            if gap <= 10:
                reasons.append("Data próxima")
            if deposit.reference and deposit.reference.casefold() in bank.description.casefold():
                reasons.append("Referência encontrada")
            result.append(
                {
                    "id": bank.id,
                    "date": bank.movement_date,
                    "value_date": bank.value_date,
                    "description": bank.description,
                    "amount": str(Decimal(bank.amount_cents) / 100),
                    "available": str(available_amount),
                    "reasons": reasons,
                }
            )
        return sorted(result, key=lambda row: len(row["reasons"]), reverse=True)

    @app.get("/api/deposits/{deposit_id}/pdf")
    def pdf(deposit_id: str, user=Depends(current), db: Session = Depends(db_session)):
        deposit = get(db, user, deposit_id)
        return Response(
            deposit_pdf(db, deposit),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{deposit.number}.pdf"'},
        )

    @app.post("/api/deposits/{deposit_id}/attachments")
    def upload(
        deposit_id: str,
        file: UploadFile = File(...),
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        deposit = get(db, user, deposit_id, True)
        unlocked(db, [deposit.preparation_date])
        raw = file.file.read(5 * 1024 * 1024 + 1)
        mime = (
            "application/pdf"
            if raw.startswith(b"%PDF-")
            else "image/png"
            if raw.startswith(b"\x89PNG\r\n\x1a\n")
            else "image/jpeg"
            if raw.startswith(b"\xff\xd8\xff")
            else ""
        )
        if not raw or len(raw) > 5 * 1024 * 1024 or not mime or mime != file.content_type:
            raise HTTPException(422, "Aceitam-se PDF, PNG e JPG até 5 MB, com conteúdo válido.")
        if (
            db.scalar(
                select(func.count())
                .select_from(DepositAttachment)
                .where(DepositAttachment.deposit_id == deposit.id)
            )
            >= 10
        ):
            raise HTTPException(422, "Máximo de dez anexos por depósito.")
        attachment = DepositAttachment(
            deposit_id=deposit.id,
            name=(file.filename or "comprovativo").replace("\\", "/").split("/")[-1][:200],
            mime=mime,
            data=raw,
        )
        db.add(attachment)
        db.flush()
        audit(db, user, deposit, "anexo", {"attachment_id": attachment.id})
        db.commit()
        return projection(db, deposit)

    @app.get("/api/deposits/{deposit_id}/attachments/{attachment_id}")
    def preview(
        deposit_id: str,
        attachment_id: str,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        get(db, user, deposit_id)
        attachment = db.get(DepositAttachment, attachment_id)
        if not attachment or attachment.deposit_id != deposit_id:
            raise HTTPException(404, "Anexo não encontrado.")
        return Response(
            attachment.data,
            media_type=attachment.mime,
            headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/deposits/{deposit_id}/attachments/{attachment_id}/validate")
    def validate_attachment(
        deposit_id: str,
        attachment_id: str,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        deposit = get(db, user, deposit_id, True)
        authorize(user, db, deposit.store_id, ["administrador", "gestor"])
        unlocked(db, [deposit.preparation_date])
        attachment = db.get(DepositAttachment, attachment_id)
        if not attachment or attachment.deposit_id != deposit_id:
            raise HTTPException(404, "Anexo não encontrado.")
        attachment.validated_by = user.id
        audit(db, user, deposit, "comprovativo_validado", {"attachment_id": attachment.id})
        db.commit()
        return projection(db, deposit)
