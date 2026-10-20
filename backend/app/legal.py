"""Publication gates, durable terms receipts and proportionate rights handling."""

import calendar
import hashlib
import json
import re
import secrets
from collections import defaultdict, deque
from datetime import timezone
from time import monotonic
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .access import current
from .db import db_session, platform_db
from .email_settings import email_address
from .legal_content import COOKIE_VERSION, COOKIES, documents
from .models import now
from .platform import require_platform
from .platform_models import (
    LegalAcceptance,
    LegalConfiguration,
    LegalPublication,
    PlatformAudit,
    PrivacyRequest,
)
from .schemas import Strict

router = APIRouter()
attempts = defaultdict(deque)
FIELDS = (
    "audience_notice",
    "commercial_terms",
    "processing_notice",
    "providers_notice",
    "transfers_notice",
    "retention_policy",
    "complaints_notice",
    "accessibility_notice",
    "dpa_instructions",
)
DEFAULTS = {
    "version": "",
    "audience": "unconfirmed",
    "identity": {
        k: "" for k in ("legal_name", "nif", "address", "email", "registry", "privacy_email")
    },
    **{k: "" for k in FIELDS},
    "reviewed": False,
    "consumer_terms": "",
}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def month_after(value, months=1):
    original_zone = value.tzinfo
    value = value.replace(tzinfo=timezone.utc) if original_zone is None else value
    value = value.astimezone(ZoneInfo("Europe/Lisbon"))
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    result = value.replace(
        year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1])
    )
    return result.astimezone(original_zone or timezone.utc)


def config_view(db):
    row = db.get(LegalConfiguration, "default")
    return row, {**DEFAULTS, **(json.loads(row.data) if row else {})}


def publication(db):
    row = db.get(LegalConfiguration, "default")
    return db.get(LegalPublication, row.publication_id) if row and row.publication_id else None


def pending(config):
    missing = []
    for key in DEFAULTS["identity"]:
        if not config.get("identity", {}).get(key, "").strip():
            missing.append("identity." + key)
    for key in FIELDS:
        if not config.get(key, "").strip():
            missing.append(key)
    if config.get("audience") not in ("professionals", "consumers", "both"):
        missing.append("audience")
    if (
        config.get("audience") in ("consumers", "both")
        and not config.get("consumer_terms", "").strip()
    ):
        missing.append("consumer_terms")
    if not config.get("version", "").strip():
        missing.append("version")
    if not config.get("reviewed"):
        missing.append("reviewed")
    if re.search(r"\[(?:preencher|pendente|TODO)[^\]]*\]|<preencher>", dump(config), re.I):
        missing.append("Campos por preencher no texto")
    return missing


class Identity(Strict):
    legal_name: str = Field(default="", max_length=200)
    nif: str = Field(default="", max_length=30)
    address: str = Field(default="", max_length=500)
    email: str = Field(default="", max_length=254)
    registry: str = Field(default="", max_length=500)
    privacy_email: str = Field(default="", max_length=254)

    @field_validator("email", "privacy_email")
    @classmethod
    def email_valid(cls, value):
        return email_address(value) if value else ""


class LegalInput(Strict):
    revision: int = Field(ge=0)
    publish: bool = False
    version: str = Field(default="", max_length=80, pattern=r"^[\w.\-]*$")
    audience: Literal["unconfirmed", "professionals", "consumers", "both"] = "unconfirmed"
    identity: Identity = Field(default_factory=Identity)
    audience_notice: str = Field(default="", max_length=10000)
    commercial_terms: str = Field(default="", max_length=20000)
    processing_notice: str = Field(default="", max_length=20000)
    providers_notice: str = Field(default="", max_length=20000)
    transfers_notice: str = Field(default="", max_length=10000)
    retention_policy: str = Field(default="", max_length=20000)
    complaints_notice: str = Field(default="", max_length=10000)
    accessibility_notice: str = Field(default="", max_length=10000)
    dpa_instructions: str = Field(default="", max_length=20000)
    consumer_terms: str = Field(default="", max_length=20000)
    reviewed: bool = False


@router.get("/api/platform/legal")
def get_config(admin=Depends(require_platform), db: Session = Depends(platform_db)):
    row, config = config_view(db)
    released = publication(db)
    return {
        "config": config,
        "revision": row.revision if row else 0,
        "pending": pending(config),
        "published_version": released.version if released else None,
        "preview": documents(config),
        "cookies": COOKIES,
    }


