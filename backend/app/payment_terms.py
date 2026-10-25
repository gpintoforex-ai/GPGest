"""Prazos de pagamento: regras de vencimento e lista para as fichas de clientes/fornecedores.

A tabela (criar, editar, ativar/desativar, remover e sincronizar com o ERP) é gerida pelo
framework comum das tabelas (table_framework.py / table_specs.py, chave «payment_terms»).
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import current
from .db import db_session
from .models import PaymentTerm

router = APIRouter()

TERM_TYPES = {
    "apos": "Após N dias",
    "fim_mes": "Fim do mês",
    "fim_mes_seguinte": "Fim do próximo mês",
    "outro": "Outro (Wintouch)",
}


def due_date(document_date, term_type, days):
    """Vencimento de um documento segundo o prazo; None quando o tipo não é conhecido."""
    if term_type == "apos" and days is not None:
        return document_date + timedelta(days=days)
    if term_type in ("fim_mes", "fim_mes_seguinte"):
        year, month = document_date.year, document_date.month
        if term_type == "fim_mes_seguinte":
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return date(year, month, calendar.monthrange(year, month)[1])
    return None


def term_rule(db, term_id):
    """Resumo do prazo de uma entidade para cálculo de vencimentos (None se não houver)."""
    term = db.get(PaymentTerm, term_id) if term_id else None
    if term is None:
        return None
    return {"name": term.name, "term_type": term.term_type, "days": term.days}


def payment_days_for(term):
    """Dias equivalentes guardados na entidade (usados quando o prazo é «Após N dias»)."""
    return term.days if term is not None and term.term_type == "apos" else None


@router.get("/api/payment-terms")
def list_terms(user=Depends(current), db: Session = Depends(db_session)):
    """Prazos para escolher nas fichas (os inativos só aparecem quando já estão escolhidos)."""
    terms = sorted(
        db.scalars(select(PaymentTerm)),
        key=lambda t: (not t.active, t.term_type != "apos", t.days or 0, t.name.casefold()),
    )
    return [
        {
            "id": t.id,
            "code": t.code,
            "name": t.name,
            "term_type": t.term_type,
            "term_type_label": TERM_TYPES.get(t.term_type, t.term_type),
            "days": t.days,
            "discount_percent": f"{Decimal(t.discount_basis_points or 0) / 100:.2f}",
            "active": t.active,
        }
        for t in terms
    ]
