"""Local SAF-T staging, explicit conflict resolution and atomic source snapshots."""

import hashlib
import json
import os
import tempfile
import threading
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import db_session
from .models import (
    BusinessEntity,
    CompanySetup,
    IntegrationBinding,
    SaftImport,
    SaftImportItem,
    Store,
    WintouchSaleRecord,
    WintouchSyncRun,
    now,
)
from .operating_modes import policy
from .saft_parser import (
    INVOICE_TYPES,
    PAYMENT_CODES,
    VERSION,
    SaftError,
    canonical,
    extract,
    validate,
)
from .schemas import Strict

MAX_BYTES = min(500_000_000, max(64_000, int(os.getenv("SAFT_MAX_FILE_BYTES", "100000000"))))
WORKERS = threading.BoundedSemaphore(2)


class SaftBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("path") != "/api/saft/imports"
            or scope.get("method") != "POST"
        ):
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse

        maximum = MAX_BYTES + 1024 * 1024
        try:
            length = int(dict(scope.get("headers", [])).get(b"content-length", b"0"))
        except ValueError:
            length = maximum + 1
        if length > maximum:
            return await JSONResponse(
                {"detail": "O ficheiro excede o limite SAF-T configurado."}, status_code=413
            )(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum:
                raise HTTPException(413, "O ficheiro excede o limite SAF-T configurado.")
            return message

        await self.app(scope, limited_receive, send)


def compact(data):
    if data is None:
        return None
    return {
        **{
            key: value
            for key, value in data.items()
            if key not in {"raw", "payments", "reference_details"}
        },
        "raw": {
            key: data["raw"][key]
            for key in ("CompanyName", "ProductDescription", "CustomerTaxID")
            if key in data["raw"]
        },
    }


def previous_version(db, item):
    if not item.base_fingerprint:
        return None
    prior = db.scalar(
        select(SaftImportItem)
        .join(SaftImport)
        .where(
            SaftImportItem.identity == item.identity,
            SaftImportItem.fingerprint == item.base_fingerprint,
            SaftImportItem.outcome == "applied",
            SaftImportItem.id != item.id,
        )
        .order_by(SaftImport.created_at.desc())
        .limit(1)
    )
    if prior:
        return json.loads(prior.data)
    current = lookup(db, [item.identity], enterprise(db)).get(item.identity)
    return (
        json.loads(current.data)
        if current and current.source_version == item.base_fingerprint
        else None
    )


def enterprise(db):
    nif = str(db.info.get("company", {}).get("nif") or "").strip()
    if len(nif) != 9 or not nif.isdigit():
        raise HTTPException(422, "Configure o NIF da empresa antes de importar SAF-T.")
    return "saft:" + nif


def lookup(db, identities, source):
    return {
        row.external_id: row
        for row in db.scalars(
            select(WintouchSaleRecord).where(
                WintouchSaleRecord.enterprise_id == source,
                WintouchSaleRecord.external_id.in_(identities),
            )
        )
    }


def decision(item, current, store):
    if not current:
        return "new"
    return (
        "existing"
        if current.source_version == item.fingerprint
        and (item.kind in {"customer", "product"} or current.store_id == store)
        else "conflict"
    )


def chunks(db, identifier):
    cursor = ""
    while True:
        batch = list(
            db.scalars(
                select(SaftImportItem)
                .where(SaftImportItem.import_id == identifier, SaftImportItem.id > cursor)
                .order_by(SaftImportItem.id)
                .limit(250)
            )
        )
        if not batch:
            return
        cursor = batch[-1].id
        yield batch


def project(row):
    return {
        "id": row.id,
        "store_id": row.store_id,
        "user_id": row.user_id,
        "filename": row.filename,
        "fingerprint": row.fingerprint,
        "size_bytes": row.size_bytes,
        "status": row.status,
        "header": json.loads(row.header),
        "summary": json.loads(row.summary),
        "error": row.error,
        "revision": row.revision,
        "created_at": row.created_at,
        "processing_started_at": row.processing_started_at,
        "finished_at": row.finished_at,
    }


def review_items(db, row):
    source, counts, types, totals, codes = (
        enterprise(db),
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    for batch in chunks(db, row.id):
        existing = lookup(db, [item.identity for item in batch], source)
        for item in batch:
            current = existing.get(item.identity)
            item.decision = decision(item, current, row.store_id)
            item.base_fingerprint = current.source_version if current else None
            item.base_store = current.store_id if current else None
            counts[item.decision] += 1
            counts[item.kind] += 1
            data = json.loads(item.data)
            if item.kind in {"invoice", "receipt"}:
                types[item.kind + ":" + data["type"]] += 1
                if data["status"] == "A":
                    counts["cancelled"] += 1
                if (
                    item.kind == "invoice"
                    and data["status"] == "N"
                    and data["type"] in INVOICE_TYPES
                ):
                    for field in ("gross_cents", "net_cents", "tax_cents"):
                        totals[field] += data[field] * data["direction"]
                    if data["type"] == "NC":
                        totals["credit_notes_cents"] += data["gross_cents"]
                if not data["payments"]:
                    counts["without_payments"] += 1
                for payment in data["payments"]:
                    codes[payment["code"] or "(sem código)"] += 1
        db.flush()
    row.summary = canonical(
        {
            "counts": counts,
            "types": types,
            "totals": totals,
            "payment_codes": codes,
            "warnings": [
                "Faturação não equivale a recebimentos. Informação de recebimento indisponível quando não constam pagamentos com data e meio identificados.",
                "Transportes, encomendas e movimentos contabilísticos não são importados como faturação.",
                "Estados A/F/R/S e tipos especiais fora de FT/FS/FR/ND/NC ficam disponíveis para consulta, excluídos da faturação normal.",
                "As assinaturas fiscais não são certificadas pela Zela; a validação estrutural usa o XSD oficial da AT.",
            ],
        }
    )
    row.revision += 1


def fail(db, identifier, message):
    db.rollback()
    row = db.get(SaftImport, identifier)
    if row:
        summary = json.loads(row.summary)
        counts = summary.get("counts", {})
        rejected = sum(counts.get(key, 0) for key in ("new", "existing", "conflict"))
        row.summary = canonical(
            {
                **summary,
                "result": {"imported": 0, "updated": 0, "ignored": 0, "rejected": rejected or None},
            }
        )
        row.status, row.error, row.finished_at = "failed", message, now()
        db.commit()


def prepare_worker(bind, info, identifier, filename):
    with WORKERS, Session(bind=bind, expire_on_commit=False) as db:
        db.info.update(info)
        records = None
        try:
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(current_schema() || :source))"),
                {"source": enterprise(db)},
            )
            row = db.get(SaftImport, identifier)
            if not row or row.status != "validating":
                return
            validate(filename)
            staged = []
            records = extract(filename)
            for kind, value in records:
                if kind == "header":
                    if str(value["TaxRegistrationNumber"]) != enterprise(db).split(":")[1]:
                        raise SaftError(
                            "O NIF do ficheiro não corresponde à empresa selecionada. Nenhum dado foi importado."
                        )
                    row.header = canonical(value)
                else:
                    identity, fingerprint, data = value
                    staged.append(
                        SaftImportItem(
                            import_id=row.id,
                            identity=identity,
                            kind=data["kind"],
                            fingerprint=fingerprint,
                            data=canonical(data),
                            decision="new",
                        )
                    )
                    if len(staged) == 250:
                        db.add_all(staged)
                        db.flush()
                        staged = []
            db.add_all(staged)
            db.flush()
            review_items(db, row)
            row.status, row.finished_at = "ready", now()
            db.commit()
        except (SaftError, IntegrityError) as exc:
            fail(
                db,
                identifier,
                str(exc)
                if isinstance(exc, SaftError)
                else "O XML repete a identidade de um documento ou código de catálogo. Corrija o ficheiro e importe novamente.",
            )
        except Exception:
            fail(
                db,
                identifier,
                "Falha ao processar o SAF-T. Nenhum documento foi importado. Consulte a seleção e volte a carregar o ficheiro.",
            )
        finally:
            if records:
                records.close()
            Path(filename).unlink(missing_ok=True)


