import hashlib
import io
import json
from datetime import timedelta

import pytest
from app import db as database
from app.main import app
from app.models import (
    ArchiveBlob,
    ArchiveDocument,
    ArchiveEvent,
    ArchiveJob,
    ArchiveLink,
    AttachmentIntegrationConfig,
    Audit,
    BankAccount,
    BankTransaction,
    DocumentPayment,
    DocumentScan,
    DocumentState,
    DriveDocumentAttachment,
    GoogleConnection,
    InvoiceEvidenceCache,
    Purchase,
    PurchaseAttachment,
    Register,
    User,
    now,
)
from app.platform import cipher
from app.platform_models import Company
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_document_scanning import Drive
from test_invoice_qr import document as qr_pdf
from test_platform import create, enter, platform  # noqa: F401

QR = "A:500000000*B:999999990*C:PT*D:FT*E:N*F:20260914*G:FT EDP/12*H:EDP1-12*I1:PT*I7:100.37*I8:23.08*N:23.08*O:123.45*Q:ABCD*R:9999"


def text_pdf(*lines):
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    for index, line in enumerate(lines):
        pdf.drawString(40, 800 - index * 20, line)
    pdf.save()
    return output.getvalue()


@pytest.fixture
def arch(setup, monkeypatch):
    client, ids, factory = setup

    def company():
        with factory() as db:
            db.info["company"] = {"id": "arquivo-demo", "name": "Empresa Demo"}
            yield db

    original = app.dependency_overrides[database.db_session]
    app.dependency_overrides[database.db_session] = company
    drive = Drive()
    monkeypatch.setattr("app.archive.authorized_client", lambda db: (drive, db.get(AttachmentIntegrationConfig, "default")))
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        other = db.get(Register, ids["registers"][2]).store_id
        db.add(AttachmentIntegrationConfig(id="default", google_email="demo@example.pt", drive_folder="root"))
    yield client, factory, store, other, drive
    app.dependency_overrides[database.db_session] = original


def upload(client, store, *files, **form):
    return client.post("/api/archive/upload", data={"store_id": store, **form},
                       files=[("files", (name, raw, "application/octet-stream")) for name, raw in files])


def login(email):
    other = TestClient(app)
    assert other.post("/api/auth/login", json={"email": email, "password": "PasswordTest123!"}).status_code == 200
    return other


def purchase(factory, store, **values):
    with factory.begin() as db:
        row = Purchase(store_id=store, supplier_nif=values.get("nif", "500000000"), supplier_name=values.get("name", "EDP Comercial"),
                       document=values.get("document", "FT EDP/12"), document_type="FT", document_date=values.get("date", "2026-09-14"),
                       amount_cents=values.get("amount", 12345), origin=values.get("origin", "efatura"))
        db.add(row)
        db.flush()
        return row.id


