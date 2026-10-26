"""Tipos de documento de compra (FC, FCD, NCC, NCCD…): definem o efeito financeiro das receções.

Natureza e condição de pagamento decidem o efeito ao confirmar uma receção:
- crédito (FC/NCC): compra em dívida e movimento previsto no vencimento;
- dinheiro (FCD/NCCD): movimento real na caixa ou na conta do meio de pagamento, sem dívida.
Os indicadores «gera movimento de caixa» e «gera dívida» derivam da condição, mas ficam guardados
para permitir exceções configuradas.
"""

import json

from sqlalchemy import func, select

from .models import ProcurementDocument, Purchase, PurchaseDocumentType

NATURES = {"compra": "Compra", "nota_credito_compra": "Nota de crédito de compra"}
CONDITIONS = {"credito": "Crédito", "dinheiro": "Dinheiro"}
DEFAULTS = [
    # código, descrição, natureza, condição, meio por omissão, dias
    ("FC", "Fatura de Compra", "compra", "credito", None, 30),
    ("FCD", "Fatura de Compra a Dinheiro", "compra", "dinheiro", "numerario", None),
    ("NCC", "Nota de Crédito de Compra", "nota_credito_compra", "credito", None, 30),
    ("NCCD", "Nota de Crédito Compra Dinheiro", "nota_credito_compra", "dinheiro", "numerario", None),
]


def sign_of(nature):
    return -1 if nature == "nota_credito_compra" else 1


def derived_flags(condition):
    """(gera movimento de caixa, gera dívida) por omissão para a condição de pagamento."""
    return (condition == "dinheiro", condition == "credito")


def ensure_defaults(db):
    """Tipos pré-carregados na primeira utilização (empresas novas incluídas)."""
    if db.scalar(select(PurchaseDocumentType.id).limit(1)) is not None:
        return
    for code, description, nature, condition, method, days in DEFAULTS:
        cash, debt = derived_flags(condition)
        db.add(PurchaseDocumentType(
            code=code, description=description, nature=nature, sign=sign_of(nature),
            payment_condition=condition, default_payment_method=method, creates_cash_movement=cash,
            creates_debt=debt, default_due_days=days, active=True,
        ))
    db.flush()


def purchase_code(kind):
    """Tipo fiscal da compra (identidade com e-Fatura/ERP): FT, FR (fatura-recibo) ou NC."""
    if kind.nature == "nota_credito_compra":
        return "NC"
    return "FR" if kind.payment_condition == "dinheiro" else "FT"


def document_type_usage(db):
    """Documentos (receções e compras) por tipo: um tipo usado não pode ser eliminado, só desativado."""
    usage = dict(db.execute(
        select(Purchase.document_type_id, func.count()).where(Purchase.document_type_id.is_not(None))
        .group_by(Purchase.document_type_id)).all())
    for row in db.scalars(select(ProcurementDocument).where(ProcurementDocument.data.contains('"document_type_id"'))):
        identifier = json.loads(row.data).get("document_type_id")
        if identifier:
            usage[identifier] = usage.get(identifier, 0) + 1
    return usage
