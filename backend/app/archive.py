"""Arquivo digital inteligente: índice central, processamento assíncrono e motor de relações.

Princípios:
- O original nunca é substituído nem apagado: o arquivo referencia o ficheiro onde já está (Drive,
  anexos de compras, movimentos, depósitos, Gmail) e só guarda bytes nos carregamentos diretos,
  cifrados em repouso.
- A extração reutiliza o motor local (QR fiscal, texto do PDF e OCR Tesseract). Só preenche valores
  identificados de forma única no documento; havendo vários candidatos, nada é inventado.
- As relações são propostas com pontuação e justificação; só o utilizador as confirma (salvo a opção
  explícita de confirmar automaticamente identidades fortes por QR).
"""
import hashlib
import json
import re
import unicodedata
from contextlib import closing
from datetime import date, datetime, timedelta
from itertools import combinations
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .access import record
from .google_invoices import DRIVE, api_json, authorized_client
from .invoice_evidence import invoice_key, nif_key
from .models import (
    ArchiveBlob,
    ArchiveDocument,
    ArchiveEvent,
    ArchiveJob,
    ArchiveLink,
    ArchiveSettings,
    Attachment,
    BankAccount,
    BankDeposit,
    BankTransaction,
    BusinessEntity,
    CashSession,
    DepositAttachment,
    DocumentPayment,
    DocumentScan,
    DocumentState,
    DriveDocumentAttachment,
    ERPDocument,
    GoogleConnection,
    GoogleInvoiceFile,
    InvoiceEvidenceCache,
    Movement,
    PaymentDocumentLine,
    Purchase,
    PurchaseAttachment,
    Register,
    now,
)
from .platform import cipher

LISBON = ZoneInfo("Europe/Lisbon")
MAX_UPLOAD = 10 * 1024 * 1024
MAX_ATTEMPTS = 5
RETRY_MINUTES = [1, 5, 15, 60, 240]
TYPES = {
    "fatura": "Fatura",
    "fatura_recibo": "Fatura-recibo",
    "fatura_simplificada": "Fatura simplificada",
    "nota_credito": "Nota de crédito",
    "nota_debito": "Nota de débito",
    "recibo": "Recibo",
    "comprovativo_pagamento": "Comprovativo de pagamento",
    "extrato_bancario": "Extrato bancário",
    "despesa": "Despesa",
    "documento_caixa": "Documento de caixa",
    "contrato": "Contrato",
    "outro": "Outro",
    "por_classificar": "Por classificar",
}
FISCAL_TYPES = {"fatura", "fatura_recibo", "fatura_simplificada", "nota_credito", "nota_debito"}
CODE_TYPES = {"FT": "fatura", "FR": "fatura_recibo", "FS": "fatura_simplificada", "NC": "nota_credito",
              "ND": "nota_debito", "RG": "recibo", "RC": "recibo"}
QUICK_TYPES = {"fatura": "fatura", "despesa": "despesa", "comprovativo": "comprovativo_pagamento",
               "caixa": "documento_caixa", "outro": "outro"}
ORIGINS = {
    "upload": "Carregamento",
    "digitalizacao": "Digitalização",
    "digitalizacao_rapida": "Digitalização rápida",
    "google_drive": "Google Drive",
    "gmail": "Gmail",
    "anexo_compra": "Anexo de compra",
    "anexo_movimento": "Anexo de movimento de caixa",
    "anexo_deposito": "Anexo de depósito",
}
TARGETS = {
    "procurement": "Encomenda ao fornecedor",
    "purchase": "Compra / e-Fatura",
    "erp_document": "Compra do ERP",
    "bank_transaction": "Movimento bancário",
    "document_payment": "Pagamento / recebimento",
    "cash_movement": "Movimento de caixa",
    "bank_deposit": "Depósito",
}
FIELDS = ("doc_type", "issue_date", "entity_nif", "entity_name", "document_code", "document_number",
          "base_cents", "tax_cents", "total_cents", "currency", "payment_reference")
DRIVE_MIMES = ("application/pdf", "image/jpeg", "image/png")


class TransientError(Exception):
    """Falha recuperável (rede, serviço cloud, autorização): a fila volta a tentar."""


def today():
    return now().astimezone(LISBON).date()


def settings(db):
    row = db.get(ArchiveSettings, "default")
    if not row:
        row = ArchiveSettings(id="default", retention_years=10, ocr_enabled=True, auto_process=True,
                              include_drive=True, auto_confirm_strong=False)
        db.add(row)
        db.flush()
    return row


def event(db, document_id, user, action, detail=None):
    db.add(ArchiveEvent(document_id=document_id, user_id=getattr(user, "id", None), action=action,
                        detail=json.dumps(detail or {}, ensure_ascii=False, default=str)))


def retention(doc, years):
    """Conservação por defeito: até 31/12 do ano da emissão + N anos (10 por omissão)."""
    received = parse_time(doc.received_at) if isinstance(doc.received_at, str) else doc.received_at or now()
    base = int(doc.issue_date[:4]) if doc.issue_date else received.astimezone(LISBON).year
    return f"{base + years}-12-31"


