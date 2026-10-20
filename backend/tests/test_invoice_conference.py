"""Conferência e-Fatura ↔ anexos ↔ compras: Drive, estados independentes, propostas e lançamentos."""

import hashlib
import json

import pytest
from app.models import (
    AttachmentIntegrationConfig,
    BusinessEntity,
    DocumentImport,
    DriveDocumentAttachment,
    InvoiceWorkflowCheck,
    ProcurementDocument,
    Purchase,
    StockEntry,
)
from sqlalchemy import func, select
from test_google_invoices import google_case  # noqa: F401
from test_invoice_workflow import verify
from test_invoice_workflow import workflow as base_workflow  # noqa: F401
from test_platform import platform  # noqa: F401

PERIOD = {"start": "2026-10-01", "end": "2026-10-31"}
CSV_HEADER = "NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;IVA\n"


@pytest.fixture
def conference(base_workflow, monkeypatch):  # noqa: F811
    client, factory, drive, options, identifier, erp_id, other = base_workflow
    options.update(erp=False)
    monkeypatch.setattr("app.drive_imports.authorized_client", lambda db: (drive, db.get(AttachmentIntegrationConfig, "default")))
    saved = client.post("/api/invoice-workflow/settings", json={"folders": [], "efatura_folders": ["folder123456789"], "include_subfolders": True,
                                                                "tolerance_cents": 2, "ocr_enabled": False, "interval_minutes": 0, "window_days": 90})
    assert saved.status_code == 200, saved.text
    return base_workflow


def add_csv(drive, identifier, text, name="efatura-outubro.csv", version="1"):
    raw = text.encode("utf-8")
    drive.raw[identifier] = raw
    drive.files[identifier] = {"id": identifier, "name": name, "mimeType": "text/csv", "size": len(raw), "parents": ["folder123456789"],
                               "md5Checksum": hashlib.md5(raw).hexdigest(), "modifiedTime": "2026-10-20T10:00:00Z", "version": version}


def rows(client):
    return client.get("/api/invoice-workflow?start=2026-10-01&end=2026-10-31").json()["rows"]


def row_for(client, identifier):
    return next(row for row in rows(client) if row["id"] == identifier)


def store_of(factory, identifier):
    with factory() as db:
        return db.get(Purchase, identifier).store_id


