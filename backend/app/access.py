"""Autenticação, autorização por loja, auditoria e serialização comuns às rotas."""

import hashlib
import json
from datetime import timezone
from decimal import Decimal, InvalidOperation

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session
from .models import Access, Audit, BankAccount, LoginSession, Store, User, now


def cents(value: str, zero=False):
    try:
        amount = Decimal(value.replace(",", "."))
        if (
            not amount.is_finite()
            or amount < 0
            or (not zero and amount == 0)
            or amount > Decimal("999999999")
            or amount * 100 != (amount * 100).to_integral_value()
        ):
            raise ValueError()
        return int(amount * 100)
    except (InvalidOperation, ValueError):
        raise HTTPException(
            422, "Introduza um montante positivo com, no máximo, duas casas decimais."
        )


def current(request: Request, db: Session = Depends(db_session)):
    token = request.cookies.get("gpgest_session", "")
    selected = db.execute(
        select(LoginSession, User)
        .join(User, User.id == LoginSession.user_id)
        .where(LoginSession.token_hash == hashlib.sha256(token.encode()).hexdigest())
    ).first()
    if not selected:
        raise HTTPException(401, "Inicie sessão para continuar.")
    login, user = selected
    expires = (
        login.expires_at.replace(tzinfo=timezone.utc)
        if login.expires_at.tzinfo is None
        else login.expires_at
    )
    if expires <= now() or not user or not user.active:
        raise HTTPException(401, "A sessão expirou.")
    return user


def authorize(user, db, store_id=None, roles=None):
    if roles and user.role not in roles:
        raise HTTPException(403, "O seu perfil não permite esta ação.")
    if store_id and user.role != "administrador" and not db.get(Access, (user.id, store_id)):
        raise HTTPException(403, "Não tem acesso a esta loja.")


def scope(user, db):
    return (
        [s.id for s in db.scalars(select(Store))]
        if user.role == "administrador"
        else list(db.scalars(select(Access.store_id).where(Access.user_id == user.id)))
    )


def record(db, user, action, target, detail, store_id=None):
    db.add(
        Audit(
            user_id=user.id,
            store_id=store_id,
            action=action,
            target_id=target,
            detail=json.dumps(detail, ensure_ascii=False),
        )
    )


def serial(obj):
    result = {
        c.name: getattr(obj, c.name)
        for c in obj.__table__.columns
        if c.name not in {"password_hash", "token_hash", "iban_ciphertext", "password_ciphertext", "payload_ciphertext"}
    }
    if isinstance(obj, BankAccount):
        result["iban_configured"] = bool(obj.iban_ciphertext)
    return result
