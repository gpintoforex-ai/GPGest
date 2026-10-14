"""Documentary evidence, ERP links, preparation and delivery are independent of payments."""

import csv
import io
import json
import os
import re
import zipfile
from datetime import date, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Literal

import httpx
from fastapi import Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
from pydantic import Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import db as database
from .db import db_session
from .document_identity import identity, number_key
from .models import (
    Audit,
    DocumentERPLink,
    DocumentImport,
    DocumentPayment,
    DocumentState,
    DriveDocumentAttachment,
    ERPDocument,
    ExpenseDraft,
    PaymentDocumentLine,
    Purchase,
    PurchaseAttachment,
    ReconciliationLine,
    Store,
    User,
    WintouchSyncRun,
    now,
)
from .platform import cipher, validate_url
from .platform_models import Company
from .schemas import Strict
from .wintouch_sales import Reader
from .wintouch_sync import SyncError

Availability = Literal["por_verificar", "fisico", "digital", "nao_localizado", "solicitado"]
Treatment = Literal["por_tratar", "preparar_erp", "contabilidade", "nao_aplicavel"]
Accounting = Literal["por_preparar", "pronto", "entregue", "confirmado"]


class StateChange(Strict):
    availability: Availability | None = None
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    treatment: Treatment | None = None
    treatment_reason: str | None = Field(default=None, max_length=2000)
    accounting: Accounting | None = None


class Batch(Strict):
    ids: list[str] = Field(min_length=1, max_length=200)
    change: StateChange
    confirm: bool = False


class Reason(Strict):
    reason: str = Field(min_length=3, max_length=2000)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("Indique uma justificação com pelo menos três caracteres.")
        return value.strip()


class LinkRequest(Reason):
    erp_id: str


class SyncRequest(Strict):
    store_id: str
    start: date
    end: date
    confirm_demo: bool = False


class ExpenseLine(Strict):
    description: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=200)
    base: str = Field(max_length=30)
    rate: str = Field(max_length=20)
    tax: str = Field(max_length=30)

    @field_validator("description", "category")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Preencha a descrição e categoria de cada linha.")
        return value.strip()


class DraftRequest(Strict):
    supplier_nif: str = Field(pattern=r"^\d{9}$")
    supplier_name: str = Field(min_length=1, max_length=300)
    document: str = Field(min_length=1, max_length=100)
    document_type: Literal["FT", "FS", "FR", "ND", "NC"]
    document_date: date
    due_date: date
    total: str = Field(max_length=30)
    lines: list[ExpenseLine] = Field(min_length=1, max_length=100)
    reason: str = Field(default="", max_length=2000)
    reviewed: bool = False

    @field_validator("supplier_name", "document")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Indique o fornecedor e o número do documento.")
        return value.strip()


class PaymentLine(Strict):
    purchase_id: str
    amount_cents: int = Field(strict=True, gt=0, le=10**15)


class PaymentRequest(Strict):
    store_id: str
    payment_date: date
    amount_cents: int = Field(strict=True, gt=0, le=10**15)
    direction: Literal[-1, 1]
    entity: str = Field(default="", max_length=300)
    reference: str = Field(default="", max_length=200)
    kind: Literal["pagamento", "comissao", "juros", "sem_fatura"] = "pagamento"
    method: Literal["numerario", "transferencia", "cartao", "outro"] = "transferencia"
    source: Literal["manual", "movement", "wintouch"] = "manual"
    source_id: str | None = None
    request_key: str = Field(min_length=1, max_length=100)
    lines: list[PaymentLine] = Field(default_factory=list, max_length=200)


class PaymentAllocation(Strict):
    lines: list[PaymentLine] = Field(min_length=1, max_length=200)


def money_value(text):
    try:
        value = Decimal(text.replace(",", "."))
        if (
            not value.is_finite()
            or value < 0
            or value * 100 != (value * 100).to_integral_value()
            or value > 10**13
        ):
            raise ValueError()
        return int(value * 100)
    except (InvalidOperation, ValueError):
        raise HTTPException(
            422, "Indique valores monetários não negativos com até duas casas decimais."
        ) from None


