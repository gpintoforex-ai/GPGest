"""Obrigações e Prazos: documentos do arquivo e associação a registos reais (fatura, ERP, banco, pagamento).

- Documentos: as relações do Arquivo digital (`archive_links`) com os destinos prazo, obrigação, contrato e viatura;
  associar e ver os originais reutiliza o Arquivo (o original fica onde está; pré-visualização na aplicação).
- Registos reais: propostas para um prazo (fornecedor/entidade, valor e data), que só ficam associadas por
  confirmação. Associar não conclui o prazo (distingue obrigação, despesa reconhecida e pagamento efetuado), mas
  faz sair a estimativa da previsão de liquidez. Anular exige motivo e fica no histórico.
"""

import json
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import authorize, current, record, scope
from .db import db_session
from .models import (
    ArchiveDocument,
    ArchiveLink,
    BusinessEntity,
    Contract,
    DocumentPayment,
    Obligation,
    ObligationLink,
    ObligationOccurrence,
    User,
    Vehicle,
    now,
)
from .obligations import ROLES, check_scope, fail
from .schemas import Strict

router = APIRouter()
KINDS = {"obligation_occurrence": ObligationOccurrence, "obligation": Obligation, "contract": Contract, "vehicle": Vehicle}
SOURCES = {"purchase": "Compra / fatura", "erp": "Documento do ERP", "bank": "Custo bancário", "payment": "Pagamento registado"}


def target_store(db, kind, identifier):
    row = db.get(KINDS[kind], identifier)
    if not row:
        fail("Registo inexistente.", 404)
    if kind == "obligation_occurrence":
        return db.get(Obligation, row.obligation_id).store_id
    return row.store_id


def marks(db, occurrence_ids):
    """{ocorrência: {"documents": n, "links": n}} para as listas (uma leitura por tipo)."""
    result = {i: {"documents": 0, "links": 0} for i in occurrence_ids}
    if not occurrence_ids:
        return result
    for target in db.scalars(select(ArchiveLink.target_id).where(ArchiveLink.target_kind == "obligation_occurrence", ArchiveLink.status == "confirmada",
                                                                 ArchiveLink.target_id.in_(occurrence_ids))):
        result[target]["documents"] += 1
    for occurrence in db.scalars(select(ObligationLink.occurrence_id).where(ObligationLink.occurrence_id.in_(occurrence_ids), ObligationLink.undone_at.is_(None))):
        result[occurrence]["links"] += 1
    return result


def linked_occurrences(db):
    """Ocorrências com registo real associado (para a previsão de liquidez não as contar como estimativa)."""
    return {row.occurrence_id: row for row in db.scalars(select(ObligationLink).where(ObligationLink.undone_at.is_(None)))}


# --- Documentos do arquivo -------------------------------------------------------------------------

