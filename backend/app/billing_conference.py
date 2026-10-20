"""Faturação, movimentos reais e conferência como três conceitos separados.

* Faturação: documentos de venda importados do SAF-T (WintouchSaleRecord kind=saft_invoice).
  Nunca são dinheiro: não entram em caixa, bancos nem movimentos reais.
* Movimentos reais: recebimentos efetivamente registados (caixas, fechos locais, lançamentos
  manuais e registos bancários aprovados).
* Conferência: associações explícitas entre os dois. Associar nunca cria movimentos; só a ação
  «Rever possíveis recebimentos em falta», com confirmação, regista um recebimento novo.

Todos os valores em cêntimos inteiros; dias em Europe/Lisbon.
"""

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .cash_sheet import day as local_day
from .db import db_session
from .models import (
    BankAccount,
    BankBookEntry,
    BillingConference,
    BillingConferenceLine,
    CashSession,
    Category,
    CompanySetup,
    Movement,
    PaymentTerminal,
    Register,
    WintouchSaleRecord,
    uid,
)
from .operating_modes import policy
from .saft_parser import INVOICE_TYPES
from .schemas import Strict

router = APIRouter()
LISBON = ZoneInfo("Europe/Lisbon")
RECEIPT_KINDS = ("venda", "cliente", "reembolso")
STATUS_LABELS = {"A": "Anulado", "F": "Faturado (documento de origem)", "R": "Resumo", "S": "Autofaturação"}
MAX_PERIOD_DAYS = 366


def fail(message, status=422):
    raise HTTPException(status, message)


# ---------------------------------------------------------------- leituras de base


def saft_source(db):
    """Identificador SAF-T da empresa ou None quando o NIF não está configurado."""
    nif = str(db.info.get("company", {}).get("nif") or "").strip()
    return "saft:" + nif if len(nif) == 9 and nif.isdigit() else None


def payment_mapping(db):
    from .cash_local import methods

    settings = db.get(CompanySetup, "default")
    mapping = json.loads(settings.settings or "{}").get("saft_payment_mapping", {}) if settings else {}
    names = methods(db)
    return {code: key for code, key in mapping.items() if key in names}, names


def documents(db, stores, start, end):
    """Documentos de venda SAF-T do período (data do documento), com sentido e estado."""
    source = saft_source(db)
    if not source or not stores:
        return []
    mapping, names = payment_mapping(db)
    result = []
    for row in db.scalars(
        select(WintouchSaleRecord).where(
            WintouchSaleRecord.enterprise_id == source,
            WintouchSaleRecord.store_id.in_(stores),
            WintouchSaleRecord.kind == "saft_invoice",
            WintouchSaleRecord.active.is_(True),
            WintouchSaleRecord.document_date >= start,
            WintouchSaleRecord.document_date <= end,
        )
    ):
        data = json.loads(row.data)
        eligible = data.get("status") == "N" and data.get("type") in INVOICE_TYPES
        result.append(
            {
                "id": row.id,
                "store_id": row.store_id,
                "document": data["document"],
                "type": data["type"],
                "date": data["date"],
                "customer_id": data.get("customer_id", ""),
                "status": data.get("status", ""),
                "status_label": "Normal" if data.get("status") == "N" else STATUS_LABELS.get(data.get("status"), "Excluído"),
                "eligible": eligible,
                "direction": data.get("direction", 1) or 1,
                "gross_cents": data["gross_cents"],
                "net_cents": data["net_cents"],
                "tax_cents": data["tax_cents"],
                # Informação de pagamento do ficheiro: apenas apoio à conferência, nunca um recebimento.
                "saft_payments": [
                    {
                        "index": index,
                        "code": payment["code"],
                        "method": mapping.get(payment["code"]),
                        "method_name": names.get(mapping.get(payment["code"]), "Por classificar"),
                        "date": payment.get("date") or "",
                        "amount_cents": payment["amount_cents"],
                    }
                    for index, payment in enumerate(data.get("payments", []))
                ],
                "import_id": data.get("import_id"),
            }
        )
    return sorted(result, key=lambda item: (item["date"], item["document"]))


def bounds(start, end):
    first = datetime.combine(date.fromisoformat(start), time.min, tzinfo=LISBON)
    last = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, tzinfo=LISBON)
    return first, last


def movement_store(db, move, registers):
    if move.session_id:
        session = db.get(CashSession, move.session_id)
        register = registers.get(session.register_id) if session else None
        return (register.store_id if register else None), (register.id if register else None)
    if move.bank_account_id:
        account = db.get(BankAccount, move.bank_account_id)
        return (account.store_id if account else None), None
    return None, None


def receipts(db, stores, start, end):
    """Recebimentos (e reembolsos) efetivamente registados no período, pela data do movimento."""
    first, last = bounds(start, end)
    registers = {row.id: row for row in db.scalars(select(Register))}
    result = []
    for move in db.scalars(
        select(Movement).where(
            Movement.kind.in_(RECEIPT_KINDS),
            Movement.reversed_by.is_(None),
            Movement.reversal_of.is_(None),
            Movement.transfer_id.is_(None),
            Movement.occurred_at >= first,
            Movement.occurred_at < last,
        )
    ):
        store, register = movement_store(db, move, registers)
        if store not in stores:
            continue
        result.append(
            {
                "source": "movement",
                "id": move.id,
                "store_id": store,
                "register_id": register,
                "register_name": registers[register].name if register else "",
                "date": local_day(move.occurred_at),
                "payment": move.payment,
                "kind": move.kind,
                "origin": move.origin,
                "direction": move.direction,
                "amount_cents": move.amount_cents,
                "document": move.document,
                "reference": move.reference,
                "entity": move.entity,
                "notes": move.notes,
                "aggregate": move.origin == "fecho_local",
            }
        )
    accounts = {row.id: row for row in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores)))}
    reversed_ids = set(db.scalars(select(BankBookEntry.original_id).where(BankBookEntry.original_id.is_not(None))))
    for entry in db.scalars(
        select(BankBookEntry).where(
            BankBookEntry.kind == "receipt",
            BankBookEntry.account_id.in_(accounts),
            BankBookEntry.movement_date >= start,
            BankBookEntry.movement_date <= end,
        )
    ):
        if entry.id in reversed_ids:
            continue
        account = accounts[entry.account_id]
        result.append(
            {
                "source": "book",
                "id": entry.id,
                "store_id": account.store_id,
                "register_id": None,
                "register_name": f"Banco · {account.name}",
                "date": entry.movement_date,
                "payment": "transferencia",
                "kind": "cliente",
                "origin": "banco",
                "direction": 1 if entry.amount_cents > 0 else -1,
                "amount_cents": abs(entry.amount_cents),
                "document": "",
                "reference": entry.reference,
                "entity": "",
                "notes": entry.description,
                "aggregate": False,
            }
        )
    return sorted(result, key=lambda item: (item["date"], item["id"]))


