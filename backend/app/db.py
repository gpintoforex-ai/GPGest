import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
url = os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError("Configure DATABASE_URL no ficheiro .env na raiz do projeto.")
engine = create_engine(url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def db_session():
    with SessionLocal() as db:
        yield db
