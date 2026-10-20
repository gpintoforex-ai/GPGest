"""Conferência e-Fatura ↔ anexos ↔ compras: extensão do assistente de `invoice_workflow`.

Acrescenta ao assistente existente:
- pesquisa e importação de exportações e-Fatura a partir das pastas Drive da empresa;
- a dimensão «compra» (módulo Compras no modo autónomo, ERP no modo integrado);
- compras sem correspondência no e-Fatura, assinaladas para revisão;
- rejeição e confirmação em lote de anexos propostos;
- preparação de lançamentos em falta (rascunho em Compras) e pedidos de documentos por email.

O conteúdo dos documentos é tratado apenas como dados; nada é lançado, pago ou movimentado
sem a operação própria e confirmação explícita.
"""

import csv
import hashlib
import io
import json
import threading
from collections import defaultdict
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session
from .drive_imports import download, drive_configured, list_files, read_files
from .email_delivery import SendInput
from .invoice_evidence import invoice_key, nif_key
from .models import (
    AttachmentIntegrationConfig,
    BusinessEntity,
    Category,
    DocumentERPLink,
    DocumentImport,
    DocumentState,
    DriveDocumentAttachment,
    EmailDispatch,
    ERPDocument,
    InvoiceEvidenceCache,
    InvoiceEvidenceValidation,
    InvoiceWorkflowCheck,
    ProcurementDocument,
    Purchase,
    Store,
    now,
)
from .operating_modes import policy
from .schemas import Strict

EFATURA_LIMIT = 5_000_000
STEPS = ("files", "import", "attachments", "purchases", "review", "finish")

PURCHASE_LABELS = {
    "consistent": "Compra encontrada e consistente",
    "divergent": "Compra encontrada com divergências",
    "draft": "Rascunho de compra por confirmar",
    "not_found": "Compra não encontrada",
    "unavailable": "Consulta ao ERP indisponível",
    "unknown": "Compra por conferir",
}


# ---------------------------------------------------------------------------
# Compras: módulo Compras (modo autónomo) ou ERP (modo integrado)
# ---------------------------------------------------------------------------


def procurement_items(db, stores):
    """Compras e notas de crédito do módulo Compras (não anuladas), em formato comparável."""
    rows = db.execute(
        select(ProcurementDocument, BusinessEntity)
        .join(BusinessEntity, BusinessEntity.id == ProcurementDocument.supplier_id)
        .where(
            ProcurementDocument.kind.in_(("invoice", "credit")),
            ProcurementDocument.status != "cancelled",
            ProcurementDocument.store_id.in_(stores),
        )
    )
    items = []
    for doc, entity in rows:
        payload = json.loads(doc.data)
        snapshot = payload.get("supplier_snapshot", {})
        items.append({
            "id": doc.id,
            "store_id": doc.store_id,
            # Rascunhos guardam a ligação ao documento nos dados; a coluna só é preenchida ao confirmar.
            "purchase_id": doc.purchase_id or payload.get("purchase_id"),
            "confirmed": bool(doc.confirmed_at),
            "supplier_nif": snapshot.get("nif", entity.tax_number),
            "supplier_name": snapshot.get("name", entity.name),
            "document": payload.get("document", ""),
            "document_type": "NC" if doc.kind == "credit" else payload.get("document_type", "FT"),
            "document_date": payload.get("document_date", ""),
            "total_cents": int(payload.get("total_cents") or 0),
            "base_cents": payload.get("base_cents"),
            "tax_cents": payload.get("tax_cents"),
        })
    return items


def purchase_differences(purchase, item, tolerance):
    """Diferenças entre o documento e-Fatura e a compra; arredondamentos ficam à vista."""
    differences, notes = [], []
    if invoice_key(purchase.supplier_nif, purchase.document_type, purchase.document) != invoice_key(
        item["supplier_nif"], item["document_type"], item["document"]
    ):
        differences.append("Fornecedor, tipo ou número diferente")
    if item["document_date"] != purchase.document_date:
        differences.append(f"Data: e-Fatura {purchase.document_date}, compra {item['document_date']}")
    gap = abs(abs(item["total_cents"]) - abs(purchase.amount_cents))
    if gap > tolerance:
        differences.append(f"Total: e-Fatura {abs(purchase.amount_cents) / 100:.2f} €, compra {abs(item['total_cents']) / 100:.2f} €")
    elif gap:
        notes.append(f"Diferença de {gap} cêntimo(s) no total, dentro da tolerância de {tolerance}.")
    for field, label, known in (("base_cents", "Base tributável", purchase.base_cents), ("tax_cents", "IVA", purchase.tax_cents or None)):
        value = item.get(field)
        if known is None or value is None:
            continue
        gap = abs(abs(int(value)) - abs(known))
        if gap > tolerance:
            differences.append(f"{label}: e-Fatura {abs(known) / 100:.2f} €, compra {abs(int(value)) / 100:.2f} €")
        elif gap:
            notes.append(f"Diferença de {gap} cêntimo(s) em {label.lower()}, dentro da tolerância.")
    if item.get("cancelled"):
        differences.append("Documento anulado")
    return differences, notes


def local_purchase_state(purchase, index, tolerance):
    linked = index["by_purchase"].get(purchase.id)
    key = invoice_key(purchase.supplier_nif, purchase.document_type, purchase.document)
    candidates = [linked] if linked else [item for item in index["by_key"].get(key, []) if not item["purchase_id"]]
    if not candidates:
        return {"state": "not_found", "source": "local", "candidates": [], "notes": []}
    results = []
    for item in candidates:
        differences, notes = purchase_differences(purchase, item, tolerance)
        results.append({"id": item["id"], "label": f"{item['document_type']} {item['document']}", "confirmed": item["confirmed"],
                        "linked": item["purchase_id"] == purchase.id, "differences": differences, "notes": notes})
    if len(results) > 1:
        state = "divergent"
        results[0]["differences"].append("Várias compras com a mesma identidade")
    elif not results[0]["confirmed"]:
        state = "draft"
    else:
        state = "divergent" if results[0]["differences"] else "consistent"
    return {"state": state, "source": "local", "candidates": results, "notes": [n for r in results for n in r["notes"]]}


def erp_purchase_state(row):
    state = {"linked": "consistent", "divergence": "divergent", "not_found": "not_found",
             "error": "unavailable", "unavailable": "unavailable"}.get(row["erp_state"], "unknown")
    candidates = [{"id": item["id"], "label": item["document"], "differences": item["differences"], "notes": []} for item in row.get("erp_candidates", [])]
    if state == "consistent" and any(item["differences"] for item in candidates):
        state = "divergent"
    return {"state": state, "source": "erp", "candidates": candidates, "notes": []}


