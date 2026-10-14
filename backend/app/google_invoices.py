"""Read Gmail, copy explicitly selected files, confirm invoice evidence separately."""

import base64
import hashlib
import io
import json
import re
import secrets
import unicodedata
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import quote

import httpx
from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import db_session
from .google_integration import credentials
from .models import (
    AttachmentIntegrationConfig,
    DriveDocumentAttachment,
    GoogleConnection,
    GoogleInvoiceFile,
    Purchase,
    PurchaseAttachment,
)
from .platform import cipher
from .schemas import Strict

PREFIX = "/api/integrations/attachments/google"
MAX_BYTES = 5 * 1024 * 1024
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
DRIVE = "https://www.googleapis.com/drive/v3"


class Scan(Strict):
    start_date: date
    end_date: date
    page_token: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_period(self):
        if self.end_date < self.start_date or (self.end_date - self.start_date).days > 90:
            raise ValueError("Escolha um período até 90 dias.")
        return self


class ImportFiles(Strict):
    ids: list[str] = Field(min_length=1, max_length=30)


class Associate(Strict):
    purchase_id: str = Field(max_length=36)
    confirmed: bool


def config_for(db):
    config = db.get(AttachmentIntegrationConfig, "default")
    connection = db.scalar(
        select(GoogleConnection).where(GoogleConnection.id == "default").with_for_update()
    )
    if not config or not connection or not connection.token_ciphertext:
        raise HTTPException(409, "Ligue a conta Gmail e Drive primeiro.")
    if config.google_email.lower() != connection.email.lower():
        raise HTTPException(409, "A conta configurada mudou. Volte a autorizar a conta Google.")
    return config, connection


def api_json(response, context):
    if response.status_code == 400 and context == "Renovar autorização":
        raise HTTPException(
            409,
            "A autorização Google expirou ou foi revogada. Desligue e volte a ligar Gmail e Drive nas Configurações.",
        )
    if response.status_code == 404 and context == "Verificar pasta Drive":
        raise HTTPException(
            422,
            "A pasta não foi encontrada ou a autorização atual não permite aceder-lhe. Confirme o identificador e volte a ligar Gmail e Drive para autorizar a pasta existente.",
        )
    if response.status_code in {401, 403}:
        raise HTTPException(
            409,
            f"{context}: confirme as APIs e volte a autorizar Gmail e Drive com as permissões solicitadas.",
        )
    if response.status_code >= 400:
        raise HTTPException(
            502,
            f"{context}: o serviço Google recusou a operação ({response.status_code}). Tente novamente.",
        )
    try:
        return response.json()
    except ValueError:
        raise HTTPException(502, f"{context}: resposta Google inválida.") from None


def authorized_client(db):
    config, connection = config_for(db)
    try:
        tokens = json.loads(cipher().decrypt(connection.token_ciphertext.encode()))
    except (InvalidToken, ValueError):
        raise HTTPException(409, "Volte a ligar a conta Google.") from None
    creds = credentials()
    try:
        with httpx.Client(timeout=30) as client:
            refreshed = api_json(
                client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": creds["client_id"],
                        "client_secret": creds["client_secret"],
                        "refresh_token": tokens.get("refresh_token", ""),
                        "grant_type": "refresh_token",
                    },
                ),
                "Renovar autorização",
            )
        if not refreshed.get("access_token"):
            raise HTTPException(409, "Volte a ligar a conta Google.")
        tokens.update(refreshed)
        connection.token_ciphertext = cipher().encrypt(json.dumps(tokens).encode()).decode()
        db.commit()
    except httpx.HTTPError:
        raise HTTPException(502, "Não foi possível contactar a Google. Tente novamente.") from None
    return httpx.Client(
        timeout=45, headers={"Authorization": "Bearer " + tokens["access_token"]}
    ), config


def parts(payload):
    yield payload
    for child in payload.get("parts", []):
        yield from parts(child)


def filename(value):
    return (value.replace("\\", "/").split("/")[-1].replace("\x00", "") or "anexo")[:200]


def validate_bytes(raw):
    if not raw or len(raw) > MAX_BYTES:
        raise HTTPException(422, "Selecione um PDF, JPG ou PNG até 5 MB.")
    if raw.startswith(b"%PDF-"):
        return "application/pdf"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise HTTPException(422, "O conteúdo do ficheiro não é um PDF, JPG ou PNG válido.")


