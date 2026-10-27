from datetime import date
from typing import Literal
from urllib.parse import quote

import httpx
from cryptography.fernet import InvalidToken
from fastapi import Depends, File, HTTPException, Response, UploadFile
from pydantic import Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .db import db_session
from .models import BankAccount, BankTransaction, EfaturaConfig, Purchase, PurchaseAllocation, PurchaseAttachment, Store, WintouchEntity
from .platform import cipher
from .schemas import Strict
from .integrations.efatura import EfaturaError, configured, fetch_invoices
from .document_identity import identity


class PurchaseCreate(Strict):
    store_id: str
    supplier_nif: str = Field(pattern=r"^\d{9}$")
    supplier_name: str = Field(min_length=1, max_length=300)
    document: str = Field(min_length=1, max_length=100)
    document_type: Literal["FT", "FS", "FR", "ND", "NC"] = "FT"
    document_date: date
    amount: str = Field(max_length=30)
    notes: str = Field(max_length=5000, default="")


class Validation(Strict):
    validated: bool


class Allocation(Strict):
    transaction_id: str
    amount: str = Field(max_length=30)


class Connection(Strict):
    nif: str = Field(pattern=r"^\d{9}$")
    username: str = Field(pattern=r"^\d{9}/\d{1,10}$")
    password: str = Field(max_length=200, default="")
    environment: Literal["testes", "producao"] = "testes"

    @model_validator(mode="after")
    def matching_nif(self):
        if self.username.split("/")[0] != self.nif:
            raise ValueError("O NIF deve corresponder ao subutilizador.")
        return self


class Sync(Strict):
    start: date
    end: date

    @model_validator(mode="after")
    def monthly(self):
        if self.start > self.end or (self.start.year, self.start.month) != (self.end.year, self.end.month):
            raise ValueError("Selecione um intervalo dentro do mesmo mês.")
        return self


def paid(db, purchase_id):
    from .reconciliation import used
    return used(db, "purchase", purchase_id)


def allocated(db, transaction_id):
    from .reconciliation import used
    return used(db, "bank", transaction_id)


