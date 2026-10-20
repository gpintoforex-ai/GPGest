"""Digitalização rápida por PIN, a partir do ecrã de acesso.

O dispositivo é emparelhado pelo administrador (código de uso único) e guarda apenas um token de
dispositivo. O PIN abre uma sessão curta que só permite pré-visualizar páginas, gerar o PDF e
guardá-lo na pasta cloud do acesso. Não lista nem descarrega ficheiros da cloud, não cria compras,
pagamentos nem stock. As credenciais cloud ficam sempre no servidor.
"""
import hashlib
import json
import re
import secrets
from collections import defaultdict, deque
from contextlib import closing
from datetime import timedelta
from time import monotonic
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pwdlib import PasswordHash
from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from . import access
from . import db as database
from .db import company_db, db_session
from .document_scanning import (
    MAX_IMAGE,
    MAX_INPUT,
    PageEdit,
    make_pdf,
    process_image,
    safe_name,
    scan_settings,
)
from .google_invoices import DRIVE, api_json, authorized_client
from .models import DocumentScan, GoogleConnection, QuickScanAccess, QuickScanSession, Store, now
from .platform import cipher
from .platform_models import Company
from .scan_storage import GoogleDriveStorage
from .schemas import Strict

router = APIRouter()
hasher = PasswordHash.recommended()
dummy_hash = hasher.hash(secrets.token_urlsafe(16))
LISBON = ZoneInfo("Europe/Lisbon")

TYPES = {
    "fatura": "Fatura de fornecedor",
    "despesa": "Despesa",
    "comprovativo": "Comprovativo de pagamento",
    "caixa": "Documento de caixa",
    "outro": "Outro",
}
IDLE = timedelta(minutes=10)
LIFETIME = timedelta(hours=1)
PAIRING = timedelta(minutes=30)
TEMPORARY = timedelta(hours=2)
FAILS_PER_LOCK = 5
LOCK_MINUTES = [1, 5, 15, 60, 240]
HOURLY_DOCUMENTS = 60
ORIGIN = "digitalizacao_rapida"
# Tentativas por endereço (PIN e emparelhamento), além do bloqueio progressivo por acesso.
ip_attempts = defaultdict(deque)

ENDED = "A sessão de digitalização terminou. Introduza novamente o PIN."
DEVICE = "Este dispositivo não está autorizado para digitalização rápida. Peça ao administrador um novo emparelhamento."


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def parse(token, kind, parts):
    """Token «kind.empresa.(acesso.)segredo»: a empresa é resolvida pelo identificador, nunca pelo PIN."""
    values = (token or "").strip().split(".")
    if len(values) != parts or values[0] != kind or not all(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", v) for v in values[1:]):
        return None
    return values


def credential(request: Request):
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get("x-quick-scan-device") or request.headers.get("x-quick-scan-pairing") or "").strip()


def open_company(company_id):
    with database.next_platform_session() as core:
        company = core.get(Company, company_id) if company_id else None
        if not company or not company.active:
            return None
        core.expunge(company)
        return company


def quick_db(request: Request):
    """Sessão de base de dados da empresa indicada no token (dispositivo, emparelhamento ou sessão)."""
    values = credential(request).split(".")
    company = open_company(values[1]) if len(values) >= 3 else None
    if not company:
        raise HTTPException(401, DEVICE)
    with company_db(company) as db:
        yield db


def throttle(request: Request):
    key = request.client.host if request.client else "local"
    times = ip_attempts[key]
    while times and times[0] < monotonic() - 900:
        times.popleft()
    if len(times) >= 30:
        raise HTTPException(429, "Demasiadas tentativas a partir deste dispositivo. Aguarde 15 minutos.")
    times.append(monotonic())


def usable(item):
    return bool(item and item.active and not item.revoked_at and item.device_hash)


def audit(db, item, action, detail, target=None):
    # A digitalização rápida não tem utilizador: a auditoria fica no responsável pelo acesso.
    access.record(db, SimpleNamespace(id=item.created_by), action, target or item.id,
        {"origem": ORIGIN, "acesso": item.name, "acesso_id": item.id, **detail}, item.store_id)


def lisbon_today():
    return now().astimezone(LISBON).date()


