from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Strict):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class Name(Strict):
    name: str = Field(min_length=2, max_length=100)

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Introduza um nome válido.")
        return value


class RegisterCreate(Name):
    store_id: str


class PaymentMethodStatus(Strict):
    active: bool = Field(strict=True)


class BankAccountEdit(Name):
    bank: str = Field(min_length=2, max_length=100)
    iban_last4: str = Field(pattern=r"^(?:\d{4})?$")

    @field_validator("bank")
    @classmethod
    def meaningful_bank(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Introduza um banco válido.")
        return value


class BankAccountDetailsEdit(BankAccountEdit):
    store_id: str | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    iban: str = Field(default="", max_length=42)
    opening: str = Field(default="", max_length=24)
    opening_date: date | None = None
    principal: bool | None = None


class BankCreate(BankAccountEdit):
    store_id: str
    iban_last4: str = Field(pattern=r"^\d{4}$")


class BankInstitutionEdit(Name):
    active: bool = Field(strict=True)


class UserCreate(Name):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=12, max_length=200)
    role: Literal["administrador", "gestor", "operador", "consulta"]
    store_ids: list[str]


class OpenCash(Strict):
    register_id: str
    opening: str
    sheet_day: date | None = None
    include_wintouch: bool | None = None
    opening_wintouch_id: str | None = None


class CashSources(Strict):
    sheet_day: date
    include_wintouch: bool | None = None


class MovementCreate(Strict):
    session_id: str
    category_id: str
    kind: Literal[
        "venda",
        "cliente",
        "entrada_extra",
        "fornecedor",
        "despesa",
        "reembolso",
        "deposito",
        "reforco",
    ]
    payment: str = Field(min_length=1, max_length=20)
    terminal_id: str | None = Field(default=None, max_length=36)
    amount: str
    occurred_at: datetime
    entity: str = Field(default="", max_length=200)
    document: str = Field(default="", max_length=200)
    reference: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    request_key: str = Field(min_length=10, max_length=100)


class PlannedCreate(Strict):
    store_id: str
    category_id: str
    entity: str = Field(min_length=1, max_length=200)
    document: str = Field(min_length=1, max_length=200)
    due_date: date
    amount: str
    direction: Literal[-1, 1]

    @field_validator("entity", "document")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Preencha este campo.")
        return value.strip()


class PlannedPay(Strict):
    session_id: str
    amount: str
    payment: Literal["numerario", "cartao", "transferencia", "outro"]
    occurred_at: datetime
    request_key: str = Field(min_length=10, max_length=100)


class SalesImport(Strict):
    store_id: str
    start_date: date
    end_date: date
    confirm_demo: bool = False


class WintouchSettlement(Strict):
    document_type_id: str
    document_serie_id: str
    payment_method_id: str
    payment_number: str = Field(default="", max_length=100)
    amount: str
    occurred_at: datetime
    notes: str = Field(default="", max_length=2000)
    request_key: str = Field(min_length=36, max_length=36)


class Reason(Strict):
    reason: str = Field(min_length=5, max_length=2000)


class CashRealized(Strict):
    values: dict[str, str | None] = Field(default_factory=dict, max_length=50)
    debits: dict[str, str | None] | None = Field(default=None, max_length=50)
    credits: dict[str, str | None] | None = Field(default=None, max_length=50)
    notes: str = Field(default="", max_length=2000)
    pos_reference: str = Field(default="", max_length=200)


class CashCount(Strict):
    counts: dict[str, StrictInt] = Field(default_factory=dict, max_length=15)
    payments: dict[str, str] = Field(default_factory=dict, max_length=30)
    cash_total: str | None = None
    cash_mode: Literal["total", "denominations"] | None = None
    reason: str = Field(default="", max_length=2000)
    realized: CashRealized | None = None


class SupplierDebitAllocation(Strict):
    supplier_id: str = Field(min_length=1, max_length=100)
    amount_cents: StrictInt = Field(gt=0)
    payment: str | None = Field(default=None, min_length=1, max_length=20)


class CloseCash(CashCount):
    supplier_answer: Literal["indicado", "desconhecido", "nao_aplicavel"] | None = None
    supplier_name: str = Field(default="", max_length=300)
    supplier_id: str | None = Field(default=None, max_length=100)
    supplier_allocations: list[SupplierDebitAllocation] | None = Field(default=None, max_length=50)


class Handover(Reason):
    operator_id: str


class Transfer(Strict):
    source_id: str
    destination_id: str
    amount: str
    category_id: str
    reason: str = Field(min_length=5, max_length=2000)
    request_key: str = Field(min_length=10, max_length=100)


class PasswordChange(Strict):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=12, max_length=200)
