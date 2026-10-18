"""Read-only cash flow projection. Forecast edits never change financial documents."""
import calendar
import csv
import hashlib
import io
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from typing import Literal

from fastapi import Depends, HTTPException, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session
from .models import (AccountBalanceReference, Audit, BankAccount, BankDeposit, BankTransaction, CashSession, Category, DocumentPayment, DocumentERPLink, DriveDocumentAttachment, ERPDocument, Purchase, PurchaseAttachment, LiquidityAdjustment,
                     LiquidityManual, LiquidityScenario, LiquiditySettings, LiquiditySettlement, Movement, PlannedMovement, Register, WintouchSaleRecord, WintouchSyncRun, now)
from . import db as database
from .platform_models import Company
from .schemas import Strict
from .cash_sheet import day


def today():
    return date.fromisoformat(day(now()))


def digest(event):
    return hashlib.sha256(json.dumps({k: event.get(k) for k in ("amount_cents", "due_date", "direction", "currency", "target", "from_target")}, sort_keys=True).encode()).hexdigest()


def confirmed_partial_update(db, event, adjustment):
    """Only verified ERP settlements may reduce an adjusted forecast without a conflict."""
    if event["source"] != "wintouch":
        return False
    identifier = event["key"].partition(":")[2]
    initiated = list(db.scalars(select(Audit).where(
        Audit.action == "liquidacao_wintouch_iniciada", Audit.target_id == identifier,
        Audit.created_at >= adjustment.updated_at,
    ).order_by(Audit.created_at)))
    confirmed = {row.target_id for row in db.scalars(select(Audit).where(
        Audit.action == "liquidacao_wintouch_concluida", Audit.created_at >= adjustment.updated_at,
    )) if json.loads(row.detail).get("record_id") == identifier}
    operations = [json.loads(row.detail) for row in initiated if json.loads(row.detail).get("settlement_id") in confirmed]
    for index, operation in enumerate(operations):
        initial = abs(operation.get("pending_value_cents", 0))
        if digest({**event, "amount_cents": initial}) != adjustment.fingerprint:
            continue
        remaining = initial
        for paid in operations[index:]:
            amount = paid.get("amount_cents", 0)
            if abs(paid.get("pending_value_cents", 0)) != remaining or not 0 < amount <= remaining:
                break
            remaining -= amount
        else:
            if remaining == event["amount_cents"] and remaining < initial:
                return True
    return False


class ManualInput(Strict):
    store_id: str
    title: str = Field(min_length=2, max_length=200)
    amount_cents: int = Field(strict=True, gt=0, le=10**13)
    direction: Literal[-1, 1] = -1
    currency: str = Field(default="EUR", pattern="^[A-Z]{3}$")
    due_date: date | None = None
    expected_date: date | None = None
    target: str = ""
    from_target: str = ""
    confidence: Literal["confirmado", "provavel", "incerto"] = "provavel"
    recurrence: Literal["none", "monthly", "quarterly", "yearly"] = "none"
    recurrence_end: date | None = None
    active: bool = True
    reason: str = Field(min_length=3, max_length=2000)


class AdjustmentInput(Strict):
    expected_date: date | None = None
    target: str = ""
    confidence: Literal["confirmado", "provavel", "incerto"] = "provavel"
    replaces_key: str = ""
    reason: str = Field(min_length=3, max_length=2000)


class ScenarioChange(Strict):
    expected_date: date | None = None
    amount_cents: int | None = Field(default=None, strict=True, ge=0, le=10**13)
    excluded: bool = False
    target: str | None = None


class ScenarioInput(Strict):
    name: str = Field(min_length=2, max_length=100)
    mode: Literal["base", "conservative", "custom"] = "custom"
    delay_days: int = Field(default=0, ge=0, le=365)
    exclude_uncertain: bool = True
    shared: bool = False
    changes: dict[str, ScenarioChange] = Field(default_factory=dict, max_length=500)
    extras: list[ManualInput] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=3, max_length=2000)


class SettingsInput(Strict):
    reserves: dict[str, int] = Field(default_factory=lambda: {"EUR": 0}, max_length=20)
    stale_days: int = Field(default=7, ge=1, le=90)
    reason: str = Field(min_length=3, max_length=2000)


class ForecastInput(Strict):
    start: date = Field(default_factory=today)
    end: date = Field(default_factory=lambda: today() + timedelta(days=29))
    targets: list[str] | None = None
    store_id: str = ""
    scenario_id: str = "base"
    delay_days: int = Field(default=0, ge=0, le=365)
    exclude_uncertain: bool = True
    compare: bool = False


class LocalSettlementInput(Strict):
    amount_cents: int = Field(strict=True, gt=0, le=10**13)
    target: str = Field(min_length=1, max_length=80)
    payment_date: date = Field(default_factory=today)
    category_id: str
    request_key: str = Field(min_length=10, max_length=100)
    reason: str = Field(min_length=3, max_length=2000)
    confirmed: Literal[True]


class SettlementProposalInput(Strict):
    entity: str = Field(default="", max_length=300)
    direction: Literal[-1, 1] = -1
    overdue_only: bool = True
    amount_cents: int | None = Field(default=None, strict=True, gt=0, le=10**13)
    store_id: str = ""
    currency: Literal["EUR"] = "EUR"


class SustainabilityLine(Strict):
    key: str = Field(min_length=1, max_length=180)
    amount_cents: int = Field(strict=True, gt=0, le=10**13)


class SustainabilityInput(Strict):
    target: str = Field(min_length=1, max_length=80)
    lines: list[SustainabilityLine] = Field(min_length=1, max_length=500)
    horizon_days: int = Field(default=30, ge=1, le=365)
    reserved_cents: int = Field(default=0, strict=True, ge=0, le=10**13)


def active_local_settlements(db):
    for link, payment in db.execute(select(LiquiditySettlement, DocumentPayment).join(DocumentPayment, DocumentPayment.id == LiquiditySettlement.payment_id)):
        movement = db.get(Movement, link.movement_id) if link.movement_id else None
        if not movement or not movement.reversed_by:
            yield link, payment


