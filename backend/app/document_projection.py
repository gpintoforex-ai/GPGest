"""Request-scoped bulk reads for document lists; never download PDF/image bodies."""

from collections import defaultdict
from types import SimpleNamespace

from sqlalchemy import func, or_, select

from .models import (
    DocumentERPLink,
    DocumentImport,
    DocumentPayment,
    DocumentScan,
    DocumentState,
    DriveDocumentAttachment,
    ERPDocument,
    ExpenseDraft,
    InvoiceEvidenceValidation,
    InvoiceWorkflowCheck,
    PaymentDocumentLine,
    PurchaseAllocation,
    PurchaseAttachment,
    Reconciliation,
    ReconciliationLine,
    User,
)


def grouped(rows, key):
    result = defaultdict(list)
    for row in rows:
        result[getattr(row, key)].append(row)
    return result


class AttachmentContext:
    def __init__(self, db, ids, hashes=False):
        self.natives = defaultdict(list)
        self.drives = defaultdict(list)
        self.scans = defaultdict(list)
        self.validations = defaultdict(list)
        if not ids:
            return
        columns = [
            PurchaseAttachment.id,
            PurchaseAttachment.purchase_id,
            PurchaseAttachment.name,
            PurchaseAttachment.mime,
        ]
        if hashes:
            # PostgreSQL's built-in SHA-256 avoids transferring binary contents.
            columns.append(func.encode(func.sha256(PurchaseAttachment.data), "hex").label("digest"))
        self.natives = grouped(
            [
                SimpleNamespace(**dict(row))
                for row in db.execute(
                    select(*columns).where(PurchaseAttachment.purchase_id.in_(ids))
                ).mappings()
            ],
            "purchase_id",
        )
        self.drives = grouped(
            db.scalars(
                select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id.in_(ids))
            ),
            "purchase_id",
        )
        self.scans = grouped(
            [
                SimpleNamespace(**dict(row))
                for row in db.execute(
                    select(
                        DocumentScan.id,
                        DocumentScan.purchase_id,
                        DocumentScan.attachment_id,
                        DocumentScan.digest,
                        DocumentScan.validation_status,
                    ).where(
                        DocumentScan.purchase_id.in_(ids),
                        DocumentScan.status == "cloud_saved",
                        DocumentScan.attachment_id.is_not(None),
                    )
                ).mappings()
            ],
            "purchase_id",
        )
        if hashes:
            self.validations = grouped(
                db.scalars(
                    select(InvoiceEvidenceValidation).where(
                        InvoiceEvidenceValidation.purchase_id.in_(ids)
                    )
                ),
                "purchase_id",
            )


class WorkflowContext(AttachmentContext):
    def __init__(self, db, purchases):
        ids = [row.id for row in purchases]
        super().__init__(db, ids, hashes=True)
        self.checks = (
            {
                row.purchase_id: row
                for row in db.scalars(
                    select(InvoiceWorkflowCheck).where(InvoiceWorkflowCheck.purchase_id.in_(ids))
                )
            }
            if ids
            else {}
        )
        self.links = (
            {
                row.purchase_id: row
                for row in db.scalars(
                    select(DocumentERPLink).where(
                        DocumentERPLink.purchase_id.in_(ids), DocumentERPLink.undone_at.is_(None)
                    )
                )
            }
            if ids
            else {}
        )


