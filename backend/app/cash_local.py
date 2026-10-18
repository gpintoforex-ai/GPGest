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
from .models import CashSession, CompanySetup, LocalPaymentMethod, Movement, Register, now

BASE_METHODS = {
    "numerario": "Numerário",
    "cartao": "Multibanco / TPA",
    "mbway": "MB WAY",
    "transferencia": "Transferência",
    "outro": "Outros meios",
}


def method_catalog(db):
    setup = db.get(CompanySetup, "default")
    disabled = json.loads(setup.settings or "{}").get("disabled_base_methods", []) if setup else []
    configured = {
        row.id: row
        for row in db.scalars(select(LocalPaymentMethod).order_by(LocalPaymentMethod.name))
    }
    names = {**BASE_METHODS, **{key: row.name for key, row in configured.items()}}
    return [
        {
            "id": key,
            "name": name,
            "active": key == "numerario" or (configured[key].active if key in configured else key not in disabled),
            "kind": "base" if key in BASE_METHODS else "custom",
            "required": key == "numerario",
            "cash": key == "numerario" or (json.loads(configured[key].settings or "{}").get("cash", False) if key in configured else False),
        }
        for key, name in names.items()
    ]


def methods(db, active_only=False):
    return {
        row["id"]: row["name"]
        for row in method_catalog(db)
        if not active_only or row["active"]
    }


def is_local(db, session, policy):
    stored = json.loads(session.count_data or "{}")
    return (
        stored.get("workflow_mode", "integrated") == "local"
        if session.closed_at
        else policy(db)["mode"] == "local"
    )


def drawer_rows(db, session):
    stored = json.loads(session.count_data or "{}")
    if session.closed_at and stored.get("final"):
        # Closed sheets use their immutable snapshot, including historical
        # method names and statuses. No current catalogue lookup is needed.
        return stored["summary"]
    catalog = method_catalog(db)
    catalog = [row for row in catalog if row["id"] == "numerario" or not row["cash"]]
    rows = summary(db, session, register_only=True, methods={r["id"]: r["name"] for r in catalog})
    if session.closed_at:
        return rows
    inactive = {row["id"] for row in catalog if not row["active"]}
    return [row for row in rows if row["key"] not in inactive]