def add_procurement(factory, identifier, *, nif="510774318", document="FT ABC/1", kind="invoice", document_type="FT",
                    date="2026-10-10", total=12300, confirmed=True, linked=True):
    from app.models import User, now
    with factory.begin() as db:
        supplier = db.scalar(select(BusinessEntity).where(BusinessEntity.tax_number == nif)) or BusinessEntity(name="Fornecedor " + nif, tax_number=nif, is_supplier=True)
        db.add(supplier)
        db.flush()
        user = db.scalar(select(User).limit(1))
        payload = {"document": document, "document_type": document_type, "document_date": date, "total_cents": total,
                   "base_cents": total * 100 // 123, "tax_cents": total - total * 100 // 123, "lines": []}
        row = ProcurementDocument(kind=kind, store_id=db.get(Purchase, identifier).store_id, supplier_id=supplier.id, user_id=user.id,
                                  purchase_id=identifier if linked else None, status="confirmed" if confirmed else "draft",
                                  request_key="test:" + document + nif, confirmed_at=now() if confirmed else None, data=json.dumps(payload))
        db.add(row)
        db.flush()
        return row.id


def test_efatura_files_are_found_previewed_imported_once_and_updates_detected(conference):
    client, factory, drive, _, identifier, *_ = conference
    store = store_of(factory, identifier)
    add_csv(drive, "efat-out", CSV_HEADER + "510774318;Fornecedor Alfa;FT ABC/1;FT;2026-10-10;123,00;\n"
                                            "500000000;Fornecedor Beta;FT B/7;FT;2026-10-12;50,00;\n")
    listing = client.get("/api/invoice-workflow/efatura-files").json()
    assert listing["configured"] and [(f["name"], f["status"], f["format"]) for f in listing["files"]] == [("efatura-outubro.csv", "new", "CSV")]
    assert all(not f["name"].endswith(".pdf") for f in listing["files"])  # anexos PDF não são exportações e-Fatura

    preview = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-out", **PERIOD})
    body = preview.json()
    assert preview.status_code == 200, preview.text
    assert (body["new_count"], body["duplicate_count"], body["conflict_count"], body["imported"]) == (1, 1, 0, False)
    assert body["format"] == "CSV" and "Documento" in body["columns"] and body["period"] == ["2026-10-10", "2026-10-12"]
    assert any("adquirente" in warning for warning in body["warnings"])
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Purchase)) == 1  # a pré-visualização não grava

    done = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-out", "confirm": True, **PERIOD}).json()
    assert done["imported"] and done["new_count"] == 1
    with factory() as db:
        batch = db.scalar(select(DocumentImport).where(DocumentImport.drive_id == "efat-out"))
        assert batch.drive_version == "1" and len(batch.fingerprint) == 64
    assert client.get("/api/invoice-workflow/efatura-files").json()["files"][0]["status"] == "imported"

    again = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-out", "confirm": True}).json()
    assert again["new_count"] == 0 and any("mesmo conteúdo" in warning for warning in again["warnings"])
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Purchase)) == 2

    # O ficheiro foi alterado no Drive: nova versão, total diferente num documento existente.
    add_csv(drive, "efat-out", CSV_HEADER + "500000000;Fornecedor Beta;FT B/7;FT;2026-10-12;55,00;\n", version="2")
    assert client.get("/api/invoice-workflow/efatura-files").json()["files"][0]["status"] == "updated"
    changed = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-out"}).json()
    assert changed["conflict_count"] == 1 and changed["rows"][0]["differences"] == ["total"]
    blocked = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-out", "confirm": True})
    assert blocked.status_code == 409


PORTAL_HEADER = "Setor;Emitente;Nº Fatura / ATCUD;Tipo;Data Emissão;Total;IVA;Base Tributável;Situação\n"


def test_without_efatura_folder_the_attachment_tree_is_searched_and_files_identified_by_content(conference, monkeypatch):
    client, factory, drive, _, identifier, *_ = conference
    monkeypatch.setattr("app.invoice_conference.drive_configured", lambda db: True)
    store = store_of(factory, identifier)
    saved = client.post("/api/invoice-workflow/settings", json={"folders": [], "efatura_folders": [], "include_subfolders": True,
                                                                "tolerance_cents": 2, "ocr_enabled": False, "interval_minutes": 0, "window_days": 90})
    assert saved.status_code == 200
    portal = PORTAL_HEADER + ("Geral;500000000 - Fornecedor Beta;FT B/7 / ATCUD-1;Fatura;2026-10-12;50,00 €;9,35 €;40,65 €;Registado\n"
                              "Geral;500000000 - Fornecedor Beta;FT B/8 / ATCUD-2;Fatura;2026-10-13;20,00 €;0,00 €;20,00 €;Documento Anulado pelo Emitente\n")
    add_csv(drive, "efat-portal", portal, name="e-fatura 3 2026.csv")
    add_csv(drive, "extrato-bpi", "Data;Descrição;Montante\n2026-10-01;Transferência;10,00\n", name="bpinet_empresas.csv")
    add_csv(drive, "efat-other", "NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;NIF adquirente\n500000000;Beta;FT B/9;FT;2026-10-12;10,00;123456789\n", name="outra-empresa.csv")
    listing = client.get("/api/invoice-workflow/efatura-files").json()
    assert listing["configured"] and listing["source"] == "attachments" and listing["subfolders"] is True
    assert [(f["name"], f["status"]) for f in listing["files"]] == [("e-fatura 3 2026.csv", "new")]
    assert "1 documento(s)" in listing["files"][0]["note"] and "anulado" in listing["files"][0]["note"]
    ignored = {f["name"]: f["reason"] for f in listing["ignored"]}
    assert "colunas de uma exportação e-Fatura" in ignored["bpinet_empresas.csv"] and "outra empresa" in ignored["outra-empresa.csv"]
    assert not any(name.endswith(".pdf") for name in ignored)

    preview = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-portal", **PERIOD}).json()
    assert (preview["new_count"], preview["skipped_count"]) == (1, 1)
    skipped = next(row for row in preview["rows"] if row["status"] == "skipped")
    assert skipped["document"] == "FT B/8" and skipped["skip_reason"].startswith("Anulado")
    assert any("Documentos não importados" in warning for warning in preview["warnings"])
    done = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-portal", "confirm": True}).json()
    assert done["imported"] and done["new_count"] == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Purchase).where(Purchase.document == "FT B/8")) == 0


