from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[int] = mapped_column(Integer, default=1)


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Access(Base):
    __tablename__ = "store_access"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), primary_key=True)


class Register(Base):
    __tablename__ = "registers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    name: Mapped[str] = mapped_column(String(100))


class BankAccount(Base):
    __tablename__ = "bank_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    name: Mapped[str] = mapped_column(String(100))
    bank: Mapped[str] = mapped_column(String(100))
    iban_last4: Mapped[str] = mapped_column(String(4))


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class CashSession(Base):
    __tablename__ = "cash_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    register_id: Mapped[str] = mapped_column(ForeignKey("registers.id"))
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    opening_cents: Mapped[int] = mapped_column(BigInteger)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    counted_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    close_reason: Mapped[str] = mapped_column(Text, default="")
    denominations: Mapped[str] = mapped_column(Text, default="{}")
    __table_args__ = (
        Index(
            "one_open_session_per_register",
            "register_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
            sqlite_where=text("closed_at IS NULL"),
        ),
    )


class Movement(Base):
    __tablename__ = "movements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("cash_sessions.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    kind: Mapped[str] = mapped_column(String(40))
    payment: Mapped[str] = mapped_column(String(20))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    entity: Mapped[str] = mapped_column(String(200), default="")
    document: Mapped[str] = mapped_column(String(200), default="")
    reference: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(20), default="manual")
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    reversed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reversal_of: Mapped[str | None] = mapped_column(
        ForeignKey("movements.id"), unique=True, nullable=True
    )
    transfer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    store_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    movement_id: Mapped[str] = mapped_column(ForeignKey("movements.id"))
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
