import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select,func
from app.models import (AttachmentIntegrationConfig,Purchase,ERPDocument,DocumentERPLink,DriveDocumentAttachment,
    InvoiceEvidenceValidation,InvoiceWorkflowCheck,InvoiceWorkflowSettings,InvoiceEvidenceCache,User)
from app.invoice_evidence import invoice_key,extract,compare_evidence,text_fields
from app.invoice_workflow import live_erp as actual_live_erp
from test_google_invoices import google_case
from test_platform import platform,enter
from test_invoice_qr import document

class Drive:
    def __init__(self): self.files={};self.raw={};self.reads=0;self.fail=False
    def close(self): pass
    def add(self,identifier,amount="123.00",number="FT ABC/1"):
        raw=document(f"A:510774318*B:999999990*C:PT*D:FT*E:N*F:20261010*G:{number}*O:{amount}")
        self.raw[identifier]=raw
        self.files[identifier]={"id":identifier,"name":"invoice.pdf","mimeType":"application/pdf","size":len(raw),"parents":["folder123456789"],"md5Checksum":hashlib.md5(raw).hexdigest(),"modifiedTime":"2026-10-14T10:00:00Z"}
    def get(self,url,params=None):
        if self.fail: raise httpx.ConnectError("Fictitious Drive interruption")
        if url.endswith("/files"):
            return httpx.Response(200,json={"files":[] if "application/vnd.google-apps.folder" in params["q"] else list(self.files.values())})
        return httpx.Response(200,json=self.files[url.rsplit("/",1)[-1]])
    @contextmanager
    def stream(self,method,url,params=None):
        self.reads+=1; yield httpx.Response(200,content=self.raw[url.rsplit("/",1)[-1]])

@pytest.fixture
def workflow(google_case,monkeypatch):
    client,factory,_,company,other,identifier=google_case
    drive=Drive(); options={"erp":True,"supplier":True,"erp_error":False}
    with factory.begin() as db:
        purchase=db.get(Purchase,identifier);purchase.origin="efatura"
        erp=ERPDocument(store_id=purchase.store_id,enterprise_id="demo-enterprise",external_id="demo-erp",supplier_nif=purchase.supplier_nif,supplier_name=purchase.supplier_name,document="ABC / 1",internal_document="COMPRA DEMO/1",document_type="FT",document_date=purchase.document_date,amount_cents=purchase.amount_cents)
        db.add(erp);db.flush();erp_id=erp.id
    def live(db,*args):
        return {"state":"error" if options["erp_error"] else "checked","message":"Erro ERP fictício" if options["erp_error"] else "","documents":[erp_id] if options["erp"] and not options["erp_error"] else [],"supplier_state":"checked","suppliers":[{"external_id":"demo-supplier","nif":"510774318","name":"Fornecedor Alfa","is_supplier":True}] if options["supplier"] else []}
    monkeypatch.setattr("app.invoice_workflow.live_erp",live)
    monkeypatch.setattr("app.invoice_workflow.authorized_client",lambda db:(drive,db.get(AttachmentIntegrationConfig,"default")))
    monkeypatch.setattr("app.drive_documents.authorized_client",lambda db:(drive,db.get(AttachmentIntegrationConfig,"default")))
    return client,factory,drive,options,identifier,erp_id,other

def verify(fixture,**changes):
    response=fixture[0].post("/api/invoice-workflow/verify",json={"start":"2026-10-01","end":"2026-10-31","confirm_demo":True,**changes})
    assert response.status_code==200,response.text
    return fixture[0].get("/api/invoice-workflow?start=2026-10-01&end=2026-10-31").json()["rows"][0]

def test_complete_unique_safe_associations_and_incremental_retry(workflow):
    client,factory,drive,_,identifier,*_=workflow;drive.add("safe")
    row=verify(workflow)
    assert row["complete"] and row["erp_state"]=="linked" and row["attachment_state"]=="validated"
    assert verify(workflow)["complete"] and drive.reads==1
    drive.files["safe"]["name"]="renamed-invoice.pdf"
    assert verify(workflow)["attachments"][0]["name"]=="renamed-invoice.pdf" and drive.reads==1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DocumentERPLink))==1
        assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment))==1
        assert db.scalar(select(func.count()).select_from(InvoiceEvidenceValidation))==1
    assert client.post(f"/api/invoice-workflow/{identifier}/execute").status_code==409