def conference_usage(db):
    """Valores já justificados (só conferências individuais ativas) e documentos em totais diários."""
    used = defaultdict(int)
    for side, source, source_id, amount in db.execute(
        select(
            BillingConferenceLine.side,
            BillingConferenceLine.source,
            BillingConferenceLine.source_id,
            func.sum(BillingConferenceLine.amount_cents),
        )
        .join(BillingConference)
        .where(BillingConference.undone_at.is_(None), BillingConference.kind == "individual")
        .group_by(BillingConferenceLine.side, BillingConferenceLine.source, BillingConferenceLine.source_id)
    ):
        used[side, source, source_id] = int(amount)
    aggregated = {
        source_id: conference_id
        for source_id, conference_id in db.execute(
            select(BillingConferenceLine.source_id, BillingConference.id)
            .join(BillingConference)
            .where(
                BillingConference.undone_at.is_(None),
                BillingConference.kind == "agregada",
                BillingConferenceLine.side == "document",
            )
        )
    }
    return used, aggregated


def linked(db, source, source_id):
    """Valor de um recebimento ligado a documentos em conferências ativas."""
    return int(
        db.scalar(
            select(func.coalesce(func.sum(BillingConferenceLine.amount_cents), 0))
            .join(BillingConference)
            .where(
                BillingConference.undone_at.is_(None),
                BillingConferenceLine.side == "receipt",
                BillingConferenceLine.source == source,
                BillingConferenceLine.source_id == source_id,
            )
        )
        or 0
    )


def annotate(db, docs, recs):
    used, aggregated = conference_usage(db)
    for doc in docs:
        doc["justified_cents"] = min(doc["gross_cents"], used["document", "saft", doc["id"]])
        doc["remaining_cents"] = doc["gross_cents"] - doc["justified_cents"] if doc["eligible"] else 0
        doc["aggregate_conference_id"] = aggregated.get(doc["id"])
        if not doc["eligible"]:
            doc["state"] = "excluido"
        elif not doc["remaining_cents"]:
            doc["state"] = "justificado"
        elif doc["justified_cents"]:
            doc["state"] = "parcial"
        elif doc["aggregate_conference_id"]:
            doc["state"] = "conferido_agregado"
        else:
            doc["state"] = "por_conferir"
    for rec in recs:
        rec["linked_cents"] = min(rec["amount_cents"], used["receipt", rec["source"], rec["id"]])
        rec["remaining_cents"] = rec["amount_cents"] - rec["linked_cents"]
    return docs, recs


def criteria(doc, rec):
    """Critérios objetivos entre um documento e um recebimento (o valor isolado não basta)."""
    reasons, evidence = [], False
    number = doc["document"].replace(" ", "").casefold()
    haystack = " ".join([rec.get("document", ""), rec.get("reference", ""), rec.get("notes", "")]).replace(" ", "").casefold()
    if number and number in haystack:
        reasons.append("Número do documento indicado no recebimento")
        evidence = True
    for payment in doc["saft_payments"]:
        if payment["date"] and payment["date"] == rec["date"] and payment["method"] and payment["method"] == rec["payment"]:
            reasons.append("Data e meio iguais ao pagamento indicado no SAF-T")
            evidence = True
            break
    if rec["date"] == doc["date"]:
        reasons.append("Recebimento na data do documento")
    elif rec["date"] > doc["date"]:
        reasons.append(f"Recebimento {(date.fromisoformat(rec['date']) - date.fromisoformat(doc['date'])).days} dias após o documento")
    else:
        reasons.append("Recebimento anterior ao documento (possível adiantamento)")
    if rec.get("aggregate"):
        reasons.append("Movimento agregado do fecho de caixa")
    return reasons, evidence


# ---------------------------------------------------------------- âmbito e permissões


def scope_for(db, user, store_id, start, end):
    if start > end or (end - start).days > MAX_PERIOD_DAYS:
        fail("Selecione um período válido, até um ano.")
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        if store_id not in stores:
            fail("Estabelecimento não disponível.", 404)
        stores = [store_id]
    return stores, str(start), str(end)


def manager(user, db, store_id=None):
    authorize(user, db, store_id, ["administrador", "gestor"])


def lock(db):
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(current_schema() || :key))"), {"key": "billing-conference"})


# ---------------------------------------------------------------- indicadores


def deferred_settlement(db, move, terminals=None):
    """TPA (e MB WAY configurado) fica pendente até a liquidação ser associada ao banco."""
    terminals = terminals if terminals is not None else list(db.scalars(select(PaymentTerminal).where(PaymentTerminal.active.is_(True))))
    terminal = next((t for t in terminals if t.id == move.terminal_id), None)
    if move.payment == "cartao":
        return not (terminal and terminal.settlement_days == 0)
    if move.payment == "mbway":
        if terminal:
            return terminal.mbway and terminal.settlement_days > 0
        return any(t.mbway and t.settlement_days > 0 for t in terminals)
    return False


def payment_list(db, stores, start, end):
    """Pagamentos externos do período (fornecedores, despesas, saídas e encargos bancários), datados.

    Exclui transferências internas, estornos e movimentos estornados.
    """
    first, last = bounds(start, end)
    registers = {row.id: row for row in db.scalars(select(Register))}
    result = []
    for move in db.scalars(
        select(Movement).where(
            Movement.direction == -1,
            Movement.kind.in_(("fornecedor", "despesa", "saida_caixa")),
            Movement.reversed_by.is_(None),
            Movement.reversal_of.is_(None),
            Movement.transfer_id.is_(None),
            Movement.occurred_at >= first,
            Movement.occurred_at < last,
        )
    ):
        if movement_store(db, move, registers)[0] in stores:
            result.append({"date": local_day(move.occurred_at), "amount_cents": move.amount_cents, "source": "movement"})
    accounts = list(db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores))))
    reversed_ids = set(db.scalars(select(BankBookEntry.original_id).where(BankBookEntry.original_id.is_not(None))))
    for entry in db.scalars(
        select(BankBookEntry).where(
            BankBookEntry.account_id.in_([a.id for a in accounts]),
            BankBookEntry.kind.in_(("payment", "fee")),
            BankBookEntry.movement_date >= start,
            BankBookEntry.movement_date <= end,
        )
    ):
        if entry.id not in reversed_ids:
            result.append({"date": entry.movement_date, "amount_cents": abs(entry.amount_cents), "source": "book"})
    return result