def pin_problem(pin):
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9]{6,12}", pin):
        return "O PIN tem de ter entre 6 e 12 algarismos."
    digits = [int(c) for c in pin]
    steps = {b - a for a, b in zip(digits, digits[1:])}
    if len(set(digits)) == 1 or steps in ({1}, {-1}):
        return "Escolha um PIN menos previsível (sem algarismos repetidos ou em sequência)."
    return ""


def device_view(item, db):
    store = db.get(Store, item.store_id)
    company = db.info.get("company", {})
    return {"name": item.name, "company": company.get("name", ""), "store": store.name if store else "",
            "folder": item.folder_name or "Pasta configurada"}


def session_for(request: Request, db: Session = Depends(quick_db)):
    """Valida a sessão em cada pedido (incluindo envios): validade, inatividade e estado do acesso."""
    values = parse(credential(request), "s1", 3)
    if not values:
        raise HTTPException(401, ENDED)
    row = db.scalar(select(QuickScanSession).where(QuickScanSession.token_hash == digest(values[2])).with_for_update())
    moment = now()
    if not row or row.expires_at <= moment or row.last_seen_at + IDLE <= moment:
        if row:
            db.delete(row)
            db.commit()
        raise HTTPException(401, ENDED)
    item = db.get(QuickScanAccess, row.access_id)
    if not usable(item):
        db.delete(row)
        db.commit()
        raise HTTPException(401, "Este acesso foi desativado ou revogado pelo administrador.")
    row.last_seen_at = moment
    db.commit()
    return row, item


def cleanup(db):
    db.execute(delete(QuickScanSession).where(
        (QuickScanSession.expires_at < now()) | (QuickScanSession.last_seen_at < now() - IDLE)))


# --- Dispositivo (sem sessão de utilizador) ---------------------------------------------------

class PinInput(Strict):
    pin: str = Field(max_length=64)


@router.post("/api/quick-scan/pair")
def pair(request: Request, db: Session = Depends(quick_db)):
    """O código (uso único, cabeçalho X-Quick-Scan-Pairing) troca-se por um token de dispositivo."""
    throttle(request)
    values = parse(request.headers.get("x-quick-scan-pairing", ""), "p1", 4)
    if not values:
        raise HTTPException(401, "Código de emparelhamento inválido ou expirado. Peça um novo ao administrador.")
    item = db.scalar(select(QuickScanAccess).where(QuickScanAccess.id == values[2]).with_for_update())
    valid = (item and item.active and not item.revoked_at and item.pairing_hash
             and secrets.compare_digest(item.pairing_hash, digest(values[3]))
             and item.pairing_expires_at and item.pairing_expires_at > now())
    if not valid:
        raise HTTPException(401, "Código de emparelhamento inválido ou expirado. Peça um novo ao administrador.")
    secret = secrets.token_urlsafe(40)
    item.device_hash, item.paired_at, item.pairing_hash, item.pairing_expires_at = digest(secret), now(), "", None
    item.failed_attempts, item.locked_until, item.updated_at = 0, None, now()
    db.execute(delete(QuickScanSession).where(QuickScanSession.access_id == item.id))
    audit(db, item, "digitalizacao_rapida_emparelhada", {})
    view = device_view(item, db)
    db.commit()
    return {"device_token": f"d1.{db.info.get('company', {}).get('id')}.{item.id}.{secret}", "device": view}


def device_access(request, db, lock=False):
    values = parse(request.headers.get("x-quick-scan-device", ""), "d1", 4)
    if not values:
        raise HTTPException(401, DEVICE)
    query = select(QuickScanAccess).where(QuickScanAccess.id == values[2])
    item = db.scalar(query.with_for_update() if lock else query)
    if not item or not item.device_hash or not secrets.compare_digest(item.device_hash, digest(values[3])):
        raise HTTPException(401, DEVICE)
    if not usable(item):
        raise HTTPException(401, "Este acesso foi desativado ou revogado pelo administrador.")
    return item


@router.get("/api/quick-scan/device")
def device(request: Request, db: Session = Depends(quick_db)):
    item = device_access(request, db)
    locked = item.locked_until and item.locked_until > now()
    return {**device_view(item, db), "locked_until": item.locked_until if locked else None}