@router.post("/api/platform/legal")
def save_config(
    data: LegalInput, admin=Depends(require_platform), db: Session = Depends(platform_db)
):
    # Serializes first publication as well as edits to an existing singleton.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('iazela-legal-publication'))"))
    row, _ = config_view(db)
    if data.revision != (row.revision if row else 0):
        raise HTTPException(409, "A configuração mudou. Atualize antes de guardar.")
    config = data.model_dump(exclude={"revision", "publish"})
    if data.publish and pending(config):
        raise HTTPException(422, "Publicação bloqueada: " + ", ".join(pending(config)))
    if row is None:
        row = LegalConfiguration(id="default", revision=0)
        db.add(row)
    row.data = dump(config)
    row.revision += 1
    if data.publish:
        snapshot = {
            "version": data.version,
            "documents": documents(config),
            "identity": config["identity"],
            "audience": config["audience"],
        }
        encoded = dump(snapshot)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        existing = db.scalar(
            select(LegalPublication).where(LegalPublication.version == data.version)
        )
        if existing and existing.digest != digest:
            raise HTTPException(
                409, "Esta versão já foi publicada. Use uma nova versão para alterar os textos."
            )
        released = existing or LegalPublication(
            version=data.version, digest=digest, snapshot=encoded, admin_id=admin.id
        )
        db.add(released)
        db.flush()
        row.publication_id = released.id
    db.add(
        PlatformAudit(
            admin_id=admin.id,
            action="legal_publicado" if data.publish else "legal_rascunho_guardado",
        )
    )
    db.commit()
    return get_config(admin, db)


@router.get("/api/legal/public")
def public(db: Session = Depends(platform_db)):
    released = publication(db)
    return {
        "published": bool(released),
        "publication": {**json.loads(released.snapshot), "digest": released.digest}
        if released
        else None,
        "cookies": COOKIES,
        "cookie_version": COOKIE_VERSION,
        "optional_categories": [],
        "marketing_available": False,
    }


@router.get("/api/legal/documents/{version}/{slug}")
def download(version: str, slug: str, db: Session = Depends(platform_db)):
    released = db.scalar(select(LegalPublication).where(LegalPublication.version == version))
    doc = json.loads(released.snapshot)["documents"].get(slug) if released else None
    if not doc:
        raise HTTPException(404, "Documento publicado não encontrado.")
    content = (
        f"# {doc['title']}\nVersão: {version}\nSHA-256 da publicação: {released.digest}\n\n"
        + "\n\n".join(f"## {s['title']}\n{s['text']}" for s in doc["sections"])
    )
    return Response(
        content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="iazela-{slug}-{version}.md"'},
    )


class RightsInput(Strict):
    email: str = Field(max_length=254)
    kind: Literal[
        "access",
        "rectification",
        "erasure",
        "restriction",
        "objection",
        "portability",
        "closure",
        "company_export",
    ]
    company_reference: str = Field(default="", max_length=200)
    message: str = Field(default="", max_length=4000)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        return email_address(value)


def receive(data, db, company_id="", user_id=""):
    token = secrets.token_urlsafe(32)
    received = now()
    row = PrivacyRequest(
        receipt_hash=hashlib.sha256(token.encode()).hexdigest(),
        email=data.email,
        kind=data.kind,
        company_reference=data.company_reference,
        message=data.message,
        received_at=received,
        due_at=month_after(received),
        company_id=company_id,
        user_id=user_id,
    )
    db.add(row)
    db.commit()
    return {
        "reference": row.id,
        "receipt": token,
        "received_at": row.received_at,
        "due_at": row.due_at,
        "status": row.status,
        "message": "Pedido registado. Guarde o comprovativo privado. A identidade e o âmbito serão verificados antes de divulgar ou alterar dados. Não foi enviado email automático.",
    }


@router.post("/api/legal/rights")
def rights(data: RightsInput, request: Request, db: Session = Depends(platform_db)):
    if not publication(db):
        raise HTTPException(
            503,
            {
                "public_message": "O canal público ainda não foi ativado. A identificação e os contactos do prestador aguardam validação."
            },
        )
    key = request.client.host if request.client else "local"
    queue = attempts[key]
    while queue and queue[0] < monotonic() - 3600:
        queue.popleft()
    if len(queue) >= 5:
        raise HTTPException(
            429, "Demasiados pedidos. Aguarde ou utilize o contacto de privacidade publicado."
        )
    queue.append(monotonic())
    return receive(data, db)


class ReceiptInput(Strict):
    receipt: str = Field(min_length=30, max_length=100)


def request_view(row, private=False):
    result = {
        "reference": row.id,
        "kind": row.kind,
        "status": row.status,
        "received_at": row.received_at,
        "due_at": row.due_at,
        "revision": row.revision,
    }
    if private:
        result.update(
            email=row.email,
            message=row.message,
            company_reference=row.company_reference,
            company_id=row.company_id,
            identity_verified=bool(row.identity_verified),
            history=json.loads(row.history),
        )
    return result


@router.post("/api/legal/rights/status")
def rights_status(data: ReceiptInput, db: Session = Depends(platform_db)):
    row = db.scalar(
        select(PrivacyRequest).where(
            PrivacyRequest.receipt_hash == hashlib.sha256(data.receipt.encode()).hexdigest()
        )
    )
    if not row:
        raise HTTPException(404, "Comprovativo não encontrado.")
    return request_view(row)


@router.get("/api/platform/privacy-requests")
def requests(admin=Depends(require_platform), db: Session = Depends(platform_db)):
    return [
        request_view(row, True)
        for row in db.scalars(
            select(PrivacyRequest).order_by(PrivacyRequest.received_at.desc()).limit(500)
        )
    ]