def indicators(db, stores, start, end, docs=None, recs=None):
    from .bank_balances import allocations

    docs = docs if docs is not None else documents(db, stores, start, end)
    recs = recs if recs is not None else receipts(db, stores, start, end)
    annotate(db, docs, recs)
    eligible = [d for d in docs if d["eligible"]]
    billing_gross = sum(d["gross_cents"] * d["direction"] for d in eligible)
    billing_net = sum(d["net_cents"] * d["direction"] for d in eligible)
    receipts_in = sum(r["amount_cents"] for r in recs if r["direction"] > 0)
    refunds = sum(r["amount_cents"] for r in recs if r["direction"] < 0)
    payments = sum(item["amount_cents"] for item in payment_list(db, stores, start, end))
    accounts = list(db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores))))
    # Saldos: caixas abertas e livro bancário (com TPA por liquidar separado).
    from .main import balance

    cash = sum(
        balance(db, session)
        for session in db.scalars(
            select(CashSession).join(Register).where(Register.store_id.in_(stores), CashSession.closed_at.is_(None))
        )
    )
    from .bank_balances import summary

    allocated = allocations(db)
    banks = [summary(db, account, start, end, allocated) for account in accounts]
    pending = pending_settlement(db, stores, end, allocated)
    return {
        "period": {"start": start, "end": end},
        "billing": {
            "available": saft_source(db) is not None,
            "net_cents": billing_net,
            "gross_cents": billing_gross,
            "documents": len(eligible),
            "credit_notes_cents": sum(d["gross_cents"] for d in eligible if d["direction"] < 0),
            "excluded_documents": len(docs) - len(eligible),
            "basis": "Documentos SAF-T normais (FT, FS, FR, ND, NC) pela data do documento; notas de crédito subtraem; anulados e outros estados excluídos.",
        },
        "receipts": {
            "received_cents": receipts_in,
            "refunds_cents": refunds,
            "net_cents": receipts_in - refunds,
            "basis": "Recebimentos e reembolsos registados nas caixas, fechos e registos bancários aprovados, pela data do movimento. Não inclui faturação.",
        },
        "payments_cents": payments,
        "balances": {
            "cash_open_sessions_cents": cash,
            "bank_book_cents": sum(b["book_balance_cents"] or 0 for b in banks if b["book_balance_cents"] is not None),
            "bank_accounts_without_opening": sum(b["book_balance_cents"] is None for b in banks),
            "card_pending_settlement_cents": pending["amount_cents"],
            "card_pending_settlement_count": pending["count"],
        },
        "conference": {
            "justified_cents": sum(d["justified_cents"] * d["direction"] for d in eligible),
            "aggregate_cents": sum(d["remaining_cents"] * d["direction"] for d in eligible if d["state"] == "conferido_agregado"),
            "pending_cents": sum(d["remaining_cents"] * d["direction"] for d in eligible if d["state"] in ("por_conferir", "parcial")),
            "pending_documents": sum(d["state"] in ("por_conferir", "parcial") for d in eligible),
        },
        # Mesma base: faturação com IVA vs recebimentos líquidos de reembolsos (também com IVA).
        "to_justify_cents": billing_gross - (receipts_in - refunds) if saft_source(db) else None,
        "to_justify_note": "Por justificar/conferir: pode resultar de vendas a crédito, recebimentos de períodos anteriores, adiantamentos, diferenças de datas ou dados em falta. Não é, por si só, dívida de clientes nem erro de caixa.",
    }


def pending_settlement(db, stores, end, allocated=None):
    """Recebimentos TPA/MB WAY já registados que ainda aguardam liquidação no banco."""
    from .bank_balances import allocations

    allocated = allocations(db) if allocated is None else allocated
    terminals = list(db.scalars(select(PaymentTerminal).where(PaymentTerminal.active.is_(True))))
    registers = {row.id: row for row in db.scalars(select(Register))}
    total, count = 0, 0
    for move in db.scalars(
        select(Movement).where(
            Movement.payment.in_(("cartao", "mbway")),
            Movement.direction == 1,
            Movement.kind.in_(("venda", "cliente")),
            Movement.reversed_by.is_(None),
            Movement.reversal_of.is_(None),
        )
    ):
        if local_day(move.occurred_at) > end or not deferred_settlement(db, move, terminals):
            continue
        if movement_store(db, move, registers)[0] not in stores:
            continue
        remaining = move.amount_cents - min(move.amount_cents, allocated.get(("movement", move.id), 0))
        if remaining:
            total += remaining
            count += 1
    return {"amount_cents": total, "count": count}


# ---------------------------------------------------------------- totais diários


def daily(db, docs, recs, stores):
    """Por dia, estabelecimento e meio: pagamentos indicados no SAF-T vs recebimentos registados."""
    rows = defaultdict(lambda: {"saft_payments_cents": 0, "received_cents": 0, "documents": set(), "receipts": []})
    billing = defaultdict(int)
    for doc in docs:
        if not doc["eligible"]:
            continue
        billing[doc["date"], doc["store_id"]] += doc["gross_cents"] * doc["direction"]
        for payment in doc["saft_payments"]:
            if payment["date"]:
                row = rows[payment["date"], doc["store_id"], payment["method"] or "?"]
                row["saft_payments_cents"] += payment["amount_cents"] * doc["direction"]
                row["documents"].add(doc["id"])
    for rec in recs:
        row = rows[rec["date"], rec["store_id"], rec["payment"]]
        row["received_cents"] += rec["amount_cents"] * rec["direction"]
        row["receipts"].append(rec["source"] + ":" + rec["id"])
    confirmed = {
        (c.day, c.store_id, c.payment or ""): c.id
        for c in db.scalars(
            select(BillingConference).where(
                BillingConference.kind == "agregada",
                BillingConference.undone_at.is_(None),
                BillingConference.store_id.in_(stores),
            )
        )
    }
    from .cash_local import methods

    names = methods(db)
    result = []
    for (day, store, method), row in sorted(rows.items()):
        result.append(
            {
                "date": day,
                "store_id": store,
                "payment": None if method == "?" else method,
                "payment_name": "Por classificar" if method == "?" else names.get(method, method),
                "billing_day_cents": billing.get((day, store), 0),
                "saft_payments_cents": row["saft_payments_cents"],
                "received_cents": row["received_cents"],
                "difference_cents": row["received_cents"] - row["saft_payments_cents"],
                "documents": len(row["documents"]),
                "receipts": len(row["receipts"]),
                "conference_id": confirmed.get((day, store, "" if method == "?" else method)),
            }
        )
    return result


