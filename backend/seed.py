import os

from app.db import SessionLocal
from app.models import Access, BankAccount, Category, Register, Store, User
from pwdlib import PasswordHash
from sqlalchemy import select

with SessionLocal.begin() as db:
    if not db.scalar(select(User)):
        password = os.getenv("ADMIN_PASSWORD", "")
        if len(password) < 12 or password.startswith("CHANGE_ME"):
            raise RuntimeError("Configure ADMIN_PASSWORD com pelo menos 12 caracteres.")
        demo = os.getenv("APP_MODE") == "demonstracao"
        user = User(
            name="Administrador",
            email=os.getenv("ADMIN_EMAIL", "admin@gpgest.local"),
            password_hash=PasswordHash.recommended().hash(password),
            role="administrador",
        )
        db.add(user)
        db.flush()
        if demo:
            store = Store(name="Loja de demonstração")
            db.add(store)
            db.flush()
            db.add(Register(store_id=store.id, name="Caixa 01"))
            db.add(
                BankAccount(
                    store_id=store.id,
                    name="Conta de demonstração",
                    bank="Banco de exemplo",
                    iban_last4="0000",
                )
            )
            db.add(Access(user_id=user.id, store_id=store.id))
        for name in [
            "Vendas",
            "Clientes",
            "Fornecedores",
            "Despesas correntes",
            "Transferências e fundos",
            "Outros",
        ]:
            db.add(Category(name=name))
        print("Administrador e categorias criados. Modo:", os.getenv("APP_MODE", "real"))
    else:
        print("Base de dados já inicializada; dados preservados.")
