import base64
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from app import db as database
from app.google_invoices import PREFIX, score, validate_bytes
from app.models import (
    AttachmentIntegrationConfig,
    DocumentState,
    DriveDocumentAttachment,
    GoogleInvoiceFile,
    Purchase,
    PurchaseAttachment,
)
from app.platform_models import Company
from fastapi import HTTPException
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_platform import create, enter, platform  # noqa: F401


class GoogleAPI:
    def __init__(self, raw):
        self.raw = raw
        self.saved = {}
        self.uploads = []
        self.generated = 0
        self.uncertain_response = False
        self.bad_folder = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def close(self):
        pass

    @contextmanager
    def stream(self, method, url, params=None):
        assert method == "GET" and params == {"alt": "media", "supportsAllDrives": True}
        yield httpx.Response(200, content=self.raw)

    def get(self, url, params=None):
        params = params or {}
        if url.endswith("/files"):
            return httpx.Response(200, json={"files": list(self.saved.values())})
        if url.endswith("/labels"):
            return httpx.Response(200, json={"labels": [{"id": "INBOX", "name": "INBOX"}]})
        if url.endswith("/messages"):
            assert "has:attachment" in params["q"]
            assert params["labelIds"] == "INBOX"
            messages = [{"id": "m2"}] if params.get("pageToken") else [{"id": "m1"}]
            return httpx.Response(
                200,
                json={
                    "messages": messages,
                    "nextPageToken": "next" if not params.get("pageToken") else "",
                },
            )
        if "/messages/" in url:
            return httpx.Response(
                200,
                json={
                    "internalDate": "1791633600000",
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Fornecedor Alfa FT ABC/1"},
                            {"name": "From", "value": "supplier@example.pt"},
                        ],
                        "parts": [
                            {
                                "partId": "1",
                                "filename": "FT-ABC-1.pdf",
                                "mimeType": "application/pdf",
                                "body": {
                                    "size": len(self.raw),
                                    "data": base64.urlsafe_b64encode(self.raw).decode(),
                                },
                            },
                            {
                                "partId": "2",
                                "filename": "signature.png",
                                "headers": [{"name": "Content-Disposition", "value": "inline"}],
                                "body": {"size": 50},
                            },
                            {"partId": "3", "filename": "script.exe", "body": {"size": 100}},
                        ],
                    },
                },
            )
        if url.endswith("/files/folder123456789"):
            return httpx.Response(
                200,
                json={
                    "mimeType": "application/vnd.google-apps.folder",
                    "capabilities": {"canAddChildren": not self.bad_folder},
                },
            )
        if url.endswith("/files/generateIds"):
            self.generated += 1
            return httpx.Response(200, json={"ids": ["drive-id-" + str(self.generated)]})
        file_id = url.rsplit("/", 1)[-1]
        return (
            httpx.Response(200, json=self.saved[file_id])
            if file_id in self.saved
            else httpx.Response(404, json={})
        )

    def post(self, url, params, content, headers):
        assert url == "https://www.googleapis.com/upload/drive/v3/files"
        metadata = json.loads(content.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
        assert metadata["parents"] == ["folder123456789"]
        self.saved[metadata["id"]] = metadata
        self.uploads.append(metadata["id"])
        if self.uncertain_response:
            self.uncertain_response = False
            raise httpx.ReadTimeout("uncertain response")
        return httpx.Response(200, json={"id": metadata["id"]})


@pytest.fixture
def google_case(platform, monkeypatch):  # noqa: F811
    client, core = platform
    company = create(client, "google-invoices")
    other = create(client, "google-other")
    boot = enter(client, company["slug"])
    with core() as db:
        schema = db.get(Company, company["id"]).schema_name
    factory = sessionmaker(
        database.engine.execution_options(schema_translate_map={None: schema}),
        expire_on_commit=False,
    )
    with factory.begin() as db:
        config = AttachmentIntegrationConfig(
            id="default",
            google_email="mail@gmail.com",
            drive_folder="folder123456789",
            mail_folder="inbox",
            outlook_email="",
        )
        purchase = Purchase(
            store_id=boot["stores"][0]["id"],
            supplier_name="Fornecedor Alfa",
            supplier_nif="510774318",
            document="FT ABC/1",
            document_date="2026-10-10",
            amount_cents=12300,
        )
        db.add_all([config, purchase])
        db.flush()
        doc_id = purchase.id
        db.add(
            DocumentState(
                purchase_id=doc_id, availability="nao_localizado", accounting="por_preparar"
            )
        )
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(30, 800, "Fornecedor Alfa NIF 510774318 FT ABC/1 Total 123,00 2026-10-10")
    pdf.save()
    google = GoogleAPI(output.getvalue())
    monkeypatch.setattr(
        "app.google_invoices.authorized_client",
        lambda db: (google, db.get(AttachmentIntegrationConfig, "default")),
    )
    monkeypatch.setattr(
        "app.drive_documents.authorized_client",
        lambda db: (google, db.get(AttachmentIntegrationConfig, "default")),
    )
    return client, factory, google, company, other, doc_id


def scan(client, page=""):
    r = client.post(
        PREFIX + "/scan",
        json={"start_date": "2026-10-01", "end_date": "2026-10-31", "page_token": page},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_scan_selection_upload_suggestions_and_confirmed_evidence(google_case):
    c, factory, google, company, other, doc_id = google_case
    result = scan(c)
    assert result["skipped"] == 1 and result["next_page"] == "next"
    first = result["files"][0]["id"]
    assert google.uploads == []
    assert scan(c)["files"][0]["id"] == first
    assert len(c.get(PREFIX + "/files").json()) == 1
    r = c.post(PREFIX + "/import", json={"ids": [first]})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["status"] == "importado"
    assert len(google.uploads) == 1
    assert (
        c.post(PREFIX + "/import", json={"ids": [first]}).json()["results"][0]["status"]
        == "já_importado"
    )
    assert len(google.uploads) == 1
    suggestions = c.get(PREFIX + "/files/" + first + "/suggestions").json()
    assert suggestions["suggestions"][0]["id"] == doc_id
    assert suggestions["suggestions"][0]["confidence"] >= 80
    association = PREFIX + "/files/" + first + "/associate"
    assert c.post(association, json={"purchase_id": doc_id, "confirmed": False}).status_code == 422
    r = c.post(association, json={"purchase_id": doc_id, "confirmed": True})
    assert r.status_code == 200, r.text
    assert c.post(association, json={"purchase_id": doc_id, "confirmed": True}).json() == r.json()
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseAttachment)) == 0
        assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment)) == 1
        assert db.scalar(select(GoogleInvoiceFile.data)) is None
        state = db.get(DocumentState, doc_id)
        assert state.availability == "nao_localizado" and state.accounting == "por_preparar"
        assert db.scalar(select(func.count()).select_from(Purchase)) == 1
    enter(c, other["slug"])
    assert c.get(PREFIX + "/files").json() == []
    assert c.get(PREFIX + "/files/" + first + "/suggestions").status_code == 404


