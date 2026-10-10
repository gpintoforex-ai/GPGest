from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass(frozen=True)
class ExternalReceipt:
    external_id: str
    amount_cents: int
    payment: str
    store_code: str


class WintouchAdapter(Protocol):
    """Contrato interno. Não presume endpoints nem tabelas do Wintouch."""

    def receipts(self, cursor: str | None) -> Iterable[ExternalReceipt]: ...


class UnconfiguredWintouch:
    def receipts(self, cursor=None):
        raise NotImplementedError(
            "Integração pendente da versão e documentação autorizada do Wintouch."
        )