@router.post("/api/quick-scan/session")
def start(data: PinInput, request: Request, db: Session = Depends(quick_db)):
    throttle(request)
    item = device_access(request, db, lock=True)
    moment = now()
    if item.locked_until and item.locked_until > moment:
        minutes = max(1, round((item.locked_until - moment).total_seconds() / 60))
        raise HTTPException(429, f"Acesso bloqueado temporariamente por tentativas falhadas. Tente dentro de {minutes} minuto(s) ou peça ao administrador para desbloquear.")
    if not hasher.verify(data.pin, item.pin_hash or dummy_hash):
        item.failed_attempts += 1
        if item.failed_attempts % FAILS_PER_LOCK == 0:
            step = min(item.failed_attempts // FAILS_PER_LOCK - 1, len(LOCK_MINUTES) - 1)
            item.locked_until = moment + timedelta(minutes=LOCK_MINUTES[step])
            audit(db, item, "digitalizacao_rapida_bloqueada", {"tentativas": item.failed_attempts, "minutos": LOCK_MINUTES[step]})
            db.commit()
            raise HTTPException(429, f"PIN incorreto. Acesso bloqueado durante {LOCK_MINUTES[step]} minuto(s).")
        db.commit()
        left = FAILS_PER_LOCK - item.failed_attempts % FAILS_PER_LOCK
        raise HTTPException(401, f"PIN incorreto. Restam {left} tentativa(s) antes de um bloqueio temporário.")
    item.failed_attempts, item.locked_until, item.last_used_at = 0, None, moment
    cleanup(db)
    secret = secrets.token_urlsafe(40)
    db.add(QuickScanSession(token_hash=digest(secret), access_id=item.id, created_at=moment, last_seen_at=moment, expires_at=moment + LIFETIME))
    audit(db, item, "digitalizacao_rapida_sessao", {})
    db.commit()
    company = db.info.get("company", {})
    return {"token": f"s1.{company.get('id')}.{secret}", "idle_seconds": int(IDLE.total_seconds()),
            "expires_at": moment + LIFETIME}


@router.post("/api/quick-scan/session/end")
def end(request: Request, db: Session = Depends(quick_db)):
    values = parse(credential(request), "s1", 3)
    if values:
        db.execute(delete(QuickScanSession).where(QuickScanSession.token_hash == digest(values[2])))
        db.commit()
    return {"ok": True}


# --- Sessão de digitalização -------------------------------------------------------------------

@router.get("/api/quick-scan/context")
def context(current=Depends(session_for), db: Session = Depends(quick_db)):
    row, item = current
    limits = scan_settings(db)
    return {**device_view(item, db), "types": [{"id": k, "name": v} for k, v in TYPES.items()],
            "max_pages": limits["max_pages"], "max_bytes": limits["max_bytes"],
            "idle_seconds": int(IDLE.total_seconds()), "expires_at": row.expires_at, "today": lisbon_today().isoformat()}


@router.post("/api/quick-scan/preview")
async def preview(edit: str = Form(...), file: UploadFile = File(...), current=Depends(session_for)):
    try:
        options = PageEdit.model_validate_json(edit)
        raw = await file.read(MAX_IMAGE + 1)
        jpg, _, warnings = await run_in_threadpool(process_image, raw, options)
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Scan-Warnings": str(len(warnings))})
    except ValueError:
        raise HTTPException(422, "Cantos ou recorte inválidos. Reveja os limites da página.") from None
    finally:
        await file.close()


class QuickPrepare(Strict):
    request_key: str = Field(min_length=36, max_length=36)
    edits: list[PageEdit] = Field(min_length=1, max_length=40)


def quick_view(row):
    details = json.loads(row.data)
    return {"id": row.id, "status": row.status, "name": row.name, "size": row.size, "pages": row.pages,
            "document_type": details["quick"]["type"], "description": details["quick"]["description"],
            "folder": details["quick"]["folder_name"], "error": row.error, "warnings": details.get("warnings", []),
            "cloud_saved": row.status == "cloud_saved", "name_locked": bool(row.drive_id), "expires_at": row.expires_at}


@router.post("/api/quick-scan/prepare")
async def prepare(payload: str = Form(...), files: list[UploadFile] = File(...), current=Depends(session_for), db: Session = Depends(quick_db)):
    session, item = current
    try:
        try:
            data = QuickPrepare.model_validate_json(payload)
            UUID(data.request_key)
        except ValueError:
            raise HTTPException(422, "Opções das páginas inválidas. Reveja os cantos e volte a gerar.") from None
        previous = db.scalar(select(DocumentScan).where(DocumentScan.request_key == data.request_key))
        if previous:
            if previous.quick_access_id != item.id:
                raise HTTPException(409, "Referência de digitalização indisponível.")
            return quick_view(previous)
        recent = db.scalar(select(func.count()).select_from(DocumentScan).where(
            DocumentScan.quick_access_id == item.id, DocumentScan.created_at > now() - timedelta(hours=1)))
        if recent >= HOURLY_DOCUMENTS:
            raise HTTPException(429, "Limite de documentos por hora atingido neste acesso. Tente mais tarde.")
        limits = scan_settings(db)
        if len(files) != len(data.edits) or len(files) > limits["max_pages"]:
            raise HTTPException(422, f"Máximo de {limits['max_pages']} páginas por documento.")
        raws, total = [], 0
        for file in files:
            raw = await file.read(MAX_IMAGE + 1)
            total += len(raw)
            if total > MAX_INPUT:
                raise HTTPException(413, "As fotografias ultrapassam 50 MB.")
            if raw.startswith(b"%PDF-"):
                raise HTTPException(422, "Use fotografias (JPG, PNG ou WebP).")
            raws.append(raw)
        pdf, pages, warnings = await run_in_threadpool(make_pdf, raws, data.edits, limits)
        if len(pdf) > limits["max_bytes"]:
            raise HTTPException(422, f"O PDF tem {len(pdf) / 1048576:.1f} MB e o limite é {limits['max_bytes'] / 1048576:.0f} MB. Use Cinzentos ou Preto e branco, ou menos páginas.")
        today = lisbon_today().isoformat()
        details = {
            "metadata": {"store_id": item.store_id, "document_type": "DIG", "document_date": today, "document": "Digitalização rápida",
                         "entity": "", "nif": "", "notes": ""},
            "quick": {"type": "outro", "type_label": TYPES["outro"], "description": "", "access_id": item.id,
                      "access_name": item.name, "folder_name": item.folder_name, "session": session.token_hash[:12]},
            "pdf_md5": hashlib.md5(pdf).hexdigest(), "warnings": warnings, "organisation": "root",
        }
        row = DocumentScan(request_key=data.request_key, store_id=item.store_id, user_id=item.created_by, account="",
                           root_folder=item.folder_id, document_date=today, digest=hashlib.sha256(pdf).hexdigest(),
                           size=len(pdf), pages=pages, data=json.dumps(details), pdf_ciphertext=cipher().encrypt(pdf),
                           expires_at=now() + TEMPORARY, name="preparar.pdf", origin=ORIGIN, quick_access_id=item.id)
        db.add(row)
        db.flush()
        row.name = f"{today}_outro_{row.id[:8]}.pdf"
        audit(db, item, "digitalizacao_rapida_pdf_gerado", {"pages": pages, "size": len(pdf)}, row.id)
        db.commit()
        return quick_view(row)
    finally:
        for file in files:
            await file.close()


def own_scan(db, item, identifier, lock=False):
    query = select(DocumentScan).where(DocumentScan.id == identifier, DocumentScan.quick_access_id == item.id,
                                       DocumentScan.origin == ORIGIN)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        raise HTTPException(404, "Digitalização indisponível neste acesso.")
    return row


@router.get("/api/quick-scan/scans/{identifier}/pdf")
def pdf(identifier: str, current=Depends(session_for), db: Session = Depends(quick_db)):
    # Só o PDF temporário gerado por este acesso; nunca ficheiros da cloud.
    row = own_scan(db, current[1], identifier)
    if not row.pdf_ciphertext or row.expires_at <= now():
        raise HTTPException(410, "O PDF temporário já não está disponível.")
    raw = cipher().decrypt(row.pdf_ciphertext)
    return Response(raw, media_type="application/pdf", headers={
        "Content-Disposition": "inline; filename*=UTF-8''" + quote(row.name), "Cache-Control": "no-store", "Content-Security-Policy": "sandbox"})


class UploadInput(Strict):
    document_type: str = Field(max_length=20)
    name: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=500)