def test_file_uploaded_from_the_computer_is_recognised_as_imported_in_the_drive(conference):
    client, factory, drive, _, identifier, *_ = conference
    store = store_of(factory, identifier)
    text = CSV_HEADER + "500000000;Fornecedor Beta;FT B/7;FT;2026-10-12;50,00;\n"
    uploaded = client.post(f"/api/purchases/import/{store}?confirm=true", files={"file": ("e-fatura.csv", text.encode("utf-8"), "text/csv")})
    assert uploaded.status_code == 200, uploaded.text
    add_csv(drive, "efat-same", text)
    assert client.get("/api/invoice-workflow/efatura-files").json()["files"][0]["status"] == "imported"


def test_cancelled_document_already_active_is_flagged_without_changes(conference):
    client, factory, drive, _, identifier, *_ = conference
    store = store_of(factory, identifier)
    add_csv(drive, "efat-cancel", PORTAL_HEADER + "Geral;510774318 - Fornecedor Alfa;FT ABC/1 / ATCUD-9;Fatura;2026-10-10;123,00 €;0,00 €;123,00 €;Documento Anulado pelo Emitente\n")
    preview = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-cancel"}).json()
    assert preview["cancelled_existing_count"] == 1 and preview["new_count"] == 0
    assert any("já existem como ativos" in warning for warning in preview["warnings"])
    with factory() as db:
        assert db.get(Purchase, identifier).amount_cents == 12300


def test_efatura_file_of_another_company_or_outside_folders_is_refused(conference):
    client, factory, drive, _, identifier, *_ = conference
    store = store_of(factory, identifier)
    add_csv(drive, "efat-other", "NIF fornecedor;Fornecedor;Documento;Tipo;Data;Total;NIF adquirente\n500000000;Beta;FT B/9;FT;2026-10-12;10,00;123456789\n")
    refused = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-other"})
    assert refused.status_code == 409 and "adquirente" in refused.json()["detail"]
    assert "outra empresa" in client.get("/api/invoice-workflow/efatura-files").json()["ignored"][0]["reason"]
    add_csv(drive, "efat-out2", CSV_HEADER + "500000000;Beta;FT B/9;FT;2026-10-12;10,00;\n")
    drive.files["efat-out2"]["parents"] = ["outrapasta123456"]
    drive.files["outrapasta123456"] = {"id": "outrapasta123456", "name": "Outra", "mimeType": "application/vnd.google-apps.folder", "parents": []}
    outside = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-out2"})
    assert outside.status_code == 422 and "pastas autorizadas" in outside.json()["detail"]
    add_csv(drive, "efat-bad", "Coluna;Outra\n1;2\n", name="vendas.csv")
    unknown = client.post("/api/invoice-workflow/efatura-files/import", json={"store_id": store, "drive_id": "efat-bad"})
    assert unknown.status_code == 422 and "colunas" in unknown.json()["detail"]