def receipt_rows(db, session, drawer):
    register = db.get(Register, session.register_id)
    selected = session.sheet_day or day(session.opened_at)
    selected_date = datetime.fromisoformat(selected).date()
    timezone_name = json.loads(register.settings or "{}").get("timezone", "Europe/Lisbon")
    start = datetime.combine(selected_date, time.min, tzinfo=ZoneInfo(timezone_name))
    end = datetime.combine(
        selected_date + timedelta(days=1), time.min, tzinfo=ZoneInfo(timezone_name)
    )
    rows = {
        row["key"]: {
            "key": row["key"],
            "name": row["name"],
            "registered_cents": 0,
            "receipt_credits_cents": 0,
            "receipt_debits_cents": 0,
            "manual_cents": 0,
            "erp_cents": 0,
            "sources": [],
        }
        for row in drawer
    }

    def add(key, name, amount, source):
        # Receipts must not reintroduce an inactive method excluded from the drawer.
        if key not in rows:
            return
        row = rows[key]
        row["registered_cents"] += amount
        row["receipt_credits_cents" if amount >= 0 else "receipt_debits_cents"] += abs(amount)
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
    for data in real_receipts(db, register.store_id, selected, daily_enterprise(db, session), timezone_name):
        if data.get("source_kind") in {
            "cash_movement",
            "opening_balance",
        } or not belongs_to_register(db, session, register, data):
            continue
        key, name = payment_key(data)
        if not data.get("_local_payment_key") and str(data.get("payment", "")).casefold().replace(" ", "") == "mbway":
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
    setup = db.get(CompanySetup, "default")
    manual = json.loads(setup.settings or "{}").get("preferences", {}).get("manual_realized", True) if setup else True
    balances = {row["key"]: row for row in drawer}
    for row in rows:
        declared = realized.get("values", {}).get(row["key"]) if manual else row["registered_cents"]
        balance = balances[row["key"]]
        fund = balance["fund_cents"]
        # Declared debits are the day's total, including recorded non-sale outflows.
        registered_credits = balance["credits_cents"] - row["receipt_credits_cents"]
        declared_credits = realized.get("credits", {}).get(row["key"])
        credits = registered_credits if declared_credits is None else declared_credits
        registered_debits = balance["debits_cents"] - row["receipt_debits_cents"]
        declared_debits = realized.get("debits", {}).get(row["key"])
        debits = registered_debits if declared_debits is None else declared_debits
        other = credits - debits
        expected = None if declared is None else fund + declared + other
        counted = stored.get("values", {}).get(row["key"])
        row.update(
            declared_cents=declared,
            difference_cents=None if declared is None else row["registered_cents"] - declared,
            declared_source="Manual" if manual else "Movimentos",
            fund_cents=fund,
            other_credits_cents=credits,
            registered_credits_cents=registered_credits,
            declared_credits_cents=declared_credits,
            registered_debits_cents=registered_debits,
            declared_debits_cents=declared_debits,
            debits_cents=debits,
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
    inactive = {row["id"] for row in method_catalog(db) if not row["active"]}
    allowed = {row["key"] for row in drawer}
    # Older/open browser drafts may still submit inactive fields. Ignore them and
    # retain saved values as history, without including them in this conference.
    active_data = data.model_copy(update={"payments": {
        key: value for key, value in data.payments.items() if key not in inactive
    }})
    parsed = validate_count(active_data, drawer, parse_money)
    parsed["values"] = {
        **{key: value for key, value in previous.get("values", {}).items() if key in inactive},
        **parsed["values"],
    }
    if not {"counts", "payments", "cash_total", "cash_mode"} & data.model_fields_set:
        for key in ("values", "counts", "cash_mode"):
            if key in previous:
                parsed[key] = previous[key]
    if "reason" not in data.model_fields_set:
        parsed["reason"] = previous.get("reason", "")
    counting = {**previous, **parsed}
    stamp = now().isoformat()
    if data.realized is not None:
        previous_realized = realized_for(session, previous)
        if set(data.realized.values) - (allowed | inactive):
            raise HTTPException(422, "Meio de pagamento inválido no realizado do dia.")
        values = (
            {key: value for key, value in previous_realized.get("values", {}).items() if key in inactive}
            if "values" in data.realized.model_fields_set
            else previous_realized.get("values", {})
        )
        for key, text in data.realized.values.items():
            if key not in allowed:
                continue
            if text is None or not text.strip():
                values[key] = None
            else:
                value = text.strip()
                values[key] = (
                    -parse_money(value[1:], True)
                    if value.startswith("-")
                    else parse_money(value, True)
                )
        debits = previous_realized.get("debits", {})
        if data.realized.debits is not None:
            if set(data.realized.debits) - (allowed | inactive):
                raise HTTPException(422, "Meio de pagamento inválido nos débitos do dia.")
            debits = {key: value for key, value in previous_realized.get("debits", {}).items() if key in inactive}
            debits.update({
                key: None if text is None or not text.strip() else parse_money(text.strip(), True)
                for key, text in data.realized.debits.items() if key in allowed
            })
        credits = previous_realized.get("credits", {})
        if data.realized.credits is not None:
            if set(data.realized.credits) - (allowed | inactive):
                raise HTTPException(422, "Meio de pagamento inválido nos créditos do dia.")
            credits = {key: value for key, value in previous_realized.get("credits", {}).items() if key in inactive}
            credits.update({
                key: None if text is None or not text.strip() else parse_money(text.strip(), True)
                for key, text in data.realized.credits.items() if key in allowed
            })
        counting["realized"] = {
            "values": values,
            "debits": debits,
            "credits": credits,
            "notes": data.realized.notes
            if "notes" in data.realized.model_fields_set
            else previous_realized.get("notes", ""),
            "pos_reference": data.realized.pos_reference
            if "pos_reference" in data.realized.model_fields_set
            else previous_realized.get("pos_reference", ""),
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
        register = db.get(Register, session.register_id)
        if "numerario" not in counting["values"] and json.loads(register.settings or "{}").get("count_required", True):
            raise HTTPException(
                422, "Indique o numerário contado, incluindo zero quando a gaveta estiver vazia."
            )
    if closing:
        setup = db.get(CompanySetup, "default")
        require_reason = json.loads(setup.settings or "{}").get("preferences", {}).get("require_reason", True) if setup else True
        if (
            any(row["count_difference_cents"] not in (None, 0) for row in result["rows"])
            and len(data.reason.strip()) < 5
            and require_reason
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
