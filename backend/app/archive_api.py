"""Rotas do arquivo digital. Leitura por loja autorizada; escrita com perfis e auditoria."""
import io
import json
import re
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import access
from .archive import (
    FISCAL_TYPES,
    MAX_UPLOAD,
    ORIGINS,
    TARGETS,
    TYPES,
    TransientError,
    confirm,
    event,
    label,
    match_document,
    mime_of,
    process_jobs,
    queue,
    read_bytes,
    retention,
    settings,
    sync_sources,
    today,
    verify,
)
from .db import db_session
from .invoice_evidence import nif_key, ocr_command
from .models import (
    AccountingPeriod,
    ArchiveBlob,
    ArchiveDocument,
    ArchiveEvent,
    ArchiveJob,
    ArchiveLink,
    BankAccount,
    BankDeposit,
    BusinessEntity,
    BankTransaction,
    DocumentPayment,
    DriveDocumentAttachment,
    ERPDocument,
    Movement,
    Purchase,
    PurchaseAllocation,
    PurchaseAttachment,
    ProcurementDocument,
    Store,
    User,
    now,
    uid,
)
from .platform import cipher
from .schemas import Strict

router = APIRouter()
MANAGE = ["administrador", "gestor"]
WRITE = ["administrador", "gestor", "operador"]
PAGES = {"purchase": "Compras", "erp_document": "Conferência documental", "bank_transaction": "Extratos",
         "document_payment": "Movimentos previstos", "cash_movement": "Folha de caixa", "bank_deposit": "Depósitos"}


def visible(user, db):
    stores = access.scope(user, db)
    if user.role in MANAGE:
        return or_(ArchiveDocument.store_id.in_(stores), ArchiveDocument.store_id.is_(None))
    return ArchiveDocument.store_id.in_(stores)


def get_doc(db, user, identifier, lock=False):
    query = select(ArchiveDocument).where(ArchiveDocument.id == identifier, visible(user, db))
    doc = db.scalar(query.with_for_update() if lock else query)
    if not doc:
        raise HTTPException(404, "Documento não encontrado no arquivo desta empresa.")
    return doc


def euros(cents):
    return None if cents is None else cents


def doc_view(doc, stores=None, relation=None):
    extraction = json.loads(doc.extraction or "{}")
    return {
        "id": doc.id, "store_id": doc.store_id, "store": (stores or {}).get(doc.store_id, ""), "origin": doc.origin,
        "origin_label": ORIGINS.get(doc.origin, doc.origin), "doc_type": doc.doc_type, "type_label": TYPES.get(doc.doc_type, doc.doc_type),
        "original_name": doc.original_name, "mime": doc.mime, "size": doc.size, "storage": doc.storage,
        "sha256": doc.sha256, "duplicate_of": doc.duplicate_of, "issue_date": doc.issue_date, "received_at": doc.received_at,
        "archived_at": doc.archived_at, "entity_nif": doc.entity_nif, "entity_name": doc.entity_name,
        "document_code": doc.document_code, "document_number": doc.document_number, "currency": doc.currency,
        "base_cents": doc.base_cents, "tax_cents": doc.tax_cents, "total_cents": doc.total_cents,
        "payment_reference": doc.payment_reference, "description": doc.description,
        "processing_status": doc.processing_status, "review_status": doc.review_status,
        "relation": relation or "sem_relacao", "retention_until": doc.retention_until, "legal_hold": doc.legal_hold,
        "integrity_ok": doc.integrity_ok, "integrity_checked_at": doc.integrity_checked_at,
        "confidence": {k: v.get("confidence") for k, v in extraction.get("fields", {}).items()},
        "warnings": extraction.get("warnings", []),
    }


def relations(db, ids):
    result = {}
    if not ids:
        return result
    for document_id, status in db.execute(select(ArchiveLink.document_id, ArchiveLink.status).where(ArchiveLink.document_id.in_(ids))):
        current = result.get(document_id, "sem_relacao")
        rank = {"sem_relacao": 0, "proposta": 1, "associado": 2}
        value = "associado" if status == "confirmada" else "proposta" if status == "proposta" else "sem_relacao"
        if rank[value] > rank[current]:
            result[document_id] = value
    return result


def store_names(db):
    return {s.id: s.name for s in db.scalars(select(Store))}


