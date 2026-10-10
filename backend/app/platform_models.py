from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .models import now, uid


class PlatformBase(DeclarativeBase):
    pass


class Company(PlatformBase):
    __tablename__ = "platform_companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    schema_name: Mapped[str] = mapped_column(String(80), unique=True)
    active: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(20), default="demonstracao")
    api_url: Mapped[str] = mapped_column(
        String(300), default="https://api.wintouchcloud.com/api/v1"
    )
    api_enterprise: Mapped[str] = mapped_column(String(100), default="")
    api_key_ciphertext: Mapped[str] = mapped_column(Text, default="")
    api_active: Mapped[int] = mapped_column(Integer, default=0)
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
