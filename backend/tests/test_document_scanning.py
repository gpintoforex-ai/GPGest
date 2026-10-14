import hashlib
import io
import json
from uuid import uuid4
from datetime import timedelta
from contextlib import contextmanager
import httpx
import pytest
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select, func
from app import db as database
from app.main import app
from app.models import (AttachmentIntegrationConfig, DocumentScan, DocumentScanSettings, DriveDocumentAttachment, Purchase, Register, ScanCloudFolder)
from app.document_scanning import PageEdit, make_pdf, process_image, cleanup, now
from test_platform import platform, create, enter
from fastapi import HTTPException

def photo(colour="red", size=(1000,1400), orientation=None):
    image = Image.new("RGB", size, colour); output = io.BytesIO()
    exif = image.getexif()
    if orientation: exif[274] = orientation
    image.save(output, "JPEG", exif=exif)
    return output.getvalue()

def pdf_pages_and_images(raw):
    return PdfReader(io.BytesIO(raw)).pages

class Drive:
    def __init__(self):
        self.files = {"root": {"id":"root", "name":"Root", "mimeType":"application/vnd.google-apps.folder", "capabilities":{"canAddChildren":True}, "parents":[]}}
        self.contents = {}; self.next = 0; self.uploads = 0; self.fail = False; self.writable = True
    def close(self): pass
    def get(self, url, params=None):
        params = params or {}
        if url.endswith("generateIds"):
            self.next += 1; return httpx.Response(200,json={"ids":["cloud-"+str(self.next)]})
        if url.endswith("/files"):
            return httpx.Response(200,json={"files":[]})
        identifier = url.rsplit("/",1)[-1]
        value = self.files.get(identifier)
        if not value: return httpx.Response(404,json={"error":"missing"})
        if identifier == "root": value = {**value,"capabilities":{"canAddChildren":self.writable}}
        return httpx.Response(200,json=value)
    def post(self,url,params=None,json=None,content=None,headers=None):
        if content is None:
            self.files[json["id"]] = {**json,"capabilities":{"canAddChildren":True}}; return httpx.Response(200,json={"id":json["id"]})
        boundary = headers["Content-Type"].split("boundary=")[1].encode()
        parts = content.split(b"--"+boundary)
        metadata = __import__('json').loads(parts[1].split(b"\r\n\r\n",1)[1][:-2])
        raw = parts[2].split(b"\r\n\r\n",1)[1][:-2]
        self.files[metadata["id"]] = {**metadata,"size":str(len(raw)),"md5Checksum":hashlib.md5(raw).hexdigest()}
        self.contents[metadata["id"]] = raw; self.uploads += 1
        if self.fail: self.fail=False; raise httpx.ReadTimeout("Fictitious uncertain result")
        return httpx.Response(200,json={"id":metadata["id"]})
    @contextmanager
    def stream(self,method,url,params=None):
        raw = self.contents[url.rsplit("/",1)[-1]]
        class Stream:
            status_code=200
            def iter_bytes(self): yield raw
        yield Stream()

@pytest.fixture
def scans(setup, monkeypatch):
    client, ids, factory = setup
    original = app.dependency_overrides[database.db_session]
    def selected():
        with factory() as db:
            db.info["company"]={"id":"demo-scan-company","name":"Empresa Demo"}
            yield db
    app.dependency_overrides[database.db_session]=selected
    with factory.begin() as db:
        store = db.get(Register,ids["registers"][0]).store_id
        purchase = Purchase(store_id=store,supplier_nif="500000000",supplier_name="Fornecedor Demo",document="FT DEMO",document_type="FT",document_date="2026-10-14",amount_cents=12300,origin="manual")
        db.add(purchase); db.flush(); purchase_id=purchase.id
        db.add(AttachmentIntegrationConfig(id="default",google_email="demo@example.pt",drive_folder="root"))
    drive=Drive()
    def authorized(db): return drive,db.get(AttachmentIntegrationConfig,"default")
    monkeypatch.setattr("app.document_scanning.authorized_client",authorized)
    monkeypatch.setattr("app.drive_documents.authorized_client",authorized)
    yield client,ids,factory,store,purchase_id,drive
    app.dependency_overrides[database.db_session]=original

