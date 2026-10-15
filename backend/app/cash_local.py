"""Declared sales set local count expectations without changing the cash ledger."""

import hashlib
import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from .cash_sheet import (
    belongs_to_register,
    daily_enterprise,
    day,
    payment_key,
    real_receipts,
    summary,
    validate_count,
)
from .models import CashSession, LocalPaymentMethod, Movement, Register, now

BASE_METHODS = {
    "numerario": "Numerário",
    "cartao": "Multibanco / TPA",
    "mbway": "MB WAY",
    "transferencia": "Transferência",
    "outro": "Outros meios",
}


def methods(db):
    return {
        **BASE_METHODS,
        **{
            row.id: row.name
            for row in db.scalars(select(LocalPaymentMethod).order_by(LocalPaymentMethod.name))
        },
    }


def is_local(db, session, policy):
    stored = json.loads(session.count_data or "{}")
    return (
        stored.get("workflow_mode", "integrated") == "local"
        if session.closed_at
        else policy(db)["mode"] == "local"
    )


def drawer_rows(db, session):
    return summary(db, session, register_only=True, methods=methods(db))


def receipt_rows(db, session, drawer):
    register = db.get(Register, session.register_id)
    selected = session.sheet_day or day(session.opened_at)
    selected_date = datetime.fromisoformat(selected).date()
    start = datetime.combine(selected_date, time.min, tzinfo=ZoneInfo("Europe/Lisbon"))
    end = datetime.combine(
        selected_date + timedelta(days=1), time.min, tzinfo=ZoneInfo("Europe/Lisbon")
    )
    rows = {
        row["key"]: {
            "key": row["key"],
            "name": row["name"],
            "registered_cents": 0,
            "manual_cents": 0,
            "erp_cents": 0,
            "sources": [],
        }
        for row in drawer
    }

    def add(key, name, amount, source):
        row = rows.setdefault(
            key,
            {
                "key": key,
                "name": name,
                "registered_cents": 0,
                "manual_cents": 0,
                "erp_cents": 0,
                "sources": [],
            },
        )
        row["registered_cents"] += amount
        row[source + "_cents"] += amount
        label = "ERP" if source == "erp" else "Manual"
        if label not in row["sources"]:
            row["sources"].append(label)

    for movement in db.scalars(
        select(Movement)
        .join(CashSession)
        .where(
            CashSession.register_id == register.id,
            Movement.occurred_at >= start,
            Movement.occurred_at < end,
            Movement.reversal_of.is_(None),
            Movement.reversed_by.is_(None),
            Movement.transfer_id.is_(None),
            Movement.kind.in_(["venda", "cliente", "reembolso"]),
            Movement.origin != "fecho_caixa",
        )
    ):
        add(
            movement.payment,
            rows.get(movement.payment, {}).get("name", movement.payment),
            movement.direction * movement.amount_cents,
            "manual",
        )
    for data in real_receipts(db, register.store_id, selected, daily_enterprise(db, session)):
        if data.get("source_kind") in {
            "cash_movement",
            "opening_balance",
        } or not belongs_to_register(db, session, register, data):
            continue
        key, name = payment_key(data)
        if str(data.get("payment", "")).casefold().replace(" ", "") == "mbway":
            key, name = "mbway", "MB WAY"
        add(key, name, data["direction"] * data["amount_cents"], "erp")
    return list(rows.values())


def realized_for(session, counting):
    realized = counting.get("realized", {})
    if (
        realized.get("day") != (session.sheet_day or day(session.opened_at))
        or realized.get("register_id") != session.register_id
    ):
        return {"values": {}, "notes": "", "pos_reference": ""}
    return realized