def outcome(row):
    """Resultado, próxima ação e explicação simples, a partir das dimensões independentes."""
    attachment, purchase = row["attachment_state"], row["purchase_state"]
    source = "no ERP" if row.get("purchase_source") == "erp" else "em Compras"
    if row["efatura_state"] == "absent":
        return "review", "review_efatura", "Confirmar no e-Fatura", (
            "A compra está registada, mas não foi encontrada nos documentos e-Fatura importados. O fornecedor pode ainda não "
            "ter comunicado a fatura, ou ela pertence a outro período. Confirme no portal; não é, por si só, um erro.")
    if purchase == "divergent":
        return "divergent", "review_divergence", "Rever divergências da compra", (
            "A compra registada " + source + " não coincide com o e-Fatura. Compare os valores e corrija o lançamento ou "
            "esclareça a diferença com o fornecedor.")
    if purchase == "unavailable":
        return "pending", "retry_erp", "Repetir a consulta ao ERP", (
            "Não foi possível consultar o ERP. Isto não prova que a compra não exista; repita quando a ligação estiver disponível.")
    drive = row.get("drive_state")
    if attachment == "validated" and drive == "review":
        return "pending", "review_attachment", "Rever anexo associado", (
            "O ficheiro associado foi alterado ou já não está nas pastas de pesquisa. A associação foi preservada; abra-o e confirme.")
    if attachment == "validated" and drive in ("error", "partial") and any(a["key"].startswith("drive:") for a in row.get("attachments", [])):
        return "pending", "find_attachment", "Repetir a procura de anexos", (
            "A última procura no Drive não terminou. O progresso foi guardado; repita para confirmar os anexos.")
    if attachment == "missing":
        return "pending", "request_document", "Pedir documento ao fornecedor", (
            "Não foi encontrado o PDF ou a fotografia desta fatura nas pastas de anexos. Peça o documento ao fornecedor ou "
            "digitalize o original." + (" A compra também não está registada." if purchase == "not_found" else ""))
    if attachment == "review":
        return "pending", "review_attachment", "Rever anexo proposto", (
            "Há um anexo possível, mas ainda não foi confirmado. Abra-o e confirme se corresponde a esta fatura.")
    if attachment == "unknown":
        return "pending", "find_attachment", "Procurar anexos", "Ainda não foram procurados anexos para esta fatura."
    if purchase == "draft":
        return "pending", "confirm_draft", "Confirmar o rascunho em Compras", (
            "Já existe um rascunho de compra para este documento. Reveja-o e confirme-o no módulo Compras.")
    if purchase == "not_found":
        return "pending", "prepare_purchase", "Preparar lançamento", (
            "O documento está validado, mas a compra não está registada " + source + ". Prepare o lançamento a partir dos dados conhecidos.")
    if purchase == "unknown":
        return "pending", "check_purchases", "Conferir compras", "Ainda não foi conferido se a compra está registada."
    return "complete", "none", "Nenhuma", "Documento completo: e-Fatura, anexo validado e compra consistente."


def enrich(db, rows, purchases, settings, stores, start, end):
    """Acrescenta compra, resultado e próxima ação às linhas do assistente e as compras sem e-Fatura."""
    mode = policy(db).get("mode", "local")
    tolerance = settings.tolerance_cents
    by_id = {purchase.id: purchase for purchase in purchases}
    items = procurement_items(db, stores) if mode != "integrated" else []
    index = {"by_purchase": {}, "by_key": defaultdict(list)}
    for item in items:
        if item["purchase_id"]:
            index["by_purchase"][item["purchase_id"]] = item
        index["by_key"][invoice_key(item["supplier_nif"], item["document_type"], item["document"])].append(item)
    efatura_imports = set(db.scalars(select(DocumentImport.id).where(DocumentImport.source == "ficheiro_efatura")))
    for row in rows:
        purchase = by_id[row["id"]]
        # Compras confirmadas no módulo Compras também criam um registo de documento: só conta como
        # e-Fatura o que veio de uma importação e-Fatura (ou de documentos de origem e-Fatura).
        from_efatura = purchase.origin in ("ficheiro", "efatura", "efatura_testes") or purchase.import_id in efatura_imports
        # Associações ERP existentes contam sempre; sem elas, no modo autónomo confere-se o módulo Compras.
        uses_erp = mode == "integrated" or row["erp_state"] in ("linked", "divergence")
        result = erp_purchase_state(row) if uses_erp else local_purchase_state(purchase, index, tolerance)
        row.update(efatura_state="present" if from_efatura else "absent", purchase_state=result["state"], purchase_source=result["source"],
                   purchase_candidates=result["candidates"], purchase_notes=result["notes"],
                   rejected=json.loads(row.get("rejected_raw") or "[]"))
        row.pop("rejected_raw", None)
    known = {invoice_key(p.supplier_nif, p.document_type, p.document) for p in db.scalars(select(Purchase))}
    extra = []
    if mode != "integrated":
        for item in items:
            if item["purchase_id"] or not (str(start) <= item["document_date"] <= str(end)):
                continue
            if invoice_key(item["supplier_nif"], item["document_type"], item["document"]) in known:
                continue
            extra.append(absent_row("proc:" + item["id"], item, "local", "consistent" if item["confirmed"] else "draft"))
    else:
        linked = set(db.scalars(select(DocumentERPLink.erp_id).where(DocumentERPLink.undone_at.is_(None))))
        for erp in db.scalars(select(ERPDocument).where(ERPDocument.store_id.in_(stores), ERPDocument.document_date >= str(start),
                                                        ERPDocument.document_date <= str(end), ERPDocument.cancelled.is_(False))):
            if erp.id in linked or invoice_key(erp.supplier_nif, erp.document_type, erp.document) in known:
                continue
            extra.append(absent_row("erp:" + erp.id, {"store_id": erp.store_id, "supplier_nif": erp.supplier_nif, "supplier_name": erp.supplier_name,
                                    "document": erp.document, "document_type": erp.document_type, "document_date": erp.document_date,
                                    "total_cents": erp.amount_cents}, "erp", "consistent"))
    for row in [*rows, *extra]:
        row["result"], row["action"], row["action_label"], row["explanation"] = outcome(row)
        row["complete"] = row["result"] == "complete"
    return rows + extra, mode


