"""Movimentos de uma rubrica (Dashboard e Despesas por rubrica): consulta, pesquisa e correção da classificação.

Os registos e valores são os mesmos do gráfico (rubric_nature.contributions sobre cost_documents e saídas de
tesouraria): cada documento conta uma vez, pela data do documento; um pagamento ligado a uma fatura não é uma
segunda despesa. Corrigir a rubrica usa sempre o circuito próprio de cada origem e nunca cria movimentos:

- documento do ERP ou compra sem linhas: repartição de gestão (classification.apply_one; o ERP não é alterado);
- compra com linhas: a rubrica vem das linhas (corrige-se em Compras);
- registo bancário ou saída de caixa: rubrica de análise (rubric_reclassifications; o registo não muda) — só
  administrador; o gestor pede aprovação.

Valores, IVA, datas e documentos nunca são alterados. Cada alteração fica na auditoria e pode ser revertida; o
centro de custo e as observações são classificação de gestão (record_annotations).
"""

import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .models import (
    Audit,
    BankAccount,
    BankBookEntry,
    Category,
    CostCenter,
    DocumentERPLink,
    DocumentPayment,
    ERPDocument,
    Movement,
    PaymentDocumentLine,
    Purchase,
    Reconciliation,
    ReconciliationLine,
    RecordAnnotation,
    RubricReclassification,
    User,
    now,
)
from .rubric_nature import Reason
from .schemas import Strict

router = APIRouter()
ROLES = ["administrador", "gestor"]
# Onde foi feita a alteração (fica na auditoria).
Origin = Literal["despesas_por_rubrica", "dashboard", "mapas_de_gestao"]
ORIGINS = {"erp": "ERP", "purchase": "Compra", "efatura": "e-Fatura", "bank": "Banco", "move": "Caixa"}


def fail(message, status=422):
    raise HTTPException(status, message)


def stores_for(user, db, store_id):
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        stores = [store_id] if store_id in stores else []
    return stores


def mode_of(db, user, identifier):
    """(modo de edição, motivo de bloqueio) de um registo."""
    from .classification import load
    if identifier.startswith(("bank:", "move:")):
        return identifier.split(":", 1)[0], "" if user.role == "administrador" else "Só o administrador corrige pagamentos do banco e da caixa: peça aprovação."
    if identifier.startswith("erp:"):
        return "erp", ""
    try:
        _, _, target = load(db, user, "purchase", identifier)
    except HTTPException as exc:
        return "unavailable", exc.detail
    if target[0] == "lines":
        return "lines", "Compra com linhas lançadas: a rubrica vem das linhas e corrige-se em Compras."
    return "purchase", ""


