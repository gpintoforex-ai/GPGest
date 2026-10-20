"""Camera/galley scans: protected PDF preparation and confirmed cloud association."""
import hashlib
import io
import json
import math
import re
import unicodedata
from contextlib import closing
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

import httpx
from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import Field, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps, ImageStat
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from .db import db_session
from .models import (DocumentScan, DocumentScanSettings, AttachmentIntegrationConfig, GoogleConnection,
                     Purchase, Movement, CashSession, Register, Store, Category, BankDeposit, DriveDocumentAttachment, DocumentState, now)
from .schemas import Strict
from .platform import cipher
from .google_invoices import authorized_client
from .drive_documents import attach_reference, read_link
from .scan_storage import PROVIDERS

MAX_INPUT = 50 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000

class ScanBodyLimit:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(("/api/scans/", "/api/quick-scan/")) or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        from starlette.responses import JSONResponse
        headers = dict(scope.get("headers", []))
        maximum = MAX_INPUT + 1024*1024
        try: length = int(headers.get(b"content-length", b"0"))
        except ValueError: length = maximum+1
        if length > maximum:
            return await JSONResponse({"detail": "As fotografias ultrapassam o limite de 50 MB."}, status_code=413)(scope, receive, send)
        received = 0
        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum: raise HTTPException(413, "As fotografias ultrapassam o limite de 50 MB.")
            return message
        await self.app(scope, limited_receive, send)

class ScanSettingsInput(Strict):
    max_pages: int = Field(default=20, ge=1, le=40)
    max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024 * 1024, le=5 * 1024 * 1024)
    organisation: Literal["company_period_type", "period_type", "root"] = "company_period_type"

class PageEdit(Strict):
    rotation: Literal[0, 90, 180, 270] = 0
    left: float = Field(default=0, ge=0, lt=1)
    top: float = Field(default=0, ge=0, lt=1)
    width: float = Field(default=1, gt=0.03, le=1)
    height: float = Field(default=1, gt=0.03, le=1)
    # colour = original; enhanced = cor realçada; bw = preto e branco com limiar adaptativo.
    colour: Literal["colour", "enhanced", "grey", "contrast", "bw"] = "colour"
    quality: Literal[70, 85] = 85
    corners: list[list[float]] | None = None

    @model_validator(mode="after")
    def valid(self):
        if self.left + self.width > 1.001 or self.top + self.height > 1.001:
            raise ValueError("O recorte ultrapassa a página.")
        if self.corners:
            if len(self.corners) != 4 or any(len(p) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in p) for p in self.corners):
                raise ValueError("Indique quatro cantos dentro da imagem.")
            crosses = []
            for i in range(4):
                a, b, c = [self.corners[j % 4] for j in (i, i+1, i+2)]
                crosses.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
            if min(crosses) <= 0.01:
                raise ValueError("Os cantos devem formar um quadrilátero sem cruzamentos: superior esquerdo, superior direito, inferior direito, inferior esquerdo.")
        return self

class ScanMetadata(Strict):
    store_id: str
    purchase_id: str | None = None
    movement_id: str | None = None
    deposit_id: str | None = None
    document_type: str = Field(default="FT", min_length=1, max_length=10)
    document_date: date
    entity: str = Field(default="", max_length=300)
    nif: str = Field(default="", max_length=30)
    document: str = Field(min_length=1, max_length=100)
    amount_cents: int | None = Field(default=None, strict=True, ge=-(10**13), le=10**13)
    category_id: str | None = None
    notes: str = Field(default="", max_length=2000)

class PrepareInput(Strict):
    request_key: str = Field(min_length=36, max_length=36)
    metadata: ScanMetadata
    edits: list[PageEdit] = Field(default_factory=list, max_length=40)

class ConfirmScan(Strict):
    confirmed: Literal[True]

def safe_name(value, limit=150):
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return re.sub(r"\s+", "_", value)[:limit] or "Documento"

def scan_settings(db):
    row = db.get(DocumentScanSettings, "default")
    return {"max_pages": row.max_pages if row else 20, "max_bytes": row.max_bytes if row else 5*1024*1024, "organisation": row.organisation if row else "company_period_type"}

