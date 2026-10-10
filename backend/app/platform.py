import hashlib
import os
import re
import secrets
from collections import defaultdict, deque
from datetime import timedelta, timezone
from time import monotonic
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pwdlib import PasswordHash
from pydantic import Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import engine, platform_db
from .models import Base, Category, Register, Store, User, WintouchSyncRun, now, uid
from .platform_models import Company, PlatformAdmin, PlatformAudit, PlatformSession
from .schemas import Login, Name, Strict
from .wintouch_sync import RESOURCES, SyncError, synchronize

router = APIRouter(prefix="/api/platform", tags=["Administração da plataforma"])
hasher = PasswordHash.recommended()
dummy = hasher.hash(secrets.token_urlsafe(32))
attempts = defaultdict(deque)


class CompanyCreate(Name):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    admin_name: str = Field(min_length=2, max_length=100)
    admin_email: str = Field(min_length=3, max_length=200)
    admin_password: str = Field(min_length=12, max_length=200)

    @field_validator("slug", "admin_email", mode="before")
    @classmethod
    def normalize_identifier(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class CompanyConfig(Name):
    active: bool
    api_active: bool
    api_url: str = Field(max_length=300)
    api_enterprise: str = Field(max_length=100)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False


class CompanySelect(Strict):
    slug: str = Field(max_length=80)


class SyncRequest(Strict):
    resource: str
    confirm_demo: bool = False


def cipher():
    value = os.getenv("PLATFORM_ENCRYPTION_KEY", "")
    if not value:
        raise HTTPException(503, "A chave de encriptação da plataforma não está configurada.")
    try:
        return Fernet(value.encode())
    except ValueError:
        raise HTTPException(503, "A chave de encriptação da plataforma é inválida.") from None


def validate_url(value):
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise HTTPException(422, "Endereço da API inválido.") from None
    allowed = {
        v.strip().lower()
        for v in os.getenv("WINTOUCH_ALLOWED_HOSTS", "api.wintouchcloud.com").split(",")
    }
    if (
        parts.scheme != "https"
        or parts.hostname not in allowed
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or port not in {None, 443}
    ):
        raise HTTPException(
            422,
            "Use um endereço HTTPS num servidor Wintouch autorizado, sem credenciais ou parâmetros no URL.",
        )
    return value.rstrip("/")


def view(c):
    return {
        "id": c.id,
        "name": c.name,
        "slug": c.slug,
        "active": bool(c.active),
        "mode": c.mode,
        "api_active": bool(c.api_active),
        "api_url": c.api_url,
        "api_enterprise": c.api_enterprise,
        "has_api_key": bool(c.api_key_ciphertext),
        "created_at": c.created_at,
    }


def require_platform(request: Request, db: Session = Depends(platform_db)):
    token = request.cookies.get("gpgest_platform", "")
    session = db.get(PlatformSession, hashlib.sha256(token.encode()).hexdigest())
    if not session:
        raise HTTPException(401, "Entre na administração da plataforma.")
    expires = session.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    admin = db.get(PlatformAdmin, session.admin_id)
    if expires <= now() or not admin:
        raise HTTPException(401, "A sessão da plataforma expirou.")
    return admin


@router.post("/login")
def login(data: Login, request: Request, response: Response, db: Session = Depends(platform_db)):
    key = request.client.host if request.client else "local"
    times = attempts[key]
    while times and times[0] < monotonic() - 900:
        times.popleft()
    if len(times) >= 10:
        raise HTTPException(429, "Demasiadas tentativas. Aguarde 15 minutos.")
    times.append(monotonic())
    admin = db.scalar(
        select(PlatformAdmin).where(PlatformAdmin.email == data.email.strip().lower())
    )
    valid = hasher.verify(data.password, admin.password_hash if admin else dummy)
    if not valid or not admin:
        raise HTTPException(401, "Email ou palavra-passe incorretos.")
    times.clear()
    token = secrets.token_urlsafe(48)
    db.add(
        PlatformSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            admin_id=admin.id,
            expires_at=now() + timedelta(hours=2),
        )
    )
    db.add(PlatformAudit(admin_id=admin.id, action="login"))
    db.commit()
    response.set_cookie(
        "gpgest_platform",
        token,
        max_age=7200,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE") == "true",
        samesite="strict",
        path="/api/platform",
    )
    return {"email": admin.email}


