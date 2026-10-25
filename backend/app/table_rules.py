"""Regra comum: um registo inativo de uma tabela não pode ser usado em novos registos.

A exceção é manter o valor que um registo já tinha (`current`), para que editar um documento
antigo não obrigue a trocar uma rubrica, caixa, armazém ou prazo entretanto desativado.
"""

from fastapi import HTTPException

LABELS = {
    "Category": "A rubrica",
    "Register": "A caixa",
    "StockWarehouse": "O armazém",
    "PaymentTerm": "O prazo de pagamento",
    "BankInstitution": "O banco",
}


def ensure_usable(db, model, identifier, current=None):
    """Devolve o registo; recusa-o se estiver inativo e não for o valor já existente."""
    if not identifier:
        return None
    row = db.get(model, identifier)
    if row is not None and getattr(row, "active", True) is False and identifier != current:
        label = LABELS.get(model.__name__, "O registo")
        name = getattr(row, "name", "")
        raise HTTPException(
            422,
            f"{label} «{name}» está inativ{'a' if label.startswith('A ') else 'o'} e não pode ser usad"
            f"{'a' if label.startswith('A ') else 'o'} em novos registos. Reative-{'a' if label.startswith('A ') else 'o'} em Tabelas se for necessário.",
        )
    return row
