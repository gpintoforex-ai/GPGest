"""Post only unrecorded local closing totals; never post count differences."""
import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .cash_sheet import belongs_to_register, daily_enterprise, day, payment_key, real_receipts
from .models import CashSession, Category, Movement, PaymentMethodBankDefault, now, uid

ORIGIN = "fecho_local"


def default_bank(db, store_id, payment):
    if payment == "numerario":
        return None
    configured = db.get(PaymentMethodBankDefault, (payment, store_id))
    return configured.account_id if configured else None


def debit_parts(db, session, register, rows, association):
    """Use declared supplier amounts only where the payment attribution is known."""
    allocations = association.get("allocations", [])
    methods = {row["key"] for row in rows if row["debits_cents"] > 0}
    names = defaultdict(set)
    for allocation in allocations:
        names[allocation["name"].strip().casefold()].add(allocation["id"])
    registered = defaultdict(int)
    selected = session.sheet_day or day(session.opened_at)
    timezone_name = json.loads(register.settings or "{}").get("timezone", "Europe/Lisbon")
    start = datetime.combine(datetime.fromisoformat(selected).date(), time.min, tzinfo=ZoneInfo(timezone_name))
    end = start + timedelta(days=1)

    def account(payment, entity, amount):
        identities = names.get((entity or "").strip().casefold(), set())
        if len(identities) == 1:
            registered[payment, next(iter(identities))] += amount

    if allocations:
        for move in db.scalars(select(Movement).join(CashSession).where(
            CashSession.register_id == register.id, Movement.direction == -1,
            Movement.occurred_at >= start, Movement.occurred_at < end,
            Movement.kind.in_(("despesa", "fornecedor")), Movement.transfer_id.is_(None),
            Movement.reversed_by.is_(None), Movement.reversal_of.is_(None),
            Movement.origin.notin_(("fecho_caixa", "deposito_modulo")),
        )):
            account(move.payment, move.entity, move.amount_cents)
        for item in real_receipts(db, register.store_id, selected, daily_enterprise(db, session), timezone_name):
            if item.get("source_kind") == "cash_movement" and item.get("direction") == -1 and belongs_to_register(db, session, register, item):
                key = "mbway" if str(item.get("payment", "")).casefold().replace(" ", "") == "mbway" else payment_key(item)[0]
                account(key, item.get("entity"), item["amount_cents"])
    result = {}
    for row in rows:
        key = row["key"]
        missing = max(0, row["debits_cents"] - row["registered_debits_cents"])
        if not missing:
            result[key] = []
            continue
        if not allocations:
            result[key] = [{"name": association.get("name", ""), "amount_cents": missing, "id": association.get("id")}]
            continue
        chosen = [a for a in allocations if a.get("payment") == key or (not a.get("payment") and len(methods) == 1)]
        parts = [{**a, "amount_cents": max(0, a["amount_cents"] - registered[key, a["id"]])} for a in chosen]
        parts = [part for part in parts if part["amount_cents"]]
        unidentified = row["registered_debits_cents"] - sum(registered[key, a["id"]] for a in chosen)
        full_single = len(chosen) == 1 and chosen[0]["amount_cents"] == row["debits_cents"]
        if full_single:
            parts = [{**chosen[0], "amount_cents": missing}]
        if (unidentified > 0 and not full_single) or sum(part["amount_cents"] for part in parts) > missing:
            # Keep ambiguous existing payments intact; never guess their supplier.
            parts = []
        remaining = missing - sum(part["amount_cents"] for part in parts)
        if remaining:
            parts.append({"name": "", "id": None, "amount_cents": remaining})
        result[key] = parts
    return result


def supplier_views(db, movements):
    """Expand an old single-method grouped close for display, without reposting it."""
    session_ids = {m.session_id for m in movements if m.origin == ORIGIN and m.kind == "fornecedor"}
    if not session_ids:
        return {}
    grouped = defaultdict(list)
    for move in db.scalars(select(Movement).where(
        Movement.session_id.in_(session_ids), Movement.origin == ORIGIN, Movement.kind == "fornecedor",
        Movement.reversed_by.is_(None), Movement.reversal_of.is_(None),
    )):
        grouped[move.session_id].append(move)
    result = {}
    for session in db.scalars(select(CashSession).where(CashSession.id.in_(session_ids), CashSession.closed_at.is_not(None))):
        stored = json.loads(session.count_data or "{}")
        for part in stored.get("posted_movements", {}).get("supplier_payments", []):
            if not part.get("supplier_id") and not part.get("name"):
                result[part["movement_id"]] = {"supplier_unassigned": True}
        group = grouped[session.id]
        if len(group) != 1 or "supplier_payments" in stored.get("posted_movements", {}):
            continue
        association = stored.get("debit_supplier", {})
        allocations = association.get("allocations", [])
        if not allocations:
            continue
        parts = [{"supplier_id": a["id"], "name": a["name"], "amount_cents": a["amount_cents"]} for a in allocations]
        if association.get("unassigned_cents", 0):
            parts.append({"supplier_id": None, "name": "", "amount_cents": association["unassigned_cents"]})
        if sum(p["amount_cents"] for p in parts) == group[0].amount_cents and len(parts) > 1:
            result[group[0].id] = {"supplier_parts": parts}
    return result


