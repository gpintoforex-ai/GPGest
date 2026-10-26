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
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    __tablename__ = "registers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    name: Mapped[str] = mapped_column(String(100))
    settings: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    # Caixa inativa: mantém o histórico, mas não abre novas folhas de caixa.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class CompanyEmailSettings(Base):
    __tablename__ = "company_email_settings"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    settings: Mapped[str] = mapped_column(Text, default="{}")
    password_ciphertext: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailDispatch(Base):
    __tablename__ = "email_dispatches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(30), default="accounting")
    status: Mapped[str] = mapped_column(String(20), default="prepared", index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    store_ids: Mapped[str] = mapped_column(Text, default="[]")
    manifest: Mapped[str] = mapped_column(Text, default="{}")
    draft: Mapped[str] = mapped_column(Text, default="{}")
    payload_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    config_revision: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_key: Mapped[str] = mapped_column(String(100), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    receipt_note: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_of: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EmailDispatchPart(Base):
    __tablename__ = "email_dispatch_parts"
    __table_args__ = (UniqueConstraint("dispatch_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    dispatch_id: Mapped[str] = mapped_column(ForeignKey("email_dispatches.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="prepared")
    message_id: Mapped[str] = mapped_column(String(200))
    message_digest: Mapped[str] = mapped_column(String(64), default="")
    file_keys: Mapped[str] = mapped_column(Text)
    recipients: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")


class SaftImport(Base):
    __tablename__ = "saft_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="validating", index=True)
    header: Mapped[str] = mapped_column(Text, default="{}")
    summary: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    processing_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SaftImportItem(Base):
    __tablename__ = "saft_import_items"
    __table_args__ = (UniqueConstraint("import_id", "identity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    import_id: Mapped[str] = mapped_column(ForeignKey("saft_imports.id"), index=True)
    identity: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(25), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    base_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    base_store: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(20), default="pending")
    data: Mapped[str] = mapped_column(Text)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), default="", server_default="")
    phone: Mapped[str] = mapped_column(String(60), default="", server_default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmployeeStore(Base):
    __tablename__ = "employee_stores"
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), primary_key=True)


class BankAccount(Base):
    currency: Mapped[str] = mapped_column(String(3), default="EUR", server_default="EUR")
    __tablename__ = "bank_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    name: Mapped[str] = mapped_column(String(100))
    bank: Mapped[str] = mapped_column(String(100))
    iban_last4: Mapped[str] = mapped_column(String(4))
    iban_ciphertext: Mapped[str] = mapped_column(Text, default="", server_default="")
    settings: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")


class BankBookEntry(Base):
    __tablename__ = "bank_book_entries"
    __table_args__ = (CheckConstraint("amount_cents <> 0"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    movement_date: Mapped[str] = mapped_column(String(10), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(String(1000))
    reference: Mapped[str] = mapped_column(String(200), default="")
    request_key: Mapped[str] = mapped_column(String(150), unique=True)
    source_bank_id: Mapped[str | None] = mapped_column(ForeignKey("bank_transactions.id"), nullable=True, index=True)
    transfer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    original_id: Mapped[str | None] = mapped_column(ForeignKey("bank_book_entries.id"), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BankInstitution(Base):
    __tablename__ = "bank_institutions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BankTransaction(Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (UniqueConstraint("account_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"), index=True)
    movement_date: Mapped[str] = mapped_column(String(10))
    value_date: Mapped[str] = mapped_column(String(10))
    description: Mapped[str] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    balance_cents: Mapped[int] = mapped_column(BigInteger)
    fingerprint: Mapped[str] = mapped_column(String(64))
    source_account: Mapped[str] = mapped_column(String(100))
    exported_at: Mapped[str] = mapped_column(String(19))
    source_position: Mapped[int] = mapped_column(Integer)
    import_id: Mapped[str | None] = mapped_column(ForeignKey("document_imports.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Purchase(Base):
    __tablename__ = "purchases"
    __table_args__ = (UniqueConstraint("supplier_nif", "document_type", "document"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    supplier_nif: Mapped[str] = mapped_column(String(30))
    supplier_name: Mapped[str] = mapped_column(String(300))
    document: Mapped[str] = mapped_column(String(100))
    document_type: Mapped[str] = mapped_column(String(10), default="FT")
    document_date: Mapped[str] = mapped_column(String(10))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    tax_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    base_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    import_id: Mapped[str | None] = mapped_column(ForeignKey("document_imports.id"), nullable=True)
    origin: Mapped[str] = mapped_column(String(20), default="manual")
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    # Tipo de documento de compra quando a compra nasce de uma receção (rastreabilidade).
    document_type_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentImport(Base):
    __tablename__ = "document_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(30))
    # Ficheiro de origem no Google Drive (ID e versão), para detetar reimportações e alterações.
    drive_id: Mapped[str] = mapped_column(String(200), default="", server_default="")
    drive_version: Mapped[str] = mapped_column(String(200), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DocumentState(Base):
    __tablename__ = "document_states"
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), primary_key=True)
    availability: Mapped[str] = mapped_column(String(30), default="por_verificar")
    location: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    treatment: Mapped[str] = mapped_column(String(30), default="por_tratar")
    treatment_reason: Mapped[str] = mapped_column(Text, default="")
    accounting: Mapped[str] = mapped_column(String(30), default="por_preparar")
    accounting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accounting_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class ERPDocument(Base):
    __tablename__ = "erp_documents"
    __table_args__ = (UniqueConstraint("enterprise_id", "external_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    enterprise_id: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(200))
    supplier_nif: Mapped[str] = mapped_column(String(30))
    supplier_name: Mapped[str] = mapped_column(String(300))
    document: Mapped[str] = mapped_column(String(100))
    internal_document: Mapped[str] = mapped_column(String(200))
    document_type: Mapped[str] = mapped_column(String(10))
    document_date: Mapped[str] = mapped_column(String(10))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    base_cents: Mapped[int | None] = mapped_column(BigInteger)
    tax_cents: Mapped[int | None] = mapped_column(BigInteger)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ERPDocumentRubric(Base):
    """Rubrica atribuída localmente a uma compra do ERP (substitui a do fornecedor; pode repartir)."""
    __tablename__ = "erp_document_rubrics"
    __table_args__ = (UniqueConstraint("erp_document_id", "category_id"), CheckConstraint("amount_cents > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    erp_document_id: Mapped[str] = mapped_column(ForeignKey("erp_documents.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)


class DocumentERPLink(Base):
    __tablename__ = "document_erp_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    erp_id: Mapped[str] = mapped_column(ForeignKey("erp_documents.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index(
            "one_active_document_erp_link",
            "purchase_id",
            unique=True,
            postgresql_where=text("undone_at IS NULL"),
            sqlite_where=text("undone_at IS NULL"),
        ),
        Index(
            "one_active_erp_document_link",
            "erp_id",
            unique=True,
            postgresql_where=text("undone_at IS NULL"),
            sqlite_where=text("undone_at IS NULL"),
        ),
    )


class ExpenseDraft(Base):
    __tablename__ = "expense_drafts"
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="preparado")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentPayment(Base):
    __tablename__ = "document_payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    payment_date: Mapped[str] = mapped_column(String(10))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[int] = mapped_column(Integer)
    entity: Mapped[str] = mapped_column(String(300), default="")
    reference: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(30), default="pagamento")
    method: Mapped[str] = mapped_column(String(20), default="transferencia")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    source_id: Mapped[str | None] = mapped_column(String(36))
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("amount_cents > 0"),
        CheckConstraint("direction IN (-1,1)"),
        UniqueConstraint("source", "source_id"),
    )


class PaymentDocumentLine(Base):
    __tablename__ = "payment_document_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    payment_id: Mapped[str] = mapped_column(ForeignKey("document_payments.id"), index=True)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        UniqueConstraint("payment_id", "purchase_id"),
        CheckConstraint("amount_cents > 0"),
    )


class PurchaseAttachment(Base):
    __tablename__ = "purchase_attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)


class PurchaseAllocation(Base):
    __tablename__ = "purchase_allocations"
    __table_args__ = (
        UniqueConstraint("purchase_id", "transaction_id"),
        CheckConstraint("amount_cents > 0"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("bank_transactions.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    commission_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    difference_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReconciliationLine(Base):
    __tablename__ = "reconciliation_lines"
    __table_args__ = (
        UniqueConstraint("reconciliation_id", "source", "source_id"),
        CheckConstraint("amount_cents > 0"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    reconciliation_id: Mapped[str] = mapped_column(ForeignKey("reconciliations.id"), index=True)
    source: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)


class BillingConference(Base):
    """Conferência entre faturação (SAF-T) e recebimentos já existentes.

    Nunca cria movimentos. «individual» justifica valores de documentos com recebimentos
    concretos (inclui um movimento agregado para vários documentos e valores parciais);
    «agregada» confirma totais de um dia sem marcar cada documento como recebido.
    """

    __tablename__ = "billing_conferences"
    __table_args__ = (CheckConstraint("kind IN ('individual', 'agregada')"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    day: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    register_id: Mapped[str | None] = mapped_column(ForeignKey("registers.id"), nullable=True)
    payment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    billed_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    received_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    criteria: Mapped[str] = mapped_column(Text, default="[]")
    notes: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    undo_reason: Mapped[str] = mapped_column(Text, default="")


class BillingConferenceLine(Base):
    __tablename__ = "billing_conference_lines"
    __table_args__ = (
        UniqueConstraint("conference_id", "side", "source", "source_id"),
        CheckConstraint("amount_cents > 0"),
        CheckConstraint("side IN ('document', 'receipt')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    conference_id: Mapped[str] = mapped_column(ForeignKey("billing_conferences.id"), index=True)
    side: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)


class ReconciliationIgnore(Base):
    __tablename__ = "reconciliation_ignores"
    __table_args__ = (UniqueConstraint("source", "source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(Text)


class EfaturaConfig(Base):
    __tablename__ = "efatura_config"
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    nif: Mapped[str] = mapped_column(String(9))
    username: Mapped[str] = mapped_column(String(40))
    password_ciphertext: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(String(20), default="testes")


class Category(Base):
    cash_difference: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    __table_args__ = (Index("uq_category_cash_difference", "cash_difference", unique=True, postgresql_where=text("cash_difference = true")),)
    icon: Mapped[str] = mapped_column(String(40), default="tag", server_default="tag")
    color: Mapped[str] = mapped_column(String(7), default="#245fc5", server_default="#245fc5")
    __tablename__ = "categories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Percentagem estimada de IVA dedutível nas compras desta rubrica (vazio = 100%); só para o mapa indicativo.
    vat_deduction_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Rubrica inativa: mantém o histórico, mas não é proposta em novos registos.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class LocalPaymentMethod(Base):
    __tablename__ = "local_payment_methods"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    settings: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")


class CompanySetup(Base):
    __tablename__ = "company_setup"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    step: Mapped[str] = mapped_column(String(30), default="company")
    draft: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    settings: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    erp_test: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    erp_preview: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PaymentTerminal(Base):
    __tablename__ = "payment_terminals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    identifier: Mapped[str] = mapped_column(String(100), default="")
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    register_id: Mapped[str | None] = mapped_column(ForeignKey("registers.id"), nullable=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"))
    provider: Mapped[str] = mapped_column(String(100), default="")
    settlement_days: Mapped[int] = mapped_column(Integer, default=1)
    settlement_mode: Mapped[str] = mapped_column(String(10), default="gross")
    commission_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commission_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    statement_reference: Mapped[str] = mapped_column(String(200), default="")
    mbway: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("store_id", "name"),)


class PaymentMethodBankDefault(Base):
    __tablename__ = "payment_method_bank_defaults"
    method_id: Mapped[str] = mapped_column(ForeignKey("local_payment_methods.id"), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"))


class CashSession(Base):
    __tablename__ = "cash_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    register_id: Mapped[str] = mapped_column(ForeignKey("registers.id"))
    operator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    opening_cents: Mapped[int] = mapped_column(BigInteger)
    retained_fund_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    counted_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    close_reason: Mapped[str] = mapped_column(Text, default="")
    denominations: Mapped[str] = mapped_column(Text, default="{}")
    sheet_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    wintouch_scope: Mapped[str | None] = mapped_column(String(50), nullable=True, unique=True)
    wintouch_enterprise: Mapped[str | None] = mapped_column(String(100), nullable=True)
    count_data: Mapped[str] = mapped_column(Text, default="{}")
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
    session_id: Mapped[str | None] = mapped_column(ForeignKey("cash_sessions.id"), nullable=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    bank_account_id: Mapped[str | None] = mapped_column(ForeignKey("bank_accounts.id"), nullable=True)
    terminal_id: Mapped[str | None] = mapped_column(ForeignKey("payment_terminals.id"), nullable=True)
    payment_method_id: Mapped[str | None] = mapped_column(ForeignKey("local_payment_methods.id"), nullable=True)
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
    purchase_id: Mapped[str | None] = mapped_column(ForeignKey("purchases.id"), unique=True, nullable=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    entity: Mapped[str] = mapped_column(String(200))
    document: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[str] = mapped_column(String(10))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    direction: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlannedAllocation(Base):
    """Repartição por rubrica de um movimento previsto vindo de uma compra com linhas de várias rubricas.

    O pagamento continua a ser um só (o do previsto); a repartição serve a análise por rubrica.
    """
    __tablename__ = "planned_allocations"
    __table_args__ = (UniqueConstraint("planned_id", "category_id"), CheckConstraint("amount_cents > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    planned_id: Mapped[str] = mapped_column(ForeignKey("planned_movements.id"), index=True)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)


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


class LiquiditySettings(Base):
    __tablename__ = "liquidity_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    reserves: Mapped[str] = mapped_column(Text, default='{"EUR":0}')
    stale_days: Mapped[int] = mapped_column(Integer, default=7)


class LiquidityManual(Base):
    __tablename__ = "liquidity_manual"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    data: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LiquidityAdjustment(Base):
    __tablename__ = "liquidity_adjustments"
    source_key: Mapped[str] = mapped_column(String(180), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    data: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LiquidityScenario(Base):
    __tablename__ = "liquidity_scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LiquiditySettlement(Base):
    __tablename__ = "liquidity_settlements"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_key: Mapped[str] = mapped_column(String(180), index=True)
    target: Mapped[str] = mapped_column(String(80))
    payment_id: Mapped[str] = mapped_column(ForeignKey("document_payments.id"), unique=True)
    movement_id: Mapped[str | None] = mapped_column(ForeignKey("movements.id"), nullable=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    origin_data: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IntegrationBinding(Base):
    __tablename__ = "integration_bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(String(36))
    connector: Mapped[str] = mapped_column(String(40))
    external_kind: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    internal_kind: Mapped[str] = mapped_column(String(40))
    internal_id: Mapped[str] = mapped_column(String(36))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="confirmed")
    local_data: Mapped[str] = mapped_column(Text, default="{}")
    __table_args__ = (UniqueConstraint("company_id", "connector", "external_kind", "external_id"), UniqueConstraint("company_id", "connector", "internal_kind", "internal_id"))


class AccountBalanceReference(Base):
    __tablename__ = "account_balance_references"
    target: Mapped[str] = mapped_column(String(80), primary_key=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    fund_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    reference_date: Mapped[str] = mapped_column(String(10))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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
    # Progresso das sincronizações de tabelas (executadas em segundo plano).
    total_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    done_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    current_label: Mapped[str] = mapped_column(String(300), default="", server_default="")
    results: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Morada principal (EntityAddresses com IsMainAddress), onde a Wintouch guarda também email e telefone.
    address: Mapped[str | None] = mapped_column(String(500))
    locality: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    is_customer: Mapped[bool | None] = mapped_column(Boolean)
    is_supplier: Mapped[bool | None] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Condições de pagamento na Wintouch (IDs externos; nomes em wt_payment_terms/methods).
    payment_term_external_id: Mapped[str | None] = mapped_column(String(200))
    payment_method_external_id: Mapped[str | None] = mapped_column(String(200))
    supplier_payment_term_external_id: Mapped[str | None] = mapped_column(String(200))
    supplier_payment_method_external_id: Mapped[str | None] = mapped_column(String(200))
    # Eliminada na Wintouch a partir da IAZELA: o espelho fica para os documentos já importados.
    erp_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class WintouchSaleRecord(WintouchRecord, Base):
    __tablename__ = "wt_sale_records"
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    document_date: Mapped[str] = mapped_column(String(10), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[str] = mapped_column(Text)


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
            "local_payment IS NULL OR local_payment IN ('numerario','cartao','mbway','transferencia','outro') OR local_payment LIKE 'pm_%'",
            name="wt_payment_mapping",
        ),
    )


class WintouchPaymentTerm(WintouchRecord, Base):
    """Prazos de pagamento da Wintouch; `days` fica vazio em prazos como «Fim do mês»."""

    __tablename__ = "wt_payment_terms"
    code: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    days: Mapped[int | None] = mapped_column(Integer)
    term_type: Mapped[str | None] = mapped_column(String(20))


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


class BusinessEntity(Base):
    __tablename__ = "business_entities"
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    removed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    wintouch_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    tax_number: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(String(500), default="")
    locality: Mapped[str] = mapped_column(String(120), default="")
    postal_code: Mapped[str] = mapped_column(String(20), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(60), default="")
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Em entidades do ERP: Cliente/Fornecedor/Ativo definidos na IAZELA em vez de seguirem a Wintouch.
    flags_local: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Em entidades do ERP: morada e contactos alterados na IAZELA, ainda por enviar.
    contact_local: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Prazo de pagamento habitual (dias após a data do documento), usado nas propostas de lançamento.
    payment_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_term_id: Mapped[str | None] = mapped_column(
        ForeignKey("payment_terms.id"), nullable=True
    )
    # Meio de pagamento habitual (identificador da tabela «Meios de pagamento»: base ou pm_…).
    payment_method_id: Mapped[str | None] = mapped_column(String(20), nullable=True)


class PaymentTerm(Base):
    """Tabela de prazos de pagamento: criados na IAZELA ou importados da Wintouch."""

    __tablename__ = "payment_terms"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # apos (N dias após o documento), fim_mes, fim_mes_seguinte ou outro (tipo Wintouch não mapeado).
    term_type: Mapped[str] = mapped_column(String(20), default="apos")
    days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Desconto em centésimas de ponto percentual (250 = 2,5 %).
    discount_basis_points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class PurchaseDocumentType(Base):
    """Tipo de documento de compra (FC, FCD, NCC, NCCD…): efeito financeiro das receções."""

    __tablename__ = "purchase_document_types"
    __table_args__ = (
        CheckConstraint("nature IN ('compra', 'nota_credito_compra')", name="purchase_doc_type_nature"),
        CheckConstraint("sign IN (-1, 1)", name="purchase_doc_type_sign"),
        CheckConstraint("payment_condition IN ('dinheiro', 'credito')", name="purchase_doc_type_condition"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    description: Mapped[str] = mapped_column(String(100))
    nature: Mapped[str] = mapped_column(String(30))
    sign: Mapped[int] = mapped_column(Integer)
    payment_condition: Mapped[str] = mapped_column(String(10))
    default_payment_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    creates_cash_movement: Mapped[bool] = mapped_column(Boolean, default=False)
    creates_debt: Mapped[bool] = mapped_column(Boolean, default=False)
    default_due_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    series: Mapped[str] = mapped_column(String(40), default="", server_default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AttachmentIntegrationConfig(Base):
    __tablename__ = "attachment_integration_config"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    outlook_email: Mapped[str] = mapped_column(String(200), default="")
    google_email: Mapped[str] = mapped_column(String(200), default="")
    drive_folder: Mapped[str] = mapped_column(String(300), default="")
    mail_folder: Mapped[str] = mapped_column(String(100), default="inbox")


class DocumentScanSettings(Base):
    __tablename__ = "document_scan_settings"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    max_pages: Mapped[int] = mapped_column(Integer, default=20)
    max_bytes: Mapped[int] = mapped_column(Integer, default=5 * 1024 * 1024)
    organisation: Mapped[str] = mapped_column(String(30), default="company_period_type")


class DocumentScan(Base):
    __tablename__ = "document_scans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    purchase_id: Mapped[str | None] = mapped_column(ForeignKey("purchases.id"), nullable=True)
    movement_id: Mapped[str | None] = mapped_column(ForeignKey("movements.id"), nullable=True)
    attachment_id: Mapped[str | None] = mapped_column(ForeignKey("drive_document_attachments.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(40), default="google_drive")
    account: Mapped[str] = mapped_column(String(200), default="")
    root_folder: Mapped[str] = mapped_column(String(200), default="")
    drive_folder: Mapped[str] = mapped_column(String(200), default="")
    drive_id: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100), default="application/pdf")
    digest: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(Integer)
    pages: Mapped[int] = mapped_column(Integer)
    document_date: Mapped[str] = mapped_column(String(10), index=True)
    data: Mapped[str] = mapped_column(Text)
    pdf_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pdf_generated")
    validation_status: Mapped[str] = mapped_column(String(30), default="por_validar")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # «digitalizacao» (área autenticada) ou «digitalizacao_rapida» (acesso por PIN).
    origin: Mapped[str] = mapped_column(String(40), default="digitalizacao", server_default="digitalizacao")
    quick_access_id: Mapped[str | None] = mapped_column(ForeignKey("quick_scan_accesses.id"), nullable=True, index=True)


class QuickScanAccess(Base):
    """Acesso de digitalização rápida: um dispositivo emparelhado, uma loja, uma pasta cloud e um PIN."""
    __tablename__ = "quick_scan_accesses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"))
    folder_id: Mapped[str] = mapped_column(String(200))
    folder_name: Mapped[str] = mapped_column(String(300), default="")
    pin_hash: Mapped[str] = mapped_column(String(300))
    pairing_hash: Mapped[str] = mapped_column(String(64), default="")
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuickScanSession(Base):
    """Sessão curta, só de digitalização; o token fica apenas em memória no dispositivo."""
    __tablename__ = "quick_scan_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    access_id: Mapped[str] = mapped_column(ForeignKey("quick_scan_accesses.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ScanCloudFolder(Base):
    __tablename__ = "scan_cloud_folders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    drive_id: Mapped[str] = mapped_column(String(200))
    __table_args__ = (UniqueConstraint("account", "parent_id", "name"),)


class BankDeposit(Base):
    __tablename__ = "bank_deposits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    register_id: Mapped[str] = mapped_column(ForeignKey("registers.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("bank_accounts.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    preparation_date: Mapped[str] = mapped_column(String(10))
    exit_date: Mapped[str | None] = mapped_column(String(10))
    delivery_date: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(30), default="rascunho")
    cash_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    cheque_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    details: Mapped[str] = mapped_column(Text, default="{}")
    reference: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    movement_id: Mapped[str | None] = mapped_column(ForeignKey("movements.id"))
    reversal_id: Mapped[str | None] = mapped_column(ForeignKey("movements.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DepositCheque(Base):
    __tablename__ = "deposit_cheques"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    deposit_id: Mapped[str] = mapped_column(ForeignKey("bank_deposits.id"), index=True)
    identity: Mapped[str] = mapped_column(String(300))
    number: Mapped[str] = mapped_column(String(100))
    bank: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    cheque_date: Mapped[str] = mapped_column(String(10))
    issuer: Mapped[str] = mapped_column(String(200), default="")
    receipt_id: Mapped[str | None] = mapped_column(ForeignKey("document_payments.id"))
    source_receipt: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("deposit_cheque_active_identity", "identity", unique=True, postgresql_where=text("active"), sqlite_where=text("active")),
        Index("deposit_cheque_active_receipt", "receipt_id", unique=True, postgresql_where=text("active AND receipt_id IS NOT NULL"), sqlite_where=text("active AND receipt_id IS NOT NULL")),
        Index("deposit_cheque_active_source", "source_receipt", unique=True, postgresql_where=text("active AND source_receipt IS NOT NULL"), sqlite_where=text("active AND source_receipt IS NOT NULL")),
    )


class DepositCredit(Base):
    __tablename__ = "deposit_credits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    deposit_id: Mapped[str] = mapped_column(ForeignKey("bank_deposits.id"), index=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("bank_transactions.id"), index=True)
    cheque_id: Mapped[str | None] = mapped_column(ForeignKey("deposit_cheques.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    kind: Mapped[str] = mapped_column(String(20), default="credito")
    request_key: Mapped[str] = mapped_column(String(100), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DepositAttachment(Base):
    __tablename__ = "deposit_attachments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    deposit_id: Mapped[str] = mapped_column(ForeignKey("bank_deposits.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    validated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CategoryIntegrationLink(Base):
    __tablename__ = "category_integration_links"
    __table_args__ = (UniqueConstraint("provider", "enterprise_id", "category_id"), UniqueConstraint("provider", "enterprise_id", "external_id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    provider: Mapped[str] = mapped_column(String(40))
    enterprise_id: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(200))
    external_code: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    payload: Mapped[str] = mapped_column(Text, default="{}")


class AccountingPeriod(Base):
    __tablename__ = "accounting_periods"
    __table_args__ = (UniqueConstraint("vat_period", "year", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    vat_period: Mapped[str] = mapped_column(String(20))
    year: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    start: Mapped[str] = mapped_column(String(10))
    end: Mapped[str] = mapped_column(String(10))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")


class CompanyFiscalSettings(Base):
    __tablename__ = "company_fiscal_settings"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    vat_period: Mapped[str] = mapped_column(String(20), default="", server_default="")
    # normal, isento_53 ou pequenos_retalhistas; vazio enquanto não for configurado.
    vat_regime: Mapped[str] = mapped_column(String(30), default="", server_default="")


class GoogleConnection(Base):
    __tablename__ = "google_connections"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    token_ciphertext: Mapped[str] = mapped_column(Text, default="")
    state_hash: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(200), default="")


class GoogleInvoiceFile(Base):
    __tablename__ = "google_invoice_files"
    __table_args__ = (UniqueConstraint("account", "message_id", "part_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account: Mapped[str] = mapped_column(String(200))
    message_id: Mapped[str] = mapped_column(String(200))
    part_id: Mapped[str] = mapped_column(String(200))
    attachment_key: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(200))
    sender: Mapped[str] = mapped_column(String(500), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    received_at: Mapped[str] = mapped_column(String(40), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(100), default="")
    digest: Mapped[str] = mapped_column(String(64), default="", index=True)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    drive_id: Mapped[str] = mapped_column(String(200), default="")
    drive_folder: Mapped[str] = mapped_column(String(200), default="")
    uploaded: Mapped[bool] = mapped_column(Boolean, default=False)
    attachment_id: Mapped[str | None] = mapped_column(
        ForeignKey("purchase_attachments.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DriveDocumentAttachment(Base):
    __tablename__ = "drive_document_attachments"
    __table_args__ = (UniqueConstraint("account", "drive_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), index=True)
    account: Mapped[str] = mapped_column(String(200))
    drive_id: Mapped[str] = mapped_column(String(200))
    drive_folder: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InvoiceWorkflowSettings(Base):
    __tablename__ = "invoice_workflow_settings"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    folders: Mapped[str] = mapped_column(Text, default="[]")
    # Pastas dos ficheiros exportados do e-Fatura (distintas das pastas de anexos).
    efatura_folders: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    include_subfolders: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Etapa e período do assistente por utilizador, para retomar o trabalho.
    assistant_state: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    tolerance_cents: Mapped[int] = mapped_column(Integer, default=2)
    ocr_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=0)
    window_days: Mapped[int] = mapped_column(Integer, default=90)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    demo_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class InvoiceWorkflowCheck(Base):
    __tablename__ = "invoice_workflow_checks"
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), primary_key=True)
    erp_state: Mapped[str] = mapped_column(String(30), default="unknown")
    drive_state: Mapped[str] = mapped_column(String(30), default="unknown")
    erp_candidates: Mapped[str] = mapped_column(Text, default="[]")
    drive_candidates: Mapped[str] = mapped_column(Text, default="[]")
    messages: Mapped[str] = mapped_column(Text, default="[]")
    supplier: Mapped[str] = mapped_column(Text, default="{}")
    proposal: Mapped[str] = mapped_column(Text, default="{}")
    # Anexos propostos e rejeitados pelo utilizador (não voltam a ser sugeridos).
    rejected: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    proposal_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proposal_reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvoiceEvidenceCache(Base):
    __tablename__ = "invoice_evidence_cache"
    __table_args__ = (UniqueConstraint("account", "drive_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    account: Mapped[str] = mapped_column(String(200))
    drive_id: Mapped[str] = mapped_column(String(200))
    folder: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(500))
    digest: Mapped[str] = mapped_column(String(64), default="")
    evidence: Mapped[str] = mapped_column(Text, default="{}")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InvoiceEvidenceValidation(Base):
    __tablename__ = "invoice_evidence_validations"
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), primary_key=True)
    attachment_key: Mapped[str] = mapped_column(String(240), primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InvoiceWorkflowRun(Base):
    __tablename__ = "invoice_workflow_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    stores: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="em_curso")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    detail: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StockArticle(Base):
    __tablename__ = "stock_articles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    erp_id: Mapped[str | None] = mapped_column(ForeignKey("wt_products.id"), unique=True, nullable=True)
    data: Mapped[str] = mapped_column(Text, default="{}")
    revision: Mapped[int] = mapped_column(default=0)


class PurchaseArticleAlias(Base):
    """Correspondência aprendida ao receber uma fatura: referência/EAN/descrição do fornecedor → artigo."""

    __tablename__ = "purchase_article_aliases"
    __table_args__ = (
        UniqueConstraint("supplier_key", "kind", "key", name="purchase_alias_unique"),
        CheckConstraint("kind IN ('ref', 'ean', 'desc')", name="purchase_alias_kind"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    # Fornecedor (vazio nos códigos de barras, que valem para qualquer fornecedor).
    supplier_key: Mapped[str] = mapped_column(String(36), default="", server_default="")
    kind: Mapped[str] = mapped_column(String(10))
    key: Mapped[str] = mapped_column(String(300))
    article_id: Mapped[str] = mapped_column(ForeignKey("stock_articles.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StockWarehouse(Base):
    __tablename__ = "stock_warehouses"
    __table_args__ = (UniqueConstraint("store_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    # Código opcional, único por empresa; liga o armazém ao ERP sem depender do nome.
    code: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class StockLot(Base):
    __tablename__ = "stock_lots"
    __table_args__ = (UniqueConstraint("article_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    article_id: Mapped[str] = mapped_column(ForeignKey("stock_articles.id"), index=True)
    number: Mapped[str] = mapped_column(String(100))
    expiry: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    data: Mapped[str] = mapped_column(Text, default="{}")


class StockEntry(Base):
    __tablename__ = "stock_entries"
    __table_args__ = (UniqueConstraint("request_key", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    article_id: Mapped[str] = mapped_column(ForeignKey("stock_articles.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("stock_warehouses.id"), index=True)
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("stock_lots.id"), nullable=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(24, 8), nullable=True)
    net_sales: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    direct_cost: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    occurred_on: Mapped[str] = mapped_column(String(10), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    request_key: Mapped[str] = mapped_column(String(200), index=True)
    sequence: Mapped[int] = mapped_column(default=0)
    original_id: Mapped[str | None] = mapped_column(ForeignKey("stock_entries.id"), nullable=True)
    data: Mapped[str] = mapped_column(Text, default="{}")


class StockInventory(Base):
    __tablename__ = "stock_inventories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("stock_warehouses.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(default=0)
    data: Mapped[str] = mapped_column(Text, default="[]")


class ProcurementDocument(Base):
    __tablename__ = "procurement_documents"
    __table_args__ = (UniqueConstraint("request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("business_entities.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    purchase_id: Mapped[str | None] = mapped_column(ForeignKey("purchases.id"), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    request_key: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data: Mapped[str] = mapped_column(Text, default="{}")


class CustomerOrder(Base):
    __tablename__ = "customer_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(ForeignKey("business_entities.id"), nullable=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revision: Mapped[int] = mapped_column(default=0)
    data: Mapped[str] = mapped_column(Text, default="{}")


class CustomerOrderLine(Base):
    __tablename__ = "customer_order_lines"
    __table_args__ = (CheckConstraint("quantity > 0 AND cancelled >= 0 AND delivered >= 0 AND cancelled + delivered <= quantity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("customer_orders.id"), index=True)
    article_id: Mapped[str | None] = mapped_column(ForeignKey("stock_articles.id"), nullable=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("business_entities.id"), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    cancelled: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    delivered: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    data: Mapped[str] = mapped_column(Text, default="{}")


class CustomerOrderReservation(Base):
    __tablename__ = "customer_order_reservations"
    __table_args__ = (CheckConstraint("quantity >= 0"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    line_id: Mapped[str] = mapped_column(ForeignKey("customer_order_lines.id"), index=True)
    warehouse_id: Mapped[str] = mapped_column(ForeignKey("stock_warehouses.id"), index=True)
    lot_id: Mapped[str | None] = mapped_column(ForeignKey("stock_lots.id"), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))


class SupplierOrderTerms(Base):
    __tablename__ = "supplier_order_terms"
    supplier_id: Mapped[str] = mapped_column(ForeignKey("business_entities.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(default=0)
    data: Mapped[str] = mapped_column(Text, default="{}")


class CustomerPurchaseSimulation(Base):
    __tablename__ = "customer_purchase_simulations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("business_entities.id"))
    procurement_id: Mapped[str | None] = mapped_column(ForeignKey("procurement_documents.id"), unique=True, nullable=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revision: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    data: Mapped[str] = mapped_column(Text, default="{}")


class CustomerSupplyLink(Base):
    __tablename__ = "customer_supply_links"
    __table_args__ = (UniqueConstraint("simulation_id", "line_id"), CheckConstraint("quantity > 0 AND released >= 0 AND received >= 0 AND confirmed >= 0 AND received + released <= quantity AND confirmed <= quantity"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    simulation_id: Mapped[str] = mapped_column(ForeignKey("customer_purchase_simulations.id"), index=True)
    simulation_line: Mapped[int] = mapped_column(Integer)
    line_id: Mapped[str] = mapped_column(ForeignKey("customer_order_lines.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    released: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    confirmed: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)
    received: Mapped[Decimal] = mapped_column(Numeric(24, 6), default=0)


class CustomerOrderEvent(Base):
    __tablename__ = "customer_order_events"
    __table_args__ = (UniqueConstraint("request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("customer_orders.id"), nullable=True, index=True)
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    request_key: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    dispatch_id: Mapped[str | None] = mapped_column(ForeignKey("email_dispatches.id"), nullable=True)
    data: Mapped[str] = mapped_column(Text, default="{}")


class OrderReplenishmentPolicy(Base):
    __tablename__ = "order_replenishment_policies"
    __table_args__ = (UniqueConstraint("store_id", "article_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    article_id: Mapped[str] = mapped_column(ForeignKey("stock_articles.id"), index=True)
    revision: Mapped[int] = mapped_column(default=0)
    data: Mapped[str] = mapped_column(Text, default="{}")


class OrderDocumentReview(Base):
    __tablename__ = "order_document_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("archive_documents.id"), unique=True)
    simulation_id: Mapped[str] = mapped_column(ForeignKey("customer_purchase_simulations.id"), index=True)
    receipt_id: Mapped[str | None] = mapped_column(ForeignKey("procurement_documents.id"), nullable=True)
    procurement_id: Mapped[str | None] = mapped_column(ForeignKey("procurement_documents.id"), unique=True, nullable=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    data: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    drive_id: Mapped[str] = mapped_column(String(300), default="")
    drive_folder: Mapped[str] = mapped_column(String(300), default="")
    account: Mapped[str] = mapped_column(String(200), default="")
    cloud_status: Mapped[str] = mapped_column(String(30), default="pending")
    cloud_error: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(300), default="")
    mime: Mapped[str] = mapped_column(String(100), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    digest: Mapped[str] = mapped_column(String(64), default="")


class OrderCycleSettings(Base):
    __tablename__ = "order_cycle_settings"
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(default=0)
    data: Mapped[str] = mapped_column(Text, default="{}")


class ProcurementLink(Base):
    __tablename__ = "procurement_links"
    __table_args__ = (UniqueConstraint("document_id", "line_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("procurement_documents.id"), index=True)
    line_index: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str] = mapped_column(ForeignKey("procurement_documents.id"), index=True)
    source_line: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6))


class StockSettings(Base):
    __tablename__ = "stock_settings"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    revision: Mapped[int] = mapped_column(default=0)
    data: Mapped[str] = mapped_column(Text, default="{}")


class StockImport(Base):
    __tablename__ = "stock_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    status: Mapped[str] = mapped_column(String(20), default="preview")
    data: Mapped[str] = mapped_column(Text)


class ArchiveDocument(Base):
    """Índice central do arquivo digital: um registo por ficheiro, sem substituir o original.

    O original fica onde já estava (Drive, anexos de compras, movimentos, depósitos, Gmail) ou,
    nos carregamentos diretos, cifrado em ``archive_blobs``. Os valores são sempre em cêntimos.
    """
    __tablename__ = "archive_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    store_id: Mapped[str | None] = mapped_column(ForeignKey("stores.id"), nullable=True, index=True)
    source_key: Mapped[str] = mapped_column(String(300), unique=True)
    origin: Mapped[str] = mapped_column(String(40), index=True)
    doc_type: Mapped[str] = mapped_column(String(40), default="por_classificar", index=True)
    category_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    original_name: Mapped[str] = mapped_column(String(300), default="")
    mime: Mapped[str] = mapped_column(String(100), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    storage: Mapped[str] = mapped_column(String(30))
    storage_ref: Mapped[str] = mapped_column(String(300))
    storage_account: Mapped[str] = mapped_column(String(200), default="")
    storage_folder: Mapped[str] = mapped_column(String(300), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    duplicate_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    issue_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    entity_nif: Mapped[str] = mapped_column(String(30), default="", index=True)
    entity_name: Mapped[str] = mapped_column(String(300), default="")
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("business_entities.id"), nullable=True)
    document_code: Mapped[str] = mapped_column(String(10), default="")
    document_number: Mapped[str] = mapped_column(String(120), default="")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    base_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tax_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payment_reference: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(String(500), default="")
    # pendente → processado | erro | duplicado; revisão humana separada do processamento.
    processing_status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    review_status: Mapped[str] = mapped_column(String(20), default="por_rever", index=True)
    extraction: Mapped[str] = mapped_column(Text, default="{}")
    corrections: Mapped[str] = mapped_column(Text, default="[]")
    retention_until: Mapped[str | None] = mapped_column(String(10), nullable=True)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    integrity_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    integrity_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ArchiveBlob(Base):
    """Original carregado diretamente no arquivo, cifrado em repouso."""
    __tablename__ = "archive_blobs"
    document_id: Mapped[str] = mapped_column(ForeignKey("archive_documents.id"), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)


class ArchiveLink(Base):
    """Relação documento ↔ registo (compra/e-Fatura, ERP, banco, pagamento, caixa, depósito)."""
    __tablename__ = "archive_links"
    __table_args__ = (UniqueConstraint("document_id", "target_kind", "target_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("archive_documents.id"), index=True)
    target_kind: Mapped[str] = mapped_column(String(30), index=True)
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    # Valor imputado: pagamentos parciais ou agrupados; vazio quando é o documento inteiro.
    amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    strength: Mapped[str] = mapped_column(String(20), default="possivel")
    status: Mapped[str] = mapped_column(String(20), default="proposta", index=True)
    method: Mapped[str] = mapped_column(String(60), default="")
    reasons: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ArchiveEvent(Base):
    """Histórico de alterações e acessos a cada documento do arquivo."""
    __tablename__ = "archive_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ArchiveJob(Base):
    """Fila de processamento assíncrono (extração e relações), com tentativas e recuperação."""
    __tablename__ = "archive_jobs"
    __table_args__ = (UniqueConstraint("document_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("archive_documents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pendente", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ArchiveSettings(Base):
    __tablename__ = "archive_settings"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    retention_years: Mapped[int] = mapped_column(Integer, default=10)
    ocr_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_process: Mapped[bool] = mapped_column(Boolean, default=True)
    include_drive: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_confirm_strong: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ErpLink(Base):
    """Ligação estável entre um registo de uma tabela da IAZELA e o registo no ERP.

    Isolada por empresa (esquema), ERP e empresa na API. `base_values` guarda os valores
    acordados na última sincronização confirmada, para detetar alterações de cada lado.
    """

    __tablename__ = "erp_links"
    __table_args__ = (
        UniqueConstraint("provider", "enterprise_id", "table_key", "local_id"),
        UniqueConstraint("provider", "enterprise_id", "table_key", "external_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    table_key: Mapped[str] = mapped_column(String(40))
    local_id: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(40))
    enterprise_id: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(200))
    external_code: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # Onde o registo nasceu: local (IAZELA) ou erp.
    origin: Mapped[str] = mapped_column(String(10), default="local", server_default="local")
    # sincronizado, conflito, erro ou a_enviar (escrita no ERP por confirmar).
    state: Mapped[str] = mapped_column(String(20), default="sincronizado", server_default="sincronizado")
    base_values: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    remote_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", server_default="")
    error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Escrita em curso ou incerta: {"kind", "payload", "state"}; vazio quando não há nenhuma.
    operation: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class TableSyncSetting(Base):
    """Regra de prioridade em conflitos de sincronização, por tabela."""

    __tablename__ = "table_sync_settings"
    table_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    # perguntar, erp ou local.
    conflict_policy: Mapped[str] = mapped_column(String(20), default="perguntar", server_default="perguntar")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