@pytest.mark.parametrize("erp,attachment,expected",[(True,False,("linked","missing")),(False,False,("not_found","missing")),(False,True,("not_found","validated"))])
def test_independent_absence_states(workflow,erp,attachment,expected):
    _,_,drive,options,*_=workflow;options["erp"]=erp
    if attachment: drive.add("safe")
    row=verify(workflow)
    assert not row["complete"] and (row["erp_state"],row["attachment_state"])==expected

@pytest.mark.parametrize("supplier",[True,False])
def test_review_supplier_and_invoice_together_without_fake_creation(workflow,supplier):
    client,_,drive,options,identifier,*_=workflow;options.update(erp=False,supplier=supplier);drive.add("safe")
    assert verify(workflow)["attachment_state"]=="validated"
    proposal=client.get(f"/api/invoice-workflow/{identifier}/proposal").json()
    assert proposal["supplier_action"]==("reuse" if supplier else "propose_creation")
    assert not proposal["creation_available"] and proposal["fields"]["address"]=="" and proposal["fields"]["lines"]==[]
    payload={**proposal["fields"],"confirmed":True}
    response=client.post(f"/api/invoice-workflow/{identifier}/proposal",json=payload)
    assert response.status_code==200,response.text
    assert client.get(f"/api/invoice-workflow/{identifier}/proposal-export").status_code==200
    options["erp"]=True
    assert client.post(f"/api/invoice-workflow/{identifier}/proposal",json=payload).status_code==409

def test_no_valid_attachment_no_ready_proposal(workflow):
    client,_,_,options,identifier,*_=workflow;options["erp"]=False;verify(workflow)
    assert client.get(f"/api/invoice-workflow/{identifier}/proposal").status_code==409

def test_ambiguous_files_and_values_do_not_auto_associate(workflow):
    _,factory,drive,options,identifier,erp_id,_=workflow;drive.add("a");drive.add("b")
    with factory.begin() as db: db.get(ERPDocument,erp_id).amount_cents+=100
    row=verify(workflow)
    assert row["erp_state"]=="divergence" and row["attachment_state"]=="review" and not row["attachments"]
    assert len(row["drive_candidates"])==2 and not row["complete"]
    drive.files.pop("b");drive.add("a",amount="150.00")
    row=verify(workflow)
    assert row["attachment_state"]=="review" and not row["attachments"]

def test_connector_failure_is_not_document_absence(workflow):
    _,_,drive,options,*_=workflow;options["erp_error"]=True;drive.add("safe")
    row=verify(workflow)
    assert row["erp_state"]=="error" and row["attachment_state"]=="validated" and not row["complete"]
    options.update(erp_error=False,erp=False);drive.fail=True
    row=verify(workflow)
    assert row["drive_state"]=="error" and not row["complete"]

def test_changed_file_invalidates_validation_and_preserves_reference(workflow):
    client,factory,drive,_,identifier,*_=workflow;drive.add("safe");assert verify(workflow)["complete"]
    drive.add("safe",amount="500.00")
    row=verify(workflow)
    assert not row["complete"] and row["attachment_state"]=="review" and len(row["attachments"])==1
    with factory() as db: assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment))==1
    response=client.post(f"/api/invoice-workflow/{identifier}/validate",json={"attachment_key":row["attachments"][0]["key"],"confirmed":True,"reason":"Ficheiro alterado aberto e revisto explicitamente"})
    assert response.status_code==200,response.text
    assert response.json()["attachment_state"]=="validated"