def post_close(db, session, register, reconciliation, counting, user, record):
    selected = session.sheet_day or day(session.opened_at)
    stamp = now()
    timezone_name = json.loads(register.settings or "{}").get("timezone", "Europe/Lisbon")
    occurred = stamp if day(stamp, timezone_name) == selected else datetime.combine(
        datetime.fromisoformat(selected).date(), time(12), tzinfo=ZoneInfo(timezone_name)
    )
    association = counting.get("debit_supplier", {})
    allocations = association.get("allocations", [])
    parts = debit_parts(db, session, register, reconciliation["rows"], association)
    created = []
    supplier_payments = []

    def add(row, amount, direction, kind, category_name, description, supplier=None):
        if not amount:
            return
        db.execute(insert(Category).values(id=uid(), name=category_name).on_conflict_do_nothing(index_elements=[Category.name]))
        category_id = db.scalar(select(Category.id).where(Category.name == category_name))
        move = Movement(
            session_id=session.id, user_id=user.id, category_id=category_id,
            bank_account_id=default_bank(db, register.store_id, row["key"]),
            kind=kind, payment=row["key"], amount_cents=amount, direction=direction,
            occurred_at=occurred, origin=ORIGIN, request_key=uid(), reference=session.id,
            document=counting.get("realized", {}).get("pos_reference") or f"Fecho de caixa {selected}",
            entity=supplier["name"] if supplier else "",
            notes=f"{description} · {register.name} · {selected}. Apenas o montante ainda não registado."
                  + (" Repartição dos fornecedores guardada na folha de caixa." if kind == "fornecedor" and allocations else ""),
        )
        db.add(move)
        db.flush()
        created.append(move.id)
        if supplier is not None:
            supplier_payments.append({"movement_id": move.id, "supplier_id": supplier.get("id"), "name": supplier["name"], "payment": row["key"], "amount_cents": amount})
        record(db, user, "movimento_fecho_local", move.id, {
            "session_id": session.id, "amount_cents": amount, "direction": direction,
            "payment": move.payment, "bank_account_id": move.bank_account_id,
            **({"supplier_id": supplier.get("id"), "supplier_name": supplier["name"]} if supplier else {}),
        }, register.store_id)

    for row in reconciliation["rows"]:
        declared = row["declared_cents"]
        # A lower declaration never cancels/reduces an existing receipt. Refund
        # declarations are negative and can produce only additional refunds.
        if declared > 0:
            add(row, max(0, declared - row["registered_cents"]), 1, "venda", "Vendas", "Faturado do dia")
        elif declared < 0:
            add(row, max(0, row["registered_cents"] - declared), -1, "reembolso", "Vendas", "Reembolsos do dia")
        add(row, max(0, row["other_credits_cents"] - row["registered_credits_cents"]),
            1, "entrada_extra", "Créditos de caixa", "Créditos do dia")
        for part in parts[row["key"]]:
            add(row, part["amount_cents"], -1, "fornecedor", "Débitos de caixa", "Pagamento a fornecedor", part)
    counting["posted_movements"] = {"ids": created, "posted_at": stamp.isoformat(), "posted_by": user.id, "supplier_payments": supplier_payments}
    return created


def reverse_close(db, session, register, user, reason, record):
    from .cash_local import methods
    from .reconciliation import identified, used

    moves = list(db.scalars(select(Movement).where(
        Movement.session_id == session.id, Movement.origin == ORIGIN,
        Movement.reversed_by.is_(None), Movement.reversal_of.is_(None),
        Movement.payment.in_(methods(db, active_only=True)),
    ).with_for_update()))
    later = db.scalars(select(CashSession).where(
        CashSession.register_id == register.id, CashSession.closed_at > session.closed_at,
    )) if moves else []
    if any((other.sheet_day or day(other.opened_at)) == (session.sheet_day or day(session.opened_at)) for other in later):
        raise HTTPException(409, "Existe um fecho posterior da mesma caixa e dia. Reabra esse fecho para rever os valores.")
    for move in moves:
        if used(db, "movement", move.id) or identified(db, "movement", move.id):
            raise HTTPException(409, "Desfaça a reconciliação bancária e a associação aos documentos dos movimentos deste fecho antes de reabrir a caixa.")
    for move in moves:
        reversal = Movement(
            session_id=session.id, user_id=user.id, category_id=move.category_id,
            bank_account_id=move.bank_account_id, kind=move.kind, payment=move.payment,
            amount_cents=move.amount_cents, direction=-move.direction, occurred_at=move.occurred_at,
            origin=ORIGIN, request_key=uid(), reference=session.id, document=move.document,
            entity=move.entity, notes=f"Reabertura: {reason}", reversal_of=move.id,
        )
        db.add(reversal)
        db.flush()
        move.reversed_by = reversal.id
        record(db, user, "estorno_fecho_local", move.id, {"session_id": session.id, "reversal": reversal.id, "reason": reason}, register.store_id)