def normalized(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", text).strip()


def number_key(value):
    """Número sem o prefixo do tipo («FT A/1» e «A/1» comparam iguais)."""
    number = invoice_key("", "", value or "")[2]
    return re.sub(r"^(FT|FR|FS|NC|ND|RG|RC)\s+", "", number).replace(" ", "")


def mime_of(raw):
    if raw.startswith(b"%PDF-"):
        return "application/pdf"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""


# --- Indexação das fontes existentes ------------------------------------------------------------

def upsert(db, key, user=None, **values):
    doc = db.scalar(select(ArchiveDocument).where(ArchiveDocument.source_key == key))
    if doc:
        # O mesmo ficheiro encontrado por outra fonte só completa o que falta (loja, hash).
        for field in ("store_id", "sha256", "storage_account", "storage_folder"):
            if not getattr(doc, field) and values.get(field):
                setattr(doc, field, values[field])
        return doc, False
    doc = ArchiveDocument(source_key=key, created_by=getattr(user, "id", None), **values)
    db.add(doc)
    db.flush()
    doc.retention_until = retention(doc, settings(db).retention_years)
    event(db, doc.id, user, "indexado", {"origem": doc.origin, "armazenamento": doc.storage})
    return doc, True


def queue(db, document_id, kind, reset=False):
    job = db.scalar(select(ArchiveJob).where(ArchiveJob.document_id == document_id, ArchiveJob.kind == kind))
    if not job:
        db.add(ArchiveJob(document_id=document_id, kind=kind, status="pendente", attempts=0, next_attempt_at=now()))
    elif reset or (kind == "match" and job.status == "concluido"):
        job.status, job.attempts, job.next_attempt_at, job.error, job.updated_at = "pendente", 0, now(), "", now()


def existing_link(db, doc, kind, target_id, reason="Associação já registada na aplicação"):
    link = db.scalar(select(ArchiveLink).where(ArchiveLink.document_id == doc.id, ArchiveLink.target_kind == kind,
                                               ArchiveLink.target_id == target_id))
    if not link:
        db.add(ArchiveLink(document_id=doc.id, target_kind=kind, target_id=target_id, score=100, strength="existente",
                           status="confirmada", method="associacao_existente", reasons=json.dumps([reason]),
                           decided_at=now()))


def declared(doc, values, source="registo", confidence=0.9):
    """Valores já registados na aplicação (por exemplo, digitalização associada a uma compra)."""
    extraction = json.loads(doc.extraction or "{}")
    fields = extraction.setdefault("fields", {})
    for key, value in values.items():
        if value in (None, "") or key in fields:
            continue
        fields[key] = {"value": value, "confidence": confidence, "source": source}
        setattr(doc, key, value)
    doc.extraction = json.dumps(extraction, ensure_ascii=False)


def movement_store(db, movement_id):
    move = db.get(Movement, movement_id)
    if not move:
        return None
    if move.session_id:
        session = db.get(CashSession, move.session_id)
        register = db.get(Register, session.register_id) if session else None
        return register.store_id if register else None
    account = db.get(BankAccount, move.bank_account_id) if move.bank_account_id else None
    return account.store_id if account else None


def sync_sources(db, user=None, drive=True):
    """Indexa, sem copiar nem alterar, os ficheiros já existentes na aplicação. Idempotente."""
    company = db.info.get("company", {}).get("id", "")
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(company + ":archive-sync"))))
    config = settings(db)
    known = set(db.scalars(select(ArchiveDocument.source_key)))
    created, warnings = {}, []

    def add(key, **values):
        doc, new = upsert(db, key, user, **values)
        if new:
            created[doc.origin] = created.get(doc.origin, 0) + 1
            known.add(key)
        return doc, new

    for scan in db.scalars(select(DocumentScan).where(DocumentScan.status == "cloud_saved", DocumentScan.drive_id != "")):
        details = json.loads(scan.data or "{}")
        meta, quick = details.get("metadata", {}), details.get("quick")
        doc, new = add("drive:" + scan.drive_id, store_id=scan.store_id, origin=scan.origin or "digitalizacao",
                       original_name=scan.name, mime=scan.mime, size=scan.size, storage="drive", storage_ref=scan.drive_id,
                       storage_account=scan.account, storage_folder=scan.drive_folder, sha256=scan.digest,
                       received_at=scan.created_at, description=((quick or {}).get("description") or meta.get("notes") or "")[:500],
                       doc_type=QUICK_TYPES.get((quick or {}).get("type"), "por_classificar") if quick else "por_classificar")
        if new:
            if not quick and meta.get("document") and meta.get("document_type") != "DIG":
                declared(doc, {"entity_nif": nif_key(meta.get("nif")), "entity_name": meta.get("entity", ""),
                               "document_code": meta.get("document_type", ""), "document_number": meta.get("document", ""),
                               "issue_date": meta.get("document_date"), "total_cents": abs(meta["amount_cents"]) if meta.get("amount_cents") is not None else None})
            queue(db, doc.id, "extract")
        if scan.purchase_id:
            existing_link(db, doc, "purchase", scan.purchase_id, "Digitalização associada à compra")
        if scan.movement_id:
            existing_link(db, doc, "cash_movement", scan.movement_id, "Digitalização associada ao movimento de caixa")
        if meta.get("deposit_id"):
            existing_link(db, doc, "bank_deposit", meta["deposit_id"], "Digitalização associada ao depósito")

    purchases = {}
    for link in db.scalars(select(DriveDocumentAttachment)):
        purchase = purchases.get(link.purchase_id) or db.get(Purchase, link.purchase_id)
        purchases[link.purchase_id] = purchase
        doc, new = add("drive:" + link.drive_id, store_id=purchase.store_id if purchase else None, origin="google_drive",
                       original_name=link.name, mime=link.mime, storage="drive", storage_ref=link.drive_id,
                       storage_account=link.account, storage_folder=link.drive_folder, sha256=link.digest if len(link.digest or "") == 64 else "",
                       received_at=link.created_at)
        if new:
            queue(db, doc.id, "extract")
        existing_link(db, doc, "purchase", link.purchase_id, "Anexo Drive da compra")

    for cache in db.scalars(select(InvoiceEvidenceCache).where(InvoiceEvidenceCache.mime.in_(DRIVE_MIMES))):
        if "drive:" + cache.drive_id in known:
            continue
        doc, new = add("drive:" + cache.drive_id, origin="google_drive", original_name=cache.name, mime=cache.mime,
                       storage="drive", storage_ref=cache.drive_id, storage_account=cache.account, storage_folder=cache.folder,
                       sha256=cache.digest if len(cache.digest or "") == 64 else "", received_at=cache.checked_at)
        evidence = json.loads(cache.evidence or "{}")
        if new and evidence and cache.digest:
            # A prova já foi extraída pela conferência: reutiliza-se, sem voltar a descarregar.
            apply_extraction(db, doc, evidence)
            doc.processing_status = "processado"
            queue(db, doc.id, "match")
        elif new:
            queue(db, doc.id, "extract")

    def blobs(model, prefix, origin, store, link):
        indexed = {k.split(":", 1)[1] for k in known if k.startswith(prefix + ":")}
        for identifier in db.scalars(select(model.id)):
            if identifier in indexed:
                continue
            row = db.get(model, identifier)
            raw = row.data or b""
            doc, new = add(f"{prefix}:{row.id}", store_id=store(row), origin=origin, original_name=row.name, mime=row.mime,
                           size=len(raw), storage="base_dados", storage_ref=f"{model.__tablename__}:{row.id}",
                           sha256=hashlib.sha256(raw).hexdigest(), received_at=getattr(row, "created_at", None) or now())
            if new:
                queue(db, doc.id, "extract")
            if link:
                kind, target = link(row)
                existing_link(db, doc, kind, target)

    blobs(PurchaseAttachment, "purchase_attachment", "anexo_compra",
          lambda r: getattr(db.get(Purchase, r.purchase_id), "store_id", None), lambda r: ("purchase", r.purchase_id))
    blobs(Attachment, "movement_attachment", "anexo_movimento",
          lambda r: movement_store(db, r.movement_id), lambda r: ("cash_movement", r.movement_id))
    blobs(DepositAttachment, "deposit_attachment", "anexo_deposito",
          lambda r: getattr(db.get(BankDeposit, r.deposit_id), "store_id", None), lambda r: ("bank_deposit", r.deposit_id))

    for mail in db.scalars(select(GoogleInvoiceFile)):
        key = "drive:" + mail.drive_id if mail.drive_id else "gmail:" + mail.id
        if key in known:
            continue
        values = dict(origin="gmail", original_name=mail.name, mime=mail.mime, size=mail.size, sha256=mail.digest or "",
                      received_at=parse_time(mail.received_at) if mail.received_at else mail.created_at,
                      description=f"Email de {mail.sender}: {mail.subject}"[:500])
        if mail.drive_id:
            values.update(storage="drive", storage_ref=mail.drive_id, storage_account=mail.account, storage_folder=mail.drive_folder or "")
        elif mail.data:
            values.update(storage="base_dados", storage_ref="google_invoice_files:" + mail.id)
        else:
            continue
        doc, new = add(key, **values)
        if new:
            queue(db, doc.id, "extract")

    if drive and config.include_drive:
        try:
            found = drive_inbox(db, known)
            for item in found:
                doc, new = add("drive:" + item["id"], origin="google_drive", original_name=item.get("name", "")[:300],
                               mime=item.get("mimeType", ""), size=int(item.get("size") or 0), storage="drive",
                               storage_ref=item["id"], storage_account=item["account"], storage_folder=item["folder"],
                               received_at=parse_time(item.get("createdTime")))
                if new:
                    queue(db, doc.id, "extract")
        except (HTTPException, httpx.HTTPError) as exc:
            warnings.append("Pastas Drive não consultadas: " + (exc.detail if isinstance(exc, HTTPException) else "falha de rede ou do serviço") + ".")
    config.last_sync_at = now()
    return {"created": sum(created.values()), "by_origin": created, "warnings": warnings}