def target_view(db, kind, identifier):
    stores = store_names(db)
    if kind == "procurement":
        row = db.get(ProcurementDocument, identifier)
        if not row:
            return None
        data = json.loads(row.data)
        entity = db.get(BusinessEntity, row.supplier_id)
        return {"label": data.get("document") or row.id, "entity": entity.name if entity else "", "nif": entity.tax_number if entity else "", "date": data.get("document_date"), "amount_cents": data.get("total_cents"), "store": stores.get(row.store_id, ""), "detail": "Encomenda / compras"}
    if kind == "purchase":
        row = db.get(Purchase, identifier)
        return row and {"label": label(row.document_type, row.document), "entity": row.supplier_name, "nif": row.supplier_nif,
                        "date": row.document_date, "amount_cents": row.amount_cents, "store": stores.get(row.store_id, ""),
                        "detail": "e-Fatura" if row.origin.startswith("efatura") or row.import_id else f"Compra ({row.origin})"}
    if kind == "erp_document":
        row = db.get(ERPDocument, identifier)
        return row and {"label": label(row.document_type, row.document), "entity": row.supplier_name, "nif": row.supplier_nif,
                        "date": row.document_date, "amount_cents": row.amount_cents, "store": stores.get(row.store_id, ""), "detail": "ERP"}
    if kind == "bank_transaction":
        row = db.get(BankTransaction, identifier)
        account = db.get(BankAccount, row.account_id) if row else None
        return row and {"label": row.description[:120], "entity": account.name if account else "", "nif": "", "date": row.movement_date,
                        "amount_cents": row.amount_cents, "store": stores.get(account.store_id, "") if account else "", "detail": "Extrato bancário"}
    if kind == "document_payment":
        row = db.get(DocumentPayment, identifier)
        return row and {"label": f"{row.kind.capitalize()} {row.reference}".strip(), "entity": row.entity, "nif": "", "date": row.payment_date,
                        "amount_cents": row.amount_cents * row.direction, "store": stores.get(row.store_id, ""), "detail": row.method}
    if kind == "cash_movement":
        row = db.get(Movement, identifier)
        return row and {"label": f"{row.kind} {row.document}".strip(), "entity": row.entity, "nif": "", "date": row.occurred_at.date().isoformat(),
                        "amount_cents": row.amount_cents * row.direction, "store": "", "detail": "Movimento de caixa"}
    if kind == "bank_deposit":
        row = db.get(BankDeposit, identifier)
        return row and {"label": f"Depósito {row.number}", "entity": "", "nif": "", "date": row.preparation_date,
                        "amount_cents": None, "store": stores.get(row.store_id, ""), "detail": row.status}
    return None


def link_view(db, link, doc=None, stores=None):
    target = target_view(db, link.target_kind, link.target_id)
    return {"id": link.id, "document_id": link.document_id, "target_kind": link.target_kind, "target_label": TARGETS.get(link.target_kind, link.target_kind),
            "target_id": link.target_id, "target": target, "page": PAGES.get(link.target_kind), "amount_cents": link.amount_cents,
            "score": link.score, "strength": link.strength, "status": link.status, "method": link.method,
            "reasons": json.loads(link.reasons or "[]"), "decided_at": link.decided_at,
            "document": doc_view(doc, stores) if doc else None}


def dossier_of(db, doc):
    if not doc.issue_date:
        return None
    period = db.scalar(select(AccountingPeriod).where(AccountingPeriod.start <= doc.issue_date, AccountingPeriod.end >= doc.issue_date).limit(1))
    if period:
        return {"label": f"Período {period.number} de {period.year} ({period.vat_period})", "closed": bool(period.closed_at),
                "reason": "Data de emissão dentro do período"}
    return {"label": f"Mês {doc.issue_date[5:7]}/{doc.issue_date[:4]}", "closed": False, "reason": "Data de emissão (período ainda não criado)"}


# --- Consulta -----------------------------------------------------------------------------------

@router.get("/api/archive/overview")
def overview(user=Depends(access.current), db: Session = Depends(db_session)):
    condition = visible(user, db)
    count = lambda *where: db.scalar(select(func.count()).select_from(ArchiveDocument).where(condition, *where))  # noqa: E731
    config = settings(db)
    db.commit()
    proposals = db.scalar(select(func.count()).select_from(ArchiveLink).join(ArchiveDocument, ArchiveDocument.id == ArchiveLink.document_id)
                          .where(condition, ArchiveLink.status == "proposta"))
    origins = dict(db.execute(select(ArchiveDocument.origin, func.count()).where(condition).group_by(ArchiveDocument.origin)).all())
    stores = store_names(db)
    latest = list(db.scalars(select(ArchiveDocument).where(condition).order_by(ArchiveDocument.received_at.desc()).limit(6)))
    return {
        "total": count(), "inbox": count(ArchiveDocument.review_status == "por_rever", ArchiveDocument.duplicate_of.is_(None)),
        "pending": count(ArchiveDocument.processing_status == "pendente"), "errors": count(ArchiveDocument.processing_status == "erro"),
        "duplicates": count(ArchiveDocument.duplicate_of.is_not(None)), "proposals": proposals,
        "retention_due": count(ArchiveDocument.retention_until < today().isoformat(), ArchiveDocument.legal_hold.is_(False)),
        "integrity_failed": count(ArchiveDocument.integrity_ok.is_(False)),
        "origins": [{"origin": k, "label": ORIGINS.get(k, k), "count": v} for k, v in sorted(origins.items(), key=lambda i: -i[1])],
        "queue": db.scalar(select(func.count()).select_from(ArchiveJob).where(ArchiveJob.status.in_(("pendente", "em_curso")))),
        "last_sync_at": config.last_sync_at, "ocr_available": bool(ocr_command()), "ocr_enabled": config.ocr_enabled,
        "auto_process": config.auto_process, "latest": [doc_view(d, stores) for d in latest],
        "can_manage": user.role in MANAGE, "can_upload": user.role in WRITE, "is_admin": user.role == "administrador",
        "types": TYPES, "origin_labels": ORIGINS,
    }


