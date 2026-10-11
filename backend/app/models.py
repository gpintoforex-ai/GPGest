from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


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


class PlannedMovement(Base):
    __tablename__ = "planned_movements"
    __table_args__ = (CheckConstraint("amount_cents > 0"), CheckConstraint("direction IN (-1, 1)"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    entity: Mapped[str] = mapped_column(String(200))
    document: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[str] = mapped_column(String(10))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlannedSettlement(Base):
    __tablename__ = "planned_settlements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    planned_id: Mapped[str] = mapped_column(ForeignKey("planned_movements.id"), index=True)
    movement_id: Mapped[str] = mapped_column(ForeignKey("movements.id"), unique=True)


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


class WintouchSyncRun(Base):
    __tablename__ = "wt_sync_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    enterprise_id: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pendente")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_count: Mapped[int] = mapped_column(Integer, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendente', 'em_curso', 'concluida', 'falhou')", name="wt_run_status"
        ),
        CheckConstraint(
            "read_count >= 0 AND inserted_count >= 0 AND updated_count >= 0", name="wt_run_counts"
        ),
        Index("wt_runs_resource_date", "enterprise_id", "resource", "started_at"),
        Index(
            "wt_one_running_resource",
            "enterprise_id",
            "resource",
            unique=True,
            postgresql_where=text("status = 'em_curso'"),
            sqlite_where=text("status = 'em_curso'"),
        ),
    )


class WintouchSyncState(Base):
    __tablename__ = "wt_sync_state"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    enterprise_id: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(String(80))
    cursor: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[str | None] = mapped_column(ForeignKey("wt_sync_runs.id"))
    __table_args__ = (UniqueConstraint("enterprise_id", "resource"),)


class WintouchRecord:
    """Identidade externa, limitada ao esquema da empresa e à empresa na API."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    enterprise_id: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(200))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[str | None] = mapped_column(String(200))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sync_run_id: Mapped[str | None] = mapped_column(ForeignKey("wt_sync_runs.id"))

    @declared_attr.directive
    def __table_args__(cls):
        return (UniqueConstraint("enterprise_id", "external_id"),)


class WintouchCategory(WintouchRecord, Base):
    __tablename__ = "wt_categories"
    level: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    parent_external_id: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("enterprise_id", "level", "external_id"),
        CheckConstraint("level BETWEEN 1 AND 7", name="wt_category_level"),
    )


class WintouchProduct(WintouchRecord, Base):
    __tablename__ = "wt_products"
    code: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    category_id: Mapped[str | None] = mapped_column(ForeignKey("wt_categories.id"))
    unit: Mapped[str | None] = mapped_column(String(40))
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    last_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    batches_enabled: Mapped[bool | None] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("enterprise_id", "external_id"),
        Index("wt_products_code", "enterprise_id", "code"),
    )


class WintouchEntity(WintouchRecord, Base):
    __tablename__ = "wt_entities"
    code: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    tax_number: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(60))
    is_customer: Mapped[bool | None] = mapped_column(Boolean)
    is_supplier: Mapped[bool | None] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("enterprise_id", "external_id"),
        Index("wt_entities_tax_number", "enterprise_id", "tax_number"),
    )


class WintouchCashAccount(WintouchRecord, Base):
    __tablename__ = "wt_cash_accounts"
    code: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    currency: Mapped[str | None] = mapped_column(String(3))
    store_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id"))
    register_id: Mapped[str | None] = mapped_column(ForeignKey("registers.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WintouchWorkshift(WintouchRecord, Base):
    __tablename__ = "wt_workshifts"
    cash_account_id: Mapped[str] = mapped_column(ForeignKey("wt_cash_accounts.id"))
    operator_external_id: Mapped[str | None] = mapped_column(String(200))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opening_cents: Mapped[int | None] = mapped_column(BigInteger)
    expected_cents: Mapped[int | None] = mapped_column(BigInteger)
    counted_cents: Mapped[int | None] = mapped_column(BigInteger)
    local_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("cash_sessions.id"), unique=True
    )


class WintouchDocument(WintouchRecord, Base):
    __tablename__ = "wt_documents"
    resource: Mapped[str] = mapped_column(String(80))
    document_type: Mapped[str | None] = mapped_column(String(80))
    number: Mapped[str | None] = mapped_column(String(200))
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("wt_entities.id"))
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    net_cents: Mapped[int | None] = mapped_column(BigInteger)
    tax_cents: Mapped[int | None] = mapped_column(BigInteger)
    total_cents: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(String(3))
    source_status: Mapped[str | None] = mapped_column(String(80))
    cancelled: Mapped[bool | None] = mapped_column(Boolean)
    __table_args__ = (UniqueConstraint("enterprise_id", "resource", "external_id"),)


class WintouchDocumentLine(Base):
    __tablename__ = "wt_document_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("wt_documents.id"))
    external_id: Mapped[str] = mapped_column(String(200))
    product_id: Mapped[str | None] = mapped_column(ForeignKey("wt_products.id"))
    description: Mapped[str | None] = mapped_column(String(300))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    total_cents: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (UniqueConstraint("document_id", "external_id"),)


class WintouchPaymentMethod(WintouchRecord, Base):
    __tablename__ = "wt_payment_methods"
    name: Mapped[str] = mapped_column(String(200))
    local_payment: Mapped[str | None] = mapped_column(String(20))
    __table_args__ = (
        UniqueConstraint("enterprise_id", "external_id"),
        CheckConstraint(
            "local_payment IS NULL OR local_payment IN ('numerario','cartao','transferencia','outro')",
            name="wt_payment_mapping",
        ),
    )


class WintouchMovement(WintouchRecord, Base):
    __tablename__ = "wt_movements"
    resource: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str | None] = mapped_column(String(80))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("wt_documents.id"))
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("wt_entities.id"))
    cash_account_id: Mapped[str | None] = mapped_column(ForeignKey("wt_cash_accounts.id"))
    workshift_id: Mapped[str | None] = mapped_column(ForeignKey("wt_workshifts.id"))
    payment_method_id: Mapped[str | None] = mapped_column(ForeignKey("wt_payment_methods.id"))
    amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    direction: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str | None] = mapped_column(String(3))
    source_status: Mapped[str | None] = mapped_column(String(80))
    cancelled: Mapped[bool | None] = mapped_column(Boolean)
    local_movement_id: Mapped[str | None] = mapped_column(ForeignKey("movements.id"), unique=True)
    __table_args__ = (
        UniqueConstraint("enterprise_id", "resource", "external_id"),
        CheckConstraint("amount_cents IS NULL OR amount_cents >= 0", name="wt_movement_amount"),
        CheckConstraint("direction IS NULL OR direction IN (-1,1)", name="wt_movement_direction"),
        Index("wt_movements_date", "occurred_at"),
    )