def test_three_sources_coincide_and_document_is_complete(conference):
    client, factory, drive, _, identifier, *_ = conference
    add_procurement(factory, identifier)
    drive.add("safe")
    row = verify(conference)
    assert row["attachment_state"] == "validated" and row["purchase_state"] == "consistent" and row["purchase_source"] == "local"
    assert row["complete"] and row["result"] == "complete" and row["action"] == "none"


def test_purchase_without_attachment_requests_document(conference):
    client, factory, drive, _, identifier, *_ = conference
    add_procurement(factory, identifier)
    row = verify(conference)
    assert row["attachment_state"] == "missing" and row["purchase_state"] == "consistent"
    assert row["action"] == "request_document" and "fornecedor" in row["explanation"]


def test_attachment_without_purchase_prepares_a_draft_once_creating_the_supplier(conference):
    client, factory, drive, _, identifier, *_ = conference
    drive.add("safe")
    row = verify(conference)
    assert row["purchase_state"] == "not_found" and row["action"] == "prepare_purchase"
    proposal = client.get(f"/api/invoice-workflow/{identifier}/prepare-purchase").json()
    assert proposal["supplier"]["state"] == "not_found" and proposal["fields"]["due_date"] == ""
    assert any("vencimento" in item for item in proposal["review"])
    body = {"create_supplier": {"name": "Fornecedor Alfa", "nif": "510774318"}, "document": "FT ABC/1", "document_type": "FT",
            "document_date": "2026-10-10", "due_date": "2026-11-09", "lines": [{"description": "Serviço", "nature": "expense", "quantity": "1", "price": "100", "vat": "23"}],
            "confirmed": True}
    wrong = client.post(f"/api/invoice-workflow/{identifier}/prepare-purchase", json={**body, "lines": [{**body["lines"][0], "price": "90"}]})
    assert wrong.status_code == 422 and "difere do total" in wrong.json()["detail"]
    created = client.post(f"/api/invoice-workflow/{identifier}/prepare-purchase", json=body)
    assert created.status_code == 200, created.text
    with factory() as db:
        draft = db.get(ProcurementDocument, created.json()["id"])
        assert draft.status == "draft" and draft.confirmed_at is None and json.loads(draft.data)["purchase_id"] == identifier
        assert db.scalar(select(func.count()).select_from(BusinessEntity).where(BusinessEntity.tax_number == "510774318")) == 1
        assert db.scalar(select(func.count()).select_from(StockEntry)) == 0
    duplicate = client.post(f"/api/invoice-workflow/{identifier}/prepare-purchase", json={**body, "create_supplier": None, "supplier_id": draft.supplier_id})
    assert duplicate.status_code == 409 and "Já existe" in duplicate.json()["detail"]
    assert row_for(client, identifier)["purchase_state"] == "draft"


def test_purchase_registered_in_procurement_but_not_in_efatura_is_flagged_for_review(conference):
    client, factory, drive, _, identifier, *_ = conference
    with factory.begin() as db:
        db.get(Purchase, identifier).origin = "procurement"
    add_procurement(factory, identifier)
    drive.add("safe")
    row = verify(conference)
    assert row["efatura_state"] == "absent" and row["attachment_state"] == "validated" and row["purchase_state"] == "consistent"
    assert row["action"] == "review_efatura" and "não é, por si só, um erro" in row["explanation"]


def test_divergent_totals_are_shown_and_rounding_within_tolerance_is_not_hidden(conference):
    client, factory, drive, _, identifier, *_ = conference
    procurement_id = add_procurement(factory, identifier, total=12500)
    row = verify(conference)
    assert row["purchase_state"] == "divergent" and row["action"] == "review_divergence"
    assert any("Total" in difference for difference in row["purchase_candidates"][0]["differences"])
    with factory.begin() as db:
        doc = db.get(ProcurementDocument, procurement_id)
        doc.data = json.dumps({**json.loads(doc.data), "total_cents": 12301})
    row = row_for(client, identifier)
    assert row["purchase_state"] == "consistent" and any("tolerância" in note for note in row["purchase_notes"])


