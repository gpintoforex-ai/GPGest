"""Copia um snapshot local para uma base Neon vazia, sem alterar a origem.

As credenciais são lidas de ficheiros privados e passadas a libpq pelo ambiente.
Nenhum comando aceita apagar, limpar ou sobrepor uma base existente.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key
from psycopg import pq, sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.database_url import database_url

COMPANY_SCHEMA = re.compile(r"gpgest_c_[a-f0-9]{32}\Z")
SSL_MODES = {"require", "verify-ca", "verify-full"}


def read_settings(path):
    if not path.is_file():
        raise ValueError("Falta o ficheiro privado de configuração.")
    settings = dict(dotenv_values(path, encoding="utf-8-sig", interpolate=False))
    if not settings.get("DATABASE_URL"):
        raise ValueError("Preencha DATABASE_URL no ficheiro privado .local/neon.env.")
    return settings


def parameters(url):
    """Devolve parâmetros libpq; conserva TLS e channel_binding do URL Neon."""
    if not url.host or not url.database or not url.username or not url.password:
        raise ValueError("A ligação precisa de servidor, base, utilizador e palavra-passe.")
    if "=" in url.database or url.database.startswith(("postgresql://", "postgres://")):
        raise ValueError("O nome da base não pode ser uma segunda ligação PostgreSQL.")
    if any(not isinstance(v, str) for v in url.query.values()):
        raise ValueError("Não repita parâmetros no URL PostgreSQL.")
    # Não permitir que parâmetros da query substituam o destino validado.
    if set(url.query) & {"host", "hostaddr", "port", "user", "password", "dbname", "service", "servicefile"}:
        raise ValueError("A query do URL não pode substituir os parâmetros da ligação.")
    return {
        **url.query,
        "host": url.host,
        "port": str(url.port or 5432),
        "user": url.username,
        "password": url.password,
        "dbname": url.database,
        "connect_timeout": "20",
        "application_name": "gpgest_migration",
    }


def validate_source(url):
    if url.host != "127.0.0.1" or url.port != 55432:
        raise ValueError("A origem tem de ser o PostgreSQL local 127.0.0.1:55432.")
    parameters(url)


def validate_neon(url):
    host = (url.host or "").lower()
    if not host.endswith(".neon.tech") or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("O destino tem de ser uma base PostgreSQL do Neon.")
    if "-pooler" in host:
        raise ValueError("Use a ligação direta: desligue Connection pooling no painel Connect do Neon.")
    if url.query.get("sslmode") not in SSL_MODES:
        raise ValueError("A ligação Neon tem de usar sslmode=require ou verificação de certificado.")
    parameters(url)


def connect(url):
    return psycopg.connect(**parameters(url))


def pg_tool(name):
    local = ROOT / ".local" / "pgsql" / "bin" / (name + ".exe")
    found = local if local.is_file() else shutil.which(name)
    if not found:
        raise ValueError("Instale as ferramentas PostgreSQL pg_dump e pg_restore.")
    return str(found)


def run_pg(name, url, arguments):
    # Não usar DSN na linha de comandos nem herdar PGOPTIONS/PGSERVICE do terminal.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    names = {o.keyword.decode(): o.envvar.decode() for o in pq.Conninfo.get_defaults() if o.envvar}
    for parameter, value in parameters(url).items():
        if parameter not in names:
            raise ValueError("Um parâmetro da ligação não é suportado pelas ferramentas PostgreSQL.")
        env[names[parameter]] = str(value)
    env.update(PGTZ="UTC", PGCLIENTENCODING="UTF8")
    result = subprocess.run(
        [pg_tool(name), "--no-password", "--dbname=" + url.database, *arguments],
        env=env,
        capture_output=True,
        timeout=1800,
    )
    if result.returncode:
        # COPY errors podem incluir dados pessoais; não reproduzir stderr no terminal.
        (ROOT / ".local" / "neon-migration-error.log").write_bytes(result.stderr)
        raise ValueError(f"{name} falhou. Detalhes no ficheiro privado .local/neon-migration-error.log. A origem foi preservada; não limpe o destino.")


def app_schemas(connection):
    schemas = [row[0] for row in connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname='public' OR nspname ~ '^gpgest_c_[a-f0-9]{32}$' ORDER BY nspname"
    )]
    company_schemas = [row[0] for row in connection.execute(
        "SELECT schema_name FROM public.platform_companies"
    )]
    if any(s not in schemas or (s != "public" and not COMPANY_SCHEMA.fullmatch(s)) for s in company_schemas):
        raise ValueError("Uma empresa referencia um esquema inválido ou inexistente. Corrija antes de copiar.")
    if connection.execute("SELECT count(*) FROM pg_largeobject_metadata").fetchone()[0]:
        raise ValueError("A base contém large objects PostgreSQL; é necessária uma migração específica.")
    return schemas


def inventory(connection, schemas):
    """Assinaturas sem dados pessoais, com ordenação independente da ordem das linhas."""
    connection.execute("SET LOCAL TIME ZONE 'UTC'")
    connection.execute("SET LOCAL DateStyle TO 'ISO, YMD'")
    connection.execute("SET LOCAL extra_float_digits TO 3")
    connection.execute("SET LOCAL bytea_output TO 'hex'")
    tables = connection.execute(
        "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=ANY(%s) AND c.relkind IN ('r','p') ORDER BY 1,2", (schemas,)
    ).fetchall()
    result = []
    for index, (schema, table) in enumerate(tables):
        digest = hashlib.sha256()
        count = 0
        with connection.cursor(name="inventory_" + uuid4().hex) as cursor:
            cursor.execute(sql.SQL("SELECT md5(row_to_json(t)::text) FROM {} t ORDER BY 1").format(
                sql.Identifier(schema, table)
            ))
            for (row_hash,) in cursor:
                digest.update(row_hash.encode("ascii"))
                count += 1
        result.append({"schema": schema, "table": table, "rows": count, "sha256": digest.hexdigest()})
        if (index + 1) % 100 == 0:
            print(f"A conferir tabelas: {index + 1}/{len(tables)}", flush=True)
    return result


def key_fingerprint(settings):
    key = settings.get("PLATFORM_ENCRYPTION_KEY", "")
    try:
        Fernet(key.encode())
    except (ValueError, TypeError):
        raise ValueError("Preserve a chave válida PLATFORM_ENCRYPTION_KEY do ambiente local.") from None
    return hashlib.sha256(key.encode()).hexdigest()


def prepare(source_settings):
    source_url = database_url(source_settings["DATABASE_URL"])
    validate_source(source_url)
    fingerprint = key_fingerprint(source_settings)
    folder = ROOT / "backups"
    folder.mkdir(exist_ok=True)
    prefix = folder / ("gpgest-neon-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8])
    dump = prefix.with_suffix(".dump")
    manifest = prefix.with_suffix(".json")
    print("A criar uma cópia consistente dos dados locais…", flush=True)
    with connect(source_url) as source:
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        schemas = app_schemas(source)
        revision = [r[0] for r in source.execute("SELECT version_num FROM public.alembic_version")]
        snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
        tables = inventory(source, schemas)
        run_pg("pg_dump", source_url, [
            "--format=custom", "--no-owner", "--no-acl", "--lock-wait-timeout=20s",
            "--snapshot=" + snapshot, *["--schema=" + s for s in schemas], "--file=" + str(dump),
        ])
    info = {
        "format": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dump": dump.name,
        "dump_sha256": file_hash(dump),
        "schemas": schemas,
        "tables": tables,
        "alembic_revision": revision,
        "encryption_key_sha256": fingerprint,
    }
    manifest.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # A chave de cifragem não está no PostgreSQL: preservá-la junto do backup privado.
    shutil.copyfile(ROOT / ".env", prefix.with_suffix(".env"))
    google = ROOT / ".local" / "google.json"
    if google.is_file():
        shutil.copyfile(google, prefix.with_suffix(".google.json"))
    print(f"Backup criado: {dump.relative_to(ROOT)}", flush=True)
    print(f"Manifesto: {manifest.relative_to(ROOT)}", flush=True)
    print(f"Inclui {len(schemas)} esquemas, {len(tables)} tabelas e {sum(t['rows'] for t in tables)} registos.", flush=True)
    return manifest


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_empty(connection):
    # Um public vazio e plpgsql são normais numa base nova; outros objetos não.
    schemas = [r[0] for r in connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname NOT IN ('public','information_schema')"
    )]
    objects = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public') "
        "OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public') "
        "OR EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public') "
        "OR EXISTS (SELECT 1 FROM pg_extension WHERE extname<>'plpgsql')"
    ).fetchone()[0]
    if schemas or objects:
        raise ValueError("A base de destino não está vazia. Escolha uma base Neon nova; nenhum dado foi sobreposto.")


def restore(dump, info, target_url):
    if file_hash(dump) != info["dump_sha256"]:
        raise ValueError("O backup não corresponde ao manifesto. A transferência foi cancelada.")
    with connect(target_url) as target:
        require_empty(target)
        major = int(target.execute("SHOW server_version_num").fetchone()[0]) // 10000
        if major < 16:
            raise ValueError("Escolha PostgreSQL 16 ou mais recente no Neon.")
    print("Destino vazio confirmado. A copiar o backup…", flush=True)
    # As bases novas já têm public. O dump por esquemas inclui CREATE SCHEMA public;
    # ignorar apenas essa entrada, sem apagar o public existente nem os seus objetos.
    listing = subprocess.run(
        [pg_tool("pg_restore"), "--list", str(dump)], capture_output=True, timeout=60,
    )
    if listing.returncode:
        raise ValueError("Não foi possível ler o catálogo do backup PostgreSQL.")
    catalog = listing.stdout.decode("utf-8")
    lines = [line for line in catalog.splitlines() if not re.match(r"^\d+;\s+\d+\s+\d+\s+SCHEMA\s+-\s+public\s+", line)]
    with tempfile.NamedTemporaryFile(dir=dump.parent, suffix=".restore-list", delete=False) as file:
        file.write(("\n".join(lines) + "\n").encode("utf-8"))
        path = Path(file.name)
    try:
        run_pg("pg_restore", target_url, [
            "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl", "--no-tablespaces",
            "--use-list=" + str(path), str(dump),
        ])
    finally:
        path.unlink(missing_ok=True)
    verify(info, target_url)


def verify(info, target_url):
    with connect(target_url) as target:
        target.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        schemas = app_schemas(target)
        if schemas != info["schemas"] or inventory(target, schemas) != info["tables"]:
            raise ValueError("A cópia não passou a conferência. Preserve ambas as bases e não inicie a aplicação no Neon.")
        revision = [r[0] for r in target.execute("SELECT version_num FROM public.alembic_version")]
        if sorted(revision) != sorted(info["alembic_revision"]):
            raise ValueError("A revisão das migrações diverge da origem.")
    print("Cópia conferida: empresas, tabelas, contagens e assinaturas dos dados coincidem.", flush=True)


def write_runtime(source_settings, target_url):
    path = ROOT / ".local" / "neon-runtime.env"
    if path.exists():
        raise ValueError("Já existe .local/neon-runtime.env. Preserve as definições existentes e configure a ligação manualmente.")
    path.touch(mode=0o600)
    for name, value in {**source_settings, "DATABASE_URL": target_url.render_as_string(hide_password=False)}.items():
        if value is not None:
            set_key(str(path), name, value, quote_mode="always", encoding="utf-8")
    print("Configuração privada preparada: .local/neon-runtime.env (modo e chave de cifragem preservados).", flush=True)


def ready():
    settings = read_settings(ROOT / ".local" / "neon-runtime.env")
    url = database_url(settings["DATABASE_URL"])
    validate_neon(url)
    key_fingerprint(settings)
    with connect(url) as target:
        target.execute("SET TRANSACTION READ ONLY")
        app_schemas(target)
        revision = target.execute("SELECT version_num FROM public.alembic_version").fetchall()
        if not revision:
            raise ValueError("Faltam as migrações no Neon. Conclua a cópia antes de iniciar.")
    print("Ligação Neon e esquemas da aplicação disponíveis; configuração privada preservada.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "copy", "verify", "ready"])
    parser.add_argument("--manifest", type=Path, help="Manifesto criado por prepare; obrigatório em verify.")
    args = parser.parse_args()
    if args.action == "ready":
        ready()
        return
    settings = read_settings(ROOT / ".env")
    if args.action == "prepare":
        prepare(settings)
        return
    target_url = database_url(read_settings(ROOT / ".local" / "neon.env")["DATABASE_URL"])
    validate_neon(target_url)
    source_url = database_url(settings["DATABASE_URL"])
    validate_source(source_url)
    if args.action == "verify" and not args.manifest:
        raise ValueError("Indique --manifest para conferir uma cópia existente.")
    if args.action == "copy":
        with connect(target_url) as target:
            require_empty(target)
    manifest = args.manifest or prepare(settings)
    info = json.loads(manifest.read_text(encoding="utf-8"))
    if info.get("format") != 1 or key_fingerprint(settings) != info["encryption_key_sha256"]:
        raise ValueError("Preserve a configuração e a chave de cifragem usadas neste backup.")
    dump = manifest.parent / info["dump"]
    if args.action == "copy":
        if (ROOT / ".local" / "neon-runtime.env").exists():
            raise ValueError("Já existe uma configuração Neon. Verifique-a antes de copiar para outro destino.")
        restore(dump, info, target_url)
        write_runtime(settings, target_url)
    else:
        verify(info, target_url)


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except psycopg.Error as exc:
        print("Falha PostgreSQL: verifique a ligação e as permissões (SQLSTATE " + (exc.sqlstate or "indisponível") + "). Nenhuma credencial foi apresentada.", file=sys.stderr)
        sys.exit(1)
    except (OSError, subprocess.TimeoutExpired, KeyError, json.JSONDecodeError):
        print("Não foi possível concluir a operação. Verifique os ficheiros privados e a ligação; a origem foi preservada.", file=sys.stderr)
        sys.exit(1)
