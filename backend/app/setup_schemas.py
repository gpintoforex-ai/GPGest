"""Validated, versioned input for the company configuration assistant."""
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from .schemas import Strict

STEPS = ("company", "mode", "registers", "banks", "payments", "terminals", "preferences", "saft", "summary")


class CompanyData(Strict):
    name: str = Field(default="", max_length=100)
    trade_name: str = Field(default="", max_length=100)
    nif: str = Field(default="", max_length=9)
    country: str = Field(default="PT", pattern=r"^[A-Z]{2}$")
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    timezone: str = Field(default="Europe/Lisbon", max_length=100)
    store_id: str = Field(default="primary", max_length=64)
    store_name: str = Field(default="Loja principal", max_length=100)
    vat_regime: Literal["", "normal", "isento_53", "pequenos_retalhistas"] = ""
    vat_period: Literal["", "mensal", "trimestral"] = ""
    # Proposta de armazém de stock no estabelecimento principal (não duplica um existente).
    create_warehouse: bool = True
    warehouse_name: str = Field(default="Armazém principal", max_length=100)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Escolha um fuso horário válido.") from None
        return value


class ModeData(Strict):
    mode: Literal["local", "integrated"] = "local"
    provider: str = Field(default="wintouch", max_length=40)
    api_url: str = Field(default="https://api.wintouchcloud.com/api/v1", max_length=300)
    enterprise: str = Field(default="", max_length=100)
    api_key: str = Field(default="", max_length=4096)
    resources: list[Literal["products", "entities", "cash_accounts", "sales", "purchase_documents", "rubrics"]] = Field(default_factory=lambda: ["cash_accounts", "entities", "products"], max_length=6)
    defer: bool = False


class RegisterData(Strict):
    key: str = Field(min_length=1, max_length=64)
    id: str | None = Field(default=None, max_length=36)
    name: str = Field(default="", max_length=100)
    store_id: str = Field(default="primary", max_length=64)
    responsible: str = Field(default="", max_length=80)
    start_date: date | None = None
    fund: str = Field(default="0,00", max_length=24)
    opening: str | None = Field(default=None, max_length=24)
    count_required: bool = True
    balance_mode: Literal["fixed", "carry"] = "fixed"
    erp_id: str | None = Field(default=None, max_length=200)


class AccountData(Strict):
    key: str = Field(min_length=1, max_length=64)
    id: str | None = Field(default=None, max_length=36)
    bank_id: str | None = Field(default=None, max_length=36)
    bank: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=100)
    store_id: str = Field(default="primary", max_length=64)
    iban: str = Field(default="", max_length=42)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    opening: str | None = Field(default=None, max_length=24)
    opening_date: date | None = None
    principal: bool = False


class PaymentData(Strict):
    key: str = Field(min_length=1, max_length=64)
    id: str | None = Field(default=None, max_length=20)
    name: str = Field(default="", max_length=100)
    selected: bool = True
    cash: bool = False
    bank_receivable: bool = False
    account: str | None = Field(default=None, max_length=64)
    receipts: bool = True
    payments: bool = True
    erp_id: str | None = Field(default=None, max_length=36)
    mbway_via: Literal["terminal", "account"] = "account"


class TerminalData(Strict):
    key: str = Field(min_length=1, max_length=64)
    id: str | None = Field(default=None, max_length=36)
    name: str = Field(default="", max_length=100)
    identifier: str = Field(default="", max_length=100)
    store_id: str = Field(default="primary", max_length=64)
    register_id: str | None = Field(default=None, max_length=64)
    provider: str = Field(default="", max_length=100)
    account: str = Field(default="", max_length=64)
    settlement_days: int = Field(default=1, ge=0, le=90)
    settlement_mode: Literal["gross", "net"] = "gross"
    commission_percent: str | None = Field(default=None, max_length=12)
    commission_fixed: str | None = Field(default=None, max_length=24)
    statement_reference: str = Field(default="", max_length=200)
    mbway: bool = False


class Preferences(Strict):
    daily_close: bool = True
    manual_realized: bool = True
    require_reason: bool = True
    unclosed_warning: bool = True
    categories: list[str] = Field(default_factory=lambda: ["Vendas", "Clientes", "Fornecedores", "Despesas correntes"], max_length=50)
    archive: Literal["later", "existing", "drive"] = "later"
    # Impedir (predefinição) ou apenas avisar ao confirmar SAF-T com códigos de pagamento por associar.
    saft_block_unmapped: bool = True


class SetupData(Strict):
    company: CompanyData = Field(default_factory=CompanyData)
    mode: ModeData = Field(default_factory=ModeData)
    registers: list[RegisterData] = Field(default_factory=list, max_length=100)
    accounts: list[AccountData] = Field(default_factory=list, max_length=100)
    payments: list[PaymentData] = Field(default_factory=list, max_length=50)
    terminals: list[TerminalData] = Field(default_factory=list, max_length=100)
    preferences: Preferences = Field(default_factory=Preferences)
    skipped: list[Literal["banks", "terminals", "saft", "preferences"]] = Field(default_factory=list, max_length=4)


class DraftSave(Strict):
    revision: int = Field(ge=0)
    step: Literal["company", "mode", "registers", "banks", "payments", "terminals", "preferences", "saft", "summary"]
    data: SetupData


class Revision(Strict):
    revision: int = Field(ge=0)


class Finish(Revision):
    token: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]


class ImportPreview(Revision):
    resources: list[Literal["products", "entities", "cash_accounts", "payment_methods"]] = Field(min_length=1, max_length=4)
    confirm_demo: bool = False