def test_preserve_manual_erp_link_and_company_isolation(workflow):
    client,factory,drive,_,identifier,erp_id,other=workflow;drive.add("safe")
    with factory.begin() as db:
        db.add(DocumentERPLink(purchase_id=identifier,erp_id=erp_id,reason="Revisão manual"));db.get(ERPDocument,erp_id).amount_cents+=100
    row=verify(workflow)
    assert row["erp_state"]=="divergence"
    with factory() as db: assert db.scalar(select(DocumentERPLink.reason))=="Revisão manual"
    enter(client,other["slug"])
    assert client.get(f"/api/invoice-workflow/{identifier}/history").status_code==404
    assert client.post(f"/api/invoice-workflow/{identifier}/execute").status_code==404

def test_lease_blocks_overlapping_runs_and_settings(workflow):
    client,factory,*_=workflow
    from datetime import timedelta
    from app.models import now
    verify(workflow)
    with factory.begin() as db: db.get(InvoiceWorkflowSettings,"default").lease_until=now()+timedelta(minutes=10)
    assert client.post("/api/invoice-workflow/verify",json={"start":"2026-10-01","end":"2026-10-31"}).status_code==409
    assert client.post("/api/invoice-workflow/settings",json={"folders":["folder123456789"]}).status_code==409

def test_identity_preserves_series_separator_and_leading_zero():
    assert invoice_key("PT 510774318"," ft ","FT ABC / 001")==invoice_key("510774318","FT","abc/001")
    assert invoice_key("510774318","FT","ABC/001")!=invoice_key("510774318","FT","ABC/1")
    assert invoice_key("510774318","FT","ABC-1")!=invoice_key("510774318","FT","ABC/1")
    purchase=SimpleNamespace(supplier_nif="510774318",document_type="FT",document="FT ABC/1",document_date="2026-10-10",amount_cents=12300)
    assert not compare_evidence(text_fields("Filename FT ABC/1"),purchase)["safe"]
    assert compare_evidence(text_fields("NIF 510774318 FT ABC/1 Data 2026-10-10 Total 123,00 EUR"),purchase)["safe"]
    assert not compare_evidence(text_fields("NIF 510774318 FT ABC/1 Data 2026-10-10 Total 123,00 USD"),purchase)["safe"]

def test_failed_extraction_is_retried_and_never_cached_as_success(workflow):
    _,factory,drive,_,_,*_=workflow;drive.add("bad")
    drive.raw["bad"]=b"%PDF-invalid"
    row=verify(workflow)
    assert row["drive_state"]=="partial" and row["attachment_state"]=="unknown"
    with factory() as db: assert db.scalar(select(func.count()).select_from(InvoiceEvidenceCache))==0
    drive.add("bad")
    assert verify(workflow)["complete"]

def test_erp_exception_does_not_stop_drive_check(workflow,monkeypatch):
    _,_,drive,*_=workflow;drive.add("safe")
    def broken(*args): raise httpx.ConnectError("Fictitious ERP timeout")
    monkeypatch.setattr("app.invoice_workflow.live_erp",broken)
    row=verify(workflow)
    assert row["erp_state"]=="error" and row["attachment_state"]=="validated" and not row["complete"]

def test_actual_read_adapter_updates_snapshot_and_supplier_without_writes(workflow,monkeypatch):
    from app import db as database
    from app.platform_models import Company
    from app.platform import cipher
    _,factory,_,_,identifier,*_=workflow
    with factory() as db: purchase=db.get(Purchase,identifier);store=purchase.store_id
    with database.next_platform_session() as core:
        company=core.scalar(select(Company).where(Company.slug=="google-invoices"))
        company.operating_mode="integrated";company.erp_provider="wintouch";company.api_active=1;company.connector_writes=0;company.api_enterprise="demo-enterprise";company.api_url="https://api.wintouchcloud.com/api/v1";company.api_key_ciphertext=cipher().encrypt(b"demo-key").decode();company.sync_resources=json.dumps(["purchase_documents","entities"]);company_id=company.id;core.commit()
    class Client:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,*args,**kwargs): pytest.fail("A read-only workflow must not send any writes")
    class Read:
        def __init__(self,*args): pass
        def collection(self,kind):
            assert kind=="entities"
            return [{"ID":"supplier-demo","VATNumber":"PT510774318","Name":"Fornecedor Alfa","IsSupplier":True}]
    monkeypatch.setattr("app.invoice_workflow.httpx.Client",lambda **kwargs:Client())
    monkeypatch.setattr("app.invoice_workflow.Reader",Read)
    monkeypatch.setattr("app.invoice_workflow.snapshot",lambda *args:[{"external_id":"demo-erp","supplier_nif":"510774318","supplier_name":"Fornecedor Alfa","document":"ABC/1","internal_document":"COMPRA DEMO/1","document_type":"FT","document_date":"2026-10-10","amount_cents":12300,"cancelled":False}])
    with factory() as db:
        db.info["company"]={"id":company_id}
        result=actual_live_erp(db,[db.get(Purchase,identifier)],SimpleNamespace(role="administrador"),True)
        assert result["state"]=="checked" and len(result["documents"])==1 and result["suppliers"][0]["is_supplier"]
        assert db.scalar(select(func.count()).select_from(ERPDocument))==1