def customer(db, data, prior):
    # Keep manual entity edits intact. Source CustomerID is the identity for
    # generic tax IDs; only a unique real-NIF match can share a local entity.
    existing_id = json.loads(prior.data).get("entity_id") if prior else None
    external = enterprise(db) + ":" + data["code"]
    binding = db.scalar(
        select(IntegrationBinding).where(
            IntegrationBinding.connector == "saft",
            IntegrationBinding.external_kind == "customer",
            IntegrationBinding.external_id == external,
        )
    )
    if binding:
        existing_id = binding.internal_id
    if existing_id and db.get(BusinessEntity, existing_id):
        return existing_id
    raw = data["raw"]
    nif = raw.get("CustomerTaxID", "")
    matches = (
        list(
            db.scalars(
                select(BusinessEntity).where(
                    BusinessEntity.tax_number == nif, BusinessEntity.removed.is_(False)
                )
            )
        )
        if nif
        and any(char.isdigit() for char in nif)
        and nif not in {"999999990", "999999999", "000000000", "0"}
        else []
    )
    if len(matches) == 1:
        entity = matches[0]
        entity.is_customer = True
    else:
        entity = BusinessEntity(
            name=raw["CompanyName"][:300],
            tax_number=nif[:40],
            email=raw.get("Email", "")[:200],
            phone=raw.get("Telephone", "")[:60],
            is_customer=True,
        )
        db.add(entity)
    db.flush()
    linked = db.scalar(
        select(IntegrationBinding.id).where(
            IntegrationBinding.connector == "saft",
            IntegrationBinding.internal_kind == "entity",
            IntegrationBinding.internal_id == entity.id,
        )
    )
    if not linked:
        db.add(
            IntegrationBinding(
                company_id=db.info["company"]["id"],
                connector="saft",
                external_kind="customer",
                external_id=external,
                internal_kind="entity",
                internal_id=entity.id,
                synced_at=now(),
            )
        )
    return entity.id


