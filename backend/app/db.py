import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from .platform_models import Company

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
url = os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError("Configure DATABASE_URL no ficheiro .env na raiz do projeto.")
engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def platform_db():
    with SessionLocal(bind=engine.execution_options(schema_translate_map={None: "public"})) as db:
        yield db


def db_session(request: Request):
    selected = request.cookies.get("gpgest_company")
    company = None
    with next_platform_session() as core:
        company = (
            core.get(Company, selected)
            if selected
            else core.scalar(select(Company).where(Company.schema_name == "public"))
        )
        if not company or not company.active:
            raise HTTPException(
                401, "Empresa indisponível. Selecione uma empresa ativa para entrar."
            )
    bind = (
        engine
        if company.schema_name == "public"
        else engine.execution_options(schema_translate_map={None: company.schema_name})
    )
    with SessionLocal(bind=bind) as db:
        db.info["company"] = {
            "id": company.id,
            "name": company.name,
            "slug": company.slug,
            "nif": company.nif,
            "mode": company.mode,
            "operating_mode": company.operating_mode,
            "erp_provider": company.erp_provider,
            "erp_active": bool(company.api_active and company.operating_mode == "integrated"),
            "connector_writes": bool(company.connector_writes),
        }
        yield db


def next_platform_session():
    return SessionLocal(bind=engine.execution_options(schema_translate_map={None: "public"}))