def test_upload_validation_extraction_correction_and_purchase_link(arch):
    client, factory, store, _, _ = arch
    invoice = qr_pdf(QR)
    purchase_id = purchase(factory, store)
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.add_js("app.alert('x');")
    active = io.BytesIO()
    writer.write(active)
    r = upload(client, store, ("fatura.pdf", invoice), ("ativo.pdf", active.getvalue()), ("virus.pdf", b"MZ\x90\x00 executavel"))
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [x["status"] for x in results] == ["carregado", "rejeitado", "rejeitado"]
    identifier = results[0]["id"]
    again = upload(client, store, ("copia.pdf", invoice)).json()["results"][0]
    assert again["status"] == "duplicado" and again["id"] == identifier
    with factory() as db:
        blob = db.get(ArchiveBlob, identifier)
        assert invoice not in blob.ciphertext and cipher().decrypt(blob.ciphertext) == invoice  # cifrado em repouso
    assert client.post("/api/archive/process").json()["processed"] >= 2  # extração + relações
    doc = client.get(f"/api/archive/documents/{identifier}").json()
    assert doc["processing_status"] == "processado" and doc["doc_type"] == "fatura"
    assert (doc["entity_nif"], doc["document_number"], doc["issue_date"], doc["total_cents"]) == ("500000000", "FT EDP/12", "2026-09-14", 12345)
    assert (doc["base_cents"], doc["tax_cents"]) == (10037, 2308)
    assert doc["extraction"]["fields"]["total_cents"] == {"value": 12345, "confidence": 0.99, "source": "qr"}
    assert doc["entity_name"] == "EDP Comercial"  # nome conhecido pelas compras com o mesmo NIF
    proposal = next(link for link in doc["links"] if link["target_kind"] == "purchase")
    assert proposal["status"] == "proposta" and proposal["strength"] == "forte" and proposal["score"] == 100
    assert "NIF e número do documento iguais" in proposal["reasons"]
    # Correção manual: fica no histórico e nunca é substituída por um novo processamento.
    r = client.put(f"/api/archive/documents/{identifier}", json={"description": "Eletricidade setembro", "payment_reference": "MB 21312 123456789", "reviewed": True})
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "revisto" and r.json()["corrections"][0]["campo"] == "payment_reference"
    client.put(f"/api/archive/documents/{identifier}", json={"total_cents": 12000})
    client.post(f"/api/archive/documents/{identifier}/retry")
    client.post("/api/archive/process")
    doc = client.get(f"/api/archive/documents/{identifier}").json()
    assert doc["total_cents"] == 12000 and doc["extraction"]["fields"]["total_cents"]["source"] == "correcao"
    client.put(f"/api/archive/documents/{identifier}", json={"total_cents": 12345})
    link = next(link for link in client.get("/api/archive/links").json() if link["document_id"] == identifier and link["target_kind"] == "purchase")
    preview = client.post("/api/archive/links/decide", json={"ids": [link["id"]], "decision": "confirmar"}).json()
    assert preview["confirmed"] is False and preview["items"][0]["target"]["detail"] == "e-Fatura"
    with factory() as db:
        assert db.get(ArchiveLink, link["id"]).status == "proposta"  # a pré-visualização não grava
    done = client.post("/api/archive/links/decide", json={"ids": [link["id"]], "decision": "confirmar", "confirm": True}).json()
    assert done["confirmed"] and "anexada" in done["notes"][0]
    with factory() as db:
        attachment = db.scalar(select(PurchaseAttachment).where(PurchaseAttachment.purchase_id == purchase_id))
        assert attachment.data == invoice and db.get(DocumentState, purchase_id).availability == "digital"
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "arquivo_associacao_confirmada")) == 1
    assert client.post("/api/archive/links/decide", json={"ids": [link["id"]], "decision": "confirmar", "confirm": True}).status_code == 409
    file = client.get(f"/api/archive/documents/{identifier}/file")
    assert file.status_code == 200 and file.content == invoice and file.headers["content-security-policy"] == "sandbox"
    assert client.post("/api/archive/links/decide", json={"ids": [link["id"]], "decision": "anular", "confirm": True, "reason": ""}).status_code == 422
    assert client.post("/api/archive/links/decide", json={"ids": [link["id"]], "decision": "anular", "confirm": True, "reason": "Erro de associação"}).status_code == 200
    # Integridade: um original alterado é detetado e registado.
    with factory.begin() as db:
        db.get(ArchiveBlob, identifier).ciphertext = cipher().encrypt(invoice + b"%alterado")
    assert client.get(f"/api/archive/documents/{identifier}/file").status_code == 409
    with factory() as db:
        assert db.get(ArchiveDocument, identifier).integrity_ok is False
        actions = {e.action for e in db.scalars(select(ArchiveEvent).where(ArchiveEvent.document_id == identifier))}
        assert {"carregado", "processado", "corrigido", "revisto", "associacao_confirmada", "associacao_anulada", "visualizado", "integridade_falhou"} <= actions


