import os

from app.db import next_platform_session
from app.platform_models import Company, PlatformAdmin
from pwdlib import PasswordHash
from sqlalchemy import select

with next_platform_session() as db:
    if not db.scalar(select(Company).where(Company.schema_name == "public")):
        db.add(
            Company(
                name="Empresa de demonstração"
                if os.getenv("APP_MODE") == "demonstracao"
                else "Empresa inicial",
                slug="demo" if os.getenv("APP_MODE") == "demonstracao" else "principal",
                schema_name="public",
                mode=os.getenv("APP_MODE", "real"),
            )
        )
    if not db.scalar(select(PlatformAdmin)):
        password = os.getenv("PLATFORM_ADMIN_PASSWORD", "")
        if len(password) < 12:
            raise RuntimeError("Configure PLATFORM_ADMIN_PASSWORD com pelo menos 12 caracteres.")
        db.add(
            PlatformAdmin(
                email=os.getenv("PLATFORM_ADMIN_EMAIL", "plataforma@gpgest.local").lower(),
                password_hash=PasswordHash.recommended().hash(password),
            )
        )
    db.commit()
print("Plataforma preparada; empresas existentes preservadas.")
