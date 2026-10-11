from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class BankCreate(RegisterCreate):
    bank: str = Field(min_length=2, max_length=100)
    iban_last4: str = Field(pattern=r"^\d{4}$")


class UserCreate(Name):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=12, max_length=200)
    role: Literal["administrador", "gestor", "operador", "consulta"]
    store_ids: list[str]


class OpenCash(Strict):
    register_id: str
    opening: str


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
    payment: Literal["numerario", "cartao", "transferencia", "outro"]
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


class Reason(Strict):
    reason: str = Field(min_length=5, max_length=2000)


class CloseCash(Strict):
    counts: dict[str, int]
    reason: str = Field(default="", max_length=2000)


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
