"""Provider-independent, read-only ERP contracts. No database or network setup here."""

from collections.abc import Callable, Iterable
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ERPIntegrationError(ValueError):
    """A connector cannot supply a complete, valid snapshot."""


class PurchaseDocument(BaseModel):
    """Canonical EUR document; money is signed integer cents, including credit notes."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    external_id: str = Field(min_length=1, max_length=200)
    supplier_nif: str = Field(max_length=30)
    supplier_name: str = Field(min_length=1, max_length=300)
    document: str = Field(max_length=100)
    internal_document: str = Field(max_length=200)
    document_type: str = Field(max_length=10)
    document_date: str
    amount_cents: int
    base_cents: int | None = None
    tax_cents: int | None = None
    cancelled: bool = False

    @field_validator("document_date")
    @classmethod
    def iso_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Use uma data no formato AAAA-MM-DD.")
        return value

    @field_validator("external_id", "supplier_name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("O campo não pode estar vazio.")
        return value

    @field_validator("amount_cents", "base_cents", "tax_cents")
    @classmethod
    def database_integer(cls, value: int | None) -> int | None:
        if value is not None and not -(2**63) <= value < 2**63:
            raise ValueError("O montante excede a capacidade de armazenamento.")
        return value


class PurchaseDocumentAdapter(Protocol):
    """Each instance belongs to one configured connection and must not post documents."""

    def purchase_documents(self, start: date, end: date) -> Iterable[PurchaseDocument]: ...


class ERPRegistry:
    """Factories create fresh adapters; credentials and clients stay with the caller."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], PurchaseDocumentAdapter]] = {}

    def register(self, provider: str, factory: Callable[[], PurchaseDocumentAdapter]) -> None:
        if not provider or provider != provider.strip().lower():
            raise ERPIntegrationError("Identificador de ERP inválido.")
        if provider in self._factories:
            raise ERPIntegrationError("O conector ERP já está registado.")
        self._factories[provider] = factory

    def purchase_documents(self, provider: str, start: date, end: date) -> list[PurchaseDocument]:
        if start > end:
            raise ERPIntegrationError("O início do período deve preceder o fim.")
        factory = self._factories.get(provider)
        if factory is None:
            raise ERPIntegrationError("Não existe um conector para o ERP selecionado.")
        result: list[PurchaseDocument] = []
        seen: set[str] = set()
        # Materialize and validate the entire snapshot before the caller persists anything.
        try:
            for item in factory().purchase_documents(start, end):
                if not isinstance(item, PurchaseDocument):
                    raise ERPIntegrationError("O conector devolveu um documento não normalizado.")
                if item.external_id in seen:
                    raise ERPIntegrationError("O ERP devolveu documentos com IDs repetidos.")
                if not start <= date.fromisoformat(item.document_date) <= end:
                    raise ERPIntegrationError("O ERP devolveu documentos fora do período pedido.")
                seen.add(item.external_id)
                result.append(item)
        except ValidationError:
            # Do not expose raw supplier data or connector payloads in public error messages.
            raise ERPIntegrationError("O ERP devolveu um documento com formato inválido.") from None
        return result