def reconciliation(db, session, drawer, counting=None):
    stored = counting if counting is not None else json.loads(session.count_data or "{}")
    if session.closed_at and stored.get("local_reconciliation"):
        return stored["local_reconciliation"]
    realized = realized_for(session, stored)
    rows = receipt_rows(db, session, drawer)
    balances = {row["key"]: row for row in drawer}
    for row in rows:
        declared = realized.get("values", {}).get(row["key"])
        balance = balances[row["key"]]
        fund = balance["fund_cents"]
        # Replace the recorded receipts with declared sales; never add both.
        other = balance["expected_cents"] - fund - row["registered_cents"]
        expected = None if declared is None else fund + declared + other
        counted = stored.get("values", {}).get(row["key"])
        row.update(
            declared_cents=declared,
            difference_cents=None if declared is None else row["registered_cents"] - declared,
            declared_source="Manual",
            fund_cents=fund,
            other_movements_cents=other,
            expected_cents=expected,
            counted_cents=counted,
            count_difference_cents=(
                None if expected is None or counted is None else counted - expected
            ),
        )
    signature = hashlib.sha256(
        json.dumps(
            {
                "day": session.sheet_day or day(session.opened_at),
                "rows": rows,
                "drawer": drawer,
                "realized": realized,
                "values": stored.get("values", {}),
                "reason": stored.get("reason", ""),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    checked = stored.get("reconciliation_check", {}).get("signature") == signature
    for row in rows:
        row["status"] = (
            "Com diferença"
            if row["count_difference_cents"] not in (None, 0)
            else "Conferido"
            if checked and row["count_difference_cents"] == 0
            else "Por conferir"
        )
    complete = all(row["declared_cents"] is not None for row in rows)
    declared_total = sum(row["declared_cents"] or 0 for row in rows)
    registered_total = sum(row["registered_cents"] for row in rows)
    return {
        "rows": rows,
        "expectation_basis": "declared_sales",
        "realized": realized,
        "complete": complete,
        "checked": checked,
        "signature": signature,
        "declared_total_cents": declared_total,
        "registered_total_cents": registered_total,
        "difference_cents": registered_total - declared_total if complete else None,
        "check": stored.get("reconciliation_check") if checked else None,
    }


def validate(db, session, data, drawer, parse_money, user, complete=False, closing=False):
    previous = json.loads(session.count_data or "{}")
    parsed = validate_count(data, drawer, parse_money)
    if not {"counts", "payments", "cash_total", "cash_mode"} & data.model_fields_set:
        for key in ("values", "counts", "cash_mode"):
            if key in previous:
                parsed[key] = previous[key]
    if "reason" not in data.model_fields_set:
        parsed["reason"] = previous.get("reason", "")
    counting = {**previous, **parsed}
    stamp = now().isoformat()
    if data.realized is not None:
        allowed = {row["key"] for row in drawer}
        if set(data.realized.values) - allowed:
            raise HTTPException(422, "Meio de pagamento inválido no realizado do dia.")
        values = {}
        for key, text in data.realized.values.items():
            if text is None or not text.strip():
                values[key] = None
            else:
                value = text.strip()
                values[key] = (
                    -parse_money(value[1:], True)
                    if value.startswith("-")
                    else parse_money(value, True)
                )
        counting["realized"] = {
            "values": values,
            "notes": data.realized.notes,
            "pos_reference": data.realized.pos_reference,
            "day": session.sheet_day or day(session.opened_at),
            "register_id": session.register_id,
            "source": "Manual",
            "updated_at": stamp,
            "updated_by": user.id,
            "updated_by_name": user.name,
        }
    counting.update(updated_at=stamp, updated_by=user.id, updated_by_name=user.name)
    result = reconciliation(db, session, drawer, counting)
    if complete or closing:
        if not result["complete"]:
            raise HTTPException(
                422,
                "Preencha o realizado de todos os meios de pagamento. Confirme zero nos meios sem vendas/recebimentos.",
            )
        if "numerario" not in counting["values"]:
            raise HTTPException(
                422, "Indique o numerário contado, incluindo zero quando a gaveta estiver vazia."
            )
    if closing:
        if (
            any(row["count_difference_cents"] not in (None, 0) for row in result["rows"])
            and len(data.reason.strip()) < 5
        ):
            raise HTTPException(
                422,
                "Justifique as diferenças da conferência e do numerário antes de fechar a caixa.",
            )
    if complete or closing:
        counting["reconciliation_check"] = {
            "signature": result["signature"],
            "checked_at": stamp,
            "checked_by": user.id,
            "checked_by_name": user.name,
        }
    elif not result["checked"]:
        counting.pop("reconciliation_check", None)
    return counting