def erp_status(db):
    company_id = db.info.get("company", {}).get("id")
    if not company_id:
        return {"active": False, "provider": "none"}
    with database.next_platform_session() as core:
        company = core.get(Company, company_id)
        from .operating_modes import company_policy
        config = company_policy(company)
        return {"active": config["mode"] == "integrated", "connected": config["active"], "provider": config["provider"], "can_settle": config["capabilities"]["write_settlements"]}


def occurrences(data, end):
    anchor = data.get("expected_date") or data.get("due_date")
    if data["recurrence"] == "none" or not anchor:
        yield anchor
        return
    anchor = date.fromisoformat(anchor)
    stop = min(end, date.fromisoformat(data["recurrence_end"])) if data.get("recurrence_end") else end
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[data["recurrence"]]
    # Clamp each occurrence against the original day: 31 Jan -> 28 Feb -> 31 Mar.
    for index in range(1200):
        number = anchor.year * 12 + anchor.month - 1 + index * months
        year, month = number // 12, number % 12 + 1
        stamp = date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))
        if stamp > stop:
            break
        yield stamp.isoformat()


def project(accounts, events, selected, start, end, reserves, assumptions):
    result, pending, scheduled = {}, [], []
    all_selected = selected == {a["key"] for a in accounts}
    replacements = {e.get("replaces_key") for e in events if e.get("replaces_key") and not e.get("conflict") and e["amount_cents"] > 0}
    for original in events:
        event = dict(original)
        event["days_until_due"] = (date.fromisoformat(event["due_date"]) - today()).days if event.get("due_date") else None
        if event["key"] in replacements:
            pending.append({**event, "exclusion": "Recorrência substituída pelo documento associado"})
            continue
        if event.get("conflict"):
            pending.append({**event, "exclusion": "A origem mudou; reveja e confirme o ajuste"})
            continue
        if event["amount_cents"] <= 0:
            continue
        if event.get("excluded") or (assumptions.get("exclude_uncertain") and event["direction"] == 1 and event["confidence"] == "incerto"):
            pending.append({**event, "exclusion": "Excluído pelas premissas do cenário"})
            continue
        stamp = event.get("expected_date")
        if not stamp or stamp < today().isoformat():
            pending.append({**event, "exclusion": "Vencida sem data prevista" if event.get("due_date") and event["due_date"] < today().isoformat() else "Sem data prevista válida"})
            continue
        if event["direction"] == 1 and not event.get("transfer"):
            stamp = (date.fromisoformat(stamp) + timedelta(days=assumptions.get("delay_days", 0))).isoformat()
        event["expected_date"] = stamp
        legs = []
        if event.get("from_target"):
            if event["from_target"] in selected:
                legs.append({**event, "direction": -1, "target": event["from_target"]})
            if event["target"] in selected:
                legs.append({**event, "direction": 1})
        elif event.get("target"):
            if event["target"] in selected:
                legs.append(event)
        elif all_selected:
            legs.append(event)
        if not legs:
            pending.append({**event, "exclusion": "Conta por associar; excluído da seleção parcial" if not event.get("target") and not event.get("from_target") else "Conta ou caixa fora da seleção"})
            continue
        if stamp > end.isoformat():
            pending.append({**event, "exclusion": "Fora do horizonte selecionado"})
        else:
            scheduled.extend(legs)
    currencies = sorted({a["currency"] for a in accounts if a["key"] in selected} | {e["currency"] for e in scheduled})
    for currency in currencies:
        accounts_currency = [a for a in accounts if a["key"] in selected and a["currency"] == currency]
        balance = sum(a["balance_cents"] or 0 for a in accounts_currency)
        reserve = reserves.get(currency, 0)
        daily, first = [], None
        minimum, min_date, initial = None, None, balance
        stamp = today()
        while stamp <= end:
            entries = [e for e in scheduled if e["currency"] == currency and e["expected_date"] == stamp.isoformat()]
            incoming = sum(e["amount_cents"] for e in entries if e["direction"] == 1)
            outgoing = sum(e["amount_cents"] for e in entries if e["direction"] == -1)
            opening = balance
            balance += incoming - outgoing
            if stamp >= start:
                if not daily:
                    initial = opening
                row = {"date": stamp.isoformat(), "opening_cents": opening, "incoming_cents": incoming, "outgoing_cents": outgoing, "closing_cents": balance, "movements": entries}
                daily.append(row)
                if minimum is None or balance < minimum:
                    minimum, min_date = balance, stamp.isoformat()
                if first is None and balance < reserve:
                    causes = sorted({e["title"] for e in entries if e["direction"] == -1})
                    first = {"date": stamp.isoformat(), "shortfall_cents": reserve - balance, "causes": causes or ["Saldo transportado abaixo da reserva mínima"]}
            stamp += timedelta(days=1)
        result[currency] = {"daily": daily, "initial_cents": initial, "incoming_cents": sum(r["incoming_cents"] for r in daily), "outgoing_cents": sum(r["outgoing_cents"] for r in daily), "final_cents": balance, "minimum_cents": minimum, "minimum_date": min_date, "first_below": first, "reserve_cents": reserve, "required_cents": max(0, reserve - min(initial, minimum if minimum is not None else initial))}
        result[currency]["incomplete"] = not accounts_currency or any(a["missing"] for a in accounts_currency)
    return result, pending, scheduled


