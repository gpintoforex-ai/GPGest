"""Account-scoped rules, immutable closing snapshots and reports. No ERP financial writes."""
import csv
import io
import json
from datetime import date
from urllib.parse import quote
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, record
from .db import db_session
from .models import Audit, BankAccount, BankReconciliationPeriod, BankTransaction, DocumentImport, Reconciliation, ReconciliationLine, User, now
from .reconciliation_matching import fingerprint
from .schemas import Strict

router = APIRouter()


def locked_account(db, user, identifier, write=False):
    query = select(BankAccount).where(BankAccount.id == identifier)
    if write:
        query = query.with_for_update().execution_options(populate_existing=True)
    account = db.scalar(query)
    if not account:
        raise HTTPException(404, "Conta bancária não encontrada.")
    authorize(user, db, account.store_id, ["administrador", "gestor"] if write else None)
    return account


def assert_open(db, account_id, stamp):
    # Same serialization point as closing. Acquired before any financial mutation.
    db.scalar(select(BankAccount).where(BankAccount.id == account_id).with_for_update())
    closed = db.scalar(select(BankReconciliationPeriod.id).where(
        BankReconciliationPeriod.account_id == account_id, BankReconciliationPeriod.start <= stamp,
        BankReconciliationPeriod.end >= stamp, BankReconciliationPeriod.reopened_at.is_(None)).limit(1))
    if closed:
        raise HTTPException(409, "O período desta conta está fechado. Reabra-o com justificação antes de alterar os movimentos ou associações.")


class Rules(Strict):
    date_tolerance_days: int = Field(default=10, ge=0, le=90)
    grouped_proposals: bool = True


def account_rules(account):
    return Rules(**json.loads(account.settings or "{}").get("reconciliation_rules", {})).model_dump()


@router.get("/api/reconciliation/accounts/{account_id}/rules")
def rules(account_id: str, user=Depends(current), db: Session = Depends(db_session)):
    return account_rules(locked_account(db, user, account_id))


@router.put("/api/reconciliation/accounts/{account_id}/rules")
def save_rules(account_id: str, data: Rules, user=Depends(current), db: Session = Depends(db_session)):
    account = locked_account(db, user, account_id, True)
    settings = json.loads(account.settings or "{}")
    settings["reconciliation_rules"] = data.model_dump()
    account.settings = json.dumps(settings)
    record(db, user, "reconciliacao_regras", account.id, data.model_dump(), account.store_id)
    db.commit()
    return data.model_dump()