def cleanup(db):
    db.execute(update(DocumentScan).where(DocumentScan.expires_at < now(), DocumentScan.pdf_ciphertext.is_not(None)).values(pdf_ciphertext=None))
    from .quick_scan import cleanup as quick_cleanup
    quick_cleanup(db)

def cleanup_all():
    from . import db as database
    from .platform_models import Company
    with database.next_platform_session() as core:
        schemas = list(core.scalars(select(Company.schema_name).where(Company.active == 1)))
    for schema in schemas:
        with Session(database.engine.execution_options(schema_translate_map={None: schema})) as db:
            cleanup(db); db.commit()

def deposit_scans(db, deposit):
    return [r for r in db.scalars(select(DocumentScan).where(DocumentScan.store_id == deposit.store_id, DocumentScan.status == "cloud_saved")) if json.loads(r.data)["metadata"].get("deposit_id") == deposit.id]

def dossier_scan_files(db, start, end, store_ids=None):
    rows = list(db.scalars(select(DocumentScan).where(DocumentScan.status == "cloud_saved", DocumentScan.purchase_id.is_(None), DocumentScan.document_date >= start, DocumentScan.document_date <= end, *([DocumentScan.store_id.in_(store_ids)] if store_ids is not None else []))))
    files, pending = {}, []
    for row in rows:
        try:
            raw, _ = read_link(db, row)
            files["digitalizacoes/"+row.id+".pdf"] = raw
        except (HTTPException, httpx.HTTPError):
            pending.append({"id": row.id, "name": row.name, "reason": "Anexo cloud indisponível para o dossiê; tente novamente após rever a ligação."})
    if rows:
        files["digitalizacoes/indice.json"] = json.dumps([scan_view(r) for r in rows], default=str, ensure_ascii=False).encode()
        files["digitalizacoes/pendencias.json"] = json.dumps(pending, ensure_ascii=False).encode()
    return files