def test_sync_indexes_existing_sources_once_and_keeps_links(arch, monkeypatch):
    client, factory, store, _, drive = arch
    purchase_id = purchase(factory, store)
    scanned = qr_pdf(QR.replace("EDP/12", "EDP/13"))
    drive.files["cloud-scan"] = {"id": "cloud-scan", "mimeType": "application/pdf", "size": str(len(scanned)), "parents": ["root"]}
    drive.contents["cloud-scan"] = scanned
    listed = text_pdf("Recibo", "NIF: 500000000", "Total: 50,00")
    drive.files["inbox-1"] = {"id": "inbox-1", "mimeType": "application/pdf", "size": str(len(listed)), "parents": ["root"]}
    drive.contents["inbox-1"] = listed
    original_get = drive.get

    def listing(url, params=None):
        if url.endswith("/files") and params and "mimeType='application/pdf'" in params.get("q", "") and "'root' in parents" in params["q"]:
            import httpx
            return httpx.Response(200, json={"files": [{"id": "inbox-1", "name": "recibo.pdf", "mimeType": "application/pdf", "size": str(len(listed))},
                                                       {"id": "cloud-scan", "name": "x.pdf", "mimeType": "application/pdf"}]})
        return original_get(url, params)
    drive.get = listing
    with factory.begin() as db:
        db.add(GoogleConnection(id="default", token_ciphertext="token"))
        db.add(PurchaseAttachment(purchase_id=purchase_id, name="fatura.pdf", mime="application/pdf", data=qr_pdf(QR)))
        db.add(DocumentScan(request_key="k1", store_id=store, user_id=db.scalar(select(User.id).where(User.email == "admin@test.pt")),
                            drive_id="cloud-scan", drive_folder="root", account="demo@example.pt", name="2026-10-20_fatura_ab.pdf", digest=hashlib.sha256(scanned).hexdigest(),
                            size=len(scanned), pages=1, document_date="2026-10-20", status="cloud_saved", origin="digitalizacao_rapida", expires_at=now(),
                            data=json.dumps({"metadata": {"store_id": store, "document_type": "DIG", "document_date": "2026-10-20", "document": "Digitalização rápida"},
                                             "quick": {"type": "fatura", "description": "Gás"}})))
        db.add(DriveDocumentAttachment(purchase_id=purchase_id, account="demo@example.pt", drive_id="cloud-scan", drive_folder="root",
                                       name="digitalizada.pdf", mime="application/pdf", digest=hashlib.sha256(scanned).hexdigest()))
        db.add(InvoiceEvidenceCache(account="demo@example.pt", drive_id="cloud-scan", folder="root", name="x.pdf", mime="application/pdf",
                                    version="1", digest=hashlib.sha256(scanned).hexdigest(), evidence="{}"))
    # Sem OCR: o texto do PDF chega e o resultado não depende do Tesseract instalado.
    client.put("/api/archive/settings", json={"retention_years": 10, "ocr_enabled": False, "auto_process": True, "include_drive": True, "auto_confirm_strong": False})
    r = client.post("/api/archive/sync", json={"drive": True, "process": True})
    assert r.status_code == 200, r.text
    assert r.json()["by_origin"] == {"digitalizacao_rapida": 1, "anexo_compra": 1, "google_drive": 1}
    assert client.post("/api/archive/sync", json={"drive": True, "process": False}).json()["created"] == 0
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ArchiveDocument)) == 3
        scan = db.scalar(select(ArchiveDocument).where(ArchiveDocument.storage_ref == "cloud-scan"))
        assert scan.origin == "digitalizacao_rapida" and scan.doc_type == "fatura" and scan.description == "Gás" and scan.store_id == store
        links = list(db.scalars(select(ArchiveLink).where(ArchiveLink.document_id == scan.id, ArchiveLink.status == "confirmada")))
        assert [(link.target_kind, link.target_id) for link in links] == [("purchase", purchase_id)]
        inbox = db.scalar(select(ArchiveDocument).where(ArchiveDocument.storage_ref == "inbox-1"))
        assert inbox.store_id is None and inbox.processing_status == "processado" and inbox.doc_type == "recibo"
        assert inbox.entity_nif == "500000000" and inbox.total_cents == 5000
        assert json.loads(inbox.extraction)["fields"]["total_cents"]["confidence"] == 0.75
        assert drive.uploads == 0  # indexar nunca envia nem copia ficheiros
    operator = login("operator@test.pt")
    ids = [d["id"] for d in operator.get("/api/archive/documents").json()["documents"]]
    assert scan.id in ids and inbox.id not in ids  # sem loja: só gestão