def states(db, ids):
    """Estado de reconciliação/pagamento e data do pagamento por registo."""
    result = {}
    plain = [i for i in ids if ":" not in i]
    erp = [i[4:] for i in ids if i.startswith("erp:")]
    links = dict(db.execute(select(DocumentERPLink.erp_id, DocumentERPLink.purchase_id).where(
        DocumentERPLink.erp_id.in_(erp), DocumentERPLink.undone_at.is_(None))).all()) if erp else {}
    purchases = set(plain) | set(links.values())
    paid, paid_on = defaultdict(int), {}
    if purchases:
        for purchase_id, amount, day in db.execute(
                select(PaymentDocumentLine.purchase_id, PaymentDocumentLine.amount_cents, DocumentPayment.payment_date)
                .join(DocumentPayment, DocumentPayment.id == PaymentDocumentLine.payment_id)
                .where(PaymentDocumentLine.purchase_id.in_(purchases))):
            paid[purchase_id] += amount
            paid_on[purchase_id] = max(paid_on.get(purchase_id, ""), day or "")
        for source_id, amount in db.execute(
                select(ReconciliationLine.source_id, func.sum(ReconciliationLine.amount_cents)).join(Reconciliation)
                .where(Reconciliation.undone_at.is_(None), ReconciliationLine.source == "purchase", ReconciliationLine.source_id.in_(purchases))
                .group_by(ReconciliationLine.source_id)):
            paid[source_id] += int(amount)
        amounts = {p.id: abs(p.amount_cents) for p in db.scalars(select(Purchase).where(Purchase.id.in_(purchases)))}
    else:
        amounts = {}

    def purchase_state(purchase_id):
        total, done = amounts.get(purchase_id, 0), paid.get(purchase_id, 0)
        return ("pago" if total and done >= total else "parcial" if done else "por_pagar"), paid_on.get(purchase_id)

    for identifier in plain:
        result[identifier] = purchase_state(identifier)
    for erp_id in erp:
        result["erp:" + erp_id] = purchase_state(links[erp_id]) if erp_id in links else ("sem_ligacao", None)
    book = [i[5:] for i in ids if i.startswith("bank:")]
    moves = [i[5:] for i in ids if i.startswith("move:")]
    reconciled = set()
    if book or moves:
        reconciled = {(s, i) for s, i in db.execute(select(ReconciliationLine.source, ReconciliationLine.source_id).join(Reconciliation).where(
            Reconciliation.undone_at.is_(None), ReconciliationLine.source.in_(("book", "movement")),
            ReconciliationLine.source_id.in_(book + moves)))}
    entries = {e.id: e for e in db.scalars(select(BankBookEntry).where(BankBookEntry.id.in_(book)))} if book else {}
    for entry_id in book:
        entry = entries.get(entry_id)
        # Um registo criado a partir do extrato já nasce associado ao movimento bancário.
        result["bank:" + entry_id] = ("reconciliado" if entry and (entry.source_bank_id or ("book", entry_id) in reconciled) else "por_reconciliar",
                                      entry.movement_date if entry else None)
    movement_rows = {m.id: m for m in db.scalars(select(Movement).where(Movement.id.in_(moves)))} if moves else {}
    for move_id in moves:
        move = movement_rows.get(move_id)
        state = "caixa" if move and move.payment == "numerario" else "reconciliado" if ("movement", move_id) in reconciled else "por_reconciliar"
        result["move:" + move_id] = (state, move.occurred_at.date().isoformat() if move else None)
    return result