# ---------------------------------------------------------------- sugestões e em falta


def suggestions(docs, recs):
    """Propostas com critérios visíveis; nunca confirmadas automaticamente."""
    result = []
    pending_recs = [r for r in recs if r["remaining_cents"] > 0]
    for doc in docs:
        if doc["state"] not in ("por_conferir", "parcial", "conferido_agregado") or not doc["remaining_cents"]:
            continue
        for rec in pending_recs:
            if rec["store_id"] != doc["store_id"] or rec["direction"] != doc["direction"] or rec["aggregate"]:
                continue
            reasons, evidence = criteria(doc, rec)
            if not evidence or rec["remaining_cents"] < doc["remaining_cents"] and "Número do documento indicado no recebimento" not in reasons:
                continue
            amount = min(doc["remaining_cents"], rec["remaining_cents"])
            result.append(
                {
                    "kind": "individual",
                    "documents": [{"record_id": doc["id"], "document": doc["document"], "amount_cents": amount}],
                    "receipts": [{"source": rec["source"], "id": rec["id"], "amount_cents": amount}],
                    "reasons": reasons + (["Valor igual ao remanescente"] if rec["remaining_cents"] == doc["remaining_cents"] else ["Associação parcial"]),
                    "confidence": "Alta" if len(reasons) >= 3 else "Média",
                }
            )
    # Vários documentos → um movimento agregado do fecho, pelo meio e data dos pagamentos SAF-T.
    for rec in pending_recs:
        if not rec["aggregate"] or rec["direction"] < 0:
            continue
        group = []
        for doc in docs:
            if doc["store_id"] != rec["store_id"] or doc["direction"] < 0 or not doc["remaining_cents"] or doc["state"] == "justificado":
                continue
            for payment in doc["saft_payments"]:
                if payment["date"] == rec["date"] and payment["method"] == rec["payment"]:
                    group.append((doc, min(payment["amount_cents"], doc["remaining_cents"])))
                    break
        total = sum(amount for _, amount in group)
        if len(group) >= 1 and 0 < total <= rec["remaining_cents"]:
            result.append(
                {
                    "kind": "individual",
                    "documents": [{"record_id": d["id"], "document": d["document"], "amount_cents": a} for d, a in group],
                    "receipts": [{"source": rec["source"], "id": rec["id"], "amount_cents": total}],
                    "reasons": [
                        "Movimento agregado do fecho de caixa",
                        f"{len(group)} documentos com pagamento SAF-T na mesma data e meio",
                        "Soma igual ao movimento" if total == rec["remaining_cents"] else "Soma inferior ao movimento; o remanescente fica por associar",
                    ],
                    "confidence": "Média",
                }
            )
    return result[:200]


def missing_receipts(db, stores, docs, recs):
    """Pagamentos indicados no SAF-T sem recebimento correspondente encontrado."""
    from .cash_local import methods

    names = methods(db, active_only=True)
    by_day_method = defaultdict(int)
    saft_by_day_method = defaultdict(int)
    for rec in recs:
        by_day_method[rec["date"], rec["store_id"], rec["payment"]] += rec["amount_cents"] * rec["direction"]
    for doc in docs:
        if doc["eligible"]:
            for payment in doc["saft_payments"]:
                if payment["date"] and payment["method"]:
                    saft_by_day_method[payment["date"], doc["store_id"], payment["method"]] += payment["amount_cents"] * doc["direction"]
    aggregates = {
        (c.day, c.store_id, c.payment): c.id
        for c in db.scalars(select(BillingConference).where(BillingConference.kind == "agregada", BillingConference.undone_at.is_(None)))
    }
    open_sessions = defaultdict(list)
    for session, register in db.execute(
        select(CashSession, Register).join(Register).where(Register.store_id.in_(stores), CashSession.closed_at.is_(None))
    ):
        open_sessions[register.store_id].append({"id": session.id, "register": register.name, "day": session.sheet_day or local_day(session.opened_at)})
    result = []
    for doc in docs:
        if not doc["eligible"] or doc["direction"] < 0 or doc["state"] == "justificado":
            continue
        for payment in doc["saft_payments"]:
            missing, warnings = [], []
            if not payment["date"]:
                missing.append("Data de pagamento não indicada no SAF-T (a data da fatura não é usada como data de recebimento).")
            if not payment["method"]:
                missing.append(f"Código SAF-T {payment['code']} sem meio de pagamento associado (não se assume numerário).")
            elif payment["method"] not in names:
                missing.append("Meio de pagamento inativo.")
            amount = min(payment["amount_cents"], doc["remaining_cents"])
            if payment["date"] and payment["method"]:
                key = (payment["date"], doc["store_id"], payment["method"])
                if by_day_method[key] >= saft_by_day_method[key] and by_day_method[key] > 0:
                    warnings.append("O total registado nesse dia e meio cobre os pagamentos SAF-T: o valor deve já estar incluído num recebimento agregado da caixa.")
                elif by_day_method[key] > 0:
                    warnings.append(f"Existem recebimentos nesse dia e meio ({by_day_method[key] / 100:.2f} €); confirme se este pagamento não está incluído.".replace(".", ",", 1))
                if aggregates.get(key):
                    warnings.append("Esse dia e meio já tem uma conferência agregada confirmada.")
            candidates = [
                r["source"] + ":" + r["id"]
                for r in recs
                if r["store_id"] == doc["store_id"] and r["direction"] > 0 and r["remaining_cents"] >= amount and criteria(doc, r)[1]
            ]
            if candidates:
                warnings.append(f"{len(candidates)} recebimento(s) existente(s) compatível(is): associe em vez de criar.")
            if doc["justified_cents"]:
                warnings.append("O documento já tem valores justificados por associação.")
            result.append(
                {
                    "record_id": doc["id"],
                    "payment_index": payment["index"],
                    "document": doc["document"],
                    "document_date": doc["date"],
                    "store_id": doc["store_id"],
                    "payment_date": payment["date"],
                    "code": payment["code"],
                    "method": payment["method"],
                    "method_name": payment["method_name"],
                    "amount_cents": amount,
                    "missing": missing,
                    "warnings": warnings,
                    "candidates": candidates,
                    "proposable": not missing and amount > 0,
                    "destinations": open_sessions.get(doc["store_id"], []),
                }
            )
    return result