def period_state(db, account, start, end):
    from .bank_balances import summary
    if start > end:
        raise HTTPException(422, "Selecione um período válido: o início deve ser anterior ao fim.")
    result = summary(db, account, start, end)
    transactions = list(db.scalars(select(BankTransaction).where(BankTransaction.account_id == account.id,
        BankTransaction.movement_date >= start, BankTransaction.movement_date <= end)))
    bank_ids = [b.id for b in transactions]
    groups = list(db.scalars(select(Reconciliation).join(ReconciliationLine).where(
        ReconciliationLine.source == "bank", ReconciliationLine.source_id.in_(bank_ids),
        Reconciliation.undone_at.is_(None)).distinct().order_by(Reconciliation.id))) if bank_ids else []
    ignored = {r.target_id: json.loads(r.detail).get("reason", "") for r in db.scalars(select(Audit).where(
        Audit.store_id == account.store_id, Audit.action == "movimento_ignorado_reconciliacao",
        Audit.target_id.in_(bank_ids)).order_by(Audit.created_at))} if bank_ids else {}
    amounts = [b.amount_cents for b in transactions]
    # A difference spanning accounts/dates is not automatically attributable to this period.
    justified = 0
    group_details = []
    for group in groups:
        links = list(db.scalars(select(ReconciliationLine).where(ReconciliationLine.reconciliation_id == group.id).order_by(ReconciliationLine.source, ReconciliationLine.source_id)))
        group_banks = [db.get(BankTransaction, l.source_id) for l in links if l.source == "bank"]
        attributable = all(b and b.account_id == account.id and start <= b.movement_date <= end for b in group_banks)
        if attributable and group.reason.strip():
            justified += group.difference_cents
        group_details.append({"id": group.id, "reason": group.reason, "commission_cents": group.commission_cents,
            "difference_cents": group.difference_cents, "attributable": attributable,
            "lines": [{"source": l.source, "source_id": l.source_id, "amount_cents": l.amount_cents} for l in links]})
    # Only a supplied balance at the exact boundary is treated as the final statement balance.
    unresolved = result["difference_cents"] + justified if result["difference_cents"] is not None else None
    state = {**result, "statement_incoming_cents": sum(n for n in amounts if n > 0),
        "statement_outgoing_cents": -sum(n for n in amounts if n < 0),
        "justified_difference_cents": justified, "unresolved_difference_cents": unresolved,
        "reconciliations": group_details, "ignored_reasons": ignored,
        "statement_opening_cents": None,
        "closing_blockers": []}
    # Initial balance can be inferred only with a full, continuous sequence of supplied balances.
    ordered = sorted(transactions, key=lambda b: (b.movement_date, b.exported_at, -b.source_position, b.id))
    if ordered and all(b.balance_cents is not None for b in ordered):
        initial = ordered[0].balance_cents - ordered[0].amount_cents
        running = initial
        continuous = True
        for b in ordered:
            running += b.amount_cents
            continuous &= running == b.balance_cents
        if continuous:
            state["statement_opening_cents"] = initial
    if not result["comparable"]:
        state["closing_blockers"].append("Faltam saldos comparáveis da mesma conta, moeda e data final.")
    if unresolved:
        state["closing_blockers"].append("Existem diferenças por justificar entre os saldos.")
    if result["pending_bank"]["count"]:
        state["closing_blockers"].append("Existem movimentos do extrato por reconciliar.")
    state["signature"] = fingerprint(state)
    return state


def closing_view(db, closing, signature=None):
    actor = db.get(User, closing.user_id)
    reopening = db.scalar(select(Audit).where(Audit.action == "reconciliacao_periodo_reaberto", Audit.target_id == closing.id).order_by(Audit.created_at.desc()).limit(1))
    reopener = db.get(User, reopening.user_id) if reopening else None
    return {"id": closing.id, "start": closing.start, "end": closing.end, "created_at": closing.created_at,
        "created_by": actor.name if actor else "Utilizador registado", "reason": closing.reason,
        "reopened_at": closing.reopened_at, "stale": signature is not None and signature != closing.signature,
        "signature": closing.signature, "reopened_by": reopener.name if reopener else None,
        "reopen_reason": json.loads(reopening.detail).get("reason", "") if reopening else ""}


@router.get("/api/reconciliation/accounts/{account_id}/period")
def period(account_id: str, start: date, end: date, user=Depends(current), db: Session = Depends(db_session)):
    account = locked_account(db, user, account_id)
    state = period_state(db, account, str(start), str(end))
    closes = list(db.scalars(select(BankReconciliationPeriod).where(BankReconciliationPeriod.account_id == account_id,
        BankReconciliationPeriod.start == str(start), BankReconciliationPeriod.end == str(end)).order_by(BankReconciliationPeriod.created_at.desc())))
    return {**state, "closings": [closing_view(db, c, state["signature"]) for c in closes]}


class Close(Strict):
    start: date
    end: date
    signature: str = Field(min_length=64, max_length=64)
    confirmed: bool
    authorized_exception: bool = False
    reason: str = Field(default="", max_length=2000)