def absent_row(identifier, item, source, state):
    return {"id": identifier, "store_id": item["store_id"], "document_date": item["document_date"], "supplier_name": item["supplier_name"],
            "supplier_nif": item["supplier_nif"], "document": item["document"], "document_type": item["document_type"],
            "amount_cents": item["total_cents"], "currency": "EUR", "origin": "compra", "efatura_state": "absent",
            "attachment_state": "unknown", "drive_state": "unknown", "erp_state": "unknown", "attachments": [],
            "erp_candidates": [], "drive_candidates": [], "messages": [], "supplier": {}, "proposal": {},
            "purchase_state": state, "purchase_source": source, "purchase_candidates": [], "purchase_notes": [], "rejected": []}


# ---------------------------------------------------------------------------
# Identificação das exportações e-Fatura pelo conteúdo
# ---------------------------------------------------------------------------

# Resultado por (empresa, ficheiro, versão Drive): uma versão já lida não volta a ser descarregada.
CLASSIFIED = {}
CLASSIFIED_LOCK = threading.Lock()
CLASSIFY_PER_REQUEST = 40


def classify_content(raw, name):
    """Usa o próprio importador para decidir se o ficheiro é uma exportação e-Fatura."""
    from .purchase_import import parse_purchases
    info = {}
    fingerprint = hashlib.sha256(raw).hexdigest()
    try:
        rows = parse_purchases(raw, name, info)
    except (ValueError, UnicodeError) as exc:
        if info.get("columns"):
            return {"kind": "invalid", "reason": str(exc), "fingerprint": fingerprint}
        return {"kind": "other", "fingerprint": fingerprint,
                "reason": "Não tem as colunas de uma exportação e-Fatura (por exemplo, um extrato bancário ou outra lista)."}
    dates = sorted(row["document_date"] for row in rows)
    skipped = sum(bool(row.get("skip_reason")) for row in rows)
    return {"kind": "efatura", "documents": len(rows) - skipped, "skipped": skipped, "period": [dates[0], dates[-1]],
            "fingerprint": fingerprint, "buyer_nifs": sorted(info.get("buyer_nifs") or [])}


def classify_files(db, files):
    company = (db.info.get("company") or {}).get("id")
    results, pending = {}, []
    with CLASSIFIED_LOCK:
        for item in files:
            cached = CLASSIFIED.get((company, item["id"], item["version"]))
            if cached is not None:
                results[item["id"]] = dict(cached)
            elif int(item.get("size") or 0) > EFATURA_LIMIT:
                results[item["id"]] = {"kind": "other", "reason": "Ficheiro acima de 5 MB; o importador não o aceita."}
            else:
                pending.append(item)
    batch = pending[:CLASSIFY_PER_REQUEST]
    by_id = {item["id"]: item for item in batch}
    for drive_id, raw, error in read_files(db, list(by_id), EFATURA_LIMIT):
        if raw is None:
            # Falhas de leitura não ficam em cache: são tentadas de novo na próxima pesquisa.
            results[drive_id] = {"kind": "error", "reason": f"Não foi possível ler o ficheiro: {error}"}
            continue
        found = classify_content(raw, by_id[drive_id]["name"])
        with CLASSIFIED_LOCK:
            CLASSIFIED[(company, drive_id, by_id[drive_id]["version"])] = found
        results[drive_id] = dict(found)
    for item in pending[CLASSIFY_PER_REQUEST:]:
        results[item["id"]] = {"kind": "unchecked", "reason": "Formato por confirmar; é verificado na pré-visualização."}
    return results


def file_note(found):
    if found["kind"] == "efatura":
        start, end = (value[8:10] + "/" + value[5:7] + "/" + value[:4] for value in found["period"])
        skipped = f" (+{found['skipped']} anulado(s) ou com total zero, não importáveis)" if found.get("skipped") else ""
        return f"Exportação e-Fatura · {found['documents']} documento(s){skipped} de {start} a {end}"
    if found["kind"] == "invalid":
        return "Colunas e-Fatura reconhecidas, mas com erros: " + found["reason"]
    return found["reason"]


# ---------------------------------------------------------------------------
# Pedidos ao assistente
# ---------------------------------------------------------------------------


class EfaturaImport(Strict):
    store_id: str = Field(max_length=64)
    drive_id: str = Field(min_length=1, max_length=200)
    confirm: bool = False
    start: date | None = None
    end: date | None = None


class RejectInput(Strict):
    drive_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=2000)


class BatchItem(Strict):
    id: str = Field(max_length=36)
    drive_id: str = Field(min_length=1, max_length=200)


class BatchInput(Strict):
    items: list[BatchItem] = Field(min_length=1, max_length=50)
    confirmed: Literal[True]
    reason: str = Field(min_length=3, max_length=2000)


class LineInput(Strict):
    description: str = Field(min_length=1, max_length=500)
    nature: Literal["service", "expense"] = "expense"
    quantity: str = Field(default="1", max_length=30)
    price: str = Field(max_length=30)
    vat: str = Field(max_length=10)
    category_id: str | None = Field(default=None, max_length=36)


class SupplierInput(Strict):
    name: str = Field(min_length=2, max_length=300)
    nif: str = Field(pattern=r"^\d{9}$")
    email: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=500)


class PrepareInput(Strict):
    supplier_id: str | None = Field(default=None, max_length=36)
    create_supplier: SupplierInput | None = None
    document: str = Field(min_length=1, max_length=100)
    document_type: Literal["FT", "FS", "FR", "ND", "NC"]
    document_date: date
    due_date: date
    category_id: str | None = Field(default=None, max_length=36)
    lines: list[LineInput] = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=5000)
    confirmed: Literal[True]

    @model_validator(mode="after")
    def supplier_choice(self):
        if bool(self.supplier_id) == bool(self.create_supplier):
            raise ValueError("Escolha um fornecedor existente ou confirme a criação de um novo.")
        return self


VAT_RATES = (0, 4, 5, 6, 9, 12, 13, 16, 22, 23)


class LaunchItem(Strict):
    id: str = Field(max_length=36)
    category_id: str | None = Field(default=None, max_length=36)
    due_date: date | None = None


class LaunchInput(Strict):
    items: list[LaunchItem] = Field(min_length=1, max_length=100)
    create_missing_suppliers: bool = False
    confirmed: Literal[True]


class RequestPreview(Strict):
    ids: list[str] = Field(min_length=1, max_length=100)
    to: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=10000)


