from typing import Protocol


class BankingAdapter(Protocol):
    def transactions(self, consent_id: str, cursor: str | None): ...


# Sem credenciais de homebanking. Integração depende de prestador e consentimento.