def test_bank_full_partial_grouped_and_payment_relations(arch):
    client, factory, store, _, _ = arch
    with factory.begin() as db:
        account = BankAccount(store_id=store, name="BPI", bank="BPI", iban_last4="1234")
        db.add(account)
        db.flush()
        docs = []
        for number, total in (("FT A/1", 10000), ("FT A/2", 5000)):
            doc = ArchiveDocument(source_key="teste:" + number, store_id=store, origin="upload", storage="base_dados", storage_ref="archive_blobs:x",
                                  doc_type="fatura", entity_nif="503504564", entity_name="EDP Comercial", document_code="FT", document_number=number,
                                  issue_date="2026-09-01", total_cents=total, processing_status="processado")
            db.add(doc)
            docs.append(doc)
        db.flush()
        tx = {}
        for key, amount, text, day in (("full", -10000, "TRF EDP SETEMBRO", "2026-09-20"), ("partial", -4000, "PAG FT A/1 PARCIAL", "2026-09-10"),
                                       ("group", -15000, "DD EDP COMERCIAL", "2026-09-25"), ("other", -10000, "SUPERMERCADO", "2026-12-30")):
            row = BankTransaction(account_id=account.id, movement_date=day, value_date=day, description=text, amount_cents=amount, balance_cents=0, fingerprint=key, source_account="PT", exported_at="2026-10-01T00:00:00", source_position=1)
            db.add(row)
            db.flush()
            tx[key] = row.id
        payment = DocumentPayment(store_id=store, payment_date="2026-09-12", amount_cents=2500, direction=-1, entity="EDP Comercial",
                                  reference="FT A/2", kind="pagamento", method="transferencia", source="manual", request_key="p1")
        db.add(payment)
        first, second = docs[0].id, docs[1].id
    for identifier in (first, second):
        r = client.post("/api/archive/documents/batch", json={"ids": [identifier], "action": "relacionar", "confirm": True})
        assert r.status_code == 200, r.text
    with factory() as db:
        links = {(link.document_id, link.target_kind, link.target_id): link for link in db.scalars(select(ArchiveLink))}
    full = links[(first, "bank_transaction", tx["full"])]
    assert full.amount_cents is None and full.score >= 70 and "Valor igual ao total (100,00 €)" in json.loads(full.reasons)
    partial = links[(first, "bank_transaction", tx["partial"])]
    assert partial.method == "pagamento_parcial" and partial.amount_cents == 4000
    for identifier, amount in ((first, 10000), (second, 5000)):
        group = links[(identifier, "bank_transaction", tx["group"])]
        assert group.method == "pagamento_agrupado" and group.amount_cents == amount
    assert (first, "bank_transaction", tx["other"]) not in links  # fora da janela de datas
    paid = [link for key, link in links.items() if key[0] == second and key[1] == "document_payment"]
    assert paid and paid[0].amount_cents == 2500 and paid[0].method == "pagamento_parcial"
    assert all(link.status == "proposta" for link in links.values())  # nada é confirmado sem validação
    pending = client.get("/api/archive/pending").json()
    assert pending["proposals"] == len(links) and pending["payments_without_proof"]


def test_permissions_scope_and_settings(arch):
    client, factory, store, other, _ = arch
    operator, reader = login("operator@test.pt"), login("reader@test.pt")
    assert upload(reader, store, ("a.pdf", text_pdf("Fatura"))).status_code == 403
    assert upload(operator, other, ("a.pdf", text_pdf("Fatura"))).status_code == 403  # loja sem acesso
    mine = upload(operator, store, ("a.pdf", text_pdf("Fatura", "Total: 10,00"))).json()["results"][0]["id"]
    foreign = upload(client, other, ("b.pdf", text_pdf("Outra loja"))).json()["results"][0]["id"]
    assert operator.get(f"/api/archive/documents/{mine}").status_code == 200
    assert operator.get(f"/api/archive/documents/{foreign}").status_code == 404
    assert reader.get(f"/api/archive/documents/{foreign}/file").status_code == 404
    assert operator.put(f"/api/archive/documents/{mine}", json={"reviewed": True}).status_code == 403
    assert operator.post("/api/archive/links/decide", json={"ids": ["x"], "decision": "confirmar"}).status_code == 403
    assert operator.post("/api/archive/sync", json={}).status_code == 403
    assert operator.get("/api/archive/audit").status_code == 403
    assert client.put("/api/archive/settings", json={"retention_years": 5, "ocr_enabled": False, "auto_process": True, "include_drive": True, "auto_confirm_strong": False}).status_code == 422
    r = client.put("/api/archive/settings", json={"retention_years": 12, "ocr_enabled": False, "auto_process": True, "include_drive": False, "auto_confirm_strong": False})
    assert r.status_code == 200 and r.json()["retention_years"] == 12
    with factory() as db:
        assert db.get(ArchiveDocument, mine).retention_until.endswith("-12-31") and int(db.get(ArchiveDocument, mine).retention_until[:4]) >= now().year + 12
    assert client.put(f"/api/archive/documents/{mine}", json={"legal_hold": True}).status_code == 200
    gestor_like = login("operator@test.pt")
    assert gestor_like.put("/api/archive/settings", json={"retention_years": 12, "ocr_enabled": False, "auto_process": True, "include_drive": False, "auto_confirm_strong": False}).status_code == 403