@router.get("/me")
def me(admin=Depends(require_platform)):
    return {"email": admin.email, "mode": os.getenv("APP_MODE", "real")}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    admin=Depends(require_platform),
    db: Session = Depends(platform_db),
):
    token = request.cookies.get("gpgest_platform", "")
    session = db.get(PlatformSession, hashlib.sha256(token.encode()).hexdigest())
    if session:
        db.delete(session)
    db.commit()
    response.delete_cookie("gpgest_platform", path="/api/platform")
    return {"ok": True}


@router.get("/companies")
def companies(admin=Depends(require_platform), db: Session = Depends(platform_db)):
    return [view(c) for c in db.scalars(select(Company).order_by(Company.created_at))]


@router.post("/companies")
def create_company(
    data: CompanyCreate, admin=Depends(require_platform), db: Session = Depends(platform_db)
):
    if "@" not in data.admin_email:
        raise HTTPException(422, "Email do administrador inválido.")
    if db.scalar(select(Company).where(Company.slug == data.slug)):
        raise HTTPException(409, "O identificador da empresa já existe.")
    schema = "gpgest_c_" + uid().replace("-", "")
    company = Company(
        name=data.name, slug=data.slug, schema_name=schema, mode=os.getenv("APP_MODE", "real")
    )
    db.add(company)
    db.flush()
    # O nome do esquema é gerado exclusivamente no servidor. DDL e criação ficam na mesma transação.
    assert re.fullmatch(r"gpgest_c_[a-f0-9]{32}", schema)
    connection = db.connection()
    connection.execute(text('CREATE SCHEMA "' + schema + '"'))
    tenant_connection = connection.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(tenant_connection)
    with Session(bind=tenant_connection, expire_on_commit=False) as tenant:
        tenant.add(
            User(
                name=data.admin_name,
                email=data.admin_email.strip().lower(),
                password_hash=hasher.hash(data.admin_password),
                role="administrador",
            )
        )
        store = Store(name="Loja principal")
        tenant.add(store)
        tenant.flush()
        tenant.add(Register(store_id=store.id, name="Caixa 01"))
        for name in [
            "Vendas",
            "Clientes",
            "Fornecedores",
            "Despesas correntes",
            "Transferências e fundos",
            "Outros",
        ]:
            tenant.add(Category(name=name))
        tenant.flush()
    # execution_options muda a ligação: voltar ao esquema central antes do commit e auditoria.
    connection.execution_options(
        schema_translate_map={None: db.info.get("platform_schema", "public")}
    )
    db.add(PlatformAudit(admin_id=admin.id, company_id=company.id, action="empresa_criada"))
    db.commit()
    return view(company)


@router.post("/companies/{company_id}/config")
def configure(
    company_id: str,
    data: CompanyConfig,
    admin=Depends(require_platform),
    db: Session = Depends(platform_db),
):
    company = db.scalar(select(Company).where(Company.id == company_id).with_for_update())
    if not company:
        raise HTTPException(404, "Empresa não encontrada.")
    company.api_url = validate_url(data.api_url)
    if data.clear_api_key:
        company.api_key_ciphertext = ""
    elif data.api_key:
        company.api_key_ciphertext = cipher().encrypt(data.api_key.encode()).decode()
    if data.api_active and (not company.api_key_ciphertext or not data.api_enterprise.strip()):
        raise HTTPException(
            422,
            "Configure a chave e o identificador da empresa Wintouch antes de ativar a ligação.",
        )
    company.name = data.name
    company.active = int(data.active)
    company.api_active = int(data.api_active)
    company.api_enterprise = data.api_enterprise.strip()
    db.add(PlatformAudit(admin_id=admin.id, company_id=company.id, action="configuracao_alterada"))
    db.commit()
    return view(company)


