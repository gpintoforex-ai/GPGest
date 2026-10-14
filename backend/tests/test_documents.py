"""Document evidence, ERP registration, delivery, payment and bank are independent."""

import io
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from app.db import db_session
from app.main import app
from app.models import ERPDocument, ExpenseDraft, Movement, Purchase, Register
from sqlalchemy import func, select
from test_reconciliation import confirm, line, seed


def test_erp_confidence_reflects_date_total_and_cancellation():
    from app.documents import comparison
    values = dict(supplier_nif="510774318", document="FT A/1", document_type="FT", document_date="2026-10-10", amount_cents=12300)
    purchase = SimpleNamespace(**values)
    match = comparison(purchase, SimpleNamespace(**values, cancelled=False))
    assert match["confidence"] == 100 and not match["differences"]
    assert "N.º do documento coincide (doc. de referência ERP)" in match["reasons"]
    changed = {**values, "amount_cents": 12000, "document_date": "2026-10-11"}
    match = comparison(purchase, SimpleNamespace(**changed, cancelled=False))
    assert match["confidence"] == 75 and match["differences"] == ["Data", "Total"]
    assert comparison(purchase, SimpleNamespace(**values, cancelled=True))["confidence"] == 0


def detail(client, document):
    response = client.get(f"/api/documents/{document.id}/detail")
    assert response.status_code == 200, response.text
    return response.json()


def change(client, document, **values):
    return client.post(f"/api/documents/{document.id}/state", json=values)


