"""Receção de mercadoria a partir da fatura do fornecedor guardada no Arquivo digital.

Lê as linhas do PDF (texto ou OCR, ver invoice_lines), relaciona-as com os artigos existentes
(correspondências aprendidas, código de barras, referência do fornecedor, código, nome
semelhante) e propõe fichas novas para as restantes. Nada entra em stock sem confirmação: a
receção é criada e confirmada pelo circuito de compras existente (validações, lotes, custo e
movimentos), e as correspondências confirmadas ficam aprendidas para a fatura seguinte.
"""

import hashlib
import io
import json
import re
from datetime import date
from decimal import Decimal as D
from difflib import SequenceMatcher
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import inventory as stock
from . import procurement as buying
from .db import db_session
from .invoice_evidence import nif_key, ocr_command, ocr_image
from .invoice_lines import ean_valid, extract, plain
from .models import (
    ArchiveDocument,
    BusinessEntity,
    PurchaseArticleAlias,
    StockArticle,
    StockWarehouse,
    WintouchProduct,
)
from .models import ProcurementDocument as Doc
from .payment_terms import term_rule
from .schemas import Strict

INVOICE_TYPES = ("fatura", "fatura_recibo", "fatura_simplificada", "nota_debito", "por_classificar")
READABLE = ("application/pdf", "image/jpeg", "image/png")
ROLES = ["administrador", "gestor"]


def fail(message, status=422):
    raise HTTPException(status, message)


def description_key(value):
    return re.sub(r"[^a-z0-9]+", " ", plain(value or "")).strip()[:300]


def reference_key(value):
    return stock.document_key(value or "")[:300]


# -- Texto do documento -------------------------------------------------------------------------


