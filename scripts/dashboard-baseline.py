"""Regista os valores atuais do Dashboard de uma empresa para comparar com uma nova versão (só leitura).

Chama as mesmas rotas que a página (`/api/dashboard/overview` e `/api/order-cycle/analytics`) para vários
períodos e comparações, com o utilizador indicado, e grava o JSON em `.local/dashboard-baseline/<etiqueta>/`
(dados reais: pasta privada, fora do Git). Não grava nada na base de dados.

Uso (a partir de backend/):
  ..\\.venv\\Scripts\\python.exe ..\\scripts\\dashboard-baseline.py <esquema> <email-ou-nome> <etiqueta>
  ..\\.venv\\Scripts\\python.exe ..\\scripts\\dashboard-baseline.py --compare <etiqueta-a> <etiqueta-b>
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".local" / "dashboard-baseline"
# Períodos fixos para que duas recolhas em dias diferentes sejam comparáveis.
PERIODS = [
    ("mes-novembro-anterior", "2026-11-01", "2026-11-08", "previous"),
    ("mes-outubro-anterior", "2026-10-01", "2026-10-31", "previous"),
    ("trimestre-3-homologo", "2026-07-01", "2026-09-30", "yoy"),
    ("ano-2026-anterior", "2026-01-01", "2026-11-08", "previous"),
]
# Campos que dependem do momento da consulta (não são diferenças de cálculo).
VOLATILE = {"generated_at", "updated_at", "in_progress", "forecast", "due", "stale", "balance_date", "warnings", "liquidity_error"}


def collect(schema, who, label):
    sys.path.insert(0, str(ROOT / "backend"))
    from app import db as database
    from app.access import current
    from app.db import db_session
    from app.main import app
    from app.models import User
    from app.platform_models import Company
    from fastapi.testclient import TestClient
    from sqlalchemy import or_, select
    from sqlalchemy.orm import Session

    with database.next_platform_session() as core:
        company = core.scalar(select(Company).where(Company.schema_name == schema))
        company_id = company.id
    engine = database.engine.execution_options(schema_translate_map={None: schema})
    with Session(engine) as db:
        user = db.scalar(select(User).where(or_(User.email == who, User.name == who)))
        if not user:
            sys.exit(f"Utilizador não encontrado: {who}")
        user_id = user.id

    def session():
        with Session(engine) as db:
            db.info["company"] = {"id": company_id}
            yield db

    def acting():
        with Session(engine) as db:
            found = db.get(User, user_id)
            db.expunge(found)
            return found

    app.dependency_overrides[current] = acting
    app.dependency_overrides[db_session] = session
    target = OUT / label
    target.mkdir(parents=True, exist_ok=True)
    # Sem o arranque da aplicação: não liga o agendador (sincronizações em segundo plano).
    client = TestClient(app)
    if True:
        for name, start, end, compare in PERIODS:
            for path, params in (("/api/dashboard/overview", {"start": start, "end": end, "compare": compare, "store_id": ""}),
                                 ("/api/order-cycle/analytics", {"start": start, "end": end, "store_id": ""})):
                response = client.get(path, params=params)
                kind = "overview" if "overview" in path else "encomendas"
                (target / f"{name}-{kind}.json").write_text(
                    json.dumps({"status": response.status_code, "params": params, "body": response.json()}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                print(f"{name:26} {kind:10} {response.status_code}")
    print(f"Gravado em {target}")


def strip(value):
    if isinstance(value, dict):
        return {k: strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [strip(v) for v in value]
    return value


def diff(left, right, path=""):
    """(alterados ou removidos, acrescentados): um campo novo na versão nova não é uma regressão."""
    changed, added = [], []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in left:
            if key not in right:
                changed.append(f"{path}/{key} removido")
            else:
                c, a = diff(left[key], right[key], f"{path}/{key}")
                changed += c
                added += a
        added += [f"{path}/{key}" for key in right if key not in left]
    elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        for index, (x, y) in enumerate(zip(left, right)):
            c, a = diff(x, y, f"{path}[{index}]")
            changed += c
            added += a
    elif left != right:
        changed.append(f"{path}: {json.dumps(left, ensure_ascii=False)[:80]} → {json.dumps(right, ensure_ascii=False)[:80]}")
    return changed, added


def compare(a, b):
    """Compara duas recolhas (ignora campos dependentes do momento): valores alterados/removidos são regressões."""
    regressions = 0
    for first in sorted((OUT / a).glob("*.json")):
        second = OUT / b / first.name
        if not second.exists():
            print(f"FALTA em {b}: {first.name}")
            regressions += 1
            continue
        changed, added = diff(strip(json.loads(first.read_text(encoding="utf-8"))), strip(json.loads(second.read_text(encoding="utf-8"))))
        if changed:
            regressions += 1
            print(f"DIFERENTE: {first.name}")
            for line in changed[:20]:
                print(f"   · {line}")
        if added:
            print(f"acrescentado em {first.name}: {', '.join(sorted({x.split('[')[0] for x in added}))}")
    print("Sem diferenças nos valores existentes." if not regressions else f"{regressions} ficheiro(s) com diferenças.")
    return regressions


if __name__ == "__main__":
    if sys.argv[1:2] == ["--compare"]:
        sys.exit(1 if compare(sys.argv[2], sys.argv[3]) else 0)
    collect(*sys.argv[1:4])
