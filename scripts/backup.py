import os
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
load_dotenv(root / ".env")
url = make_url(os.environ["DATABASE_URL"])
backup = root / "backups"
backup.mkdir(exist_ok=True)
target = backup / ("gpgest-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".dump")
pgdump = root / ".local" / "pgsql" / "bin" / "pg_dump.exe"
environ = {**os.environ, "PGPASSWORD": url.password}
subprocess.run(
    [
        str(pgdump),
        "-h",
        url.host,
        "-p",
        str(url.port),
        "-U",
        url.username,
        "-d",
        url.database,
        "--exclude-schema=test_*",
        "--exclude-schema=ui_*",
        "--exclude-schema=platform_test_*",
        "-Fc",
        "-f",
        str(target),
    ],
    env=environ,
    check=True,
)
print("Cópia de segurança criada:", target)
