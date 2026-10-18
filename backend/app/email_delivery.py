"""Frozen email drafts, shared history, explicit SMTP submission and receipts."""

import hashlib
import io
import json
import time
import zipfile
from datetime import timedelta
from typing import Literal

from fastapi import Depends, HTTPException, Response
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, defer

from . import email_transport
from .db import db_session
from .email_settings import configuration, email_address
from .models import EmailDispatch, EmailDispatchPart, now
from .platform import cipher
from .schemas import Strict


class DraftInput(Strict):
    revision: int = Field(ge=1)
    to: list[str] = Field(min_length=1, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=30_000)
    selected_keys: list[str] = Field(min_length=1, max_length=1000)
    format: Literal["zip", "individual"] = "zip"
    allow_split: bool = False

    @field_validator("to", "cc")
    @classmethod
    def emails(cls, values):
        return list(dict.fromkeys(email_address(value) for value in values))

    @field_validator("subject")
    @classmethod
    def header(cls, value):
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Indique um assunto sem quebras de linha.")
        return value.strip()


class SendInput(Strict):
    revision: int = Field(ge=1)
    request_key: str = Field(min_length=10, max_length=100)
    confirmed: Literal[True]


class ReceiptInput(Strict):
    confirmed: Literal[True]
    note: str = Field(min_length=3, max_length=2000)


class RetryInput(Strict):
    request_key: str = Field(min_length=10, max_length=100)
    acknowledge_uncertain: bool = False


def get_dispatch(db, identifier, lock=False):
    query = select(EmailDispatch).where(EmailDispatch.id == identifier)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if not row:
        raise HTTPException(404, "Envio não encontrado nesta empresa.")
    return row


def parts_for(db, identifier):
    return list(
        db.scalars(
            select(EmailDispatchPart)
            .where(EmailDispatchPart.dispatch_id == identifier)
            .order_by(EmailDispatchPart.number)
        )
    )


def dispatch_view(db, row):
    draft, manifest = json.loads(row.draft), json.loads(row.manifest)
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "start": row.start_date,
        "end": row.end_date,
        "store_ids": json.loads(row.store_ids),
        "revision": row.revision,
        "draft": draft,
        "manifest": manifest,
        "error": row.error,
        "uncertain": row.uncertain,
        "retry_of": row.retry_of,
        "received_at": row.received_at,
        "received_by": row.received_by,
        "receipt_note": row.receipt_note,
        "parts": [
            {
                "id": part.id,
                "number": part.number,
                "status": part.status,
                "message_id": part.message_id,
                "message_digest": part.message_digest,
                "file_keys": json.loads(part.file_keys),
                "recipients": json.loads(part.recipients),
                "size_bytes": part.size_bytes,
                "accepted_at": part.accepted_at,
                "error": part.error,
            }
            for part in parts_for(db, row.id)
        ],
    }


def frozen_files(row):
    raw = cipher().decrypt(row.payload_ciphertext)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def selected_files(row, draft):
    files = frozen_files(row)
    if not draft.get("selected_keys") or any(key not in files for key in draft["selected_keys"]):
        raise HTTPException(422, "Selecione ficheiros presentes neste dossiê preparado.")
    return {key: files[key] for key in dict.fromkeys(draft["selected_keys"])}


def prior_versions(db, recipients):
    accepted = {}
    if not recipients:
        return accepted
    query = (
        select(EmailDispatchPart, EmailDispatch.manifest)
        .join(EmailDispatch)
        .where(EmailDispatchPart.status == "accepted")
    )
    for part, raw_manifest in db.execute(query):
        if not {email.lower() for email in recipients}.issubset(
            {email.lower() for email in json.loads(part.recipients)}
        ):
            continue
        keys = set(json.loads(part.file_keys))
        for file in json.loads(raw_manifest).get("files", []):
            if file["key"] in keys:
                accepted[(file["key"], file["sha256"])] = part.dispatch_id
    return accepted