@router.get("/api/rubric-movements")
def listing(start: date, end: date, store_id: str = "", view: str = "operating", category: str | None = None,
            user=Depends(current), db: Session = Depends(db_session)):
    from .dashboard import describe_sources, expense_contributions
    from .rubric_nature import VIEWS
    from .settings import vat_settings
    authorize(user, db, roles=ROLES)
    if start > end or (end - start).days > 400:
        fail("Período inválido (máximo de 400 dias).")
    # «costs»: custos do mapa de custos e proveitos (operacionais ou compras); «all»: qualquer rubrica (linhas fora dos custos).
    if view not in (*VIEWS, "costs", "all"):
        fail("Vista inválida.")
    stores = stores_for(user, db, store_id)
    regime = vat_settings(db)["vat_regime"]
    categories = {c.id: c for c in db.scalars(select(Category))}
    percents = {key: c.vat_deduction_percent for key, c in categories.items()}
    records = {}
    for day, identifier, key, base, tax, amount in expense_contributions(db, stores, start, end, regime, percents, view):
        row = records.setdefault(identifier, {"id": identifier, "date": day, "shares": [], "amount_cents": 0, "base_cents": 0, "tax_cents": 0})
        row["shares"].append({"category_id": key or None, "base_cents": base, "tax_cents": tax, "amount_cents": amount})
        row["amount_cents"] += amount
        row["base_cents"] += base
        row["tax_cents"] += tax
    rows = [row for row in records.values() if category is None or any((s["category_id"] or "") == category for s in row["shares"])]
    describe_sources(db, rows)
    known = states(db, [row["id"] for row in rows])
    notes = {a.record_id: a for a in db.scalars(select(RecordAnnotation).where(RecordAnnotation.record_id.in_([r["id"] for r in rows])))} if rows else {}
    accounts = {a.id: a for a in db.scalars(select(BankAccount))}
    entries = {e.id: e for e in db.scalars(select(BankBookEntry).where(BankBookEntry.id.in_([r["id"][5:] for r in rows if r["id"].startswith("bank:")])))}
    moves = {m.id: m for m in db.scalars(select(Movement).where(Movement.id.in_([r["id"][5:] for r in rows if r["id"].startswith("move:")])))}

    def name(key):
        item = categories.get(key)
        if not item:
            return "Por classificar"
        parent = categories.get(item.parent_id) if item.parent_id else None
        return f"{parent.name} › {item.name}" if parent else item.name

    for row in rows:
        identifier = row["id"]
        mode, blocked = mode_of(db, user, identifier)
        state, paid_on = known.get(identifier, ("", None))
        account = None
        if identifier.startswith("bank:") and (entry := entries.get(identifier[5:])):
            account = accounts.get(entry.account_id)
        elif identifier.startswith("move:") and (move := moves.get(identifier[5:])) and move.bank_account_id:
            account = accounts.get(move.bank_account_id)
        origin = "erp" if identifier.startswith("erp:") else identifier.split(":", 1)[0] if ":" in identifier else (
            "efatura" if row.get("source") == "e-Fatura" else "purchase")
        annotation = notes.get(identifier)
        for share in row["shares"]:
            share["name"] = name(share["category_id"])
        row.update(mode=mode, blocked=blocked, editable=not blocked and mode in ("erp", "purchase", "bank", "move"), state=state,
                   payment_date=paid_on, origin=origin, origin_label=ORIGINS[origin],
                   account_id=account.id if account else None, account=f"{account.bank} · {account.name}" if account else "",
                   category_amount_cents=sum(s["amount_cents"] for s in row["shares"] if category is None or (s["category_id"] or "") == category),
                   annotation={"cost_center_id": annotation.cost_center_id, "notes": annotation.notes, "revision": annotation.revision}
                   if annotation else {"cost_center_id": None, "notes": "", "revision": 0})
    rows.sort(key=lambda row: (row["date"], row["id"]), reverse=True)
    return {
        "rows": rows, "total_cents": sum(row["category_amount_cents"] for row in rows), "can_edit_payments": user.role == "administrador",
        "categories": [{"id": c.id, "name": name(c.id), "parent_id": c.parent_id, "color": c.color, "icon": c.icon}
                       for c in sorted(categories.values(), key=lambda c: name(c.id)) if c.active and not c.cash_difference],
        "cost_centers": [{"id": c.id, "name": f"{c.code} · {c.name}"} for c in db.scalars(select(CostCenter).where(CostCenter.active.is_(True)).order_by(CostCenter.code))],
        "accounts": [{"id": a.id, "name": f"{a.bank} · {a.name}"} for a in accounts.values() if a.store_id in stores],
    }


# -------------------------------------------------------------------------- correção da rubrica


class Change(Strict):
    ids: list[str] = Field(min_length=1, max_length=500)
    category_id: str = Field(min_length=1, max_length=36)


class Confirm(Change):
    fingerprint: str = Field(min_length=64, max_length=64)
    reason: Reason
    origin: Origin = "despesas_por_rubrica"


def current_rubrics(db, user, identifier):
    """Rubricas atuais de um registo (para a pré-visualização e a impressão digital)."""
    from .classification import current_split, load
    from .fiscal_payments import reclassified
    if identifier.startswith(("bank:", "move:")):
        model = BankBookEntry if identifier.startswith("bank:") else Movement
        item = db.get(model, identifier[5:])
        if not item:
            fail("Registo inexistente.", 404)
        return [reclassified(db).get(identifier, item.category_id)]
    kind, target_id = ("erp", identifier[4:]) if identifier.startswith("erp:") else ("purchase", identifier)
    _, _, target = load(db, user, kind, target_id)
    split = current_split(db, target)
    if split:
        return [category for category, _ in split]
    # Sem repartição própria: a rubrica da ficha do fornecedor (como nos mapas).
    from .invoice_evidence import nif_key
    from .rubric_expenses import supplier_rubrics
    doc = db.get(Purchase if kind == "purchase" else ERPDocument, target_id)
    return [supplier_rubrics(db).get(nif_key(doc.supplier_nif or "")) if doc else None]