# ---------------------------------------------------------------- duplicados históricos


def duplicates(db, stores):
    """Possíveis duplicações; apenas pré-visualização, nunca corrigidas automaticamente."""
    from .reconciliation import identified, used

    registers = {row.id: row for row in db.scalars(select(Register))}
    result = []
    moves = [
        m
        for m in db.scalars(
            select(Movement).where(
                Movement.direction == 1,
                Movement.kind.in_(("venda", "cliente")),
                Movement.reversed_by.is_(None),
                Movement.reversal_of.is_(None),
                Movement.transfer_id.is_(None),
            )
        )
        if movement_store(db, m, registers)[0] in stores
    ]

    def info(move):
        session = db.get(CashSession, move.session_id) if move.session_id else None
        return {
            "source": "movement",
            "id": move.id,
            "date": local_day(move.occurred_at),
            "amount_cents": move.amount_cents,
            "payment": move.payment,
            "origin": move.origin,
            "document": move.document,
            "description": move.notes or move.entity or move.kind,
            "reconciled": bool(used(db, "movement", move.id) or identified(db, "movement", move.id)),
            "conference_cents": linked(db, "movement", move.id),
            "closed": bool(session and session.closed_at),
        }

    # 1. Crédito bancário aprovado como recebimento que pode ser liquidação TPA já registada.
    accounts = {a.id: a for a in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(stores)))}
    reversed_ids = set(db.scalars(select(BankBookEntry.original_id).where(BankBookEntry.original_id.is_not(None))))
    terminals = list(db.scalars(select(PaymentTerminal).where(PaymentTerminal.active.is_(True))))
    for entry in db.scalars(select(BankBookEntry).where(BankBookEntry.kind == "receipt", BankBookEntry.account_id.in_(accounts))):
        if entry.id in reversed_ids:
            continue
        store = accounts[entry.account_id].store_id
        stamp = date.fromisoformat(entry.movement_date)
        batch = [
            m for m in moves
            if m.payment in ("cartao", "mbway")
            and deferred_settlement(db, m, terminals)
            and movement_store(db, m, registers)[0] == store
            and 0 <= (stamp - date.fromisoformat(local_day(m.occurred_at))).days <= 10
        ]
        by_day = defaultdict(list)
        for m in batch:
            by_day[local_day(m.occurred_at)].append(m)
        for day, group in by_day.items():
            gross = sum(m.amount_cents for m in group)
            if 0 <= gross - entry.amount_cents <= gross // 10:
                result.append(
                    {
                        "kind": "tpa_registado_como_receita",
                        "reason": f"Crédito bancário aprovado como recebimento ({entry.amount_cents / 100:.2f} €) compatível com {len(group)} venda(s) TPA de {day} já registadas ({gross / 100:.2f} €). A liquidação deve regularizar o TPA, não criar nova receita.",
                        "records": [
                            {
                                "source": "book",
                                "id": entry.id,
                                "date": entry.movement_date,
                                "amount_cents": entry.amount_cents,
                                "payment": "transferencia",
                                "origin": "Registo bancário aprovado",
                                "document": entry.reference,
                                "description": entry.description,
                                "reconciled": True,
                                "conference_cents": linked(db, "book", entry.id),
                                "closed": False,
                            },
                            *[info(m) for m in group],
                        ],
                        "correct": {"source": "book", "id": entry.id},
                    }
                )
                break
    # 2. Movimentos idênticos na mesma caixa, dia, meio, valor e documento.
    groups = defaultdict(list)
    for m in moves:
        if m.document and m.origin != "fecho_local":
            groups[(movement_store(db, m, registers)[1], local_day(m.occurred_at), m.kind, m.payment, m.amount_cents, m.document.strip().casefold())].append(m)
    for key, group in groups.items():
        if len(group) > 1:
            group.sort(key=lambda m: m.created_at)
            result.append(
                {
                    "kind": "movimentos_identicos",
                    "reason": f"{len(group)} recebimentos com o mesmo documento, dia, meio e valor na mesma caixa.",
                    "records": [info(m) for m in group],
                    "correct": {"source": "movement", "id": group[-1].id},
                }
            )
    # 3. Recebimento manual possivelmente repetido de um movimento ERP já importado.
    erp = defaultdict(list)
    for row in db.scalars(select(WintouchSaleRecord).where(WintouchSaleRecord.kind == "real", WintouchSaleRecord.active.is_(True), WintouchSaleRecord.store_id.in_(stores))):
        data = json.loads(row.data)
        if int(data.get("direction", 1)) > 0:
            erp[row.store_id, local_day(data.get("occurred_at") or row.document_date), int(data.get("amount_cents", 0))].append(row)
    for m in moves:
        if m.origin != "manual":
            continue
        matches = erp.get((movement_store(db, m, registers)[0], local_day(m.occurred_at), m.amount_cents))
        if matches:
            result.append(
                {
                    "kind": "manual_repete_erp",
                    "reason": "Recebimento manual com o mesmo estabelecimento, dia e valor de um movimento ERP importado.",
                    "records": [info(m), *[{"source": "wintouch", "id": r.id, "date": r.document_date, "amount_cents": m.amount_cents, "origin": "ERP", "payment": "", "document": json.loads(r.data).get("document", ""), "description": "Movimento importado do ERP", "reconciled": False, "conference_cents": 0, "closed": False} for r in matches]],
                    "correct": {"source": "movement", "id": m.id},
                }
            )
    saft_movements = db.scalar(select(func.count()).select_from(Movement).where(Movement.origin.ilike("saft%"))) or 0
    return {
        "items": result,
        "checks": [
            {
                "name": "Movimentos reais gerados por importações SAF-T",
                "count": int(saft_movements),
                "ok": not saft_movements,
                "detail": "As importações SAF-T guardam apenas faturação; não criam entradas em caixa ou banco.",
            }
        ],
        "note": "Pré-visualização: nada é anulado automaticamente. A correção usa o estorno existente, com permissão, motivo e histórico; movimentos reconciliados ou em caixas fechadas exigem primeiro desfazer a associação ou reabrir a caixa.",
    }


