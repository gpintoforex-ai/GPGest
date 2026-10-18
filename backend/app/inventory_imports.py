"""Previewed CSV catalogue import and explicit SAF-T -> stock policy."""

import csv
import hashlib
import io
import json
from datetime import date
from decimal import Decimal as D

from fastapi import Depends
from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session
from .inventory import (
    ArticleInput,
    MovementInput,
    article,
    document_key,
    dump,
    fail,
    lock,
    post,
    require,
    save_article,
    settings,
    warehouse,
)
from .models import (
    StockArticle,
    StockEntry,
    StockImport,
    StockLot,
    WintouchProduct,
    WintouchSaleRecord,
)
from .schemas import Strict


class CSVPreview(Strict):
    content: str = Field(max_length=2_000_000)
    mapping: dict[str, str]
    delimiter: str = ";"


class Confirm(Strict):
    confirmed: bool


class SourceApply(Confirm):
    warehouse_id: str
    start: date
    end: date


class ERPAttach(Strict):
    erp_id: str


class SourceReview(Confirm):
    record_id: str
    key: str
    fingerprint: str
    reason: str = Field(min_length=3, max_length=2000)


class LotEdit(Strict):
    expiry: date | None = None
    deadline: str = "validade"
    reason: str = Field(min_length=3, max_length=2000)


def source_lines(db, stores, start, end):
    result = []
    query = select(WintouchSaleRecord).where(
        WintouchSaleRecord.kind == "saft_invoice",
        WintouchSaleRecord.store_id.in_(stores),
        WintouchSaleRecord.document_date >= start.isoformat(),
        WintouchSaleRecord.document_date <= end.isoformat(),
    )
    mapping = settings(db)["saft_mapping"]
    posted = {
        r.request_key: r
        for r in db.scalars(select(StockEntry).where(StockEntry.request_key.like("saft-stock:%")))
    }
    reviewed = set()
    for review in db.scalars(select(StockImport).where(StockImport.status == "source_review")):
        acknowledgement = json.loads(review.data)
        reviewed.add((acknowledgement["key"], acknowledgement["fingerprint"]))
    by_record = {}
    for previous in posted.values():
        by_record.setdefault(json.loads(previous.data).get("source_record"), []).append(previous)
    for snapshot in db.scalars(query):
        data = json.loads(snapshot.data)
        previous_rows = by_record.get(snapshot.id, [])
        invalid = (
            not snapshot.active
            or data.get("status") in ("A", "F", "R", "S")
            or data.get("type") not in ("FT", "FS", "FR", "NC")
        )
        if invalid and not previous_rows:
            continue
        keys = set()
        lines = data.get("raw", {}).get("Line", [])
        if isinstance(lines, dict):
            lines = [lines]
        for index, line in enumerate(lines):
            # Use document/line identity across revised files, not file/import identity.
            identifier = (
                "saft-stock:"
                + hashlib.sha256(
                    f"{snapshot.enterprise_id}:{snapshot.external_id}:{line.get('LineNumber', index)}".encode()
                ).hexdigest()
            )
            keys.add(identifier)
            fingerprint = hashlib.sha256(
                dump(
                    {
                        "line": line,
                        "status": data.get("status"),
                        "type": data.get("type"),
                        "date": data.get("date"),
                        "active": snapshot.active,
                    }
                ).encode()
            ).hexdigest()
            quantity = D(str(line.get("Quantity", "0")))
            value = line.get("CreditAmount", line.get("DebitAmount"))
            net = D(str(value)) if value is not None else None
            code = str(line.get("ProductCode", ""))
            previous = posted.get(identifier)
            if invalid and not previous:
                continue
            unchanged = (
                previous
                and not invalid
                and json.loads(previous.data).get("source_fingerprint") == fingerprint
            )
            result.append(
                {
                    "key": identifier,
                    "fingerprint": fingerprint,
                    "record_id": snapshot.id,
                    "store_id": snapshot.store_id,
                    "document": data.get("document", ""),
                    "date": data.get("date"),
                    "code": code,
                    "description": line.get("ProductDescription", ""),
                    "quantity": str(quantity),
                    "net_sales": str(net) if net is not None else None,
                    "type": data.get("type"),
                    "article_id": mapping.get(code),
                    "state": "posted"
                    if unchanged
                    else "reviewed"
                    if previous and (identifier, fingerprint) in reviewed
                    else "conflict"
                    if previous
                    else "unmapped"
                    if not mapping.get(code)
                    else "ready",
                }
            )
        for previous in previous_rows:
            if previous.request_key in keys:
                continue
            fingerprint = hashlib.sha256(
                dump({"removed": previous.request_key, "source": data}).encode()
            ).hexdigest()
            result.append(
                {
                    "key": previous.request_key,
                    "fingerprint": fingerprint,
                    "record_id": snapshot.id,
                    "store_id": snapshot.store_id,
                    "document": data.get("document", ""),
                    "date": data.get("date"),
                    "code": "Linha removida na origem",
                    "description": "Rever movimento já registado; não foi alterado automaticamente.",
                    "quantity": str(abs(previous.quantity)),
                    "net_sales": str(previous.net_sales),
                    "type": data.get("type"),
                    "article_id": previous.article_id,
                    "state": "reviewed"
                    if (previous.request_key, fingerprint) in reviewed
                    else "conflict",
                }
            )
    return result