def file_name(value, today, kind, identifier):
    stem = re.sub(r"\.pdf$", "", value.strip(), flags=re.IGNORECASE)
    return safe_name(stem or f"{today}_{kind}_{identifier}", 120) + ".pdf"


def name_taken(client, db, row, folder, name):
    if db.scalar(select(DocumentScan.id).where(DocumentScan.drive_folder == folder, DocumentScan.name == name,
                                               DocumentScan.id != row.id, DocumentScan.status == "cloud_saved")):
        return True
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    found = api_json(client.get(DRIVE + "/files", params={
        "q": f"'{folder}' in parents and name='{escaped}' and trashed=false", "fields": "files(id)", "pageSize": 1,
        "supportsAllDrives": True, "includeItemsFromAllDrives": True}), "Verificar nome do ficheiro")
    return bool(found.get("files"))


@router.post("/api/quick-scan/scans/{identifier}/upload")
def upload(identifier: str, data: UploadInput, current=Depends(session_for), db: Session = Depends(quick_db)):
    _, item = current
    if data.document_type not in TYPES:
        raise HTTPException(422, "Escolha o tipo de documento.")
    row = own_scan(db, item, identifier, lock=True)
    if row.status == "cloud_saved":
        return quick_view(row)
    if row.status == "uploading" and (now() - row.updated_at).total_seconds() < 300:
        raise HTTPException(409, "O envio está em curso. Aguarde antes de repetir.")
    if not row.drive_id and (not row.pdf_ciphertext or row.expires_at <= now()):
        raise HTTPException(410, "O PDF temporário expirou. Gere novamente o documento a partir das páginas.")
    details = json.loads(row.data)
    if not row.drive_id:
        # Classificação e nome só mudam antes de existir identificador cloud (evita duplicados).
        today = details["metadata"]["document_date"]
        details["quick"].update(type=data.document_type, type_label=TYPES[data.document_type], description=data.description.strip())
        details["metadata"]["notes"] = data.description.strip()
        row.name = file_name(data.name, today, data.document_type, row.id[:8])
        row.data = json.dumps(details)
    row.status, row.error, row.updated_at = "uploading", "", now()
    db.commit()
    stage = "auth"
    try:
        client, config = authorized_client(db)
        with closing(client):
            storage = GoogleDriveStorage(client)
            stage = "folder"
            storage.check_folder(item.folder_id)
            if row.drive_folder and row.drive_folder != item.folder_id:
                raise HTTPException(409, "A pasta de destino deste acesso mudou. Gere novamente o documento.")
            stage = "upload"
            if not row.drive_id:
                if name_taken(client, db, row, item.folder_id, row.name):
                    row.name = row.name[:-4] + "_" + row.id[:8] + ".pdf"
                    if name_taken(client, db, row, item.folder_id, row.name):
                        row.name = row.name[:-4] + "_" + secrets.token_hex(3) + ".pdf"
                row.drive_folder = item.folder_id
            row.account, row.root_folder = config.google_email, item.folder_id
            db.commit()
            raw = cipher().decrypt(row.pdf_ciphertext) if row.pdf_ciphertext and row.expires_at > now() else None
            storage.save(db, row, db.info.get("company", {}).get("id", ""), raw)
        row.status, row.pdf_ciphertext, row.updated_at = "cloud_saved", None, now()
        audit(db, item, "digitalizacao_rapida_guardada", {
            "provider": row.provider, "cloud_id": row.drive_id, "pasta": item.folder_name or item.folder_id,
            "tipo": details["quick"]["type"], "nome": row.name, "data": details["metadata"]["document_date"],
            "validation": "por_validar"}, row.id)
        db.commit()
        return quick_view(row)
    except Exception as exc:
        db.rollback()
        message = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else ""
        if stage == "auth":
            message = "A ligação à cloud não está disponível (autorização expirada ou por configurar). Peça ao administrador para voltar a ligar a conta. O PDF fica guardado nesta sessão."
        elif stage == "folder":
            message = "A pasta de destino não está disponível ou não permite guardar ficheiros. Peça ao administrador para rever o acesso. O PDF fica guardado nesta sessão."
        elif isinstance(exc, httpx.HTTPError) or not message:
            message = "Não foi possível confirmar o envio (falha de rede ou do serviço). Pode repetir: o envio usa o mesmo identificador e não cria duplicados."
        row = own_scan(db, item, identifier, lock=True)
        row.status, row.error, row.updated_at = "upload_error", message, now()
        audit(db, item, "digitalizacao_rapida_erro_envio", {"cloud_id": row.drive_id, "fase": stage}, row.id)
        db.commit()
        status = 503 if stage in ("auth", "folder") else exc.status_code if isinstance(exc, HTTPException) else 502
        raise HTTPException(status, message) from None


