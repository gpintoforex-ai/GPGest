"""Read-only suggestions from imported, unallocated Wintouch cash entries."""

import json

from sqlalchemy import select

from . import db as database
from .cash_sheet import day, payment_key
from .models import CashSession, Register, WintouchCashAccount, WintouchSaleRecord
from .platform_models import Company


def candidates(db, register, selected_day):
    company_id = db.info.get("company", {}).get("id")
    if not company_id:
        return []
    with database.next_platform_session() as core:
        company = core.get(Company, company_id)
        enterprise = company.api_enterprise if company else None
    if not enterprise:
        return []
    accounts = list(
        db.scalars(
            select(WintouchCashAccount).where(
                WintouchCashAccount.enterprise_id == enterprise,
                WintouchCashAccount.active.is_(True),
            )
        )
    )
    mapped = {a.external_id for a in accounts if a.register_id == register.id}
    other = {a.external_id for a in accounts if a.register_id and a.register_id != register.id}
    used = {
        json.loads(s.count_data or "{}").get("opening_wintouch_id")
        for s in db.scalars(
            select(CashSession).join(Register).where(Register.store_id == register.store_id)
        )
    }
    result = []
    for row in db.scalars(
        select(WintouchSaleRecord).where(
            WintouchSaleRecord.enterprise_id == enterprise,
            WintouchSaleRecord.store_id == register.store_id,
            WintouchSaleRecord.kind == "real",
            WintouchSaleRecord.active.is_(True),
        )
    ):
        data = json.loads(row.data)
        if (
            row.id in used
            or data.get("source_kind") != "cash_movement"
            or data.get("direction") != 1
        ):
            continue
        if payment_key(data)[0] != "numerario" or day(data["occurred_at"]) != selected_day:
            continue
        account = data.get("account_id")
        if (mapped and account not in mapped) or account in other:
            continue
        result.append(
            {
                "id": row.id,
                "amount_cents": data["amount_cents"],
                "document": data.get("document", ""),
                "account_id": account,
            }
        )
    return result


def included_as_opening(session, data):
    return (
        bool(data.get("_record_id"))
        and json.loads(session.count_data or "{}").get("opening_wintouch_id") == data["_record_id"]
    )