@router.get("/api/obligations/documents")
def documents(kind: Literal["obligation_occurrence", "obligation", "contract", "vehicle"], id: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    check_scope(user, db, target_store(db, kind, id))
    rows = db.execute(select(ArchiveLink, ArchiveDocument).join(ArchiveDocument, ArchiveDocument.id == ArchiveLink.document_id).where(
        ArchiveLink.target_kind == kind, ArchiveLink.target_id == id, ArchiveLink.status.in_(("confirmada", "proposta"))).order_by(ArchiveLink.created_at.desc()))
    return [{"link_id": link.id, "status": link.status, "document_id": doc.id, "name": doc.original_name or "Documento", "mime": doc.mime,
             "doc_type": doc.doc_type, "issue_date": doc.issue_date, "total_cents": doc.total_cents, "file_url": f"/api/archive/documents/{doc.id}/file"}
            for link, doc in rows]


# --- Registos reais --------------------------------------------------------------------------------

class LinkInput(Strict):
    source_kind: Literal["purchase", "erp", "bank", "payment"]
    source_id: str = Field(min_length=1, max_length=36)
    reasons: list[str] = Field(default_factory=list, max_length=10)


class UndoInput(Strict):
    reason: str = Field(min_length=3, max_length=2000)


def load_occurrence(db, user, identifier, lock=False):
    occurrence = db.get(ObligationOccurrence, identifier, with_for_update=lock)
    if not occurrence:
        fail("Prazo inexistente.", 404)
    obligation = db.get(Obligation, occurrence.obligation_id)
    check_scope(user, db, obligation.store_id)
    return occurrence, obligation


def candidates(db, user, occurrence, obligation):
    """Registos reais compatíveis (até 45 dias antes ou depois), com pontuação e motivos."""
    from .budget_links import documents as describe
    from .budget_links import source_key
    from .liquidity_estimates import tokens
    from .management_maps import cost_documents
    stores = [obligation.store_id] if obligation.store_id else scope(user, db)
    due = date.fromisoformat(occurrence.due_date)
    start, end = due - timedelta(days=45), due + timedelta(days=45)
    names = tokens(obligation.entity_name or obligation.title)
    nif = None
    if obligation.entity_id:
        entity = db.get(BusinessEntity, obligation.entity_id)
        nif = entity.tax_number if entity else None
    expected = occurrence.expected_cents
    found = []
    keys = {}
    for stamp, identifier, shares in cost_documents(db, stores, start, end):
        kind, sid = source_key(identifier)
        keys[(kind, sid)] = stamp
    info = describe(db, {(k, s, None) for k, s in keys})
    for (kind, sid), stamp in keys.items():
        doc = info.get((kind, sid), {})
        found.append((kind, sid, stamp, doc.get("supplier_name", ""), doc.get("supplier_nif", ""), abs(doc.get("total_cents") or 0), doc.get("label", "")))
    for row in db.scalars(select(DocumentPayment).where(DocumentPayment.store_id.in_(stores), DocumentPayment.direction == -1,
                                                        DocumentPayment.payment_date >= start.isoformat(), DocumentPayment.payment_date <= end.isoformat())):
        found.append(("payment", row.id, row.payment_date, row.entity, "", abs(row.amount_cents), f"{row.kind} {row.reference}".strip()))
    result = []
    for kind, sid, stamp, name, doc_nif, total, label in found:
        reasons, points = [], 0
        if nif and doc_nif and nif == doc_nif:
            points += 45
            reasons.append("Mesmo NIF da entidade")
        elif names and names & tokens(name):
            points += 40
            reasons.append("Mesma entidade")
        if expected:
            gap = abs(total - expected) / expected
            if gap == 0:
                points, reasons = points + 30, reasons + ["Valor igual ao previsto"]
            elif gap <= 0.05:
                points, reasons = points + 25, reasons + ["Valor dentro de 5 % do previsto"]
            elif gap <= 0.2:
                points, reasons = points + 10, reasons + ["Valor dentro de 20 % do previsto"]
        days = abs((date.fromisoformat(stamp) - due).days)
        if days <= 15:
            points, reasons = points + 20, reasons + [f"{days} dia(s) da data-limite"]
        elif days <= 45:
            points, reasons = points + 10, reasons + [f"{days} dias da data-limite"]
        if points >= 50 and reasons and reasons[0].startswith("Mesm"):
            result.append({"source_kind": kind, "source_label": SOURCES[kind], "source_id": sid, "date": stamp, "entity": name, "label": label,
                           "amount_cents": total, "score": min(99, points), "reasons": reasons})
    return sorted(result, key=lambda c: -c["score"])[:6]


def link_view(db, row, users):
    from .budget_links import documents as describe
    doc = {}
    if row.source_kind != "payment":
        doc = describe(db, {(row.source_kind, row.source_id, None)}).get((row.source_kind, row.source_id), {})
    else:
        payment = db.get(DocumentPayment, row.source_id)
        doc = {"supplier_name": payment.entity, "label": f"{payment.kind} {payment.reference}".strip(), "total_cents": payment.amount_cents} if payment else {}
    return {"id": row.id, "source_kind": row.source_kind, "source_label": SOURCES[row.source_kind], "source_id": row.source_id,
            "entity": doc.get("supplier_name", ""), "label": doc.get("label", ""), "amount_cents": row.amount_cents, "reasons": json.loads(row.reasons or "[]"),
            "created_by": users.get(row.created_by), "created_at": row.created_at.isoformat(), "undone_at": row.undone_at.isoformat() if row.undone_at else None,
            "undo_reason": row.undo_reason}


@router.get("/api/obligations/occurrences/{identifier}/matches")
def matches(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    occurrence, obligation = load_occurrence(db, user, identifier)
    users = {u.id: u.name for u in db.scalars(select(User))}
    links = [link_view(db, row, users) for row in db.scalars(select(ObligationLink).where(ObligationLink.occurrence_id == occurrence.id).order_by(ObligationLink.created_at.desc()))]
    active = {(link["source_kind"], link["source_id"]) for link in links if not link["undone_at"]}
    others = {(r.source_kind, r.source_id) for r in db.scalars(select(ObligationLink).where(ObligationLink.undone_at.is_(None), ObligationLink.occurrence_id != occurrence.id))}
    proposals = [{**c, "used_elsewhere": (c["source_kind"], c["source_id"]) in others} for c in candidates(db, user, occurrence, obligation)
                 if (c["source_kind"], c["source_id"]) not in active]
    return {"occurrence_id": occurrence.id, "title": obligation.title, "due_date": occurrence.due_date, "expected_cents": occurrence.expected_cents,
            "status": occurrence.status, "links": links, "proposals": proposals,
            "notes": ["Associar não conclui o prazo: conclua-o com o valor efetivo quando estiver cumprido.",
                      "Com um registo real associado, a estimativa deste prazo sai da previsão de liquidez."]}


@router.post("/api/obligations/occurrences/{identifier}/links")
def link(identifier: str, data: LinkInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    occurrence, obligation = load_occurrence(db, user, identifier, lock=True)
    proposals = {(c["source_kind"], c["source_id"]): c for c in candidates(db, user, occurrence, obligation)}
    candidate = proposals.get((data.source_kind, data.source_id))
    if not candidate:
        fail("Registo não compatível com este prazo (entidade, valor ou data) ou fora do seu âmbito.", 422)
    if db.scalar(select(ObligationLink.id).where(ObligationLink.occurrence_id == occurrence.id, ObligationLink.source_kind == data.source_kind,
                                                 ObligationLink.source_id == data.source_id, ObligationLink.undone_at.is_(None))):
        fail("Este registo já está associado ao prazo.", 409)
    row = ObligationLink(occurrence_id=occurrence.id, source_kind=data.source_kind, source_id=data.source_id, amount_cents=candidate["amount_cents"],
                         reasons=json.dumps(candidate["reasons"], ensure_ascii=False), created_by=user.id)
    db.add(row)
    db.flush()
    record(db, user, "obrigacao_registo_associado", occurrence.id, {"origem": data.source_kind, "registo": data.source_id, "valor": candidate["amount_cents"]}, obligation.store_id)
    db.commit()
    return matches(identifier, user, db)


@router.post("/api/obligation-links/{identifier}/undo")
def undo(identifier: str, data: UndoInput, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=ROLES)
    row = db.get(ObligationLink, identifier, with_for_update=True)
    if not row or row.undone_at:
        fail("Associação inexistente ou já anulada.", 404)
    occurrence, obligation = load_occurrence(db, user, row.occurrence_id)
    row.undone_by, row.undone_at, row.undo_reason = user.id, now(), data.reason.strip()
    record(db, user, "obrigacao_registo_anulado", occurrence.id, {"origem": row.source_kind, "registo": row.source_id, "motivo": data.reason}, obligation.store_id)
    db.commit()
    return matches(occurrence.id, user, db)

