import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
env = root / ".env"
content = env.read_text(encoding="utf-8")
settings = dotenv_values(env)
password = secrets.token_urlsafe(24)


def set_value(name, value):
    global content
    lines = [line for line in content.splitlines() if not line.startswith(name + "=")]
    content = "\n".join(lines) + "\n" + name + "=" + value + "\n"


key = settings.get("PLATFORM_ENCRYPTION_KEY")
if not key or key == "GENERATE_WITH_SETUP_PLATFORM":
    set_value("PLATFORM_ENCRYPTION_KEY", Fernet.generate_key().decode())
else:
    Fernet(key.encode())
existing_password = settings.get("PLATFORM_ADMIN_PASSWORD")
if not existing_password or existing_password.startswith("CHANGE_ME"):
    email = settings.get("PLATFORM_ADMIN_EMAIL") or "plataforma@gpgest.local"
    set_value("PLATFORM_ADMIN_EMAIL", email)
    set_value("PLATFORM_ADMIN_PASSWORD", password)
    (root / ".local").mkdir(exist_ok=True)
    (root / ".local" / "ACESSO-PLATAFORMA.txt").write_text(
        "Administração da plataforma GPGest\nURL: http://127.0.0.1:5174/plataforma\nEmail: "
        + email
        + "\nPalavra-passe: "
        + password
        + "\nEmpresa inicial: "
        + ("demo" if settings.get("APP_MODE") == "demonstracao" else "principal")
        + "\nNão partilhe este ficheiro.\n",
        encoding="utf-8",
    )
env.write_text(content, encoding="utf-8")
print("Configuração privada da plataforma preparada. Credenciais em .local/ACESSO-PLATAFORMA.txt.")