def test_cloud_failure_retries_with_backoff_and_recovers(arch, monkeypatch):
    client, factory, store, _, drive = arch
    purchase_id = purchase(factory, store)
    raw = qr_pdf(QR)
    drive.files["d1"] = {"id": "d1", "mimeType": "application/pdf", "size": str(len(raw)), "parents": ["root"]}
    drive.contents["d1"] = raw
    with factory.begin() as db:
        db.add(DriveDocumentAttachment(purchase_id=purchase_id, account="demo@example.pt", drive_id="d1", drive_folder="root", name="f.pdf",
                                       mime="application/pdf", digest=hashlib.sha256(raw).hexdigest()))

    def offline(db):
        raise HTTPException(409, "Volte a ligar a conta Google.")
    monkeypatch.setattr("app.archive.authorized_client", offline)
    client.post("/api/archive/sync", json={"drive": False, "process": True})
    with factory() as db:
        job = db.scalar(select(ArchiveJob).where(ArchiveJob.kind == "extract"))
        doc = db.get(ArchiveDocument, job.document_id)
        assert job.status == "pendente" and job.attempts == 1 and job.next_attempt_at > now() and "Volte a ligar" in job.error
        assert doc.processing_status == "pendente"
    monkeypatch.setattr("app.archive.authorized_client", lambda db: (drive, db.get(AttachmentIntegrationConfig, "default")))
    assert client.post("/api/archive/process").json()["processed"] == 0  # respeita o intervalo entre tentativas
    with factory.begin() as db:
        job = db.scalar(select(ArchiveJob).where(ArchiveJob.kind == "extract"))
        job.status, job.updated_at = "em_curso", now() - timedelta(minutes=30)  # processo interrompido
    assert client.post("/api/archive/process").json()["processed"] >= 1
    with factory() as db:
        assert db.get(ArchiveDocument, doc.id).processing_status == "processado"
    # Falha permanente (ficheiro removido do Drive): erro sem novas tentativas, original intacto.
    drive.files["d2"] = {"id": "d2", "mimeType": "application/pdf", "size": "10", "parents": ["root"], "trashed": True}
    with factory.begin() as db:
        gone = ArchiveDocument(source_key="drive:d2", origin="google_drive", storage="drive", storage_ref="d2", storage_account="demo@example.pt")
        db.add(gone)
        db.flush()
        db.add(ArchiveJob(document_id=gone.id, kind="extract"))
        gone_id = gone.id
    client.post("/api/archive/process")
    with factory() as db:
        job = db.scalar(select(ArchiveJob).where(ArchiveJob.document_id == gone_id))
        assert job.status == "erro" and job.attempts == 1 and db.get(ArchiveDocument, gone_id).processing_status == "erro"
    detail = client.get(f"/api/archive/documents/{gone_id}").json()
    assert detail["jobs"][0]["error"] and any(e["action"] == "erro_processamento" for e in detail["events"])


