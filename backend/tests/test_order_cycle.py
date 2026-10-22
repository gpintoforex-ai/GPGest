import hashlib
import io
import json
from datetime import date
from decimal import Decimal as D
from uuid import uuid4

import pytest
from app.models import (
    ArchiveBlob,
    ArchiveDocument,
    OrderDocumentReview,
    ProcurementDocument,
    StockEntry,
)
from app.order_cycle import automated_notifications
from app.platform import cipher
from app.purchase_line_evidence import extract_lines
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from test_customer_orders import action, order, prepared, receive, simulation, smtp
from test_customer_orders import case as customer_case
from test_customer_orders import request as customer_request
from test_inventory import login, move, product, stock_case  # noqa: F401

BASE = "/api/order-cycle"


@pytest.fixture
def case(request, monkeypatch):
    monkeypatch.setattr("app.order_cycle.policy", lambda _: {"mode": "local", "provider": "none"})
    return customer_case.__wrapped__(request, monkeypatch)


def send(case, path, body, code=200, method="post"):
    response = getattr(case[0][0], method)(BASE + path, json={"request_key": str(uuid4()), **body})
    assert response.status_code == code, response.text
    return response.json()


def original(case, sim, aid, qty="5", price="10", total=6150):
    with case[0][2].begin() as db:
        db.add(
            ArchiveDocument(
                id=str(uuid4()),
                source_key="test:" + str(uuid4()),
                store_id=case[0][4],
                origin="upload",
                doc_type="fatura",
                original_name="fatura.pdf",
                mime="application/pdf",
                size=5,
                storage="base_dados",
                storage_ref="archive_blobs:test",
                sha256=hashlib.sha256(str(uuid4()).encode()).hexdigest(),
                entity_nif="500000000",
                document_code="FT",
                document_number="TEST/1",
                issue_date=date.today().isoformat(),
                total_cents=total,
                tax_cents=total - 5000,
                processing_status="processado",
                extraction=json.dumps(
                    {
                        "purchase_lines": {
                            "lines": [
                                {
                                    "article_id": aid,
                                    "reference": "REF",
                                    "description": "Artigo",
                                    "quantity": qty,
                                    "price": price,
                                    "discount": "0",
                                    "vat": "23",
                                }
                            ],
                            "warnings": [],
                        }
                    }
                ),
            )
        )
        db.flush()
        identifier = db.scalar(select(ArchiveDocument.id))
    return send(case, "/reviews", {"simulation_id": sim["id"], "document_id": identifier})


def setup_review(case, quantity="5", received="5"):
    aid = product(case[0], supplier_id=case[1])
    row, _ = customer_request(case, aid, quantity=quantity)
    sim, _ = simulation(case, [row], aid)
    sim = prepared(case, sim)
    if received:
        sim, _ = receive(case, sim, received)
    review = original(case, sim, aid, quantity)
    return aid, row, sim, review


def review_body(review, aid, qty="5", price="10", total=6150):
    return {
        "revision": review["revision"],
        "supplier_nif": "500000000",
        "document": "TEST/1",
        "document_type": "FT",
        "document_date": date.today().isoformat(),
        "due_date": date.today().isoformat(),
        "currency": "EUR",
        "total_cents": total,
        "tax_cents": 1150,
        "lines": [
            {
                "article_id": aid,
                "reference": "REF",
                "description": "Artigo",
                "quantity": qty,
                "price": price,
                "discount": "0",
                "vat": "23",
            }
        ],
        "note": "Original revisto",
    }


def decision(review):
    return {
        "revision": review["revision"],
        "fingerprint": review["comparison"]["fingerprint"],
        "reason": "Conferido com o original",
        "confirmed": True,
    }


def test_table_extraction_requires_real_columns_and_never_invents_lines():
    result = extract_lines(
        "Referência;Descrição;Qtd;Preço;Desconto;IVA\nABC;Produto;5;10,00;0;23\nERR;Ambíguo;O;10,00;0;23"
    )
    assert len(result["lines"]) == 1 and result["lines"][0]["quantity"] == "5"
    assert result["warnings"] and result["review_required"]
    assert extract_lines("Total 100,00 EUR ABC 5 10,00")["lines"] == []


