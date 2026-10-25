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
    WintouchPaymentMethod,
    WintouchPaymentTerm,
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
        # Morada principal (moradas juntas por with_addresses): morada, localidade, código postal e contactos.
        if isinstance(row.get("EntityAddresses"), list):
            main = main_address(row["EntityAddresses"]) or {}
            lines = [str(main.get(k) or "").strip() for k in ("Address1", "Address2")]
            values["address"] = string("\n".join(line for line in lines if line), 500) or ""
            values["locality"] = string(main.get("City"), 120) or ""
            values["postal_code"] = string(main.get("PostalCode"), 20) or ""
            if not values.get("email"):
                values["email"] = string(main.get("Email"), 200) or ""
            if not values.get("phone"):
                values["phone"] = string(main.get("Telephone") or main.get("MobilePhone"), 60) or ""
        # Condições de pagamento confirmadas na listagem /entities (cliente e fornecedor).
        for source, target in [
            ("PaymentTermID", "payment_term_external_id"),
            ("PaymentMethodID", "payment_method_external_id"),
            ("SupplierPaymentTermID", "supplier_payment_term_external_id"),
            ("SupplierPaymentMethodID", "supplier_payment_method_external_id"),
        ]:
            if source in row:
                values[target] = string(row[source], 200)
        # IsClient e IsSupplier foram confirmados na resposta da API configurada.
    return values


def main_address(addresses):
    """Morada principal de uma entidade Wintouch (a primeira, se nenhuma estiver marcada)."""
    rows = [a for a in addresses or [] if isinstance(a, dict)]
    return next((a for a in rows if a.get("IsMainAddress")), rows[0] if rows else None)


def addresses_by_entity(rows):
    """Moradas da Wintouch agrupadas por entidade (lidas à parte: ver with_addresses)."""
    grouped = {}
    for address in rows:
        if isinstance(address, dict) and address.get("EntityID"):
            grouped.setdefault(str(address["EntityID"]), []).append(address)
    return grouped


def with_addresses(rows, grouped):
    """Junta a cada entidade as suas moradas, como faria embed=EntityAddresses.

    A Wintouch recusa listas de entidades com embed=EntityAddresses quando há muitas entidades
    (HTTP 500 «O nº de registos é demasiado elevado»), mesmo pedindo uma de cada vez; a lista de
    moradas (/addresses) lê-se sem esse limite. Entidades sem moradas na leitura ficam sem o campo,
    para não apagar a morada guardada (ex.: entidade criada entre as duas leituras).
    """
    return [
        {**row, "EntityAddresses": grouped[str(row.get("ID"))]}
        if isinstance(row, dict) and str(row.get("ID")) in grouped
        else row
        for row in rows
    ]


def catalog_rows(client, url, path, key, enterprise, maximum=5000):
    """Lê um catálogo paginado; só GET."""
    items = []
    while True:
        response = client.get(
            url + path,
            params={"limit": 200, "skip": len(items)},
            headers={
                "Authorization": "ApiKey " + key,
                "Accept": "application/json",
                "x-current-enterprise": enterprise,
            },
        )
        if response.status_code != 200:
            raise SyncError(f"{path} respondeu com HTTP {response.status_code}.")
        payload = response.json()
        rows = payload if isinstance(payload, list) else next(
            (payload[k] for k in ("items", "Items", "data", "Data") if isinstance(payload.get(k), list)),
            None,
        ) if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise SyncError(f"Formato de {path} não reconhecido.")
        if not rows:
            return items
        items.extend(rows)
        if len(items) > maximum:
            raise SyncError(f"{path} excede {maximum} registos.")


def term_days(value):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 3650:
        raise SyncError("O número de dias do prazo de pagamento é inválido.")
    return value


def sync_payment_catalogs(db, client, url, key, enterprise, run_id):
    """Atualiza prazos e meios de pagamento referidos pelas entidades.

    Uma falha aqui não anula as entidades já lidas: devolve um aviso para o histórico.
    """
    try:
        with db.begin_nested():
            for path, model in (("/payment_terms", WintouchPaymentTerm), ("/payment_methods", WintouchPaymentMethod)):
                existing = {
                    item.external_id: item
                    for item in db.scalars(select(model).where(model.enterprise_id == enterprise))
                }
                for row in catalog_rows(client, url, path, key, enterprise):
                    if not isinstance(row, dict):
                        raise SyncError(f"{path} contém um registo inválido.")
                    values = {
                        "external_id": string(row.get("ID"), 200, True),
                        "name": string(row.get("Name"), 200, True),
                    }
                    if model is WintouchPaymentTerm:
                        values.update(
                            code=string(row.get("Code"), 200),
                            days=term_days(row.get("Days")),
                            term_type=string(row.get("TermType"), 20),
                        )
                    item = existing.get(values["external_id"])
                    if item is None:
                        item = model(enterprise_id=enterprise, **values)
                        db.add(item)
                    else:
                        for field, value in values.items():
                            setattr(item, field, value)
                    item.sync_run_id = run_id
                    item.synced_at = now()
            db.flush()
    except (SyncError, httpx.HTTPError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, SyncError) else "falha de comunicação"
        return f"Entidades importadas, mas os prazos/meios de pagamento não foram atualizados ({detail})."
    return ""


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
                # Moradas lidas à parte (a lista de entidades com embed falha com muitas entidades).
                addresses = (
                    addresses_by_entity(catalog_rows(client, url, "/addresses", key, enterprise, maximum=50000))
                    if resource == "entities"
                    else None
                )
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
                    if addresses is not None:
                        rows = with_addresses(rows, addresses)
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
                if resource == "entities":
                    warning = sync_payment_catalogs(db, client, url, key, enterprise, run_id)
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
            if resource == "entities" and warning:
                run.error_message = warning
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