def register_routes(app, current, authorize, scope, record, serial, cents):
    def get_purchase(db, user, purchase_id, write=False):
        row = db.get(Purchase, purchase_id)
        if not row:
            raise HTTPException(404, "Compra não encontrada.")
        authorize(user, db, row.store_id, ["administrador", "gestor"] if write else None)
        return row

    def related(db, ids):
        # Carrega de uma vez os dados de várias compras: uma consulta por tipo, não por compra.
        from .models import DocumentScan, DriveDocumentAttachment, PaymentDocumentLine, Reconciliation, ReconciliationLine
        found = {"paid": {}, "groups": {}, "attachments": {}, "allocations": {}}
        for start in range(0, len(ids), 1000):
            chunk = ids[start:start + 1000]
            for line, group in db.execute(select(ReconciliationLine, Reconciliation).join(Reconciliation).where(ReconciliationLine.source == "purchase", ReconciliationLine.source_id.in_(chunk), Reconciliation.undone_at.is_(None))):
                found["groups"].setdefault(line.source_id, []).append({"id": group.id, "amount_cents": line.amount_cents, "reason": group.reason})
                found["paid"][line.source_id] = found["paid"].get(line.source_id, 0) + line.amount_cents
            for a in db.scalars(select(PurchaseAllocation).where(PurchaseAllocation.purchase_id.in_(chunk))):
                found["allocations"].setdefault(a.purchase_id, []).append(serial(a))
                found["paid"][a.purchase_id] = found["paid"].get(a.purchase_id, 0) + a.amount_cents
            for purchase_id, amount in db.execute(select(PaymentDocumentLine.purchase_id, func.sum(PaymentDocumentLine.amount_cents)).where(PaymentDocumentLine.purchase_id.in_(chunk)).group_by(PaymentDocumentLine.purchase_id)):
                found["paid"][purchase_id] = found["paid"].get(purchase_id, 0) + int(amount or 0)
            scans = {(r.purchase_id, r.attachment_id): r for r in db.scalars(select(DocumentScan).where(DocumentScan.purchase_id.in_(chunk), DocumentScan.status == "cloud_saved"))}
            for a in db.scalars(select(PurchaseAttachment).where(PurchaseAttachment.purchase_id.in_(chunk))):
                found["attachments"].setdefault(a.purchase_id, []).append({"id": a.id, "name": a.name})
            for a in db.scalars(select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id.in_(chunk))):
                scan = scans.get((a.purchase_id, a.id))
                found["attachments"].setdefault(a.purchase_id, []).append({"id": "drive:"+a.id, "name": a.name, "view_url": "/api/scans/"+scan.id+"/pdf" if scan else "/api/drive-attachments/"+a.id+"/view", "validation_status": scan.validation_status if scan else "", "scan_id": scan.id if scan else None})
        return found

    def projection(db, row, found=None):
        found = found or related(db, [row.id])
        linked = found["paid"].get(row.id, 0)
        total = abs(row.amount_cents)
        return {**serial(row), "paid_cents": linked, "remaining_cents": max(0, total - linked), "payment_status": "reconciliado" if linked >= total else "parcial" if linked else "por_reconciliar", "attachments": found["attachments"].get(row.id, []), "allocations": found["allocations"].get(row.id, []), "reconciliations": found["groups"].get(row.id, [])}

    @app.get("/api/purchases")
    def purchases(user=Depends(current), db: Session = Depends(db_session)):
        rows = db.scalars(select(Purchase).where(Purchase.store_id.in_(scope(user, db))).order_by(Purchase.document_date.desc(), Purchase.created_at.desc())).all()
        found = related(db, [row.id for row in rows])
        return [projection(db, row, found) for row in rows]

    @app.post("/api/purchases")
    def create(data: PurchaseCreate, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, data.store_id, ["administrador", "gestor"])
        if not db.get(Store, data.store_id):
            raise HTTPException(404, "Loja não encontrada.")
        document = data.document.strip()
        if not document or not data.supplier_name.strip():
            raise HTTPException(422, "Indique o documento e o fornecedor.")
        db.scalar(select(Store).order_by(Store.id).limit(1).with_for_update())
        if any(identity(r.supplier_nif, r.document_type, r.document) == identity(data.supplier_nif, data.document_type, document) for r in db.scalars(select(Purchase))):
            raise HTTPException(409, "Esta compra já existe.")
        row = Purchase(**data.model_dump(exclude={"amount", "document_date", "document"}), document=document, document_date=data.document_date.isoformat(), amount_cents=cents(data.amount) * (-1 if data.document_type == "NC" else 1))
        db.add(row); db.flush()
        record(db, user, "compra_criada", row.id, {"document": row.document, "amount_cents": row.amount_cents}, row.store_id)
        db.commit()
        return projection(db, row)

    @app.get("/api/purchases/import-template")
    def import_template(user=Depends(current)):
        content = "NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;IVA\n500000000;Fornecedor exemplo;FT A/1;FT;2026-10-11;123,00;23,00\n"
        return Response(content.encode("utf-8-sig"), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=modelo-compras.csv"})

    @app.post("/api/purchases/import/{store_id}")
    def import_file(store_id: str, confirm: bool = False, file: UploadFile = File(...), user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, store_id, ["administrador", "gestor"])
        if not db.get(Store, store_id):
            raise HTTPException(404, "Loja inexistente.")
        # Parsing and this SQLAlchemy session are synchronous. Run the whole
        # operation in FastAPI's worker pool, keeping the API event loop free.
        raw = file.file.read(5_000_001)
        if len(raw) > 5_000_000:
            raise HTTPException(413, "Limite de 5 MB por ficheiro.")
        return import_purchase_file(db, user, store_id, raw, file.filename or "", confirm, record)

    @app.post("/api/purchases/{purchase_id}/validate")
    def validate(purchase_id: str, data: Validation, user=Depends(current), db: Session = Depends(db_session)):
        row = get_purchase(db, user, purchase_id, True)
        row.validated = data.validated
        record(db, user, "compra_validada", row.id, {"validated": row.validated}, row.store_id)
        db.commit()
        return projection(db, row)

    @app.get("/api/purchases/bank-transactions")
    def transactions(user=Depends(current), db: Session = Depends(db_session)):
        rows = db.execute(select(BankTransaction, BankAccount).join(BankAccount).where(BankAccount.store_id.in_(scope(user, db))).order_by(BankTransaction.movement_date.desc()))
        return [{**serial(t), "store_id": a.store_id, "account_name": a.name, "available_cents": max(0, abs(t.amount_cents) - allocated(db, t.id))} for t, a in rows]

    @app.post("/api/purchases/{purchase_id}/allocate")
    def allocate(purchase_id: str, data: Allocation, user=Depends(current), db: Session = Depends(db_session)):
        transaction = db.scalar(select(BankTransaction).where(BankTransaction.id == data.transaction_id).with_for_update())
        if not transaction:
            raise HTTPException(404, "Movimento bancário não encontrado.")
        row = get_purchase(db, user, purchase_id, True)
        row = db.scalar(select(Purchase).where(Purchase.id == row.id).with_for_update().execution_options(populate_existing=True))
        from .models import ReconciliationIgnore
        if db.scalar(select(ReconciliationIgnore.id).where(ReconciliationIgnore.source == "bank", ReconciliationIgnore.source_id == transaction.id)) or db.scalar(select(ReconciliationIgnore.id).where(ReconciliationIgnore.source == "purchase", ReconciliationIgnore.source_id == row.id)):
            raise HTTPException(409, "Reative os movimentos ignorados antes de os associar.")
        account = db.get(BankAccount, transaction.account_id)
        if account.store_id != row.store_id or transaction.amount_cents * row.amount_cents >= 0:
            raise HTTPException(422, "Selecione um movimento da mesma loja e com o sentido correto.")
        if row.origin == "efatura_testes":
            raise HTTPException(422, "As compras do ambiente de testes não podem ser associadas a movimentos bancários.")
        amount = cents(data.amount)
        if amount > abs(row.amount_cents) - paid(db, row.id) or amount > abs(transaction.amount_cents) - allocated(db, transaction.id):
            raise HTTPException(409, "O valor excede o saldo da compra ou do movimento bancário.")
        link = db.scalar(select(PurchaseAllocation).where(PurchaseAllocation.purchase_id == row.id, PurchaseAllocation.transaction_id == transaction.id))
        if link:
            raise HTTPException(409, "Este movimento já está associado à compra. Desfaça a associação para corrigir o valor.")
        link = PurchaseAllocation(purchase_id=row.id, transaction_id=transaction.id, amount_cents=amount)
        db.add(link); db.flush()
        record(db, user, "compra_reconciliada", row.id, {"allocation_id": link.id, "transaction_id": transaction.id, "amount_cents": amount}, row.store_id)
        db.commit()
        return projection(db, row)

    @app.post("/api/purchases/{purchase_id}/allocations/{allocation_id}/remove")
    def remove(purchase_id: str, allocation_id: str, user=Depends(current), db: Session = Depends(db_session)):
        row = get_purchase(db, user, purchase_id, True)
        link = db.get(PurchaseAllocation, allocation_id)
        if not link or link.purchase_id != row.id:
            raise HTTPException(404, "Associação não encontrada.")
        db.scalar(select(BankTransaction).where(BankTransaction.id == link.transaction_id).with_for_update())
        db.scalar(select(Purchase).where(Purchase.id == row.id).with_for_update())
        link = db.scalar(select(PurchaseAllocation).where(PurchaseAllocation.id == allocation_id).execution_options(populate_existing=True))
        if not link:
            raise HTTPException(404, "A associação já foi removida.")
        record(db, user, "reconciliacao_compra_removida", row.id, {"allocation_id": link.id, "transaction_id": link.transaction_id, "amount_cents": link.amount_cents}, row.store_id)
        db.delete(link); db.commit()
        return projection(db, row)

    @app.post("/api/purchases/{purchase_id}/attachments")
    async def attach(purchase_id: str, file: UploadFile = File(...), user=Depends(current), db: Session = Depends(db_session)):
        row = get_purchase(db, user, purchase_id, True)
        db.scalar(select(Purchase).where(Purchase.id == row.id).with_for_update())
        from .models import DriveDocumentAttachment
        if db.scalar(select(func.count()).select_from(PurchaseAttachment).where(PurchaseAttachment.purchase_id == row.id)) + db.scalar(select(func.count()).select_from(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id == row.id)) >= 5:
            raise HTTPException(422, "Máximo de cinco anexos por compra.")
        raw = await file.read(5 * 1024 * 1024 + 1)
        mime = "application/pdf" if raw.startswith(b"%PDF-") else "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg" if raw.startswith(b"\xff\xd8\xff") else None
        if not mime or not raw or len(raw) > 5 * 1024 * 1024:
            raise HTTPException(422, "Selecione um PDF, JPG ou PNG com até 5 MB.")
        name = (file.filename or "documento").replace("\\", "/").split("/")[-1][:200]
        attachment = PurchaseAttachment(purchase_id=row.id, name=name, mime=mime, data=raw)
        db.add(attachment); db.flush()
        record(db, user, "documento_compra_anexado", row.id, {"attachment_id": attachment.id, "name": name}, row.store_id)
        db.commit()
        return {"id": attachment.id, "name": name}

    @app.get("/api/purchases/attachments/{attachment_id}")
    def download(attachment_id: str, user=Depends(current), db: Session = Depends(db_session)):
        a = db.get(PurchaseAttachment, attachment_id)
        if not a:
            raise HTTPException(404, "Documento não encontrado.")
        get_purchase(db, user, a.purchase_id)
        return Response(a.data, media_type=a.mime, headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(a.name), "Content-Security-Policy": "sandbox"})

    @app.post("/api/purchases/{purchase_id}/attachments/{attachment_id}/remove")
    def detach(purchase_id: str, attachment_id: str, user=Depends(current), db: Session = Depends(db_session)):
        row = get_purchase(db, user, purchase_id, True)
        a = db.get(PurchaseAttachment, attachment_id)
        if not a or a.purchase_id != row.id:
            raise HTTPException(404, "Documento não encontrado.")
        record(db, user, "documento_compra_removido", row.id, {"attachment_id": a.id, "name": a.name}, row.store_id)
        db.delete(a); db.commit()
        return {"ok": True}

    @app.get("/api/purchases/efatura/{store_id}")
    def connection(store_id: str, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, store_id, ["administrador", "gestor"])
        config = db.get(EfaturaConfig, store_id)
        return {"nif": config.nif if config else "", "username": config.username if config else "", "environment": config.environment if config else "testes", "has_password": bool(config and config.password_ciphertext), "certificate_ready": configured(config.environment if config else "testes")}

    @app.post("/api/purchases/efatura/{store_id}")
    def save_connection(store_id: str, data: Connection, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, store_id, ["administrador"])
        if not db.get(Store, store_id):
            raise HTTPException(404, "Loja não encontrada.")
        db.scalar(select(Store).where(Store.id == store_id).with_for_update())
        config = db.get(EfaturaConfig, store_id)
        if not data.password and (not config or config.username != data.username):
            raise HTTPException(422, "Indique a senha do subutilizador.")
        if not config:
            config = EfaturaConfig(store_id=store_id)
            db.add(config)
        config.nif, config.username, config.environment = data.nif, data.username, data.environment
        if data.password:
            config.password_ciphertext = cipher().encrypt(data.password.encode()).decode()
        record(db, user, "efatura_configurada", store_id, {"environment": data.environment}, store_id)
        db.commit()
        return connection(store_id, user, db)

    @app.post("/api/purchases/efatura/{store_id}/sync")
    def sync(store_id: str, data: Sync, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, store_id, ["administrador", "gestor"])
        config = db.get(EfaturaConfig, store_id)
        if not config:
            raise HTTPException(422, "Configure primeiro o acesso ao e-Fatura.")
        try:
            password = cipher().decrypt(config.password_ciphertext.encode()).decode()
            invoices = fetch_invoices(config, password, data.start.isoformat(), data.end.isoformat())
        except (EfaturaError, InvalidToken, httpx.HTTPError, OSError, ValueError) as exc:
            raise HTTPException(422, str(exc) if isinstance(exc, EfaturaError) else "Não foi possível consultar a AT. Verifique a configuração do acesso e do certificado.") from None
        db.scalar(select(Store).order_by(Store.id).limit(1).with_for_update())
        imported = updated = 0
        origin = "efatura_testes" if config.environment == "testes" else "efatura"
        for item in invoices:
            supplier = db.scalar(select(WintouchEntity).where(WintouchEntity.tax_number == item["supplier_nif"], WintouchEntity.active.is_(True)))
            if supplier:
                item["supplier_name"] = supplier.name
            if not item["supplier_nif"] or not item["document"] or len(item["document"]) > 100 or not item["amount_cents"]:
                raise HTTPException(422, "A AT devolveu um documento sem os dados necessários. Nenhuma alteração foi guardada.")
            row = db.scalar(select(Purchase).where(Purchase.supplier_nif == item["supplier_nif"], Purchase.document_type == item["document_type"], Purchase.document == item["document"]).with_for_update())
            if row:
                if (row.origin == "efatura_testes") != (origin == "efatura_testes"):
                    raise HTTPException(409, "Este documento já existe noutro ambiente. Use uma empresa de demonstração separada para testes.")
                if row.store_id != store_id:
                    continue
                if row.amount_cents * item["amount_cents"] < 0 or paid(db, row.id) > abs(item["amount_cents"]):
                    raise HTTPException(409, "Um documento alterado pela AT já tem pagamentos associados. Reveja a reconciliação antes de sincronizar.")
                if row.amount_cents != item["amount_cents"] or row.document_date != item["document_date"] or row.tax_cents != item["tax_cents"]:
                    row.validated = False
                    updated += 1
                for name in ("amount_cents", "tax_cents", "document_date"):
                    setattr(row, name, item[name])
                row.origin = origin
                if row.supplier_name == row.supplier_nif and item["supplier_name"] != item["supplier_nif"]:
                    row.supplier_name = item["supplier_name"]
            else:
                db.add(Purchase(store_id=store_id, origin=origin, **item)); imported += 1
            db.flush()
        record(db, user, "efatura_sincronizada", store_id, {"start": data.start.isoformat(), "end": data.end.isoformat(), "imported": imported, "updated": updated}, store_id)
        db.commit()
        return {"imported": imported, "updated": updated, "total": len(invoices)}


