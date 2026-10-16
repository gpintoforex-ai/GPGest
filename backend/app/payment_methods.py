"""Default bank destinations for local payment methods, scoped by establishment."""

from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .cash_local import method_catalog
from .db import db_session
from .models import BankAccount, LocalPaymentMethod, PaymentMethodBankDefault, Store
from .operating_modes import policy
from .schemas import Strict


class BankDefault(Strict):
    store_id: str = Field(min_length=1, max_length=36)
    account_id: str | None = Field(max_length=36)


def bank_defaults(db, store_ids):
    return [
        {"method_id": row.method_id, "store_id": row.store_id, "account_id": row.account_id}
        for row in db.scalars(
            select(PaymentMethodBankDefault)
            .where(PaymentMethodBankDefault.store_id.in_(store_ids))
            .order_by(PaymentMethodBankDefault.store_id, PaymentMethodBankDefault.method_id)
        )
    ]


def register_routes(app, current, authorize, record):
    @app.post("/api/cash/payment-methods/{method_id}/bank-default")
    def save_bank_default(method_id: str, data: BankDefault, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, data.store_id, ["administrador", "gestor"])
        if policy(db)["mode"] != "local":
            raise HTTPException(409, "Configure os meios locais no modo local autónomo.")
        method = next((row for row in method_catalog(db) if row["id"] == method_id), None)
        if method is None:
            raise HTTPException(404, "Meio de pagamento não encontrado nesta empresa.")
        if method_id == "numerario":
            raise HTTPException(422, "O numerário é conferido na gaveta. Associe a conta bancária aos restantes meios de pagamento.")
        if not db.get(Store, data.store_id):
            raise HTTPException(404, "Estabelecimento não encontrado nesta empresa.")
        if data.account_id is not None:
            account = db.get(BankAccount, data.account_id)
            if not account or account.store_id != data.store_id:
                raise HTTPException(422, "Escolha uma conta bancária deste estabelecimento.")
        previous = db.get(PaymentMethodBankDefault, (method_id, data.store_id))
        before = previous.account_id if previous else None
        if before != data.account_id:
            if data.account_id is None:
                db.delete(previous)
            else:
                # Base methods are virtual until first configured. Preserve activation.
                db.execute(insert(LocalPaymentMethod).values(
                    id=method_id, name=method["name"], active=method["active"]
                ).on_conflict_do_nothing(index_elements=[LocalPaymentMethod.id]))
                db.execute(insert(PaymentMethodBankDefault).values(
                    method_id=method_id, store_id=data.store_id, account_id=data.account_id
                ).on_conflict_do_update(
                    index_elements=[PaymentMethodBankDefault.method_id, PaymentMethodBankDefault.store_id],
                    set_={"account_id": data.account_id},
                ))
            record(db, user, "meio_pagamento_banco_alterado", method_id,
                   {"name": method["name"], "before": before, "account_id": data.account_id}, data.store_id)
            db.commit()
        return {"method_id": method_id, "store_id": data.store_id, "account_id": data.account_id}