@router.get("/api/archive/documents")
def documents(q: str = "", doc_type: str = "", origin: str = "", status: str = "", relation: str = "", store_id: str = "",
              start: str = "", end: str = "", inbox: bool = False, limit: int = 50, offset: int = 0,
              user=Depends(access.current), db: Session = Depends(db_session)):
    query = select(ArchiveDocument).where(visible(user, db))
    if q.strip():
        term = "%" + q.strip()[:100] + "%"
        digits = re.sub(r"\D", "", q)
        query = query.where(or_(ArchiveDocument.original_name.ilike(term), ArchiveDocument.entity_name.ilike(term),
                                ArchiveDocument.document_number.ilike(term), ArchiveDocument.description.ilike(term),
                                ArchiveDocument.payment_reference.ilike(term),
                                *([ArchiveDocument.entity_nif.ilike("%" + digits + "%")] if len(digits) >= 3 else [])))
    if doc_type:
        query = query.where(ArchiveDocument.doc_type == doc_type)
    if origin:
        query = query.where(ArchiveDocument.origin == origin)
    if status == "duplicado":
        query = query.where(ArchiveDocument.duplicate_of.is_not(None))
    elif status in ("por_rever", "revisto"):
        query = query.where(ArchiveDocument.review_status == status, ArchiveDocument.duplicate_of.is_(None))
    elif status:
        query = query.where(ArchiveDocument.processing_status == status)
    if inbox:
        query = query.where(ArchiveDocument.duplicate_of.is_(None), or_(ArchiveDocument.review_status == "por_rever", ArchiveDocument.processing_status == "erro"))
    if store_id:
        query = query.where(ArchiveDocument.store_id == store_id)
    if start:
        query = query.where(func.coalesce(ArchiveDocument.issue_date, func.to_char(ArchiveDocument.received_at, "YYYY-MM-DD")) >= start[:10])
    if end:
        query = query.where(func.coalesce(ArchiveDocument.issue_date, func.to_char(ArchiveDocument.received_at, "YYYY-MM-DD")) <= end[:10])
    if relation in ("associado", "proposta", "sem_relacao"):
        confirmed = select(ArchiveLink.document_id).where(ArchiveLink.status == "confirmada")
        proposed = select(ArchiveLink.document_id).where(ArchiveLink.status == "proposta")
        query = query.where(ArchiveDocument.id.in_(confirmed)) if relation == "associado" else \
            query.where(ArchiveDocument.id.in_(proposed), ArchiveDocument.id.not_in(confirmed)) if relation == "proposta" else \
            query.where(ArchiveDocument.id.not_in(confirmed), ArchiveDocument.id.not_in(proposed))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(db.scalars(query.order_by(ArchiveDocument.received_at.desc(), ArchiveDocument.id).limit(max(1, min(limit, 200))).offset(max(0, offset))))
    links, stores = relations(db, [r.id for r in rows]), store_names(db)
    return {"total": total, "documents": [doc_view(r, stores, links.get(r.id)) for r in rows]}


@router.get("/api/archive/documents/{identifier}")
def detail(identifier: str, user=Depends(access.current), db: Session = Depends(db_session)):
    doc = get_doc(db, user, identifier)
    stores = store_names(db)
    names = {u.id: u.name for u in db.scalars(select(User))}
    extraction = json.loads(doc.extraction or "{}")
    links = list(db.scalars(select(ArchiveLink).where(ArchiveLink.document_id == doc.id).order_by(ArchiveLink.score.desc())))
    events = list(db.scalars(select(ArchiveEvent).where(ArchiveEvent.document_id == doc.id).order_by(ArchiveEvent.created_at.desc()).limit(60)))
    jobs = list(db.scalars(select(ArchiveJob).where(ArchiveJob.document_id == doc.id)))
    duplicates = list(db.scalars(select(ArchiveDocument).where(ArchiveDocument.duplicate_of == doc.id, visible(user, db))))
    return {
        **doc_view(doc, stores, relations(db, [doc.id]).get(doc.id)),
        "extraction": extraction, "corrections": json.loads(doc.corrections or "[]"),
        "links": [link_view(db, link) for link in links],
        "events": [{"action": e.action, "user": names.get(e.user_id, "Sistema"), "detail": json.loads(e.detail or "{}"), "created_at": e.created_at} for e in events],
        "jobs": [{"kind": j.kind, "status": j.status, "attempts": j.attempts, "error": j.error, "next_attempt_at": j.next_attempt_at} for j in jobs],
        "duplicates": [doc_view(d, stores) for d in duplicates], "dossier": dossier_of(db, doc),
        "file_url": f"/api/archive/documents/{doc.id}/file",
    }