class DocumentContext(AttachmentContext):
    def __init__(self, db, purchases):
        ids = [row.id for row in purchases]
        super().__init__(db, ids)
        self.states, self.users, self.imports, self.drafts = {}, {}, {}, {}
        self.links, self.erp, self.erp_owners = {}, {}, defaultdict(set)
        self.payments, self.payment_used, self.legacy = defaultdict(list), {}, {}
        if not ids:
            return
        self.states = {
            row.purchase_id: row
            for row in db.scalars(select(DocumentState).where(DocumentState.purchase_id.in_(ids)))
        }
        user_ids = {row.verified_by for row in self.states.values() if row.verified_by}
        if user_ids:
            self.users = {
                row.id: row for row in db.scalars(select(User).where(User.id.in_(user_ids)))
            }
        import_ids = {row.import_id for row in purchases if row.import_id}
        if import_ids:
            self.imports = {
                row.id: row
                for row in db.scalars(
                    select(DocumentImport).where(DocumentImport.id.in_(import_ids))
                )
            }
        self.drafts = {
            row.purchase_id: row
            for row in db.scalars(select(ExpenseDraft).where(ExpenseDraft.purchase_id.in_(ids)))
        }
        self.links = {
            row.purchase_id: row
            for row in db.scalars(
                select(DocumentERPLink).where(
                    DocumentERPLink.purchase_id.in_(ids), DocumentERPLink.undone_at.is_(None)
                )
            )
        }
        linked_ids = {row.erp_id for row in self.links.values()}
        erp_rows = list(
            db.scalars(
                select(ERPDocument).where(
                    ERPDocument.store_id.in_({row.store_id for row in purchases}),
                    or_(
                        ERPDocument.supplier_nif.in_({row.supplier_nif for row in purchases}),
                        ERPDocument.id.in_(linked_ids),
                    ),
                )
            )
        )
        self.erp = {row.id: row for row in erp_rows}
        self.erp_by_supplier = grouped(erp_rows, "supplier_nif")
        if self.erp:
            for erp_id, purchase_id in db.execute(
                select(DocumentERPLink.erp_id, DocumentERPLink.purchase_id).where(
                    DocumentERPLink.erp_id.in_(self.erp), DocumentERPLink.undone_at.is_(None)
                )
            ):
                self.erp_owners[erp_id].add(purchase_id)
        payments = {}
        for line, payment in db.execute(
            select(PaymentDocumentLine, DocumentPayment)
            .join(DocumentPayment)
            .where(PaymentDocumentLine.purchase_id.in_(ids))
        ):
            self.payments[line.purchase_id].append((line, payment))
            payments[payment.id] = payment
        # The legacy amount excludes PaymentDocumentLine allocations, which the
        # projection adds separately. This is identical to used(purchase)-claimed.
        self.legacy = {
            key: int(total)
            for key, total in db.execute(
                select(
                    ReconciliationLine.source_id,
                    func.sum(ReconciliationLine.amount_cents),
                )
                .join(Reconciliation)
                .where(
                    Reconciliation.undone_at.is_(None),
                    ReconciliationLine.source == "purchase",
                    ReconciliationLine.source_id.in_(ids),
                )
                .group_by(ReconciliationLine.source_id)
            )
        }
        for key, total in db.execute(
            select(PurchaseAllocation.purchase_id, func.sum(PurchaseAllocation.amount_cents))
            .where(PurchaseAllocation.purchase_id.in_(ids))
            .group_by(PurchaseAllocation.purchase_id)
        ):
            self.legacy[key] = self.legacy.get(key, 0) + int(total)
        if not payments:
            return
        source_ids = {
            payment.source_id
            for payment in payments.values()
            if payment.source in {"movement", "wintouch"}
        }
        related = dict(payments)
        if source_ids:
            related.update(
                {
                    payment.id: payment
                    for payment in db.scalars(
                        select(DocumentPayment).where(
                            DocumentPayment.source.in_(["movement", "wintouch"]),
                            DocumentPayment.source_id.in_(source_ids),
                        )
                    )
                }
            )
        totals = {
            (source, key): int(total)
            for source, key, total in db.execute(
                select(
                    ReconciliationLine.source,
                    ReconciliationLine.source_id,
                    func.sum(ReconciliationLine.amount_cents),
                )
                .join(Reconciliation)
                .where(
                    Reconciliation.undone_at.is_(None),
                    ReconciliationLine.source_id.in_(set(related) | source_ids),
                )
                .group_by(ReconciliationLine.source, ReconciliationLine.source_id)
            )
        }
        for payment in related.values():
            if payment.source in {"movement", "wintouch"}:
                key = (payment.source, payment.source_id)
                totals[key] = totals.get(key, 0) + totals.get(("payment", payment.id), 0)
        self.payment_used = {
            payment.id: totals.get(
                (payment.source, payment.source_id)
                if payment.source in {"movement", "wintouch"}
                else ("payment", payment.id),
                0,
            )
            for payment in payments.values()
        }

    def candidates(self, purchase):
        link = self.links.get(purchase.id)
        rows = {
            row.id: row
            for row in self.erp_by_supplier[purchase.supplier_nif]
            if row.store_id == purchase.store_id
        }
        if link and link.erp_id in self.erp:
            row = self.erp[link.erp_id]
            if row.store_id == purchase.store_id:
                rows[row.id] = row
        return rows.values()