def test_same_content_forwarded_is_copied_once_and_uncertain_retry_safe(google_case):
    c, _, google, *_ = google_case
    first = scan(c)["files"][0]["id"]
    second = scan(c, "next")["files"][0]["id"]
    google.uncertain_response = True
    assert (
        c.post(PREFIX + "/import", json={"ids": [first]}).json()["results"][0]["status"] == "falhou"
    )
    assert (
        c.post(PREFIX + "/import", json={"ids": [first]}).json()["results"][0]["status"]
        == "importado"
    )
    assert len(google.uploads) == 1 and google.generated == 1
    assert (
        c.post(PREFIX + "/import", json={"ids": [second]}).json()["results"][0]["status"]
        == "conteúdo_duplicado"
    )
    assert len(google.uploads) == 1


def test_invalid_folder_and_missing_selection_never_upload(google_case):
    c, _, google, *_ = google_case
    first = scan(c)["files"][0]["id"]
    assert c.post(PREFIX + "/import", json={"ids": []}).status_code == 422
    google.bad_folder = True
    assert c.post(PREFIX + "/import", json={"ids": [first]}).status_code == 422
    assert not google.uploads
    assert (
        c.post(
            PREFIX + "/scan", json={"start_date": "2026-01-01", "end_date": "2026-10-10"}
        ).status_code
        == 422
    )


def test_score_uses_reference_nif_amount_and_does_not_match_other_totals():
    item = SimpleNamespace(
        name="FT ABC1.pdf", subject="", extracted_text="Fornecedor NIF 510774318 Total 1123,00"
    )
    doc = SimpleNamespace(
        document="ABC1",
        supplier_nif="999999999",
        supplier_name="Other",
        amount_cents=12300,
        document_date="2026-01-01",
    )
    assert score(item, doc) == (0, [])
    doc.document = "FT ABC/1"
    item.name = "FT-ABC-12.pdf"
    assert score(item, doc) == (0, [])
    with pytest.raises(HTTPException):
        validate_bytes(b"<html>not a PDF</html>")


def test_forwarded_copy_cannot_be_associated_to_two_different_invoices(google_case):
    c, factory, _, _, _, doc_id = google_case
    first = scan(c)["files"][0]["id"]
    second = scan(c, "next")["files"][0]["id"]
    assert c.post(PREFIX + "/import", json={"ids": [first, second]}).status_code == 200
    association = PREFIX + "/files/" + first + "/associate"
    attachment = c.post(association, json={"purchase_id": doc_id, "confirmed": True}).json()[
        "attachment_id"
    ]
    with factory.begin() as db:
        original = db.get(Purchase, doc_id)
        other = Purchase(
            store_id=original.store_id,
            supplier_name=original.supplier_name,
            supplier_nif=original.supplier_nif,
            document="FT ABC/2",
            document_date=original.document_date,
            amount_cents=12300,
        )
        db.add(other)
        db.flush()
        other_id = other.id
    forwarded = PREFIX + "/files/" + second + "/associate"
    assert c.post(forwarded, json={"purchase_id": other_id, "confirmed": True}).status_code == 409
    assert (
        c.post(
            "/api/drive-attachments/" + attachment.removeprefix("drive:") + "/remove", json={}
        ).status_code
        == 200
    )
    assert c.post(forwarded, json={"purchase_id": other_id, "confirmed": True}).status_code == 200


def test_scan_uses_real_httpx_client_without_reentering_open_client(google_case, monkeypatch):
    c, _, google, *_ = google_case
    from app.models import AttachmentIntegrationConfig

    def respond(request):
        return google.get(str(request.url).split("?", 1)[0], dict(request.url.params))

    actual_client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(
        "app.google_invoices.authorized_client",
        lambda db: (actual_client, db.get(AttachmentIntegrationConfig, "default")),
    )
    result = scan(c)
    assert len(result["files"]) == 1
    assert actual_client.is_closed
