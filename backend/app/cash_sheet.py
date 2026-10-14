"""Cash sheet projections; imported receipts are not copied into the local ledger."""

import hashlib
import json
import unicodedata
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select

from . import db as database
from .models import (
    CashSession,
    Movement,
    Register,
    WintouchSaleRecord,
    BankDeposit,
    DocumentPayment,
    WintouchCashAccount,
    LocalPaymentMethod,
)
from .platform_models import Company

LABELS = {
    "numerario": "Numerário",
    "cartao": "Multibanco / cartão",
    "transferencia": "Transferência",
    "outro": "Outro",
}


def is_real_cash_movement(data):
    """Opening funds and shift totals carry balances rather than new money."""
    names = {
        " ".join(unicodedata.normalize("NFKD", name).casefold().split())
        for name in str(data.get("category_name") or "").split(" / ")
    }
    if "valor do turno" in names or data.get("source_kind") == "opening_balance":
        return False
    return not (
        data.get("source_kind") == "cash_movement"
        and data.get("direction") == 1
        and "fundo de maneio" in names
    )
DENOMINATIONS = {1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000}


def day(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo:
        stamp = stamp.astimezone(ZoneInfo("Europe/Lisbon"))
    return stamp.date().isoformat()


def payment_key(row):
    code = row.get("payment_code")
    codes = {
        "NU": "numerario",
        "MO": "numerario",
        "CD": "cartao",
        "CC": "cartao",
        "TB": "transferencia",
    }
    if code in codes:
        key = codes[code]
        return key, LABELS[key]
    name = str(row.get("payment") or "Não indicado").strip()
    plain = "".join(
        c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c)
    ).lower()
    names = {
        "numerario": "numerario",
        "cartao": "cartao",
        "multibanco": "cartao",
        "cartao de debito": "cartao",
        "cartao de credito": "cartao",
        "transferencia": "transferencia",
        "transferencia bancaria": "transferencia",
        "outro": "outro",
    }
    if plain in names:
        key = names[plain]
        return key, LABELS[key]
    name = {"CH": "Cheque", "TR": "Cartão refeição"}.get(code, name)
    return "wt_" + hashlib.sha256(name.casefold().encode()).hexdigest()[:24], name


def sheet_source_context(db, session):
    """Read-only automatic attribution to one open sheet per store/day."""
    if session.wintouch_scope:
        return session.wintouch_scope, session.wintouch_enterprise
    if session.closed_at or json.loads(session.count_data or "{}").get("wintouch_excluded"):
        return None, None
    company_id = db.info.get("company", {}).get("id")
    if not company_id:
        return None, None
    with database.next_platform_session() as core:
        company = core.get(Company, company_id)
        enterprise = company.api_enterprise if company else None
    if not enterprise:
        return None, None
    register = db.get(Register, session.register_id)
    selected_day = session.sheet_day or day(session.opened_at)
    token = register.store_id + ":" + selected_day
    if db.scalar(select(CashSession.id).where(CashSession.wintouch_scope == token)):
        return None, None
    candidates = db.scalars(
        select(CashSession)
        .join(Register)
        .where(Register.store_id == register.store_id, CashSession.closed_at.is_(None))
        .order_by(CashSession.opened_at, CashSession.id)
    )
    for candidate in candidates:
        if (candidate.sheet_day or day(candidate.opened_at)) == selected_day and not json.loads(
            candidate.count_data or "{}"
        ).get("wintouch_excluded"):
            return (token, enterprise) if candidate.id == session.id else (None, None)
    return None, None


def reserve_auto_source(db, session):
    token, enterprise = sheet_source_context(db, session)
    if token and not session.wintouch_scope:
        session.wintouch_scope = token
        session.wintouch_enterprise = enterprise
        session.sheet_day = session.sheet_day or day(session.opened_at)
        db.flush()


def daily_enterprise(db, session):
    company_id = db.info.get("company", {}).get("id")
    if not company_id:
        return session.wintouch_enterprise
    with database.next_platform_session() as core:
        company = core.get(Company, company_id)
        return company.api_enterprise if company else None


def real_receipts(db, store_id, selected_day, enterprise):
    if not enterprise:
        return
    # Use the payment date, which can differ from the invoice date.
    for record in db.scalars(
        select(WintouchSaleRecord).where(
            WintouchSaleRecord.store_id == store_id,
            WintouchSaleRecord.enterprise_id == enterprise,
            WintouchSaleRecord.kind == "real",
            WintouchSaleRecord.active.is_(True),
        )
    ):
        data = json.loads(record.data)
        if not is_real_cash_movement(data):
            continue
        data["_record_id"] = record.id
        if day(data["occurred_at"]) == selected_day:
            yield data


def belongs_to_register(db, session, register, data):
    account_id = data.get("account_id")
    if account_id:
        account = db.scalar(select(WintouchCashAccount).where(
            WintouchCashAccount.enterprise_id == daily_enterprise(db, session),
            WintouchCashAccount.external_id == account_id,
        ))
        if account and account.register_id:
            return account.register_id == register.id
    registers = list(db.scalars(select(Register.id).where(Register.store_id == register.store_id)))
    return len(registers) == 1 or bool(session.wintouch_scope)