def register_routes(app, current, authorize, scope, audit, serial, planned_summary, sales_list):
    writers = ["administrador", "gestor", "operador"]
    managers = ["administrador", "gestor"]

    def targets(db, user):
        ids = scope(user, db)
        return {**{"bank:" + a.id: a for a in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(ids)))}, **{"cash:" + r.id: r for r in db.scalars(select(Register).where(Register.store_id.in_(ids)))}}

    def validate_target(db, user, key, currency=None, store_id=None):
        if not key:
            return
        obj = targets(db, user).get(key)
        if not obj:
            raise HTTPException(404, "Conta ou caixa indisponível nesta empresa e perfil.")
        if currency and obj.currency != currency:
            raise HTTPException(422, "As moedas têm de coincidir; não existe conversão cambial automática.")
        if store_id and obj.store_id != store_id:
            raise HTTPException(422, "A conta ou caixa pertence a outro estabelecimento.")

    def settings(db):
        row = db.get(LiquiditySettings, "default")
        return {"reserves": json.loads(row.reserves) if row else {"EUR": 0}, "stale_days": row.stale_days if row else 7}

    def source_data(db, user, end, store_id=""):
        stamp = today().isoformat()
        accounts, events, warnings = [], [], []
        cfg = settings(db)
        local_settlements = [(link, payment) for link, payment in active_local_settlements(db) if payment.store_id in scope(user, db)]
        for key, obj in targets(db, user).items():
            if store_id and obj.store_id != store_id:
                continue
            if key.startswith("bank:"):
                transactions = list(db.scalars(select(BankTransaction).where(BankTransaction.account_id == obj.id, BankTransaction.movement_date <= stamp).order_by(BankTransaction.exported_at.desc(), BankTransaction.source_position.asc(), BankTransaction.created_at.desc())))
                last = transactions[0] if transactions else None
                reference = db.get(AccountBalanceReference, key)
                value = last.balance_cents if last else None
                source = "Último saldo do extrato importado"
                updated, balance_date = last.created_at.isoformat() if last else None, last.movement_date if last else None
                kind = "contabilistico"
                uses_reference = bool(reference and (not last or last.movement_date < reference.reference_date))
                if uses_reference:
                    value, source, updated, balance_date, kind = reference.amount_cents, "Saldo inicial local com data de referência", reference.updated_at.isoformat(), reference.reference_date, "estimado"
                from .reconciliation import used
                unreflected = sum((payment.amount_cents - used(db, "payment", payment.id)) * payment.direction for link, payment in local_settlements if link.target == key and payment.payment_date <= stamp and (not uses_reference or payment.payment_date >= reference.reference_date))
                if unreflected:
                    if value is not None:
                        value += unreflected
                    source += " + liquidações locais por reconciliar"
                    kind = "estimado"
            else:
                accumulated = app.state.liquidity_cash_sources(db, obj, stamp)
                sheet = db.scalar(select(CashSession).where(CashSession.register_id == obj.id, CashSession.sheet_day <= stamp).order_by(CashSession.sheet_day.desc(), CashSession.opened_at.desc()))
                reference = db.get(AccountBalanceReference, key)
                value = int(Decimal(accumulated["cash_balance"]) * 100) + int(Decimal(accumulated["cheque_balance"]) * 100) + (reference.fund_cents if reference else sheet.opening_cents if sheet else 0)
                if not accumulated["movements"] and not sheet and not reference:
                    value = None
                source = "Fundo a manter + acumulado de movimentos reais, incluindo cheques"
                kind = "estimado"
                update_dates = [m["date"] for m in accumulated["movements"]]
                if sheet:
                    update_dates.append(sheet.sheet_day or day(sheet.opened_at))
                if reference:
                    update_dates.append(reference.reference_date)
                balance_date = max(update_dates) if update_dates else None
                updated = (sheet.closed_at or sheet.opened_at).isoformat() if sheet else balance_date
                warnings.append(f"{obj.name}: saldo estimado; cheques não representam fundos bancários imediatamente disponíveis.")
            stale = balance_date is None or (today() - date.fromisoformat(balance_date)).days >= cfg["stale_days"]
            accounts.append({"key": key, "name": (obj.bank + " · " if key.startswith("bank:") else "Caixa · ") + obj.name, "store_id": obj.store_id, "currency": obj.currency, "balance_cents": value, "source": source, "updated_at": updated, "balance_date": balance_date, "kind": kind, "stale": stale, "missing": value is None})
            if stale:
                warnings.append(f"{accounts[-1]['name']}: saldo sem atualização recente (limite {cfg['stale_days']} dias).")
        allowed = {a["key"] for a in accounts}

        def event(key, store, title, amount, direction, due, target="", currency="EUR", **extra):
            events.append({"key": key, "store_id": store, "title": title, "amount_cents": amount, "direction": direction, "due_date": due, "expected_date": due if due and due >= stamp else None, "target": target, "currency": currency, "confidence": "provavel", "source": key.split(":", 1)[0], **extra})
            events[-1]["source_url"] = "/api/liquidity/origin/" + key
            events[-1]["origin_fingerprint"] = digest(events[-1])

        ids = [s for s in scope(user, db) if not store_id or s == store_id]
        for row in db.scalars(select(PlannedMovement).where(PlannedMovement.store_id.in_(ids))):
            data = planned_summary(db, row)
            event("planned:" + row.id, row.store_id, row.entity + " · " + row.document, max(0, data["remaining_cents"]), row.direction, row.due_date, entity=row.entity, document=row.document, source_url="?page=Movimentos%20previstos", updated_at=row.created_at.isoformat())
            events[-1]["paid_cents"] = data["paid_cents"]
            events[-1]["paid_source"] = "Liquidações locais registadas"
        confirmations = {r.target_id for r in db.scalars(select(Audit).where(Audit.action == "liquidacao_wintouch_concluida", Audit.store_id.in_(ids)))}
        erp_paid = {}
        seen = set()
        for audit_row in db.scalars(select(Audit).where(Audit.action == "liquidacao_wintouch_iniciada", Audit.store_id.in_(ids))):
            detail = json.loads(audit_row.detail)
            identifier = detail.get("settlement_id")
            if identifier in confirmations and identifier not in seen:
                erp_paid[audit_row.target_id] = erp_paid.get(audit_row.target_id, 0) + detail.get("amount_cents", 0)
                seen.add(identifier)
        for row in sales_list(user=user, db=db):
            if row["kind"] == "previsto" and row["store_id"] in ids:
                event("wintouch:" + row["id"], row["store_id"], (row.get("entity") or "Entidade não indicada") + " · " + row["document"], row["amount_cents"], row["direction"], row.get("due_date"), currency=row.get("currency") or "EUR", source_url="?page=Movimentos%20previstos", updated_at=str(row["synced_at"]), entity=row.get("entity") or "", document=row["document"], document_id=row.get("source_document_id"), document_date=row.get("document_date") or day(row["occurred_at"]))
                events[-1]["paid_cents"] = erp_paid.get(row["id"])
                events[-1]["paid_source"] = "Liquidações no ERP confirmadas através da Zela; pode não incluir pagamentos anteriores ou feitos diretamente no ERP"
        for row in db.scalars(select(LiquidityManual).where(LiquidityManual.store_id.in_(ids), LiquidityManual.active.is_(True))):
            data = json.loads(row.data)
            for index, occurrence in enumerate(occurrences(data, end)):
                key = "manual:" + row.id + (":" + occurrence if data["recurrence"] != "none" and occurrence else "")
                due = data.get("due_date")
                if due and data["recurrence"] != "none":
                    anchor = date.fromisoformat(due)
                    number = anchor.year * 12 + anchor.month - 1 + index * {"monthly": 1, "quarterly": 3, "yearly": 12}[data["recurrence"]]
                    year, month = number // 12, number % 12 + 1
                    due = date(year, month, min(anchor.day, calendar.monthrange(year, month)[1])).isoformat()
                event(key, row.store_id, data["title"], data["amount_cents"], data["direction"], due, target=data["target"], currency=data["currency"], expected_date=occurrence, from_target=data["from_target"], transfer=bool(data["from_target"]), confidence=data["confidence"], manual_id=row.id, recurrence=data["recurrence"], updated_at=row.updated_at.isoformat())
        from .deposits import credit_total, cents
        transit = []
        for row in db.scalars(select(BankDeposit).where(BankDeposit.store_id.in_(ids), BankDeposit.status.notin_(["rascunho", "cancelado", "reconciliado"]))):
            remaining = cents(row.cash_amount + row.cheque_amount - credit_total(db, row))
            if remaining <= 0:
                continue
            bank = targets(db, user).get("bank:" + row.account_id)
            cash = targets(db, user).get("cash:" + row.register_id)
            if not bank or not cash or bank.currency != cash.currency:
                warnings.append(row.number + ": depósito sem destino acessível ou com moedas incompatíveis; excluído.")
                continue
            in_transit = row.status != "preparado"
            event("deposit:" + row.id, row.store_id, row.number + " · Depósito " + ("em trânsito" if in_transit else "preparado"), remaining, 1, None, target="bank:" + row.account_id, currency=bank.currency, expected_date=None, from_target="" if in_transit else "cash:" + row.register_id, transfer=True, confidence="confirmado", source_url="?page=Dep%C3%B3sitos", deposit_status=row.status)
            if in_transit:
                transit.append({"key": "deposit:" + row.id, "amount_cents": remaining, "currency": bank.currency, "title": row.number})
        original_keys = {e["key"] for e in events}
        by_key = {e["key"]: e for e in events}
        for key, item in by_key.items():
            payments = [(link, payment) for link, payment in local_settlements if link.source_key == key]
            if not payments or item["source"] == "planned":
                continue
            item["local_settlement_conflict"] = any(digest(item) != json.loads(link.origin_data)["fingerprint"] for link, payment in payments)
            item["origin_amount_cents"] = item["amount_cents"]
            paid = sum(payment.amount_cents for link, payment in payments)
            item["amount_cents"] = max(0, item["amount_cents"] - paid)
            item["paid_cents"] = (item.get("paid_cents") or 0) + paid
            item["paid_source"] = "Liquidações locais registadas; não enviadas ao ERP"
        for row in db.scalars(select(LiquidityAdjustment).where(LiquidityAdjustment.store_id.in_(ids))):
            if row.source_key not in by_key:
                warnings.append("Ajuste sem origem ativa: " + row.source_key + ". Pode ter sido liquidada ou removida na sincronização.")
                continue
            item = by_key[row.source_key]
            current_fingerprint = digest(item)
            partial_update = current_fingerprint != row.fingerprint and confirmed_partial_update(db, item, row)
            changes = json.loads(row.data)
            item.update({k: changes[k] for k in ("expected_date", "target", "confidence", "replaces_key") if k in changes})
            local_update = item["origin_fingerprint"] == row.fingerprint and not item.get("local_settlement_conflict")
            item["conflict"] = current_fingerprint != row.fingerprint and not partial_update and not local_update
            item["adjustment_reason"] = changes["reason"]
            if item.get("replaces_key") and item["replaces_key"] not in original_keys:
                item["conflict"] = True
            if item.get("target") and item["target"] not in allowed:
                item["conflict"] = True
        for e in events:
            if e.get("local_settlement_conflict"):
                e["conflict"] = True
            e["fingerprint"] = digest(e)
            e["overdue_days"] = max(0, (today() - date.fromisoformat(e["due_date"])).days) if e.get("due_date") else None
        runs = list(db.scalars(select(WintouchSyncRun).where(WintouchSyncRun.resource == "sales").order_by(WintouchSyncRun.started_at.desc()).limit(1)))
        if runs and runs[0].status != "concluida":
            warnings.append("Última sincronização ERP não concluída: os dados apresentados são os últimos guardados.")
        imported = [e for e in events if e["source"] == "wintouch"]
        if imported:
            last_sync = max(e["updated_at"] for e in imported)
            if (today() - date.fromisoformat(last_sync[:10])).days >= cfg["stale_days"]:
                warnings.append("Documentos ERP desatualizados; última atualização: " + last_sync[:10])
        return accounts, events, warnings, transit

    def scenario(db, user, key):
        if key in ("base", "conservative", "custom"):
            return None
        row = db.get(LiquidityScenario, key)
        if not row or (row.user_id != user.id and (not row.shared or not set(json.loads(row.data).get("allowed_stores", [])) <= set(scope(user, db)))):
            raise HTTPException(404, "Cenário indisponível nesta empresa e perfil.")
        return row

    def forecast(data, user, db):
        if data.start < today() or data.end < data.start or (data.end - today()).days > 365:
            raise HTTPException(422, "Use um período futuro com duração máxima de 366 dias a partir de hoje.")
        if data.store_id:
            authorize(user, db, data.store_id)
        accounts, events, warnings, transit = source_data(db, user, data.end, data.store_id)
        selected = {a["key"] for a in accounts} if data.targets is None else set(data.targets)
        if not selected <= {a["key"] for a in accounts}:
            raise HTTPException(404, "Conta ou caixa indisponível na seleção.")
        saved = scenario(db, user, data.scenario_id)
        config = json.loads(saved.data) if saved else {"mode": data.scenario_id, "delay_days": data.delay_days, "exclude_uncertain": data.exclude_uncertain, "changes": {}, "extras": []}
        assumptions = {"delay_days": config["delay_days"] if config["mode"] == "conservative" else 0, "exclude_uncertain": config["exclude_uncertain"] if config["mode"] == "conservative" else False, "mode": config["mode"], "recurrence_rule": "Dia original, limitado ao último dia do mês", "start_balance": "Saldo atual; movimentos anteriores ao início escolhido são projetados até esse dia"}
        if config["mode"] == "custom":
            for key in config.get("changes", {}):
                if key not in {event["key"] for event in events}:
                    warnings.append("Uma alteração do cenário perdeu a origem ativa; reveja a simulação: " + key)
        for item in events:
            change = config.get("changes", {}).get(item["key"])
            if change and config["mode"] == "custom":
                item.update({k: v for k, v in change.items() if k == "expected_date" or v is not None})
                item["simulation"] = True
        for index, extra in enumerate(config.get("extras", []) if config["mode"] == "custom" else []):
            if not extra.get("active", True):
                continue
            if extra["store_id"] not in scope(user, db) or data.store_id and extra["store_id"] != data.store_id:
                continue
            if any(extra.get(k) and extra[k] not in {a["key"] for a in accounts} for k in ("target", "from_target")):
                warnings.append("Simulação com conta indisponível; excluída.")
                continue
            for occurrence in occurrences(extra, data.end):
                events.append({"key": f"simulation:{index}:{occurrence}", **extra, "expected_date": occurrence, "source": "simulation", "simulation": True, "transfer": bool(extra.get("from_target"))})
        cfg = settings(db)
        groups, pending, scheduled = project(accounts, events, selected, data.start, data.end, cfg["reserves"], assumptions)
        for currency, group in groups.items():
            if group["incomplete"]:
                warnings.append(currency + ": saldo inicial parcial; os saldos indisponíveis não estão incluídos no cálculo.")
        if any(a["missing"] or a["stale"] or a["kind"] == "estimado" for a in accounts if a["key"] in selected):
            warnings.append("A previsão utiliza saldos incompletos, estimados ou desatualizados; os indicadores não representam disponibilidade bancária confirmada.")
        result = {"updated_at": now().isoformat(), "company": db.info.get("company", {}), "scenario": {"id": data.scenario_id, "name": saved.name if saved else {"base": "Base", "conservative": "Conservador", "custom": "Personalizado"}[data.scenario_id], "assumptions": assumptions}, "settings": cfg, "accounts": accounts, "selected": sorted(selected), "groups": groups, "pending": pending, "events": events, "scheduled": scheduled, "warnings": warnings, "transit": transit, "period": {"start": data.start.isoformat(), "end": data.end.isoformat()}}
        if data.compare:
            base_groups, _, _ = project(accounts, source_data(db, user, data.end, data.store_id)[1], selected, data.start, data.end, cfg["reserves"], {"delay_days": 0, "exclude_uncertain": False})
            result["comparison"] = base_groups
        return result

    @app.post("/api/liquidity/forecast")
    def get_forecast(data: ForecastInput, user=Depends(current), db: Session = Depends(db_session)):
        return forecast(data, user, db)

    @app.post("/api/liquidity/settlement-proposal")
    def settlement_proposal(data: SettlementProposalInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=managers)
        if data.store_id:
            authorize(user, db, data.store_id)
        events = source_data(db, user, today() + timedelta(days=365), data.store_id)[1]
        erp = erp_status(db)
        candidates = []
        for item in events:
            if item["source"] not in ("planned", "wintouch", "manual") or item["direction"] != data.direction or item["currency"] != data.currency or item["amount_cents"] <= 0 or item.get("from_target") or item.get("transfer"):
                continue
            entity = item.get("entity") or "Entidade não indicada"
            overdue = bool(item.get("due_date") and item["due_date"] < today().isoformat())
            if data.overdue_only and not overdue:
                continue
            route = "erp" if item["source"] == "wintouch" and erp["active"] else "local"
            blocked = "Reveja o conflito na origem." if item.get("conflict") else ""
            if route == "erp" and (erp.get("can_settle") is False or erp.get("provider") != "wintouch"):
                blocked = "A ligação ERP não permite liquidar neste momento. Não será criado um pagamento local em substituição."
            candidates.append({**item, "entity": entity, "route": route, "blocked": blocked})
        entities = sorted({e["entity"] for e in candidates}, key=str.casefold)
        candidates = [e for e in candidates if not data.entity or e["entity"] == data.entity]
        candidates.sort(key=lambda e: (e.get("due_date") or "9999-12-31", e["entity"].casefold(), e["key"]))
        available = sum(e["amount_cents"] for e in candidates if not e["blocked"])
        remaining = data.amount_cents if data.amount_cents is not None else available
        lines = []
        for item in candidates:
            proposed = min(item["amount_cents"], remaining) if not item["blocked"] else 0
            remaining -= proposed
            if proposed or item["blocked"]:
                lines.append({**item, "proposed_cents": proposed})
        return {"entities": entities, "lines": lines, "total_cents": sum(e["proposed_cents"] for e in lines), "available_cents": available, "unallocated_cents": remaining, "currency": data.currency, "as_of": today().isoformat(), "rule": "Documentos por ordem de vencimento, do mais antigo para o mais recente; o último pode ter liquidação parcial."}

    @app.post("/api/liquidity/settlement-sustainability")
    def settlement_sustainability(data: SustainabilityInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=managers)
        account = targets(db, user).get(data.target)
        if not data.target.startswith("bank:") or not account or account.currency != "EUR":
            raise HTTPException(422, "Escolha uma conta bancária em EUR da empresa.")
        end = today() + timedelta(days=data.horizon_days - 1)
        accounts, events, _, _ = source_data(db, user, end)
        bank = next(a for a in accounts if a["key"] == data.target)
        indexed = {e["key"]: e for e in events}
        selected = {}
        effect = 0
        for line in data.lines:
            item = indexed.get(line.key)
            if line.key in selected or not item or item["source"] not in ("planned", "wintouch", "manual") or item["store_id"] != account.store_id or item["currency"] != "EUR" or item.get("conflict") or item.get("from_target") or item["amount_cents"] < line.amount_cents:
                raise HTTPException(409, "Os documentos, valores ou loja não correspondem a este banco. Atualize a proposta.")
            selected[line.key] = line.amount_cents
            effect += item["direction"] * line.amount_cents
        other_outgoing, unassigned = 0, 0
        for item in events:
            is_transfer_out = item.get("from_target") == data.target
            is_out = item["direction"] == -1 and not item.get("from_target") and (item.get("target") == data.target or not item.get("target") and item["store_id"] == account.store_id)
            stamp = item.get("expected_date") or item.get("due_date")
            if not (is_out or is_transfer_out) or item["currency"] != "EUR" or stamp and stamp > end.isoformat():
                continue
            remaining = max(0, item["amount_cents"] - selected.get(item["key"], 0))
            other_outgoing += remaining
            if not item.get("target") and not is_transfer_out:
                unassigned += remaining
        reserve = settings(db)["reserves"].get("EUR", 0)
        balance = bank["balance_cents"]
        after = balance + effect - data.reserved_cents if balance is not None else None
        minimum = after - other_outgoing if after is not None else None
        shortfall = max(0, reserve - minimum) if minimum is not None else None
        valid = balance is not None and not bank["stale"] and shortfall == 0
        return {"valid": valid, "target": data.target, "bank": bank["name"], "balance_cents": balance, "after_cents": after, "other_outgoing_cents": other_outgoing, "minimum_cents": minimum, "reserve_cents": reserve, "shortfall_cents": shortfall, "unassigned_outgoing_cents": unassigned, "balance_date": bank["balance_date"], "estimated": bank["kind"] == "estimado", "stale": bank["stale"], "end": end.isoformat(), "message": "Saldo sustentável para esta seleção e horizonte." if valid else "Saldo indisponível ou desatualizado." if balance is None or bank["stale"] else "O pagamento e as restantes saídas ultrapassam o saldo ou a reserva mínima.", "assumption": "Não conta com recebimentos futuros. Inclui, por prudência, as saídas sem conta associada desta loja e os valores ainda por liquidar, sem duplicar os pagamentos propostos."}

    @app.get("/api/liquidity/origin/{key:path}")
    def origin(key: str, user=Depends(current), db: Session = Depends(db_session)):
        source, _, identifier = key.partition(":")
        if source == "planned":
            row = db.get(PlannedMovement, identifier)
            if row:
                authorize(user, db, row.store_id)
                return {"source": "Movimento previsto local", "data": planned_summary(db, row)}
        elif source == "wintouch":
            row = next((r for r in sales_list(user=user, db=db) if r["id"] == identifier), None)
            if row:
                record = db.get(WintouchSaleRecord, identifier)
                purchases = list(db.scalars(select(Purchase.id).join(DocumentERPLink, DocumentERPLink.purchase_id == Purchase.id).join(ERPDocument, ERPDocument.id == DocumentERPLink.erp_id).where(
                    DocumentERPLink.undone_at.is_(None), ERPDocument.external_id == row.get("source_document_id"),
                    ERPDocument.enterprise_id == record.enterprise_id, ERPDocument.store_id == row["store_id"], Purchase.store_id == row["store_id"],
                )))
                attachments = [{"id": r.id, "name": r.name, "view_url": "/api/documents/attachments/" + r.id + "/view"} for r in db.execute(select(PurchaseAttachment.id, PurchaseAttachment.name).where(PurchaseAttachment.purchase_id.in_(purchases)))]
                attachments += [{"id": "drive:" + r.id, "name": r.name, "view_url": "/api/drive-attachments/" + r.id + "/view"} for r in db.execute(select(DriveDocumentAttachment.id, DriveDocumentAttachment.name).where(DriveDocumentAttachment.purchase_id.in_(purchases)))]
                return {"source": "Último documento sincronizado do ERP", "data": row, "attachments": attachments}
        elif source == "manual":
            row = db.get(LiquidityManual, identifier.split(":")[0])
            if row:
                authorize(user, db, row.store_id)
                return {"source": "Compromisso manual da previsão", "data": json.loads(row.data)}
        elif source == "deposit":
            row = db.get(BankDeposit, identifier)
            if row:
                authorize(user, db, row.store_id)
                return {"source": "Depósito bancário", "data": {"document": row.number, "status": row.status, "amount_cents": int((row.cash_amount + row.cheque_amount) * 100), "due_date": row.preparation_date, "exit_date": row.exit_date, "delivery_date": row.delivery_date, "account_id": row.account_id, "register_id": row.register_id}}
        raise HTTPException(404, "Origem indisponível nesta empresa e perfil.")

    @app.get("/api/liquidity/setup")
    def setup(user=Depends(current), db: Session = Depends(db_session)):
        return {"erp": erp_status(db), "settings": settings(db), "manual": [serial(r) | {"data": json.loads(r.data)} for r in db.scalars(select(LiquidityManual).where(LiquidityManual.store_id.in_(scope(user, db))))], "scenarios": [serial(r) | {"data": json.loads(r.data)} for r in db.scalars(select(LiquidityScenario).where((LiquidityScenario.user_id == user.id) | LiquidityScenario.shared.is_(True))) if r.user_id == user.id or set(json.loads(r.data).get("allowed_stores", [])) <= set(scope(user, db))], "permissions": {"consult": True, "manual": user.role in writers, "adjust": user.role in writers, "recurrence": user.role in managers, "scenario": user.role in writers, "share": user.role in managers, "reserve": user.role == "administrador"}}

    @app.post("/api/liquidity/settings")
    def save_settings(data: SettingsInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        import re
        if any(not re.fullmatch("[A-Z]{3}", currency) or isinstance(value, bool) or value < 0 or value > 10**13 for currency, value in data.reserves.items()):
            raise HTTPException(422, "Reserva ou moeda inválida.")
        row = db.get(LiquiditySettings, "default") or LiquiditySettings(id="default")
        db.add(row)
        row.reserves, row.stale_days = json.dumps(data.reserves), data.stale_days
        audit(db, user, "liquidez_reserva_alterada", "default", data.model_dump())
        db.commit()
        return settings(db)

    def manual_save(data, user, db, key=None):
        authorize(user, db, data.store_id, writers)
        row = db.get(LiquidityManual, key) if key else None
        if key and not row:
            raise HTTPException(404, "Compromisso indisponível.")
        if row:
            authorize(user, db, row.store_id, writers)
        if data.recurrence != "none" or row and json.loads(row.data)["recurrence"] != "none":
            authorize(user, db, roles=managers)
            anchor = data.expected_date or data.due_date
            if not anchor or data.recurrence_end and data.recurrence_end < anchor:
                raise HTTPException(422, "Defina início e fim válidos para a recorrência.")
        validate_target(db, user, data.target, data.currency, data.store_id)
        validate_target(db, user, data.from_target, data.currency)
        if data.from_target and (not data.target or data.from_target == data.target):
            raise HTTPException(422, "Uma transferência exige origem e destino distintos.")
        if row is None:
            row = LiquidityManual(user_id=user.id, store_id=data.store_id)
            db.add(row)
        row.data, row.active, row.updated_at = data.model_dump_json(), data.active, now()
        db.flush()
        audit(db, user, "liquidez_compromisso_guardado", row.id, data.model_dump(mode="json"), data.store_id)
        db.commit()
        return serial(row)

    @app.post("/api/liquidity/manual")
    def create_manual(data: ManualInput, user=Depends(current), db: Session = Depends(db_session)):
        return manual_save(data, user, db)

    @app.post("/api/liquidity/manual/{key}")
    def edit_manual(key: str, data: ManualInput, user=Depends(current), db: Session = Depends(db_session)):
        return manual_save(data, user, db, key)

    @app.post("/api/liquidity/settle/{key:path}")
    def local_settle(key: str, data: LocalSettlementInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=managers)
        source, _, identifier = key.partition(":")
        model = {"wintouch": WintouchSaleRecord, "planned": PlannedMovement, "manual": LiquidityManual}.get(source)
        origin = db.scalar(select(model).where(model.id == identifier.split(":")[0]).with_for_update()) if model else None
        if not origin:
            raise HTTPException(404, "Documento local indisponível.")
        authorize(user, db, origin.store_id, managers)
        if source == "wintouch" and erp_status(db)["active"]:
            raise HTTPException(409, "A empresa está em modo integrado. A liquidação externa depende das capacidades e da ligação ERP; uma falha de ligação não autoriza liquidação local automática.")
        previous = db.scalar(select(LiquiditySettlement).where(LiquiditySettlement.request_key == data.request_key))
        if previous:
            payment = db.get(DocumentPayment, previous.payment_id)
            if previous.source_key != key or previous.target != data.target or payment.amount_cents != data.amount_cents or payment.payment_date != data.payment_date.isoformat():
                raise HTTPException(409, "Referência de liquidação já utilizada.")
            return {"id": previous.id, "message": "Liquidação local já registada."}
        if data.payment_date > today():
            raise HTTPException(422, "Um pagamento realizado não pode ter uma data futura.")
        item = next((e for e in source_data(db, user, today() + timedelta(days=365))[1] if e["key"] == key), None)
        if not item or item["amount_cents"] <= 0:
            raise HTTPException(409, "O documento já não tem saldo por liquidar.")
        if item.get("from_target") or item.get("conflict"):
            raise HTTPException(409, "Reveja a origem do documento antes de liquidar.")
        if data.amount_cents > item["amount_cents"]:
            raise HTTPException(422, "O valor ultrapassa o saldo por liquidar.")
        if item["currency"] != "EUR":
            raise HTTPException(422, "O registo atual de pagamentos reais suporta apenas EUR.")
        validate_target(db, user, data.target, item["currency"], item["store_id"])
        category = db.get(Category, data.category_id)
        if not category:
            raise HTTPException(404, "Rubrica indisponível.")
        from .deposits import unlocked
        unlocked(db, [data.payment_date.isoformat()])
        move = None
        entity = json.loads(origin.data).get("entity", "") if source == "wintouch" else getattr(origin, "entity", "")
        if data.target.startswith("cash:"):
            register_id = data.target.partition(":")[2]
            session = db.scalar(select(CashSession).where(CashSession.register_id == register_id, CashSession.closed_at.is_(None)).with_for_update())
            if not session or session.sheet_day != data.payment_date.isoformat():
                raise HTTPException(409, "Selecione uma caixa com folha aberta na data do pagamento.")
            from .main import balance
            if item["direction"] == -1 and balance(db, session) < data.amount_cents:
                raise HTTPException(409, "Numerário insuficiente nesta caixa.")
            move = Movement(session_id=session.id, user_id=user.id, category_id=category.id, kind="cliente" if item["direction"] == 1 else "fornecedor", payment="numerario", amount_cents=data.amount_cents, direction=item["direction"], occurred_at=datetime.combine(data.payment_date, time(12), tzinfo=ZoneInfo("Europe/Lisbon")), entity=str(entity or "")[:200], document=item["title"][:200], reference=key, notes=data.reason, origin="manual", request_key=data.request_key)
            db.add(move); db.flush()
        payment = DocumentPayment(store_id=item["store_id"], payment_date=data.payment_date.isoformat(), amount_cents=data.amount_cents, direction=item["direction"], reference=item["title"][:200], entity=str(entity or "")[:300], method="numerario" if move else "transferencia", source="movement" if move else "manual", source_id=move.id if move else None, request_key=data.request_key)
        db.add(payment); db.flush()
        link = LiquiditySettlement(source_key=key, target=data.target, payment_id=payment.id, movement_id=move.id if move else None, user_id=user.id, origin_data=json.dumps({"fingerprint": item["origin_fingerprint"]}), request_key=data.request_key)
        db.add(link); db.flush()
        adjustment = db.get(LiquidityAdjustment, key)
        if adjustment:
            refreshed = next(e for e in source_data(db, user, today() + timedelta(days=365))[1] if e["key"] == key)
            adjustment.fingerprint = refreshed["origin_fingerprint"]
        audit(db, user, "liquidez_liquidacao_local", link.id, {**data.model_dump(mode="json"), "source_key": key, "payment_id": payment.id}, item["store_id"])
        db.commit()
        return {"id": link.id, "message": "Liquidação local registada. Não foi enviada ao ERP."}

    @app.post("/api/liquidity/adjust/{key:path}")
    def adjust(key: str, data: AdjustmentInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=writers)
        events = source_data(db, user, today() + timedelta(days=365))[1]
        item = next((e for e in events if e["key"] == key), None)
        if not item:
            raise HTTPException(404, "Movimento indisponível na previsão.")
        authorize(user, db, item["store_id"], writers)
        validate_target(db, user, data.target, item["currency"], item["store_id"])
        if data.replaces_key:
            replacement = next((e for e in events if e["key"] == data.replaces_key), None)
            if not replacement or replacement.get("recurrence", "none") == "none" or item["source"] not in ("planned", "wintouch") or replacement["store_id"] != item["store_id"] or replacement["currency"] != item["currency"] or replacement["direction"] != item["direction"]:
                raise HTTPException(422, "Associe uma ocorrência recorrente do mesmo estabelecimento, moeda e sentido.")
        row = db.get(LiquidityAdjustment, key)
        # Revalidation uses the current origin, not the previously applied adjustment.
        if row:
            db.delete(row)
            db.flush()
            item = next(e for e in source_data(db, user, today() + timedelta(days=365))[1] if e["key"] == key)
        row = LiquidityAdjustment(source_key=key, store_id=item["store_id"], user_id=user.id, fingerprint=digest(item), data=data.model_dump_json())
        db.add(row)
        audit(db, user, "liquidez_data_ajustada", key, data.model_dump(mode="json"), item["store_id"])
        db.commit()
        return {"ok": True}

    @app.post("/api/liquidity/scenarios")
    def save_scenario(data: ScenarioInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=writers)
        if data.shared:
            authorize(user, db, roles=managers)
        for extra in data.extras:
            authorize(user, db, extra.store_id, writers)
            validate_target(db, user, extra.target, extra.currency, extra.store_id)
            validate_target(db, user, extra.from_target, extra.currency)
            if extra.from_target and (not extra.target or extra.target == extra.from_target):
                raise HTTPException(422, "Transferência simulada inválida.")
            if extra.recurrence != "none":
                authorize(user, db, roles=managers)
        sources = {e["key"]: e for e in source_data(db, user, today() + timedelta(days=365))[1]}
        for key, change in data.changes.items():
            item = sources.get(key)
            if not item:
                raise HTTPException(404, "Movimento da simulação indisponível.")
            if change.target:
                validate_target(db, user, change.target, item["currency"], item["store_id"])
        payload = data.model_dump(mode="json")
        payload["changes"] = {key: change.model_dump(mode="json", exclude_unset=True) for key, change in data.changes.items()}
        payload["allowed_stores"] = scope(user, db)
        row = LiquidityScenario(user_id=user.id, name=data.name, shared=data.shared, data=json.dumps(payload))
        db.add(row)
        db.flush()
        audit(db, user, "liquidez_cenario_guardado", row.id, data.model_dump(mode="json"))
        db.commit()
        return serial(row)

    @app.post("/api/liquidity/export")
    def export(data: ForecastInput, user=Depends(current), db: Session = Depends(db_session)):
        result = forecast(data, user, db)
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        def safe(value):
            text = str(value or "")
            return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
        writer.writerow(["Empresa", safe(result["company"].get("name")), "Cenário", safe(result["scenario"]["name"]), "Atualização", result["updated_at"]])
        writer.writerow(["Premissas", safe(json.dumps(result["scenario"]["assumptions"], ensure_ascii=False))])
        writer.writerow(["Moeda", "Data", "Saldo inicial", "Entradas", "Saídas", "Saldo final", "Reserva"])
        def number(value):
            return f"{value / 100:.2f}".replace(".", ",")
        def display_date(value):
            return date.fromisoformat(value).strftime("%d/%m/%Y") if value else ""
        for currency, group in result["groups"].items():
            for row in group["daily"]:
                writer.writerow([currency, date.fromisoformat(row["date"]).strftime("%d/%m/%Y"), *[number(row[k]) for k in ("opening_cents", "incoming_cents", "outgoing_cents", "closing_cents")], number(group["reserve_cents"])])
        writer.writerow(["Movimentos explicativos"])
        writer.writerow(["Origem", "Documento", "Data do documento", "Vencimento original", "Data esperada", "Moeda", "Valor", "Sentido", "Conta", "Confiança", "Exclusão"])
        for item in result["scheduled"] + result["pending"]:
            writer.writerow([safe(item["key"]), safe(item["title"]), display_date(item.get("document_date")), display_date(item.get("due_date")), display_date(item.get("expected_date")), item["currency"], number(item["amount_cents"]), item["direction"], safe(item.get("target")), item["confidence"], safe(item.get("exclusion"))])
        writer.writerow(["Qualidade dos saldos"])
        for account in result["accounts"]:
            writer.writerow([safe(account["name"]), account["currency"], number(account["balance_cents"]) if account["balance_cents"] is not None else "Indisponível", safe(account["source"]), account["updated_at"], account["kind"]])
        for warning in result["warnings"]:
            writer.writerow(["Limitação", safe(warning)])
        return Response(content=("\ufeff" + output.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="previsao-liquidez.csv"'})
