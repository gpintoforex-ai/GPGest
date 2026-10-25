"""Edit cash register configuration and remove unused registers without losing history."""

from typing import Literal

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import db_session
from .models import (
    AccountBalanceReference,
    BankDeposit,
    CashSession,
    IntegrationBinding,
    PaymentTerminal,
    Register,
    Store,
    WintouchCashAccount,
)
from .schemas import RegisterCreate, Strict


class RemoveRegister(Strict):
    confirmed: Literal[True]


def register_dependencies(db, identifier):
    """Registos que impedem remover uma caixa: (descrição, quantidade)."""
    result = [
        (label, db.scalar(select(func.count()).select_from(model).where(model.register_id == identifier)))
        for model, label in (
            (CashSession, "sessões ou folhas de caixa no histórico"),
            (BankDeposit, "depósitos associados"),
            (PaymentTerminal, "terminais de pagamento associados"),
            (WintouchCashAccount, "associação a uma caixa Wintouch"),
        )
    ]
    result.append(
        (
            "associação de integração",
            db.scalar(
                select(func.count())
                .select_from(IntegrationBinding)
                .where(
                    IntegrationBinding.internal_kind == "register",
                    IntegrationBinding.internal_id == identifier,
                )
            ),
        )
    )
    reference = db.get(AccountBalanceReference, "cash:" + identifier)
    result.append(("saldo inicial registado", 1 if reference and reference.amount_cents else 0))
    return result


def protected_reason(db, identifier):
    return next((label for label, count in register_dependencies(db, identifier) if count), None)


def register_routes(app, current, authorize, audit, serial):
    def writable(db, user, identifier):
        row = db.scalar(select(Register).where(Register.id == identifier).with_for_update())
        if not row:
            raise HTTPException(404, "Caixa não encontrada.")
        authorize(user, db, row.store_id, ["administrador", "gestor"])
        return row

    @app.post("/api/registers/{register_id}")
    def edit_register(
        register_id: str,
        data: RegisterCreate,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        row = writable(db, user, register_id)
        authorize(user, db, data.store_id, ["administrador", "gestor"])
        if not db.get(Store, data.store_id):
            raise HTTPException(404, "Estabelecimento não encontrado.")
        if data.store_id != row.store_id:
            reason = protected_reason(db, row.id)
            if reason:
                raise HTTPException(
                    409,
                    f"A caixa «{row.name}» tem {reason}. Pode editar o nome, mas não mudar o estabelecimento. Crie outra caixa para o novo estabelecimento.",
                )
            reference = db.get(AccountBalanceReference, "cash:" + row.id)
            if reference:
                reference.store_id = data.store_id
        before = {"name": row.name, "store_id": row.store_id}
        row.name, row.store_id = data.name, data.store_id
        audit(
            db,
            user,
            "caixa_editada",
            row.id,
            {"before": before, "after": data.model_dump()},
            row.store_id,
        )
        db.commit()
        return serial(row)

    @app.post("/api/registers/{register_id}/delete")
    def remove_register(
        register_id: str,
        data: RemoveRegister,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        row = writable(db, user, register_id)
        reason = protected_reason(db, row.id)
        if reason:
            raise HTTPException(
                409,
                f"Não é possível remover a caixa «{row.name}»: tem {reason}. O histórico e as configurações associadas foram preservados. Pode editar o nome da caixa.",
            )
        reference = db.get(AccountBalanceReference, "cash:" + row.id)
        if reference:
            db.delete(reference)  # Only a zero initial reference can reach this point.
        audit(
            db,
            user,
            "caixa_removida",
            row.id,
            {"name": row.name, "store_id": row.store_id},
            row.store_id,
        )
        db.delete(row)
        db.commit()
        return {"ok": True, "id": register_id}