def summary(db, session, register_only=False, methods=None):
    stored = json.loads(session.count_data or "{}")
    if session.closed_at and stored.get("final"):
        return stored["summary"]
    rows = {
        key: {
            "key": key,
            "name": name,
            "credits_cents": 0,
            "debits_cents": 0,
            "local_cents": 0,
            "wintouch_cents": 0,
            "count": 0,
        }
        for key, name in (methods or LABELS).items()
    }

    def add(key, name, amount, direction, source):
        row = rows.setdefault(
            key,
            {
                "key": key,
                "name": name,
                "credits_cents": 0,
                "debits_cents": 0,
                "local_cents": 0,
                "wintouch_cents": 0,
                "count": 0,
            },
        )
        row["credits_cents" if direction == 1 else "debits_cents"] += amount
        row[source + "_cents"] += amount * direction
        row["count"] += 1

    register = db.get(Register, session.register_id)
    selected_day = session.sheet_day or day(session.opened_at)
    query = select(Movement).where(
        (Movement.origin == "deposito_modulo")
        | (Movement.reversed_by.is_(None) & Movement.reversal_of.is_(None)),
    )
    if session.closed_at:
        # Legacy closes without a snapshot keep their original session scope.
        query = query.where(Movement.session_id == session.id)
    else:
        selected_date = datetime.fromisoformat(selected_day).date()
        start = datetime.combine(selected_date, time.min, tzinfo=ZoneInfo("Europe/Lisbon"))
        end = datetime.combine(
            selected_date + timedelta(days=1), time.min, tzinfo=ZoneInfo("Europe/Lisbon")
        )
        query = (
            query.join(CashSession)
            .join(Register)
            .where(
                Register.store_id == register.store_id,
                Movement.occurred_at >= start,
                Movement.occurred_at < end,
            )
        )
        if register_only:
            query = select(Movement).outerjoin(CashSession).outerjoin(
                BankDeposit, (BankDeposit.movement_id == Movement.id) | (BankDeposit.reversal_id == Movement.id)
            ).where(
                func.coalesce(CashSession.register_id, BankDeposit.register_id) == register.id,
                Movement.occurred_at >= start, Movement.occurred_at < end,
                (Movement.origin == "deposito_modulo") | (Movement.reversed_by.is_(None) & Movement.reversal_of.is_(None)),
                Movement.origin != "fecho_caixa", Movement.origin != "opening_balance",
            )
    for move in db.scalars(query):
        if move.origin == "deposito_modulo":
            deposit = db.scalar(
                select(BankDeposit).where(
                    (BankDeposit.movement_id == move.id) | (BankDeposit.reversal_id == move.id)
                )
            )
            if deposit:
                if deposit.cash_amount:
                    add(
                        "numerario",
                        "Numerário",
                        int(deposit.cash_amount * 100),
                        move.direction,
                        "local",
                    )
                if deposit.cheque_amount:
                    key, label = payment_key({"payment_code": "CH"})
                    add(key, label, int(deposit.cheque_amount * 100), move.direction, "local")
            continue
        name = (methods or LABELS).get(move.payment, "MB WAY" if move.payment == "mbway" else move.payment)
        if methods is None and move.payment.startswith("pm_"):
            configured = db.get(LocalPaymentMethod, move.payment)
            if configured:
                name = configured.name
        add(move.payment, name, move.amount_cents, move.direction, "local")
    enterprise = (
        daily_enterprise(db, session) if not session.closed_at else session.wintouch_enterprise
    )
    for data in real_receipts(db, register.store_id, selected_day, enterprise):
        from .opening_fund import included_as_opening

        if included_as_opening(session, data):
            continue
        if register_only and not belongs_to_register(db, session, register, data):
            continue
        key, name = payment_key(data)
        if register_only and str(data.get("payment", "")).casefold().replace(" ", "") == "mbway":
            key, name = "mbway", "MB WAY"
        add(key, name, data["amount_cents"], data["direction"], "wintouch")
    for receipt in db.scalars(
        select(DocumentPayment).where(
            DocumentPayment.store_id == register.store_id,
            DocumentPayment.payment_date == selected_day,
            DocumentPayment.source == "manual",
            DocumentPayment.method == "cheque",
        )
    ):
        if register_only and len(list(db.scalars(select(Register.id).where(Register.store_id == register.store_id)))) > 1:
            # A store-level cheque without a register cannot be attributed to a drawer.
            continue
        key, label = payment_key({"payment_code": "CH"})
        add(key, label, receipt.amount_cents, receipt.direction, "local")
    for row in rows.values():
        row["net_cents"] = row["credits_cents"] - row["debits_cents"]
        row["fund_cents"] = session.opening_cents if row["key"] == "numerario" else 0
        row["expected_cents"] = row["fund_cents"] + row["net_cents"]
    return list(rows.values())


def validate_count(data, rows, parse_money, closing=False):
    if any(
        not k.isdigit() or int(k) not in DENOMINATIONS or not 0 <= v <= 100000
        for k, v in data.counts.items()
    ):
        raise HTTPException(422, "Contagem de notas ou moedas inválida.")
    if data.cash_total is not None and data.counts:
        raise HTTPException(422, "Use o valor total ou a contagem por notas e moedas.")
    expected = {row["key"]: row["expected_cents"] for row in rows}
    if set(data.payments) - (set(expected) - {"numerario"}):
        raise HTTPException(422, "Meio de pagamento inválido nesta folha.")
    values = {}
    for key, value in data.payments.items():
        text = value.strip()
        values[key] = (
            -parse_money(text[1:], True) if text.startswith("-") else parse_money(text, True)
        )
    if data.cash_total is not None:
        values["numerario"] = parse_money(data.cash_total, True)
    elif data.counts or closing:
        values["numerario"] = sum(int(k) * v for k, v in data.counts.items())
    # Keep the legacy close API (cash only), but check every supplied payment count.
    if (
        closing
        and any(values[key] != expected[key] for key in values)
        and len(data.reason.strip()) < 5
    ):
        raise HTTPException(422, "Justifique as diferenças antes de fechar a folha de caixa.")
    return {
        "values": values,
        "counts": data.counts,
        "reason": data.reason,
        "cash_mode": data.cash_mode
        or ("total" if data.cash_total is not None else "denominations"),
    }