def confirm_worker(bind, info, identifier, choices, audit):
    with WORKERS, Session(bind=bind, expire_on_commit=False) as db:
        db.info.update(info)
        try:
            source = enterprise(db)
            # Serialize overlapping periods and catalogue identities per tenant.
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(current_schema() || :source))"),
                {"source": source},
            )
            row = db.get(SaftImport, identifier, with_for_update=True)
            if row.status != "importing":
                return
            if str(json.loads(row.header).get("TaxRegistrationNumber")) != source.split(":")[1]:
                raise SaftError(
                    "O NIF da empresa mudou. Volte a validar o ficheiro na empresa correta."
                )
            counts = Counter()
            run = WintouchSyncRun(enterprise_id=source, resource="saft_sales", status="em_curso")
            db.add(run)
            db.flush()
            for batch in chunks(db, identifier):
                existing = lookup(db, [item.identity for item in batch], source)
                for item in batch:
                    current = existing.get(item.identity)
                    if (current.source_version if current else None) != item.base_fingerprint or (
                        current.store_id if current else None
                    ) != item.base_store:
                        raise SaftError(
                            "Os documentos mudaram desde a pré-visualização. Atualize-a e reveja os conflitos antes de confirmar."
                        )
                    if item.decision == "existing" or (
                        item.decision == "conflict" and choices.get(item.id) == "keep"
                    ):
                        item.outcome = "ignored"
                        counts["ignored"] += 1
                        continue
                    data = json.loads(item.data)
                    if item.kind == "customer":
                        data["entity_id"] = customer(db, data, current)
                    if not current:
                        current = WintouchSaleRecord(
                            enterprise_id=source, external_id=item.identity
                        )
                        db.add(current)
                        counts["imported"] += 1
                    else:
                        counts["updated"] += 1
                    current.store_id, current.kind = row.store_id, "saft_" + item.kind
                    current.source_version, current.sync_run_id = item.fingerprint, run.id
                    current.document_date = data.get("date", json.loads(row.header)["StartDate"])
                    current.synced_at, current.active = now(), True
                    current.data = canonical({**data, "origin": "SAF-T", "import_id": row.id})
                    item.outcome = "applied"
                db.flush()
            run.status, run.finished_at = "concluida", now()
            run.read_count = sum(counts.values())
            run.inserted_count, run.updated_count = counts["imported"], counts["updated"]
            row.summary = canonical(
                {**json.loads(row.summary), "result": {**counts, "rejected": 0}}
            )
            row.status, row.finished_at = "completed", now()
            from .models import User

            audit(
                db,
                db.get(User, json.loads(row.summary).get("confirmed_by", row.user_id)),
                "saft_importacao_concluida",
                row.id,
                {"fingerprint": row.fingerprint, "result": dict(counts), "ledger_changed": False},
                row.store_id,
            )
            db.commit()
        except SaftError as exc:
            fail(db, identifier, str(exc))
        except Exception:
            fail(
                db,
                identifier,
                "A importação falhou e foi revertida integralmente. Atualize a pré-visualização para repetir sem duplicações.",
            )