def import_purchase_file(db, user, store_id, raw, filename, confirm, record, drive=None, period=None):
    """Pré-visualiza ou importa um ficheiro e-Fatura (CSV/XLSX) para as compras da loja.

    Partilhado pelo carregamento manual e pela importação a partir do Google Drive.
    `drive` guarda o ID/versão do ficheiro de origem; `period` (início, fim) só produz avisos:
    a importação inclui sempre o ficheiro completo.
    """
    import hashlib

    from .models import DocumentImport
    from .purchase_import import parse_purchases
    info = {}
    try:
        rows = parse_purchases(raw, filename, info)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from None
    if confirm:
        # A preview never writes and must not queue behind a saving import.
        db.scalar(select(Store).order_by(Store.id).limit(1).with_for_update())
    existing = {identity(r.supplier_nif, r.document_type, r.document): r for r in db.scalars(select(Purchase))}
    fresh = {}
    enrich = []
    conflicts = 0
    skipped = {"Anulado pelo emitente no e-Fatura": 0, "Total 0,00 €": 0}
    cancelled_existing = 0
    for item in rows:
        key = identity(item["supplier_nif"], item["document_type"], item["document"])
        if item.get("skip_reason"):
            item["status"] = "skipped"
            skipped[item["skip_reason"]] = skipped.get(item["skip_reason"], 0) + 1
            item["differences"] = []
            if item["skip_reason"].startswith("Anulado") and key in existing:
                item["differences"] = ["existe como documento ativo na aplicação; reveja"]
                cancelled_existing += 1
            continue
        previous = existing.get(key) or fresh.get(key)
        if previous:
            old = {name: getattr(previous, name) for name in ("amount_cents", "tax_cents", "base_cents", "document_date", "origin")} if isinstance(previous, Purchase) else previous
            differences = [label for changed, label in (
                (old["amount_cents"] != item["amount_cents"], "total"),
                (old["document_date"] != item["document_date"], "data"),
                (item["tax_provided"] and old["tax_cents"] != item["tax_cents"], "IVA"),
                (old.get("base_cents") is not None and item.get("base_cents") is not None and old["base_cents"] != item["base_cents"], "base tributável"),
                (old.get("origin") == "efatura_testes", "documento de testes"),
            ) if changed]
            if isinstance(previous, Purchase) and not differences and previous.store_id == store_id:
                enrich.append((previous, item))
            item["status"] = "conflict" if differences else "duplicate"
            item["differences"] = differences
            if isinstance(previous, Purchase) and previous.store_id != store_id and not differences:
                item["differences"] = ["já importado noutra loja"]
            conflicts += int(bool(differences))
        else:
            item["status"] = "new"
            fresh[key] = item
    fingerprint = hashlib.sha256(raw).hexdigest()
    warnings = []
    company_nif = (db.info.get("company") or {}).get("nif") or ""
    buyers = sorted(info.get("buyer_nifs") or [])
    if not info.get("buyer_column"):
        warnings.append("O ficheiro não indica o NIF do adquirente; confirme que é uma exportação desta empresa.")
    elif company_nif and buyers and buyers != [company_nif]:
        raise HTTPException(409, f"O ficheiro indica o adquirente {', '.join(buyers)}, diferente do NIF desta empresa ({company_nif}). Nada foi importado.")
    dates = sorted(item["document_date"] for item in rows)
    if period:
        outside = sum(not (str(period[0]) <= day <= str(period[1])) for day in dates)
        if outside:
            warnings.append(f"{outside} documento(s) fora do período a conferir ({period[0]} a {period[1]}). A importação inclui todo o ficheiro.")
    previous_imports = [
        {"filename": row.filename, "created_at": row.created_at, "same_content": row.fingerprint == fingerprint, "drive_version": row.drive_version}
        for row in db.scalars(select(DocumentImport).where(
            DocumentImport.source == "ficheiro_efatura",
            or_(DocumentImport.fingerprint == fingerprint, DocumentImport.drive_id == drive["id"]) if drive else DocumentImport.fingerprint == fingerprint,
        ).order_by(DocumentImport.created_at.desc()))
    ]
    if any(skipped.values()):
        detail = ", ".join(f"{count} {'anulado(s) pelo emitente' if reason.startswith('Anulado') else 'com total 0,00 €'}" for reason, count in skipped.items() if count)
        warnings.append(f"Documentos não importados: {detail}. Constam da pré-visualização com o motivo.")
    if cancelled_existing:
        warnings.append(f"{cancelled_existing} documento(s) anulados no e-Fatura já existem como ativos na aplicação. A importação não os altera; reveja-os e corrija por estorno, se necessário.")
    if any(item["same_content"] for item in previous_imports):
        warnings.append("Um ficheiro com o mesmo conteúdo já foi importado; os documentos existentes não são duplicados.")
    elif previous_imports and drive:
        warnings.append("Este ficheiro do Drive foi alterado desde a última importação. Confira os documentos novos e os conflitos.")
    if confirm:
        if conflicts:
            raise HTTPException(409, "Existem documentos repetidos com valores ou datas diferentes. Corrija o ficheiro antes de importar.")
        name = (filename or "ficheiro").replace("\\", "/").split("/")[-1][:200]
        batch = DocumentImport(store_id=store_id, user_id=user.id, filename=name, fingerprint=fingerprint, source="ficheiro_efatura",
            drive_id=(drive or {}).get("id", ""), drive_version=(drive or {}).get("version", ""))
        db.add(batch); db.flush()
        for previous, item in enrich:
            if previous.base_cents is None:
                previous.base_cents = item.get("base_cents")
            if previous.import_id is None:
                previous.import_id = batch.id
            record(db, user, "documento_importacao_reconferida", previous.id, {"import_id": batch.id, "base_cents": previous.base_cents}, store_id)
        for item in fresh.values():
            values = {k: v for k, v in item.items() if k not in {"line", "status", "tax_provided", "differences", "skip_reason"}}
            db.add(Purchase(store_id=store_id, origin="ficheiro", import_id=batch.id, **values))
        record(db, user, "compras_importadas_ficheiro", store_id, {"imported": len(fresh), "duplicates": sum(r["status"] == "duplicate" for r in rows), "skipped": sum(skipped.values()), "filename": name, "fingerprint": fingerprint, "drive_id": batch.drive_id, "drive_version": batch.drive_version}, store_id)
        db.commit()
    return {"rows": rows, "new_count": len(fresh), "duplicate_count": sum(r["status"] == "duplicate" for r in rows), "conflict_count": conflicts, "imported": confirm,
            "skipped_count": sum(skipped.values()), "cancelled_existing_count": cancelled_existing,
            "format": info.get("format"), "columns": info.get("columns", []), "period": [dates[0], dates[-1]] if dates else None,
            "warnings": warnings, "previous_imports": previous_imports, "fingerprint": fingerprint}