def pdf(client, document):
    response = client.post(
        f"/api/purchases/{document.id}/attachments",
        files={"file": ("fatura.pdf", b"%PDF-1.7\ninvoice", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def erp(factory, purchase, **overrides):
    with factory.begin() as db:
        values = dict(
            store_id=purchase.store_id,
            enterprise_id="test",
            external_id=str(uuid4()),
            supplier_nif=purchase.supplier_nif,
            supplier_name=purchase.supplier_name,
            document=purchase.document,
            internal_document="FTCOMPRA A/1",
            document_type=purchase.document_type,
            document_date=purchase.document_date,
            amount_cents=purchase.amount_cents,
        )
        values.update(overrides)
        row = ERPDocument(**values)
        db.add(row)
        db.flush()
        return row


def associate(client, document, erp_row):
    return client.post(
        f"/api/documents/{document.id}/erp-link",
        json={"erp_id": erp_row.id, "reason": "Conferido com a fatura"},
    )


def payment(client, store, amount, documents=(), **extra):
    values = dict(
        store_id=store,
        payment_date="2026-10-10",
        amount_cents=amount,
        direction=-1,
        request_key=str(uuid4()),
        lines=[
            {"purchase_id": document.id, "amount_cents": value} for document, value in documents
        ],
    )
    values.update(extra)
    return client.post("/api/document-payments", json=values)


def draft(document, reviewed=False):
    return dict(
        supplier_nif=document.supplier_nif,
        supplier_name=document.supplier_name,
        document=document.document,
        document_type="FT",
        document_date=document.document_date,
        due_date="2026-10-30",
        total="129",
        lines=[
            {
                "description": "Eletricidade",
                "category": "Energia",
                "base": "100",
                "rate": "23",
                "tax": "23",
            },
            {
                "description": "Serviço",
                "category": "Energia",
                "base": "5",
                "rate": "20",
                "tax": "1",
            },
        ],
        reviewed=reviewed,
    )


def test_goods_erp_pdf_confirmation_does_not_pay(setup):
    client, factory, _, _, documents, _ = seed(setup)
    document = documents[0]
    pdf(client, document)
    remote = erp(factory, document)
    assert detail(client, document)["erp_status"] == "sugerida"
    result = associate(client, document, remote).json()
    assert result["erp_status"] == "confirmada"
    assert result["attachment_status"] == "com_anexo"
    assert result["availability"] == "por_verificar"
    assert result["payment_status"] == "por_pagar"
    assert result["bank_status"] == "pendente"
    assert (
        client.post(
            f"/api/documents/{document.id}/erp-unlink", json={"reason": "Rever associação"}
        ).status_code
        == 200
    )
    result = detail(client, document)
    assert result["erp_status"] == "sugerida" and result["erp_links_history"][0]["undone_at"]
    assert associate(client, document, remote).status_code == 200


def test_electricity_only_efatura_pdf_remains_expense_pending(setup):
    client, _, _, _, documents, _ = seed(setup)
    pdf(client, documents[0])
    result = detail(client, documents[0])
    assert result["erp_status"] == "sem_correspondencia"
    assert result["treatment"] == "por_tratar" and result["accounting"] == "por_preparar"
    assert result["payment_status"] == "por_pagar"


def test_paper_available_without_scan_and_requested_dates(setup):
    client, _, _, _, documents, _ = seed(setup)
    result = change(
        client, documents[0], availability="fisico", location="Pasta outubro", notes="Combustível"
    ).json()
    assert result["availability"] == "fisico" and result["attachment_status"] == "sem_anexo"
    assert result["verified_at"] and result["verified_by_name"] == "Admin"
    assert result["payment_status"] == "por_pagar"
    result = change(client, documents[0], availability="solicitado").json()
    assert result["requested_at"] and result["pending_days"] >= 0


def test_missing_paper_and_pdf_are_explicit(setup):
    client, _, _, _, documents, _ = seed(setup)
    result = change(client, documents[0], availability="nao_localizado").json()
    assert result["availability"] == "nao_localizado" and not result["attachments"]
    assert change(client, documents[0], accounting="pronto").status_code == 422


def test_erp_registration_does_not_resolve_document_missing(setup):
    client, factory, _, _, documents, _ = seed(setup)
    change(client, documents[0], availability="nao_localizado")
    result = associate(client, documents[0], erp(factory, documents[0])).json()
    assert result["erp_status"] == "confirmada" and result["availability"] == "nao_localizado"
    assert result["attachment_status"] == "sem_anexo"


def test_direct_accounting_delivery_export_is_not_delivery(setup):
    client, _, _, _, documents, _ = seed(setup)
    document = documents[0]
    pdf(client, document)
    assert (
        change(
            client, document, treatment="contabilidade", availability="digital", accounting="pronto"
        ).status_code
        == 200
    )
    package = client.post(
        "/api/documents/export-package", json={"ids": [document.id], "change": {}}
    )
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert any(name.endswith(".pdf") for name in archive.namelist())
        assert any(name.endswith(".csv") for name in archive.namelist())
    assert detail(client, document)["accounting"] == "pronto"
    result = change(client, document, accounting="entregue").json()
    assert result["treatment"] == "contabilidade" and result["erp_status"] == "sem_correspondencia"
    assert result["accounting"] == "entregue" and result["payment_status"] == "por_pagar"
    assert change(client, document, accounting="confirmado").status_code == 200


def test_one_transfer_pays_three_documents_then_bank_and_undo(setup):
    client, _, store, banks, documents, _ = seed(setup, (-30000,), (10000, 10000, 10000))
    response = payment(client, store, 30000, [(document, 10000) for document in documents])
    assert response.status_code == 200, response.text
    pay = SimpleNamespace(**response.json())
    overview = client.get("/api/reconciliation").json()
    assert all(
        row["status"] == "pago" for row in overview["internals"] if row["source"] == "purchase"
    )
    assert (
        next(row for row in overview["internals"] if row["source"] == "payment")["status"]
        == "pendente"
    )
    for document in documents:
        result = detail(client, document)
        assert result["payment_status"] == "pago" and result["bank_status"] == "pendente"
        assert result["paid_cents"] == 10000
    result = confirm(client, [line("bank", banks[0], 30000), line("payment", pay, 30000)])
    assert result.status_code == 200, result.text
    assert all(detail(client, document)["bank_status"] == "reconciliado" for document in documents)
    assert (
        confirm(client, [line("bank", banks[0], 100), line("payment", pay, 100)]).status_code == 409
    )
    assert (
        client.post(
            f"/api/reconciliation/{result.json()['id']}/undo", json={"reason": "Conferir extrato"}
        ).status_code
        == 200
    )
    assert detail(client, documents[0])["payment_status"] == "pago"
    assert detail(client, documents[0])["bank_status"] == "pendente"
    actions = {event["action"] for event in detail(client, documents[0])["history"]}
    assert {"reconciliacao_confirmada", "reconciliacao_desfeita"} <= actions


def test_two_partial_payments_correct_balance_and_audited_reversal(setup):
    client, _, store, _, documents, _ = seed(setup)
    document = documents[0]
    first = payment(client, store, 4000, [(document, 4000)])
    assert first.status_code == 200, first.text
    result = detail(client, document)
    assert result["payment_status"] == "parcial" and result["remaining_cents"] == 6000
    assert payment(client, store, 6001, [(document, 6001)]).status_code == 409
    assert payment(client, store, 6000, [(document, 6000)]).status_code == 200
    result = detail(client, document)
    assert result["payment_status"] == "pago" and result["remaining_cents"] == 0
    pay = result["payments"][0]
    assert (
        client.post(
            f"/api/document-payments/{pay['id']}/allocations/{pay['allocation_id']}/remove",
            json={"reason": "Distribuição incorreta"},
        ).status_code
        == 200
    )
    assert detail(client, document)["payment_status"] == "parcial"
    assert any(
        action["action"] == "pagamento_documento_desassociado"
        for action in detail(client, document)["history"]
    )


def test_own_transfer_does_not_create_expense_or_revenue(setup):
    client, factory, _, banks, _, _ = seed(setup, (-10000, 10000), ())
    result = confirm(
        client,
        [line("bank", bank, 10000) for bank in banks],
        kind="transferencia",
        reason="Transferência entre contas próprias",
    )
    assert result.status_code == 200, result.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 0
        assert db.scalar(select(func.count()).select_from(Purchase)) == 0


def test_reimport_preserves_evidence_state_and_credit_identity(setup):
    client, _, store, _, documents, _ = seed(setup, (), ())
    content = "Setor;Emitente;Nº Fatura / ATCUD;Tipo;Data Emissão;Total;IVA;Base Tributável\nGeral;500000001 - Energia;FT E/001 / ABC-1;FT;10/10/2026;123,00;23,00;100,00\nGeral;500000001 - Energia;NC E/001 / ABC-2;NC;10/10/2026;12,30;2,30;10,00\n".encode(
        "utf-8-sig"
    )

    def upload(data):
        return client.post(
            f"/api/purchases/import/{store}?confirm=true", files={"file": ("e-fatura.csv", data)}
        )

    first = upload(content)
    assert first.status_code == 200, first.text
    rows = client.get("/api/documents").json()
    assert len(rows) == 2 and {row["amount_cents"] for row in rows} == {12300, -1230}
    assert all(row["import"]["filename"] == "e-fatura.csv" for row in rows)
    document = SimpleNamespace(**next(row for row in rows if row["amount_cents"] > 0))
    pdf(client, document)
    change(client, document, availability="fisico")
    repeated = upload(content).json()
    assert repeated["duplicate_count"] == 2 and repeated["new_count"] == 0
    assert (
        detail(client, document)["availability"] == "fisico"
        and detail(client, document)["attachments"]
    )
    assert upload(content.replace(b"123,00", b"124,00")).status_code == 409


def test_pdf_without_payment_stays_unpaid_and_permissions_protect_attachment(setup):
    client, factory, _, _, documents, _ = seed(setup)
    attachment = pdf(client, documents[0])
    assert detail(client, documents[0])["payment_status"] == "por_pagar"
    assert client.get(f"/api/documents/attachments/{attachment['id']}/view").status_code == 200
    client.post("/api/auth/logout")
    assert client.get(f"/api/documents/attachments/{attachment['id']}/view").status_code == 401
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert change(client, documents[0], availability="digital").status_code == 403
    assert client.get(f"/api/documents/attachments/{attachment['id']}/view").status_code == 200
    with factory.begin() as db:
        db.get(Purchase, documents[0].id).store_id = db.get(
            Register, setup[1]["registers"][2]
        ).store_id
    assert client.get(f"/api/documents/attachments/{attachment['id']}/view").status_code == 403
    assert client.get("/api/documents").json() == []


def test_failed_erp_recheck_keeps_draft_pending(setup, monkeypatch):
    import app.documents as module
    import app.erp_documents as adapter

    client, factory, _, _, documents, _ = seed(setup, (), (12900,))
    document = documents[0]
    path = f"/api/documents/{document.id}/draft"
    assert client.post(path, json=draft(document)).status_code == 200

    def override():
        with factory() as db:
            db.info["company"] = {"id": "test"}
            yield db

    @contextmanager
    def core():
        yield SimpleNamespace(
            get=lambda *_: SimpleNamespace(
                api_active=True,
                erp_provider="wintouch",
                operating_mode="integrated",
                api_url="https://api.wintouchcloud.com/api",
                api_enterprise="test",
                api_key_ciphertext="encrypted",
            )
        )

    monkeypatch.setattr(module.database, "next_platform_session", core)
    monkeypatch.setattr(module, "cipher", lambda: SimpleNamespace(decrypt=lambda _: b"test-key"))

    def fail(*_):
        raise RuntimeError("ERP unavailable")

    monkeypatch.setattr(adapter, "snapshot", fail)
    app.dependency_overrides[db_session] = override
    response = client.post(path, json=draft(document, True))
    assert response.status_code == 502, response.text
    with factory() as db:
        assert db.get(ExpenseDraft, document.id).status == "preparado"
        assert db.scalar(select(func.count()).select_from(ERPDocument)) == 0


def test_multirate_expense_review_export_and_duplicate_recheck(setup):
    client, factory, _, _, documents, _ = seed(setup, (), (12900,))
    document = documents[0]
    path = f"/api/documents/{document.id}/draft"
    assert client.post(f"/api/documents/{document.id}/draft-export", json={}).status_code == 422
    assert client.post(path, json=draft(document, True)).status_code == 200
    exported = client.post(f"/api/documents/{document.id}/draft-export", json={})
    assert exported.status_code == 200, exported.text
    assert "não lançada no ERP" in exported.content.decode("utf-8-sig")
    result = detail(client, document)
    assert (
        result["draft"]["status"] == "exportado" and result["erp_status"] == "sem_correspondencia"
    )
    assert result["payment_status"] == "por_pagar"
    erp(factory, document)
    assert client.post(f"/api/documents/{document.id}/draft-export", json={}).status_code == 409


def test_matching_preserves_number_and_detects_divergences_and_manual_other_nif(setup):
    client, factory, _, _, documents, _ = seed(setup)
    document = documents[0]
    erp(factory, document, document="FT A/01")
    erp(factory, document, document="FT B/1")
    assert detail(client, document)["erp_status"] == "sem_correspondencia"
    erp(factory, document, amount_cents=10001)
    assert detail(client, document)["erp_status"] == "divergencias"
    remote = erp(factory, document, supplier_nif="599999999", document="FT Z/1")
    result = associate(client, document, remote).json()
    assert result["erp_link_id"] and result["erp_status"] == "divergencias"


def test_batch_summary_confirmation_and_atomic_validation(setup):
    client, _, _, _, documents, _ = seed(setup, (), (10000, 10000))
    payload = {
        "ids": [document.id for document in documents],
        "change": {"availability": "fisico"},
        "confirm": False,
    }
    result = client.post("/api/documents/batch", json=payload)
    assert len(result.json()["documents"]) == 2
    assert detail(client, documents[0])["availability"] == "por_verificar"
    payload["confirm"] = True
    assert client.post("/api/documents/batch", json=payload).status_code == 200
    change(client, documents[1], availability="nao_localizado")
    payload["change"] = {"accounting": "pronto"}
    assert client.post("/api/documents/batch", json=payload).status_code == 422
    assert all(detail(client, document)["accounting"] == "por_preparar" for document in documents)
    assert change(client, documents[0], treatment="nao_aplicavel").status_code == 422


def test_payment_wrapper_shared_balance_no_duplicate_or_origin_reversal(setup):
    client, _, store, banks, documents, movements = seed(setup, (-10000,), (10000,), (-10000,))
    response = payment(
        client, store, 10000, [(documents[0], 10000)], source="movement", source_id=movements[0].id
    )
    assert response.status_code == 200, response.text
    pay = SimpleNamespace(**response.json())
    assert (
        payment(client, store, 10000, source="movement", source_id=movements[0].id).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/movements/{movements[0].id}/reverse", json={"reason": "Conferir"}
        ).status_code
        == 409
    )
    result = confirm(client, [line("bank", banks[0], 10000), line("payment", pay, 10000)])
    assert result.status_code == 200, result.text
    assert (
        confirm(client, [line("bank", banks[0], 1), line("movement", movements[0], 1)]).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/movements/{movements[0].id}/reverse", json={"reason": "Conferir"}
        ).status_code
        == 409
    )


def test_commission_no_invoice_and_idempotency_rejects_changed_payload(setup):
    client, _, store, banks, _, _ = seed(setup, (-10000,), ())
    key = str(uuid4())
    response = payment(client, store, 10000, kind="comissao", request_key=key)
    assert response.status_code == 200, response.text
    assert (
        payment(client, store, 10000, kind="comissao", request_key=key).json()["id"]
        == response.json()["id"]
    )
    assert payment(client, store, 9999, kind="comissao", request_key=key).status_code == 409
    assert (
        confirm(
            client,
            [
                line("bank", banks[0], 10000),
                line("payment", SimpleNamespace(**response.json()), 10000),
            ],
        ).status_code
        == 200
    )


def test_reversed_source_payment_cannot_be_distributed_later(setup):
    client, _, store, _, documents, movements = seed(setup, (), (10000,), (-10000,))
    response = payment(client, store, 10000, source="movement", source_id=movements[0].id)
    assert response.status_code == 200, response.text
    reversed_source = client.post(
        f"/api/movements/{movements[0].id}/reverse", json={"reason": "Pagamento anulado na origem"}
    )
    assert reversed_source.status_code == 200, reversed_source.text
    result = client.post(
        f"/api/document-payments/{response.json()['id']}/allocate",
        json={"lines": [{"purchase_id": documents[0].id, "amount_cents": 10000}]},
    )
    assert result.status_code == 409, result.text
    assert detail(client, documents[0])["payment_status"] == "por_pagar"


def test_proposed_identity_is_rechecked_before_review_and_export(setup):
    client, factory, _, _, documents, _ = seed(setup, (), (12900,))
    document = documents[0]
    payload = draft(document, True)
    payload.update(document="FT NEW/1", reason="Número conferido no PDF")
    path = f"/api/documents/{document.id}/draft"
    assert client.post(path, json=payload).status_code == 200
    erp(factory, document, document="FT NEW/1")
    assert detail(client, document)["erp_status"] == "sem_correspondencia"
    assert client.post(path, json=payload).status_code == 409
    assert client.post(f"/api/documents/{document.id}/draft-export", json={}).status_code == 409