def test_same_number_from_another_supplier_is_not_matched(conference):
    client, factory, drive, _, identifier, *_ = conference
    add_procurement(factory, identifier, nif="500000000", linked=False)
    row = row_for(client, identifier)
    assert row["purchase_state"] == "not_found"
    extra = [item for item in rows(client) if item["efatura_state"] == "absent"]
    assert len(extra) == 1 and extra[0]["supplier_nif"] == "500000000" and extra[0]["action"] == "review_efatura"


def test_multiple_candidates_are_ambiguous_never_automatic_and_can_be_rejected(conference):
    client, factory, drive, _, identifier, *_ = conference
    drive.add("first"); drive.add("second")
    row = verify(conference)
    assert row["attachment_state"] == "review" and {c["strength"] for c in row["drive_candidates"]} == {"ambigua"}
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment)) == 0
    batch = client.post("/api/invoice-workflow/attach-batch", json={"items": [{"id": identifier, "drive_id": "first"}], "confirmed": True, "reason": "Lote"})
    assert batch.json()["attached"] == [] and batch.json()["skipped"]
    rejected = client.post(f"/api/invoice-workflow/{identifier}/reject", json={"drive_id": "second", "reason": "Fatura de outro mês"})
    assert rejected.status_code == 200
    row = verify(conference)
    assert [c["drive_id"] for c in row["drive_candidates"]] == ["first"] and row["rejected"] == ["second"]
    assert row["drive_candidates"][0]["strength"] == "forte" or row["attachment_state"] == "validated"


def test_strong_proposals_are_confirmed_in_batch(conference):
    client, factory, drive, _, identifier, *_ = conference
    drive.add("safe")
    with factory.begin() as db:
        db.add(InvoiceWorkflowCheck(purchase_id=identifier, drive_state="checked", drive_candidates=json.dumps([
            {"drive_id": "safe", "name": "invoice.pdf", "safe": True, "strength": "forte", "reasons": [], "fields": {}, "url": ""}])))
    batch = client.post("/api/invoice-workflow/attach-batch", json={"items": [{"id": identifier, "drive_id": "safe"}], "confirmed": True, "reason": "Pré-visualizadas"})
    assert batch.status_code == 200 and batch.json()["attached"] == [identifier]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id == identifier)) == 1


def test_credit_notes_compare_absolute_totals_and_keep_their_type(conference):
    client, factory, drive, _, identifier, *_ = conference
    with factory.begin() as db:
        purchase = db.get(Purchase, identifier)
        purchase.document_type, purchase.document, purchase.amount_cents = "NC", "NC ABC/2", -12300
    add_procurement(factory, identifier, document="NC ABC/2", kind="credit", document_type="NC")
    row = row_for(client, identifier)
    assert row["purchase_state"] == "consistent"


def test_drive_failure_keeps_progress_and_erp_failure_is_not_absence(conference, monkeypatch):
    client, factory, drive, options, identifier, *_ = conference
    drive.add("safe")
    assert verify(conference)["attachment_state"] == "validated"
    drive.fail = True
    row = verify(conference)
    assert row["attachment_state"] == "validated" and len(row["attachments"]) == 1
    drive.fail = False
    monkeypatch.setattr("app.invoice_conference.policy", lambda db: {"mode": "integrated"})
    options.update(erp=True, erp_error=True)
    row = verify(conference, parts=["purchases"])
    assert row["purchase_state"] == "unavailable" and row["action"] == "retry_erp" and "não prova" in row["explanation"]


def test_assistant_state_is_saved_per_user(conference):
    client, *_ = conference
    saved = client.post("/api/invoice-workflow/assistant-state", json={"step": "review", **PERIOD, "store_id": "", "drive_id": "efat-out"})
    assert saved.status_code == 200
    assert client.get("/api/invoice-workflow/assistant-state").json()["step"] == "review"