@router.post("/api/reconciliation/accounts/{account_id}/close")
def close(account_id: str, data: Close, user=Depends(current), db: Session = Depends(db_session)):
    account = locked_account(db, user, account_id, True)
    if not data.confirmed:
        raise HTTPException(422, "É necessária a confirmação explícita do fecho.")
    if data.end > date.today():
        raise HTTPException(422, "Não pode fechar um período com data final futura.")
    overlap = db.scalar(select(BankReconciliationPeriod.id).where(BankReconciliationPeriod.account_id == account_id,
        BankReconciliationPeriod.start <= str(data.end), BankReconciliationPeriod.end >= str(data.start),
        BankReconciliationPeriod.reopened_at.is_(None)).limit(1))
    if overlap:
        raise HTTPException(409, "Existe um fecho ativo neste período. Reabra-o antes de voltar a fechar.")
    state = period_state(db, account, str(data.start), str(data.end))
    if state["signature"] != data.signature:
        raise HTTPException(409, "Os dados mudaram desde a revisão. Atualize e confira novamente.")
    if not state["comparable"]:
        raise HTTPException(409, "Não pode fechar sem saldos comparáveis da mesma conta, moeda e data.")
    if state["closing_blockers"] and not (data.authorized_exception and user.role == "administrador" and len(data.reason.strip()) >= 10):
        raise HTTPException(409, "Resolva as pendências ou solicite a exceção auditada de um administrador, com justificação.")
    if data.authorized_exception and (user.role != "administrador" or len(data.reason.strip()) < 10):
        raise HTTPException(403, "A exceção de fecho exige administrador e justificação detalhada.")
    closing = BankReconciliationPeriod(account_id=account_id, user_id=user.id, start=str(data.start), end=str(data.end),
        signature=state["signature"], snapshot=json.dumps(state, default=str, ensure_ascii=False), reason=data.reason.strip())
    db.add(closing); db.flush()
    record(db, user, "reconciliacao_periodo_fechado", closing.id, {"account_id": account_id,
        "start": closing.start, "end": closing.end, "signature": closing.signature,
        "exception": data.authorized_exception, "reason": closing.reason}, account.store_id)
    db.commit()
    return closing_view(db, closing)


class Reopen(Strict):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/api/reconciliation/accounts/{account_id}/periods/{identifier}/reopen")
def reopen(account_id: str, identifier: str, data: Reopen, user=Depends(current), db: Session = Depends(db_session)):
    account = locked_account(db, user, account_id, True)
    closing = db.get(BankReconciliationPeriod, identifier)
    if not closing or closing.account_id != account_id:
        raise HTTPException(404, "Fecho não encontrado nesta conta.")
    if closing.reopened_at:
        raise HTTPException(409, "O período já foi reaberto.")
    if len(data.reason.strip()) < 3:
        raise HTTPException(422, "Indique o motivo da reabertura.")
    closing.reopened_at = now()
    record(db, user, "reconciliacao_periodo_reaberto", closing.id, {"reason": data.reason.strip()}, account.store_id)
    db.commit()
    return closing_view(db, closing)