@router.post("/companies/{company_id}/test")
def test_connection(
    company_id: str, admin=Depends(require_platform), db: Session = Depends(platform_db)
):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404, "Empresa não encontrada.")
    if not company.active or not company.api_active:
        raise HTTPException(409, "Ative a empresa e a ligação antes de testar.")
    url = validate_url(company.api_url)
    try:
        key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
    except InvalidToken:
        raise HTTPException(
            503,
            "Não foi possível decifrar a chave API. Verifique a chave de encriptação do servidor.",
        ) from None
    db.add(PlatformAudit(admin_id=admin.id, company_id=company.id, action="teste_ligacao"))
    db.commit()
    try:
        # Único pedido de leitura ao endpoint confirmado no Swagger local; sem importar dados.
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            result = client.get(
                url + "/products",
                params={"limit": 1, "skip": 0},
                headers={
                    "Authorization": "ApiKey " + key,
                    "Accept": "application/json",
                    "x-current-enterprise": company.api_enterprise,
                },
            )
        if result.status_code != 200:
            return {
                "success": False,
                "message": f"A API respondeu com HTTP {result.status_code}. Verifique a configuração e as permissões.",
            }
        result.json()
        return {
            "success": True,
            "message": "Ligação de leitura ao catálogo confirmada. Não foram importados dados.",
        }
    except (httpx.HTTPError, ValueError):
        return {
            "success": False,
            "message": "Não foi possível confirmar a ligação. Verifique o servidor e a resposta JSON.",
        }


@router.get("/audit")
def audit(admin=Depends(require_platform), db: Session = Depends(platform_db)):
    return [
        {"id": a.id, "company_id": a.company_id, "action": a.action, "created_at": a.created_at}
        for a in db.scalars(
            select(PlatformAudit).order_by(PlatformAudit.created_at.desc()).limit(200)
        )
    ]


@router.post("/companies/{company_id}/sync")
def sync_company(
    company_id: str,
    data: SyncRequest,
    admin=Depends(require_platform),
    db: Session = Depends(platform_db),
):
    company = db.get(Company, company_id)
    if not company or not company.active or not company.api_active:
        raise HTTPException(409, "Ative a empresa e a ligação Wintouch antes de importar.")
    if data.resource not in RESOURCES:
        raise HTTPException(
            422,
            "Escolha artigos, entidades ou caixas. A importação financeira exige validação própria.",
        )
    if company.mode == "demonstracao" and not data.confirm_demo:
        raise HTTPException(
            422,
            "Confirme explicitamente a importação de dados Wintouch para esta empresa de demonstração.",
        )
    url = validate_url(company.api_url)
    try:
        key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
    except InvalidToken:
        raise HTTPException(503, "Não foi possível decifrar a chave API.") from None
    # A origem é obtida exclusivamente da configuração central desta empresa.
    bind = engine.execution_options(schema_translate_map={None: company.schema_name})
    if admin is not None:
        db.add(
            PlatformAudit(
                admin_id=admin.id, company_id=company.id, action="sincronizacao_" + data.resource
            )
        )
    db.commit()
    try:
        return synchronize(bind, company.api_enterprise, data.resource, url, key)
    except SyncError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/companies/{company_id}/sync-history")
def sync_history(
    company_id: str, admin=Depends(require_platform), db: Session = Depends(platform_db)
):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404, "Empresa não encontrada.")
    with Session(
        engine.execution_options(schema_translate_map={None: company.schema_name})
    ) as tenant:
        return [
            {
                "id": r.id,
                "resource": r.resource,
                "status": r.status,
                "read_count": r.read_count,
                "inserted_count": r.inserted_count,
                "updated_count": r.updated_count,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "error_message": r.error_message,
            }
            for r in tenant.scalars(
                select(WintouchSyncRun)
                .where(WintouchSyncRun.enterprise_id == company.api_enterprise)
                .order_by(WintouchSyncRun.started_at.desc())
                .limit(30)
            )
        ]


selection_router = APIRouter()


@selection_router.post("/api/company/select")
def select_company(data: CompanySelect, response: Response, db: Session = Depends(platform_db)):
    c = db.scalar(
        select(Company).where(Company.slug == data.slug.strip().lower(), Company.active == 1)
    )
    if not c:
        raise HTTPException(404, "Empresa indisponível ou identificador incorreto.")
    response.set_cookie(
        "gpgest_company",
        c.id,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE") == "true",
        samesite="strict",
        max_age=28800,
    )
    response.delete_cookie("gpgest_session")
    return {"name": c.name, "slug": c.slug}