def pdf_text(raw, mime):
    if mime != "application/pdf":
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            return ""
        return "\n".join((page.extract_text() or "")[:10000] for page in reader.pages[:10])[:60000]
    except Exception:
        return ""


def download(client, item):
    msg = api_json(
        client.get(
            GMAIL + "/messages/" + quote(item.message_id, safe=""), params={"format": "full"}
        ),
        "Ler email",
    )
    part = next(
        (p for p in parts(msg.get("payload", {})) if p.get("partId", "root") == item.part_id), None
    )
    if not part or filename(part.get("filename", "")) != item.name:
        raise HTTPException(409, "O anexo de origem mudou. Consulte novamente os emails.")
    body = part.get("body", {})
    if int(body.get("size", 0)) > MAX_BYTES:
        raise HTTPException(422, "Anexo superior a 5 MB.")
    if body.get("attachmentId"):
        body = api_json(
            client.get(
                GMAIL
                + "/messages/"
                + quote(item.message_id, safe="")
                + "/attachments/"
                + quote(body["attachmentId"], safe="")
            ),
            "Ler anexo",
        )
    try:
        encoded = body.get("data", "")
        if len(encoded) > MAX_BYTES * 4 // 3 + 16:
            raise ValueError()
        return base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise HTTPException(422, "Anexo inválido ou demasiado grande.") from None


def upload(client, db, item, folder, company_id, raw):
    # A persisted pre-generated ID makes retries after an uncertain response safe.
    if not item.drive_id:
        ids = api_json(
            client.get(DRIVE + "/files/generateIds", params={"count": 1, "space": "drive"}),
            "Preparar ficheiro Drive",
        )["ids"]
        item.drive_id = ids[0]
        item.drive_folder = folder
        db.commit()
    if item.drive_folder != folder:
        raise HTTPException(
            409, "Este ficheiro já foi preparado para outra pasta Drive. Reveja a configuração."
        )
    existing = client.get(
        DRIVE + "/files/" + quote(item.drive_id, safe=""),
        params={"fields": "id,parents,trashed,appProperties", "supportsAllDrives": True},
    )
    if existing.status_code == 200:
        info = existing.json()
        if (
            info.get("trashed")
            or folder not in info.get("parents", [])
            or info.get("appProperties", {}).get("gpgest_hash") != item.digest
        ):
            raise HTTPException(
                409,
                "O ficheiro no Drive foi movido, removido ou não corresponde ao anexo. Reveja-o antes de repetir.",
            )
        return
    if existing.status_code != 404:
        api_json(existing, "Verificar ficheiro Drive")
    boundary = "gpgest_" + secrets.token_hex(24)
    metadata = json.dumps(
        {
            "id": item.drive_id,
            "name": item.name,
            "mimeType": item.mime,
            "parents": [folder],
            "appProperties": {"gpgest_company": company_id, "gpgest_hash": item.digest},
        }
    ).encode()
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        + metadata
        + f"\r\n--{boundary}\r\nContent-Type: {item.mime}\r\n\r\n".encode()
        + raw
        + f"\r\n--{boundary}--\r\n".encode()
    )
    response = client.post(
        "https://www.googleapis.com/upload/drive/v3/files",
        params={"uploadType": "multipart", "fields": "id", "supportsAllDrives": True},
        content=body,
        headers={"Content-Type": "multipart/related; boundary=" + boundary},
    )
    api_json(response, "Guardar anexo no Drive")


def view(item, db):
    attachment = db.get(PurchaseAttachment, item.attachment_id) if item.attachment_id else None
    remote = (
        db.scalar(
            select(DriveDocumentAttachment).where(
                DriveDocumentAttachment.account == item.account,
                DriveDocumentAttachment.drive_id == item.drive_id,
            )
        )
        if item.drive_id
        else None
    )
    return {
        "id": item.id,
        "name": item.name,
        "sender": item.sender,
        "subject": item.subject,
        "received_at": item.received_at,
        "size": item.size,
        "uploaded": item.uploaded,
        "drive_url": "https://drive.google.com/file/d/" + item.drive_id + "/view"
        if item.uploaded
        else "",
        "purchase_id": attachment.purchase_id
        if attachment
        else remote.purchase_id
        if remote
        else None,
        "has_content": bool(item.data or item.uploaded),
    }