def prepare(fixture,**changes):
    client,_,_,store,purchase,_=fixture
    metadata={"store_id":store,"purchase_id":purchase,"document_type":"FT","document_date":"2026-10-14","entity":"Fornecedor Demo","nif":"500000000","document":"FT DEMO","amount_cents":12300}
    metadata.update(changes.pop("metadata",{}))
    payload={"request_key":str(uuid4()),"metadata":metadata,"edits":[{},{}],**changes}
    result=client.post("/api/scans/prepare",data={"payload":json.dumps(payload)},files=[("files",("a.jpg",photo(),"image/jpeg")),("files",("b.jpg",photo("blue"),"image/jpeg"))])
    assert result.status_code==200,result.text
    return result.json(),payload

def test_exif_rotation_crop_colour_order_and_long_receipt():
    raw,size,_=process_image(photo(size=(1200,800),orientation=6),PageEdit())
    assert size==(800,1200)
    raw,size,_=process_image(photo(),PageEdit(rotation=90,left=.1,top=.1,width=.8,height=.8,colour="grey"))
    assert size==(1120,800)
    pdf,count,_=make_pdf([photo("blue"),photo("red",(700,3500))],[PageEdit(),PageEdit()],{"max_pages":20,"max_bytes":5*1024*1024})
    pages=pdf_pages_and_images(pdf)
    assert count==2 and float(pages[1].mediabox.height)/float(pages[1].mediabox.width)>4
    pixel=pages[0].images[0].image.getpixel((100,100)); assert pixel[2]>pixel[0]+100
    assert float(pages[0].mediabox.width)>float(pages[1].mediabox.width)

def test_prepare_upload_retry_same_file_existing_doc_and_validation(scans):
    client,_,factory,_,purchase,drive=scans
    result,_=prepare(scans)
    raw=client.get(result["view_url"]).content
    assert len(pdf_pages_and_images(raw))==2
    drive.fail=True
    assert client.post("/api/scans/"+result["id"]+"/upload",json={"confirmed":True}).status_code==502
    row=client.get("/api/scans/"+result["id"]).json()
    assert row["cloud_id"] and row["status"]=="upload_error"
    response=client.post("/api/scans/"+result["id"]+"/upload",json={"confirmed":True})
    assert response.status_code==200,response.text
    assert response.json()["status"]=="cloud_saved" and response.json()["validation_status"]=="por_validar"
    assert drive.uploads==1
    assert client.post("/api/scans/"+result["id"]+"/upload",json={"confirmed":True}).json()["cloud_id"]==row["cloud_id"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Purchase))==1
        assert db.scalar(select(func.count()).select_from(DriveDocumentAttachment))==1
        assert db.get(DocumentScan,result["id"]).pdf_ciphertext is None
        assert db.scalar(select(func.count()).select_from(ScanCloudFolder))==5
    detail=client.get("/api/documents/"+purchase+"/detail").json()
    assert detail["attachments"][0]["validation_status"]=="por_validar"
    assert client.get(result["view_url"]).content==raw
    assert client.post("/api/scans/"+result["id"]+"/validate",json={"confirmed":True}).json()["validation_status"]=="validado"

def test_remote_upload_recovers_after_association_failure_and_expiration(scans,monkeypatch):
    client,_,factory,_,_,drive=scans
    result,_=prepare(scans)
    from app.document_scanning import attach_reference
    def fail(*args): raise RuntimeError("Association failed")
    monkeypatch.setattr("app.document_scanning.attach_reference",fail)
    assert client.post("/api/scans/"+result["id"]+"/upload",json={"confirmed":True}).status_code==502
    with factory.begin() as db:
        db.get(DocumentScan,result["id"]).expires_at=now()-timedelta(hours=1)
        db.flush(); cleanup(db)
    monkeypatch.setattr("app.document_scanning.attach_reference",attach_reference)
    response=client.post("/api/scans/"+result["id"]+"/upload",json={"confirmed":True})
    assert response.status_code==200,response.text
    assert drive.uploads==1