class AssistantState(Strict):
    step: Literal["files", "import", "attachments", "purchases", "review", "finish"] = "files"
    start: date | None = None
    end: date | None = None
    store_id: str = Field(default="", max_length=64)
    drive_id: str = Field(default="", max_length=200)


def money_cents(value):
    from decimal import Decimal, InvalidOperation
    try:
        amount = Decimal(value.strip().replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise HTTPException(422, "Indique valores numéricos válidos.") from None
    if not amount.is_finite():
        raise HTTPException(422, "Indique valores numéricos válidos.")
    return amount


def register_routes(app, current, authorize, scope, audit, workflow):
    """`workflow` é o módulo invoice_workflow (configurações, verificações e associação Drive)."""

    def efatura_roots(db):
        """Pastas e-Fatura próprias ou, sem elas, as pastas Drive já configuradas na empresa."""
        settings = workflow.settings_for(db)
        own = json.loads(settings.efatura_folders or "[]")
        if own:
            return settings, own, False
        if not drive_configured(db):
            return settings, [], True
        config = db.get(AttachmentIntegrationConfig, "default")
        return settings, list(dict.fromkeys([*json.loads(settings.folders or "[]"), config.drive_folder])), True

    def get_purchase(db, user, identifier, write=False):
        row = db.scalar(select(Purchase).where(Purchase.id == identifier).with_for_update() if write else select(Purchase).where(Purchase.id == identifier))
        if not row:
            raise HTTPException(404, "Fatura indisponível nesta empresa.")
        authorize(user, db, row.store_id, ["administrador", "gestor"] if write else None)
        return row

    @app.get("/api/invoice-workflow/assistant-state")
    def get_state(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db)
        settings = workflow.settings_for(db)
        db.commit()
        return json.loads(settings.assistant_state or "{}").get(user.id, {})

    @app.post("/api/invoice-workflow/assistant-state")
    def save_state(data: AssistantState, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db)
        settings = workflow.settings_for(db, True)
        states = json.loads(settings.assistant_state or "{}")
        states[user.id] = {**data.model_dump(mode="json"), "saved_at": now().isoformat()}
        settings.assistant_state = json.dumps(states)
        db.commit()
        return states[user.id]

    @app.get("/api/invoice-workflow/efatura-files")
    def efatura_files(user=Depends(current), db: Session = Depends(db_session)):
        """Exportações e-Fatura nas pastas Drive, identificadas pelo conteúdo, com o estado de importação."""
        authorize(user, db, roles=["administrador", "gestor"])
        settings, roots, fallback = efatura_roots(db)
        db.commit()
        if not roots:
            return {"configured": False, "files": [], "ignored": []}
        files = list_files(db, "efatura", roots, settings.include_subfolders)
        classes = classify_files(db, files)
        versions = defaultdict(set)
        for drive_id, version in db.execute(select(DocumentImport.drive_id, DocumentImport.drive_version).where(
                DocumentImport.source == "ficheiro_efatura", DocumentImport.drive_id.in_([item["id"] for item in files]))):
            versions[drive_id].add(version)
        fingerprints = set(db.scalars(select(DocumentImport.fingerprint).where(DocumentImport.source == "ficheiro_efatura")))
        company_nif = (db.info.get("company") or {}).get("nif") or ""
        shown, ignored = [], []
        for item in files:
            found = classes[item["id"]]
            if found["kind"] == "efatura" and company_nif and found["buyer_nifs"] and found["buyer_nifs"] != [company_nif]:
                found = {"kind": "other", "reason": f"Exportação de outra empresa (adquirente {', '.join(found['buyer_nifs'])})."}
            if found["kind"] == "other":
                ignored.append({"id": item["id"], "name": item["name"], "modified_at": item["modified_at"], "reason": found["reason"]})
                continue
            imported = item["version"] in versions.get(item["id"], set()) or found.get("fingerprint") in fingerprints
            item.update(
                format="XLSX" if item["name"].lower().endswith(".xlsx") else "CSV",
                status="imported" if imported else "updated" if versions.get(item["id"]) else "new",
                imported=imported, documents=found.get("documents"), period=found.get("period"), note=file_note(found),
            )
            shown.append(item)
        return {"configured": True, "source": "attachments" if fallback else "efatura", "subfolders": settings.include_subfolders, "files": shown, "ignored": ignored}

    @app.post("/api/invoice-workflow/efatura-files/import")
    def efatura_import(data: EfaturaImport, user=Depends(current), db: Session = Depends(db_session)):
        """Pré-visualiza (confirm=false) ou importa um ficheiro e-Fatura das pastas autorizadas."""
        from .purchases import import_purchase_file
        authorize(user, db, data.store_id, ["administrador", "gestor"])
        if not db.get(Store, data.store_id):
            raise HTTPException(404, "Loja inexistente.")
        if data.start and data.end and data.start > data.end:
            raise HTTPException(422, "O início do período deve ser anterior ao fim.")
        settings, roots, _ = efatura_roots(db)
        if not roots:
            raise HTTPException(409, "Ligue a conta Google e indique a pasta Drive em Configurações → Anexos.")
        buffer, meta = io.BytesIO(), {}
        name, _ = download(db, "efatura", data.drive_id, EFATURA_LIMIT, buffer.write, roots, settings.include_subfolders, meta)
        period = (data.start, data.end) if data.start and data.end else None
        result = import_purchase_file(db, user, data.store_id, buffer.getvalue(), name, data.confirm, audit, meta, period)
        return {**result, "filename": name, "drive": meta}

    @app.post("/api/invoice-workflow/{identifier}/reject")
    def reject(identifier: str, data: RejectInput, user=Depends(current), db: Session = Depends(db_session)):
        """Rejeita um anexo proposto: deixa de ser sugerido para esta fatura, sem apagar o ficheiro."""
        purchase = get_purchase(db, user, identifier, True)
        check = db.get(InvoiceWorkflowCheck, purchase.id)
        if not check:
            raise HTTPException(409, "Procure anexos antes de rejeitar propostas.")
        rejected = list(dict.fromkeys([*json.loads(check.rejected or "[]"), data.drive_id]))
        check.rejected = json.dumps(rejected)
        check.drive_candidates = json.dumps([item for item in json.loads(check.drive_candidates) if item["drive_id"] != data.drive_id])
        audit(db, user, "efatura_anexo_rejeitado", purchase.id, {"drive_id": data.drive_id, "reason": data.reason}, purchase.store_id)
        db.commit()
        return workflow.summarize(db, purchase)

    @app.post("/api/invoice-workflow/{identifier}/reject/undo")
    def undo_reject(identifier: str, data: RejectInput, user=Depends(current), db: Session = Depends(db_session)):
        purchase = get_purchase(db, user, identifier, True)
        check = db.get(InvoiceWorkflowCheck, purchase.id)
        if check:
            check.rejected = json.dumps([item for item in json.loads(check.rejected or "[]") if item != data.drive_id])
            audit(db, user, "efatura_anexo_rejeicao_desfeita", purchase.id, {"drive_id": data.drive_id, "reason": data.reason}, purchase.store_id)
            db.commit()
        return workflow.summarize(db, purchase)

    @app.post("/api/invoice-workflow/attach-batch")
    def attach_batch(data: BatchInput, user=Depends(current), db: Session = Depends(db_session)):
        """Confirma várias propostas fortes já pré-visualizadas. Divergências e ambiguidades ficam de fora."""
        done, skipped = [], []
        for item in data.items:
            purchase = get_purchase(db, user, item.id, True)
            check = db.get(InvoiceWorkflowCheck, purchase.id)
            candidates = json.loads(check.drive_candidates) if check else []
            candidate = next((c for c in candidates if c["drive_id"] == item.drive_id), None)
            if not candidate or candidate.get("strength") != "forte" or len(candidates) != 1:
                skipped.append({"id": purchase.id, "reason": "A proposta já não é forte e única; reveja-a individualmente."})
                db.rollback()
                continue
            try:
                workflow.attach_from_drive(db, user, purchase, item.drive_id, data.reason, audit, "batch")
                db.commit()
                done.append(purchase.id)
            except HTTPException as exc:
                db.rollback()
                skipped.append({"id": purchase.id, "reason": exc.detail})
        return {"attached": done, "skipped": skipped}

    # -- Preparar lançamento (modo autónomo: rascunho no módulo Compras) --------------------

    def supplier_match(db, nif):
        rows = [row for row in db.scalars(select(BusinessEntity).where(BusinessEntity.removed.is_(False))) if nif_key(row.tax_number) == nif_key(nif)]
        ready = [row for row in rows if row.is_supplier and row.active]
        state = "exists" if len(ready) == 1 else "review" if rows else "not_found"
        return state, [{"id": row.id, "name": row.name, "nif": row.tax_number, "is_supplier": row.is_supplier, "active": row.active, "email": row.email, "category_id": row.category_id, "payment_days": row.payment_days} for row in rows]

    def duplicate_reason(db, purchase, document_type=None, document=None):
        key = invoice_key(purchase.supplier_nif, document_type or purchase.document_type, document or purchase.document)
        for item in procurement_items(db, [purchase.store_id]):
            if item["purchase_id"] == purchase.id:
                return "Já existe uma compra ou rascunho em Compras para este documento."
            if invoice_key(item["supplier_nif"], item["document_type"], item["document"]) == key:
                return f"Já existe a compra {item['document_type']} {item['document']} deste fornecedor em Compras."
        if db.scalar(select(DocumentERPLink.id).where(DocumentERPLink.purchase_id == purchase.id, DocumentERPLink.undone_at.is_(None))):
            return "O documento já está associado a um lançamento no ERP."
        return ""

    def evidence_lines(db, purchase):
        """Linhas propostas a partir do QR fiscal do anexo validado (base e IVA por taxa)."""
        keys = [row.attachment_key for row in db.scalars(select(InvoiceEvidenceValidation).where(InvoiceEvidenceValidation.purchase_id == purchase.id))]
        drive_ids = [link.drive_id for link in db.scalars(select(DriveDocumentAttachment).where(
            DriveDocumentAttachment.id.in_([key[6:] for key in keys if key.startswith("drive:")])))]
        target = invoice_key(purchase.supplier_nif, purchase.document_type, purchase.document)
        for cache in db.scalars(select(InvoiceEvidenceCache).where(InvoiceEvidenceCache.drive_id.in_(drive_ids))):
            for document in json.loads(cache.evidence).get("documents", []):
                if invoice_key(document["nif"], document["document_type"], document["document"]) == target and document.get("tax_breakdown"):
                    return document["tax_breakdown"], "QR fiscal do anexo"
        return [], ""

    @app.get("/api/invoice-workflow/{identifier}/prepare-purchase")
    def prepare_purchase(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        purchase = get_purchase(db, user, identifier)
        authorize(user, db, purchase.store_id, ["administrador", "gestor"])
        mode = policy(db).get("mode", "local")
        if mode == "integrated":
            raise HTTPException(409, "No modo integrado, prepare a proposta para o ERP: o conector não permite criar compras.")
        if not workflow.existing_evidence(db, purchase):
            raise HTTPException(409, "Valide primeiro o anexo da fatura. Sem documento validado, não se prepara o lançamento.")
        reason = duplicate_reason(db, purchase)
        if reason:
            raise HTTPException(409, reason)
        state, candidates = supplier_match(db, purchase.supplier_nif)
        breakdown, breakdown_source = evidence_lines(db, purchase)
        lines = [{"description": "", "nature": "expense", "quantity": "1", "price": f"{item['base_cents'] / 100:.2f}",
                  "vat": "" if item["rate"] is None else str(item["rate"]), "base_cents": item["base_cents"], "tax_cents": item["tax_cents"],
                  "origin": breakdown_source} for item in breakdown]
        suggestion = None
        if not lines and purchase.base_cents is not None:
            # Só os totais e-Fatura: proposta de linha única, apenas quando a taxa resulta exatamente dos valores.
            rate = next((r for r in (23, 13, 6, 0) if abs(round(abs(purchase.base_cents) * r / 100) - abs(purchase.tax_cents)) <= 1), None)
            suggestion = {"description": "", "nature": "expense", "quantity": "1", "price": f"{abs(purchase.base_cents) / 100:.2f}",
                          "vat": "" if rate is None else str(rate), "origin": "Totais e-Fatura (base e IVA)"}
        return {
            "fields": {"supplier_nif": purchase.supplier_nif, "supplier_name": purchase.supplier_name, "document_type": purchase.document_type,
                       "document": purchase.document, "document_date": purchase.document_date, "due_date": "",
                       "total_cents": purchase.amount_cents, "base_cents": purchase.base_cents, "tax_cents": purchase.tax_cents},
            "origins": {"supplier_nif": "e-Fatura", "supplier_name": "e-Fatura", "document_type": "e-Fatura", "document": "e-Fatura",
                        "document_date": "e-Fatura", "total_cents": "e-Fatura", "due_date": "Por indicar: não consta do e-Fatura nem do QR"},
            "supplier": {"state": state, "candidates": candidates},
            "lines": lines,
            "line_suggestion": suggestion,
            "review": [text for text in (
                "Indique o vencimento: não é inventado." ,
                "Descreva cada linha e confirme a natureza (despesa ou serviço). Mercadorias com stock completam-se em Compras.",
                "Confirme as taxas de IVA." if any(not line["vat"] for line in lines) or not lines else "",
                "Confirme a rubrica de cada linha: por omissão é a do fornecedor; uma fatura com mercadorias e serviços pode ter rubricas diferentes.",
                "Confirme a criação do fornecedor." if state == "not_found" else "Reveja a entidade com este NIF." if state == "review" else "",
            ) if text],
            "categories": [{"id": row.id, "name": row.name} for row in db.scalars(select(Category).order_by(Category.name))],
            "attachments": [{"name": link.name, "url": "https://drive.google.com/file/d/" + link.drive_id + "/view"}
                            for link in db.scalars(select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id == purchase.id))],
        }

    @app.post("/api/invoice-workflow/{identifier}/prepare-purchase")
    def confirm_prepare(identifier: str, data: PrepareInput, user=Depends(current), db: Session = Depends(db_session)):
        """Cria um rascunho em Compras (não confirmado): não paga, não movimenta caixa, banco ou stock."""
        from . import inventory as stock
        stock.lock(db)
        purchase = get_purchase(db, user, identifier, True)
        if policy(db).get("mode", "local") == "integrated":
            raise HTTPException(409, "No modo integrado, o lançamento é feito no ERP; o conector não permite criar compras.")
        if not workflow.existing_evidence(db, purchase):
            raise HTTPException(409, "O anexo deixou de estar validado. Reveja-o antes de preparar o lançamento.")
        reason = duplicate_reason(db, purchase, data.document_type, data.document)
        if reason:
            raise HTTPException(409, reason)
        if data.due_date < data.document_date:
            raise HTTPException(422, "O vencimento não pode ser anterior à data do documento.")
        if (data.document_type == "NC") != (purchase.document_type == "NC"):
            raise HTTPException(422, "Uma nota de crédito só pode originar uma nota de crédito em Compras.")
        if data.create_supplier:
            if nif_key(data.create_supplier.nif) != nif_key(purchase.supplier_nif):
                raise HTTPException(422, "O NIF do novo fornecedor deve ser o NIF do documento.")
            state, _ = supplier_match(db, data.create_supplier.nif)
            if state != "not_found":
                raise HTTPException(409, "Já existe uma entidade com este NIF. Escolha-a ou reveja-a em Tabelas → Entidades.")
            entity = BusinessEntity(name=data.create_supplier.name.strip(), tax_number=data.create_supplier.nif, email=data.create_supplier.email.strip(),
                                    address=data.create_supplier.address.strip(), is_supplier=True, active=True)
            db.add(entity)
            db.flush()
            audit(db, user, "fornecedor_criado_conferencia", entity.id, {"name": entity.name, "nif": entity.tax_number, "purchase_id": purchase.id}, purchase.store_id)
            supplier_id = entity.id
        else:
            supplier_id = data.supplier_id
            entity = db.get(BusinessEntity, supplier_id)
            if not entity or nif_key(entity.tax_number) != nif_key(purchase.supplier_nif):
                raise HTTPException(422, "O fornecedor escolhido não tem o NIF do documento.")
        lines = []
        for line in data.lines:
            vat = money_cents(line.vat)
            if vat not in VAT_RATES:
                raise HTTPException(422, "Indique uma taxa de IVA válida em cada linha.")
            lines.append({"description": line.description.strip(), "nature": line.nature, "unit": "un", "quantity": str(money_cents(line.quantity)),
                          "price": str(money_cents(line.price)), "vat": str(vat), "category_id": line.category_id})
        row = create_draft(db, user, purchase, supplier_id, data.document.strip(), data.document_type, data.document_date, data.due_date,
                           data.category_id, lines, data.notes, workflow.settings_for(db).tolerance_cents)
        db.commit()
        return {"id": row.id, "status": "draft", "message": "Rascunho criado em Compras. Reveja-o e confirme-o lá; não foi registado pagamento nem stock."}

    def create_draft(db, user, purchase, supplier_id, document, document_type, document_date, due_date, category_id, lines, notes, tolerance, source="conferencia_efatura"):
        """Rascunho em Compras ligado ao documento e-Fatura, com o anexo de origem."""
        from . import inventory as stock
        from . import procurement
        draft = procurement.Draft(
            kind="credit" if document_type == "NC" else "invoice", store_id=purchase.store_id, supplier_id=supplier_id,
            document=document, document_type=document_type, document_date=document_date, due_date=due_date,
            notes=notes, purchase_id=purchase.id, category_id=category_id, lines=lines, request_key="efatura:" + purchase.id,
            attachments=["https://drive.google.com/file/d/" + link.drive_id + "/view"
                         for link in db.scalars(select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id == purchase.id))][:20],
        )
        procurement.validate(db, user, draft, authorize)
        raw = draft.model_dump(mode="json")
        raw.update(procurement.totals(raw["lines"]))
        if abs(raw["total_cents"] - abs(purchase.amount_cents)) > tolerance:
            raise HTTPException(422, f"O total das linhas ({raw['total_cents'] / 100:.2f} €) difere do total do documento ({abs(purchase.amount_cents) / 100:.2f} €). Reveja as linhas; nada foi criado.")
        raw["creation_digest"] = hashlib.sha256(stock.dump(draft.model_dump(mode="json")).encode()).hexdigest()
        raw["source"] = {"kind": source, "purchase_id": purchase.id}
        row = ProcurementDocument(kind=draft.kind, store_id=purchase.store_id, supplier_id=supplier_id, user_id=user.id,
                                  request_key=draft.request_key, data=stock.dump(raw))
        db.add(row)
        db.flush()
        audit(db, user, "compra_preparada_conferencia", row.id, {"purchase_id": purchase.id, "total_cents": raw["total_cents"], "source": source}, purchase.store_id)
        audit(db, user, "efatura_lancamento_preparado", purchase.id, {"procurement_id": row.id, "source": source}, purchase.store_id)
        return row

    # -- Propostas automáticas de lançamento (modo autónomo) ------------------------------

    def validated_evidence(db, purchase):
        """Dados extraídos dos anexos validados (QR, texto ou OCR) que correspondem ao documento."""
        keys = [row.attachment_key for row in db.scalars(select(InvoiceEvidenceValidation).where(InvoiceEvidenceValidation.purchase_id == purchase.id))]
        drive_ids = [link.drive_id for link in db.scalars(select(DriveDocumentAttachment).where(
            DriveDocumentAttachment.id.in_([key[6:] for key in keys if key.startswith("drive:")])))]
        drive_ids += [key for key in keys if not key.startswith("drive:")]
        return [json.loads(cache.evidence) for cache in db.scalars(select(InvoiceEvidenceCache).where(InvoiceEvidenceCache.drive_id.in_(drive_ids)))] if drive_ids else []

    def proposal(db, purchase, overrides=None, by_supplier=None):
        overrides = overrides or {}
        missing = []
        state, candidates = supplier_match(db, purchase.supplier_nif)
        supplier = next((c for c in candidates if c["is_supplier"] and c["active"]), None) if state == "exists" else None
        if state == "not_found":
            missing.append("Fornecedor por criar")
        elif state == "review":
            missing.append("Entidade com este NIF inativa ou não classificada como fornecedor")
        category = overrides.get("category_id") or (supplier or {}).get("category_id")
        if not category:
            missing.append("Rubrica (indique-a no fornecedor ou nesta proposta)")
        target = invoice_key(purchase.supplier_nif, purchase.document_type, purchase.document)
        evidence = validated_evidence(db, purchase)
        breakdown, dues, source = [], set(), "Totais e-Fatura"
        for item in evidence:
            for document in item.get("documents", []):
                if invoice_key(document["nif"], document["document_type"], document["document"]) == target and document.get("tax_breakdown"):
                    breakdown, source = document["tax_breakdown"], "QR fiscal do anexo"
            dues.update(day for day in item.get("due_dates", []) if day >= purchase.document_date)
        label = f"{purchase.supplier_name} · {purchase.document_type} {purchase.document}"[:500]
        lines = []
        if breakdown:
            for part in breakdown:
                if part["rate"] is None:
                    missing.append("Taxa de IVA de uma das bases do QR")
                lines.append({"description": label, "nature": "expense", "unit": "un", "quantity": "1", "price": f"{part['base_cents'] / 100:.2f}", "vat": str(part["rate"] or 0)})
        else:
            base, tax = abs(purchase.base_cents) if purchase.base_cents is not None else None, abs(purchase.tax_cents or 0)
            if base is None and not tax:
                base = abs(purchase.amount_cents)
            rate = next((r for r in (23, 13, 6, 0) if base is not None and round(base * r / 100) == tax), None)
            if rate is None:
                missing.append("Várias taxas de IVA ou taxa não identificável: reveja as linhas")
            else:
                lines.append({"description": label, "nature": "expense", "unit": "un", "quantity": "1", "price": f"{base / 100:.2f}", "vat": str(rate)})
        if lines:
            from .procurement import totals
            gap = totals(lines)["total_cents"] - abs(purchase.amount_cents)
            if gap:
                missing.append(f"O IVA calculado difere {abs(gap)} cêntimo(s) do documento: reveja as linhas")
        due, due_source = overrides.get("due_date"), "indicado"
        if not due and len(dues) == 1:
            due, due_source = next(iter(dues)), "lido no anexo"
        elif not due and supplier and supplier.get("payment_days") is not None:
            due, due_source = (date.fromisoformat(purchase.document_date) + timedelta(days=supplier["payment_days"])).isoformat(), f"prazo do fornecedor ({supplier['payment_days']} dias)"
        elif not due and purchase.document_type in ("FR", "FS"):
            due, due_source = purchase.document_date, "fatura-recibo/simplificada: pagamento no ato"
        if not due:
            missing.append("Vencimento (indique-o, ou o prazo habitual na ficha do fornecedor)" + (": o anexo indica várias datas" if len(dues) > 1 else ""))
        elif due < purchase.document_date:
            missing.append("Vencimento anterior à data do documento")
        names = {c.id: c.name for c in db.scalars(select(Category))} if category else {}
        return {"id": purchase.id, "store_id": purchase.store_id, "supplier_name": purchase.supplier_name, "supplier_nif": purchase.supplier_nif,
                "document": purchase.document, "document_type": purchase.document_type, "document_date": purchase.document_date,
                "amount_cents": purchase.amount_cents, "supplier": {"state": state, "id": (supplier or {}).get("id")},
                "category_id": category, "category_name": names.get(category), "due_date": due, "due_source": due_source if due else "",
                "lines": lines, "lines_source": source if lines else "", "missing": missing, "ready": not missing}

    def launchable(db, stores, start=None, end=None):
        """Documentos e-Fatura com anexo validado e ainda sem lançamento (Compras ou ERP)."""
        efatura_imports = set(db.scalars(select(DocumentImport.id).where(DocumentImport.source == "ficheiro_efatura")))
        query = select(Purchase).where(Purchase.store_id.in_(stores), Purchase.amount_cents != 0)
        if start and end:
            query = query.where(Purchase.document_date >= str(start), Purchase.document_date <= str(end))
        for purchase in db.scalars(query.order_by(Purchase.document_date, Purchase.document)):
            if not (purchase.origin in ("ficheiro", "efatura") or purchase.import_id in efatura_imports):
                continue
            if duplicate_reason(db, purchase) or not workflow.existing_evidence(db, purchase):
                continue
            yield purchase

    @app.get("/api/invoice-workflow/launch-proposals")
    def launch_proposals(start: date, end: date, store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        if policy(db).get("mode", "local") == "integrated":
            return {"mode": "integrated", "proposals": []}
        stores = scope(user, db)
        if store_id:
            authorize(user, db, store_id)
            stores = [store_id] if store_id in stores else []
        items = [proposal(db, purchase) for purchase in launchable(db, stores, start, end)]
        return {"mode": "local", "proposals": items, "ready": sum(item["ready"] for item in items),
                "categories": [{"id": c.id, "name": c.name} for c in db.scalars(select(Category).order_by(Category.name))]}

    @app.post("/api/invoice-workflow/launch-proposals/confirm")
    def confirm_proposals(data: LaunchInput, user=Depends(current), db: Session = Depends(db_session)):
        """Lança as propostas prontas: compra confirmada em Compras e movimento previsto. Não regista pagamentos."""
        from . import inventory as stock
        from . import procurement
        if policy(db).get("mode", "local") == "integrated":
            raise HTTPException(409, "No modo integrado, o lançamento é feito no ERP.")
        tolerance = 0  # Lançamento automático só com totais exatamente iguais aos do documento.
        done, skipped = [], []
        for item in data.items:
            try:
                stock.lock(db)
                purchase = get_purchase(db, user, item.id, True)
                reason = duplicate_reason(db, purchase)
                if reason or not workflow.existing_evidence(db, purchase):
                    raise HTTPException(409, reason or "O anexo deixou de estar validado.")
                current_proposal = proposal(db, purchase, {"category_id": item.category_id, "due_date": item.due_date and item.due_date.isoformat()})
                if current_proposal["supplier"]["state"] == "not_found" and data.create_missing_suppliers:
                    entity = BusinessEntity(name=purchase.supplier_name.strip()[:300], tax_number=purchase.supplier_nif, is_supplier=True, active=True,
                                            category_id=item.category_id)
                    db.add(entity)
                    db.flush()
                    audit(db, user, "fornecedor_criado_conferencia", entity.id, {"name": entity.name, "nif": entity.tax_number, "purchase_id": purchase.id}, purchase.store_id)
                    current_proposal = proposal(db, purchase, {"category_id": item.category_id, "due_date": item.due_date and item.due_date.isoformat()})
                if not current_proposal["ready"]:
                    raise HTTPException(409, "; ".join(current_proposal["missing"]))
                row = create_draft(db, user, purchase, current_proposal["supplier"]["id"], purchase.document, purchase.document_type,
                                   date.fromisoformat(purchase.document_date), date.fromisoformat(current_proposal["due_date"]),
                                   current_proposal["category_id"], current_proposal["lines"],
                                   f"Lançamento proposto automaticamente: linhas a partir de {current_proposal['lines_source']}; vencimento {current_proposal['due_source']}.",
                                   tolerance, "proposta_automatica")
                procurement.confirm_document(db, user, row, procurement.Confirm(revision=0), authorize, audit)
                db.commit()
                done.append({"id": purchase.id, "procurement_id": row.id})
            except HTTPException as exc:
                db.rollback()
                skipped.append({"id": item.id, "reason": exc.detail})
        return {"launched": done, "skipped": skipped}

    # -- Pedido de documentos ao fornecedor (email existente, com pré-visualização) ----------

    @app.post("/api/invoice-workflow/request-documents/preview")
    def request_preview(data: RequestPreview, user=Depends(current), db: Session = Depends(db_session)):
        from .dossier_package import zip_bytes
        from .email_delivery import dispatch_view, preview_plan
        from .email_settings import configuration, email_address
        from .platform import cipher
        purchases = [get_purchase(db, user, identifier, True) for identifier in dict.fromkeys(data.ids)]
        if len({nif_key(p.supplier_nif) for p in purchases}) != 1:
            raise HTTPException(422, "Selecione documentos de um único fornecedor.")
        try:
            recipient = email_address(data.to)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        config_row, config = configuration(db)
        if not config_row or not config["enabled"]:
            raise HTTPException(422, "Ative o envio de email (SMTP) em Configurações → Email.")
        output = io.StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Fornecedor", "NIF", "Tipo", "Documento", "Data", "Total"])
        for p in purchases:
            writer.writerow([p.supplier_name, p.supplier_nif, p.document_type, p.document, p.document_date, f"{abs(p.amount_cents) / 100:.2f}".replace(".", ",")])
        raw = output.getvalue().encode("utf-8-sig")
        key = "documentos-pedidos.csv"
        dispatch = EmailDispatch(id=str(uuid4()), kind="supplier_documents", request_key=str(uuid4()), created_by=user.id, created_at=now(),
                                 start_date=min(p.document_date for p in purchases), end_date=max(p.document_date for p in purchases),
                                 store_ids=json.dumps(sorted({p.store_id for p in purchases})), config_revision=config_row.revision,
                                 payload_ciphertext=cipher().encrypt(zip_bytes({key: raw})),
                                 draft=json.dumps({"to": [recipient], "cc": [], "subject": data.subject, "message": data.message,
                                                   "selected_keys": [key], "format": "individual", "allow_split": False}),
                                 manifest=json.dumps({"files": [{"key": key, "sha256": hashlib.sha256(raw).hexdigest(), "supplier": purchases[0].supplier_name,
                                                                 "nif": purchases[0].supplier_nif, "document": f"{len(purchases)} documento(s)",
                                                                 "date": max(p.document_date for p in purchases),
                                                                 "amount_cents": sum(abs(p.amount_cents) for p in purchases), "original_name": key}],
                                                      "purchase_ids": [p.id for p in purchases]}))
        db.add(dispatch)
        db.flush()
        preview_plan(db, dispatch, json.loads(dispatch.draft), config)
        for p in purchases:
            audit(db, user, "documento_pedido_preparado", p.id, {"dispatch_id": dispatch.id, "to": recipient}, p.store_id)
        db.commit()
        return dispatch_view(db, dispatch)

    @app.post("/api/invoice-workflow/request-documents/{dispatch_id}/send")
    def request_send(dispatch_id: str, payload: SendInput, user=Depends(current), db: Session = Depends(db_session)):
        from .email_delivery import get_dispatch, submit_dispatch
        dispatch = get_dispatch(db, dispatch_id, True)
        if dispatch.kind != "supplier_documents" or dispatch.created_by != user.id and user.role != "administrador":
            raise HTTPException(404, "Pedido indisponível.")
        ids = json.loads(dispatch.manifest).get("purchase_ids", [])
        purchases = [get_purchase(db, user, identifier, True) for identifier in ids]
        result = submit_dispatch(db, get_dispatch(db, dispatch_id, True), payload, user, audit)
        if result.get("status") in ("accepted", "received"):
            for p in purchases:
                state = db.get(DocumentState, p.id) or DocumentState(purchase_id=p.id)
                db.add(state)
                if state.availability in ("por_verificar", "nao_localizado", "solicitado"):
                    state.availability = "solicitado"
                state.requested_at = now()
                audit(db, user, "documento_pedido_enviado", p.id, {"dispatch_id": dispatch_id}, p.store_id)
            db.commit()
        return result


def purchase_label(state):
    return PURCHASE_LABELS.get(state, state)