def normalized(text):
    return " ".join(
        "".join(
            c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)
        ).split()
    )


def score(item, purchase):
    text = normalized(" ".join([item.name, item.subject, item.extracted_text]))
    reasons, confidence = [], 0
    ref = re.sub(r"[^a-z0-9]", "", normalized(purchase.document))
    ref_pattern = r"(?<![a-z0-9])" + r"[^a-z0-9]*".join(re.escape(c) for c in ref) + r"(?![a-z0-9])"
    if len(ref) >= 5 and re.search(ref_pattern, text):
        reasons.append("Número do documento encontrado")
        confidence += 40
    if purchase.supplier_nif and re.search(
        r"(?<!\d)" + re.escape(purchase.supplier_nif) + r"(?!\d)", text
    ):
        reasons.append("NIF do fornecedor encontrado")
        confidence += 30
    name = normalized(purchase.supplier_name)
    if len(name) >= 5 and name in text:
        reasons.append("Nome do fornecedor encontrado")
        confidence += 15
    total = f"{purchase.amount_cents / 100:.2f}"
    if re.search(r"(?<![\d.,])" + re.escape(total).replace(r"\.", "[.,]") + r"(?!\d)", text):
        reasons.append("Montante da fatura encontrado")
        confidence += 15
    if purchase.document_date and purchase.document_date in text:
        reasons.append("Data do documento encontrada")
        confidence += 10
    return min(confidence, 95), reasons