def test_folder_permissions_limits_invalid_file_and_private_preview(scans):
    client,_,factory,_,_,drive=scans
    result,_=prepare(scans)
    drive.writable=False
    assert client.post("/api/scans/"+result["id"]+"/upload",json={"confirmed":True}).status_code==409
    assert drive.uploads==0
    assert client.post("/api/scans/image-preview",data={"edit":"{}"},files={"file":("fake.jpg",b"invalid","image/jpeg")}).status_code==422
    assert client.post("/api/scans/image-preview",data={"edit":json.dumps({"left":.9,"width":.9})},files={"file":("valid.jpg",photo(),"image/jpeg")}).status_code==422
    assert client.post("/api/auth/login",json={"email":"reader@test.pt","password":"PasswordTest123!"}).status_code==200
    assert client.get(result["view_url"]).status_code==200
    assert client.post("/api/scans/"+result["id"]+"/upload",json={"confirmed":True}).status_code==403
    client.cookies.clear()
    assert client.get(result["view_url"]).status_code==401

def test_cross_company_cannot_read_or_upload_scan(platform):
    client,_=platform
    a,b=create(client,"scan-empresa-a"),create(client,"scan-empresa-b")
    client.cookies.clear(); boot=enter(client,a["slug"])
    payload={"request_key":str(uuid4()),"metadata":{"store_id":boot["stores"][0]["id"],"document_type":"FT","document_date":"2026-10-14","entity":"Demo","document":"FT A"},"edits":[{}]}
    result=client.post("/api/scans/prepare",data={"payload":json.dumps(payload)},files=[("files",("a.jpg",photo(),"image/jpeg"))])
    assert result.status_code==200,result.text
    identifier=result.json()["id"]
    enter(client,b["slug"])
    assert client.get("/api/scans/"+identifier+"/pdf").status_code==404
    assert client.post("/api/scans/"+identifier+"/upload",json={"confirmed":True}).status_code==404

def test_existing_pdf_preserved_and_page_limits():
    limits={"max_pages":20,"max_bytes":5*1024*1024}
    pdf,_,_=make_pdf([photo(),photo("blue")],[PageEdit(),PageEdit()],limits)
    result,count,_=make_pdf([pdf],[],limits)
    assert result==pdf and count==2
    with pytest.raises(HTTPException):
        make_pdf([pdf],[],{**limits,"max_pages":1})
    from pypdf import PdfWriter
    writer=PdfWriter(); writer.add_blank_page(200,300); writer.encrypt("demo")
    output=io.BytesIO(); writer.write(output)
    with pytest.raises(HTTPException): make_pdf([output.getvalue()],[],limits)

def test_new_document_upload_and_integrity(scans):
    client,_,factory,_,_,drive=scans
    first,_=prepare(scans,metadata={"purchase_id":None,"document":"FT NEW DEMO"})
    second,_=prepare(scans,metadata={"purchase_id":None,"document":"FT NEW DEMO"})
    assert first["name"]!=second["name"]
    response=client.post("/api/scans/"+first["id"]+"/upload",json={"confirmed":True})
    assert response.status_code==200,response.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Purchase))==2
    identifier=response.json()["cloud_id"]
    drive.contents[identifier]=make_pdf([photo("green")],[PageEdit()],{"max_pages":20,"max_bytes":5*1024*1024})[0]
    assert client.get(first["view_url"]).status_code==409

def test_folder_change_requires_new_preparation(scans):
    client,_,factory,_,_,drive=scans
    result,_=prepare(scans)
    with factory.begin() as db: db.get(AttachmentIntegrationConfig,"default").drive_folder="different"
    response=client.post("/api/scans/"+result["id"]+"/upload",json={"confirmed":True})
    assert response.status_code==409 and drive.uploads==0

def test_qr_endpoint_reads_without_storing_or_uploading(scans):
    from test_invoice_qr import document, PAYLOAD
    client,_,factory,_,_,drive=scans
    response=client.post("/api/scans/read-qr",files={"file":("demo.pdf",document(PAYLOAD),"application/pdf")})
    assert response.status_code==200,response.text
    assert response.json()["documents"][0]["amount_cents"]==12345
    assert drive.uploads==0
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(DocumentScan))==0
    assert client.post("/api/scans/read-qr",files={"file":("invalid.pdf",b"invalid","application/pdf")}).status_code==422
