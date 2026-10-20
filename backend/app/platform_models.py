from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .models import now, uid


class PlatformBase(DeclarativeBase):
    pass


class LegalConfiguration(PlatformBase):
    __tablename__ = "platform_legal_configuration"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    revision: Mapped[int] = mapped_column(default=0)
    data: Mapped[str] = mapped_column(Text, default="{}")
    publication_id: Mapped[str] = mapped_column(String(36), default="")


class LegalPublication(PlatformBase):
    __tablename__ = "platform_legal_publications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    version: Mapped[str] = mapped_column(String(80), unique=True)
    digest: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    admin_id: Mapped[str] = mapped_column(String(36))


class LegalAcceptance(PlatformBase):
    __tablename__ = "platform_legal_acceptances"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(String(36))
    publication_id: Mapped[str] = mapped_column(String(36))
    digest: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PrivacyRequest(PlatformBase):
    __tablename__ = "platform_privacy_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(254))
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text, default="")
    company_reference: Mapped[str] = mapped_column(String(200), default="")
    company_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    user_id: Mapped[str] = mapped_column(String(36), default="")
    status: Mapped[str] = mapped_column(String(40), default="received")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(default=0)
    identity_verified: Mapped[int] = mapped_column(default=0)
    history: Mapped[str] = mapped_column(Text, default="[]")


class Company(PlatformBase):
    __tablename__ = "platform_companies"
    __table_args__ = (
        Index(
            "platform_companies_current_nif_key",
            "nif",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    nif: Mapped[str | None] = mapped_column(String(9), nullable=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    schema_name: Mapped[str] = mapped_column(String(80), unique=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="demonstracao")
    api_url: Mapped[str] = mapped_column(
        String(300), default="https://api.wintouchcloud.com/api/v1"
    )
    api_enterprise: Mapped[str] = mapped_column(String(100), default="")
    api_key_ciphertext: Mapped[str] = mapped_column(Text, default="")
    api_active: Mapped[int] = mapped_column(Integer, default=0)
    erp_provider: Mapped[str] = mapped_column(String(40), default="wintouch", server_default="wintouch")
    rubric_code_prefix: Mapped[str] = mapped_column(String(30), default="RUB", server_default="RUB")
    operating_mode: Mapped[str] = mapped_column(String(20), default="local", server_default="local")
    connector_writes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sync_resources: Mapped[str] = mapped_column(Text, default='["products","entities","cash_accounts","sales","purchase_documents","rubrics"]', server_default='["products","entities","cash_accounts","sales","purchase_documents","rubrics"]')
    data_authority: Mapped[str] = mapped_column(Text, default='{}', server_default='{}')
    integration_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlatformAdmin(PlatformBase):
    __tablename__ = "platform_admins"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class PlatformSession(PlatformBase):
    __tablename__ = "platform_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[str] = mapped_column(ForeignKey("platform_admins.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlatformAudit(PlatformBase):
    __tablename__ = "platform_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    admin_id: Mapped[str] = mapped_column(ForeignKey("platform_admins.id"))
    company_id: Mapped[str | None] = mapped_column(
        ForeignKey("platform_companies.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
