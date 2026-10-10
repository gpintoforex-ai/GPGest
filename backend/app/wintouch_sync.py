"""Importação manual e atómica de catálogos, exclusivamente por GET."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import monotonic

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    WintouchCashAccount,
    WintouchEntity,
    WintouchProduct,
    WintouchSyncRun,
    WintouchSyncState,
    now,
)

RESOURCES = {
    "products": WintouchProduct,
    "entities": WintouchEntity,
    "cash_accounts": WintouchCashAccount,
}


class SyncError(Exception):
    pass


def string(value, maximum, required=False):
    if value is None:
        if required:
            raise SyncError("A resposta contém um registo sem ID ou nome obrigatório.")
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise SyncError("A resposta contém um campo com formato não suportado.")
    result = str(value).strip()
    if len(result) > maximum or (required and not result):
        raise SyncError("A resposta contém um campo vazio ou demasiado longo.")
    return result or None


def normalize(resource, row):
    if not isinstance(row, dict):
        raise SyncError("A resposta contém um registo inválido.")
    values = {
        "external_id": string(row.get("ID"), 200, True),
        "name": string(row.get("Name"), 200 if resource == "cash_accounts" else 300, True),
    }
    if "Code" in row or resource == "products":
        values["code"] = string(row.get("Code"), 200, resource == "products")
    if "Enabled" in row:
        if not isinstance(row["Enabled"], bool):
            raise SyncError("O estado Enabled recebido não é booleano.")
        values["active"] = row["Enabled"]
    if row.get("LastUpdate"):
        try:
            stamp = datetime.fromisoformat(row["LastUpdate"].replace("Z", "+00:00"))
            # O fuso de datas sem offset não foi confirmado; não o inventar.
            if stamp.tzinfo is not None:
                values["source_updated_at"] = stamp.astimezone(timezone.utc)
        except (ValueError, TypeError, AttributeError):
            raise SyncError("A data LastUpdate recebida é inválida.") from None
    if isinstance(row.get("Version"), str):
        values["source_version"] = string(row["Version"], 200)
    if resource == "products":
        if row.get("LastCostGlobal") is not None:
            try:
                cost = Decimal(str(row["LastCostGlobal"]))
                if (
                    not cost.is_finite()
                    or abs(cost) >= Decimal("100000000000000")
                    or cost != cost.quantize(Decimal(".0001"))
                ):
                    raise ValueError()
                values["last_cost"] = cost
            except (ValueError, ArithmeticError):
                raise SyncError(
                    "O custo recebido excede a precisão de quatro casas decimais."
                ) from None
        if "IsBatchesEnabled" in row:
            if not isinstance(row["IsBatchesEnabled"], bool):
                raise SyncError("O controlo de lotes recebido é inválido.")
            values["batches_enabled"] = row["IsBatchesEnabled"]
    elif resource == "entities":
        for source, target in [("IsClient", "is_customer"), ("IsSupplier", "is_supplier")]:
            if source in row:
                if not isinstance(row[source], bool):
                    raise SyncError("A classificação da entidade recebida não é booleana.")
                values[target] = row[source]
        for source, target, maximum in [
            ("VATNumber", "tax_number", 40),
            ("Email", "email", 200),
            ("Telephone", "phone", 60),
        ]:
            if source in row:
                values[target] = string(row[source], maximum)
        # IsClient e IsSupplier foram confirmados na resposta da API configurada.
    return values


def synchronize(bind, enterprise, resource, url, key):
    model = RESOURCES[resource]
    with Session(bind, expire_on_commit=False) as db:
        db.execute(
            update(WintouchSyncRun)
            .where(
                WintouchSyncRun.enterprise_id == enterprise,
                WintouchSyncRun.resource == resource,
                WintouchSyncRun.status == "em_curso",
                WintouchSyncRun.started_at < now() - timedelta(minutes=10),
            )
            .values(
                status="falhou",
                finished_at=now(),
                error_message="Execução interrompida ou expirada. Pode repetir a importação.",
            )
        )
        db.commit()
        run = WintouchSyncRun(enterprise_id=enterprise, resource=resource, status="em_curso")
        db.add(run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise SyncError("Já existe uma sincronização em curso para este recurso.") from None
        run_id = run.id
        seen = set()
        inserted = updated = 0
        reported_total = None
        deadline = monotonic() + 180
        try:
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                while True:
                    if monotonic() >= deadline or len(seen) > 50000:
                        raise SyncError(
                            "A sincronização atingiu o limite de duração ou de 50 000 registos. Nenhum dado desta execução foi importado."
                        )
                    response = client.get(
                        url + "/" + resource,
                        params={"limit": 200, "skip": len(seen)},
                        headers={
                            "Authorization": "ApiKey " + key,
                            "Accept": "application/json",
                            "x-current-enterprise": enterprise,
                        },
                    )
                    if response.status_code != 200:
                        raise SyncError(
                            f"A API respondeu com HTTP {response.status_code}. Verifique as permissões da ligação."
                        )
                    try:
                        payload = json.loads(response.text, parse_float=Decimal)
                    except (ValueError, UnicodeError):
                        raise SyncError("A API não devolveu JSON válido.") from None
                    rows = payload
                    if isinstance(payload, dict):
                        if payload.get("success") is False or payload.get("Success") is False:
                            raise SyncError("A API indicou uma falha na resposta da coleção.")
                        for total_key in ("total", "Total", "totalCount", "TotalCount"):
                            if total_key in payload:
                                total = payload[total_key]
                                if (
                                    not isinstance(total, int)
                                    or isinstance(total, bool)
                                    or total < 0
                                ):
                                    raise SyncError("O total de registos recebido é inválido.")
                                if reported_total is not None and reported_total != total:
                                    raise SyncError(
                                        "O total da coleção mudou durante a importação. Repita a sincronização."
                                    )
                                reported_total = total
                        rows = next(
                            (
                                payload[k]
                                for k in ("items", "Items", "data", "Data")
                                if isinstance(payload.get(k), list)
                            ),
                            None,
                        )
                    if not isinstance(rows, list):
                        raise SyncError(
                            "Formato de coleção não reconhecido. É necessária a validação da resposta da API."
                        )
                    if not rows:
                        if reported_total is not None and len(seen) != reported_total:
                            raise SyncError(
                                "A paginação terminou antes de receber todos os registos indicados pela API."
                            )
                        break
                    if len(rows) > 200 or len(seen) + len(rows) > 50000:
                        raise SyncError("A API não respeitou os limites de paginação.")
                    page = [normalize(resource, r) for r in rows]
                    ids = [r["external_id"] for r in page]
                    if len(set(ids)) != len(ids) or seen.intersection(ids):
                        raise SyncError(
                            "A API repetiu IDs entre páginas. A importação foi anulada para evitar um catálogo incompleto."
                        )
                    existing = {
                        item.external_id: item
                        for item in db.scalars(
                            select(model).where(
                                model.enterprise_id == enterprise, model.external_id.in_(ids)
                            )
                        )
                    }
                    for values in page:
                        item = existing.get(values["external_id"])
                        if item is None:
                            item = model(enterprise_id=enterprise, **values)
                            db.add(item)
                            inserted += 1
                        else:
                            for field, value in values.items():
                                setattr(item, field, value)
                            updated += 1
                        item.sync_run_id = run_id
                        item.synced_at = now()
                    db.flush()
                    seen.update(ids)
            state = db.scalar(
                select(WintouchSyncState).where(
                    WintouchSyncState.enterprise_id == enterprise,
                    WintouchSyncState.resource == resource,
                )
            )
            if state is None:
                state = WintouchSyncState(enterprise_id=enterprise, resource=resource)
                db.add(state)
            state.last_run_id = run_id
            state.last_success_at = now()
            state.cursor = None
            run.status = "concluida"
            run.finished_at = now()
            run.read_count = len(seen)
            run.inserted_count = inserted
            run.updated_count = updated
            db.commit()
        except Exception as exc:
            db.rollback()
            run = db.get(WintouchSyncRun, run_id)
            run.status = "falhou"
            run.finished_at = now()
            run.read_count = len(seen)
            run.error_message = (
                str(exc)
                if isinstance(exc, SyncError)
                else "Falha de comunicação ou gravação. Nenhum dado desta execução foi importado."
            )
            db.commit()
        return {
            "id": run.id,
            "resource": resource,
            "status": run.status,
            "read_count": run.read_count,
            "inserted_count": run.inserted_count,
            "updated_count": run.updated_count,
            "error_message": run.error_message,
            "finished_at": run.finished_at,
        }