def test_schedule_and_demo_confirmation_are_explicit(workflow,monkeypatch):
    from app import db as database
    from app.invoice_workflow import scheduled_tick
    client,factory,*_=workflow
    payload={"folders":["folder123456789"],"interval_minutes":15,"window_days":90,"demo_confirmed":False}
    assert client.post("/api/invoice-workflow/settings",json=payload).status_code==422
    payload["demo_confirmed"]=True
    assert client.post("/api/invoice-workflow/settings",json=payload).status_code==200
    calls=[]
    monkeypatch.setattr("app.invoice_workflow.run_checks",lambda db,actor,data,stores,audit,source:calls.append({"company":db.info["company"]["id"],"stores":stores,"source":source,"confirmed":data.confirm_demo}))
    scheduled_tick();scheduled_tick()
    assert len(calls)==1 and calls[0]["source"]=="scheduled" and calls[0]["confirmed"]
    with factory() as db: assert db.get(InvoiceWorkflowSettings,"default").last_scheduled_at is not None

def test_read_only_role_cannot_verify_validate_or_configure(workflow):
    client,factory,drive,_,identifier,*_=workflow;drive.add("safe");row=verify(workflow)
    with factory.begin() as db:
        for user in db.scalars(select(User)): user.role="consulta"
    assert client.post("/api/invoice-workflow/verify",json={"start":"2026-10-01","end":"2026-10-31"}).status_code==403
    assert client.post("/api/invoice-workflow/settings",json={}).status_code==403
    assert client.post(f"/api/invoice-workflow/{identifier}/validate",json={"attachment_key":row["attachments"][0]["key"],"confirmed":True,"reason":"Consulta sem permissão"}).status_code==403

def test_missing_linked_file_is_reviewed_without_deleting_reference(workflow):
    _,factory,drive,*_=workflow;drive.add("safe");assert verify(workflow)["complete"]
    drive.files.clear()
    row=verify(workflow)
    assert not row["complete"] and row["drive_state"]=="review" and len(row["attachments"])==1
    with factory() as db: assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment))==1

def test_unknown_supplier_name_is_flagged_instead_of_invented(workflow):
    client,factory,drive,options,identifier,*_=workflow;drive.add("safe");options.update(erp=False,supplier=False)
    with factory.begin() as db: db.get(Purchase,identifier).supplier_name="Entidade não indicada"
    verify(workflow)
    proposal=client.get(f"/api/invoice-workflow/{identifier}/proposal").json()
    assert proposal["fields"]["supplier_name"]=="" and "Nome do fornecedor em falta" in proposal["missing_fields"]

def test_history_exposes_actor_and_checks_in_invoice_scope(workflow):
    client,_,drive,_,identifier,*_=workflow;drive.add("safe");verify(workflow)
    response=client.get(f"/api/invoice-workflow/{identifier}/history")
    assert response.status_code==200,response.text
    events=response.json()
    assert any(item["action"]=="efatura_verificada" for item in events)
    assert all(item["user_name"] and item["created_at"] for item in events)
