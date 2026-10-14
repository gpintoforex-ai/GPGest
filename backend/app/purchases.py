from datetime import date
from typing import Literal
from urllib.parse import quote

import httpx
from cryptography.fernet import InvalidToken
from fastapi import Depends, File, HTTPException, Response, UploadFile
from pydantic import Field, model_validator
from sqlalchemy import func, select
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

    def projection(db, row):
        linked = paid(db, row.id)
        total = abs(row.amount_cents)
        from .models import Reconciliation, ReconciliationLine
        groups = [{"id": group.id, "amount_cents": line.amount_cents, "reason": group.reason} for line, group in db.execute(select(ReconciliationLine, Reconciliation).join(Reconciliation).where(ReconciliationLine.source == "purchase", ReconciliationLine.source_id == row.id, Reconciliation.undone_at.is_(None)))]
        from .models import DriveDocumentAttachment, DocumentScan
        scans = {r.attachment_id: r for r in db.scalars(select(DocumentScan).where(DocumentScan.purchase_id == row.id, DocumentScan.status == "cloud_saved"))}
        attachments = [{"id": a.id, "name": a.name} for a in db.scalars(select(PurchaseAttachment).where(PurchaseAttachment.purchase_id == row.id))]
        attachments += [{"id": "drive:"+a.id, "name": a.name, "view_url": "/api/scans/"+scans[a.id].id+"/pdf" if a.id in scans else "/api/drive-attachments/"+a.id+"/view", "validation_status": scans[a.id].validation_status if a.id in scans else "", "scan_id": scans[a.id].id if a.id in scans else None} for a in db.scalars(select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id == row.id))]
        return {**serial(row), "paid_cents": linked, "remaining_cents": max(0, total - linked), "payment_status": "reconciliado" if linked >= total else "parcial" if linked else "por_reconciliar", "attachments": attachments, "allocations": [serial(a) for a in db.scalars(select(PurchaseAllocation).where(PurchaseAllocation.purchase_id == row.id))], "reconciliations": groups}

    @app.get("/api/purchases")
    def purchases(user=Depends(current), db: Session = Depends(db_session)):
        return [projection(db, row) for row in db.scalars(select(Purchase).where(Purchase.store_id.in_(scope(user, db))).order_by(Purchase.document_date.desc(), Purchase.created_at.desc()))]

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
    async def import_file(store_id: str, confirm: bool = False, file: UploadFile = File(...), user=Depends(current), db: Session = Depends(db_session)):
        from .purchase_import import parse_purchases
        authorize(user, db, store_id, ["administrador", "gestor"])
        if not db.get(Store, store_id):
            raise HTTPException(404, "Loja inexistente.")
        raw = await file.read(5_000_001)
        if len(raw) > 5_000_000:
            raise HTTPException(413, "Limite de 5 MB por ficheiro.")
        try:
            rows = parse_purchases(raw, file.filename or "")
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(422, str(exc)) from None
        db.scalar(select(Store).order_by(Store.id).limit(1).with_for_update())
        existing = {identity(r.supplier_nif, r.document_type, r.document): r for r in db.scalars(select(Purchase))}
        fresh = {}
        enrich = []
        conflicts = 0
        for item in rows:
            key = identity(item["supplier_nif"], item["document_type"], item["document"])
            previous = existing.get(key) or fresh.get(key)
            if previous:
                old = {name: getattr(previous, name) for name in ("amount_cents", "tax_cents", "base_cents", "document_date", "origin")} if isinstance(previous, Purchase) else previous
                conflict = old["amount_cents"] != item["amount_cents"] or old["document_date"] != item["document_date"] or (item["tax_provided"] and old["tax_cents"] != item["tax_cents"]) or (old.get("base_cents") is not None and item.get("base_cents") is not None and old["base_cents"] != item["base_cents"]) or old.get("origin") == "efatura_testes"
                if isinstance(previous, Purchase) and not conflict and previous.store_id == store_id:
                    enrich.append((previous, item))
                item["status"] = "conflict" if conflict else "duplicate"
                conflicts += int(conflict)
            else:
                item["status"] = "new"
                fresh[key] = item
        if confirm:
            if conflicts:
                raise HTTPException(409, "Existem documentos repetidos com valores ou datas diferentes. Corrija o ficheiro antes de importar.")
            import hashlib
            from .models import DocumentImport
            batch = DocumentImport(store_id=store_id, user_id=user.id, filename=(file.filename or "ficheiro").replace("\\", "/").split("/")[-1][:200], fingerprint=hashlib.sha256(raw).hexdigest(), source="ficheiro_efatura")
            db.add(batch); db.flush()
            for previous, item in enrich:
                if previous.base_cents is None:
                    previous.base_cents = item.get("base_cents")
                if previous.import_id is None:
                    previous.import_id = batch.id
                record(db, user, "documento_importacao_reconferida", previous.id, {"import_id": batch.id, "base_cents": previous.base_cents}, store_id)
            for item in fresh.values():
                values = {k: v for k, v in item.items() if k not in {"line", "status", "tax_provided"}}
                db.add(Purchase(store_id=store_id, origin="ficheiro", import_id=batch.id, **values))
            record(db, user, "compras_importadas_ficheiro", store_id, {"imported": len(fresh), "duplicates": len(rows) - len(fresh)}, store_id)
            db.commit()
        return {"rows": rows, "new_count": len(fresh), "duplicate_count": sum(r["status"] == "duplicate" for r in rows), "conflict_count": conflicts, "imported": confirm}

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
