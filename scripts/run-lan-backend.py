"""Start the same API/database with one additional, exact LAN origin."""
import argparse
import ipaddress
import os
import sys
from pathlib import Path

import uvicorn
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True)
    parser.add_argument("--https", action="store_true", help="Interface servida em HTTPS na rede local (certificado local).")
    options = parser.parse_args()
    address = ipaddress.IPv4Address(options.ip)
    if not address.is_private or address.is_loopback or address.is_link_local:
        parser.error("Indique o IPv4 privado do computador na rede local.")
    # Load the selected environment once before adding a runtime-only origin.
    from app.db import engine

    if os.getenv("COOKIE_SECURE", "").lower() == "true" and not options.https:
        raise SystemExit("Este perfil exige HTTPS. Não foi desativada a segurança dos cookies; use um acesso HTTPS ou um perfil local separado.")
    origins = {value.strip() for value in os.getenv("ALLOWED_ORIGINS", "").split(",") if value.strip()}
    scheme = "https" if options.https else "http"
    origins.update({f"{scheme}://127.0.0.1:5174", f"{scheme}://localhost:5174", f"{scheme}://{address}:5174"})
    os.environ["ALLOWED_ORIGINS"] = ",".join(sorted(origins))
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=8001)


if __name__ == "__main__":
    main()