def register_routes(app, current, authorize, scope, record):
    def manager(user, db):
        authorize(user, db, roles=["administrador", "gestor"])

    def get_file(db, key):
        item = db.get(GoogleInvoiceFile, key)
        if not item:
            raise HTTPException(404, "Anexo não encontrado nesta empresa.")
        return item

    @app.get(PREFIX + "/files")
    def files(user=Depends(current), db: Session = Depends(db_session)):
        manager(user, db)
        config = db.get(AttachmentIntegrationConfig, "default")
        if not config:
            return []
        return [
            view(f, db)
            for f in db.scalars(
                select(GoogleInvoiceFile)
                .where(GoogleInvoiceFile.account == config.google_email.lower())
                .order_by(GoogleInvoiceFile.received_at.desc())
                .limit(500)
            )
        ]

    @app.post(PREFIX + "/scan")
    def scan(data: Scan, user=Depends(current), db: Session = Depends(db_session)):
        manager(user, db)
        client, config = authorized_client(db)
        # Epoch dates avoid Gmail's date-string timezone interpretation.
        start = int(
            datetime.combine(data.start_date, datetime.min.time(), timezone.utc).timestamp()
        )
        end = int(
            datetime.combine(
                data.end_date + timedelta(days=1), datetime.min.time(), timezone.utc
            ).timestamp()
        )
        query = f"has:attachment after:{start} before:{end}"
        params = {"q": query, "maxResults": 30}
        if data.page_token:
            params["pageToken"] = data.page_token
        found, skipped = [], 0
        try:
            with closing(client):
                folder = config.mail_folder.strip()
                if folder.lower() != "all":
                    labels = api_json(client.get(GMAIL + "/labels"), "Ler pastas Gmail").get(
                        "labels", []
                    )
                    label = next(
                        (
                            entry
                            for entry in labels
                            if entry["id"].lower() == folder.lower()
                            or entry["name"].lower() == folder.lower()
                        ),
                        None,
                    )
                    if not label:
                        raise HTTPException(
                            422,
                            "Pasta Gmail não encontrada. Use inbox, all ou o nome de uma etiqueta existente.",
                        )
                    params["labelIds"] = label["id"]
                listing = api_json(
                    client.get(GMAIL + "/messages", params=params), "Consultar Gmail"
                )
                for message in listing.get("messages", []):
                    msg = api_json(
                        client.get(
                            GMAIL + "/messages/" + quote(message["id"], safe=""),
                            params={"format": "full"},
                        ),
                        "Consultar mensagem",
                    )
                    payload = msg.get("payload", {})
                    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
                    for part in parts(payload):
                        name = part.get("filename", "")
                        disposition = next(
                            (
                                h["value"].lower()
                                for h in part.get("headers", [])
                                if h["name"].lower() == "content-disposition"
                            ),
                            "",
                        )
                        if not name or disposition.startswith("inline"):
                            continue
                        size = int(part.get("body", {}).get("size", 0))
                        if (
                            not name.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))
                            or size > MAX_BYTES
                        ):
                            skipped += 1
                            continue
                        part_id = part.get("partId", "root")
                        item = db.scalar(
                            select(GoogleInvoiceFile).where(
                                GoogleInvoiceFile.account == config.google_email.lower(),
                                GoogleInvoiceFile.message_id == message["id"],
                                GoogleInvoiceFile.part_id == part_id,
                            )
                        )
                        if not item:
                            item = GoogleInvoiceFile(
                                account=config.google_email.lower(),
                                message_id=message["id"],
                                part_id=part_id,
                                attachment_key=part.get("body", {}).get("attachmentId", ""),
                                name=filename(name),
                                sender=headers.get("from", "")[:500],
                                subject=headers.get("subject", "")[:500],
                                received_at=datetime.fromtimestamp(
                                    int(msg.get("internalDate", "0")) / 1000, timezone.utc
                                ).isoformat(),
                                size=size,
                                mime=part.get("mimeType", "")[:100],
                            )
                            db.add(item)
                            db.flush()
                        found.append(view(item, db))
                record(
                    db,
                    user,
                    "gmail_consultado",
                    "default",
                    {"start": str(data.start_date), "end": str(data.end_date), "found": len(found)},
                )
                db.commit()
                return {
                    "files": found,
                    "next_page": listing.get("nextPageToken", ""),
                    "skipped": skipped,
                }
        except httpx.HTTPError:
            raise HTTPException(502, "Falha na consulta Gmail. Tente novamente.") from None

    @app.post(PREFIX + "/import")
    def import_files(data: ImportFiles, user=Depends(current), db: Session = Depends(db_session)):
        manager(user, db)
        client, config = authorized_client(db)
        results = []
        with closing(client):
            folder = api_json(
                client.get(
                    DRIVE + "/files/" + quote(config.drive_folder, safe=""),
                    params={"fields": "id,mimeType,trashed,capabilities(canAddChildren)"},
                ),
                "Verificar pasta Drive",
            )
            if (
                folder.get("mimeType") != "application/vnd.google-apps.folder"
                or folder.get("trashed")
                or not folder.get("capabilities", {}).get("canAddChildren")
            ):
                raise HTTPException(
                    422,
                    "A pasta Drive não está disponível para guardar ficheiros. Confirme a pasta e a permissão de edição.",
                )
            for key in dict.fromkeys(data.ids):
                item = db.scalar(
                    select(GoogleInvoiceFile).where(GoogleInvoiceFile.id == key).with_for_update()
                )
                if not item or item.account != config.google_email.lower():
                    raise HTTPException(404, "Anexo não encontrado nesta conta/empresa.")
                try:
                    if item.uploaded:
                        results.append({"id": key, "status": "já_importado"})
                        continue
                    raw = item.data if item.data else download(client, item)
                    item.mime = validate_bytes(raw)
                    item.size = len(raw)
                    item.digest = hashlib.sha256(raw).hexdigest()
                    duplicate = db.scalar(
                        select(GoogleInvoiceFile).where(
                            GoogleInvoiceFile.digest == item.digest,
                            GoogleInvoiceFile.uploaded.is_(True),
                            GoogleInvoiceFile.drive_folder == config.drive_folder,
                            GoogleInvoiceFile.id != item.id,
                        )
                    )
                    if duplicate:
                        item.drive_id, item.drive_folder = (
                            duplicate.drive_id,
                            duplicate.drive_folder,
                        )
                        # Verify the shared copy still exists before treating it as imported.
                    upload(client, db, item, config.drive_folder, db.info["company"]["id"], raw)
                    item.uploaded = True
                    record(
                        db,
                        user,
                        "gmail_anexo_importado",
                        item.id,
                        {
                            "drive_id": item.drive_id,
                            "name": item.name,
                            "duplicate_content": bool(duplicate),
                        },
                    )
                    db.commit()
                    results.append(
                        {"id": key, "status": "conteúdo_duplicado" if duplicate else "importado"}
                    )
                except (HTTPException, httpx.HTTPError) as exc:
                    db.rollback()
                    results.append(
                        {
                            "id": key,
                            "status": "falhou",
                            "error": exc.detail
                            if isinstance(exc, HTTPException)
                            else "Falha de comunicação Google; pode repetir sem duplicar o ficheiro.",
                        }
                    )
            return {"results": results}

    @app.get(PREFIX + "/files/{key}/preview")
    def preview(key: str, user=Depends(current), db: Session = Depends(db_session)):
        manager(user, db)
        item = get_file(db, key)
        if not item.uploaded:
            raise HTTPException(409, "Importe o anexo para o Drive primeiro.")
        from .drive_documents import content

        client, _ = authorized_client(db)
        with closing(client):
            raw, mime = content(client, item.drive_id)
        return Response(
            raw,
            media_type=mime,
            headers={"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"},
        )

    @app.get(PREFIX + "/files/{key}/suggestions")
    def suggestions(
        key: str, search: str = "", user=Depends(current), db: Session = Depends(db_session)
    ):
        manager(user, db)
        item = get_file(db, key)
        analyzed = SimpleNamespace(name=item.name, subject=item.subject, extracted_text="")
        if item.uploaded:
            from .drive_documents import content

            client, _ = authorized_client(db)
            with closing(client):
                raw, mime = content(client, item.drive_id)
            analyzed.extracted_text = pdf_text(raw, mime)
        statement = select(Purchase).where(Purchase.store_id.in_(scope(user, db)))
        if search:
            pattern = "%" + search[:100] + "%"
            statement = statement.where(
                Purchase.document.ilike(pattern)
                | Purchase.supplier_name.ilike(pattern)
                | Purchase.supplier_nif.ilike(pattern)
            )
        purchases = list(db.scalars(statement.order_by(Purchase.document_date.desc()).limit(1000)))
        rows = []
        for p in purchases:
            confidence, reasons = score(analyzed, p)
            rows.append(
                {
                    "id": p.id,
                    "supplier": p.supplier_name,
                    "nif": p.supplier_nif,
                    "document": p.document,
                    "date": p.document_date,
                    "amount_cents": p.amount_cents,
                    "confidence": confidence,
                    "reasons": reasons,
                }
            )
        return {
            "suggestions": sorted(
                [r for r in rows if r["confidence"] >= 25], key=lambda r: -r["confidence"]
            )[:5],
            "documents": rows[:1000],
            "limited": len(purchases) == 1000,
        }

    @app.post(PREFIX + "/files/{key}/associate")
    def associate(
        key: str, data: Associate, user=Depends(current), db: Session = Depends(db_session)
    ):
        manager(user, db)
        if not data.confirmed:
            raise HTTPException(422, "Confirme a associação à fatura.")
        item = get_file(db, key)
        if not item.uploaded:
            raise HTTPException(409, "Importe o anexo para o Drive primeiro.")
        purchase = db.get(Purchase, data.purchase_id)
        if not purchase:
            raise HTTPException(404, "Fatura n\u00e3o encontrada.")
        authorize(user, db, purchase.store_id, ["administrador", "gestor"])
        legacy = db.get(PurchaseAttachment, item.attachment_id) if item.attachment_id else None
        if legacy:
            if legacy.purchase_id != purchase.id:
                raise HTTPException(409, "Este anexo j\u00e1 est\u00e1 associado a outra fatura.")
            return {"ok": True, "attachment_id": legacy.id}
        from .drive_documents import attach_reference, content

        client, config = authorized_client(db)
        with closing(client):
            raw, mime = content(client, item.drive_id)
        purchase = db.scalar(
            select(Purchase).where(Purchase.id == data.purchase_id).with_for_update()
        )
        digest = hashlib.sha256(raw).hexdigest()
        db.execute(
            select(
                func.pg_advisory_xact_lock(func.hashtext(db.info["company"]["id"] + ":" + digest))
            )
        )
        linked = db.scalar(
            select(DriveDocumentAttachment).where(DriveDocumentAttachment.digest == digest)
        )
        if linked and linked.purchase_id != purchase.id:
            raise HTTPException(
                409, "Uma c\u00f3pia deste anexo j\u00e1 est\u00e1 associada a outra fatura."
            )
        link = attach_reference(
            db,
            purchase,
            item.account,
            item.drive_id,
            item.drive_folder,
            item.name,
            mime,
            digest,
            user,
            record,
        )
        db.commit()
        return {"ok": True, "attachment_id": "drive:" + link.id}