def register_imports(app, current, authorize, scope, audit):
    @app.post("/api/stock/csv/preview")
    def preview(data: CSVPreview, user=Depends(current), db: Session = Depends(db_session)):
        config = settings(db)
        require(user, config, "adjust_stock")
        catalogue = list(db.scalars(select(StockArticle)))
        if data.delimiter not in (";", ",", "\t"):
            fail("Separador CSV inválido.")
        allowed = set(ArticleInput.model_fields) - {"revision", "supplier_id", "photo"}
        if any(key not in allowed for key in data.mapping):
            fail("Associação de colunas não suportada.")
        rows, errors, seen, barcodes = [], [], set(), set()
        reader = csv.DictReader(
            io.StringIO(data.content.lstrip("\ufeff")), delimiter=data.delimiter
        )
        if (
            not {"code", "name"}.issubset(data.mapping)
            or not reader.fieldnames
            or any(v not in reader.fieldnames for v in data.mapping.values())
        ):
            fail("Associe as colunas de código e designação existentes no ficheiro.")
        decimal_fields = {"vat_rate", "sale_net", "minimum", "reorder"}
        bool_fields = {"lots", "expiry", "active"}
        for index, raw in enumerate(reader, 2):
            if index > 2001:
                fail("Limite de 2000 artigos por importação. Divida o ficheiro.")
            values = {key: raw.get(column, "") or "" for key, column in data.mapping.items()}
            try:
                for key in decimal_fields & values.keys():
                    values[key] = values[key].replace(" ", "").replace(",", ".") or "0"
                for key in bool_fields & values.keys():
                    values[key] = values[key].strip().lower() in ("1", "true", "sim", "s", "ativo")
                if "barcodes" in values:
                    values["barcodes"] = [
                        v.strip() for v in values["barcodes"].split("|") if v.strip()
                    ]
                parsed = ArticleInput(**values)
                if parsed.code.casefold() in seen or set(parsed.barcodes) & barcodes:
                    raise ValueError("Código ou código de barras repetido no ficheiro.")
                seen.add(parsed.code.casefold())
                barcodes.update(parsed.barcodes)
                # Same validator, without inserting or a network/savepoint round-trip per row.
                save_article(db, user, parsed, catalogue=catalogue, config=config, preview=True)
                rows.append(parsed.model_dump(mode="json"))
            except (ValidationError, ValueError) as error:
                errors.append({"line": index, "message": str(error)[:500]})
            except Exception as error:
                from fastapi import HTTPException

                if not isinstance(error, HTTPException):
                    raise
                errors.append({"line": index, "message": str(error.detail)})
        record = StockImport(user_id=user.id, data=dump({"rows": rows, "errors": errors}))
        db.add(record)
        db.flush()
        db.commit()
        return {
            "id": record.id,
            "rows": rows,
            "errors": errors,
            "can_confirm": bool(rows) and not errors,
        }

    @app.post("/api/stock/csv/{identifier}/confirm")
    def confirm_csv(
        identifier: str, data: Confirm, user=Depends(current), db: Session = Depends(db_session)
    ):
        require(user, settings(db), "adjust_stock")
        lock(db)
        record = db.get(StockImport, identifier)
        if not record or record.user_id != user.id:
            fail("Pré-visualização não encontrada para este utilizador e empresa.", 404)
        if not data.confirmed:
            fail("Confirme a importação.")
        content = json.loads(record.data)
        if record.status == "completed":
            return {"imported": len(content["rows"]), "already_applied": True}
        if content["errors"] or not content["rows"]:
            fail("Corrija o ficheiro antes de importar.")
        catalogue, config = list(db.scalars(select(StockArticle))), settings(db)
        for row in content["rows"]:
            catalogue.append(
                save_article(
                    db, user, ArticleInput(**row), catalogue=catalogue, config=config, flush=False
                )
            )
        record.status = "completed"
        audit(db, user, "stock_csv_import", record.id, {"articles": len(content["rows"])})
        db.commit()
        return {"imported": len(content["rows"])}

    @app.get("/api/stock/erp-products")
    def erp_products(user=Depends(current), db: Session = Depends(db_session)):
        linked = {
            r.erp_id
            for r in db.scalars(select(StockArticle).where(StockArticle.erp_id.is_not(None)))
        }
        return {
            "rows": [
                {
                    "id": p.id,
                    "code": p.code,
                    "name": p.name,
                    "synced_at": p.synced_at,
                    "linked": p.id in linked,
                }
                for p in db.scalars(select(WintouchProduct).order_by(WintouchProduct.name))
            ],
            "stock": "Indisponível: o conector Wintouch não fornece quantidades. Os artigos associados são apenas de leitura e não admitem movimentos locais.",
        }

    @app.post("/api/stock/erp-products")
    def attach_erp(data: ERPAttach, user=Depends(current), db: Session = Depends(db_session)):
        require(user, settings(db), "adjust_stock")
        lock(db)
        erp = db.get(WintouchProduct, data.erp_id)
        if not erp:
            fail("Artigo ERP não encontrado.", 404)
        existing = db.scalar(select(StockArticle).where(StockArticle.erp_id == erp.id))
        if existing:
            return {"id": existing.id}
        row = save_article(
            db, user, ArticleInput(code=erp.code, name=erp.name, unit=erp.unit or "un")
        )
        row.erp_id = erp.id
        audit(db, user, "stock_erp_link", row.id, {"erp_id": erp.id})
        db.commit()
        return {"id": row.id}

    @app.put("/api/stock/lots/{identifier}")
    def edit_lot(
        identifier: str, data: LotEdit, user=Depends(current), db: Session = Depends(db_session)
    ):
        require(user, settings(db), "manage_lots")
        lock(db)
        row = db.get(StockLot, identifier)
        if not row:
            fail("Lote não encontrado.", 404)
        article(db, row.article_id, True)
        positions = list(db.scalars(select(StockEntry).where(StockEntry.lot_id == row.id)))
        for position in positions:
            warehouse(db, user, position.warehouse_id, authorize)
        if data.deadline not in ("validade", "consumo_preferencial"):
            fail("Tipo de prazo inválido.")
        old = row.expiry
        row.expiry = data.expiry.isoformat() if data.expiry else None
        row.data = dump({**json.loads(row.data), "deadline": data.deadline})
        audit(
            db,
            user,
            "stock_lot_expiry",
            row.id,
            {"before": old, "after": row.expiry, "reason": data.reason},
        )
        db.commit()
        return {"saved": True}

    @app.get("/api/stock/saft")
    def source_preview(
        start: date, end: date, user=Depends(current), db: Session = Depends(db_session)
    ):
        return {
            "rows": source_lines(db, scope(user, db), start, end),
            "control_start": settings(db)["control_start"],
            "limitations": "Sem compras, lotes ou stock inicial no SAF-T. Só artigos associados sem lotes admitem saídas automáticas. Notas de crédito exigem uma devolução manual ligada à venda para recuperar o custo original.",
        }

    @app.post("/api/stock/saft/apply")
    def apply_source(data: SourceApply, user=Depends(current), db: Session = Depends(db_session)):
        lock(db)
        config = settings(db)
        require(user, config, "adjust_stock")
        if (
            not data.confirmed
            or not config["control_start"]
            or data.start.isoformat() < config["control_start"]
            or data.start > data.end
        ):
            fail("Configure a data de início e confirme um período dentro do controlo de stock.")
        target = warehouse(db, user, data.warehouse_id, authorize)
        rows = source_lines(db, [target.store_id], data.start, data.end)
        applied, skipped = 0, []
        for source in rows:
            if source["state"] == "conflict":
                fail(
                    "Um documento já integrado foi alterado no SAF-T. Reveja o histórico e registe ajustes explícitos antes de continuar.",
                    409,
                )
            if source["state"] in ("posted", "reviewed"):
                continue
            if (
                source["state"] != "ready"
                or source["type"] == "NC"
                or source["net_sales"] is None
                or D(source["quantity"]) <= 0
            ):
                skipped.append(source["key"])
                continue
            product = article(db, source["article_id"], True)
            if json.loads(product.data).get("lots"):
                skipped.append(source["key"])
                continue
            # Cross-origin duplicate check: manual document/article sale already posted.
            existing = [
                r
                for r in db.scalars(
                    select(StockEntry).where(
                        StockEntry.article_id == product.id, StockEntry.kind == "sale"
                    )
                )
                if document_key(json.loads(r.data).get("document", ""))
                == document_key(source["document"])
                and not r.request_key.startswith("saft-stock:")
            ]
            if existing:
                fail(
                    f"«{source['document']}» já tem uma venda manual deste artigo. Não foi criada uma duplicação; reveja a associação.",
                    409,
                )
            result = post(
                db,
                user,
                MovementInput(
                    article_id=product.id,
                    warehouse_id=target.id,
                    kind="sale",
                    quantity=source["quantity"],
                    net_sales=source["net_sales"],
                    occurred_on=source["date"],
                    document=source["document"],
                    reason="Venda SAF-T com política explícita",
                    request_key=source["key"],
                ),
                authorize,
                source="SAF-T",
            )
            for movement in result:
                movement.data = dump(
                    {
                        **json.loads(movement.data),
                        "source_fingerprint": source["fingerprint"],
                        "source_record": source["record_id"],
                    }
                )
            applied += 1
        audit(
            db,
            user,
            "stock_saft_apply",
            target.id,
            {
                "start": data.start.isoformat(),
                "end": data.end.isoformat(),
                "applied": applied,
                "skipped": skipped,
            },
            target.store_id,
        )
        db.commit()
        return {"applied": applied, "skipped": len(skipped)}

    @app.post("/api/stock/saft/resolve")
    def review_source(data: SourceReview, user=Depends(current), db: Session = Depends(db_session)):
        require(user, settings(db), "adjust_stock")
        lock(db)
        record = db.get(WintouchSaleRecord, data.record_id)
        if not record:
            fail("Documento de origem não encontrado nesta empresa.", 404)
        authorize(user, db, record.store_id)
        on = date.fromisoformat(record.document_date)
        current_line = next(
            (row for row in source_lines(db, [record.store_id], on, on) if row["key"] == data.key),
            None,
        )
        if (
            not data.confirmed
            or not current_line
            or current_line["fingerprint"] != data.fingerprint
            or current_line["state"] not in ("conflict", "reviewed")
        ):
            fail("A origem mudou. Consulte novamente e reveja a versão atual.", 409)
        if current_line["state"] == "reviewed":
            return {"reviewed": True}
        row = StockImport(
            user_id=user.id,
            status="source_review",
            data=dump(
                {
                    "key": data.key,
                    "fingerprint": data.fingerprint,
                    "record_id": record.id,
                    "reason": data.reason,
                    "decision": "keep_ledger_no_repost",
                }
            ),
        )
        db.add(row)
        db.flush()
        audit(
            db,
            user,
            "stock_saft_review",
            row.id,
            {
                "key": data.key,
                "reason": data.reason,
                "decision": "Preservar movimentos; sem nova saída automática",
            },
            record.store_id,
        )
        db.commit()
        return {"reviewed": True}