def pdf_layout_text(raw):
    """Texto do PDF com a disposição em colunas preservada (tabelas de linhas)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            return ""
        return "\n".join(
            (page.extract_text(extraction_mode="layout") or "") for page in reader.pages[:10]
        )[:60000]
    except Exception:
        return ""


def ocr_text(raw, mime):
    """OCR à resolução que melhor leu as faturas reais (3×, a cores, até 3500 px)."""
    from PIL import Image, ImageOps

    images = []
    if mime == "application/pdf":
        import pypdfium2 as pdfium

        from .invoice_qr import PDF_LOCK

        with PDF_LOCK:
            pdf = pdfium.PdfDocument(raw)
            try:
                for index in range(min(len(pdf), 5)):
                    page = pdf[index]
                    try:
                        width, height = page.get_size()
                        bitmap = page.render(scale=min(3, 3500 / max(width, height)))
                        try:
                            images.append(bitmap.to_pil().convert("RGB"))
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
            finally:
                pdf.close()
    else:
        with Image.open(io.BytesIO(raw)) as original:
            if original.width * original.height > 40_000_000:
                fail("Imagem demasiado grande para OCR.")
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((3500, 3500))
            images.append(image)
    try:
        return "\n".join(ocr_image(image) for image in images)
    finally:
        for image in images:
            image.close()


def read_lines(raw, mime, base_cents):
    """Primeiro o texto do PDF; o OCR só quando o texto não tem linhas legíveis."""
    warnings = []
    result, source = {"lines": [], "warnings": [], "check": {}}, "texto"
    if mime == "application/pdf":
        result = extract(pdf_layout_text(raw), base_cents)
    if not result["lines"]:
        if ocr_command():
            scanned = extract(ocr_text(raw, mime), base_cents)
            if scanned["lines"] or not result["lines"]:
                result, source = scanned, "ocr"
        else:
            warnings.append("OCR indisponível no servidor: só é possível ler PDFs com texto.")
    result["warnings"] = warnings + result["warnings"]
    return result, source


# -- Correspondência com artigos ----------------------------------------------------------------


class Catalogue:
    """Índices dos artigos ativos para relacionar as linhas da fatura."""

    def __init__(self, db, supplier_id):
        self.supplier = supplier_id or ""
        erp = {p.id: p.name for p in db.scalars(select(WintouchProduct))}
        self.articles = {}
        self.barcodes, self.codes, self.references = {}, {}, {}
        self.tokens = {}
        for row in db.scalars(select(StockArticle).where(StockArticle.active.is_(True))):
            meta = json.loads(row.data)
            name = erp.get(row.erp_id) or row.name
            self.articles[row.id] = {"id": row.id, "code": row.code, "name": name, "key": description_key(name), "meta": meta}
            for code in meta.get("barcodes", []):
                self.barcodes.setdefault(code.strip(), row.id)
            self.codes.setdefault(row.code.strip().casefold(), row.id)
            if meta.get("supplier_reference"):
                key = (meta.get("supplier_id") or "", reference_key(meta["supplier_reference"]))
                self.references.setdefault(key, row.id)
            for token in set(self.articles[row.id]["key"].split()):
                if len(token) >= 3:
                    self.tokens.setdefault(token, set()).add(row.id)
        self.aliases = {
            (a.supplier_key, a.kind, a.key): a.article_id
            for a in db.scalars(select(PurchaseArticleAlias))
            if a.article_id in self.articles
        }
        self.all_codes = {row.code.casefold() for row in db.scalars(select(StockArticle))}

    def view(self, identifier, method, score=1.0):
        article = self.articles[identifier]
        return {
            "article_id": identifier, "code": article["code"], "name": article["name"], "method": method,
            "score": round(score, 3), "service": bool(article["meta"].get("service")),
        }

    def match(self, line):
        barcode, reference = line["barcode"], line["reference"]
        description = description_key(line["description"])
        learned = [("", "ean", barcode), (self.supplier, "ref", reference_key(reference)), (self.supplier, "desc", description)]
        for key in learned:
            if key[2] and key in self.aliases:
                return {"selected": self.view(self.aliases[key], "aprendido"), "suggestions": []}
        if barcode and barcode in self.barcodes:
            return {"selected": self.view(self.barcodes[barcode], "codigo_barras"), "suggestions": []}
        if reference:
            for supplier in (self.supplier, ""):
                found = self.references.get((supplier, reference_key(reference)))
                if found:
                    return {"selected": self.view(found, "referencia_fornecedor"), "suggestions": []}
        for code in (reference, barcode):
            if code and code.strip().casefold() in self.codes:
                return {"selected": self.view(self.codes[code.strip().casefold()], "codigo"), "suggestions": []}
        # Nome semelhante: só candidatos com palavras em comum. Tamanhos diferentes (500 vs 250 ml)
        # penalizam; a seleção automática exige semelhança alta e margem para a 2.ª hipótese.
        pool = set().union(*(self.tokens.get(t, set()) for t in description.split() if len(t) >= 3)) if description else set()
        sizes = set(re.findall(r"\d+", description))
        scored = []
        for i in pool:
            score = SequenceMatcher(None, description, self.articles[i]["key"]).ratio()
            other = set(re.findall(r"\d+", self.articles[i]["key"]))
            if sizes and other and sizes != other:
                score *= 0.8 if not sizes & other else 0.85
            scored.append((score, i))
        scored = sorted(scored, reverse=True)[:3]
        suggestions = [self.view(i, "semelhante", score) for score, i in scored if score >= 0.5]
        margin = suggestions[0]["score"] - (suggestions[1]["score"] if len(suggestions) > 1 else 0) if suggestions else 0
        selected = suggestions[0] if suggestions and suggestions[0]["score"] >= 0.8 and margin >= 0.03 else None
        return {"selected": selected, "suggestions": suggestions}

    def proposal(self, line):
        """Ficha nova proposta a partir da linha (o utilizador pode alterar antes de confirmar)."""
        base = (line["reference"] or line["barcode"] or "ART").strip()[:90]
        code, number = base, 2
        while code.casefold() in self.all_codes:
            code, number = f"{base[:85]}-{number}", number + 1
        self.all_codes.add(code.casefold())
        quantity = D(line["quantity"])
        decimals = max(0, -quantity.normalize().as_tuple().exponent) if quantity != quantity.to_integral() else 0
        return {
            "code": code,
            "name": line["description"][:300],
            "unit": "un",
            "precision": min(decimals, 6),
            "vat_rate": line["vat"] if line["vat"] in ("0", "6", "13", "23") else "23",
            "supplier_reference": line["reference"][:100] if line["reference"] != line["barcode"] else "",
            "barcodes": [line["barcode"]] if line["barcode"] and line["barcode"] not in self.barcodes else [],
            # Portes e envios propõem-se como serviço (não movimentam stock).
            "service": bool(line.get("service")),
            "lots": bool(line["lot"]) and not line.get("service"),
            "expiry": bool(line["expiry"]) and not line.get("service"),
        }


def learn(db, supplier_id, line, article_id):
    """Guarda a correspondência confirmada (corrige a anterior, se o utilizador escolheu outra)."""
    keys = []
    if line.barcode and ean_valid(line.barcode):
        keys.append(("", "ean", line.barcode))
    if line.reference:
        keys.append((supplier_id, "ref", reference_key(line.reference)))
    if description_key(line.description):
        keys.append((supplier_id, "desc", description_key(line.description)))
    for supplier, kind, key in keys:
        row = db.scalar(
            select(PurchaseArticleAlias).where(
                PurchaseArticleAlias.supplier_key == supplier,
                PurchaseArticleAlias.kind == kind,
                PurchaseArticleAlias.key == key,
            )
        )
        if row:
            row.article_id = article_id
        else:
            db.add(PurchaseArticleAlias(supplier_key=supplier, kind=kind, key=key, article_id=article_id))
    return len(keys)


# -- Entrada de dados ---------------------------------------------------------------------------


class NewArticle(Strict):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    unit: str = Field(default="un", min_length=1, max_length=20)
    precision: int = Field(default=0, ge=0, le=6)
    vat_rate: D = Field(default=D(23), ge=0, le=100, decimal_places=3)
    supplier_reference: str = Field(default="", max_length=100)
    barcodes: list[str] = Field(default_factory=list, max_length=20)
    lots: bool = False
    expiry: bool = False
    service: bool = False


class ReceiveLine(Strict):
    article_id: str | None = None
    new_article: NewArticle | None = None
    description: str = Field(min_length=1, max_length=500)
    reference: str = Field(default="", max_length=200)
    barcode: str = Field(default="", max_length=20)
    # Quantidade faturada, bónus (unidades oferecidas), preço e desconto da linha, como na fatura.
    quantity: D = Field(gt=0, le=10**9, decimal_places=6)
    bonus: D = Field(default=D(0), ge=0, le=10**9, decimal_places=6)
    price: D = Field(ge=0, le=10**9, decimal_places=6)
    discount: D = Field(default=D(0), ge=0, le=100, decimal_places=4)
    vat: D = Field(default=D(23), ge=0, le=100, decimal_places=3)
    lot: str = Field(default="", max_length=100)
    expiry: date | None = None

    @model_validator(mode="after")
    def one_article(self):
        if bool(self.article_id) == bool(self.new_article):
            raise ValueError("Escolha um artigo existente ou crie um novo.")
        return self


class ReceiveInput(Strict):
    supplier_id: str
    warehouse_id: str
    document: str = Field(min_length=1, max_length=100)
    document_type: Literal["FT", "FS", "FR", "ND"] = "FT"
    document_date: date
    # Desconto global do documento (comercial), aplicado a todas as linhas.
    global_discount: D = Field(default=D(0), ge=0, le=100, decimal_places=4)
    # Tipo de documento de compra (efeito financeiro ao confirmar) e respetivos dados.
    document_type_id: str | None = Field(default=None, max_length=36)
    payment_method: str | None = Field(default=None, max_length=20)
    due_date: date | None = None
    credit_note_purchase_ids: list[str] = Field(default_factory=list, max_length=50)
    lines: list[ReceiveLine] = Field(min_length=1, max_length=300)
    request_key: str = Field(min_length=8, max_length=100)


def received_line(line, global_discount):
    """Unidades que entram (faturadas + bónus) e o desconto equivalente que dá o valor líquido.

    A receção guarda o preço da fatura; o desconto equivalente junta o desconto da linha, o
    global e as unidades oferecidas, para o custo em stock ser valor líquido ÷ unidades.
    """
    received = line.quantity + line.bonus
    net = line.quantity * line.price * (1 - line.discount / 100) * (1 - global_discount / 100)
    if not line.price or not received:
        return received, D(0), net
    equivalent = (1 - net / (received * line.price)) * 100
    return received, min(D(100), max(D(0), equivalent)).quantize(D("0.0001")), net


def register_routes(app, current, authorize, scope, audit):
    def document(db, user, identifier):
        doc = db.get(ArchiveDocument, identifier)
        if not doc or doc.duplicate_of:
            fail("Documento do arquivo não encontrado.", 404)
        authorize(user, db, doc.store_id, ROLES) if doc.store_id else authorize(user, db, roles=ROLES)
        stock.require(user, stock.settings(db), "view_cost")
        return doc

    def suppliers(db):
        return [
            row
            for row in db.scalars(select(BusinessEntity).where(BusinessEntity.is_supplier.is_(True)))
            if row.active and not row.removed
        ]

    def received(db):
        result = {}
        for row in db.scalars(select(Doc).where(Doc.kind == "receipt", Doc.status != "cancelled")):
            archive_id = json.loads(row.data).get("archive_document_id")
            if archive_id:
                result[archive_id] = row.id
        return result

    @app.get("/api/purchase-receiving/documents")
    def documents(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=ROLES)
        stock.require(user, stock.settings(db), "view_cost")
        allowed = set(scope(user, db))
        by_nif = {nif_key(s.tax_number): s for s in suppliers(db) if s.tax_number}
        done = received(db)
        rows = []
        for doc in db.scalars(
            select(ArchiveDocument)
            .where(ArchiveDocument.doc_type.in_(INVOICE_TYPES), ArchiveDocument.duplicate_of.is_(None))
            .order_by(ArchiveDocument.issue_date.desc().nulls_last(), ArchiveDocument.archived_at.desc())
            .limit(500)
        ):
            if doc.store_id and doc.store_id not in allowed or doc.mime not in READABLE:
                continue
            supplier = by_nif.get(nif_key(doc.entity_nif)) if doc.entity_nif else None
            rows.append({
                "id": doc.id,
                "issue_date": doc.issue_date,
                "document": " ".join(p for p in (doc.document_code, doc.document_number) if p).strip(),
                "supplier_name": supplier.name if supplier else doc.entity_name,
                "supplier_nif": doc.entity_nif,
                "supplier_id": supplier.id if supplier else None,
                "total_cents": doc.total_cents,
                "file": doc.original_name,
                "receipt_id": done.get(doc.id),
            })
        return {"rows": rows}

    @app.post("/api/purchase-receiving/{identifier}/read")
    def read(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        from .archive import read_bytes

        doc = document(db, user, identifier)
        raw, mime = read_bytes(db, doc)
        if mime not in READABLE:
            fail("Formato sem leitura de linhas (use PDF, JPG ou PNG).", 415)
        result, source = read_lines(raw, mime, doc.base_cents)
        candidates = suppliers(db)
        supplier = next(
            (s for s in candidates if doc.entity_nif and s.tax_number and nif_key(s.tax_number) == nif_key(doc.entity_nif)),
            None,
        )
        index = Catalogue(db, supplier.id if supplier else "")
        lines = [{**line, **index.match(line), "proposal": index.proposal(line)} for line in result["lines"]]
        stores = set(scope(user, db))
        kinds = {"FT": "FT", "FR": "FR", "FS": "FS", "ND": "ND"}
        return {
            "document": {
                "id": doc.id,
                "number": doc.document_number,
                "document_type": kinds.get((doc.document_code or "").upper(), "FT"),
                "issue_date": doc.issue_date or date.today().isoformat(),
                "supplier_nif": doc.entity_nif,
                "supplier_name": doc.entity_name,
                "supplier_id": supplier.id if supplier else None,
                "base_cents": doc.base_cents,
                "total_cents": doc.total_cents,
                "store_id": doc.store_id,
                "receipt_id": received(db).get(doc.id),
            },
            "source": source,
            "lines": lines,
            "warnings": result["warnings"],
            "check": result["check"],
            # Prazo de pagamento da ficha de cada fornecedor (para o vencimento no cabeçalho).
            "suppliers": [
                {"id": s.id, "name": s.name, "tax_number": s.tax_number, "payment_term": term_rule(db, s.payment_term_id)}
                for s in candidates
            ],
            "warehouses": [
                {"id": w.id, "name": w.name, "store_id": w.store_id}
                for w in db.scalars(select(StockWarehouse).where(StockWarehouse.store_id.in_(stores)))
                if w.active
            ],
        }

    def prepare(db, user, doc, data, supplier, warehouse):
        """Artigos (existentes ou novos), correspondências aprendidas e linhas da receção."""
        config = stock.settings(db)
        lines, created, learned, snapshot = [], [], 0, []
        for number, line in enumerate(data.lines, 1):
            try:
                if line.new_article:
                    new = line.new_article
                    article = stock.save_article(
                        db,
                        user,
                        stock.ArticleInput(
                            code=new.code, name=new.name, unit=new.unit, precision=new.precision,
                            vat_rate=new.vat_rate, supplier_id=supplier.id,
                            supplier_reference=new.supplier_reference, barcodes=new.barcodes,
                            lots=(new.lots or new.expiry) and not new.service,
                            expiry=new.expiry and not new.service, service=new.service,
                        ),
                        config=config,
                    )
                    audit(db, user, "stock_article_create", article.id, {"origem": "fatura do arquivo", "documento": doc.id})
                    created.append(article.id)
                else:
                    article = stock.article(db, line.article_id, True, stock=True)
            except HTTPException as exc:
                raise HTTPException(exc.status_code, f"Linha {number}: {exc.detail}") from None
            meta = json.loads(article.data)
            learned_here = learn(db, supplier.id, line, article.id)
            snapshot_line = {**line.model_dump(mode="json", exclude={"new_article"}), "article_id": article.id}
            if meta.get("service"):
                # Serviço: fica no lançamento (valor, IVA, edição), mas não entra em stock; com tipo de
                # documento conta no total do documento financeiro.
                learned += learned_here
                snapshot.append(snapshot_line)
                if data.document_type_id:
                    _, discount, _ = received_line(line, data.global_discount)
                    lines.append(buying.Line(
                        article_id=article.id, description=line.description, nature="service",
                        supplier_reference=line.reference[:100], quantity=line.quantity, price=line.price,
                        discount=discount, vat=line.vat,
                    ))
                continue
            if meta.get("lots") and not line.lot.strip():
                fail(f"Linha {number}: «{article.name}» controla lotes; indique o lote.")
            learned += learned_here
            units, discount, _ = received_line(line, data.global_discount)
            if units != units.quantize(D(1).scaleb(-meta.get("precision", 0))):
                fail(f"Linha {number}: a quantidade não respeita as casas decimais de «{article.name}».")
            lines.append(buying.Line(
                article_id=article.id,
                description=line.description,
                supplier_reference=line.reference[:100],
                unit=meta.get("unit", "un"),
                quantity=units,
                price=line.price,
                discount=discount,
                vat=line.vat,
                warehouse_id=warehouse.id,
                lot_number=line.lot.strip() if meta.get("lots") else "",
                expiry=line.expiry if meta.get("lots") else None,
            ))
            # O lançamento tal como foi feito (bónus, desconto da linha), para a edição reabrir igual.
            snapshot.append(snapshot_line)
        if not lines:
            fail("Esta fatura só tem serviços: indique o tipo de documento para a lançar, ou registe-a em Compras.")
        receiving = {"global_discount": str(data.global_discount), "warehouse_id": warehouse.id, "lines": snapshot}
        return lines, created, learned, receiving

    def draft_for(data, doc, warehouse, supplier, lines, request_key):
        return buying.Draft(
            kind="receipt",
            store_id=warehouse.store_id,
            supplier_id=supplier.id,
            document=data.document,
            document_type=data.document_type,
            document_date=data.document_date,
            notes=f"Receção a partir da fatura do Arquivo digital ({doc.original_name}).",
            archive_document_id=doc.id,
            document_type_id=data.document_type_id,
            payment_method=data.payment_method,
            due_date=data.due_date or data.document_date,
            credit_note_purchase_ids=data.credit_note_purchase_ids,
            lines=lines,
            request_key=request_key,
        )

    @app.post("/api/purchase-receiving/{identifier}/confirm")
    def confirm(identifier: str, data: ReceiveInput, user=Depends(current), db: Session = Depends(db_session)):
        stock.lock(db)
        doc = document(db, user, identifier)
        prior = db.scalar(select(Doc).where(Doc.request_key == data.request_key))
        if prior:
            return {"receipt_id": prior.id, "already": True}
        if received(db).get(doc.id):
            fail("Esta fatura já tem uma receção de mercadoria registada.", 409)
        warehouse = stock.warehouse(db, user, data.warehouse_id, authorize)
        supplier = buying.supplier(db, data.supplier_id)
        lines, created, learned, receiving = prepare(db, user, doc, data, supplier, warehouse)
        draft = draft_for(data, doc, warehouse, supplier, lines, data.request_key)
        buying.validate(db, user, draft, authorize)
        raw = draft.model_dump(mode="json")
        raw.update(buying.totals(raw["lines"]))
        raw["receiving"] = receiving
        raw["creation_digest"] = hashlib.sha256(stock.dump(draft.model_dump(mode="json")).encode()).hexdigest()
        row = Doc(
            kind="receipt", store_id=warehouse.store_id, supplier_id=supplier.id, user_id=user.id,
            request_key=data.request_key, data=stock.dump(raw),
        )
        db.add(row)
        db.flush()
        buying.confirm_document(db, user, row, buying.Confirm(revision=0, acknowledge_differences=True), authorize, audit)
        audit(
            db, user, "rececao_a_partir_de_fatura", row.id,
            {"arquivo": doc.id, "linhas": len(lines), "artigos_criados": len(created), "correspondencias": learned},
            warehouse.store_id,
        )
        db.commit()
        return {"receipt_id": row.id, "lines": len(lines), "created_articles": len(created), "learned": learned}

    def receipt_of(db, user, identifier):
        row = db.get(Doc, identifier)
        if not row or row.kind != "receipt":
            fail("Receção não encontrada.", 404)
        authorize(user, db, row.store_id, ROLES)
        stock.require(user, stock.settings(db), "view_cost")
        payload = json.loads(row.data)
        if not payload.get("archive_document_id"):
            fail("Esta receção não foi lançada a partir de uma fatura do arquivo.", 409)
        return row, payload, document(db, user, payload["archive_document_id"])

    @app.get("/api/purchase-receiving/receipts/{identifier}")
    def edit_view(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        """Reabre o lançamento de uma receção a partir de fatura exatamente como foi gravado."""
        row, payload, doc = receipt_of(db, user, identifier)
        receiving = payload.get("receiving") or {
            # Receções anteriores ao registo do lançamento: reconstrói a partir das linhas gravadas.
            "global_discount": "0",
            "warehouse_id": payload["lines"][0].get("warehouse_id") if payload["lines"] else None,
            "lines": [
                {"article_id": line["article_id"], "description": line["description"], "reference": line.get("supplier_reference", ""),
                 "barcode": "", "quantity": line["quantity"], "bonus": "0", "price": line["price"], "discount": line.get("discount", "0"),
                 "vat": line["vat"], "lot": line.get("lot_number", ""), "expiry": line.get("expiry")}
                for line in payload["lines"]
            ],
        }
        candidates = suppliers(db)
        index = Catalogue(db, row.supplier_id)
        lines = []
        for line in receiving["lines"]:
            article = index.articles.get(line["article_id"])
            selected = index.view(line["article_id"], "lancado") if article else None
            values = {
                "reference": line.get("reference", ""), "barcode": line.get("barcode", ""), "lot": line.get("lot", ""),
                "expiry": line.get("expiry") or "", "description": line["description"], "quantity": str(line["quantity"]),
                "bonus": str(line.get("bonus", "0")), "price": str(line["price"]), "discount": str(line.get("discount", "0")),
                "vat": str(line.get("vat", "23")), "global_discount": "0", "service": False, "evidence": "",
            }
            # Valor da linha como na fatura (quantidade × preço − desconto da linha).
            net = D(values["quantity"]) * D(values["price"]) * (1 - D(values["discount"]) / 100)
            values["total"] = str(net.quantize(D("0.01")))
            received_units = D(values["quantity"]) + D(values["bonus"])
            values["unit_cost"] = str((net / received_units).quantize(D("0.000001"))) if received_units else "0"
            lines.append({**values, "selected": selected, "suggestions": [], "proposal": index.proposal(values)})
        stores = set(scope(user, db))
        used_invoice = any(buying.used(db, row.id, i, kind) for i in range(len(payload["lines"])) for kind in ("invoice", "return"))
        return {
            "document": {
                "id": doc.id, "number": payload["document"], "document_type": payload.get("document_type", "FT"),
                "issue_date": payload["document_date"], "supplier_nif": doc.entity_nif, "supplier_name": doc.entity_name,
                "supplier_id": row.supplier_id, "base_cents": doc.base_cents, "total_cents": doc.total_cents,
                "store_id": row.store_id, "receipt_id": row.id,
            },
            "receipt": {"id": row.id, "revision": row.revision, "status": row.status, "editable": row.status == "confirmed" and not used_invoice,
                        "warehouse_id": receiving.get("warehouse_id"), "global_discount": receiving.get("global_discount", "0"),
                        "document_type_id": payload.get("document_type_id"), "payment_method": payload.get("payment_method"),
                        "due_date": payload.get("due_date"), "credit_note_purchase_ids": payload.get("credit_note_purchase_ids", [])},
            "source": "rececao",
            "lines": lines,
            "warnings": [] if not used_invoice else ["Esta receção já tem fatura ou devolução associada: corrija com uma devolução ao fornecedor."],
            "check": {"lines_total_cents": 0, "document_base_cents": doc.base_cents, "matches": None},
            "suppliers": [
                {"id": s.id, "name": s.name, "tax_number": s.tax_number, "payment_term": term_rule(db, s.payment_term_id)}
                for s in candidates
            ],
            "warehouses": [
                {"id": w.id, "name": w.name, "store_id": w.store_id}
                for w in db.scalars(select(StockWarehouse).where(StockWarehouse.store_id.in_(stores)))
                if w.active
            ],
        }

    @app.put("/api/purchase-receiving/receipts/{identifier}")
    def edit_save(identifier: str, data: ReceiveInput, revision: int, user=Depends(current), db: Session = Depends(db_session)):
        """Grava a edição: atualiza a mesma receção e volta a gerar os movimentos de stock."""
        stock.lock(db)
        row, payload, doc = receipt_of(db, user, identifier)
        if row.revision != revision:
            fail("A receção mudou entretanto. Feche e volte a abrir antes de gravar.", 409)
        warehouse = stock.warehouse(db, user, data.warehouse_id, authorize)
        if warehouse.store_id != row.store_id:
            fail("A edição tem de manter o estabelecimento da receção.", 409)
        supplier = buying.supplier(db, data.supplier_id)
        lines, created, learned, receiving = prepare(db, user, doc, data, supplier, warehouse)
        draft = draft_for(data, doc, warehouse, supplier, lines, row.request_key)
        edited = buying.EditConfirmed(**{**draft.model_dump(), "revision": row.revision})
        buying.replace_confirmed(db, user, row, edited, authorize, audit, {"receiving": receiving})
        db.commit()
        return {"receipt_id": row.id, "lines": len(lines), "created_articles": len(created), "learned": learned}