@router.get("/api/archive/documents/{identifier}/file")
def original(identifier: str, download: bool = False, user=Depends(access.current), db: Session = Depends(db_session)):
    doc = get_doc(db, user, identifier)
    try:
        raw, mime = read_bytes(db, doc)
        verify(db, doc, raw)
    except TransientError as exc:
        raise HTTPException(503, str(exc)) from None
    except HTTPException as exc:
        if exc.status_code == 409:
            db.commit()  # regista a falha de integridade
        raise
    event(db, doc.id, user, "descarregado" if download else "visualizado", {})
    db.commit()
    safe = mime if mime in ("application/pdf", "image/png", "image/jpeg", "image/webp") else "application/octet-stream"
    return Response(raw, media_type=safe, headers={
        "Content-Disposition": ("attachment" if download else "inline") + "; filename*=UTF-8''" + quote(doc.original_name or "documento"),
        "Content-Security-Policy": "sandbox", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


# --- Entrada ------------------------------------------------------------------------------------

def validate_upload(raw):
    if not raw or len(raw) > MAX_UPLOAD:
        raise HTTPException(422, f"Cada ficheiro pode ter até {MAX_UPLOAD // 1048576} MB.")
    mime = mime_of(raw)
    if mime == "application/pdf":
        from .document_scanning import make_pdf
        make_pdf([raw], [], {"max_pages": 200, "max_bytes": MAX_UPLOAD})  # rejeita PDF cifrado, com JavaScript ou ficheiros embutidos
        return mime
    if mime in ("image/png", "image/jpeg", "image/webp"):
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ("PNG", "JPEG", "WEBP") or image.width * image.height > 40_000_000:
                    raise ValueError()
                image.verify()
        except Exception:
            raise HTTPException(422, "Imagem inválida, danificada ou demasiado grande.") from None
        return mime
    raise HTTPException(422, "Formato não suportado. Carregue PDF, JPG, PNG ou WebP (o conteúdo é verificado, não a extensão).")


@router.post("/api/archive/upload")
async def upload(files: list[UploadFile] = File(...), store_id: str = Form(...), doc_type: str = Form(""), description: str = Form(""),
                 user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, store_id, WRITE)
    if not db.get(Store, store_id):
        raise HTTPException(422, "Escolha uma loja válida.")
    if doc_type and doc_type not in TYPES:
        raise HTTPException(422, "Tipo de documento inválido.")
    if len(files) > 20:
        raise HTTPException(422, "Carregue no máximo 20 ficheiros de cada vez.")
    results = []
    try:
        for file in files:
            name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (file.filename or "documento").replace("\\", "/").split("/")[-1]).strip(" .")[:200] or "documento"
            raw = await file.read(MAX_UPLOAD + 1)
            try:
                mime = await run_in_threadpool(validate_upload, raw)
            except HTTPException as exc:
                results.append({"name": name, "status": "rejeitado", "message": exc.detail})
                continue
            import hashlib
            digest = hashlib.sha256(raw).hexdigest()
            same = db.scalar(select(ArchiveDocument).where(ArchiveDocument.sha256 == digest).order_by(ArchiveDocument.created_at).limit(1))
            if same:
                mine = db.scalar(select(ArchiveDocument.id).where(ArchiveDocument.id == same.id, visible(user, db)))
                results.append({"name": name, "status": "duplicado", "id": mine,
                                "message": "Este ficheiro já existe no arquivo" + (f" («{same.original_name}»)." if mine else ".")})
                continue
            identifier = uid()
            doc = ArchiveDocument(id=identifier, source_key="upload:" + identifier, store_id=store_id, origin="upload",
                                  doc_type=doc_type or "por_classificar", original_name=name, mime=mime, size=len(raw),
                                  storage="base_dados", storage_ref="archive_blobs:" + identifier, sha256=digest,
                                  description=description.strip()[:500], created_by=user.id, received_at=now())
            db.add(doc)
            db.flush()
            if doc_type:
                doc.extraction = json.dumps({"fields": {"doc_type": {"value": doc_type, "confidence": 1.0, "source": "correcao"}}})
            doc.retention_until = retention(doc, settings(db).retention_years)
            db.add(ArchiveBlob(document_id=identifier, ciphertext=cipher().encrypt(raw)))
            queue(db, identifier, "extract")
            event(db, identifier, user, "carregado", {"nome": name, "tamanho": len(raw), "sha256": digest})
            access.record(db, user, "arquivo_documento_carregado", identifier, {"nome": name, "sha256": digest}, store_id)
            db.commit()
            results.append({"name": name, "status": "carregado", "id": identifier})
    finally:
        for file in files:
            await file.close()
    return {"results": results}


class SyncInput(Strict):
    drive: bool = True
    process: bool = True


@router.post("/api/archive/sync")
def sync(data: SyncInput, user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    result = sync_sources(db, user, data.drive)
    access.record(db, user, "arquivo_sincronizado", "arquivo", {"novos": result["created"], "origens": result["by_origin"]})
    db.commit()
    if data.process:
        result["processing"] = process_jobs(db, limit=25, deadline_seconds=40)
    return result


@router.post("/api/archive/process")
def process(user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=WRITE)
    return process_jobs(db, limit=25, deadline_seconds=40)


# --- Correções e revisão ------------------------------------------------------------------------

class Correction(Strict):
    doc_type: str | None = Field(default=None, max_length=40)
    issue_date: date | None = None
    entity_nif: str | None = Field(default=None, max_length=30)
    entity_name: str | None = Field(default=None, max_length=300)
    document_code: str | None = Field(default=None, max_length=10)
    document_number: str | None = Field(default=None, max_length=120)
    base_cents: int | None = Field(default=None, strict=True, ge=0, le=10**13)
    tax_cents: int | None = Field(default=None, strict=True, ge=0, le=10**13)
    total_cents: int | None = Field(default=None, strict=True, ge=0, le=10**13)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    payment_reference: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    store_id: str | None = Field(default=None, max_length=36)
    legal_hold: bool | None = None
    clear: list[str] = Field(default_factory=list, max_length=12)
    reviewed: bool = False


CORRECTABLE = ("doc_type", "issue_date", "entity_nif", "entity_name", "document_code", "document_number", "base_cents",
               "tax_cents", "total_cents", "currency", "payment_reference", "description")


@router.put("/api/archive/documents/{identifier}")
def correct(identifier: str, data: Correction, user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    doc = get_doc(db, user, identifier, True)
    values = data.model_dump(exclude_unset=True)
    if data.doc_type and data.doc_type not in TYPES:
        raise HTTPException(422, "Tipo de documento inválido.")
    if data.entity_nif is not None and data.entity_nif and not re.fullmatch(r"[A-Z]{0,2}\s?[0-9A-Z]{5,20}", data.entity_nif.strip().upper()):
        raise HTTPException(422, "NIF inválido.")
    if data.store_id:
        access.authorize(user, db, data.store_id, MANAGE)
    if data.legal_hold is not None and user.role != "administrador":
        raise HTTPException(403, "Só o administrador altera a retenção obrigatória.")
    extraction = json.loads(doc.extraction or "{}")
    fields = extraction.setdefault("fields", {})
    history = json.loads(doc.corrections or "[]")
    changes = []
    for key in CORRECTABLE:
        if key not in values and key not in data.clear:
            continue
        new = None if key in data.clear else values[key]
        if key == "issue_date" and new is not None:
            new = new.isoformat()
        if key == "entity_nif" and new:
            new = nif_key(new)
        if key in ("currency", "document_code") and new:
            new = new.upper()
        old = getattr(doc, key)
        if new == old or (new is None and key in ("entity_nif", "entity_name", "document_code", "document_number", "payment_reference", "description") and not old):
            continue
        if new is None and key in ("entity_nif", "entity_name", "document_code", "document_number", "payment_reference", "description"):
            new = ""
        if key == "doc_type" and new is None:
            new = "por_classificar"
        setattr(doc, key, new)
        if key != "description":
            fields[key] = {"value": new, "confidence": 1.0, "source": "correcao"}
        changes.append({"campo": key, "antes": old, "depois": new})
    if data.store_id and data.store_id != doc.store_id:
        changes.append({"campo": "store_id", "antes": doc.store_id, "depois": data.store_id})
        doc.store_id = data.store_id
    if data.legal_hold is not None and data.legal_hold != doc.legal_hold:
        changes.append({"campo": "legal_hold", "antes": doc.legal_hold, "depois": data.legal_hold})
        doc.legal_hold = data.legal_hold
    if changes:
        stamp = now().isoformat()
        history.extend({**c, "utilizador": user.name, "data": stamp} for c in changes)
        doc.corrections = json.dumps(history, ensure_ascii=False, default=str)
        doc.extraction = json.dumps(extraction, ensure_ascii=False, default=str)
        doc.retention_until = retention(doc, settings(db).retention_years)
        if doc.processing_status == "erro":
            doc.processing_status = "processado"  # dados completados manualmente
        event(db, doc.id, user, "corrigido", {"alteracoes": changes})
        access.record(db, user, "arquivo_documento_corrigido", doc.id, {"campos": [c["campo"] for c in changes]}, doc.store_id)
    if data.reviewed and doc.review_status != "revisto":
        doc.review_status = "revisto"
        event(db, doc.id, user, "revisto", {})
    doc.updated_at = now()
    if changes:
        match_document(db, doc, settings(db))
    db.commit()
    return detail(identifier, user, db)


class BatchInput(Strict):
    ids: list[str] = Field(min_length=1, max_length=200)
    action: str = Field(pattern="^(revisto|reprocessar|relacionar)$")
    confirm: bool = False


@router.post("/api/archive/documents/batch")
def batch(data: BatchInput, user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    docs = [get_doc(db, user, i, data.confirm) for i in dict.fromkeys(data.ids)]
    summary = [{"id": d.id, "name": d.original_name, "entity": d.entity_name, "number": d.document_number} for d in docs]
    if not data.confirm:
        return {"documents": summary, "confirmed": False}
    config = settings(db)
    for doc in docs:
        if data.action == "revisto":
            if doc.review_status != "revisto":
                doc.review_status = "revisto"
                event(db, doc.id, user, "revisto", {"lote": True})
        elif data.action == "reprocessar":
            doc.processing_status = "pendente"
            queue(db, doc.id, "extract", reset=True)
            event(db, doc.id, user, "reprocessamento_pedido", {"lote": True})
        else:
            match_document(db, doc, config)
    access.record(db, user, "arquivo_lote_" + data.action, "arquivo", {"documentos": len(docs)})
    db.commit()
    return {"documents": summary, "confirmed": True}


@router.post("/api/archive/documents/{identifier}/retry")
def retry(identifier: str, user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=WRITE)
    doc = get_doc(db, user, identifier, True)
    doc.processing_status = "pendente"
    queue(db, doc.id, "extract", reset=True)
    event(db, doc.id, user, "reprocessamento_pedido", {})
    db.commit()
    return process_jobs(db, limit=3, deadline_seconds=30)


# --- Associações --------------------------------------------------------------------------------

@router.get("/api/archive/links")
def links(status: str = "proposta", kind: str = "", strength: str = "", user=Depends(access.current), db: Session = Depends(db_session)):
    query = select(ArchiveLink, ArchiveDocument).join(ArchiveDocument, ArchiveDocument.id == ArchiveLink.document_id).where(visible(user, db))
    if status:
        query = query.where(ArchiveLink.status == status)
    if kind:
        query = query.where(ArchiveLink.target_kind == kind)
    if strength:
        query = query.where(ArchiveLink.strength == strength)
    stores = store_names(db)
    rows = db.execute(query.order_by(ArchiveLink.score.desc(), ArchiveLink.created_at.desc()).limit(300)).all()
    return [link_view(db, link, doc, stores) for link, doc in rows]


class DecideInput(Strict):
    ids: list[str] = Field(min_length=1, max_length=200)
    decision: str = Field(pattern="^(confirmar|rejeitar|anular)$")
    confirm: bool = False
    reason: str = Field(default="", max_length=500)


@router.post("/api/archive/links/decide")
def decide(data: DecideInput, user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    items = []
    for identifier in dict.fromkeys(data.ids):
        link = db.scalar(select(ArchiveLink).where(ArchiveLink.id == identifier).with_for_update() if data.confirm else select(ArchiveLink).where(ArchiveLink.id == identifier))
        if not link:
            raise HTTPException(404, "Associação não encontrada.")
        doc = get_doc(db, user, link.document_id)
        expected = "confirmada" if data.decision == "anular" else "proposta"
        if link.status != expected:
            raise HTTPException(409, "Uma das associações já foi decidida. Atualize a lista.")
        items.append((link, doc))
    if data.decision == "anular" and len(data.reason.strip()) < 3:
        raise HTTPException(422, "Indique o motivo da anulação.")
    preview = [link_view(db, link, doc) for link, doc in items]
    if not data.confirm:
        warnings = [p["id"] for p in preview if p["strength"] == "ambigua"]
        return {"items": preview, "ambiguous": warnings, "confirmed": False}
    notes = []
    for link, doc in items:
        if data.decision == "confirmar":
            notes.append(confirm(db, link, user))
        else:
            link.status = "rejeitada" if data.decision == "rejeitar" else "anulada"
            link.decided_by, link.decided_at = user.id, now()
            event(db, doc.id, user, "associacao_" + link.status, {"alvo": link.target_kind, "id": link.target_id, "motivo": data.reason})
            access.record(db, user, "arquivo_associacao_" + link.status, doc.id, {"alvo": link.target_kind, "id": link.target_id, "motivo": data.reason}, doc.store_id)
            if data.decision == "anular":
                notes.append("Associação anulada. Anexos já criados na compra mantêm-se e removem-se na própria ficha, se necessário.")
    db.commit()
    return {"items": [link_view(db, link, doc) for link, doc in items], "notes": [n for n in notes if n], "confirmed": True}


class ManualLink(Strict):
    document_id: str = Field(max_length=36)
    target_kind: str = Field(pattern="^(purchase|erp_document|bank_transaction|document_payment)$")
    target_id: str = Field(max_length=36)
    amount_cents: int | None = Field(default=None, strict=True, gt=0, le=10**13)


@router.post("/api/archive/links/manual")
def manual(data: ManualLink, user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    doc = get_doc(db, user, data.document_id, True)
    target = target_view(db, data.target_kind, data.target_id)
    if not target:
        raise HTTPException(404, "Registo não encontrado.")
    link = db.scalar(select(ArchiveLink).where(ArchiveLink.document_id == doc.id, ArchiveLink.target_kind == data.target_kind, ArchiveLink.target_id == data.target_id))
    if link and link.status == "confirmada":
        raise HTTPException(409, "Esta associação já existe.")
    if not link:
        link = ArchiveLink(document_id=doc.id, target_kind=data.target_kind, target_id=data.target_id)
        db.add(link)
    link.score, link.strength, link.method, link.amount_cents = 100, "manual", "manual", data.amount_cents
    link.reasons = json.dumps(["Associação manual por " + user.name], ensure_ascii=False)
    db.flush()
    note = confirm(db, link, user)
    db.commit()
    return {**link_view(db, link), "note": note}


@router.get("/api/archive/targets")
def targets(kind: str, q: str = "", user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    term, stores = "%" + q.strip()[:80] + "%", access.scope(user, db)
    digits = re.sub(r"\D", "", q)
    if kind == "purchase":
        rows = db.scalars(select(Purchase).where(Purchase.store_id.in_(stores), or_(Purchase.supplier_name.ilike(term), Purchase.document.ilike(term), Purchase.supplier_nif.ilike(term)))
                          .order_by(Purchase.document_date.desc()).limit(30))
    elif kind == "erp_document":
        rows = db.scalars(select(ERPDocument).where(ERPDocument.store_id.in_(stores), or_(ERPDocument.supplier_name.ilike(term), ERPDocument.document.ilike(term)))
                          .order_by(ERPDocument.document_date.desc()).limit(30))
    elif kind == "bank_transaction":
        accounts = select(BankAccount.id).where(BankAccount.store_id.in_(stores))
        amount = int(digits) if digits and re.fullmatch(r"[\d.,\s]+", q.strip()) else None
        condition = BankTransaction.description.ilike(term) if amount is None else or_(BankTransaction.description.ilike(term), func.abs(BankTransaction.amount_cents) == amount)
        rows = db.scalars(select(BankTransaction).where(BankTransaction.account_id.in_(accounts), condition).order_by(BankTransaction.movement_date.desc()).limit(30))
    elif kind == "document_payment":
        rows = db.scalars(select(DocumentPayment).where(DocumentPayment.store_id.in_(stores), or_(DocumentPayment.entity.ilike(term), DocumentPayment.reference.ilike(term)))
                          .order_by(DocumentPayment.payment_date.desc()).limit(30))
    else:
        raise HTTPException(422, "Tipo de registo inválido.")
    return [{"id": r.id, **target_view(db, kind, r.id)} for r in rows]


# --- Pendências, dossiês, auditoria -------------------------------------------------------------

@router.get("/api/archive/pending")
def pending(user=Depends(access.current), db: Session = Depends(db_session)):
    condition, stores_scope, stores = visible(user, db), access.scope(user, db), store_names(db)
    since = (today() - timedelta(days=365)).isoformat()
    confirmed = select(ArchiveLink.target_id).where(ArchiveLink.status == "confirmada", ArchiveLink.target_kind == "purchase")
    with_attachment = select(PurchaseAttachment.purchase_id).union(select(DriveDocumentAttachment.purchase_id))
    purchases = list(db.scalars(select(Purchase).where(Purchase.store_id.in_(stores_scope), Purchase.document_date >= since,
                                                       Purchase.id.not_in(confirmed), Purchase.id.not_in(with_attachment))
                                .order_by(Purchase.document_date.desc()).limit(100)))
    accounts = select(BankAccount.id).where(BankAccount.store_id.in_(stores_scope))
    linked_tx = select(ArchiveLink.target_id).where(ArchiveLink.status == "confirmada", ArchiveLink.target_kind == "bank_transaction")
    allocated = select(PurchaseAllocation.transaction_id)
    payments = list(db.scalars(select(BankTransaction).where(BankTransaction.account_id.in_(accounts), BankTransaction.amount_cents < 0,
                                                             BankTransaction.movement_date >= (today() - timedelta(days=180)).isoformat(),
                                                             BankTransaction.id.not_in(linked_tx), BankTransaction.id.not_in(allocated))
                               .order_by(BankTransaction.movement_date.desc()).limit(100)))
    no_purchase = list(db.scalars(select(ArchiveDocument).where(condition, ArchiveDocument.doc_type.in_(FISCAL_TYPES), ArchiveDocument.duplicate_of.is_(None),
                                                                 ArchiveDocument.id.not_in(select(ArchiveLink.document_id).where(ArchiveLink.status == "confirmada",
                                                                                                                                ArchiveLink.target_kind.in_(("purchase", "erp_document")))))
                                  .order_by(ArchiveDocument.received_at.desc()).limit(100)))
    review = list(db.scalars(select(ArchiveDocument).where(condition, ArchiveDocument.duplicate_of.is_(None),
                                                            or_(ArchiveDocument.review_status == "por_rever", ArchiveDocument.processing_status == "erro"))
                             .order_by(ArchiveDocument.received_at.desc()).limit(100)))
    retention_due = list(db.scalars(select(ArchiveDocument).where(condition, ArchiveDocument.retention_until < today().isoformat(),
                                                                   ArchiveDocument.legal_hold.is_(False)).limit(100)))
    proposals = db.scalar(select(func.count()).select_from(ArchiveLink).join(ArchiveDocument, ArchiveDocument.id == ArchiveLink.document_id)
                          .where(condition, ArchiveLink.status == "proposta"))
    return {
        "proposals": proposals,
        "review": [doc_view(d, stores) for d in review],
        "purchases_without_document": [{"id": p.id, "label": label(p.document_type, p.document), "entity": p.supplier_name, "nif": p.supplier_nif,
                                        "date": p.document_date, "amount_cents": p.amount_cents, "store": stores.get(p.store_id, "")} for p in purchases],
        "payments_without_proof": [{"id": t.id, "label": t.description[:140], "date": t.movement_date, "amount_cents": t.amount_cents} for t in payments],
        "documents_without_purchase": [doc_view(d, stores) for d in no_purchase],
        "retention_due": [doc_view(d, stores) for d in retention_due],
    }


@router.get("/api/archive/periods")
def periods(user=Depends(access.current), db: Session = Depends(db_session)):
    condition, scope_ids = visible(user, db), access.scope(user, db)
    start = (today().replace(day=1) - timedelta(days=330)).replace(day=1).isoformat()
    months = {}
    for doc in db.scalars(select(ArchiveDocument).where(condition, ArchiveDocument.issue_date >= start, ArchiveDocument.duplicate_of.is_(None))):
        month = months.setdefault(doc.issue_date[:7], {"documents": 0, "fiscal": 0, "review": 0, "linked": 0, "purchases": 0, "purchases_missing": 0})
        month["documents"] += 1
        month["fiscal"] += doc.doc_type in FISCAL_TYPES
        month["review"] += doc.review_status == "por_rever"
    linked_docs = set(db.scalars(select(ArchiveLink.document_id).where(ArchiveLink.status == "confirmada")))
    for doc in db.scalars(select(ArchiveDocument).where(condition, ArchiveDocument.issue_date >= start, ArchiveDocument.id.in_(linked_docs))):
        months[doc.issue_date[:7]]["linked"] += 1
    with_document = set(db.scalars(select(PurchaseAttachment.purchase_id))) | set(db.scalars(select(DriveDocumentAttachment.purchase_id))) | set(
        db.scalars(select(ArchiveLink.target_id).where(ArchiveLink.status == "confirmada", ArchiveLink.target_kind == "purchase")))
    for purchase in db.scalars(select(Purchase).where(Purchase.store_id.in_(scope_ids), Purchase.document_date >= start)):
        month = months.setdefault(purchase.document_date[:7], {"documents": 0, "fiscal": 0, "review": 0, "linked": 0, "purchases": 0, "purchases_missing": 0})
        month["purchases"] += 1
        month["purchases_missing"] += purchase.id not in with_document
    closed = [(p.start, p.end, p.closed_at) for p in db.scalars(select(AccountingPeriod))]
    result = []
    for key in sorted(months, reverse=True):
        first = key + "-01"
        state = next((c for s, e, c in closed if s <= first <= e), None)
        result.append({"month": key, **months[key], "closed": bool(state)})
    return result


@router.get("/api/archive/audit")
def audit(document_id: str = "", action: str = "", user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    names = {u.id: u.name for u in db.scalars(select(User))}
    allowed = select(ArchiveDocument.id).where(visible(user, db))
    query = select(ArchiveEvent).where(or_(ArchiveEvent.document_id.in_(allowed), ArchiveEvent.document_id.is_(None)))
    if document_id:
        query = query.where(ArchiveEvent.document_id == document_id)
    if action:
        query = query.where(ArchiveEvent.action == action)
    docs = {d.id: d.original_name for d in db.scalars(select(ArchiveDocument).where(visible(user, db)))}
    return [{"id": e.id, "document_id": e.document_id, "document": docs.get(e.document_id, ""), "action": e.action,
             "user": names.get(e.user_id, "Sistema"), "detail": json.loads(e.detail or "{}"), "created_at": e.created_at}
            for e in db.scalars(query.order_by(ArchiveEvent.created_at.desc()).limit(300))]


# --- Configuração e integridade -----------------------------------------------------------------

class SettingsInput(Strict):
    retention_years: int = Field(ge=10, le=30)
    ocr_enabled: bool
    auto_process: bool
    include_drive: bool
    auto_confirm_strong: bool


def settings_view(row):
    return {"retention_years": row.retention_years, "ocr_enabled": row.ocr_enabled, "auto_process": row.auto_process,
            "include_drive": row.include_drive, "auto_confirm_strong": row.auto_confirm_strong, "last_sync_at": row.last_sync_at,
            "ocr_available": bool(ocr_command())}


@router.get("/api/archive/settings")
def read_settings(user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=MANAGE)
    row = settings(db)
    db.commit()
    return settings_view(row)


@router.put("/api/archive/settings")
def save_settings(data: SettingsInput, user=Depends(access.current), db: Session = Depends(db_session)):
    access.authorize(user, db, roles=["administrador"])
    row = settings(db)
    changed = row.retention_years != data.retention_years
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    if changed:
        for doc in db.scalars(select(ArchiveDocument)):
            doc.retention_until = retention(doc, data.retention_years)
    access.record(db, user, "arquivo_configurado", "default", data.model_dump())
    db.commit()
    return settings_view(row)


class IntegrityInput(Strict):
    limit: int = Field(default=50, ge=1, le=500)


@router.post("/api/archive/integrity")
def integrity(data: IntegrityInput, user=Depends(access.current), db: Session = Depends(db_session)):
    """Recalcula o SHA-256 dos originais (os mais antigos primeiro) e regista o resultado."""
    access.authorize(user, db, roles=["administrador"])
    ok, failed, unavailable = 0, [], []
    rows = list(db.scalars(select(ArchiveDocument).where(ArchiveDocument.sha256 != "")
                           .order_by(ArchiveDocument.integrity_checked_at.asc().nulls_first()).limit(data.limit)))
    for doc in rows:
        try:
            raw, _ = read_bytes(db, doc)
            verify(db, doc, raw)
            ok += 1
        except TransientError as exc:
            unavailable.append({"id": doc.id, "name": doc.original_name, "reason": str(exc)})
        except HTTPException as exc:
            failed.append({"id": doc.id, "name": doc.original_name, "reason": exc.detail})
            doc.integrity_ok, doc.integrity_checked_at = False, now()
        event(db, doc.id, user, "integridade_verificada", {"resultado": "indisponivel" if any(u["id"] == doc.id for u in unavailable) else "ok" if doc.integrity_ok else "falhou"})
    access.record(db, user, "arquivo_integridade", "arquivo", {"verificados": len(rows), "ok": ok, "falhas": len(failed)})
    db.commit()
    return {"checked": len(rows), "ok": ok, "failed": failed, "unavailable": unavailable}
