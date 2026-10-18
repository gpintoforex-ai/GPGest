import hashlib

from app.document_projection import DocumentContext, WorkflowContext
from app.invoice_workflow import summarize
from app.models import (
    DocumentERPLink,
    DocumentPayment,
    ERPDocument,
    InvoiceEvidenceValidation,
    PaymentDocumentLine,
    Purchase,
    PurchaseAttachment,
    Reconciliation,
    ReconciliationLine,
    Register,
)
from app.reconciliation import used
from sqlalchemy import event, select


def documents_fixture(setup, count=60):
    client, ids, factory = setup
    with factory.begin() as db:
        store_id = db.get(Register, ids["registers"][0]).store_id
        documents = [
            Purchase(
                store_id=store_id,
                supplier_nif=f"500{i:06}",
                supplier_name="Fornecedor fictício",
                document=f"FT TESTE/{i}",
                document_type="FT",
                document_date="2026-10-01",
                amount_cents=10000,
                tax_cents=0,
                origin="efatura",
            )
            for i in range(count)
        ]
        db.add_all(documents)
        db.flush()
        raw = b"fictitious attached content"
        attachment = PurchaseAttachment(
            purchase_id=documents[0].id, name="fatura.pdf", mime="application/pdf", data=raw
        )
        db.add(attachment)
        db.flush()
        db.add(
            InvoiceEvidenceValidation(
                purchase_id=documents[0].id,
                attachment_key=attachment.id,
                digest=hashlib.sha256(raw).hexdigest(),
                source="manual",
                user_id=ids["operator"],
            )
        )
        erp = ERPDocument(
            store_id=store_id,
            enterprise_id="test",
            external_id="test-1",
            supplier_nif=documents[0].supplier_nif,
            supplier_name=documents[0].supplier_name,
            document=documents[0].document,
            internal_document="COMPRA TESTE/1",
            document_type="FT",
            document_date="2026-10-01",
            amount_cents=10000,
        )
        db.add(erp)
        db.flush()
        db.add(
            DocumentERPLink(
                purchase_id=documents[0].id, erp_id=erp.id, reason="Associação de teste"
            )
        )
        identifiers = [row.id for row in documents]
    return client, factory, identifiers


def test_workflow_bulk_matches_existing_logic_without_per_invoice_queries(setup):
    _, factory, ids = documents_fixture(setup)
    with factory() as db:
        purchases = list(db.scalars(select(Purchase).where(Purchase.id.in_(ids))))
        expected = {purchase.id: summarize(db, purchase) for purchase in purchases}
        statements = []

        def listener(conn, cursor, statement, parameters, context, many):
            statements.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", listener)
        try:
            context = WorkflowContext(db, purchases)
            query_count = len(statements)
            actual = {purchase.id: summarize(db, purchase, context) for purchase in purchases}
            assert len(statements) == query_count
            assert query_count <= 7
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", listener)
        assert actual == expected
        assert actual[ids[0]]["complete"] is True
        assert "sha256" in " ".join(statements)


def test_document_list_matches_detail_and_uses_fixed_number_of_queries(setup):
    client, factory, ids = documents_fixture(setup)
    statements = []
    engine = factory.kw["bind"]

    def listener(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        response = client.get("/api/documents")
        assert response.status_code == 200, response.text
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert len(statements) < 25
    assert not any(
        "purchase_attachments.data" in statement or "document_scans.pdf_ciphertext" in statement
        for statement in statements
    )
    listed = next(row for row in response.json() if row["id"] == ids[0])
    detail = client.get(f"/api/documents/{ids[0]}/detail").json()
    assert listed == {key: detail[key] for key in listed}


def test_bulk_payment_totals_match_reconciliation_rules(setup):
    _, factory, ids = documents_fixture(setup, count=2)
    with factory.begin() as db:
        store_id = db.get(Purchase, ids[0]).store_id
        manual = DocumentPayment(
            store_id=store_id,
            payment_date="2026-10-01",
            amount_cents=5000,
            direction=-1,
            source="manual",
            request_key="test-manual",
        )
        origin = DocumentPayment(
            store_id=store_id,
            payment_date="2026-10-01",
            amount_cents=5000,
            direction=-1,
            source="movement",
            source_id="test-source",
            request_key="test-origin",
        )
        recon = Reconciliation(store_id=store_id, kind="matching")
        db.add_all([manual, origin, recon])
        db.flush()
        db.add_all(
            [
                PaymentDocumentLine(purchase_id=ids[0], payment_id=manual.id, amount_cents=2000),
                PaymentDocumentLine(purchase_id=ids[1], payment_id=origin.id, amount_cents=4000),
                ReconciliationLine(
                    reconciliation_id=recon.id,
                    source="payment",
                    source_id=manual.id,
                    amount_cents=2000,
                ),
                ReconciliationLine(
                    reconciliation_id=recon.id,
                    source="payment",
                    source_id=origin.id,
                    amount_cents=3000,
                ),
                ReconciliationLine(
                    reconciliation_id=recon.id,
                    source="movement",
                    source_id="test-source",
                    amount_cents=1000,
                ),
                ReconciliationLine(
                    reconciliation_id=recon.id,
                    source="purchase",
                    source_id=ids[0],
                    amount_cents=500,
                ),
            ]
        )
    with factory() as db:
        purchases = list(db.scalars(select(Purchase).where(Purchase.id.in_(ids))))
        context = DocumentContext(db, purchases)
        assert context.legacy[ids[0]] == 500
        for payment in db.scalars(select(DocumentPayment)):
            assert context.payment_used[payment.id] == used(db, "payment", payment.id)