def preview_plan(db, row, draft, config):
    files = selected_files(row, draft)
    manifest = json.loads(row.manifest)
    metadata = {file["key"]: file for file in manifest["files"]}
    limit = min(
        config["max_message_bytes"],
        manifest.get("server_limit_bytes") or config["max_message_bytes"],
    )
    messages = email_transport.plan_messages(config, row, draft, files, metadata, limit)
    manifest["plan"] = {
        "limit_bytes": limit,
        "messages": len(messages),
        "sizes": [len(raw) for _, raw in messages],
        "requires_split_confirmation": len(messages) > 1 and not draft["allow_split"],
        "total_bytes": sum(len(raw) for _, raw in messages),
    }
    manifest["sender"] = {"name": config["sender_name"], "email": config["sender_email"]}
    previous = prior_versions(db, [*draft["to"], *draft["cc"]])
    for item in manifest["files"]:
        item["previous_dispatch"] = previous.get((item["key"], item["sha256"]))
    row.manifest = json.dumps(manifest, ensure_ascii=False)
    return messages


def submit_dispatch(db, row, data, user, audit):
    if row.status in {"accepted", "received"}:
        return dispatch_view(db, row)
    if row.status != "prepared" or row.last_attempt_key == data.request_key:
        raise HTTPException(
            409,
            "Este envio já foi tentado ou está em curso. Consulte o histórico antes de preparar nova tentativa.",
        )
    if row.revision != data.revision:
        raise HTTPException(409, "A pré-visualização mudou. Reveja e confirme novamente.")
    settings, config = configuration(db)
    if not settings or not config["enabled"]:
        raise HTTPException(422, "Ative o SMTP em Configurações → Email antes de enviar.")
    if row.config_revision != settings.revision:
        raise HTTPException(
            409, "A configuração de email mudou. Atualize a pré-visualização antes de enviar."
        )
    draft = json.loads(row.draft)
    plan = preview_plan(db, row, draft, config)
    if len(plan) > 1 and not draft["allow_split"]:
        db.commit()
        raise HTTPException(
            409,
            "O limite exige várias mensagens. Autorize a divisão e confirme uma nova pré-visualização.",
        )
    row.status = "sending"
    row.started_at = now()
    row.updated_at = now()
    row.last_attempt_key = data.request_key
    audit(
        db,
        user,
        "email_envio_iniciado",
        row.id,
        {"revision": row.revision, "request_key": data.request_key},
    )
    db.commit()
    client = None
    try:
        try:
            password = (
                cipher().decrypt(settings.password_ciphertext.encode()).decode()
                if settings.password_ciphertext
                else ""
            )
        except Exception:
            raise email_transport.DeliveryFailure(
                "Não foi possível abrir a credencial SMTP. Volte a guardá-la em Email."
            ) from None
        client, server_limit = email_transport.connect(config, password)
        if server_limit:
            manifest = json.loads(row.manifest)
            manifest["server_limit_bytes"] = server_limit
            row.manifest = json.dumps(manifest, ensure_ascii=False)
            plan = preview_plan(db, row, draft, config)
        if len(plan) > 1 and not draft["allow_split"]:
            row.status = "prepared"
            row.revision += 1
            row.error = "O limite anunciado pelo servidor exige divisão. Reveja a pré-visualização e autorize mensagens numeradas. Nenhum conteúdo foi enviado."
            db.commit()
            return dispatch_view(db, row)
        metadata = {file["key"]: file for file in json.loads(row.manifest)["files"]}
        for number, (keys, raw) in enumerate(plan, 1):
            part = EmailDispatchPart(
                dispatch_id=row.id,
                number=number,
                status="prepared",
                message_id=f"<zela.{row.id}.{number}@{config['sender_email'].split('@')[1]}>",
                message_digest=hashlib.sha256(raw).hexdigest(),
                file_keys=json.dumps(keys),
                recipients=json.dumps(list(dict.fromkeys([*draft["to"], *draft["cc"]]))),
                size_bytes=len(raw),
            )
            db.add(part)
        db.commit()
        started = time.monotonic()
        for part, (_, raw) in zip(parts_for(db, row.id), plan):
            if time.monotonic() - started > 120:
                raise email_transport.DeliveryFailure(
                    "O limite de tempo desta tentativa foi atingido. Prepare nova tentativa para as mensagens que faltam."
                )

            def before_data(part=part):
                part.status = "sending"
                db.commit()

            try:
                email_transport.submit(
                    client, config["sender_email"], json.loads(part.recipients), raw, before_data
                )
            except email_transport.DeliveryFailure as exc:
                part.status = "failed"
                part.error = str(exc)
                raise
            part.status = "accepted"
            part.accepted_at = now()
            audit(
                db,
                user,
                "email_smtp_aceite",
                row.id,
                {
                    "part": part.number,
                    "message_id": part.message_id,
                    "size_bytes": part.size_bytes,
                    "files": [
                        {"key": key, "sha256": metadata[key]["sha256"]}
                        for key in json.loads(part.file_keys)
                    ],
                },
            )
            db.commit()
        row.status = "accepted"
        row.error = ""
        row.updated_at = now()
        db.commit()
    except HTTPException as exc:
        row.status = (
            "failed"
            if any(part.status == "accepted" for part in parts_for(db, row.id))
            else "prepared"
        )
        row.revision += 1
        row.error = str(exc.detail)
        row.updated_at = now()
        db.commit()
    except email_transport.DeliveryFailure as exc:
        row.status = "failed"
        row.uncertain = exc.uncertain
        row.error = str(exc)
        row.updated_at = now()
        audit(
            db,
            user,
            "email_envio_falhou",
            row.id,
            {"uncertain": exc.uncertain, "message": str(exc)},
        )
        db.commit()
    except Exception:
        db.rollback()
        row = get_dispatch(db, row.id, True)
        row.status = "failed"
        row.uncertain = True
        row.error = "A tentativa foi interrompida. Confirme no servidor e com o destinatário antes de repetir."
        db.commit()
    finally:
        if client:
            email_transport.disconnect(client)
    return dispatch_view(db, row)