# --- Administração (área autenticada) ----------------------------------------------------------

class AccessInput(Strict):
    name: str = Field(min_length=2, max_length=100)
    store_id: str = Field(max_length=36)
    folder_id: str = Field(max_length=200)
    active: bool = True
    pin: str = Field(default="", max_length=64)


def admin(user, db):
    access.authorize(user, db, roles=["administrador"])


def folder_options(db):
    from .invoice_workflow import configured_folders, settings_for
    folders = configured_folders(db, settings_for(db))
    db.commit()
    names = {}
    connection = db.get(GoogleConnection, "default")
    if folders and connection and connection.token_ciphertext:
        try:
            client, _ = authorized_client(db)
            with closing(client):
                for folder in folders:
                    try:
                        names[folder] = api_json(client.get(DRIVE + "/files/" + folder, params={
                            "fields": "id,name", "supportsAllDrives": True}), "Nome da pasta").get("name", "")
                    except HTTPException:
                        names[folder] = ""
        except (HTTPException, httpx.HTTPError):
            pass
    return [{"id": f, "name": names.get(f) or f"Pasta {f[:6]}…"} for f in folders], bool(connection and connection.token_ciphertext)


def admin_view(item, stores):
    moment = now()
    return {"id": item.id, "name": item.name, "store_id": item.store_id, "store": stores.get(item.store_id, ""),
            "folder_id": item.folder_id, "folder_name": item.folder_name, "active": item.active,
            "revoked": bool(item.revoked_at), "revoked_at": item.revoked_at, "paired": bool(item.device_hash),
            "paired_at": item.paired_at, "pairing_pending": bool(item.pairing_hash and item.pairing_expires_at and item.pairing_expires_at > moment),
            "locked_until": item.locked_until if item.locked_until and item.locked_until > moment else None,
            "failed_attempts": item.failed_attempts, "last_used_at": item.last_used_at, "created_at": item.created_at}