class ConfirmInput(Strict):
    confirmed: Literal[True]
    revision: int = Field(ge=1)
    conflicts: dict[str, Literal["keep", "replace"]] = Field(default_factory=dict)


class MappingInput(Strict):
    mapping: dict[str, str] = Field(max_length=14)


def register_routes(app, current, authorize, scope, audit):
    def admin(user, db):
        authorize(user, db, roles=["administrador"])
        if policy(db)["mode"] != "local":
            raise HTTPException(409, "A importação SAF-T está disponível no modo local autónomo.")

    def get_row(db, identifier):
        row = db.get(SaftImport, identifier)
        if not row:
            raise HTTPException(404, "Importação não encontrada nesta empresa.")
        return row

    @app.get("/api/saft/imports")
    def history(user=Depends(current), db: Session = Depends(db_session)):
        admin(user, db)
        return {
            "versions": [VERSION],
            "max_bytes": MAX_BYTES,
            "imports": [
                project(row)
                for row in db.scalars(
                    select(SaftImport).order_by(SaftImport.created_at.desc()).limit(100)
                )
            ],
        }

    @app.post("/api/saft/imports")
    async def upload(
        background: BackgroundTasks,
        store_id: str = Form(...),
        file: UploadFile = File(...),
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        admin(user, db)
        enterprise(db)
        if not db.get(Store, store_id):
            raise HTTPException(404, "Estabelecimento não encontrado nesta empresa.")
        if not (file.filename or "").lower().endswith(".xml"):
            raise HTTPException(422, "Selecione um ficheiro XML SAF-T (PT), sem compactação.")
        path = None
        try:
            size, sha = 0, hashlib.sha256()
            with tempfile.NamedTemporaryFile(
                prefix="zela-saft-", suffix=".xml", delete=False
            ) as output:
                path = output.name
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(
                            413,
                            "O ficheiro excede o limite SAF-T configurado. Exporte um período menor.",
                        )
                    sha.update(chunk)
                    output.write(chunk)
            if not size:
                raise HTTPException(422, "O ficheiro XML está vazio.")
            previous = db.scalar(
                select(SaftImport)
                .where(
                    SaftImport.fingerprint == sha.hexdigest(),
                    SaftImport.store_id == store_id,
                    SaftImport.status.in_(["ready", "validating", "importing", "completed"]),
                )
                .order_by(SaftImport.created_at.desc())
            )
            if previous:
                Path(path).unlink(missing_ok=True)
                return project(previous)
            row = SaftImport(
                store_id=store_id,
                user_id=user.id,
                filename=Path(file.filename.replace("\\", "/")).name[:200],
                fingerprint=sha.hexdigest(),
                size_bytes=size,
            )
            db.add(row)
            db.flush()
            audit(
                db,
                user,
                "saft_ficheiro_recebido",
                row.id,
                {"size_bytes": size, "fingerprint": row.fingerprint},
                store_id,
            )
            db.commit()
            background.add_task(prepare_worker, db.get_bind(), dict(db.info), row.id, path)
            return project(row)
        except Exception:
            if path:
                Path(path).unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/saft/imports/{identifier}")
    def detail(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        admin(user, db)
        return project(get_row(db, identifier))

    @app.get("/api/saft/imports/{identifier}/items")
    def items(
        identifier: str,
        page: int = Query(default=1, ge=1),
        kind: str = "",
        conflicts: bool = False,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        admin(user, db)
        get_row(db, identifier)
        conditions = [SaftImportItem.import_id == identifier]
        if kind:
            conditions.append(SaftImportItem.kind == kind)
        if conflicts:
            conditions.append(SaftImportItem.decision == "conflict")
        rows = db.scalars(
            select(SaftImportItem)
            .where(*conditions)
            .order_by(SaftImportItem.identity)
            .offset((page - 1) * 30)
            .limit(30)
        )
        return {
            "total": db.scalar(select(func.count()).select_from(SaftImportItem).where(*conditions)),
            "page": page,
            "items": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "identity": item.identity,
                    "fingerprint": item.fingerprint,
                    "base_fingerprint": item.base_fingerprint,
                    "decision": item.decision,
                    "outcome": item.outcome,
                    "data": compact(json.loads(item.data)),
                    "previous": compact(previous_version(db, item)),
                }
                for item in rows
            ],
        }

    @app.get("/api/saft/imports/{identifier}/items/{item_id}")
    def item_detail(
        identifier: str, item_id: str, user=Depends(current), db: Session = Depends(db_session)
    ):
        admin(user, db)
        get_row(db, identifier)
        item = db.get(SaftImportItem, item_id)
        if not item or item.import_id != identifier:
            raise HTTPException(404, "Registo não encontrado nesta importação.")
        return {
            "id": item.id,
            "kind": item.kind,
            "fingerprint": item.fingerprint,
            "decision": item.decision,
            "data": json.loads(item.data),
            "previous": previous_version(db, item),
        }

    @app.post("/api/saft/imports/{identifier}/review")
    def review(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        admin(user, db)
        row = db.get(SaftImport, identifier, with_for_update=True)
        if not row:
            raise HTTPException(404, "Importação não encontrada nesta empresa.")
        if row.status not in {"ready", "failed"} or not db.scalar(
            select(SaftImportItem.id).where(SaftImportItem.import_id == identifier).limit(1)
        ):
            raise HTTPException(409, "Volte a carregar o ficheiro para validar antes de confirmar.")
        review_items(db, row)
        row.status, row.error = "ready", ""
        db.commit()
        return project(row)

    @app.post("/api/saft/imports/{identifier}/confirm")
    def confirm(
        identifier: str,
        data: ConfirmInput,
        background: BackgroundTasks,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        admin(user, db)
        row = db.scalar(select(SaftImport).where(SaftImport.id == identifier).with_for_update())
        if not row:
            raise HTTPException(404, "Importação não encontrada nesta empresa.")
        if row.status in {"completed", "importing"}:
            return project(row)
        if row.status != "ready" or row.revision != data.revision:
            raise HTTPException(409, "Atualize a pré-visualização antes de confirmar.")
        if str(json.loads(row.header).get("TaxRegistrationNumber")) != enterprise(db).split(":")[1]:
            raise HTTPException(
                422, "O NIF do ficheiro já não corresponde à empresa selecionada. Volte a validar."
            )
        conflict_ids = set(
            db.scalars(
                select(SaftImportItem.id).where(
                    SaftImportItem.import_id == identifier, SaftImportItem.decision == "conflict"
                )
            )
        )
        if set(data.conflicts) != conflict_ids:
            raise HTTPException(
                422,
                "Decida manter ou substituir cada registo em conflito, incluindo os das outras páginas.",
            )
        row.status, row.error, row.finished_at = "importing", "", None
        row.processing_started_at = now()
        row.summary = canonical({**json.loads(row.summary), "confirmed_by": user.id})
        db.commit()
        background.add_task(
            confirm_worker, db.get_bind(), dict(db.info), row.id, data.conflicts, audit
        )
        return project(row)

    @app.post("/api/saft/imports/{identifier}/recover")
    def recover(
        identifier: str,
        data: ConfirmInput,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        admin(user, db)
        row = get_row(db, identifier)
        if row.status not in {
            "validating",
            "importing",
        } or row.processing_started_at > now() - timedelta(minutes=30):
            raise HTTPException(
                409, "O processamento pode estar em curso. Aguarde pelo menos 30 minutos."
            )
        # Acquire the same lock: a live atomic import must finish before recovery.
        locked = db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtext(current_schema() || :source))"),
            {"source": enterprise(db)},
        )
        if not locked:
            raise HTTPException(409, "O processamento ainda está ativo. Aguarde a conclusão.")
        db.refresh(row)
        if row.status in {"validating", "importing"}:
            row.status, row.error, row.finished_at = (
                "failed",
                "Processamento interrompido. Atualize a pré-visualização ou volte a carregar o ficheiro.",
                now(),
            )
            db.commit()
        return project(row)

    @app.get("/api/saft/payment-mapping")
    def mapping(user=Depends(current), db: Session = Depends(db_session)):
        admin(user, db)
        from .cash_local import method_catalog

        settings = db.get(CompanySetup, "default")
        return {
            "mapping": json.loads(settings.settings or "{}").get("saft_payment_mapping", {})
            if settings
            else {},
            "codes": sorted(PAYMENT_CODES),
            "methods": [item for item in method_catalog(db) if item["active"]],
        }

    @app.post("/api/saft/payment-mapping")
    def save_mapping(data: MappingInput, user=Depends(current), db: Session = Depends(db_session)):
        admin(user, db)
        from .cash_local import methods

        active = methods(db, active_only=True)
        if any(
            code not in PAYMENT_CODES or key not in active for code, key in data.mapping.items()
        ):
            raise HTTPException(422, "Associe apenas códigos SAF-T e meios de pagamento ativos.")
        settings = db.get(CompanySetup, "default")
        if not settings:
            settings = CompanySetup(id="default")
            db.add(settings)
        settings.settings = canonical(
            {**json.loads(settings.settings or "{}"), "saft_payment_mapping": data.mapping}
        )
        audit(db, user, "saft_meios_associados", "default", data.mapping)
        db.commit()
        return mapping(user, db)

    @app.get("/api/saft/analysis")
    def analysis(
        start: date,
        end: date,
        store_id: str = "",
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        from .saft_reference import reference

        if policy(db)["mode"] != "local":
            raise HTTPException(409, "A referência SAF-T destina-se ao modo local autónomo.")
        if start > end or (end - start).days > 366:
            raise HTTPException(422, "Selecione um período até um ano.")
        stores = scope(user, db)
        if store_id:
            authorize(user, db, store_id)
            if store_id not in stores:
                raise HTTPException(404, "Estabelecimento não disponível.")
            stores = [store_id]
        return reference(db, stores, str(start), str(end))

    @app.get("/api/sessions/{identifier}/saft-reference")
    def sheet_reference(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        from .cash_sheet import day
        from .models import CashSession, Register
        from .saft_reference import reference

        session = db.get(CashSession, identifier)
        if not session:
            raise HTTPException(404, "Folha de caixa não encontrada nesta empresa.")
        register = db.get(Register, session.register_id)
        authorize(user, db, register.store_id)
        if policy(db)["mode"] != "local":
            raise HTTPException(409, "Esta referência destina-se ao modo local.")
        selected = session.sheet_day or day(session.opened_at)
        return reference(db, [register.store_id], selected, selected)