class RightsUpdate(Strict):
    revision: int = Field(ge=0)
    status: Literal[
        "received", "identity_required", "in_progress", "forwarded", "answered", "refused"
    ]
    identity_verified: bool = False
    note: str = Field(min_length=5, max_length=4000)
    extend: bool = False
    communication_sent: bool = False


@router.post("/api/platform/privacy-requests/{identifier}")
def update_request(
    identifier: str,
    data: RightsUpdate,
    admin=Depends(require_platform),
    db: Session = Depends(platform_db),
):
    row = db.scalar(select(PrivacyRequest).where(PrivacyRequest.id == identifier).with_for_update())
    if not row:
        raise HTTPException(404, "Pedido não encontrado.")
    if row.revision != data.revision:
        raise HTTPException(409, "O pedido foi atualizado. Recarregue a lista.")
    if data.status == "answered" and not data.identity_verified:
        raise HTTPException(
            422,
            "Confirme a verificação proporcional da identidade antes de registar uma resposta com dados.",
        )
    if (
        data.extend or data.status in ("answered", "refused", "forwarded")
    ) and not data.communication_sent:
        raise HTTPException(
            422,
            "Registe a comunicação efetivamente enviada ao titular. Guardar aqui não envia mensagens.",
        )
    if data.extend:
        initial_due = month_after(row.received_at)
        if initial_due.astimezone(timezone.utc) < now():
            raise HTTPException(
                422,
                "O primeiro mês já terminou; registe o atraso e contacte o titular. Não pode registar uma prorrogação tempestiva.",
            )
        row.due_at = month_after(row.received_at, 3)
    row.status = data.status
    row.identity_verified = int(data.identity_verified)
    row.revision += 1
    history = json.loads(row.history)
    history.append({"at": str(now()), "admin_id": admin.id, **data.model_dump()})
    row.history = dump(history)
    db.add(PlatformAudit(admin_id=admin.id, action="privacidade_pedido_atualizado"))
    db.commit()
    return request_view(row, True)


@router.get("/api/privacy/me")
def me(
    user=Depends(current), tenant: Session = Depends(db_session), db: Session = Depends(platform_db)
):
    company_id = tenant.info.get("company", {}).get("id", "public")
    acceptances = list(
        db.scalars(
            select(LegalAcceptance).where(
                LegalAcceptance.company_id == company_id, LegalAcceptance.user_id == user.id
            )
        )
    )
    own_requests = list(
        db.scalars(
            select(PrivacyRequest).where(
                PrivacyRequest.company_id == company_id, PrivacyRequest.user_id == user.id
            )
        )
    )
    return {
        "scope": "Dados da própria conta; não inclui documentos da empresa nem dados de terceiros. Não substitui uma resposta completa a um pedido de acesso ou a portabilidade quando aplicável.",
        "profile": {"name": user.name, "email": user.email, "role": user.role},
        "acceptances": [
            {"publication_id": a.publication_id, "digest": a.digest, "accepted_at": a.accepted_at}
            for a in acceptances
        ],
        "requests": [request_view(r) for r in own_requests],
    }


@router.post("/api/privacy/requests")
def own_rights(
    data: RightsInput,
    user=Depends(current),
    tenant: Session = Depends(db_session),
    db: Session = Depends(platform_db),
):
    if data.kind in ("closure", "company_export") and user.role != "administrador":
        raise HTTPException(
            403, "Só o administrador da empresa pode pedir encerramento ou exportação contratual."
        )
    return receive(data, db, tenant.info.get("company", {}).get("id", "public"), user.id)


class AcceptInput(Strict):
    version: str = Field(max_length=80)
    digest: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]


@router.post("/api/privacy/accept-terms")
def accept(
    data: AcceptInput,
    user=Depends(current),
    tenant: Session = Depends(db_session),
    db: Session = Depends(platform_db),
):
    if user.role != "administrador":
        raise HTTPException(
            403, "A aceitação em nome da empresa exige o administrador com poderes para o efeito."
        )
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('iazela-legal-publication'))"))
    released = publication(db)
    if not released or released.version != data.version or released.digest != data.digest:
        raise HTTPException(
            409,
            "Os termos publicados mudaram ou ainda não foram aprovados. Recarregue antes de aceitar.",
        )
    company_id = tenant.info.get("company", {}).get("id", "public")
    row = db.scalar(
        select(LegalAcceptance).where(
            LegalAcceptance.company_id == company_id,
            LegalAcceptance.user_id == user.id,
            LegalAcceptance.publication_id == released.id,
        )
    )
    if row is None:
        row = LegalAcceptance(
            company_id=company_id,
            user_id=user.id,
            publication_id=released.id,
            digest=released.digest,
        )
        db.add(row)
        db.commit()
    return {
        "version": released.version,
        "digest": row.digest,
        "accepted_at": row.accepted_at,
        "receipt": row.id,
        "document": f"/api/legal/documents/{released.version}/termos",
    }