@router.get("/api/reconciliation/accounts/{account_id}/report")
def report(account_id: str, start: date, end: date, format: str = "csv", closing_id: str = "", user=Depends(current), db: Session = Depends(db_session),
           sort_by: Literal['date', 'amount'] = 'date', descending: bool = False, audit: bool = False):
    account = locked_account(db, user, account_id)
    if closing_id:
        closing = db.get(BankReconciliationPeriod, closing_id)
        if not closing or closing.account_id != account_id or (closing.start, closing.end) != (str(start), str(end)):
            raise HTTPException(404, "Fecho não encontrado nesta conta e período.")
        state = json.loads(closing.snapshot)
        status = "Snapshot do fecho " + closing.id
    else:
        state = period_state(db, account, str(start), str(end))
        status = "Relatório de consulta; não constitui fecho"
    rows = [["Reconciliação bancária", account.name], ["Estado", status], ["Período", f"{start} a {end}"], ["Moeda", account.currency]]
    if closing_id:
        actor = db.get(User, closing.user_id)
        rows += [["Fechado por", actor.name if actor else closing.user_id], ["Data do fecho", str(closing.created_at)],
            ["Justificação", closing.reason], ["Assinatura dos dados", closing.signature],
            ["Reabertura", str(closing.reopened_at or "Não reaberto")]]
    rows += [["Origem do saldo contabilístico", "Registos da aplicação e saldo de abertura configurado; não é um saldo oficial fornecido pelo ERP."]]
    labels = {"statement_opening_cents": "Saldo inicial inferido da sequência importada", "statement_incoming_cents": "Entradas no extrato",
        "statement_outgoing_cents": "Saídas no extrato", "bank_balance_cents": "Saldo final do extrato",
        "book_balance_cents": "Saldo pelos registos da aplicação", "justified_difference_cents": "Diferenças justificadas",
        "unresolved_difference_cents": "Diferenças por resolver"}
    rows += [[label, "Não disponível" if state[key] is None else f"{state[key]/100:.2f}"] for key, label in labels.items()]
    rows += [["Pendentes no extrato", state["pending_bank"]["count"]], ["Registos em trânsito", state["pending_application"]["count"]],
        [], ["Data", "Descrição", "Valor", "Associado", "Remanescente"]]
    rows += [[r["date"], r["description"], f'{r["amount_cents"]/100:.2f}', f'{r["associated_cents"]/100:.2f}', f'{r["remaining_cents"]/100:.2f}'] for r in state["bank_rows"]]
    rows += [[], ["Registos em trânsito: data", "Descrição", "Valor", "Associado", "Remanescente"]]
    rows += [[r["date"], r["description"], f'{r["amount_cents"]/100:.2f}', f'{r["associated_cents"]/100:.2f}', f'{r["remaining_cents"]/100:.2f}'] for r in state["application_rows"] if r["remaining_cents"]]
    rows += [[], ["Reconciliação", "Justificação", "Diferença", "Comissão"]]
    rows += [[g["id"], g["reason"], f'{g["difference_cents"]/100:.2f}', f'{g["commission_cents"]/100:.2f}'] for g in state["reconciliations"]]
    if format == "csv":
        stream = io.StringIO(); writer = csv.writer(stream, delimiter=";")
        for row in rows:
            writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in row])
        return Response(stream.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="reconciliacao.csv"'})
    if format != "pdf":
        raise HTTPException(422, "Formato de relatório inválido.")
    from .db import request_company
    from .reconciliation_report import build_model, render_pdf
    company = request_company(db)
    closing_metadata = None
    if closing_id:
        current_state = period_state(db, account, str(start), str(end))
        closing_metadata = closing_view(db, closing, current_state['signature'])
        closing_metadata['is_snapshot'] = True
    else:
        active_closing = db.scalar(select(BankReconciliationPeriod).where(BankReconciliationPeriod.account_id == account_id,
            BankReconciliationPeriod.start == str(start), BankReconciliationPeriod.end == str(end),
            BankReconciliationPeriod.reopened_at.is_(None)).order_by(BankReconciliationPeriod.created_at.desc()).limit(1))
        if active_closing:
            closing_metadata = closing_view(db, active_closing, state['signature'])
            closing_metadata['is_snapshot'] = False
    model = build_model(state, company={'name': company.name, 'nif': company.nif} if company else {},
        account={'name': state.get('name') or account.name, 'bank': state.get('bank') or account.bank,
            'currency': account.currency, 'iban_last4': account.iban_last4}, closing=closing_metadata,
        sort_by=sort_by, descending=descending)
    return Response(render_pdf(model, audit=audit), media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="reconciliacao.pdf"'})


@router.get("/api/reconciliation/accounts/{account_id}/imports/{identifier}/original")
def original(account_id: str, identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    account = locked_account(db, user, account_id)
    batch = db.get(DocumentImport, identifier)
    linked = db.scalar(select(BankTransaction.id).where(BankTransaction.account_id == account.id, BankTransaction.import_id == identifier).limit(1))
    if not batch or not linked or not batch.file_ciphertext:
        raise HTTPException(404, "O ficheiro original não está disponível nesta importação.")
    from .platform import cipher
    raw = cipher().decrypt(batch.file_ciphertext)
    import hashlib
    if hashlib.sha256(raw).hexdigest() != batch.fingerprint:
        raise HTTPException(409, "A verificação de integridade do ficheiro original falhou.")
    record(db, user, "extrato_original_consultado", batch.id, {"fingerprint": batch.fingerprint}, account.store_id); db.commit()
    return Response(raw, media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(batch.filename, safe="")})