def new_pairing(db, item):
    secret = secrets.token_urlsafe(32)
    item.pairing_hash, item.pairing_expires_at = digest(secret), now() + PAIRING
    item.device_hash, item.paired_at = "", None
    db.execute(delete(QuickScanSession).where(QuickScanSession.access_id == item.id))
    return {"code": f"p1.{db.info.get('company', {}).get('id')}.{item.id}.{secret}", "expires_at": item.pairing_expires_at}


def validate_access(db, data, folders):
    if not db.get(Store, data.store_id):
        raise HTTPException(422, "Escolha uma loja válida.")
    folder = next((f for f in folders if f["id"] == data.folder_id), None)
    if not folder:
        raise HTTPException(422, "Escolha uma das pastas cloud configuradas em Conferência documental ou Anexos.")
    return folder


@router.get("/api/quick-scan/accesses")
def list_accesses(user=Depends(access.current), db: Session = Depends(db_session)):
    admin(user, db)
    folders, connected = folder_options(db)
    stores = {s.id: s.name for s in db.scalars(select(Store))}
    items = list(db.scalars(select(QuickScanAccess).order_by(QuickScanAccess.created_at)))
    names = {i.id: i.name for i in items}
    recent = []
    for row in db.scalars(select(DocumentScan).where(DocumentScan.origin == ORIGIN).order_by(DocumentScan.created_at.desc()).limit(30)):
        quick = json.loads(row.data)["quick"]
        recent.append({"id": row.id, "name": row.name, "status": row.status, "access": names.get(row.quick_access_id, quick["access_name"]),
                       "type": quick["type_label"], "store": stores.get(row.store_id, ""), "created_at": row.created_at,
                       "pages": row.pages, "size": row.size, "cloud_id": row.drive_id})
    return {"accesses": [admin_view(i, stores) for i in items], "stores": [{"id": k, "name": v} for k, v in stores.items()],
            "folders": folders, "cloud_connected": connected, "recent": recent, "types": TYPES,
            "idle_minutes": int(IDLE.total_seconds() // 60), "pairing_minutes": int(PAIRING.total_seconds() // 60)}


@router.post("/api/quick-scan/accesses")
def create_access(data: AccessInput, user=Depends(access.current), db: Session = Depends(db_session)):
    admin(user, db)
    problem = pin_problem(data.pin)
    if problem:
        raise HTTPException(422, problem)
    folder = validate_access(db, data, folder_options(db)[0])
    item = QuickScanAccess(name=data.name.strip(), store_id=data.store_id, folder_id=folder["id"], folder_name=folder["name"],
                           pin_hash=hasher.hash(data.pin), active=data.active, created_by=user.id)
    db.add(item)
    db.flush()
    pairing = new_pairing(db, item)
    access.record(db, user, "digitalizacao_rapida_acesso_criado", item.id, {"nome": item.name, "pasta": folder["name"]}, item.store_id)
    db.commit()
    stores = {s.id: s.name for s in db.scalars(select(Store))}
    return {"access": admin_view(item, stores), "pairing": pairing}


def editable(db, identifier):
    item = db.scalar(select(QuickScanAccess).where(QuickScanAccess.id == identifier).with_for_update())
    if not item:
        raise HTTPException(404, "Acesso não encontrado.")
    if item.revoked_at:
        raise HTTPException(409, "Acesso revogado: crie um novo acesso.")
    return item


@router.put("/api/quick-scan/accesses/{identifier}")
def edit_access(identifier: str, data: AccessInput, user=Depends(access.current), db: Session = Depends(db_session)):
    admin(user, db)
    item = editable(db, identifier)
    if data.pin:
        problem = pin_problem(data.pin)
        if problem:
            raise HTTPException(422, problem)
    folder = validate_access(db, data, folder_options(db)[0]) if data.folder_id != item.folder_id or data.store_id != item.store_id else {"id": item.folder_id, "name": item.folder_name}
    changes = [k for k, v in (("nome", data.name.strip() != item.name), ("loja", data.store_id != item.store_id),
                              ("pasta", data.folder_id != item.folder_id), ("estado", data.active != item.active), ("pin", bool(data.pin))) if v]
    item.name, item.store_id, item.folder_id, item.folder_name, item.active = data.name.strip(), data.store_id, folder["id"], folder["name"], data.active
    if data.pin:
        item.pin_hash, item.failed_attempts, item.locked_until = hasher.hash(data.pin), 0, None
    if changes:
        # Qualquer alteração termina as sessões abertas: a nova configuração aplica-se de imediato.
        db.execute(delete(QuickScanSession).where(QuickScanSession.access_id == item.id))
    item.updated_at = now()
    access.record(db, user, "digitalizacao_rapida_acesso_alterado", item.id, {"alteracoes": changes}, item.store_id)
    db.commit()
    return admin_view(item, {s.id: s.name for s in db.scalars(select(Store))})


@router.post("/api/quick-scan/accesses/{identifier}/pairing")
def pairing(identifier: str, user=Depends(access.current), db: Session = Depends(db_session)):
    admin(user, db)
    item = editable(db, identifier)
    result = new_pairing(db, item)
    access.record(db, user, "digitalizacao_rapida_emparelhamento_pedido", item.id, {}, item.store_id)
    db.commit()
    return result


@router.post("/api/quick-scan/accesses/{identifier}/unlock")
def unlock(identifier: str, user=Depends(access.current), db: Session = Depends(db_session)):
    admin(user, db)
    item = editable(db, identifier)
    item.failed_attempts, item.locked_until = 0, None
    access.record(db, user, "digitalizacao_rapida_desbloqueada", item.id, {}, item.store_id)
    db.commit()
    return {"ok": True}


@router.post("/api/quick-scan/accesses/{identifier}/revoke")
def revoke(identifier: str, user=Depends(access.current), db: Session = Depends(db_session)):
    admin(user, db)
    item = editable(db, identifier)
    item.revoked_at, item.active, item.device_hash, item.pairing_hash, item.pairing_expires_at = now(), False, "", "", None
    db.execute(delete(QuickScanSession).where(QuickScanSession.access_id == item.id))
    access.record(db, user, "digitalizacao_rapida_revogada", item.id, {}, item.store_id)
    db.commit()
    return {"ok": True}


class QrInput(Strict):
    text: str = Field(min_length=10, max_length=600)


@router.post("/api/quick-scan/qr")
def pairing_qr(data: QrInput, user=Depends(access.current), db: Session = Depends(db_session)):
    """QR do link de emparelhamento, gerado no servidor (zxing-cpp), para ler com a câmara do dispositivo."""
    admin(user, db)
    if "digitalizar=p1." not in data.text:
        raise HTTPException(422, "Link de emparelhamento inválido.")
    import zxingcpp
    svg = zxingcpp.create_barcode(data.text, zxingcpp.BarcodeFormat.QRCode).to_svg()
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
