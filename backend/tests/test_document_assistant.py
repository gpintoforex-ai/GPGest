import io
import zipfile

from app.models import (
    BusinessEntity,
    DriveDocumentAttachment,
    GoogleInvoiceFile,
    Purchase,
    PurchaseAttachment,
)
from sqlalchemy import func, select
from test_google_invoices import google_case, scan  # noqa: F401
from test_platform import platform  # noqa: F401


def uploaded(c):
    key = scan(c)["files"][0]["id"]
    r = c.post("/api/integrations/attachments/google/import", json={"ids": [key]})
    assert r.status_code == 200, r.text
    return (
        c.get("/api/integrations/attachments/google/files")
        .json()[0]["drive_url"]
        .split("/d/")[1]
        .split("/")[0]
    )


def test_drive_assistant_suggests_and_stores_only_reference(google_case):  # noqa: F811
    c, factory, google, _, other, doc_id = google_case
    drive_id = uploaded(c)
    prefix = f"/api/documents/{doc_id}/assistant"
    r = c.get(prefix + "/drive")
    assert r.status_code == 200, r.text
    assert r.json()["files"][0]["id"] == drive_id
    analysis = c.post(prefix + "/drive-analyze", json={"drive_id": drive_id, "confirmed": False})
    assert analysis.status_code == 200 and analysis.json()["confidence"] >= 80
    assert (
        c.post(
            prefix + "/drive-attach", json={"drive_id": drive_id, "confirmed": False}
        ).status_code
        == 422
    )
    r = c.post(prefix + "/drive-attach", json={"drive_id": drive_id, "confirmed": True})
    assert r.status_code == 200, r.text
    link_id = r.json()["attachment_id"].removeprefix("drive:")
    assert (
        c.post(prefix + "/drive-attach", json={"drive_id": drive_id, "confirmed": True}).json()
        == r.json()
    )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment)) == 1
        assert db.scalar(select(func.count()).select_from(PurchaseAttachment)) == 0
        assert db.scalar(select(GoogleInvoiceFile.data)) is None
        assert db.scalar(select(GoogleInvoiceFile.extracted_text)) == ""
    detail = c.get(f"/api/documents/{doc_id}/detail").json()
    assert detail["attachment_status"] == "com_anexo"
    assert detail["availability"] == "nao_localizado" and detail["accounting"] == "por_preparar"
    assert c.get(f"/api/drive-attachments/{link_id}/view").content == google.raw
    enter = __import__("test_platform").enter
    enter(c, other["slug"])
    assert c.get(prefix + "/drive").status_code == 404
    assert c.get(f"/api/drive-attachments/{link_id}/view").status_code == 404


def test_supplier_proposed_creation_requires_confirmation_and_deduplicates(google_case):  # noqa: F811
    c, factory, _, _, _, doc_id = google_case
    endpoint = f"/api/documents/{doc_id}/assistant/supplier"
    data = {"name": "Fornecedor Alfa", "nif": "510774318", "confirmed": False}
    assert c.post(endpoint, json=data).status_code == 422
    data["confirmed"] = True
    assert c.post(endpoint, json={**data, "nif": "other"}).status_code == 422
    response = c.post(endpoint, json=data)
    assert response.status_code == 200, response.text
    assert response.json()["created"]
    assert not c.post(endpoint, json=data).json()["created"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BusinessEntity)) == 1


def test_export_reads_drive_transiently_and_unlink_keeps_remote_file(google_case):  # noqa: F811
    c, factory, google, _, _, doc_id = google_case
    drive_id = uploaded(c)
    r = c.post(
        f"/api/documents/{doc_id}/assistant/drive-attach",
        json={"drive_id": drive_id, "confirmed": True},
    )
    link_id = r.json()["attachment_id"].removeprefix("drive:")
    # Export is the existing documentary delivery endpoint, no remote deletion.
    response = c.post("/api/documents/export-package", json={"ids": [doc_id], "change": {}})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert any(archive.read(n) == google.raw for n in archive.namelist() if n.endswith(".pdf"))
    assert c.post(f"/api/drive-attachments/{link_id}/remove", json={}).status_code == 200
    assert drive_id in google.saved
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment)) == 0
        assert db.scalar(select(GoogleInvoiceFile.data)) is None


def test_drive_matches_document_automatically_without_manual_selection(google_case):  # noqa: F811
    c, factory, _, _, _, doc_id = google_case
    uploaded(c)
    plan = c.post("/api/documents/assistant/drive-match", json={"ids": [doc_id]})
    assert plan.status_code == 200, plan.text
    match = plan.json()["results"][0]
    assert match["status"] == "sugerido" and match["match"]["purchase_id"] == doc_id
    pair = {"purchase_id": doc_id, "drive_id": match["drive_id"]}
    assert c.post("/api/documents/assistant/drive-match-confirm", json={"pairs": [pair], "confirmed": False}).status_code == 422
    response = c.post("/api/documents/assistant/drive-match-confirm", json={"pairs": [pair], "confirmed": True})
    assert response.status_code == 200 and response.json()["results"][0]["status"] == "associado"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment)) == 1
        assert db.scalar(select(GoogleInvoiceFile.data)) is None


def test_drive_can_be_consulted_without_documents(google_case):  # noqa: F811
    c, *_ = google_case
    uploaded(c)
    response = c.post("/api/documents/assistant/drive-match", json={"ids": []})
    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["status"] == "por_rever"
    assert row["match"] is None
    assert row["candidates"] == []


def test_ambiguous_drive_matches_are_left_for_review(google_case):  # noqa: F811
    c, factory, _, _, _, doc_id = google_case
    uploaded(c)
    with factory.begin() as db:
        original = db.get(Purchase, doc_id)
        other = Purchase(store_id=original.store_id, supplier_name=original.supplier_name,
                         supplier_nif=original.supplier_nif, document="FT ABC-1",
                         document_date=original.document_date, amount_cents=original.amount_cents)
        db.add(other); db.flush(); other_id = other.id
    # Reference punctuation variants can make two records equally plausible.
    result = c.post("/api/documents/assistant/drive-match", json={"ids": [doc_id, other_id]})
    assert result.status_code == 200
    assert result.json()["results"][0]["status"] == "por_rever"