def prepared(db, user, data):
    target = db.get(Category, data.category_id)
    if not target or not target.active or target.cash_difference:
        fail("Escolha uma rubrica ativa.")
    if len(set(data.ids)) != len(data.ids):
        fail("Há registos repetidos na seleção.")
    names = {c.id: c.name for c in db.scalars(select(Category))}
    from .fiscal_payments import load as load_payment
    from .models import Register
    stores, registers = scope(user, db), {r.id: r for r in db.scalars(select(Register))}
    items = []
    for identifier in data.ids:
        mode, blocked = mode_of(db, user, identifier)
        if mode in ("bank", "move"):
            load_payment(db, stores, identifier, registers)  # acesso à loja e pagamento ativo (não estornado)
        if mode == "unavailable":
            items.append({"id": identifier, "mode": mode, "blocked": blocked, "before": [], "before_names": []})
            continue
        before = current_rubrics(db, user, identifier)
        if not blocked and set(before) == {target.id}:
            blocked = f"Já está na rubrica «{target.name}»."
        items.append({"id": identifier, "mode": mode, "blocked": blocked, "before": before,
                      "before_names": [names.get(b, "Por classificar") for b in before]})
    return target, items


def fingerprint(target, items):
    payload = sorted((i["id"], i["mode"], sorted(str(b) for b in i["before"]), bool(i["blocked"])) for i in items)
    return hashlib.sha256(json.dumps([target.id, payload]).encode()).hexdigest()


def amounts_of(db, user, items):
    from .dashboard import describe_sources
    rows = [{"id": i["id"]} for i in items]
    describe_sources(db, rows)
    totals = {}
    for item in items:
        identifier = item["id"]
        if identifier.startswith("bank:"):
            entry = db.get(BankBookEntry, identifier[5:])
            totals[identifier] = abs(entry.amount_cents) if entry else 0
        elif identifier.startswith("move:"):
            move = db.get(Movement, identifier[5:])
            totals[identifier] = move.amount_cents if move else 0
        elif identifier.startswith("erp:"):
            doc = db.get(ERPDocument, identifier[4:])
            totals[identifier] = abs(doc.amount_cents) if doc else 0
        else:
            doc = db.get(Purchase, identifier)
            totals[identifier] = abs(doc.amount_cents) if doc else 0
    return {row["id"]: row for row in rows}, totals