def register_routes(app, current, authorize, audit):
    def admin(user, db):
        authorize(user, db, roles=["administrador"])

    @app.get("/api/emails")
    def history(user=Depends(current), db: Session = Depends(db_session)):
        admin(user, db)
        rows = db.scalars(
            select(EmailDispatch)
            .options(defer(EmailDispatch.payload_ciphertext))
            .order_by(EmailDispatch.created_at.desc())
            .limit(100)
        )
        return [dispatch_view(db, row) for row in rows]

    @app.get("/api/emails/{identifier}")
    def detail(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        admin(user, db)
        return dispatch_view(db, get_dispatch(db, identifier))

    @app.get("/api/emails/{identifier}/files/{key:path}")
    def file_view(
        identifier: str, key: str, user=Depends(current), db: Session = Depends(db_session)
    ):
        admin(user, db)
        row = get_dispatch(db, identifier)
        files = frozen_files(row)
        if key not in files:
            raise HTTPException(404, "Ficheiro não encontrado neste envio.")
        return Response(
            files[key],
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="' + key.split("/")[-1] + '"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/emails/{identifier}/preview")
    def preview(
        identifier: str, data: DraftInput, user=Depends(current), db: Session = Depends(db_session)
    ):
        admin(user, db)
        row = get_dispatch(db, identifier, True)
        if row.status != "prepared" or row.revision != data.revision:
            raise HTTPException(409, "Este envio já foi tentado ou alterado. Consulte o histórico.")
        settings, config = configuration(db)
        if not config["sender_email"]:
            raise HTTPException(
                422, "Configure o email do remetente antes de pré-visualizar o envio."
            )
        draft = data.model_dump(exclude={"revision"})
        draft["cc"] = [
            email
            for email in draft["cc"]
            if email.lower() not in {value.lower() for value in draft["to"]}
        ]
        preview_plan(db, row, draft, config)
        row.draft = json.dumps(draft, ensure_ascii=False)
        row.revision += 1
        row.config_revision = settings.revision if settings else 0
        row.updated_at = now()
        row.error = ""
        audit(
            db,
            user,
            "email_previsualizado",
            row.id,
            {"revision": row.revision, "selected_keys": draft["selected_keys"]},
        )
        db.commit()
        return dispatch_view(db, row)

    @app.post("/api/emails/{identifier}/send")
    def send(
        identifier: str, data: SendInput, user=Depends(current), db: Session = Depends(db_session)
    ):
        admin(user, db)
        return submit_dispatch(db, get_dispatch(db, identifier, True), data, user, audit)

    @app.post("/api/emails/{identifier}/receipt")
    def receipt(
        identifier: str,
        data: ReceiptInput,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        admin(user, db)
        row = get_dispatch(db, identifier, True)
        if row.status not in {"accepted", "received"}:
            raise HTTPException(409, "Só pode confirmar a receção de um envio aceite pelo SMTP.")
        if not data.note.strip():
            raise HTTPException(422, "Indique como foi confirmada a receção.")
        if row.status != "received":
            row.status = "received"
            row.received_by = user.id
            row.received_at = now()
            row.receipt_note = data.note.strip()
            audit(db, user, "email_rececao_confirmada", row.id, {"note": row.receipt_note})
            db.commit()
        return dispatch_view(db, row)

    @app.post("/api/emails/{identifier}/retry")
    def retry(
        identifier: str, data: RetryInput, user=Depends(current), db: Session = Depends(db_session)
    ):
        from .models import uid

        admin(user, db)
        row = get_dispatch(db, identifier, True)
        existing = db.scalar(
            select(EmailDispatch).where(EmailDispatch.request_key == data.request_key)
        )
        if existing:
            return dispatch_view(db, existing)
        if row.status != "failed":
            raise HTTPException(409, "Só é possível preparar nova tentativa de um envio falhado.")
        if row.uncertain and not data.acknowledge_uncertain:
            raise HTTPException(
                409,
                "A entrega é incerta. Confirme no servidor/destinatário e reconheça o risco de duplicação antes de preparar novo envio.",
            )
        accepted = {
            key
            for part in parts_for(db, row.id)
            if part.status == "accepted"
            for key in json.loads(part.file_keys)
        }
        draft = json.loads(row.draft)
        draft["selected_keys"] = [key for key in draft["selected_keys"] if key not in accepted]
        if not draft["selected_keys"]:
            raise HTTPException(
                409, "Todas as mensagens foram aceites pelo servidor. Não repita o envio."
            )
        new = EmailDispatch(
            id=uid(),
            request_key=data.request_key,
            created_by=user.id,
            start_date=row.start_date,
            end_date=row.end_date,
            store_ids=row.store_ids,
            manifest=row.manifest,
            draft=json.dumps(draft, ensure_ascii=False),
            payload_ciphertext=row.payload_ciphertext,
            retry_of=row.id,
            created_at=now(),
        )
        manifest = json.loads(new.manifest)
        manifest.pop("plan", None)
        new.manifest = json.dumps(manifest, ensure_ascii=False)
        db.add(new)
        db.flush()
        audit(
            db,
            user,
            "email_nova_tentativa_preparada",
            new.id,
            {
                "retry_of": row.id,
                "accepted_files_excluded": sorted(accepted),
                "uncertain_acknowledged": data.acknowledge_uncertain,
            },
        )
        db.commit()
        return dispatch_view(db, new)

    @app.post("/api/emails/{identifier}/recover")
    def recover(
        identifier: str,
        data: ReceiptInput,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        admin(user, db)
        row = get_dispatch(db, identifier, True)
        if (
            row.status != "sending"
            or not row.started_at
            or row.started_at > now() - timedelta(minutes=20)
        ):
            raise HTTPException(
                409, "A tentativa ainda pode estar em curso. Aguarde antes de recuperar o estado."
            )
        row.status = "failed"
        row.uncertain = True
        row.error = "Tentativa interrompida ou estado de entrega incerto. " + data.note
        audit(db, user, "email_estado_recuperado", row.id, {"note": data.note, "uncertain": True})
        db.commit()
        return dispatch_view(db, row)
