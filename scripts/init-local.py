"""Inicializa apenas o PostgreSQL privado deste projeto; preserva dados existentes."""

import os
import secrets
import subprocess
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
local = root / ".local"
local.mkdir(exist_ok=True)
pg = local / "pgsql" / "bin"
if not (pg / "initdb.exe").exists():
    archive = local / "postgres.zip"
    if not archive.exists() or not zipfile.is_zipfile(archive):
        print("A descarregar PostgreSQL do fornecedor EDB…", flush=True)
        urlretrieve(
            "https://get.enterprisedb.com/postgresql/postgresql-16.15-1-windows-x64-binaries.zip",
            archive,
        )
    with zipfile.ZipFile(archive) as z:
        z.extractall(local)
env = root / ".env"
if not env.exists():
    db_password = secrets.token_urlsafe(32)
    admin_password = secrets.token_urlsafe(18)
    env.write_text(
        f"DATABASE_URL=postgresql+psycopg://gpgest:{db_password}@127.0.0.1:55432/gpgest_demo\nAPP_MODE=demonstracao\nADMIN_EMAIL=admin@gpgest.local\nADMIN_PASSWORD={admin_password}\nCOOKIE_SECURE=false\nALLOWED_ORIGINS=http://127.0.0.1:5174\n",
        encoding="utf-8",
    )
    (local / "ACESSO-LOCAL.txt").write_text(
        f"GPGest - demonstração local\nURL: http://127.0.0.1:5174\nEmail: admin@gpgest.local\nPalavra-passe: {admin_password}\nNão partilhe este ficheiro.\n",
        encoding="utf-8",
    )
load_dotenv(env)

url = make_url(os.environ["DATABASE_URL"])
if url.host != "127.0.0.1" or url.port != 55432:
    raise RuntimeError(
        "Este script gere apenas o servidor local 127.0.0.1:55432. Para outra base use migrações diretamente."
    )
data = local / "pgdata"
if not (data / "PG_VERSION").exists():
    password_file = local / "init-password.txt"
    password_file.write_text(url.password, encoding="utf-8")
    try:
        subprocess.run(
            [
                str(pg / "initdb.exe"),
                "-D",
                str(data),
                "-U",
                url.username,
                "--pwfile=" + str(password_file),
                "-A",
                "scram-sha-256",
                "-E",
                "UTF8",
                "--locale=C",
            ],
            check=True,
        )
    finally:
        password_file.unlink(missing_ok=True)
    with (data / "postgresql.conf").open("a", encoding="utf-8") as f:
        f.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\ntimezone = 'Europe/Lisbon'\n")
if (
    subprocess.run(
        [str(pg / "pg_ctl.exe"), "-D", str(data), "status"], stdout=subprocess.DEVNULL
    ).returncode
    != 0
):
    subprocess.run(
        [
            str(pg / "pg_ctl.exe"),
            "-D",
            str(data),
            "-l",
            str(local / "postgres.log"),
            "-t",
            "600",
            "start",
        ],
        check=True,
    )
with psycopg.connect(
    host=url.host,
    port=url.port,
    user=url.username,
    password=url.password,
    dbname="postgres",
    autocommit=True,
) as connection:
    connection.execute("ALTER SYSTEM SET timezone TO 'Europe/Lisbon'")
    connection.execute("SELECT pg_reload_conf()")
    if not connection.execute(
        "SELECT 1 FROM pg_database WHERE datname=%s", (url.database,)
    ).fetchone():
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(url.database)))
print("PostgreSQL local preparado. Credenciais em .local/ACESSO-LOCAL.txt.", flush=True)