def test_replenishment_explains_stock_reservations_pending_orders_and_risk(case):
    aid = product(case[0], supplier_id=case[1], minimum="3", reorder="8")
    move(case[0], aid, quantity="10")
    move(case[0], aid, kind="sale", quantity="6")
    client_order, _ = customer_request(case, aid, quantity="2")
    client_order = action(case, client_order, "reserve", 2, warehouse_id=case[0][3][0])
    path = f"/recommendations?store_id={case[0][4]}&supplier_id={case[1]}"
    result = case[0][0].get(BASE + path).json()["items"][0]
    assert D(result["free"]) == 2 and D(result["daily_sales"]) > 0
    assert result["reasons"] and result["quantity"]
    sim, _ = simulation(case, [], aid, quantity="10")
    prepared(case, sim)
    after = case[0][0].get(BASE + path).json()["items"][0]
    assert D(after["incoming"]) == 10 and D(after["quantity"]) == 0
    with case[0][2]() as db:
        assert db.scalar(select(func.count()).select_from(ProcurementDocument)) == 1


def test_replenishment_budget_expiry_and_revision(case):
    aid = product(case[0], supplier_id=case[1], reorder="20", lots=True, expiry=True)
    response = send(
        case,
        "/replenishment",
        {
            "store_id": case[0][4],
            "article_id": aid,
            "safety": "2",
            "shelf_days": 10,
            "budget_cents": 1000,
        },
    )
    assert response["revision"] == 1
    send(case, "/replenishment", {"store_id": case[0][4], "article_id": aid, "revision": 0}, 409)
    result = case[0][0].get(BASE + f"/recommendations?store_id={case[0][4]}").json()["items"][0]
    assert D(result["quantity"]) == 0 and any("validade" in w for w in result["warnings"])


def test_three_way_review_local_post_is_idempotent_without_stock_duplication(case):
    aid, client_order, sim, review = setup_review(case)
    assert order(case, client_order["id"])["ready"]
    assert D(review["comparison"]["rows"][0]["received"]) == 5
    reviewed = send(case, f"/reviews/{review['id']}", review_body(review, aid), method="put")
    assert reviewed["comparison"]["automatic_allowed"]
    approved = send(case, f"/reviews/{review['id']}/approve", decision(reviewed))
    assert approved["comparison"]["approved"]
    body = {**decision(approved), "request_key": str(uuid4())}
    posted = send(case, f"/reviews/{review['id']}/post-local", body)
    repeated = send(case, f"/reviews/{review['id']}/post-local", body)
    assert posted["procurement_id"] == repeated["procurement_id"]
    with case[0][2]() as db:
        assert sum(db.scalars(select(StockEntry.quantity))) == 5
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcurementDocument)
                .where(ProcurementDocument.kind == "invoice")
            )
            == 1
        )


def test_partial_receipt_differences_block_post_even_when_approved(case):
    aid, client_order, sim, review = setup_review(case, received="2")
    review = send(case, f"/reviews/{review['id']}", review_body(review, aid), method="put")
    assert any("superior" in s for s in review["comparison"]["discrepancies"])
    review = send(case, f"/reviews/{review['id']}/approve", decision(review))
    send(case, f"/reviews/{review['id']}/post-local", decision(review), 409)
    assert order(case, client_order["id"])["ready"]


def test_changes_to_receipt_invalidate_approval_and_erp_is_only_a_proposal(case):
    aid, _, sim, review = setup_review(case, received="2")
    review = send(case, f"/reviews/{review['id']}", review_body(review, aid), method="put")
    approved = send(case, f"/reviews/{review['id']}/approve", decision(review))
    receive(case, sim, 3)
    send(case, f"/reviews/{review['id']}/proposal", decision(approved), 409)
    fresh = case[0][0].get(BASE + "/reviews").json()["items"][0]
    assert not fresh["comparison"]["approved"]
    fresh = send(case, f"/reviews/{fresh['id']}/approve", decision(fresh))
    proposal = send(case, f"/reviews/{fresh['id']}/proposal", decision(fresh))
    assert proposal["proposal"]["erp_id"] is None and proposal["status"] == "proposed"