def parse_time(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else now()
    except ValueError:
        return now()


def drive_inbox(db, known, limit=500):
    """Lista (só metadados) os PDF/JPG/PNG das pastas de anexos configuradas e respetivas subpastas."""
    from .drive_documents import folder_tree
    from .invoice_workflow import configured_folders, settings_for
    folders = configured_folders(db, settings_for(db))
    connection = db.get(GoogleConnection, "default")
    if not folders or not connection or not connection.token_ciphertext:
        return []
    with Session(bind=db.get_bind(), expire_on_commit=False) as auth_db:
        client, config = authorized_client(auth_db)
    found = []
    mimes = " or ".join(f"mimeType='{m}'" for m in DRIVE_MIMES)
    with closing(client):
        for root in folders:
            for folder in folder_tree(client, root):
                token = ""
                while len(found) < limit:
                    listing = api_json(client.get(DRIVE + "/files", params={
                        "q": f"'{folder}' in parents and trashed=false and ({mimes})", "pageSize": 200, "pageToken": token,
                        "fields": "files(id,name,mimeType,size,createdTime),nextPageToken",
                        "supportsAllDrives": True, "includeItemsFromAllDrives": True}), "Listar documentos Drive")
                    for item in listing.get("files", []):
                        if "drive:" + item["id"] not in known and item.get("mimeType") in DRIVE_MIMES:
                            found.append({**item, "folder": folder, "account": config.google_email})
                    token = listing.get("nextPageToken", "")
                    if not token:
                        break
    return found[:limit]


# --- Leitura do original e integridade ----------------------------------------------------------

TABLES = {"purchase_attachments": PurchaseAttachment, "attachments": Attachment,
          "deposit_attachments": DepositAttachment, "google_invoice_files": GoogleInvoiceFile}


def read_bytes(db, doc):
    if doc.storage == "base_dados":
        table, identifier = doc.storage_ref.split(":", 1)
        if table == "archive_blobs":
            blob = db.get(ArchiveBlob, doc.id)
            if not blob:
                raise HTTPException(404, "O original deste documento não foi encontrado.")
            raw = cipher().decrypt(blob.ciphertext)
        else:
            row = db.get(TABLES[table], identifier)
            if not row or not row.data:
                raise HTTPException(404, "O anexo original já não existe na aplicação.")
            raw = row.data
        return raw, mime_of(raw) or doc.mime
    from .drive_documents import content, metadata
    try:
        with Session(bind=db.get_bind(), expire_on_commit=False) as auth_db:
            client, config = authorized_client(auth_db)
    except HTTPException as exc:
        raise TransientError("A ligação Google não está disponível: " + str(exc.detail)) from None
    if doc.storage_account and config.google_email.lower() != doc.storage_account.lower():
        client.close()
        raise TransientError("Ligue a conta Google original deste documento para o consultar.")
    try:
        with closing(client):
            metadata(client, doc.storage_ref)
            return content(client, doc.storage_ref)
    except httpx.HTTPError:
        raise TransientError("Não foi possível ler o ficheiro no Drive (rede ou serviço).") from None
    except HTTPException as exc:
        if exc.status_code in (401, 403, 429) or exc.status_code >= 500:
            raise TransientError(str(exc.detail)) from None
        raise


def verify(db, doc, raw):
    digest = hashlib.sha256(raw).hexdigest()
    doc.integrity_checked_at = now()
    doc.integrity_ok = not doc.sha256 or doc.sha256 == digest
    if not doc.integrity_ok:
        event(db, doc.id, None, "integridade_falhou", {"esperado": doc.sha256, "obtido": digest})
        raise HTTPException(409, "O ficheiro original foi alterado desde o arquivo (SHA-256 diferente). Reveja a origem.")
    doc.sha256 = digest
    return digest


# --- Extração -----------------------------------------------------------------------------------

KEYWORDS = [
    ("nota_credito", r"nota\s+de\s+cr[eé]dito"),
    ("fatura_recibo", r"fatura[\s-]*recibo"),
    ("extrato_bancario", r"extrato\s+(banc[aá]rio|de\s+conta|integrado)"),
    ("comprovativo_pagamento", r"comprovativo|transfer[eê]ncia\s+(efetuada|realizada|sepa)|pagamento\s+efetuado|opera[cç][aã]o\s+efetuada"),
    ("recibo", r"\brecibo\b"),
    ("fatura_simplificada", r"fatura\s+simplificada"),
    ("fatura", r"\bfatura\b|\bfactura\b|\binvoice\b"),
    ("contrato", r"\bcontrato\b"),
]


def interpret(evidence):
    """Converte a prova extraída em campos com confiança. Valores ambíguos ficam como candidatos."""
    out, candidates, warnings = {}, {}, list(evidence.get("warnings", []))
    documents = evidence.get("documents") or []
    identities = {(nif_key(d["nif"]), d["document_type"], d["document"]) for d in documents}
    if len(identities) == 1:
        item = documents[0]
        code = (item.get("document_type") or "").upper()
        out.update(entity_nif=(nif_key(item["nif"]), 0.99, "qr"), document_code=(code, 0.99, "qr"),
                   document_number=(item["document"], 0.99, "qr"), issue_date=(item["document_date"], 0.99, "qr"),
                   total_cents=(abs(item["amount_cents"]), 0.99, "qr"))
        if code in CODE_TYPES:
            out["doc_type"] = (CODE_TYPES[code], 0.99, "qr")
        breakdown = item.get("tax_breakdown") or []
        if breakdown:
            out["base_cents"] = (sum(b["base_cents"] for b in breakdown), 0.99, "qr")
            out["tax_cents"] = (sum(b["tax_cents"] for b in breakdown), 0.99, "qr")
        if item.get("status") not in (None, "", "N"):
            warnings.append(f"O QR indica o estado «{item['status']}» (documento anulado ou alterado). Confirme com o emissor.")
    elif len(identities) > 1:
        warnings.append("O ficheiro contém mais de um documento fiscal. Separe-os ou corrija os dados manualmente.")
        candidates["documents"] = [{"nif": d["nif"], "document": d["document"], "document_date": d["document_date"], "amount_cents": d["amount_cents"]} for d in documents]
    else:
        source = evidence.get("source", "text")
        confidence = 0.6 if source == "ocr" else 0.75

        def single(name, field, convert=lambda v: v):
            values = evidence.get(name) or []
            if len(values) == 1:
                out[field] = (convert(values[0]), confidence, source)
            elif len(values) > 1:
                candidates[field] = values
        single("nifs", "entity_nif", nif_key)
        single("dates", "issue_date")
        single("totals", "total_cents", abs)
        numbers = evidence.get("numbers") or []
        if len(numbers) == 1:
            out["document_code"] = (numbers[0]["kind"].upper(), confidence, source)
            out["document_number"] = (numbers[0]["number"], confidence, source)
            if numbers[0]["kind"].upper() in CODE_TYPES:
                out["doc_type"] = (CODE_TYPES[numbers[0]["kind"].upper()], confidence, source)
        elif len(numbers) > 1:
            candidates["document_number"] = numbers
        currencies = evidence.get("currencies") or []
        if len(currencies) == 1:
            out["currency"] = (currencies[0], confidence, source)
    text = normalized(evidence.get("text", ""))
    if text and "doc_type" not in out:
        for kind, pattern in KEYWORDS:
            if re.search(pattern, text):
                out["doc_type"] = (kind, 0.6, "texto")
                break
    reference = re.search(r"entidade\s*:?\s*(\d{5}).{0,60}?refer[eê]ncia\s*:?\s*(\d{3}\s?\d{3}\s?\d{3})", text)
    if reference:
        out["payment_reference"] = (f"MB {reference.group(1)} {reference.group(2).replace(' ', '')}", 0.7, "texto")
    if evidence.get("due_dates"):
        candidates["due_dates"] = evidence["due_dates"]
    if evidence.get("incomplete"):
        warnings.append("Extração parcial: o documento é longo ou o tempo de leitura esgotou. Reveja os dados.")
    return out, candidates, warnings, evidence.get("source", "")


def apply_extraction(db, doc, evidence):
    values, candidates, warnings, source = interpret(evidence)
    extraction = json.loads(doc.extraction or "{}")
    fields = extraction.setdefault("fields", {})
    for key, (value, confidence, origin) in values.items():
        current = fields.get(key)
        # Correções manuais e valores mais fiáveis nunca são substituídos.
        if current and (current.get("source") == "correcao" or current.get("confidence", 0) > confidence):
            continue
        fields[key] = {"value": value, "confidence": confidence, "source": origin}
        setattr(doc, key, value)
    if doc.entity_nif and fields.get("entity_name", {}).get("source") != "correcao":
        entity = next((e for e in db.scalars(select(BusinessEntity).where(BusinessEntity.tax_number.ilike("%" + doc.entity_nif[-9:])))
                       if nif_key(e.tax_number) == doc.entity_nif), None)
        if entity:
            doc.entity_id, doc.entity_name = entity.id, entity.name
            fields["entity_name"] = {"value": entity.name, "confidence": 0.95, "source": "ficha_entidade"}
        elif not doc.entity_name:
            supplier = db.scalar(select(Purchase.supplier_name).where(Purchase.supplier_nif.ilike("%" + doc.entity_nif[-9:])).limit(1))
            if supplier:
                doc.entity_name = supplier
                fields["entity_name"] = {"value": supplier, "confidence": 0.8, "source": "compras"}
    extraction.update(candidates=candidates, warnings=warnings, source=source or extraction.get("source", ""),
                      processed_at=now().isoformat())
    doc.extraction = json.dumps(extraction, ensure_ascii=False, default=str)
    doc.retention_until = retention(doc, settings(db).retention_years)
    doc.updated_at = now()


def extract_document(db, doc, config):
    from .invoice_evidence import extract
    raw, mime = read_bytes(db, doc)
    verify(db, doc, raw)
    doc.size, doc.mime = len(raw), mime or doc.mime
    original = db.scalar(select(ArchiveDocument).where(ArchiveDocument.sha256 == doc.sha256, ArchiveDocument.id != doc.id,
                                                      ArchiveDocument.duplicate_of.is_(None))
                         .order_by(ArchiveDocument.created_at, ArchiveDocument.id).limit(1))
    if original and (original.created_at, original.id) < (doc.created_at, doc.id):
        doc.duplicate_of, doc.processing_status = original.id, "duplicado"
        event(db, doc.id, None, "duplicado_detetado", {"original": original.id})
        return
    if mime not in DRIVE_MIMES:
        raise HTTPException(415, "Formato sem extração automática (use PDF, JPG ou PNG). Preencha os dados manualmente.")
    evidence = extract(raw, mime, use_ocr=config.ocr_enabled, keep_text=True)
    apply_extraction(db, doc, evidence)
    from .purchase_line_evidence import extract_lines
    extraction = json.loads(doc.extraction or "{}")
    extraction["purchase_lines"] = extract_lines(evidence.get("text", ""))
    doc.extraction = json.dumps(extraction, ensure_ascii=False)
    doc.processing_status = "processado"
    event(db, doc.id, None, "processado", {"fonte": evidence.get("source"), "avisos": len(evidence.get("warnings", []))})
    queue(db, doc.id, "match", reset=True)


# --- Motor de relações --------------------------------------------------------------------------

def label(kind, number):
    """«FT A/1» sem repetir o tipo quando o número já o inclui."""
    kind, number = (kind or "").strip(), (number or "").strip()
    return number if not kind or number.upper().startswith(kind.upper() + " ") else f"{kind} {number}"


def money(cents):
    return f"{cents / 100:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".") + " €"


def days_between(a, b):
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def score_invoice(doc, nif, kind, number, issued, amount):
    nif_equal = bool(doc.entity_nif) and nif_key(doc.entity_nif) == nif_key(nif)
    number_equal = bool(doc.document_number) and number_key(doc.document_number) == number_key(number)
    if doc.document_code and kind and number_equal and doc.document_code.upper() != (kind or "").upper():
        number_equal = False
    if not nif_equal and not number_equal:
        return 0, [], ""
    score, reasons = 0, []
    if nif_equal and number_equal:
        score, method = 60, "identidade"
        reasons.append("NIF e número do documento iguais")
    elif nif_equal:
        score, method = 30, "nif_valor_data"
        reasons.append("Mesmo NIF do emitente")
    else:
        score, method = 20, "numero_valor_data"
        reasons.append("Mesmo número de documento, com NIF diferente ou desconhecido")
    if doc.total_cents is not None and amount is not None and abs(abs(amount) - doc.total_cents) <= 2:
        score += 25
        reasons.append(f"Total igual ({money(doc.total_cents)})")
    elif doc.total_cents is not None and amount is not None:
        reasons.append(f"Total diferente ({money(doc.total_cents)} no documento, {money(abs(amount))} no registo)")
    gap = days_between(doc.issue_date, issued)
    if gap == 0:
        score += 15
        reasons.append("Mesma data de emissão")
    elif gap is not None and abs(gap) <= 7:
        score += 5
        reasons.append(f"Datas próximas ({abs(gap)} dia(s))")
    return score, reasons, method


def invoice_candidates(db, doc):
    if not (doc.entity_nif or doc.document_number):
        return []
    found = []
    nif = doc.entity_nif[-9:] if doc.entity_nif else None
    filters = [Purchase.supplier_nif.ilike("%" + nif)] if nif else []
    if doc.document_number:
        filters.append(Purchase.document.ilike("%" + doc.document_number[-8:]))
    for row in db.scalars(select(Purchase).where(or_(*filters)).limit(500)):
        score, reasons, method = score_invoice(doc, row.supplier_nif, row.document_type, row.document, row.document_date, row.amount_cents)
        if score >= 45:
            found.append(("purchase", row.id, score, reasons, None, method))
    filters = [ERPDocument.supplier_nif.ilike("%" + nif)] if nif else []
    if doc.document_number:
        filters.append(ERPDocument.document.ilike("%" + doc.document_number[-8:]))
    for row in db.scalars(select(ERPDocument).where(or_(*filters), ERPDocument.cancelled.is_(False)).limit(500)):
        score, reasons, method = score_invoice(doc, row.supplier_nif, row.document_type, row.document, row.document_date, row.amount_cents)
        if score >= 45:
            found.append(("erp_document", row.id, score, reasons, None, method))
    return found


def mentions(doc, text):
    """Indícios textuais da entidade/documento numa descrição bancária ou referência de pagamento."""
    plain, digits, reasons, score = normalized(text), re.sub(r"\D", "", text or ""), [], 0
    if doc.document_number and len(number_key(doc.document_number)) >= 3 and number_key(doc.document_number).lower() in plain.replace(" ", ""):
        score += 30
        reasons.append("A descrição contém o número do documento")
    if doc.payment_reference:
        reference = re.sub(r"\D", "", doc.payment_reference)[-9:]
        if len(reference) == 9 and reference in digits:
            score += 30
            reasons.append("A descrição contém a referência de pagamento")
    if doc.entity_nif and doc.entity_nif[-9:] in digits:
        score += 20
        reasons.append("A descrição contém o NIF do emitente")
    elif doc.entity_name:
        token = next((t for t in normalized(doc.entity_name).replace(",", " ").split() if len(t) >= 3 and t not in {"lda", "sa", "unipessoal", "comercio", "servicos"}), "")
        if token and re.search(r"\b" + re.escape(token) + r"\b", plain):
            score += 15
            reasons.append(f"A descrição menciona «{token.upper()}»")
    return score, reasons


def window(doc):
    received = parse_time(doc.received_at) if isinstance(doc.received_at, str) else doc.received_at or now()
    reference = doc.issue_date or received.astimezone(LISBON).date().isoformat()
    start = date.fromisoformat(reference)
    proof = doc.doc_type == "comprovativo_pagamento"
    return (start - timedelta(days=5 if proof else 10)).isoformat(), (start + timedelta(days=5 if proof else 120)).isoformat(), proof


def payment_candidates(db, doc, others):
    if not doc.total_cents:
        return []
    found, total = [], doc.total_cents
    start, end, proof = window(doc)
    for row in db.scalars(select(BankTransaction).where(BankTransaction.amount_cents < 0, BankTransaction.movement_date >= start,
                                                        BankTransaction.movement_date <= end).limit(2000)):
        amount = -row.amount_cents
        bonus, reasons = mentions(doc, row.description)
        gap = days_between(doc.issue_date, row.movement_date) if doc.issue_date else None
        if amount == total:
            score = 50 + bonus
            reasons = [f"Valor igual ao total ({money(total)})", *reasons]
            if proof and gap is not None and abs(gap) <= 3:
                score += 20
                reasons.append("Data do comprovativo coincide com o movimento")
            elif gap is not None and 0 <= gap <= 60:
                score += 5
                reasons.append(f"Movimento {gap} dia(s) após a emissão")
            if score >= 55:
                found.append(("bank_transaction", row.id, min(score, 99), reasons, None, "valor_data_descricao"))
        elif amount < total and bonus >= 15:
            found.append(("bank_transaction", row.id, min(40 + bonus, 85), [f"Pagamento parcial: {money(amount)} de {money(total)}", *reasons], amount, "pagamento_parcial"))
        elif amount > total and doc.entity_nif and others:
            group = grouped(doc, others, amount)
            if group:
                found.append(("bank_transaction", row.id, min(60 + bonus, 85),
                              [f"Pagamento agrupado: {len(group) + 1} documentos de {doc.entity_name or doc.entity_nif} somam {money(amount)}", *reasons],
                              total, "pagamento_agrupado"))
    for row in db.scalars(select(DocumentPayment).where(DocumentPayment.direction == -1, DocumentPayment.payment_date >= start,
                                                        DocumentPayment.payment_date <= end).limit(2000)):
        bonus, reasons = mentions(doc, f"{row.entity} {row.reference}")
        linked = db.scalar(select(func.count()).select_from(PaymentDocumentLine).join(ArchiveLink, (ArchiveLink.target_id == PaymentDocumentLine.purchase_id) & (ArchiveLink.target_kind == "purchase"))
                           .where(PaymentDocumentLine.payment_id == row.id, ArchiveLink.document_id == doc.id, ArchiveLink.status == "confirmada"))
        if linked:
            bonus += 40
            reasons.append("Pagamento alocado à compra já associada a este documento")
        if row.amount_cents == total and bonus:
            found.append(("document_payment", row.id, min(55 + bonus, 99), [f"Valor igual ao total ({money(total)})", *reasons], None, "pagamento_registado"))
        elif row.amount_cents < total and bonus >= 15:
            found.append(("document_payment", row.id, min(40 + bonus, 90), [f"Pagamento parcial: {money(row.amount_cents)} de {money(total)}", *reasons], row.amount_cents, "pagamento_parcial"))
    return found


def grouped(doc, others, amount):
    """Outros documentos do mesmo emitente cujo total, somado a este, perfaz o pagamento (até 4)."""
    pool = [o for o in others if o.id != doc.id and o.total_cents][:12]
    for size in range(1, 4):
        for combo in combinations(pool, size):
            if doc.total_cents + sum(o.total_cents for o in combo) == amount:
                return combo
    return None


def match_document(db, doc, config):
    if doc.duplicate_of or doc.processing_status == "duplicado":
        return []
    others = list(db.scalars(select(ArchiveDocument).where(ArchiveDocument.entity_nif == doc.entity_nif, ArchiveDocument.id != doc.id,
                                                           ArchiveDocument.duplicate_of.is_(None), ArchiveDocument.total_cents.is_not(None)))) if doc.entity_nif else []
    proposals = invoice_candidates(db, doc) + payment_candidates(db, doc, others)
    links = {(link.target_kind, link.target_id): link for link in db.scalars(select(ArchiveLink).where(ArchiveLink.document_id == doc.id))}
    # Ambiguidade só entre candidatos do mesmo género: total, parcial e agrupado podem coexistir.
    def family(kind, method):
        return (kind, method if kind in ("bank_transaction", "document_payment") else "")
    best = {}
    for kind, _, score, _, _, method in proposals:
        best.setdefault(family(kind, method), []).append(score)
    current = set()
    for kind, target, score, reasons, amount, method in proposals:
        scores = sorted(best[family(kind, method)], reverse=True)
        ambiguous = len(scores) > 1 and scores[1] >= scores[0] - 10 and score >= scores[0] - 10
        strength = "ambigua" if ambiguous else "forte" if score >= 90 else "possivel"
        if ambiguous:
            reasons = [*reasons, "Há outros registos com pontuação semelhante: requer validação"]
        current.add((kind, target))
        link = links.get((kind, target))
        if link and link.status != "proposta":
            continue  # confirmadas, rejeitadas ou anuladas nunca são reabertas automaticamente
        if not link:
            link = ArchiveLink(document_id=doc.id, target_kind=kind, target_id=target, status="proposta")
            db.add(link)
            event(db, doc.id, None, "associacao_proposta", {"alvo": kind, "id": target, "pontuacao": score})
        link.score, link.strength, link.method, link.amount_cents = score, strength, method, amount
        link.reasons = json.dumps(reasons, ensure_ascii=False)
        if (config.auto_confirm_strong and strength == "forte" and method == "identidade" and kind == "purchase"
                and json.loads(doc.extraction or "{}").get("source") == "qr"):
            db.flush()
            confirm(db, link, None, "Confirmação automática: identidade fiscal por QR (opção ativa nas configurações)")
    for key, link in links.items():
        if link.status == "proposta" and key not in current:
            db.delete(link)  # proposta obsoleta: os dados mudaram e deixou de ser candidata
    return proposals


def confirm(db, link, user, note=""):
    doc = db.get(ArchiveDocument, link.document_id)
    link.status, link.decided_by, link.decided_at = "confirmada", getattr(user, "id", None), now()
    result = ""
    if link.target_kind == "purchase":
        purchase = db.scalar(select(Purchase).where(Purchase.id == link.target_id).with_for_update())
        if not purchase:
            raise HTTPException(404, "A compra associada já não existe.")
        result = attach_to_purchase(db, doc, purchase, user)
        if not doc.store_id:
            doc.store_id = purchase.store_id
    elif link.target_kind == "bank_transaction" and not doc.store_id:
        account = db.get(BankAccount, db.get(BankTransaction, link.target_id).account_id)
        doc.store_id = account.store_id if account else None
    event(db, doc.id, user, "associacao_confirmada", {"alvo": link.target_kind, "id": link.target_id, "nota": note or result})
    if user:
        record(db, user, "arquivo_associacao_confirmada", doc.id, {"alvo": link.target_kind, "id": link.target_id, "valor": link.amount_cents}, doc.store_id)
    return result


def attach_to_purchase(db, doc, purchase, user):
    """Disponibiliza o documento na ficha da compra (conferência, dossiê), sem duplicar ficheiros Drive."""
    from .drive_documents import attach_reference
    state = db.get(DocumentState, purchase.id) or DocumentState(purchase_id=purchase.id)
    db.add(state)
    if doc.origin == "anexo_compra" and doc.storage_ref == f"purchase_attachments:{doc.source_key.split(':', 1)[1]}":
        attached = db.get(PurchaseAttachment, doc.source_key.split(":", 1)[1])
        if attached and attached.purchase_id == purchase.id:
            state.availability = "digital"
            return "Já é anexo desta compra."
    if doc.storage == "drive":
        if len(doc.sha256) != 64:
            return "Ficheiro Drive ainda sem verificação de integridade: processe-o antes de o anexar à compra."
        existing = db.scalar(select(DriveDocumentAttachment).where(DriveDocumentAttachment.drive_id == doc.storage_ref))
        if existing and existing.purchase_id != purchase.id:
            raise HTTPException(409, "Este ficheiro Drive já está anexado a outra compra. Anule essa associação primeiro.")
        if not existing:
            attach_reference(db, purchase, doc.storage_account, doc.storage_ref, doc.storage_folder, doc.original_name,
                             doc.mime, doc.sha256, user or SimpleNamespace(id=purchase_actor(db)), record)
        state.availability = "digital"
        return "Anexo Drive registado na compra."
    raw, mime = read_bytes(db, doc)
    for attachment in db.scalars(select(PurchaseAttachment).where(PurchaseAttachment.purchase_id == purchase.id)):
        if hashlib.sha256(attachment.data).hexdigest() == hashlib.sha256(raw).hexdigest():
            state.availability = "digital"
            return "O mesmo ficheiro já é anexo desta compra."
    count = db.scalar(select(func.count()).select_from(PurchaseAttachment).where(PurchaseAttachment.purchase_id == purchase.id))
    count += db.scalar(select(func.count()).select_from(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id == purchase.id))
    if len(raw) > 5 * 1024 * 1024 or count >= 5 or mime not in ("application/pdf", "image/png", "image/jpeg"):
        return "Associação registada; o ficheiro não foi copiado para a compra (limite de 5 anexos/5 MB ou formato)."
    db.add(PurchaseAttachment(purchase_id=purchase.id, name=doc.original_name[:200] or "documento", mime=mime, data=raw))
    state.availability = "digital"
    return "Cópia do original anexada à compra."


def purchase_actor(db):
    from .models import User
    return db.scalar(select(User.id).where(User.role == "administrador").order_by(User.id).limit(1))


# --- Fila assíncrona ----------------------------------------------------------------------------

def process_jobs(db, limit=10, deadline_seconds=50):
    """Processa trabalhos pendentes com bloqueio por linha (vários processos não repetem trabalho)."""
    config = settings(db)
    db.commit()
    stuck = now() - timedelta(minutes=10)
    for job in db.scalars(select(ArchiveJob).where(ArchiveJob.status == "em_curso", ArchiveJob.updated_at < stuck)):
        job.status, job.next_attempt_at = "pendente", now()  # recuperação após interrupção
    db.commit()
    started, done, failed = now(), 0, 0
    while done + failed < limit and (now() - started).total_seconds() < deadline_seconds:
        job = db.scalar(select(ArchiveJob).where(ArchiveJob.status == "pendente", ArchiveJob.next_attempt_at <= now())
                        .order_by(ArchiveJob.kind, ArchiveJob.next_attempt_at).limit(1).with_for_update(skip_locked=True))
        if not job:
            break
        job.status, job.attempts, job.updated_at = "em_curso", job.attempts + 1, now()
        db.commit()
        identifier = job.id
        try:
            doc = db.get(ArchiveDocument, job.document_id)
            if job.kind == "extract":
                extract_document(db, doc, config)
            elif job.kind == "order_cloud":
                from .order_cycle import transfer_original
                transfer_original(db, doc)
            else:
                match_document(db, doc, config)
            job.status, job.error, job.updated_at = "concluido", "", now()
            db.commit()
            done += 1
        except Exception as exc:
            db.rollback()
            job = db.get(ArchiveJob, identifier)
            doc = db.get(ArchiveDocument, job.document_id)
            transient = isinstance(exc, (TransientError, httpx.HTTPError))
            message = str(exc) if isinstance(exc, TransientError) else exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else (
                str(exc)[:300] if isinstance(exc, ValueError) else "Falha inesperada no processamento. O documento original não foi alterado.")
            if transient and job.attempts < MAX_ATTEMPTS:
                job.status = "pendente"
                job.next_attempt_at = now() + timedelta(minutes=RETRY_MINUTES[job.attempts - 1])
            else:
                job.status = "erro"
                if job.kind == "extract":
                    doc.processing_status = "erro"
            job.error, job.updated_at = message, now()
            event(db, doc.id, None, "erro_processamento", {"trabalho": job.kind, "tentativa": job.attempts, "erro": message, "repete": job.status == "pendente"})
            db.commit()
            failed += 1
    return {"processed": done, "failed": failed,
            "pending": db.scalar(select(func.count()).select_from(ArchiveJob).where(ArchiveJob.status.in_(("pendente", "em_curso"))))}


def scheduled_tick():
    """Chamado pelo agendador: processa a fila de cada empresa ativa com processamento automático."""
    from . import db as database
    from .db import company_db
    from .platform_models import Company
    with database.next_platform_session() as core:
        companies = list(core.scalars(select(Company).where(Company.active == 1)))
        for company in companies:
            core.expunge(company)
    for company in companies:
        try:
            with company_db(company) as db:
                row = db.get(ArchiveSettings, "default")
                if row and row.auto_process and db.scalar(select(ArchiveJob.id).where(ArchiveJob.status == "pendente", ArchiveJob.next_attempt_at <= now()).limit(1)):
                    process_jobs(db, limit=5, deadline_seconds=20)
        except Exception:
            continue