def expense_payload(data):
    values = data.model_dump(mode="json")
    total = money_value(data.total)
    accumulated = 0
    for item in data.lines:
        base, tax = money_value(item.base), money_value(item.tax)
        try:
            rate = Decimal(item.rate.replace(",", "."))
            if not rate.is_finite() or not 0 <= rate <= 100:
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise HTTPException(
                422, "Indique a taxa de IVA de cada linha, incluindo 0 para isenção."
            ) from None
        from decimal import ROUND_HALF_UP

        expected = int((Decimal(base) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if abs(expected - tax) > 1:
            raise HTTPException(422, "O IVA da linha não corresponde à base e taxa indicadas.")
        accumulated += base + tax
    if not total or accumulated != total:
        raise HTTPException(422, "A soma das bases e IVA deve corresponder ao total proposto.")
    if data.due_date < data.document_date:
        raise HTTPException(422, "O vencimento não pode ser anterior à emissão.")
    return values


def comparison(purchase, erp):
    exact = identity(purchase.supplier_nif, purchase.document_type, purchase.document) == identity(
        erp.supplier_nif, erp.document_type, erp.document
    )
    # A type prefix may be absent from the ERP supplier reference. This is only a suggestion.
    number = number_key(purchase.document)
    without_prefix = (
        number[len(purchase.document_type) + 1 :]
        if number.startswith(purchase.document_type + " ")
        else number
    )
    same_number = number_key(erp.document) in {number, without_prefix} and bool(erp.document)
    same_nif = purchase.supplier_nif == erp.supplier_nif
    if not (same_nif and same_number):
        return None
    differences = []
    for label, actual, expected in [
        ("Tipo", erp.document_type, purchase.document_type),
        ("Data", erp.document_date, purchase.document_date),
        ("Total", erp.amount_cents, purchase.amount_cents),
    ]:
        if actual != expected:
            differences.append(label)
    if erp.cancelled:
        differences.append("Anulado no ERP")
    return {
        "status": "divergencias" if differences else "sugerida",
        "confidence": 0 if erp.cancelled else 70 + (5 if erp.document_type == purchase.document_type else 0) + (10 if erp.document_date == purchase.document_date else 0) + (15 if erp.amount_cents == purchase.amount_cents else 0),
        "exact_identity": exact,
        "differences": differences,
        "reasons": [
            "NIF coincide",
            "N.º do documento coincide (doc. de referência ERP)",
            "Tipo coincide" if erp.document_type == purchase.document_type else "Tipo divergente",
            "Data coincide" if erp.document_date == purchase.document_date else "Data divergente",
            "Total coincide" if erp.amount_cents == purchase.amount_cents else "Total divergente",
            *(["Anulado no ERP"] if erp.cancelled else []),
        ],
    }


def payment_claimed(db, purchase_id):
    return int(
        db.scalar(
            select(func.sum(PaymentDocumentLine.amount_cents)).where(
                PaymentDocumentLine.purchase_id == purchase_id
            )
        )
        or 0
    )


def register_routes(app, current, authorize, scope, record, serial):
    serialize_model = serial

    def serial(row):
        return jsonable_encoder(serialize_model(row))

    def refresh_for_review(db, row, proposed=None):
        company_id = db.info.get("company", {}).get("id")
        with database.next_platform_session() as core:
            company = core.get(Company, company_id) if company_id else None
            from .operating_modes import company_policy
            if not company or not company_policy(company)["capabilities"]["read_documents"] or "purchase_documents" not in company_policy(company)["resources"]:
                return {
                    "mode": "snapshot_local",
                    "checked_at": now().isoformat(),
                    "warning": "Sem ligação ativa: conferência limitada aos documentos ERP já importados. Exportar não lança a despesa.",
                }
            try:
                from datetime import timedelta

                from .erp_documents import snapshot

                day = date.fromisoformat((proposed or {}).get("document_date", row.document_date))
                with httpx.Client(timeout=20, follow_redirects=False) as client:
                    records = snapshot(
                        Reader(
                            client,
                            validate_url(company.api_url),
                            company.api_enterprise,
                            cipher().decrypt(company.api_key_ciphertext.encode()).decode(),
                        ),
                        (day - timedelta(days=366)).isoformat(),
                        (day + timedelta(days=366)).isoformat(),
                    )
                for item in records:
                    existing = db.scalar(
                        select(ERPDocument)
                        .where(
                            ERPDocument.enterprise_id == company.api_enterprise,
                            ERPDocument.external_id == item["external_id"],
                        )
                        .with_for_update()
                    )
                    if existing and existing.store_id != row.store_id:
                        continue
                    if not existing:
                        db.add(
                            ERPDocument(
                                store_id=row.store_id, enterprise_id=company.api_enterprise, **item
                            )
                        )
                    else:
                        for field, value in item.items():
                            setattr(existing, field, value)
                        existing.synced_at = now()
                db.flush()
                return {"mode": "consulta_erp", "checked_at": now().isoformat()}
            except Exception:
                raise HTTPException(
                    502,
                    "Falhou a nova conferência no ERP. A proposta permanece pendente; nenhuma exportação foi concluída.",
                ) from None

    def get_doc(db, user, doc_id, write=False):
        row = db.scalar(
            select(Purchase)
            .where(Purchase.id == doc_id)
            .with_for_update()
            .execution_options(populate_existing=True)
            if write
            else select(Purchase).where(Purchase.id == doc_id)
        )
        if not row:
            raise HTTPException(404, "Documento não encontrado.")
        authorize(user, db, row.store_id, ["administrador", "gestor"] if write else None)
        return row

    def matches(db, row):
        confirmed = db.scalar(
            select(DocumentERPLink).where(
                DocumentERPLink.purchase_id == row.id, DocumentERPLink.undone_at.is_(None)
            )
        )
        candidates = []
        for erp in db.scalars(
            select(ERPDocument).where(
                ERPDocument.store_id == row.store_id,
                (ERPDocument.supplier_nif == row.supplier_nif)
                | (ERPDocument.id == confirmed.erp_id if confirmed else False),
            )
        ):
            result = comparison(row, erp)
            if result or confirmed and confirmed.erp_id == erp.id:
                candidates.append(
                    {
                        **serial(erp),
                        **(
                            result
                            or {
                                "status": "divergencias",
                                "differences": ["Identidade documental"],
                                "reasons": ["Associação manual com identidade diferente"],
                            }
                        ),
                        "linked": bool(confirmed and confirmed.erp_id == erp.id),
                        "unavailable": bool(db.scalar(select(DocumentERPLink.id).where(DocumentERPLink.erp_id == erp.id, DocumentERPLink.undone_at.is_(None), DocumentERPLink.purchase_id != row.id))),
                    }
                )
        linked = next((c for c in candidates if c["linked"]), None)
        state = (
            "divergencias"
            if linked and linked["differences"]
            else "confirmada"
            if linked
            else "divergencias"
            if any(c["differences"] for c in candidates)
            else "sugerida"
            if candidates
            else "sem_correspondencia"
        )
        return state, candidates, confirmed

    def proposed_matches(db, row, payload):
        proposed = SimpleNamespace(
            **{
                key: payload[key]
                for key in ("supplier_nif", "document", "document_type", "document_date")
            },
            amount_cents=(-1 if payload["document_type"] == "NC" else 1)
            * money_value(payload["total"]),
        )
        return [
            erp
            for erp in db.scalars(
                select(ERPDocument).where(
                    ERPDocument.store_id == row.store_id,
                    ERPDocument.supplier_nif == proposed.supplier_nif,
                )
            )
            if comparison(proposed, erp)
        ]

    def projection(db, row, detail=False):
        from .reconciliation import used

        state = db.get(DocumentState, row.id)
        document_state = {
            "availability": "por_verificar",
            "location": "",
            "notes": row.notes,
            "treatment": "por_tratar",
            "treatment_reason": "",
            "accounting": "por_preparar",
            "verified_at": None,
            "verified_by": None,
            "requested_at": None,
            "accounting_at": None,
        }
        if state:
            document_state.update(serial(state))
        verifier = (
            db.get(User, document_state["verified_by"]) if document_state["verified_by"] else None
        )
        document_state["verified_by_name"] = verifier.name if verifier else None
        erp_status, candidates, link = matches(db, row)
        attachments = [
            {"id": a.id, "name": a.name, "mime": a.mime}
            for a in db.scalars(
                select(PurchaseAttachment).where(PurchaseAttachment.purchase_id == row.id)
            )
        ]
        attachments.extend(
            {
                "id": "drive:" + a.id,
                "name": a.name,
                "mime": a.mime,
                "source": "drive",
                "view_url": "/api/drive-attachments/" + a.id + "/view",
                "drive_url": "https://drive.google.com/file/d/" + a.drive_id + "/view",
            }
            for a in db.scalars(
                select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id == row.id)
            )
        )
        payments = []
        from .models import DocumentScan
        scans = {"drive:" + r.attachment_id: r for r in db.scalars(select(DocumentScan).where(DocumentScan.purchase_id == row.id, DocumentScan.status == "cloud_saved", DocumentScan.attachment_id.is_not(None)))}
        for attachment in attachments:
            if attachment["id"] in scans:
                scan = scans[attachment["id"]]
                attachment.update(scan_id=scan.id, validation_status=scan.validation_status, view_url="/api/scans/"+scan.id+"/pdf")
        for allocation, payment in db.execute(
            select(PaymentDocumentLine, DocumentPayment)
            .join(DocumentPayment)
            .where(PaymentDocumentLine.purchase_id == row.id)
        ):
            bank_used = used(db, "payment", payment.id)
            payments.append(
                {
                    **serial(payment),
                    "allocation_id": allocation.id,
                    "document_amount_cents": allocation.amount_cents,
                    "reconciled_cents": bank_used,
                    "bank_status": "reconciliado"
                    if bank_used >= payment.amount_cents
                    else "parcial"
                    if bank_used
                    else "pendente",
                }
            )
        legacy = used(db, "purchase", row.id) - payment_claimed(db, row.id)
        paid = legacy + sum(p["document_amount_cents"] for p in payments)
        payment_status = (
            "pago" if paid >= abs(row.amount_cents) else "parcial" if paid else "por_pagar"
        )
        fully = legacy + sum(
            p["document_amount_cents"] for p in payments if p["bank_status"] == "reconciliado"
        )
        bank_status = (
            "reconciliado"
            if fully >= abs(row.amount_cents)
            else "parcial"
            if legacy or any(p["reconciled_cents"] for p in payments)
            else "pendente"
        )
        created = (
            row.created_at.replace(tzinfo=timezone.utc)
            if row.created_at.tzinfo is None
            else row.created_at
        )
        age = (
            max(0, (now().date() - created.date()).days)
            if document_state["availability"] not in {"fisico", "digital"}
            else 0
        )
        batch = db.get(DocumentImport, row.import_id) if row.import_id else None
        draft = db.get(ExpenseDraft, row.id)
        result = {
            **serial(row),
            **document_state,
            "erp_status": erp_status,
            "erp_candidates": candidates,
            "erp_link_id": link.id if link else None,
            "attachment_status": "com_anexo" if attachments else "sem_anexo",
            "attachments": attachments,
            "pending_days": age,
            "paid_cents": paid,
            "remaining_cents": max(0, abs(row.amount_cents) - paid),
            "payment_status": payment_status,
            "bank_status": bank_status,
            "payments": payments,
            "legacy_bank_payment_cents": legacy,
            "import": serial(batch) if batch else None,
            "draft": {**serial(draft), "payload": json.loads(draft.payload)} if draft else None,
        }
        if detail:
            events = list(db.scalars(select(Audit).where(Audit.target_id == row.id)))
            payment_ids = {p["id"] for p in payments}
            payment_ids.update(
                json.loads(event.detail).get("payment_id")
                for event in events
                if json.loads(event.detail).get("payment_id")
            )
            target_ids = {row.id} | payment_ids
            for payment in db.scalars(
                select(DocumentPayment).where(DocumentPayment.id.in_(payment_ids))
            ):
                if payment.source in {"movement", "wintouch"}:
                    target_ids.add(payment.source_id)
            target_ids.update(
                db.scalars(
                    select(ReconciliationLine.reconciliation_id).where(
                        (
                            (ReconciliationLine.source == "purchase")
                            & (ReconciliationLine.source_id == row.id)
                        )
                        | (
                            (ReconciliationLine.source == "payment")
                            & ReconciliationLine.source_id.in_(payment_ids)
                        )
                        | (
                            ReconciliationLine.source.in_(["movement", "wintouch"])
                            & ReconciliationLine.source_id.in_(target_ids)
                        )
                    )
                )
            )
            result["history"] = [
                {
                    **serial(a),
                    "actor": (
                        db.get(User, a.user_id).name if db.get(User, a.user_id) else a.user_id
                    ),
                    "detail": json.loads(a.detail),
                }
                for a in db.scalars(
                    select(Audit)
                    .where(Audit.target_id.in_(target_ids))
                    .order_by(Audit.created_at.desc())
                )
            ]
            result["erp_links_history"] = [
                serial(a)
                for a in db.scalars(
                    select(DocumentERPLink).where(DocumentERPLink.purchase_id == row.id)
                )
            ]
        return result

    def state_change(db, user, row, change):
        state = db.get(DocumentState, row.id)
        if not state:
            state = DocumentState(purchase_id=row.id, notes=row.notes)
            db.add(state)
            db.flush()
        before = serial(state)
        patch = change.model_dump(exclude_none=True)
        if not patch:
            raise HTTPException(422, "Selecione uma alteração.")
        for field, value in patch.items():
            setattr(state, field, value.strip() if isinstance(value, str) else value)
        if state.treatment == "nao_aplicavel" and len(state.treatment_reason.strip()) < 3:
            raise HTTPException(422, "Justifique o tratamento não aplicável.")
        if "availability" in patch or "location" in patch:
            state.verified_at, state.verified_by = now(), user.id
            if state.availability == "solicitado" and (
                before["availability"] != "solicitado" or not state.requested_at
            ):
                state.requested_at = now()
        if "accounting" in patch:
            if state.accounting in {
                "pronto",
                "entregue",
                "confirmado",
            } and state.availability not in {"fisico", "digital"}:
                raise HTTPException(
                    422, "Confirme a disponibilidade física ou digital antes de preparar a entrega."
                )
            allowed = {
                "por_preparar": {"por_preparar", "pronto"},
                "pronto": {"por_preparar", "pronto", "entregue"},
                "entregue": {"pronto", "entregue", "confirmado"},
                "confirmado": {"entregue", "confirmado"},
            }
            if state.accounting not in allowed[before["accounting"]]:
                raise HTTPException(
                    422,
                    "Prepare a entrega antes de registar a entrega ou confirmação da contabilidade.",
                )
            state.accounting_at, state.accounting_by = now(), user.id
        record(
            db,
            user,
            "conferencia_documental_alterada",
            row.id,
            {"before": before, "after": serial(state)},
            row.store_id,
        )

    @app.get("/api/documents")
    def documents(user=Depends(current), db: Session = Depends(db_session)):
        return [
            projection(db, r)
            for r in db.scalars(
                select(Purchase)
                .where(Purchase.store_id.in_(scope(user, db)))
                .order_by(Purchase.document_date.desc())
            )
        ]

    @app.get("/api/documents/{doc_id}/detail")
    def detail(doc_id: str, user=Depends(current), db: Session = Depends(db_session)):
        return projection(db, get_doc(db, user, doc_id), True)

    @app.post("/api/documents/{doc_id}/state")
    def change_state(
        doc_id: str, data: StateChange, user=Depends(current), db: Session = Depends(db_session)
    ):
        row = get_doc(db, user, doc_id, True)
        state_change(db, user, row, data)
        db.commit()
        return projection(db, row, True)

    @app.post("/api/documents/batch")
    def batch(data: Batch, user=Depends(current), db: Session = Depends(db_session)):
        rows = [get_doc(db, user, i, True) for i in sorted(set(data.ids))]
        summary = [
            {
                "id": r.id,
                "document": r.document,
                "supplier_name": r.supplier_name,
                "change": data.change.model_dump(exclude_none=True),
            }
            for r in rows
        ]
        if data.confirm:
            for row in rows:
                state_change(db, user, row, data.change)
            db.commit()
        return {"documents": summary, "confirmed": data.confirm}

    @app.get("/api/documents/erp-catalog")
    def erp_catalog(user=Depends(current), db: Session = Depends(db_session)):
        return [
            serial(r)
            for r in db.scalars(
                select(ERPDocument).where(ERPDocument.store_id.in_(scope(user, db)))
            )
        ]

    @app.post("/api/documents/{doc_id}/erp-link")
    def link_erp(
        doc_id: str, data: LinkRequest, user=Depends(current), db: Session = Depends(db_session)
    ):
        row = get_doc(db, user, doc_id, True)
        erp = db.scalar(select(ERPDocument).where(ERPDocument.id == data.erp_id).with_for_update())
        if (
            not erp
            or erp.store_id != row.store_id
            or erp.cancelled
            or row.origin == "efatura_testes"
        ):
            raise HTTPException(
                422,
                "Selecione um documento ERP ativo da mesma loja. Documentos de teste não podem ser associados.",
            )
        if db.scalar(
            select(DocumentERPLink.id).where(
                DocumentERPLink.undone_at.is_(None),
                (DocumentERPLink.purchase_id == row.id) | (DocumentERPLink.erp_id == erp.id),
            )
        ):
            raise HTTPException(409, "Um dos documentos já tem associação confirmada.")
        match = comparison(row, erp)
        association = DocumentERPLink(purchase_id=row.id, erp_id=erp.id, reason=data.reason.strip())
        db.add(association)
        db.flush()
        record(
            db,
            user,
            "documento_erp_associado",
            row.id,
            {
                "link_id": association.id,
                "erp_id": erp.external_id,
                "reason": data.reason,
                "comparison": match,
            },
            row.store_id,
        )
        db.commit()
        return projection(db, row, True)

    @app.post("/api/documents/{doc_id}/erp-unlink")
    def unlink_erp(
        doc_id: str, data: Reason, user=Depends(current), db: Session = Depends(db_session)
    ):
        row = get_doc(db, user, doc_id, True)
        association = db.scalar(
            select(DocumentERPLink).where(
                DocumentERPLink.purchase_id == row.id, DocumentERPLink.undone_at.is_(None)
            )
        )
        if not association:
            raise HTTPException(404, "Sem associação ativa.")
        association.undone_at = now()
        record(
            db,
            user,
            "documento_erp_desassociado",
            row.id,
            {"link_id": association.id, "reason": data.reason},
            row.store_id,
        )
        db.commit()
        return projection(db, row, True)

    @app.post("/api/documents/erp-sync")
    def erp_sync(data: SyncRequest, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, data.store_id, ["administrador"])
        if not db.get(Store, data.store_id):
            raise HTTPException(404, "Loja inexistente.")
        if data.start > data.end or (data.end - data.start).days > 366:
            raise HTTPException(422, "Selecione um período de até um ano.")
        if (
            db.info.get("company", {}).get("mode", os.getenv("APP_MODE")) == "demonstracao"
            and not data.confirm_demo
        ):
            raise HTTPException(
                422, "Confirme a importação de documentos reais para esta empresa de demonstração."
            )
        company_id = db.info.get("company", {}).get("id")
        with database.next_platform_session() as core:
            company = core.get(Company, company_id) if company_id else None
            if not company or not company.api_active:
                raise HTTPException(422, "Configure a ligação Wintouch da empresa na plataforma.")
            enterprise, url = company.api_enterprise, validate_url(company.api_url)
            try:
                key = cipher().decrypt(company.api_key_ciphertext.encode()).decode()
            except Exception:
                raise HTTPException(
                    503, "Não foi possível abrir as credenciais da integração."
                ) from None
        run = WintouchSyncRun(
            enterprise_id=enterprise, resource="purchase_documents", status="em_curso"
        )
        db.add(run)
        db.commit()
        try:
            from .erp_documents import snapshot

            with httpx.Client(timeout=20, follow_redirects=False) as client:
                incoming = snapshot(
                    Reader(client, url, enterprise, key),
                    data.start.isoformat(),
                    data.end.isoformat(),
                )
            for item in incoming:
                row = db.scalar(
                    select(ERPDocument)
                    .where(
                        ERPDocument.enterprise_id == enterprise,
                        ERPDocument.external_id == item["external_id"],
                    )
                    .with_for_update()
                )
                if row and row.store_id != data.store_id:
                    raise SyncError(
                        "Um documento já foi importado noutra loja. Use a loja da importação inicial."
                    )
                if not row:
                    row = ERPDocument(store_id=data.store_id, enterprise_id=enterprise, **item)
                    db.add(row)
                    run.inserted_count += 1
                else:
                    for field, value in item.items():
                        setattr(row, field, value)
                    run.updated_count += 1
                row.synced_at = now()
            run.status, run.finished_at, run.read_count = "concluida", now(), len(incoming)
            record(
                db,
                user,
                "documentos_erp_consultados",
                run.id,
                {"count": len(incoming), "start": str(data.start), "end": str(data.end)},
                data.store_id,
            )
            db.commit()
            return {
                "status": "concluida",
                "count": len(incoming),
                "message": "Documentos ERP consultados. As correspondências aguardam confirmação.",
            }
        except Exception as exc:
            db.rollback()
            run = db.get(WintouchSyncRun, run.id)
            run.status, run.finished_at, run.inserted_count, run.updated_count = (
                "falhou",
                now(),
                0,
                0,
            )
            run.error_message = (
                str(exc)
                if isinstance(exc, SyncError)
                else "Falha ao consultar o ERP. Nenhuma alteração foi guardada."
            )
            db.commit()
            raise HTTPException(502, run.error_message) from None

    @app.post("/api/documents/{doc_id}/draft")
    def save_draft(
        doc_id: str, data: DraftRequest, user=Depends(current), db: Session = Depends(db_session)
    ):
        row = get_doc(db, user, doc_id, True)
        payload = expense_payload(data)
        if data.reviewed:
            payload["erp_check"] = refresh_for_review(db, row, payload)
        status, candidates, _ = matches(db, row)
        if status in {"confirmada", "sugerida"}:
            raise HTTPException(
                409,
                "Já existe correspondência no ERP. Confira a associação antes de preparar um lançamento.",
            )
        if data.reviewed:
            if candidates or proposed_matches(db, row, payload):
                raise HTTPException(
                    409, "Resolva as divergências com o ERP antes de rever o lançamento."
                )
            if (
                identity(data.supplier_nif, data.document_type, data.document)
                != identity(row.supplier_nif, row.document_type, row.document)
                or money_value(data.total) != abs(row.amount_cents)
                or data.document_date.isoformat() != row.document_date
            ) and len(data.reason.strip()) < 3:
                raise HTTPException(422, "Justifique a alteração dos dados importados na proposta.")
        draft = db.get(ExpenseDraft, row.id)
        if not draft:
            draft = ExpenseDraft(purchase_id=row.id)
            db.add(draft)
        draft.payload = json.dumps(payload, ensure_ascii=False)
        draft.status = "revisto" if data.reviewed else "preparado"
        draft.reviewed_at, draft.reviewed_by = (now(), user.id) if data.reviewed else (None, None)
        draft.exported_at = None
        state = db.get(DocumentState, row.id)
        if not state:
            state = DocumentState(purchase_id=row.id, notes=row.notes)
            db.add(state)
        state.treatment = "preparar_erp"
        record(
            db,
            user,
            "despesa_proposta_revista" if data.reviewed else "despesa_proposta_guardada",
            row.id,
            {"payload": payload, "erp_write": False},
            row.store_id,
        )
        db.commit()
        return projection(db, row, True)

    @app.post("/api/documents/{doc_id}/draft-export")
    def export_draft(doc_id: str, user=Depends(current), db: Session = Depends(db_session)):
        row = get_doc(db, user, doc_id, True)
        draft = db.get(ExpenseDraft, row.id)
        if not draft or draft.status not in {"revisto", "exportado"}:
            raise HTTPException(422, "Reveja e confirme a proposta antes de exportar.")
        payload = json.loads(draft.payload)
        check = refresh_for_review(db, row, payload)
        if matches(db, row)[1] or proposed_matches(db, row, payload):
            raise HTTPException(
                409, "Foi encontrada uma correspondência no ERP. Reveja antes de exportar."
            )
        # Supplier documents may have changed since the last snapshot; export is explicitly not ERP posting.
        table = io.StringIO()
        writer = csv.writer(table, delimiter=";")
        writer.writerow(
            [
                "NIF",
                "Fornecedor",
                "Documento",
                "Tipo",
                "Emissão",
                "Vencimento",
                "Categoria",
                "Descrição",
                "Base EUR",
                "Taxa IVA",
                "IVA EUR",
                "Total documento EUR",
                "Estado",
            ]
        )
        for item in payload["lines"]:
            writer.writerow(
                safe_cells(
                    [
                        payload["supplier_nif"],
                        payload["supplier_name"],
                        payload["document"],
                        payload["document_type"],
                        payload["document_date"],
                        payload["due_date"],
                        item["category"],
                        item["description"],
                        item["base"],
                        item["rate"],
                        item["tax"],
                        payload["total"],
                        "Proposta revista — não lançada no ERP",
                    ]
                )
            )
        draft.status, draft.exported_at = "exportado", now()
        record(
            db,
            user,
            "despesa_proposta_exportada",
            row.id,
            {"erp_write": False, "erp_check": check},
            row.store_id,
        )
        db.commit()
        return Response(
            table.getvalue().encode("utf-8-sig"),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=proposta-despesa.csv"},
        )

    @app.post("/api/documents/export-package")
    def export_package(data: Batch, user=Depends(current), db: Session = Depends(db_session)):
        rows = [get_doc(db, user, i) for i in sorted(set(data.ids))]
        return build_package(rows, user, db)

    def build_package(rows, user, db, extra_files=None, filename="entrega-contabilidade.zip"):
        buffer = io.BytesIO()
        listing = io.StringIO()
        writer = csv.writer(listing, delimiter=";")
        writer.writerow(
            [
                "ID",
                "NIF",
                "Fornecedor",
                "Documento",
                "Tipo",
                "Data",
                "Base EUR",
                "IVA EUR",
                "Total EUR",
                "Disponibilidade",
                "Localização",
                "Anexos",
                "ERP",
                "Tratamento",
                "Contabilidade",
                "Pagamento",
                "Banco",
            ]
        )
        total_bytes = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for row in rows:
                result = projection(db, row)
                writer.writerow(
                    safe_cells(
                        [
                            row.id,
                            row.supplier_nif,
                            row.supplier_name,
                            row.document,
                            row.document_type,
                            date.fromisoformat(row.document_date).strftime("%d/%m/%Y"),
                            euros(row.base_cents) if row.base_cents is not None else "",
                            euros(row.tax_cents),
                            euros(row.amount_cents),
                            result["availability"],
                            result["location"],
                            result["attachment_status"],
                            result["erp_status"],
                            result["treatment"],
                            result["accounting"],
                            result["payment_status"],
                            result["bank_status"],
                        ]
                    )
                )
                for attachment in db.scalars(
                    select(PurchaseAttachment).where(PurchaseAttachment.purchase_id == row.id)
                ):
                    total_bytes += len(attachment.data)
                    if total_bytes > 100_000_000:
                        raise HTTPException(
                            413, "Selecione menos documentos: o pacote excede 100 MB."
                        )
                    extension = {
                        "application/pdf": ".pdf",
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                    }[attachment.mime]
                    archive.writestr(
                        f"documentos/{row.id}/{attachment.id}{extension}", attachment.data
                    )
                from .drive_documents import read_link

                for remote in db.scalars(
                    select(DriveDocumentAttachment).where(
                        DriveDocumentAttachment.purchase_id == row.id
                    )
                ):
                    raw, mime = read_link(db, remote)
                    total_bytes += len(raw)
                    if total_bytes > 100_000_000:
                        raise HTTPException(
                            413, "Selecione menos documentos: o pacote excede 100 MB."
                        )
                    extension = {
                        "application/pdf": ".pdf",
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                    }[mime]
                    archive.writestr(f"documentos/{row.id}/{remote.id}{extension}", raw)
                record(
                    db,
                    user,
                    "pacote_contabilidade_exportado",
                    row.id,
                    {"delivery_changed": False, "attachments": len(result["attachments"])},
                    row.store_id,
                )
            archive.writestr("controlo.csv", listing.getvalue().encode("utf-8-sig"))
            archive.writestr(
                "LEIA-ME.txt",
                "Exportar não marca os documentos como entregues nem lançados no ERP. Os documentos físicos sem anexo devem acompanhar a listagem. Os nomes dos anexos usam identificadores da aplicação.\n",
            )
            for name, contents in (extra_files or {}).items():
                archive.writestr(name, contents)
        db.commit()
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    from .accounting_delivery import register_routes as register_delivery_routes

    register_delivery_routes(app, current, authorize, record, projection, build_package)

    @app.get("/api/document-payments")
    def payments(user=Depends(current), db: Session = Depends(db_session)):
        from .reconciliation import used

        return [
            {
                **serial(p),
                "reconciled_cents": used(db, "payment", p.id),
                "document_lines": [
                    serial(line)
                    for line in db.scalars(
                        select(PaymentDocumentLine).where(PaymentDocumentLine.payment_id == p.id)
                    )
                ],
            }
            for p in db.scalars(
                select(DocumentPayment).where(DocumentPayment.store_id.in_(scope(user, db)))
            )
        ]

    @app.post("/api/document-payments")
    def create_payment(
        data: PaymentRequest, user=Depends(current), db: Session = Depends(db_session)
    ):
        authorize(user, db, data.store_id, ["administrador", "gestor"])
        if not db.get(Store, data.store_id):
            raise HTTPException(404, "Loja inexistente.")
        from .models import CashSession, Movement, Register, WintouchSaleRecord
        from .reconciliation import used

        if len({line.purchase_id for line in data.lines}) != len(data.lines):
            raise HTTPException(422, "Selecione cada documento apenas uma vez.")
        if sum(line.amount_cents for line in data.lines) > data.amount_cents:
            raise HTTPException(422, "A distribuição excede o pagamento.")
        existing = db.scalar(
            select(DocumentPayment).where(DocumentPayment.request_key == data.request_key)
        )
        if existing:
            authorize(user, db, existing.store_id, ["administrador", "gestor"])
            saved = {
                key: getattr(existing, key)
                for key in data.model_dump(exclude={"lines", "payment_date"})
            }
            requested = data.model_dump(exclude={"lines", "payment_date"})
            existing_lines = sorted(
                (line.purchase_id, line.amount_cents)
                for line in db.scalars(
                    select(PaymentDocumentLine).where(PaymentDocumentLine.payment_id == existing.id)
                )
            )
            if (
                saved != requested
                or existing.payment_date != data.payment_date.isoformat()
                or existing_lines
                != sorted((line.purchase_id, line.amount_cents) for line in data.lines)
            ):
                raise HTTPException(409, "A referência da operação já foi usada.")
            return serial(existing)
        if data.kind != "pagamento" and data.lines:
            raise HTTPException(
                422, "Comissões, juros e movimentos sem fatura não podem liquidar faturas."
            )
        if data.source != "manual":
            model = Movement if data.source == "movement" else WintouchSaleRecord
            source = db.scalar(select(model).where(model.id == data.source_id).with_for_update())
            if not source:
                raise HTTPException(404, "Pagamento de origem não encontrado.")
            if data.source == "movement":
                session = db.get(CashSession, source.session_id)
                store = db.get(Register, session.register_id).store_id
                amount, direction = source.amount_cents, source.direction
                method, source_day = source.payment, source.occurred_at
                valid = (
                    not source.reversed_by
                    and not source.reversal_of
                    and not source.transfer_id
                    and source.kind not in {"deposito", "reforco", "transferencia_caixa"}
                )
            else:
                values = json.loads(source.data)
                store = source.store_id
                amount, direction = values.get("amount_cents"), values.get("direction")
                from .cash_sheet import payment_key

                method, _ = payment_key(values)
                source_day = values.get("occurred_at") or source.document_date
                valid = source.active and source.kind == "real"
            from .cash_sheet import day as local_day
            from .models import ReconciliationIgnore

            valid = valid and not db.scalar(
                select(ReconciliationIgnore.id).where(
                    ReconciliationIgnore.source == data.source,
                    ReconciliationIgnore.source_id == data.source_id,
                )
            )
            valid = (
                valid
                and method == data.method
                and local_day(source_day) == data.payment_date.isoformat()
            )
            if not valid or (store, amount, direction) != (
                data.store_id,
                data.amount_cents,
                data.direction,
            ):
                raise HTTPException(422, "Os dados devem corresponder ao pagamento real de origem.")
            if db.scalar(
                select(DocumentPayment.id).where(
                    DocumentPayment.source == data.source,
                    DocumentPayment.source_id == data.source_id,
                )
            ):
                raise HTTPException(
                    409, "O pagamento de origem já foi identificado. Use o pagamento existente."
                )
        elif data.source_id:
            raise HTTPException(422, "Um pagamento manual não pode indicar origem externa.")
        rows = {
            line.purchase_id: get_doc(db, user, line.purchase_id, True)
            for line in sorted(data.lines, key=lambda line: line.purchase_id)
        }
        for allocation in data.lines:
            row = rows[allocation.purchase_id]
            if (
                row.store_id != data.store_id
                or -row.amount_cents * data.direction <= 0
                or row.origin == "efatura_testes"
            ):
                raise HTTPException(
                    422, "Selecione documentos da mesma loja, sentido e ambiente real."
                )
            if allocation.amount_cents > abs(row.amount_cents) - used(db, "purchase", row.id):
                raise HTTPException(409, "O valor excede o saldo em aberto do documento.")
        payment = DocumentPayment(
            **data.model_dump(exclude={"lines", "payment_date"}),
            payment_date=data.payment_date.isoformat(),
        )
        db.add(payment)
        db.flush()
        for allocation in data.lines:
            db.add(PaymentDocumentLine(payment_id=payment.id, **allocation.model_dump()))
            record(
                db,
                user,
                "pagamento_documento_identificado",
                allocation.purchase_id,
                {
                    "payment_id": payment.id,
                    "amount_cents": allocation.amount_cents,
                    "bank_reconciled": False,
                },
                data.store_id,
            )
        record(
            db,
            user,
            "pagamento_identificado",
            payment.id,
            {"amount_cents": payment.amount_cents, "source": payment.source, "kind": payment.kind},
            data.store_id,
        )
        db.commit()
        return serial(payment)

    @app.post("/api/document-payments/{payment_id}/allocate")
    def allocate_existing_payment(
        payment_id: str,
        data: PaymentAllocation,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        payment = db.get(DocumentPayment, payment_id)
        if not payment:
            raise HTTPException(404, "Pagamento não encontrado.")
        authorize(user, db, payment.store_id, ["administrador", "gestor"])
        if payment.kind != "pagamento":
            raise HTTPException(422, "Este movimento não liquida faturas.")
        from .reconciliation import used, valid_payment_origin

        if payment.source in {"movement", "wintouch"}:
            from .reconciliation import MODELS

            db.scalar(
                select(MODELS[payment.source])
                .where(MODELS[payment.source].id == payment.source_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        payment = db.scalar(
            select(DocumentPayment)
            .where(DocumentPayment.id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not valid_payment_origin(db, payment):
            raise HTTPException(
                409,
                "O pagamento de origem foi alterado ou estornado. Confira a origem antes de distribuir.",
            )

        if len({line.purchase_id for line in data.lines}) != len(data.lines):
            raise HTTPException(422, "Documento repetido na distribuição.")
        claimed = int(
            db.scalar(
                select(func.sum(PaymentDocumentLine.amount_cents)).where(
                    PaymentDocumentLine.payment_id == payment.id
                )
            )
            or 0
        )
        if claimed + sum(line.amount_cents for line in data.lines) > payment.amount_cents:
            raise HTTPException(409, "A distribuição excede o pagamento disponível.")
        rows = {
            line.purchase_id: get_doc(db, user, line.purchase_id, True)
            for line in sorted(data.lines, key=lambda line: line.purchase_id)
        }
        for line in data.lines:
            row = rows[line.purchase_id]
            if (
                row.store_id != payment.store_id
                or -row.amount_cents * payment.direction <= 0
                or row.origin == "efatura_testes"
            ):
                raise HTTPException(422, "Documento incompatível com o pagamento.")
            if line.amount_cents > abs(row.amount_cents) - used(db, "purchase", row.id):
                raise HTTPException(409, "Valor superior ao saldo em aberto.")
            if db.scalar(
                select(PaymentDocumentLine.id).where(
                    PaymentDocumentLine.payment_id == payment.id,
                    PaymentDocumentLine.purchase_id == row.id,
                )
            ):
                raise HTTPException(409, "Este documento já está associado ao pagamento.")
            db.add(PaymentDocumentLine(payment_id=payment.id, **line.model_dump()))
            record(
                db,
                user,
                "pagamento_documento_identificado",
                row.id,
                {"payment_id": payment.id, "amount_cents": line.amount_cents},
                row.store_id,
            )
        db.commit()
        return {"ok": True}

    @app.post("/api/document-payments/{payment_id}/allocations/{allocation_id}/remove")
    def remove_payment_line(
        payment_id: str,
        allocation_id: str,
        data: Reason,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        payment = db.scalar(
            select(DocumentPayment).where(DocumentPayment.id == payment_id).with_for_update()
        )
        if not payment:
            raise HTTPException(404, "Pagamento não encontrado.")
        authorize(user, db, payment.store_id, ["administrador", "gestor"])
        from .reconciliation import used

        if used(db, "payment", payment_id):
            raise HTTPException(
                409, "Desfaça a reconciliação bancária antes de alterar a distribuição."
            )
        allocation = db.get(PaymentDocumentLine, allocation_id)
        if not allocation or allocation.payment_id != payment_id:
            raise HTTPException(404, "Associação não encontrada.")
        get_doc(db, user, allocation.purchase_id, True)
        record(
            db,
            user,
            "pagamento_documento_desassociado",
            allocation.purchase_id,
            {
                "payment_id": payment_id,
                "amount_cents": allocation.amount_cents,
                "reason": data.reason,
            },
            payment.store_id,
        )
        db.delete(allocation)
        db.commit()
        return {"ok": True}

    @app.get("/api/documents/attachments/{attachment_id}/view")
    def view_attachment(
        attachment_id: str, user=Depends(current), db: Session = Depends(db_session)
    ):
        attachment = db.get(PurchaseAttachment, attachment_id)
        if not attachment:
            raise HTTPException(404, "Anexo não encontrado.")
        get_doc(db, user, attachment.purchase_id)
        return Response(
            attachment.data,
            media_type=attachment.mime,
            headers={"Content-Disposition": "inline", "Content-Security-Policy": "sandbox"},
        )


def safe_cells(cells):
    return [
        "'" + str(v)
        if str(v).lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
        and not isinstance(v, (int, float))
        and not re.fullmatch(r"-?\d+(?:[.,]\d+)?", str(v))
        else str(v)
        for v in cells
    ]


def euros(cents):
    return f"{Decimal(cents) / 100:.2f}".replace(".", ",")
