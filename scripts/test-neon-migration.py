"""Testes de segurança da cópia; recuperação real opcional numa base local descartável."""

import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("neon_migration", ROOT / "scripts" / "migrate-neon.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)
from app.database_url import database_url, load_database_environment


class ConfigurationTests(unittest.TestCase):
    def test_provider_url_uses_installed_driver_and_preserves_tls_and_password(self):
        url = database_url("postgresql://role:p%40ss%3Aword@ep-test.eu-central-1.aws.neon.tech/db?sslmode=require&channel_binding=require")
        self.assertEqual(url.drivername, "postgresql+psycopg")
        self.assertEqual(url.password, "p@ss:word")
        migration.validate_neon(url)
        self.assertEqual(migration.parameters(url)["channel_binding"], "require")

    def test_invalid_urls_never_echo_the_input(self):
        for value in ["secret-password", "mysql://role:secret-password@host/db", "postgresql://role:secret-password@host:invalid/db"]:
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                database_url(value)
            self.assertNotIn("secret-password", str(caught.exception))

    def test_neon_requires_direct_tls_and_rejects_connection_overrides(self):
        cases = [
            "postgresql://role:pass@localhost/db?sslmode=require",
            "postgresql://role:pass@ep-test-pooler.aws.neon.tech/db?sslmode=require",
            "postgresql://role:pass@ep-test.aws.neon.tech/db?sslmode=disable",
            "postgresql://role:pass@ep-test.aws.neon.tech/db?sslmode=require&host=localhost",
            "postgresql://role:pass@ep-test.aws.neon.tech/db?sslmode=require&service=other",
            "postgresql://role:pass@ep-test.aws.neon.tech/db?sslmode=require&sslmode=disable",
            "postgresql://role:pass@ep-test.aws.neon.tech/host%3Dlocalhost?sslmode=require",
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                migration.validate_neon(database_url(value))

    def test_password_and_tls_are_passed_only_in_the_subprocess_environment(self):
        url = database_url("postgresql://role:private@ep-test.aws.neon.tech/db?sslmode=require&channel_binding=require")
        with patch.object(migration, "pg_tool", return_value="pg_dump") as tool, patch.object(migration.subprocess, "run") as run, patch.dict(os.environ, {"PGOPTIONS": "-csearch_path=wrong", "PGSERVICE": "wrong"}):
            run.return_value.returncode = 0
            migration.run_pg("pg_dump", url, ["--format=custom"])
        tool.assert_called_once_with("pg_dump")
        args, kwargs = run.call_args
        self.assertNotIn("private", " ".join(args[0]))
        self.assertEqual(kwargs["env"]["PGPASSWORD"], "private")
        self.assertEqual(kwargs["env"]["PGDATABASE"], "db")
        self.assertEqual(kwargs["env"]["PGSSLMODE"], "require")
        self.assertEqual(kwargs["env"]["PGCHANNELBINDING"], "require")
        self.assertNotIn("PGSERVICE", kwargs["env"])
        self.assertNotIn("PGOPTIONS", kwargs["env"])

    def test_explicit_environment_file_wins_without_falling_back_to_local_database(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"DATABASE_URL": "local", "GPGEST_ENV_FILE": "selected.env"}):
            root = Path(folder)
            (root / ".env").write_text("DATABASE_URL=wrong\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_database_environment(root)
            (root / "selected.env").touch()
            with self.assertRaises(RuntimeError):
                load_database_environment(root)
            (root / "selected.env").write_text("DATABASE_URL=remote\n", encoding="utf-8")
            load_database_environment(root)
            self.assertEqual(os.environ["DATABASE_URL"], "remote")

    def test_default_environment_preserves_hosting_configuration(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"DATABASE_URL": "hosting"}):
            os.environ.pop("GPGEST_ENV_FILE", None)
            root = Path(folder)
            (root / ".env").write_text("DATABASE_URL=local\n", encoding="utf-8")
            load_database_environment(root)
            self.assertEqual(os.environ["DATABASE_URL"], "hosting")

    def test_runtime_preserves_encryption_key_and_demo_mode_and_refuses_overwrite(self):
        key = Fernet.generate_key().decode()
        settings = {"PLATFORM_ENCRYPTION_KEY": key, "APP_MODE": "demonstracao", "DATABASE_URL": "local"}
        url = database_url("postgresql://role:private@ep-test.aws.neon.tech/db?sslmode=require")
        with tempfile.TemporaryDirectory() as folder, patch.object(migration, "ROOT", Path(folder)):
            (Path(folder) / ".local").mkdir()
            migration.write_runtime(settings, url)
            copied = dotenv_values(Path(folder) / ".local" / "neon-runtime.env")
            self.assertEqual(copied["PLATFORM_ENCRYPTION_KEY"], key)
            self.assertEqual(copied["APP_MODE"], "demonstracao")
            self.assertEqual(database_url(copied["DATABASE_URL"]), url)
            with self.assertRaises(ValueError):
                migration.write_runtime(settings, url)


@unittest.skipUnless(os.getenv("GPGEST_TEST_RESTORE") == "1", "Defina GPGEST_TEST_RESTORE=1 para testar a recuperação real local.")
class RecoveryTests(unittest.TestCase):
    def test_full_snapshot_restore_atomic_failure_and_nonempty_destination_guard(self):
        settings = migration.read_settings(ROOT / ".env")
        self.assertEqual(settings.get("APP_MODE"), "demonstracao")
        source_url = database_url(settings["DATABASE_URL"])
        migration.validate_source(source_url)
        manifest = max((p for p in (ROOT / "backups").glob("gpgest-neon-*.json") if p.with_suffix(".dump").is_file()), key=lambda p: p.stat().st_mtime)
        info = json.loads(manifest.read_text(encoding="utf-8"))
        dump = manifest.parent / info["dump"]
        name = "gpgest_neon_restore_" + uuid4().hex
        target_url = source_url.set(database=name)
        admin_url = source_url.set(database="postgres")
        with migration.connect(admin_url) as admin:
            admin.autocommit = True
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            try:
                # Omitir intencionalmente uma tabela do catálogo: COPY falha depois
                # de criar outros objetos. A transação deve reverter tudo.
                original_run = migration.subprocess.run

                def incomplete_catalog(command, **kwargs):
                    result = original_run(command, **kwargs)
                    if "--list" in command:
                        catalog = result.stdout.decode("utf-8")
                        removed = re.sub(r"(?m)^\d+;\s+\d+\s+\d+\s+TABLE\s+public\s+stores\s+[^\n]*\n", "", catalog)
                        self.assertNotEqual(catalog, removed)
                        result.stdout = removed.encode("utf-8")
                    return result

                with patch.object(migration.subprocess, "run", side_effect=incomplete_catalog), self.assertRaisesRegex(ValueError, "pg_restore"):
                    migration.restore(dump, info, target_url)
                with migration.connect(target_url) as target:
                    migration.require_empty(target)
                migration.restore(dump, info, target_url)
                with migration.connect(target_url) as target:
                    with self.assertRaises(ValueError):
                        migration.require_empty(target)
                    target.execute("UPDATE public.stores SET name='Conferencia de copia alterada'")
                with self.assertRaises(ValueError):
                    migration.verify(info, target_url)
            finally:
                self.assertRegex(name, r"^gpgest_neon_restore_[a-f0-9]{32}$")
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