@router.post("/api/rubric-movements/preview")
def preview(data: Change, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    target, items = prepared(db, user, data)
    described, totals = amounts_of(db, user, items)
    for item in items:
        item.update(label=described[item["id"]].get("label"), entity=described[item["id"]].get("entity"),
                    source=described[item["id"]].get("source"), amount_cents=totals[item["id"]])
    ready = [i for i in items if not i["blocked"]]
    return {
        "fingerprint": fingerprint(target, items), "target": target.name, "items": items,
        "count": len(ready), "total_cents": sum(i["amount_cents"] for i in ready),
        "blocked": len(items) - len(ready),
        "approval": [i["id"] for i in items if i["mode"] in ("bank", "move") and i["blocked"].startswith("Só o administrador")],
        "preserved": ["Valores, IVA, datas e documentos (nada é alterado no banco, na caixa nem no ERP)",
                      "Tesouraria e saldos bancários", "Nenhum movimento é criado ou duplicado"],
    }


@router.post("/api/rubric-movements/apply")
def apply(data: Confirm, user=Depends(current), db: Session = Depends(db_session)):
    from .classification import ApplyInput, ShareInput, apply_one
    authorize(user, db, roles=ROLES)
    target, items = prepared(db, user, data)
    if fingerprint(target, items) != data.fingerprint:
        fail("Os registos ou as rubricas mudaram desde a pré-visualização. Pré-visualize novamente.", 409)
    ready = [i for i in items if not i["blocked"]]
    if not ready:
        fail("Nenhum dos registos selecionados pode ser alterado aqui.")
    stamp = now()
    for item in ready:
        identifier = item["id"]
        if item["mode"] in ("erp", "purchase"):
            kind, target_id = ("erp", identifier[4:]) if item["mode"] == "erp" and identifier.startswith("erp:") else ("purchase", identifier)
            apply_one(db, user, ApplyInput(kind=kind, id=target_id, mode="percent", shares=[ShareInput(category_id=target.id, value=Decimal(100))]))
        else:
            source, _, source_id = identifier.partition(":")
            active = db.scalar(select(RubricReclassification).where(
                RubricReclassification.source == source, RubricReclassification.source_id == source_id,
                RubricReclassification.undone_at.is_(None)).with_for_update())
            if active:
                active.undone_at, active.undone_by = stamp, user.id
                db.flush()
            row = RubricReclassification(source=source, source_id=source_id, from_category_id=item["before"][0] if item["before"] else None,
                                         to_category_id=target.id, reason=data.reason, user_id=user.id)
            db.add(row)
            db.flush()
            record(db, user, "pagamento_rubrica_reclassificada", identifier, {"before_category_id": item["before"][0] if item["before"] else None,
                                                                              "after_category_id": target.id, "reason": data.reason,
                                                                              "reclassification_id": row.id, "origem": data.origin})
        record(db, user, "rubrica_movimento_alterada", identifier, {"mode": item["mode"], "before": item["before"], "after": target.id,
                                                                    "reason": data.reason, "origem": data.origin})
    db.commit()
    return {"applied": len(ready), "skipped": len(items) - len(ready)}


# -------------------------------------------------------------------------- histórico e reversão


@router.get("/api/rubric-movements/history")
def history(id: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    names = {c.id: c.name for c in db.scalars(select(Category))}
    users = {u.id: u.name for u in db.scalars(select(User))}
    target = id[4:] if id.startswith("erp:") else id
    entries = []
    for audit in db.scalars(select(Audit).where(Audit.target_id.in_((id, target)), Audit.action.in_((
            "classificacao_rubricas", "rubrica_movimento_alterada", "pagamento_rubrica_reclassificada", "pagamento_rubrica_reposta",
            "rubrica_movimento_revertida", "anotacao_movimento"))).order_by(Audit.created_at.desc()).limit(50)):
        detail = json.loads(audit.detail or "{}")
        entry = {"id": audit.id, "action": audit.action, "user": users.get(audit.user_id, ""), "date": audit.created_at.isoformat(),
                 "reason": detail.get("reason", ""), "origin": detail.get("origem", "")}
        if audit.action == "classificacao_rubricas":
            entry.update(before=[names.get(c, "Por classificar") for c, _ in (detail.get("antes") or [])] or ["Rubrica do fornecedor"],
                         after=[names.get(d["category_id"], "?") for d in detail.get("depois", [])], revertible=True)
        elif audit.action == "pagamento_rubrica_reclassificada":
            row = db.get(RubricReclassification, detail.get("reclassification_id") or "")
            entry.update(before=[names.get(detail.get("before_category_id"), "Por classificar")], after=[names.get(detail.get("after_category_id"), "?")],
                         revertible=bool(row and not row.undone_at))
        elif audit.action == "anotacao_movimento":
            entry.update(before=[detail.get("before", {}).get("notes", "")], after=[detail.get("after", {}).get("notes", "")], revertible=False)
        else:
            continue
        entries.append(entry)
    return {"entries": entries}


class Revert(Strict):
    id: str = Field(min_length=1, max_length=80)
    audit_id: str = Field(min_length=1, max_length=36)
    reason: Reason
    origin: Origin = "despesas_por_rubrica"


@router.post("/api/rubric-movements/revert")
def revert(data: Revert, user=Depends(current), db: Session = Depends(db_session)):
    from .classification import ApplyInput, ShareInput, apply_one, load
    from .models import ERPDocumentRubric, PurchaseRubric
    authorize(user, db, roles=ROLES)
    audit = db.get(Audit, data.audit_id)
    target_id = data.id[4:] if data.id.startswith("erp:") else data.id
    if not audit or audit.target_id not in (data.id, target_id):
        fail("Alteração não encontrada para este registo.", 404)
    detail = json.loads(audit.detail or "{}")
    if audit.action == "pagamento_rubrica_reclassificada":
        authorize(user, db, roles=["administrador"])
        row = db.scalar(select(RubricReclassification).where(RubricReclassification.id == detail.get("reclassification_id")).with_for_update())
        if not row or row.undone_at:
            fail("Esta reclassificação já foi anulada ou substituída.", 409)
        row.undone_at, row.undone_by = now(), user.id
        record(db, user, "pagamento_rubrica_reposta", data.id, {"reclassification_id": row.id, "reason": data.reason, "origem": data.origin})
    elif audit.action == "classificacao_rubricas":
        kind = "erp" if data.id.startswith("erp:") else "purchase"
        doc, total, target = load(db, user, kind, target_id, lock=True)
        before = detail.get("antes") or []
        if before:
            apply_one(db, user, ApplyInput(kind=kind, id=target_id, mode="amount",
                                           shares=[ShareInput(category_id=c, value=Decimal(a) / 100) for c, a in before]))
        else:
            model, column = (ERPDocumentRubric, ERPDocumentRubric.erp_document_id) if target[0] == "erp" else (PurchaseRubric, PurchaseRubric.purchase_id)
            from sqlalchemy import delete
            db.execute(delete(model).where(column == target[1]))
            record(db, user, "classificacao_rubricas", doc.id, {"documento": kind, "destino": target[0], "antes": [], "depois": [],
                                                                 "reversao": audit.id}, doc.store_id)
    else:
        fail("Esta alteração não pode ser revertida aqui.")
    record(db, user, "rubrica_movimento_revertida", data.id, {"audit_id": audit.id, "reason": data.reason, "origem": data.origin})
    db.commit()
    return {"reverted": audit.id}


# -------------------------------------------------------------------------- centro de custo e observações


class Annotation(Strict):
    id: str = Field(min_length=1, max_length=80)
    cost_center_id: str | None = Field(default=None, max_length=36)
    notes: str = Field(default="", max_length=2000)
    revision: int = Field(ge=0)


@router.put("/api/rubric-movements/annotation")
def annotate(data: Annotation, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    if data.cost_center_id:
        center = db.get(CostCenter, data.cost_center_id)
        if not center or not center.active:
            fail("Centro de custo inválido ou inativo.")
    mode_of(db, user, data.id)  # verifica o acesso ao registo
    row = db.scalar(select(RecordAnnotation).where(RecordAnnotation.record_id == data.id).with_for_update())
    if row and row.revision != data.revision or not row and data.revision:
        fail("A classificação deste registo mudou entretanto. Atualize a lista.", 409)
    before = {"cost_center_id": row.cost_center_id, "notes": row.notes} if row else {"cost_center_id": None, "notes": ""}
    if not row:
        row = RecordAnnotation(record_id=data.id)
        db.add(row)
    row.cost_center_id, row.notes, row.revision, row.updated_by = data.cost_center_id, data.notes.strip(), (row.revision or 0) + 1, user.id
    db.flush()
    record(db, user, "anotacao_movimento", data.id, {"before": before, "after": {"cost_center_id": row.cost_center_id, "notes": row.notes}})
    db.commit()
    return {"revision": row.revision}