def test_assistant_reads_with_sources_and_writes_only_with_confirmation(arch):
    client, factory, store, _, _ = arch
    purchase(factory, store, document="FT EDP/99", date="2026-09-20", amount=4567)
    with factory.begin() as db:
        account = BankAccount(store_id=store, name="BPI", bank="BPI", iban_last4="1234")
        db.add(account)
        db.flush()
        db.add(BankTransaction(account_id=account.id, movement_date=(now().date() - timedelta(days=3)).isoformat(), value_date=(now().date() - timedelta(days=3)).isoformat(), description="PAGAMENTO SERVICOS", balance_cents=0,
                               amount_cents=-9900, fingerprint="f1", source_account="PT", exported_at="2026-10-01T00:00:00", source_position=1))
        for number, day, nif, name in (("FT EDP/12", "2026-09-14", "500000000", "EDP Comercial"), ("FT GAL/1", "2026-09-15", "500697370", "Galp")):
            db.add(ArchiveDocument(source_key="t:" + number, store_id=store, origin="upload", storage="base_dados", storage_ref="archive_blobs:x",
                                   doc_type="fatura", entity_nif=nif, entity_name=name, document_number=number, issue_date=day, total_cents=12345,
                                   processing_status="processado"))
    r = client.post("/api/archive/assistant", json={"question": "Encontra as faturas da EDP de setembro de 2026."}).json()
    assert r["mode"] == "leitura" and r["intent"] == "pesquisa" and len(r["documents"]) == 1
    assert r["documents"][0]["entity_name"] == "EDP Comercial" and r["interpretation"]["entidade"] == "edp" and r["sources"]
    r = client.post("/api/archive/assistant", json={"question": "Mostra pagamentos sem comprovativo"}).json()
    assert r["intent"] == "pagamentos_sem_comprovativo" and r["items"][0]["amount_cents"] == -9900
    r = client.post("/api/archive/assistant", json={"question": "Quais são os documentos em falta para a contabilidade de setembro de 2026?"}).json()
    assert r["intent"] == "em_falta" and [i["label"] for i in r["items"]] == ["FT EDP/99"]
    r = client.post("/api/archive/assistant", json={"question": "Prepara o dossiê contabilístico de setembro de 2026"}).json()
    assert r["intent"] == "dossie" and r["navigate"] == "Entrega à contabilidade" and r["mode"] == "leitura"
    reader = login("reader@test.pt")
    assert reader.post("/api/archive/assistant", json={"question": "Relaciona os anexos disponíveis com as compras do ERP"}).status_code == 403
    plan = client.post("/api/archive/assistant", json={"question": "Relaciona os anexos disponíveis com as compras do ERP"}).json()
    assert plan["mode"] == "escrita" and plan["plan"]["count"] == 2
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ArchiveLink)) == 0  # a pergunta não escreve
    assert login("operator@test.pt").post("/api/archive/assistant/confirm", json={"token": plan["plan"]["token"]}).status_code == 403
    done = client.post("/api/archive/assistant/confirm", json={"token": plan["plan"]["token"]}).json()
    assert done["documents"] == 2 and "nada foi confirmado" in done["answer"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ArchiveLink).where(ArchiveLink.status == "confirmada")) == 0


def test_company_isolation(platform):  # noqa: F811
    client, core = platform
    a, b = create(client, "arquivo-empresa-a"), create(client, "arquivo-empresa-b")
    client.cookies.clear()
    boot = enter(client, a["slug"])
    raw = text_pdf("Fatura", "Total: 10,00")
    r = upload(client, boot["stores"][0]["id"], ("a.pdf", raw))
    identifier = r.json()["results"][0]["id"]
    assert client.get(f"/api/archive/documents/{identifier}").status_code == 200
    boot_b = enter(client, b["slug"])
    assert client.get(f"/api/archive/documents/{identifier}").status_code == 404
    assert client.get(f"/api/archive/documents/{identifier}/file").status_code == 404
    assert client.get("/api/archive/documents").json()["total"] == 0
    assert upload(client, boot_b["stores"][0]["id"], ("a.pdf", raw)).json()["results"][0]["status"] == "carregado"
    with core() as db:
        schema = db.get(Company, a["id"]).schema_name
    factory = sessionmaker(database.engine.execution_options(schema_translate_map={None: schema}), expire_on_commit=False)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ArchiveDocument)) == 1


def test_gmail_attachments_with_text_dates_are_indexed(arch):
    client, factory, _, _, _ = arch
    from app.models import GoogleInvoiceFile
    raw = text_pdf("Fatura", "NIF: 500000000", "Total: 12,30")
    with factory.begin() as db:
        db.add(GoogleInvoiceFile(account="demo@example.pt", message_id="m1", part_id="1", attachment_key="m1:1", name="fatura.pdf",
                                 sender="fornecedor@example.pt", subject="Fatura", received_at="2026-10-11T22:30:56+00:00", size=len(raw),
                                 mime="application/pdf", digest=hashlib.sha256(raw).hexdigest(), data=raw))
    r = client.post("/api/archive/sync", json={"drive": False, "process": False})
    assert r.status_code == 200, r.text
    assert r.json()["by_origin"] == {"gmail": 1}
    with factory() as db:
        doc = db.scalar(select(ArchiveDocument).where(ArchiveDocument.origin == "gmail"))
        from datetime import datetime
        assert doc.received_at == datetime.fromisoformat("2026-10-11T22:30:56+00:00") and doc.retention_until == "2036-12-31"