def test_ocr_original_import_is_queued_and_duplicates_do_not_add_conferences(case, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("PLATFORM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    aid = product(case[0])
    customer, _ = customer_request(case, aid)
    sim, _ = simulation(case, [customer], aid)
    sim = prepared(case, sim)
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(20, 800, "Referencia;Descricao;Qtd;Preco;Desconto;IVA")
    pdf.drawString(20, 780, "ABC;Artigo;5;10,00;0;23")
    pdf.save()
    response = case[0][0].post(
        "/api/archive/upload",
        data={"store_id": case[0][4]},
        files={"files": ("fatura.pdf", output.getvalue(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    doc = response.json()["results"][0]["id"]
    linked = send(case, "/reviews", {"document_id": doc, "simulation_id": sim["id"]})
    assert (
        send(case, "/reviews", {"document_id": doc, "simulation_id": sim["id"]})["id"]
        == linked["id"]
    )
    from app.archive import process_jobs

    with case[0][2]() as db:
        process_jobs(db)
        evidence = json.loads(db.get(ArchiveDocument, doc).extraction)
        assert evidence["purchase_lines"]["lines"][0]["quantity"] == "5"
        assert db.scalar(select(func.count()).select_from(OrderDocumentReview)) == 1


def test_article_proposal_validates_minimum_data_and_reuses_local_article_rules(case):
    aid, _, _, review = setup_review(case)
    body = {
        "revision": review["revision"],
        "line_index": 0,
        "article": {"code": "NOVO", "name": "Novo produto", "category": "", "supplier_id": case[1]},
    }
    send(case, f"/reviews/{review['id']}/article-proposal", body, 422)
    body["article"]["category"] = "Geral"
    proposed = send(case, f"/reviews/{review['id']}/article-proposal", body)
    assert proposed["article_proposals"][0]["state"] == "blocked_connector"
    created = send(
        case,
        f"/reviews/{review['id']}/article-proposal",
        {**body, "revision": proposed["revision"], "create_local": True},
    )
    assert created["article_proposals"][-1]["state"] == "created_local"
    send(
        case,
        f"/reviews/{review['id']}/article-proposal",
        {**body, "revision": created["revision"], "create_local": True},
        409,
    )


def test_automatic_email_requires_authorization_and_does_not_repeat(case, monkeypatch):
    calls = smtp(monkeypatch)
    aid = product(case[0])
    move(case[0], aid)
    customer, _ = customer_request(case, aid, preferred_channel="email")
    action(case, customer, "reserve", 5, warehouse_id=case[0][3][0])
    with case[0][2]() as db:
        assert automated_notifications(db)["sent"] == 0
    send(case, f"/automation/{case[0][4]}", {"enabled": True})
    with case[0][2]() as db:
        assert automated_notifications(db)["sent"] == 1
        assert automated_notifications(db)["sent"] == 0
    assert len(calls) == 1 and order(case, customer["id"])["notified"]


def test_scope_and_roles_protect_purchase_conference(case):
    _, _, _, review = setup_review(case)
    login(case[0][0], "operator")
    assert case[0][0].get(BASE + "/reviews").status_code == 403
    assert case[0][0].get(BASE + f"/recommendations?store_id={case[0][4]}").status_code == 403
    send(case, f"/reviews/{review['id']}/approve", decision(review), 403)
    assert case[0][0].get(BASE + f"/stock?store_id={case[0][4]}").status_code == 200


def test_cloud_uncertain_upload_reuses_remote_identifier_and_preserves_original(case, monkeypatch):
    from types import SimpleNamespace

    import httpx
    from app.models import AttachmentIntegrationConfig
    from app.order_cycle import transfer_original
    from cryptography.fernet import Fernet

    monkeypatch.setenv("PLATFORM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _, _, _, review = setup_review(case)
    raw = b"%PDF-cloud-original-for-integrity"
    remote, calls = {}, []
    lost = [True]

    def transport(request):
        path = request.url.path
        if path.endswith("/files/folder"):
            return httpx.Response(
                200,
                json={
                    "id": "folder",
                    "mimeType": "application/vnd.google-apps.folder",
                    "capabilities": {"canAddChildren": True},
                },
            )
        if path.endswith("/generateIds"):
            return httpx.Response(200, json={"ids": ["persisted-remote"]})
        if request.method == "POST":
            calls.append("upload")
            remote.update(
                id="persisted-remote",
                parents=["folder"],
                size=len(raw),
                mimeType="application/pdf",
                appProperties={"gpgest_hash": hashlib.sha256(raw).hexdigest()},
                md5Checksum=hashlib.md5(raw).hexdigest(),
            )
            return httpx.Response(200, json={"id": "persisted-remote"})
        if remote and "size" in request.url.params.get("fields", "") and lost[0]:
            lost[0] = False
            raise httpx.ReadTimeout("Lost confirmation", request=request)
        return httpx.Response(200, json=remote) if remote else httpx.Response(404)

    monkeypatch.setattr(
        "app.google_invoices.authorized_client",
        lambda db: (
            httpx.Client(transport=httpx.MockTransport(transport)),
            SimpleNamespace(google_email="test@example.test", drive_folder="folder"),
        ),
    )
    with case[0][2].begin() as db:
        doc = db.get(ArchiveDocument, review["document_id"])
        doc.sha256, doc.size, doc.storage_ref = (
            hashlib.sha256(raw).hexdigest(),
            len(raw),
            "archive_blobs:" + doc.id,
        )
        db.add(ArchiveBlob(document_id=doc.id, ciphertext=cipher().encrypt(raw)))
        db.add(
            AttachmentIntegrationConfig(
                id="default", google_email="test@example.test", drive_folder="folder"
            )
        )
    with case[0][2]() as db:
        with pytest.raises(httpx.ReadTimeout):
            transfer_original(db, db.get(ArchiveDocument, review["document_id"]))
        db.rollback()
    with case[0][2]() as db:
        assert db.get(OrderDocumentReview, review["id"]).drive_id == "persisted-remote"
        transfer_original(db, db.get(ArchiveDocument, review["document_id"]))
        db.commit()
        assert db.get(OrderDocumentReview, review["id"]).cloud_status == "confirmed"
        assert cipher().decrypt(db.get(ArchiveBlob, review["document_id"]).ciphertext) == raw
    assert calls == ["upload"]


def test_wintouch_product_creation_is_confirmed_and_same_request_never_posts_twice(
    case, monkeypatch
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    import httpx
    from app.models import StockArticle, WintouchProduct
    from app.wintouch_sales import Reader
    from test_wintouch_order_write import catalogs, mapping

    _, _, _, review = setup_review(case)
    proposal = {
        "line_index": 0,
        "revision": review["revision"],
        "article": {
            "code": "ERPNEW",
            "name": "Novo ERP",
            "category": "Geral",
            "unit": "un",
            "supplier_id": case[1],
        },
    }
    review = send(case, f"/reviews/{review['id']}/article-proposal", proposal)
    remote, calls = {}, []

    def transport(request):
        resource = request.url.path.removeprefix("/api/v1/")
        if request.method == "POST":
            calls.append(resource)
            remote.update(json.loads(request.content))
            return httpx.Response(201, json={"ID": remote["ID"]})
        if resource.startswith("products/"):
            return httpx.Response(200, json=remote)
        if resource == "products":
            return httpx.Response(200, json=[])
        values = catalogs().get(resource, [])
        return httpx.Response(200, json=[] if int(request.url.params.get("skip", 0)) else values)

    @contextmanager
    def connection(db, user, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            reader = Reader(client, "https://erp.test/api/v1", "ERP1", "test-key")
            reader.origin_fingerprint = "origin"
            yield reader

    monkeypatch.setattr("app.wintouch_order_write.connection", connection)
    monkeypatch.setattr("app.db.request_company", lambda db: SimpleNamespace(api_enterprise="ERP1"))
    review = send(
        case,
        f"/reviews/{review['id']}/erp-prepare",
        {**decision(review), **mapping(), "kind": "product"},
    )
    operation = review["erp_operations"][-1]
    assert not calls and operation["state"] == "prepared"
    body = {**decision(review), "request_key": str(uuid4())}
    result = send(case, f"/reviews/{review['id']}/erp-send/{operation['id']}", body)
    assert result["erp_operations"][-1]["state"] == "confirmed"
    assert (
        send(case, f"/reviews/{review['id']}/erp-send/{operation['id']}", body)["id"]
        == result["id"]
    )
    assert calls == ["products"]
    with case[0][2]() as db:
        imported = db.scalar(
            select(WintouchProduct).where(WintouchProduct.external_id == operation["id"])
        )
        assert imported and db.scalar(
            select(StockArticle).where(StockArticle.erp_id == imported.id)
        )


def test_automatic_notification_failure_has_one_visible_pending_action(case):
    from app.models import CustomerOrderEvent

    aid = product(case[0])
    move(case[0], aid)
    customer, _ = customer_request(case, aid, preferred_channel="email")
    action(case, customer, "reserve", 5, warehouse_id=case[0][3][0])
    send(case, f"/automation/{case[0][4]}", {"enabled": True})
    with case[0][2]() as db:
        assert automated_notifications(db)["failed"] == 1
        assert automated_notifications(db)["failed"] == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(CustomerOrderEvent)
                .where(CustomerOrderEvent.action == "auto_notify_error")
            )
            == 1
        )


def test_erp_catalog_cannot_cross_selected_enterprise(case, monkeypatch):
    from types import SimpleNamespace

    from app.models import WintouchProduct

    with case[0][2].begin() as db:
        first = WintouchProduct(
            enterprise_id="one", external_id="first", code="ONE", name="Primeira", unit="un"
        )
        other = WintouchProduct(
            enterprise_id="two", external_id="other", code="TWO", name="Segunda", unit="un"
        )
        db.add_all([first, other])
        db.flush()
        first_id, other_id = first.id, other.id
    monkeypatch.setattr(
        "app.inventory_imports.request_company", lambda db: SimpleNamespace(api_enterprise="one")
    )
    rows = case[0][0].get("/api/stock/erp-products").json()["rows"]
    assert [r["id"] for r in rows] == [first_id]
    assert case[0][0].post("/api/stock/erp-products", json={"erp_id": other_id}).status_code == 404
    assert case[0][0].post("/api/stock/erp-products", json={"erp_id": first_id}).status_code == 200


def test_cycle_migration_is_additive_and_repeatable(case, monkeypatch):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    migration = importlib.import_module("migrations.versions.0058_order_cycle")
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    aid = product(case[0])
    customer, _ = customer_request(case, aid)
    with case[0][2].kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()
            migration.upgrade()
        assert all(
            model.__tablename__ in inspect(connection).get_table_names(schema=schema)
            for model in migration.TABLES
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM customer_orders WHERE id=:id"), {"id": customer["id"]}
            )
            == 1
        )


def test_fiscal_duplicate_blocks_post_even_after_manual_approval(case):
    from app.models import Purchase

    aid, _, _, review = setup_review(case)
    with case[0][2].begin() as db:
        db.add(
            Purchase(
                store_id=case[0][4],
                supplier_nif="500000000",
                supplier_name="Fornecedor",
                document="TEST/1",
                document_type="FT",
                document_date=date.today().isoformat(),
                amount_cents=6150,
                origin="manual",
            )
        )
    review = send(case, f"/reviews/{review['id']}", review_body(review, aid), method="put")
    assert (
        review["comparison"]["fiscal_duplicate"] and not review["comparison"]["automatic_allowed"]
    )
    approved = send(case, f"/reviews/{review['id']}/approve", decision(review))
    send(case, f"/reviews/{review['id']}/post-local", decision(approved), 409)