# ---------------------------------------------------------------- pedidos


class DocumentLine(Strict):
    record_id: str = Field(max_length=36)
    amount_cents: int = Field(gt=0)


class ReceiptLine(Strict):
    source: Literal["movement", "book"]
    id: str = Field(max_length=36)
    amount_cents: int = Field(gt=0)


class ConferenceInput(Strict):
    kind: Literal["individual", "agregada"]
    confirmed: Literal[True]
    notes: str = Field(default="", max_length=2000)
    documents: list[DocumentLine] = Field(default_factory=list, max_length=500)
    receipts: list[ReceiptLine] = Field(default_factory=list, max_length=50)
    store_id: str | None = Field(default=None, max_length=36)
    day: date | None = None
    payment: str | None = Field(default=None, max_length=20)


class UndoInput(Strict):
    reason: str = Field(min_length=3, max_length=1000)


class MissingInput(Strict):
    record_id: str = Field(max_length=36)
    payment_index: int = Field(ge=0)
    session_id: str = Field(max_length=36)
    confirmed: Literal[True]
    acknowledge_warnings: bool = False
    request_key: str = Field(min_length=8, max_length=80)


class CorrectInput(Strict):
    source: Literal["movement", "book"]
    id: str = Field(max_length=36)
    reason: str = Field(min_length=5, max_length=1000)
    confirmed: Literal[True]


