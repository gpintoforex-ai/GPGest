"""Configuração PostgreSQL local ou alojada, usando o driver instalado no GPGest."""

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def load_database_environment(root: Path):
    selected = os.environ.get("GPGEST_ENV_FILE")
    if selected:
        path = Path(selected)
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            raise RuntimeError("O ficheiro selecionado em GPGEST_ENV_FILE não existe.")
        if not dotenv_values(path, encoding="utf-8-sig", interpolate=False).get("DATABASE_URL"):
            raise RuntimeError("O ficheiro selecionado em GPGEST_ENV_FILE tem de definir DATABASE_URL.")
        # Uma seleção explícita tem precedência sobre uma ligação herdada do terminal.
        load_dotenv(path, override=True, encoding="utf-8-sig", interpolate=False)
    else:
        load_dotenv(root / ".env")


def database_url(value: str) -> URL:
    try:
        url = make_url(value.strip())
    except (ArgumentError, ValueError, TypeError):
        # O erro original pode conter o URL e a palavra-passe.
        raise ValueError("URL PostgreSQL inválido.") from None
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Use um URL PostgreSQL com o driver psycopg.")
    return url
