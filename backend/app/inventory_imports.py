"""Previewed CSV catalogue import and explicit SAF-T -> stock policy."""

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal as D

from fastapi import Depends
from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session, request_company
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
    stock_from_erp,
    warehouse,
)
from .models import (
    StockArticle,
    StockEntry,
    StockImport,
    StockLot,
    StockWarehouse,
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


OPENING_FIELDS = {"code", "quantity", "unit_cost", "warehouse", "lot_number", "expiry"}


class OpeningPreview(Strict):
    """Inventário inicial valorizado: código, quantidade e custo unitário sem IVA (por linha)."""

    content: str = Field(max_length=4_000_000)
    mapping: dict[str, str]
    delimiter: str = ";"
    warehouse_id: str = Field(default="", max_length=36)
    occurred_on: date
    reason: str = Field(min_length=3, max_length=2000)


class SourceApply(Confirm):
    warehouse_id: str
    start: date
    end: date


class ERPAttach(Strict):
    erp_id: str


class ERPLinkByCode(Strict):
    # Sem confirmação devolve só a pré-visualização; nada é alterado.
    confirmed: bool = False
    create_missing: bool = False


def link_plan(db):
    """Associação por código entre artigos do ERP e fichas locais (sem alterar nada)."""
    company = request_company(db)
    products = [
        p
        for p in db.scalars(select(WintouchProduct))
        if not company or p.enterprise_id == company.api_enterprise
    ]
    articles = list(db.scalars(select(StockArticle)))
    linked = {a.erp_id for a in articles if a.erp_id}
    local = defaultdict(list)
    for row in articles:
        if not row.erp_id:
            local[row.code.strip().casefold()].append(row)
    codes = {row.code.strip().casefold() for row in articles}
    remote = defaultdict(list)
    for product in products:
        if product.id not in linked and product.code.strip():
            remote[product.code.strip().casefold()].append(product)
    matched, missing, ambiguous = [], [], []
    for code, group in remote.items():
        mine = local.get(code, [])
        if len(group) > 1 or len(mine) > 1:
            ambiguous.append(group[0].code)
        elif mine:
            matched.append((mine[0], group[0]))
        elif code in codes:
            # Já existe uma ficha com este código, ligada a outro artigo do ERP.
            ambiguous.append(group[0].code)
        elif group[0].active and len(group[0].code) <= 100:
            missing.append(group[0])
    local_only = sum(len(rows) for code, rows in local.items() if code not in remote)
    return products, matched, missing, ambiguous, local_only, len(linked)


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
                message = "; ".join(item.get("msg", "Valor inválido.") for item in error.detail) if isinstance(error.detail, list) else str(error.detail)
                errors.append({"line": index, "message": message})
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

    @app.post("/api/stock/opening/preview")
    def opening_preview(
        data: OpeningPreview, user=Depends(current), db: Session = Depends(db_session)
    ):
        """Valida o inventário inicial sem gravar: artigos, armazéns, lotes, custos e duplicados."""
        config = settings(db)
        require(user, config, "adjust_stock")
        require(user, config, "view_cost")
        if data.delimiter not in (";", ",", "\t"):
            fail("Separador CSV inválido.")
        if data.occurred_on > date.today():
            fail("A data do inventário não pode ser futura.")
        if any(key not in OPENING_FIELDS for key in data.mapping) or not {
            "code",
            "quantity",
            "unit_cost",
        }.issubset(data.mapping):
            fail("Associe as colunas de código, quantidade e custo unitário.")
        reader = csv.DictReader(
            io.StringIO(data.content.lstrip("\ufeff")), delimiter=data.delimiter
        )
        if not reader.fieldnames or any(
            column not in reader.fieldnames for column in data.mapping.values()
        ):
            fail("As colunas indicadas não existem no ficheiro.")
        articles = {a.code.casefold(): a for a in db.scalars(select(StockArticle))}
        erp_stock = stock_from_erp(db)
        allowed = {
            w.id: w
            for w in db.scalars(
                select(StockWarehouse).where(StockWarehouse.store_id.in_(scope(user, db)))
            )
        }
        by_name = {w.name.casefold(): w for w in allowed.values()}
        if data.warehouse_id and data.warehouse_id not in allowed:
            fail("Armazém indisponível.", 404)
        openings = {
            (e.article_id, e.warehouse_id)
            for e in db.scalars(select(StockEntry).where(StockEntry.kind == "opening"))
        }
        rows, errors, warnings, seen = [], [], [], set()
        for index, raw in enumerate(reader, 2):
            if index > 5001:
                fail("Limite de 5000 linhas por importação. Divida o ficheiro.")
            values = {key: (raw.get(column) or "").strip() for key, column in data.mapping.items()}
            if not any(values.values()):
                continue
            try:
                article_row = articles.get(values["code"].casefold())
                if not article_row:
                    raise ValueError(
                        f"Artigo «{values['code']}» sem ficha. Importe ou crie o artigo primeiro."
                    )
                if article_row.erp_id and erp_stock:
                    raise ValueError("Artigo controlado pelo ERP: o stock é gerido na origem.")
                if not article_row.active:
                    raise ValueError("Artigo inativo.")
                warehouse_row = (
                    by_name.get(values.get("warehouse", "").casefold())
                    if values.get("warehouse")
                    else allowed.get(data.warehouse_id)
                )
                if not warehouse_row:
                    raise ValueError("Armazém em falta ou fora do seu âmbito.")
                meta = json.loads(article_row.data)
                quantity = D(values["quantity"].replace(" ", "").replace(",", "."))
                if quantity <= 0:
                    raise ValueError("A quantidade tem de ser positiva.")
                if quantity != quantity.quantize(D(1).scaleb(-meta.get("precision", 0))):
                    raise ValueError("A quantidade não respeita a precisão do artigo.")
                if not values["unit_cost"]:
                    raise ValueError("Custo unitário em falta.")
                cost = D(values["unit_cost"].replace(" ", "").replace("€", "").replace(",", "."))
                if cost < 0:
                    raise ValueError("O custo não pode ser negativo.")
                lot_number = values.get("lot_number", "")
                expiry = values.get("expiry", "")
                if meta.get("lots") and not lot_number:
                    raise ValueError("Artigo com lotes: indique o lote.")
                if not meta.get("lots") and lot_number:
                    raise ValueError("Este artigo não utiliza lotes.")
                if expiry:
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                        try:
                            expiry = datetime.strptime(expiry, fmt).date().isoformat()
                            break
                        except ValueError:
                            continue
                    else:
                        raise ValueError("Validade inválida.")
                key = (article_row.id, warehouse_row.id, lot_number.casefold())
                if key in seen:
                    raise ValueError("Artigo, armazém e lote repetidos no ficheiro.")
                seen.add(key)
                if (article_row.id, warehouse_row.id) in openings:
                    raise ValueError(
                        "Este artigo já tem stock inicial neste armazém; corrija com um ajuste justificado."
                    )
                if cost == 0:
                    warnings.append(
                        {
                            "line": index,
                            "message": f"{article_row.code}: custo zero (será usado como custo conhecido).",
                        }
                    )
                rows.append(
                    {
                        "line": index,
                        "article_id": article_row.id,
                        "code": article_row.code,
                        "name": article_row.name,
                        "warehouse_id": warehouse_row.id,
                        "warehouse": warehouse_row.name,
                        "quantity": str(quantity),
                        "unit_cost": str(cost),
                        "lot_number": lot_number,
                        "expiry": expiry or None,
                        "value": str((quantity * cost).quantize(D("0.01"))),
                    }
                )
            except (ValueError, ArithmeticError) as error:
                errors.append({"line": index, "message": str(error)[:500]})
        if not rows and not errors:
            fail("O ficheiro não tem linhas.")
        record = StockImport(
            user_id=user.id,
            data=dump(
                {
                    "kind": "opening",
                    "rows": rows,
                    "errors": errors,
                    "warnings": warnings,
                    "occurred_on": data.occurred_on.isoformat(),
                    "reason": data.reason.strip(),
                }
            ),
        )
        db.add(record)
        db.commit()
        return {
            "id": record.id,
            "rows": rows,
            "errors": errors,
            "warnings": warnings,
            "can_confirm": bool(rows) and not errors,
            "total_value": str(sum((D(r["value"]) for r in rows), D(0))),
            "articles": len({r["article_id"] for r in rows}),
        }

    @app.post("/api/stock/opening/{identifier}/confirm")
    def opening_confirm(
        identifier: str, data: Confirm, user=Depends(current), db: Session = Depends(db_session)
    ):
        """Regista o inventário inicial (movimentos «stock inicial» com custo), tudo ou nada."""
        config = settings(db)
        require(user, config, "adjust_stock")
        require(user, config, "view_cost")
        lock(db)
        record = db.get(StockImport, identifier)
        if not record or record.user_id != user.id:
            fail("Pré-visualização não encontrada para este utilizador e empresa.", 404)
        content = json.loads(record.data)
        if content.get("kind") != "opening":
            fail("Esta pré-visualização não é de inventário inicial.")
        if not data.confirmed:
            fail("Confirme a importação.")
        if record.status == "completed":
            return {"imported": len(content["rows"]), "already_applied": True}
        if content["errors"] or not content["rows"]:
            fail("Corrija o ficheiro antes de importar.")
        occurred = date.fromisoformat(content["occurred_on"])
        for row in content["rows"]:
            if db.scalar(
                select(StockEntry.id).where(
                    StockEntry.kind == "opening",
                    StockEntry.article_id == row["article_id"],
                    StockEntry.warehouse_id == row["warehouse_id"],
                )
            ):
                fail(
                    f"Linha {row['line']}: {row['code']} já tem stock inicial neste armazém. Nada foi importado.",
                    409,
                )
            try:
                post(
                    db,
                    user,
                    MovementInput(
                        article_id=row["article_id"],
                        warehouse_id=row["warehouse_id"],
                        kind="opening",
                        quantity=D(row["quantity"]),
                        unit_cost=D(row["unit_cost"]),
                        occurred_on=occurred,
                        document=f"Inventário inicial {occurred.isoformat()}",
                        reason=content["reason"],
                        request_key=f"opening:{record.id}:{row['line']}",
                        lot_number=row["lot_number"],
                        expiry=date.fromisoformat(row["expiry"]) if row["expiry"] else None,
                    ),
                    authorize,
                    source="import",
                )
            except ValidationError as error:
                fail(f"Linha {row['line']}: {error.errors()[0]['msg']}. Nada foi importado.")
        record.status = "completed"
        audit(
            db,
            user,
            "stock_inventario_inicial",
            record.id,
            {
                "lines": len(content["rows"]),
                "occurred_on": content["occurred_on"],
                "total_value": str(sum((D(r["value"]) for r in content["rows"]), D(0))),
            },
        )
        db.commit()
        return {"imported": len(content["rows"])}

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
        company = request_company(db)
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
                if not company or p.enterprise_id == company.api_enterprise
            ],
            "stock": "O stock dos artigos do ERP é registado na IAZELA: o conector Wintouch não lê quantidades. Código, nome, estado e preço vêm do ERP.",
        }

    @app.post("/api/stock/erp-products")
    def attach_erp(data: ERPAttach, user=Depends(current), db: Session = Depends(db_session)):
        require(user, settings(db), "adjust_stock")
        lock(db)
        erp = db.get(WintouchProduct, data.erp_id)
        company = request_company(db)
        if not erp or (company and erp.enterprise_id != company.api_enterprise):
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

    @app.post("/api/stock/erp-products/link-by-code")
    def link_erp_by_code(
        data: ERPLinkByCode, user=Depends(current), db: Session = Depends(db_session)
    ):
        """Liga as fichas locais aos artigos do ERP com o mesmo código (e cria as que faltam)."""
        require(user, settings(db), "adjust_stock")
        if data.confirmed:
            lock(db)
        products, matched, missing, ambiguous, local_only, already = link_plan(db)
        if not products:
            fail("Sincronize primeiro os artigos do ERP em Configurações → Integrações.", 409)
        summary = {
            "erp_products": len(products),
            "already_linked": already,
            "link": len(matched),
            "create": len(missing),
            "ambiguous": len(ambiguous),
            "ambiguous_codes": sorted(ambiguous)[:20],
            "local_only": local_only,
        }
        if not data.confirmed:
            return summary
        for row, product in matched:
            # A ficha e o histórico de stock mantêm-se; código, nome e preço passam a vir do ERP.
            row.erp_id = product.id
            row.revision = (row.revision or 0) + 1
        created = 0
        if data.create_missing:
            for product in missing:
                unit = product.unit if product.unit and len(product.unit) <= 20 else "un"
                values = ArticleInput(code=product.code, name=product.name[:300], unit=unit)
                db.add(
                    StockArticle(
                        code=values.code,
                        name=values.name,
                        active=product.active,
                        erp_id=product.id,
                        revision=1,
                        data=dump(
                            values.model_dump(
                                mode="json", exclude={"code", "name", "active", "revision"}
                            )
                        ),
                    )
                )
                created += 1
        audit(
            db,
            user,
            "stock_erp_link_by_code",
            "artigos",
            {"ligados": len(matched), "criados": created, "ambiguos": len(ambiguous)},
        )
        db.commit()
        return {**summary, "linked": len(matched), "created": created}

    @app.put("/api/stock/lots/{identifier}")
    def edit_lot(
        identifier: str, data: LotEdit, user=Depends(current), db: Session = Depends(db_session)
    ):
        require(user, settings(db), "manage_lots")
        lock(db)
        row = db.get(StockLot, identifier)
        if not row:
            fail("Lote não encontrado.", 404)
        article(db, row.article_id, True, stock=True)
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
            product = article(db, source["article_id"], True, stock=True)
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
