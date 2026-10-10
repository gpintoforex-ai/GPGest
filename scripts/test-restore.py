"""Restaura um backup para uma base temporária, verifica contagens e remove apenas essa base."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
load_dotenv(root / ".env")
if os.getenv("APP_MODE") != "demonstracao":
    raise RuntimeError("Teste de recuperação reservado à demonstração.")
url = make_url(os.environ["DATABASE_URL"])
target = "gpgest_restore_" + uuid4().hex
dump = max((root / "backups").glob("*.dump"), key=lambda p: p.stat().st_mtime)
params = {
    "host": url.host,
    "port": url.port,
    "user": url.username,
    "password": url.password,
}
with psycopg.connect(**params, dbname="postgres", autocommit=True) as admin:
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
    try:
        subprocess.run(
            [
                str(root / ".local" / "pgsql" / "bin" / "pg_restore.exe"),
                "-h",
                url.host,
                "-p",
                str(url.port),
                "-U",
                url.username,
                "-d",
                target,
                "--clean",
                "--if-exists",
                "--exit-on-error",
                str(dump),
            ],
            env={**os.environ, "PGPASSWORD": url.password},
            check=True,
        )
        with (
            psycopg.connect(**params, dbname=url.database) as source,
            psycopg.connect(**params, dbname=target) as restored,
        ):
            tables = [
                tuple(r)
                for r in source.execute(
                    "SELECT table_schema, table_name FROM information_schema.tables WHERE (table_schema='public' OR table_schema LIKE 'gpgest_c_%') AND table_type='BASE TABLE'"
                ).fetchall()
            ]
            restored_tables = [
                tuple(r)
                for r in restored.execute(
                    "SELECT table_schema, table_name FROM information_schema.tables WHERE (table_schema='public' OR table_schema LIKE 'gpgest_c_%') AND table_type='BASE TABLE'"
                ).fetchall()
            ]
            assert sorted(tables) == sorted(restored_tables)
            for schema, table in tables:
                query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
                assert source.execute(query).fetchone() == restored.execute(query).fetchone(), table
        print("Backup restaurado numa base temporária: contagens de todas as tabelas verificadas.")
    finally:
        assert target.startswith("gpgest_restore_") and len(target) == 47
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target)))