def test_launch_proposal_needs_supplier_rubric_and_due_date_then_launches_once(conference):
    from app.models import Category, PlannedMovement
    client, factory, drive, _, identifier, *_ = conference
    drive.add("safe")
    assert verify(conference)["attachment_state"] == "validated"
    listing = client.get("/api/invoice-workflow/launch-proposals?start=2026-10-01&end=2026-10-31").json()
    item = next(p for p in listing["proposals"] if p["id"] == identifier)
    assert not item["ready"] and any("Fornecedor" in m for m in item["missing"]) and any("Vencimento" in m for m in item["missing"])
    with factory.begin() as db:
        category = Category(name="Serviços externos"); db.add(category); db.flush(); category_id = category.id
        db.add(BusinessEntity(name="Fornecedor Alfa", tax_number="510774318", is_supplier=True, category_id=category_id, payment_days=30))
    item = next(p for p in client.get("/api/invoice-workflow/launch-proposals?start=2026-10-01&end=2026-10-31").json()["proposals"] if p["id"] == identifier)
    assert item["ready"] and item["due_date"] == "2026-11-09" and item["due_source"].startswith("prazo do fornecedor")
    assert item["lines"] == [{"description": "Fornecedor Alfa · FT FT ABC/1", "nature": "expense", "unit": "un", "quantity": "1", "price": "123.00", "vat": "0"}]
    launched = client.post("/api/invoice-workflow/launch-proposals/confirm", json={"items": [{"id": identifier}], "confirmed": True}).json()
    assert len(launched["launched"]) == 1 and not launched["skipped"]
    with factory() as db:
        doc = db.get(ProcurementDocument, launched["launched"][0]["procurement_id"])
        assert doc.status == "confirmed" and doc.purchase_id == identifier
        planned = db.scalar(select(PlannedMovement).where(PlannedMovement.purchase_id == identifier))
        assert planned.due_date == "2026-11-09" and planned.amount_cents == 12300 and planned.category_id == category_id
        assert db.scalar(select(func.count()).select_from(StockEntry)) == 0
    again = client.post("/api/invoice-workflow/launch-proposals/confirm", json={"items": [{"id": identifier}], "confirmed": True}).json()
    assert not again["launched"] and "Já existe" in again["skipped"][0]["reason"]
    assert identifier not in [p["id"] for p in client.get("/api/invoice-workflow/launch-proposals?start=2026-10-01&end=2026-10-31").json()["proposals"]]


def test_launch_creates_missing_supplier_only_when_asked_and_uses_due_date_from_attachment(conference):
    from app.models import Category, InvoiceEvidenceCache
    client, factory, drive, _, identifier, *_ = conference
    drive.add("safe")
    verify(conference)
    with factory.begin() as db:
        for cache in db.scalars(select(InvoiceEvidenceCache)):
            cache.evidence = json.dumps({**json.loads(cache.evidence), "due_dates": ["2026-11-20"]})
        category = Category(name="Rubrica X"); db.add(category); db.flush(); category_id = category.id
    body = {"items": [{"id": identifier, "category_id": category_id}], "confirmed": True}
    refused = client.post("/api/invoice-workflow/launch-proposals/confirm", json=body).json()
    assert not refused["launched"] and "Fornecedor por criar" in refused["skipped"][0]["reason"]
    done = client.post("/api/invoice-workflow/launch-proposals/confirm", json={**body, "create_missing_suppliers": True}).json()
    assert len(done["launched"]) == 1
    with factory() as db:
        assert db.scalar(select(BusinessEntity).where(BusinessEntity.tax_number == "510774318")).category_id == category_id
        payload = json.loads(db.get(ProcurementDocument, done["launched"][0]["procurement_id"]).data)
        assert payload["due_date"] == "2026-11-20" and "lido no anexo" in payload["notes"]