def process_image(raw, edit):
    if not raw or len(raw) > MAX_IMAGE:
        raise HTTPException(422, "Cada fotografia pode ter até 15 MB.")
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if opened.format not in ("JPEG", "PNG", "WEBP") or opened.width * opened.height > 40_000_000:
                raise ValueError()
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if edit.corners:
            points = [(x * image.width, y * image.height) for x, y in edit.corners]
            a, b, c, d = points
            width = round(max(math.dist(a, b), math.dist(d, c)))
            height = round(max(math.dist(a, d), math.dist(b, c)))
            image = image.transform((width, height), Image.Transform.QUAD, (*a, *d, *c, *b), Image.Resampling.BICUBIC)
        left, top = round(edit.left * image.width), round(edit.top * image.height)
        image = image.crop((left, top, min(image.width, left + round(edit.width * image.width)), min(image.height, top + round(edit.height * image.height))))
        if image.width < 30 or image.height < 30:
            raise ValueError()
        if edit.rotation:
            image = image.rotate(-edit.rotation, expand=True)
        if edit.colour == "enhanced":
            image = ImageEnhance.Sharpness(ImageEnhance.Color(ImageOps.autocontrast(image, cutoff=1)).enhance(1.15)).enhance(1.3)
        elif edit.colour != "colour":
            image = ImageOps.grayscale(image)
        if edit.colour == "contrast":
            image = ImageEnhance.Contrast(ImageOps.autocontrast(image)).enhance(1.6)
        if edit.colour == "bw":
            # Limiar local: tinta = mais escura do que o fundo à volta (sombras e iluminação desigual).
            background = image.filter(ImageFilter.GaussianBlur(max(6, min(image.size) // 50)))
            image = ImageChops.subtract(background, image).point(lambda value: 0 if value > 16 else 255)
        warnings = []
        sample = ImageOps.grayscale(image.copy()); sample.thumbnail((512, 512))
        edge_image = sample.filter(ImageFilter.FIND_EDGES)
        edges = ImageStat.Stat(edge_image.crop((1, 1, sample.width-1, sample.height-1))).stddev[0]
        if edges < 8:
            warnings.append("Imagem com pouco detalhe ou possivelmente desfocada. Confirme a legibilidade; o aviso é uma estimativa.")
        if min(image.size) < 700:
            warnings.append("Resolução reduzida. Amplie a pré-visualização para confirmar o texto.")
        image.thumbnail((3200, 8000), Image.Resampling.LANCZOS)
        output = io.BytesIO(); image.save(output, format="JPEG", quality=edit.quality, optimize=True)
        return output.getvalue(), image.size, warnings
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, ValueError):
        raise HTTPException(422, "Fotografia inválida, demasiado grande ou recorte incorreto. Use JPG, PNG ou WebP; em HEIC escolha exportar como JPEG.") from None

def make_pdf(files, edits, limits):
    if not files or len(files) > limits["max_pages"] or sum(len(r) for r in files) > MAX_INPUT:
        raise HTTPException(422, "Número de páginas ou tamanho das fotografias acima do limite.")
    if len(files) == 1 and files[0].startswith(b"%PDF-"):
        if len(files[0]) > limits["max_bytes"]:
            raise HTTPException(422, "O PDF ultrapassa o limite configurado.")
        try:
            reader = PdfReader(io.BytesIO(files[0]), strict=False)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= limits["max_pages"]:
                raise ValueError()
            root = reader.trailer["/Root"]
            names = root.get("/Names")
            if names: names = names.get_object()
            if root.get("/AA") or names and (names.get("/JavaScript") or names.get("/EmbeddedFiles")):
                raise ValueError()
            action = root.get("/OpenAction")
            if action:
                action = action.get_object()
                if isinstance(action, dict) and action.get("/S") not in (None, "/GoTo"):
                    raise ValueError()
            for page in reader.pages:
                if not 0 < float(page.mediabox.width) <= 14400 or not 0 < float(page.mediabox.height) <= 14400:
                    raise ValueError()
                if page.get("/AA"): raise ValueError()
                annotations = page.get("/Annots", [])
                if hasattr(annotations, "get_object"):
                    annotations = annotations.get_object()
                for annotation in annotations:
                    annotation = annotation.get_object()
                    action = annotation.get("/A")
                    if annotation.get("/AA") or annotation.get("/Subtype") == "/RichMedia": raise ValueError()
                    if action and action.get_object().get("/S") not in ("/URI", "/GoTo"):
                        raise ValueError()
            # Existing PDFs retain their bytes/signatures; active documents are rejected, never silently rewritten.
            return files[0], len(reader.pages), []
        except Exception:
            raise HTTPException(422, "PDF inválido, protegido, com elementos ativos ou demasiadas páginas. Use uma cópia PDF simples.") from None
    if len(edits) != len(files):
        raise HTTPException(422, "Reveja as opções de todas as páginas.")
    output = io.BytesIO(); document = canvas.Canvas(output, invariant=1, pageCompression=1)
    warnings = []
    for index, (raw, edit) in enumerate(zip(files, edits)):
        jpg, (width, height), notes = process_image(raw, edit)
        # Natural proportions: narrow/long receipts stay narrow/long, never stretched to A4.
        page_width = (80 if height / width > 2.5 else 210) * 72 / 25.4
        image_width = page_width - 2 * 8 * 72 / 25.4
        image_height = image_width * height / width
        page_height = image_height + 2 * 8 * 72 / 25.4
        if page_height > 14400:
            raise HTTPException(422, "O talão é demasiado comprido para uma página PDF. Divida-o em fotografias.")
        document.setPageSize((page_width, page_height))
        document.drawImage(ImageReader(io.BytesIO(jpg)), 8*72/25.4, 8*72/25.4, width=image_width, height=image_height)
        document.showPage()
        warnings.extend([f"Página {index+1}: {text}" for text in notes])
    document.save()
    return output.getvalue(), len(files), warnings

def scan_view(row):
    return {"id": row.id, "status": row.status, "validation_status": row.validation_status, "name": row.name, "size": row.size, "pages": row.pages, "digest": row.digest, "provider": row.provider, "folder": row.drive_folder, "cloud_id": row.drive_id, "purchase_id": row.purchase_id, "movement_id": row.movement_id, "error": row.error, "created_at": row.created_at, "expires_at": row.expires_at, "view_url": f"/api/scans/{row.id}/pdf", "metadata": json.loads(row.data)["metadata"], "origin": row.origin, "quick": json.loads(row.data).get("quick")}

def register_routes(app, current, authorize, scope, audit):
    def get_scan(db, user, identifier, lock=False):
        query = select(DocumentScan).where(DocumentScan.id == identifier)
        row = db.scalar(query.with_for_update() if lock else query)
        if not row:
            raise HTTPException(404, "Digitalização indisponível nesta empresa.")
        authorize(user, db, row.store_id, ["administrador", "gestor"] if lock else None)
        return row

    @app.get("/api/scans/setup")
    def setup(user=Depends(current), db: Session=Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        cleanup(db); db.commit()
        config, connection = db.get(AttachmentIntegrationConfig, "default"), db.get(GoogleConnection, "default")
        return {"company": db.info.get("company", {}), "settings": scan_settings(db), "provider": "google_drive", "connected": bool(connection and connection.token_ciphertext), "folder_configured": bool(config and config.drive_folder), "account": config.google_email if config else "", "stores": [{"id": r.id, "name": r.name} for r in db.scalars(select(Store).where(Store.id.in_(scope(user, db))))], "categories": [{"id": r.id, "name": r.name} for r in db.scalars(select(Category))], "documents": [{"id": r.id, "store_id": r.store_id, "document": r.document, "document_type": r.document_type, "document_date": r.document_date, "supplier_name": r.supplier_name, "supplier_nif": r.supplier_nif, "amount_cents": r.amount_cents, "notes": r.notes} for r in db.scalars(select(Purchase).where(Purchase.store_id.in_(scope(user, db))).order_by(Purchase.created_at.desc()).limit(300))], "recent": [scan_view(r) for r in db.scalars(select(DocumentScan).where(DocumentScan.store_id.in_(scope(user, db))).order_by(DocumentScan.created_at.desc()).limit(20))]}

    @app.post("/api/scans/settings")
    def settings(data: ScanSettingsInput, user=Depends(current), db: Session=Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        row = db.get(DocumentScanSettings, "default") or DocumentScanSettings(id="default")
        db.add(row)
        for key, value in data.model_dump().items(): setattr(row, key, value)
        audit(db, user, "digitalizacao_configurada", "default", data.model_dump()); db.commit()
        return scan_settings(db)

    @app.get("/api/scans/settings")
    def read_settings(user=Depends(current), db: Session=Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        return scan_settings(db)

    @app.post("/api/scans/image-preview")
    async def image_preview(edit: str=Form(...), file: UploadFile=File(...), user=Depends(current), db: Session=Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        try:
            options = PageEdit.model_validate_json(edit)
            raw = await file.read(MAX_IMAGE+1)
            jpg, _, _ = await run_in_threadpool(process_image, raw, options)
            return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
        except ValueError:
            raise HTTPException(422, "Opções de recorte ou perspetiva inválidas. Reveja os limites da imagem.") from None
        finally: await file.close()

    @app.post("/api/scans/read-qr")
    async def read_qr(file: UploadFile=File(...), user=Depends(current), db: Session=Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        limits = scan_settings(db)
        try:
            raw=await file.read(limits["max_bytes"]+1)
            if len(raw)>limits["max_bytes"]: raise HTTPException(422,"O PDF ultrapassa o limite configurado.")
            if not raw.startswith(b"%PDF-"): raise HTTPException(422,"Selecione um PDF válido para ler o QR code.")
            from .invoice_qr import extract_pdf_qr
            return await run_in_threadpool(extract_pdf_qr,raw,limits)
        finally: await file.close()

    @app.post("/api/scans/prepare")
    async def prepare(payload: str=Form(...), files: list[UploadFile]=File(...), user=Depends(current), db: Session=Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        try:
            data = PrepareInput.model_validate_json(payload)
            UUID(data.request_key)
            meta = data.metadata
            authorize(user, db, meta.store_id, ["administrador", "gestor"])
            if not db.get(Store, meta.store_id) or meta.category_id and not db.get(Category, meta.category_id) or sum(bool(x) for x in (meta.purchase_id, meta.movement_id, meta.deposit_id)) > 1:
                raise HTTPException(422, "Loja, categoria ou associação inválida.")
            if meta.purchase_id:
                doc = db.get(Purchase, meta.purchase_id)
                if not doc or doc.store_id != meta.store_id:
                    raise HTTPException(404, "Documento indisponível nesta loja.")
                if (meta.document, meta.document_type, meta.document_date.isoformat(), meta.nif) != (doc.document, doc.document_type, doc.document_date, doc.supplier_nif) or meta.amount_cents is not None and meta.amount_cents != doc.amount_cents:
                    raise HTTPException(409, "Os dados identificativos diferem da ficha. Use os dados conhecidos do documento associado.")
            if meta.movement_id:
                move = db.get(Movement, meta.movement_id)
                session = db.get(CashSession, move.session_id) if move and move.session_id else None
                register = db.get(Register, session.register_id) if session else None
                if not move or not register or register.store_id != meta.store_id:
                    raise HTTPException(404, "Movimento indisponível nesta loja.")
                from .main import cash
                cash(db, user, move.session_id, True)
            if meta.deposit_id:
                deposit = db.get(BankDeposit, meta.deposit_id)
                if not deposit or deposit.store_id != meta.store_id:
                    raise HTTPException(404, "Depósito indisponível nesta loja.")
            if not meta.purchase_id and not meta.movement_id and not meta.deposit_id and db.scalar(select(Purchase.id).where(Purchase.supplier_nif == meta.nif, Purchase.document == meta.document, Purchase.document_type == meta.document_type)):
                raise HTTPException(409, "Já existe uma ficha com este NIF, tipo e número. Escolha Associar a documento existente antes de gerar.")
            limits = scan_settings(db)
            if len(files) > limits["max_pages"]: raise HTTPException(422, "Demasiadas páginas.")
            raws, total = [], 0
            for file in files:
                raw = await file.read(MAX_IMAGE+1); total += len(raw)
                if total > MAX_INPUT: raise HTTPException(413, "As fotografias ultrapassam 50 MB.")
                raws.append(raw)
            pdf, pages, warnings = await run_in_threadpool(make_pdf, raws, data.edits, limits)
            if len(pdf) > limits["max_bytes"]:
                raise HTTPException(422, "PDF acima do limite configurado. Use compressão compacta, escala de cinzentos ou menos páginas.")
            digest = hashlib.sha256(pdf).hexdigest()
            details = {"metadata": meta.model_dump(mode="json"), "pdf_md5": hashlib.md5(pdf).hexdigest(), "warnings": warnings, "organisation": limits["organisation"]}
            previous = db.scalar(select(DocumentScan).where(DocumentScan.request_key == data.request_key))
            if previous:
                if previous.digest != digest or json.loads(previous.data)["metadata"] != details["metadata"]:
                    raise HTTPException(409, "A referência já pertence a outra versão. Gere uma nova pré-visualização.")
                return {**scan_view(previous), "warnings": warnings}
            config = db.get(AttachmentIntegrationConfig, "default")
            row = DocumentScan(request_key=data.request_key, store_id=meta.store_id, user_id=user.id, purchase_id=meta.purchase_id, movement_id=meta.movement_id, account=config.google_email if config else "", root_folder=config.drive_folder if config else "", document_date=meta.document_date.isoformat(), digest=digest, size=len(pdf), pages=pages, data=json.dumps(details), pdf_ciphertext=cipher().encrypt(pdf), expires_at=now()+timedelta(hours=24), name="preparar.pdf")
            db.add(row); db.flush()
            row.name = safe_name(f"{meta.document_date.isoformat()}_{meta.entity or 'Documento'}_{meta.document_type}_{meta.document}", 170) + "_" + row.id[:8] + ".pdf"
            audit(db, user, "digitalizacao_pdf_gerado", row.id, {"digest": digest, "pages": pages, "size": len(pdf), "association": meta.purchase_id or meta.movement_id}, meta.store_id)
            db.commit()
            return {**scan_view(row), "warnings": warnings}
        except (ValueError, TypeError):
            raise HTTPException(422, "Dados ou referência de digitalização inválidos.") from None
        finally:
            for file in files: await file.close()

    @app.get("/api/scans/{identifier}")
    def status(identifier: str, user=Depends(current), db: Session=Depends(db_session)):
        return scan_view(get_scan(db, user, identifier))

    @app.get("/api/scans/{identifier}/pdf")
    def pdf_view(identifier: str, user=Depends(current), db: Session=Depends(db_session)):
        row = get_scan(db, user, identifier)
        if row.pdf_ciphertext and row.expires_at > now():
            raw = cipher().decrypt(row.pdf_ciphertext)
        elif row.drive_id:
            raw, _ = read_link(db, row)
        else: raise HTTPException(410, "O PDF temporário expirou. Gere novamente a digitalização.")
        if hashlib.sha256(raw).hexdigest() != row.digest:
            raise HTTPException(409, "O ficheiro cloud foi alterado. Reveja a associação.")
        from urllib.parse import quote
        return Response(raw, media_type="application/pdf", headers={"Content-Disposition": "inline; filename*=UTF-8''"+quote(row.name), "Cache-Control": "no-store", "Content-Security-Policy": "sandbox"})

    @app.post("/api/scans/{identifier}/upload")
    def upload_scan(identifier: str, data: ConfirmScan, user=Depends(current), db: Session=Depends(db_session)):
        row = get_scan(db, user, identifier, True)
        if row.status == "cloud_saved": return scan_view(row)
        if row.origin == "digitalizacao_rapida":
            raise HTTPException(409, "Digitalização rápida: o envio repete-se no próprio acesso rápido e nunca cria compras automaticamente.")
        if row.status == "uploading" and (now()-row.updated_at).total_seconds() < 900:
            raise HTTPException(409, "O envio está em curso. Aguarde antes de repetir.")
        row.status, row.error, row.updated_at = "uploading", "", now(); db.commit()
        try:
            client, config = authorized_client(db)
            with closing(client):
                storage = PROVIDERS[row.provider](client)
                if not config.drive_folder: raise HTTPException(409, "Configure a pasta cloud em Configurações > Anexos.")
                if row.account and row.root_folder and (row.account.lower() != config.google_email.lower() or row.root_folder != config.drive_folder):
                    raise HTTPException(409, "A conta ou pasta mudou. Volte à configuração original para recuperar este envio.")
                row.account, row.root_folder = config.google_email, config.drive_folder
                storage.check_folder(config.drive_folder)
                if not row.drive_folder:
                    folder = config.drive_folder
                    details = json.loads(row.data); meta = details["metadata"]
                    segments = []
                    if details["organisation"] == "company_period_type":
                        company = db.info.get("company", {})
                        segments += [safe_name(company.get("name", "Empresa")+"_"+(company.get("nif") or company.get("id", "")[:8])), "Contabilidade"]
                    if details["organisation"] != "root": segments += [meta["document_date"][:4], meta["document_date"][5:7], safe_name(meta["document_type"])]
                    db.commit()
                    for segment in segments: folder = storage.folder(db, row.account, folder, segment)
                    row.drive_folder = folder; db.commit()
                raw = cipher().decrypt(row.pdf_ciphertext) if row.pdf_ciphertext and row.expires_at > now() else None
                storage.save(db, row, db.info.get("company", {}).get("id", ""), raw)
            # Persist the remote ID/confirmation first. Failed association can resume without upload.
            row.status, row.updated_at = "cloud_confirmed", now(); db.commit()
            row = get_scan(db, user, identifier, True)
            if row.status == "cloud_saved": return scan_view(row)
            meta = ScanMetadata(**json.loads(row.data)["metadata"])
            if not row.purchase_id and not row.movement_id and not meta.deposit_id:
                existing = list(db.scalars(select(Purchase).where(Purchase.store_id == row.store_id, Purchase.document == meta.document, Purchase.supplier_nif == meta.nif)))
                if existing:
                    raise HTTPException(409, "Já existe um documento com este número e NIF. Associe a digitalização à ficha existente.")
                purchase = Purchase(store_id=row.store_id, supplier_name=meta.entity or "Entidade não indicada", supplier_nif=meta.nif, document=meta.document, document_type=meta.document_type, document_date=meta.document_date.isoformat(), amount_cents=meta.amount_cents or 0, notes=meta.notes, origin="digitalizacao", validated=False)
                db.add(purchase); db.flush(); row.purchase_id = purchase.id
            if row.purchase_id:
                purchase = db.scalar(select(Purchase).where(Purchase.id == row.purchase_id).with_for_update())
                link = attach_reference(db, purchase, row.account, row.drive_id, row.drive_folder, row.name, row.mime, row.digest, user, audit)
                row.attachment_id = link.id
                state = db.get(DocumentState, purchase.id) or DocumentState(purchase_id=purchase.id)
                db.add(state); state.availability = "digital"
            if row.movement_id:
                from .main import cash
                from .models import Attachment
                move = db.scalar(select(Movement).where(Movement.id == row.movement_id).with_for_update())
                cash(db, user, move.session_id, True)
                count = db.scalar(select(func.count()).select_from(Attachment).where(Attachment.movement_id == move.id)) + db.scalar(select(func.count()).select_from(DocumentScan).where(DocumentScan.movement_id == move.id, DocumentScan.status == "cloud_saved"))
                if count >= 3: raise HTTPException(422, "Máximo de três anexos por movimento. O ficheiro cloud fica disponível para recuperar.")
            row.status, row.pdf_ciphertext, row.updated_at = "cloud_saved", None, now()
            audit(db, user, "digitalizacao_cloud_guardada", row.id, {"provider": row.provider, "cloud_id": row.drive_id, "folder": row.drive_folder, "purchase_id": row.purchase_id, "movement_id": row.movement_id, "validation": "por_validar"}, row.store_id)
            db.commit()
            return scan_view(row)
        except Exception as exc:
            db.rollback()
            row = get_scan(db, user, identifier)
            row.status, row.updated_at = "upload_error", now()
            row.error = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else "Não foi possível concluir o envio ou a associação. Pode repetir com o mesmo identificador; não feche antes de descarregar o PDF."
            audit(db, user, "digitalizacao_erro_envio", row.id, {"cloud_id": row.drive_id, "error": row.error}, row.store_id)
            db.commit()
            raise HTTPException(exc.status_code if isinstance(exc, HTTPException) else 502, row.error) from None

    @app.post("/api/scans/{identifier}/associate")
    def recover_association(identifier: str, payload: ScanMetadata, user=Depends(current), db: Session=Depends(db_session)):
        row = get_scan(db, user, identifier, True)
        if row.origin == "digitalizacao_rapida" or row.status == "cloud_saved" or payload.store_id != row.store_id or not payload.purchase_id or payload.movement_id:
            raise HTTPException(409, "Escolha a ficha existente da mesma loja para recuperar a associação.")
        purchase = db.get(Purchase, payload.purchase_id)
        if not purchase or purchase.store_id != row.store_id: raise HTTPException(404, "Documento indisponível.")
        original = ScanMetadata(**json.loads(row.data)["metadata"])
        if (original.document, original.document_type, original.document_date.isoformat(), original.nif) != (purchase.document, purchase.document_type, purchase.document_date, purchase.supplier_nif):
            raise HTTPException(409, "A ficha escolhida não corresponde aos dados identificativos da digitalização.")
        row.purchase_id = purchase.id
        details = json.loads(row.data); details["metadata"]["purchase_id"] = purchase.id; row.data = json.dumps(details)
        audit(db, user, "digitalizacao_associacao_revista", row.id, {"purchase_id": purchase.id}, row.store_id); db.commit()
        return scan_view(row)

    @app.post("/api/scans/{identifier}/validate")
    def validate(identifier: str, data: ConfirmScan, user=Depends(current), db: Session=Depends(db_session)):
        row = get_scan(db, user, identifier, True)
        if row.status != "cloud_saved": raise HTTPException(409, "Conclua o envio e reveja o anexo primeiro.")
        row.validation_status = "validado"
        audit(db, user, "digitalizacao_anexo_validado", row.id, {"cloud_id": row.drive_id}, row.store_id); db.commit()
        return scan_view(row)