@router.get("/api/conference")
def overview(start: date, end: date, store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    stores, start, end = scope_for(db, user, store_id, start, end)
    docs, recs = documents(db, stores, start, end), receipts(db, stores, start, end)
    annotate(db, docs, recs)
    return {
        "mode": policy(db)["mode"],
        "billing_available": saft_source(db) is not None,
        "indicators": indicators(db, stores, start, end, docs, recs),
        "documents": docs,
        "receipts": recs,
        "daily": daily(db, docs, recs, stores),
        "suggestions": suggestions(docs, recs),
        "conferences": [
            {
                "id": c.id,
                "kind": c.kind,
                "store_id": c.store_id,
                "day": c.day,
                "payment": c.payment,
                "billed_cents": c.billed_cents,
                "received_cents": c.received_cents,
                "criteria": json.loads(c.criteria),
                "notes": c.notes,
                "created_at": c.created_at,
                "user_id": c.user_id,
                "lines": [
                    {"side": line.side, "source": line.source, "source_id": line.source_id, "amount_cents": line.amount_cents}
                    for line in db.scalars(select(BillingConferenceLine).where(BillingConferenceLine.conference_id == c.id))
                ],
            }
            for c in db.scalars(
                select(BillingConference)
                .where(BillingConference.store_id.in_(stores), BillingConference.undone_at.is_(None))
                .order_by(BillingConference.created_at.desc())
                .limit(200)
            )
        ],
        "rules": [
            "Faturação e recebimentos são medidas distintas e nunca são somadas.",
            "Associar documentos a recebimentos não cria movimentos.",
            "Uma conferência agregada de totais diários não marca cada documento como recebido.",
            "Os pagamentos indicados no SAF-T são apoio à conferência, não recebimentos registados.",
        ],
    }


@router.get("/api/conference/indicators")
def indicator_summary(start: date, end: date, store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    stores, start, end = scope_for(db, user, store_id, start, end)
    return indicators(db, stores, start, end)


@router.post("/api/conference/confirm")
def confirm(data: ConferenceInput, user=Depends(current), db: Session = Depends(db_session)):
    lock(db)
    if data.kind == "agregada":
        return confirm_aggregate(db, user, data)
    if not data.documents or not data.receipts:
        fail("Selecione pelo menos um documento e um recebimento.")
    if len({d.record_id for d in data.documents}) != len(data.documents) or len({(r.source, r.id) for r in data.receipts}) != len(data.receipts):
        fail("Um documento ou recebimento está repetido na seleção.")
    used, _ = conference_usage(db)
    stores, directions, doc_rows, rec_rows = set(), set(), [], []
    for line in data.documents:
        row = db.get(WintouchSaleRecord, line.record_id)
        if not row or row.kind != "saft_invoice" or not row.active or row.enterprise_id != saft_source(db):
            fail("Documento de faturação não encontrado nesta empresa.", 404)
        values = json.loads(row.data)
        if values.get("status") != "N" or values.get("type") not in INVOICE_TYPES:
            fail(f"O documento {values['document']} está anulado ou excluído da faturação.")
        remaining = values["gross_cents"] - used["document", "saft", row.id]
        if line.amount_cents > remaining:
            fail(f"O valor indicado excede o remanescente por justificar de {values['document']}.", 409)
        stores.add(row.store_id)
        directions.add(values.get("direction", 1) or 1)
        doc_rows.append((row, values, line.amount_cents))
    registers = {row.id: row for row in db.scalars(select(Register))}
    for line in data.receipts:
        if line.source == "movement":
            move = db.scalar(select(Movement).where(Movement.id == line.id).with_for_update())
            if not move or move.kind not in RECEIPT_KINDS or move.reversed_by or move.reversal_of or move.transfer_id:
                fail("Recebimento não encontrado ou estornado.", 404)
            store = movement_store(db, move, registers)[0]
            info = {"source": "movement", "id": move.id, "date": local_day(move.occurred_at), "payment": move.payment, "direction": move.direction,
                    "document": move.document, "reference": move.reference, "notes": move.notes, "aggregate": move.origin == "fecho_local", "amount": move.amount_cents}
        else:
            entry = db.scalar(select(BankBookEntry).where(BankBookEntry.id == line.id).with_for_update())
            if not entry or entry.kind != "receipt" or db.scalar(select(BankBookEntry.id).where(BankBookEntry.original_id == entry.id)):
                fail("Registo bancário não encontrado ou estornado.", 404)
            store = db.get(BankAccount, entry.account_id).store_id
            info = {"source": "book", "id": entry.id, "date": entry.movement_date, "payment": "transferencia", "direction": 1 if entry.amount_cents > 0 else -1,
                    "document": "", "reference": entry.reference, "notes": entry.description, "aggregate": False, "amount": abs(entry.amount_cents)}
        authorize(user, db, store)
        if line.amount_cents > info["amount"] - used["receipt", line.source, line.id]:
            fail("O valor indicado excede o remanescente por associar do recebimento.", 409)
        stores.add(store)
        directions.add(info["direction"])
        rec_rows.append((info, line.amount_cents))
    if len(stores) != 1:
        fail("Documentos e recebimentos devem pertencer ao mesmo estabelecimento.")
    if len(directions) != 1:
        fail("Faturas associam-se a recebimentos e notas de crédito a reembolsos.")
    store = stores.pop()
    manager(user, db, store)
    total_docs = sum(amount for _, _, amount in doc_rows)
    total_recs = sum(amount for _, amount in rec_rows)
    if total_docs != total_recs:
        fail(f"O valor justificado nos documentos ({total_docs / 100:.2f} €) tem de ser igual ao valor associado dos recebimentos ({total_recs / 100:.2f} €). Valores parciais ficam como remanescente.")
    reasons, evidence = [], False
    for row, values, _ in doc_rows:
        doc = {"document": values["document"], "date": values["date"], "saft_payments": [
            {"date": p.get("date") or "", "method": payment_mapping(db)[0].get(p["code"]), "amount_cents": p["amount_cents"]} for p in values.get("payments", [])]}
        for info, _ in rec_rows:
            found, strong = criteria(doc, info)
            evidence = evidence or strong
            reasons.extend(f"{values['document']}: {reason}" for reason in found)
    if not evidence and len(data.notes.strip()) < 5:
        fail("Os valores coincidem, mas não há outra evidência (número do documento, data e meio do pagamento SAF-T). Indique nas notas a evidência que suporta a associação.", 409)
    conference = BillingConference(store_id=store, kind="individual", criteria=json.dumps(list(dict.fromkeys(reasons))[:50], ensure_ascii=False),
                                   notes=data.notes.strip(), user_id=user.id, billed_cents=total_docs, received_cents=total_recs)
    db.add(conference)
    db.flush()
    db.add_all([BillingConferenceLine(conference_id=conference.id, side="document", source="saft", source_id=row.id, amount_cents=amount) for row, _, amount in doc_rows])
    db.add_all([BillingConferenceLine(conference_id=conference.id, side="receipt", source=info["source"], source_id=info["id"], amount_cents=amount) for info, amount in rec_rows])
    record(db, user, "conferencia_faturacao_confirmada", conference.id, {
        "documentos": [{"id": row.id, "documento": values["document"], "valor_cents": amount} for row, values, amount in doc_rows],
        "recebimentos": [{"origem": info["source"], "id": info["id"], "valor_cents": amount} for info, amount in rec_rows],
        "criterios": json.loads(conference.criteria), "movimentos_criados": 0,
    }, store)
    db.commit()
    return {"id": conference.id, "message": "Conferência registada. Nenhum movimento foi criado."}


def confirm_aggregate(db, user, data):
    if not data.store_id or not data.day:
        fail("Indique o estabelecimento e o dia da conferência agregada.")
    manager(user, db, data.store_id)
    day = str(data.day)
    if db.scalar(select(BillingConference.id).where(
        BillingConference.kind == "agregada", BillingConference.undone_at.is_(None), BillingConference.store_id == data.store_id,
        BillingConference.day == day, BillingConference.payment.is_(data.payment) if data.payment is None else BillingConference.payment == data.payment,
    )):
        fail("Este dia e meio já têm uma conferência agregada ativa.", 409)
    docs, recs = documents(db, [data.store_id], day, day), receipts(db, [data.store_id], day, day)
    eligible = [d for d in docs if d["eligible"]]
    if data.payment:
        # Pagamentos SAF-T com data de pagamento neste dia (de documentos de qualquer data).
        lookback = documents(db, [data.store_id], str(data.day - timedelta(days=MAX_PERIOD_DAYS)), day)
        billed_docs = [(d, sum(p["amount_cents"] for p in d["saft_payments"] if p["date"] == day and p["method"] == data.payment) * d["direction"])
                       for d in lookback if d["eligible"]]
        billed_docs = [(d, value) for d, value in billed_docs if value]
        recs = [r for r in recs if r["payment"] == data.payment]
    else:
        billed_docs = [(d, d["gross_cents"] * d["direction"]) for d in eligible]
    billed = sum(value for _, value in billed_docs)
    received = sum(r["amount_cents"] * r["direction"] for r in recs)
    if not billed_docs and not recs:
        fail("Não existem documentos nem recebimentos para conferir neste dia.")
    difference = received - billed
    if difference and len(data.notes.strip()) < 5:
        fail(f"Existe uma diferença de {difference / 100:.2f} € entre recebido e faturado. Indique a justificação nas notas ou mantenha o dia por conferir.", 409)
    conference = BillingConference(
        store_id=data.store_id, kind="agregada", day=day, payment=data.payment, billed_cents=billed, received_cents=received,
        criteria=json.dumps(["Totais do dia por estabelecimento" + (" e meio de pagamento" if data.payment else ""),
                             "Totais iguais" if not difference else "Diferença justificada nas notas",
                             "Não marca documentos individuais como recebidos"], ensure_ascii=False),
        notes=data.notes.strip(), user_id=user.id,
    )
    db.add(conference)
    db.flush()
    db.add_all([BillingConferenceLine(conference_id=conference.id, side="document", source="saft", source_id=d["id"], amount_cents=abs(value)) for d, value in billed_docs])
    db.add_all([BillingConferenceLine(conference_id=conference.id, side="receipt", source=r["source"], source_id=r["id"], amount_cents=r["amount_cents"]) for r in recs])
    record(db, user, "conferencia_agregada_confirmada", conference.id, {"dia": day, "meio": data.payment, "faturado_cents": billed,
                                                                       "recebido_cents": received, "diferenca_cents": difference, "movimentos_criados": 0}, data.store_id)
    db.commit()
    return {"id": conference.id, "billed_cents": billed, "received_cents": received, "difference_cents": difference,
            "message": "Conferência agregada registada. Os documentos não ficam marcados individualmente como recebidos."}


@router.post("/api/conference/{identifier}/undo")
def undo(identifier: str, data: UndoInput, user=Depends(current), db: Session = Depends(db_session)):
    lock(db)
    conference = db.scalar(select(BillingConference).where(BillingConference.id == identifier).with_for_update())
    if not conference or conference.undone_at:
        fail("Conferência não encontrada ou já desfeita.", 404)
    manager(user, db, conference.store_id)
    from .models import now

    conference.undone_at, conference.undone_by, conference.undo_reason = now(), user.id, data.reason
    record(db, user, "conferencia_faturacao_desfeita", conference.id, {"motivo": data.reason}, conference.store_id)
    db.commit()
    return {"ok": True}


@router.get("/api/conference/missing-receipts")
def missing(start: date, end: date, store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    stores, start, end = scope_for(db, user, store_id, start, end)
    manager(user, db)
    docs, recs = documents(db, stores, start, end), receipts(db, stores, *widened(start, end))
    annotate(db, docs, recs)
    return {
        "items": missing_receipts(db, stores, docs, recs),
        "note": "Apenas documentos com pagamento indicado no SAF-T. Um recebimento só é criado após revisão e confirmação explícita, numa caixa aberta, com a data de hoje; a data de pagamento do SAF-T fica registada nas notas.",
    }


def widened(start, end):
    """Recebimentos podem ocorrer antes (adiantamentos) ou muito depois (vendas a crédito):
    procurar até hoje, para nunca propor um recebimento que já foi registado mais tarde."""
    return str(date.fromisoformat(start) - timedelta(days=31)), max(end, date.today().isoformat())


@router.post("/api/conference/missing-receipts/confirm")
def register_missing(data: MissingInput, user=Depends(current), db: Session = Depends(db_session)):
    from .main import add_movement
    from .schemas import MovementCreate

    lock(db)
    prior = db.scalar(select(Movement).where(Movement.request_key == "saft-missing:" + data.request_key))
    if prior:
        return {"movement_id": prior.id, "message": "Recebimento já registado; não foi duplicado."}
    row = db.get(WintouchSaleRecord, data.record_id)
    if not row or row.kind != "saft_invoice" or row.enterprise_id != saft_source(db):
        fail("Documento não encontrado nesta empresa.", 404)
    manager(user, db, row.store_id)
    values = json.loads(row.data)
    docs = documents(db, [row.store_id], values["date"], values["date"])
    recs = receipts(db, [row.store_id], *widened(values["date"], values["date"]))
    annotate(db, docs, recs)
    item = next((i for i in missing_receipts(db, [row.store_id], docs, recs) if i["record_id"] == row.id and i["payment_index"] == data.payment_index), None)
    if not item:
        fail("Este pagamento já não está por registar. Atualize a revisão.", 409)
    if not item["proposable"]:
        fail("Informação insuficiente: " + " ".join(item["missing"]))
    if item["warnings"] and not data.acknowledge_warnings:
        fail("Reveja os avisos de possível duplicação e confirme explicitamente que este recebimento é distinto.", 409)
    session = db.get(CashSession, data.session_id)
    register = db.get(Register, session.register_id) if session else None
    if not session or session.closed_at or not register or register.store_id != row.store_id:
        fail("Escolha uma caixa aberta do mesmo estabelecimento.")
    from sqlalchemy.dialects.postgresql import insert

    db.execute(insert(Category).values(id=uid(), name="Vendas").on_conflict_do_nothing(index_elements=[Category.name]))
    category = db.scalar(select(Category.id).where(Category.name == "Vendas"))
    from .models import now

    move = add_movement(MovementCreate(
        session_id=session.id, category_id=category, kind="cliente", payment=item["method"], amount=f"{item['amount_cents'] / 100:.2f}",
        occurred_at=now(), document=values["document"], reference=row.id,
        notes=f"Recebimento registado a partir da conferência SAF-T. Pagamento indicado no SAF-T em {item['payment_date']} ({item['code']}).",
        request_key="saft-missing:" + data.request_key,
    ), user, db)
    conference = BillingConference(store_id=row.store_id, kind="individual", user_id=user.id, billed_cents=move.amount_cents, received_cents=move.amount_cents,
                                   criteria=json.dumps(["Recebimento registado a partir do pagamento indicado no SAF-T", *item["warnings"]], ensure_ascii=False),
                                   notes="Registo de recebimento em falta confirmado pelo utilizador.")
    db.add(conference)
    db.flush()
    db.add_all([
        BillingConferenceLine(conference_id=conference.id, side="document", source="saft", source_id=row.id, amount_cents=move.amount_cents),
        BillingConferenceLine(conference_id=conference.id, side="receipt", source="movement", source_id=move.id, amount_cents=move.amount_cents),
    ])
    record(db, user, "recebimento_em_falta_registado", move.id, {"documento": values["document"], "avisos": item["warnings"], "conferencia": conference.id}, row.store_id)
    db.commit()
    return {"movement_id": move.id, "conference_id": conference.id, "message": "Recebimento registado e associado ao documento."}


@router.get("/api/conference/duplicates")
def duplicate_preview(store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        stores = [store_id] if store_id in stores else []
    manager(user, db)
    return duplicates(db, stores)


@router.post("/api/conference/duplicates/correct")
def correct_duplicate(data: CorrectInput, user=Depends(current), db: Session = Depends(db_session)):
    manager(user, db)
    preview = duplicates(db, scope(user, db))
    if not any(item["correct"] == {"source": data.source, "id": data.id} for item in preview["items"]):
        fail("Este registo já não aparece como possível duplicado. Atualize a pré-visualização.", 409)
    if linked(db, data.source, data.id):
        fail("Desfaça primeiro a conferência de faturação deste recebimento.", 409)
    reason = "Correção de duplicado confirmada: " + data.reason.strip()
    if data.source == "movement":
        from .main import reverse
        from .schemas import Reason

        reverse(data.id, Reason(reason=reason), user=user, db=db)
    else:
        from .bank_balances import Reversal
        from .bank_balances import reverse as reverse_book

        reverse_book(data.id, Reversal(reason=reason, request_key="duplicate:" + data.id), user=user, db=db)
    record(db, user, "duplicado_corrigido", data.id, {"origem": data.source, "motivo": data.reason}, None)
    db.commit()
    return {"ok": True, "message": "Duplicado corrigido por estorno. O registo original e o estorno ficam no histórico."}
