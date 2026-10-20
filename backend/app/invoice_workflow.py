"""Independent e-Fatura / Drive / ERP checks; remote creation is capability-gated."""
import hashlib
import json
import re
from contextlib import closing
from datetime import date, timedelta
from typing import Literal
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo
import httpx
from fastapi import Depends, HTTPException, Response
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from . import db as database
from .db import db_session
from .schemas import Strict
from .models import (InvoiceWorkflowSettings, InvoiceWorkflowCheck, InvoiceEvidenceCache,
    InvoiceEvidenceValidation, InvoiceWorkflowRun, Purchase, PurchaseAttachment, DriveDocumentAttachment,
    DocumentERPLink, ERPDocument, DocumentScan, AttachmentIntegrationConfig, User, Audit, Store, now)
from .platform_models import Company
from .platform import cipher, validate_url
from .operating_modes import policy, company_policy
from .drive_documents import folder_tree, content, metadata, belongs_to_tree, attach_reference, read_link
from .google_invoices import DRIVE, MAX_BYTES, authorized_client, api_json, filename
from .invoice_evidence import extract, compare_evidence, invoice_key, nif_key, ocr_command
from .erp_documents import snapshot
from .wintouch_sales import Reader

def settings_for(db, lock=False):
    db.execute(insert(InvoiceWorkflowSettings).values(id="default").on_conflict_do_nothing())
    return db.scalar(select(InvoiceWorkflowSettings).where(InvoiceWorkflowSettings.id=="default").with_for_update() if lock else select(InvoiceWorkflowSettings).where(InvoiceWorkflowSettings.id=="default"))

def configured_folders(db, settings):
    folders=json.loads(settings.folders)
    fallback=db.get(AttachmentIntegrationConfig,"default")
    return list(dict.fromkeys(folders or ([fallback.drive_folder] if fallback and fallback.drive_folder else [])))

def drive_folder_ids(values):
    normalized=[]
    for value in values:
        value=value.strip()
        if value.startswith("https://drive.google.com/drive/folders/"): value=value.split("/folders/",1)[1].split("?",1)[0].strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}",value): raise ValueError("Indique links ou identificadores de pastas Drive válidos.")
        normalized.append(value)
    return list(dict.fromkeys(normalized))

class SettingsInput(Strict):
    folders: list[str] = Field(default_factory=list,max_length=20)
    efatura_folders: list[str] = Field(default_factory=list,max_length=20)
    include_subfolders: bool = True
    tolerance_cents: int = Field(default=2,ge=0,le=5)
    ocr_enabled: bool = False
    interval_minutes: int = Field(default=0,ge=0,le=1440)
    window_days: int = Field(default=90,ge=1,le=366)
    demo_confirmed: bool = False
    @model_validator(mode="after")
    def validate(self):
        self.folders=drive_folder_ids(self.folders)
        self.efatura_folders=drive_folder_ids(self.efatura_folders)
        if self.interval_minutes and self.interval_minutes<15: raise ValueError("O intervalo mínimo é 15 minutos.")
        return self

class VerifyInput(Strict):
    start: date
    end: date
    store_ids: list[str] = Field(default_factory=list,max_length=100)
    confirm_demo: bool = False
    force: bool = False
    # O assistente separa a procura de anexos da conferência de compras; por omissão faz ambas.
    parts: list[Literal["attachments","purchases"]] = Field(default_factory=lambda:["attachments","purchases"],min_length=1,max_length=2)
    @model_validator(mode="after")
    def period(self):
        if self.start>self.end or (self.end-self.start).days>366: raise ValueError("Selecione um período de até um ano.")
        return self

class ReviewInput(Strict):
    attachment_key: str = Field(max_length=240)
    confirmed: bool
    reason: str = Field(min_length=3,max_length=2000)

class ProposalInput(Strict):
    supplier_name: str = Field(max_length=300)
    supplier_nif: str = Field(max_length=30)
    document: str = Field(max_length=100)
    document_type: str = Field(max_length=10)
    document_date: date
    amount_cents: int = Field(strict=True,ge=-(10**13),le=10**13)
    currency: str = Field(default="EUR",min_length=3,max_length=3)
    address: str = Field(default="",max_length=500)
    email: str = Field(default="",max_length=200)
    notes: str = Field(default="",max_length=2000)
    lines: list[dict] = Field(default_factory=list,max_length=100)
    confirmed: bool = False
    reason: str = Field(default="",max_length=2000)

def live_erp(db, purchases, user, confirm_demo=False):
    config=policy(db)
    if not config["capabilities"].get("read_documents") or "purchase_documents" not in config["resources"]:
        return {"state":"unavailable","message":"ERP sem ligação ativa ou consulta de compras indisponível.","documents":[],"suppliers":[],"supplier_state":"unavailable"}
    if user.role!="administrador":
        return {"state":"unavailable","message":"É necessária uma consulta atual por um administrador; o catálogo local não prova a ausência de lançamento.","documents":[],"suppliers":[],"supplier_state":"unavailable"}
    company_id=db.info.get("company",{}).get("id")
    with database.next_platform_session() as core:
        company=core.get(Company,company_id)
        if not company: return {"state":"unavailable","message":"Configure a empresa e a ligação ERP.","documents":[],"suppliers":[],"supplier_state":"unavailable"}
        if company.mode=="demonstracao" and not confirm_demo:
            return {"state":"unavailable","message":"Confirme a consulta de dados reais nesta empresa de demonstração.","documents":[],"suppliers":[],"supplier_state":"unavailable"}
        url,enterprise=validate_url(company.api_url),company.api_enterprise
        key=cipher().decrypt(company.api_key_ciphertext.encode()).decode()
    try:
        start=min(date.fromisoformat(row.document_date) for row in purchases)-timedelta(days=366)
        end=max(date.fromisoformat(row.document_date) for row in purchases)+timedelta(days=366)
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            reader=Reader(client,url,enterprise,key)
            incoming=snapshot(reader,start.isoformat(),end.isoformat())
            supplier_state="unavailable"; suppliers=[]
            if config["capabilities"].get("read_entities") and "entities" in config["resources"]:
                try:
                    suppliers=[{"external_id":item["ID"],"nif":nif_key(item.get("VATNumber")),"name":item.get("Name",""),"is_supplier":item.get("IsSupplier") is True,"active":not bool(item.get("DeletedDate"))} for item in reader.collection("entities")]
                    supplier_state="checked"
                except Exception: supplier_state="error"
        purchase_by_key={invoice_key(row.supplier_nif,row.document_type,row.document):row for row in purchases}
        documents=[]
        for item in incoming:
            target=purchase_by_key.get(invoice_key(item["supplier_nif"],item["document_type"],item["document"]))
            # Unrelated enterprise documents are not reassigned to a user's store.
            if not target: continue
            erp=db.scalar(select(ERPDocument).where(ERPDocument.enterprise_id==enterprise,ERPDocument.external_id==item["external_id"]).with_for_update())
            if not erp: erp=ERPDocument(enterprise_id=enterprise,store_id=target.store_id,**item); db.add(erp)
            else:
                for name,value in item.items(): setattr(erp,name,value)
                erp.synced_at=now()
            db.flush(); documents.append(erp.id)
        db.commit()
        return {"state":"checked","documents":documents,"suppliers":suppliers,"supplier_state":supplier_state,"message":""}
    except Exception:
        db.rollback()
        return {"state":"error","message":"Não foi possível verificar o ERP. Tente novamente; a ausência no catálogo não confirma que falta um lançamento.","documents":[],"suppliers":[],"supplier_state":"error"}

def remote_listing(client, roots, subfolders=True):
    files={}; folders=set()
    for root in roots: folders.update(folder_tree(client,root) if subfolders else [root])
    for folder in sorted(folders):
        token=""
        while True:
            listing=api_json(client.get(DRIVE+"/files",params={"q":f"'{folder}' in parents and trashed=false and (mimeType='application/pdf' or mimeType='image/jpeg' or mimeType='image/png')","pageSize":1000,"pageToken":token,"fields":"files(id,name,mimeType,size,modifiedTime,md5Checksum,parents),nextPageToken","supportsAllDrives":True,"includeItemsFromAllDrives":True}),"Pesquisar anexos Drive")
            for item in listing.get("files",[]):
                files[item["id"]]={**item,"folder":folder}
                if len(files)>5000: raise HTTPException(422,"A pesquisa excede 5000 ficheiros. Configure pastas mais específicas.")
            token=listing.get("nextPageToken","")
            if not token: break
    return list(files.values())

def cached_file(db, account, item, raw=None, use_ocr=False):
    version=json.dumps([item.get("md5Checksum") or item.get("modifiedTime") or "",item.get("size"),bool(use_ocr),bool(ocr_command())])
    cache=db.scalar(select(InvoiceEvidenceCache).where(InvoiceEvidenceCache.account==account,InvoiceEvidenceCache.drive_id==item["id"]))
    if cache and cache.version==version and cache.digest:
        cache.name,cache.folder=filename(item["name"]),item["folder"]
        return cache
    if raw is None: return None
    # Extraction may fail. Do not persist a successful-looking cache/version before it succeeds.
    evidence=extract(raw,item["mimeType"],use_ocr)
    if not cache:
        cache=InvoiceEvidenceCache(account=account,drive_id=item["id"],name=filename(item["name"]),folder=item["folder"],mime=item["mimeType"],version=version); db.add(cache)
    cache.name,cache.folder,cache.mime,cache.version=filename(item["name"]),item["folder"],item["mimeType"],version
    cache.digest=hashlib.sha256(raw).hexdigest(); cache.evidence=json.dumps(evidence); cache.checked_at=now()
    db.flush(); return cache

def drive_checks(db, settings, force=False):
    roots=configured_folders(db,settings)
    if not roots: return {"state":"unavailable","message":"Configure a conta Google e as pastas de pesquisa em Configurações > Anexos.","files":[],"complete":False}
    caches=[]; complete=True; processed=0; failures=[]; started=monotonic()
    try:
        with Session(bind=db.get_bind(),expire_on_commit=False) as auth_db: client,config=authorized_client(auth_db)
        with closing(client):
            items=remote_listing(client,roots,settings.include_subfolders)
            for item in items:
                if int(item.get("size",0))>MAX_BYTES:
                    complete=False; failures.append("Há anexos acima de 5 MB; reveja-os individualmente."); continue
                cache=None if force else cached_file(db,config.google_email.lower(),item,use_ocr=settings.ocr_enabled)
                if not cache:
                    if processed>=100 or monotonic()-started>180: complete=False; continue
                    try:
                        raw,mime=content(client,item["id"]); item["mimeType"]=mime
                        cache=cached_file(db,config.google_email.lower(),item,raw,settings.ocr_enabled); processed+=1
                    except Exception:
                        complete=False; failures.append("Não foi possível ler um ou mais anexos. Pode repetir a verificação."); continue
                caches.append(cache)
            db.commit()
        return {"state":"checked" if complete else "partial","files":caches,"complete":complete,"message":" ".join(dict.fromkeys(failures)) or ("Pesquisa incremental incompleta. Verifique novamente para continuar os ficheiros em falta." if not complete else ""),"read_count":processed}
    except Exception:
        db.rollback()
        return {"state":"error","message":"Não foi possível verificar o Drive. Reveja a ligação, a conta e as permissões das pastas; pode repetir.","files":[],"complete":False}

def existing_evidence(db, purchase, context=None):
    keys={"drive:"+row.id:row.digest for row in (context.drives[purchase.id] if context is not None else db.scalars(select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id==purchase.id)))}
    keys.update({row.id:row.digest for row in context.natives[purchase.id]} if context is not None else {row.id:hashlib.sha256(row.data).hexdigest() for row in db.scalars(select(PurchaseAttachment).where(PurchaseAttachment.purchase_id==purchase.id))})
    for row in (context.validations[purchase.id] if context is not None else db.scalars(select(InvoiceEvidenceValidation).where(InvoiceEvidenceValidation.purchase_id==purchase.id))):
        if keys.get(row.attachment_key)==row.digest: return True
    for scan in (context.scans[purchase.id] if context is not None else db.scalars(select(DocumentScan).where(DocumentScan.purchase_id==purchase.id,DocumentScan.status=="cloud_saved",DocumentScan.validation_status=="validado"))):
        if scan.validation_status != "validado": continue
        if "drive:"+str(scan.attachment_id) in keys and keys["drive:"+str(scan.attachment_id)]==scan.digest: return True
    return False

def validate_evidence(db,user,purchase,key,digest,source,audit):
    if key.startswith("drive:"):
        for scan in db.scalars(select(DocumentScan).where(DocumentScan.attachment_id==key[6:],DocumentScan.digest==digest,DocumentScan.status=="cloud_saved")):
            scan.validation_status="validado"
    row=db.get(InvoiceEvidenceValidation,(purchase.id,key))
    if row and row.digest==digest: return
    if not row: row=InvoiceEvidenceValidation(purchase_id=purchase.id,attachment_key=key,digest=digest,source=source,user_id=user.id); db.add(row)
    else: row.digest,row.source,row.user_id,row.created_at=digest,source,user.id,now()
    audit(db,user,"efatura_anexo_validado",purchase.id,{"attachment_key":key,"digest":digest,"source":source},purchase.store_id)

def current_erp_link(db,purchase):
    return db.scalar(select(DocumentERPLink).where(DocumentERPLink.purchase_id==purchase.id,DocumentERPLink.undone_at.is_(None)))

def summarize(db,purchase,context=None):
    check=context.checks.get(purchase.id) if context is not None else db.get(InvoiceWorkflowCheck,purchase.id)
    links=context.drives[purchase.id] if context is not None else list(db.scalars(select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id==purchase.id)))
    natives=context.natives[purchase.id] if context is not None else list(db.scalars(select(PurchaseAttachment).where(PurchaseAttachment.purchase_id==purchase.id)))
    attachments=[{"key":"drive:"+item.id,"name":item.name,"url":"/api/drive-attachments/"+item.id+"/view","drive_url":"https://drive.google.com/file/d/"+item.drive_id+"/view"} for item in links]+[{"key":item.id,"name":item.name,"url":"/api/documents/attachments/"+item.id+"/view"} for item in natives]
    valid=existing_evidence(db,purchase,context)
    erp=context.links.get(purchase.id) if context is not None else current_erp_link(db,purchase)
    erp_state=check.erp_state if check else "unknown"
    if erp and erp_state not in ("error","divergence"): erp_state="linked"
    drive_state=check.drive_state if check else "unknown"
    attachment_state="validated" if valid else "review" if attachments or check and json.loads(check.drive_candidates) else "missing" if drive_state=="checked" else "unknown"
    complete=valid and erp is not None and erp_state=="linked" and (drive_state in ("checked","unknown") or not links and bool(natives))
    return {"id":purchase.id,"store_id":purchase.store_id,"document_date":purchase.document_date,"supplier_name":purchase.supplier_name,"supplier_nif":purchase.supplier_nif,"document":purchase.document,"document_type":purchase.document_type,"amount_cents":purchase.amount_cents,"currency":"EUR","origin":purchase.origin,"complete":complete,"attachment_state":attachment_state,"drive_state":drive_state,"erp_state":erp_state,"attachments":attachments,"erp_candidates":json.loads(check.erp_candidates) if check else [],"drive_candidates":json.loads(check.drive_candidates) if check else [],"messages":json.loads(check.messages) if check else [],"supplier":json.loads(check.supplier) if check else {},"proposal":json.loads(check.proposal) if check else {},"proposal_reviewed_at":check.proposal_reviewed_at if check else None,"checked_at":check.checked_at if check else None}

def same_erp(purchase, erp, tolerance):
    differences=[]
    if not nif_key(purchase.supplier_nif) or not purchase.document_type.strip() or not purchase.document.strip(): differences.append("NIF, tipo ou número em falta")
    if invoice_key(purchase.supplier_nif,purchase.document_type,purchase.document)!=invoice_key(erp.supplier_nif,erp.document_type,erp.document): differences.append("Identidade divergente")
    if erp.document_date!=purchase.document_date: differences.append("Data divergente")
    if abs(erp.amount_cents-purchase.amount_cents)>tolerance: differences.append("Total divergente")
    if erp.cancelled: differences.append("Documento anulado")
    return differences

def evidence_fields(evidence, purchase, tolerance):
    """Campos do anexo que coincidem, divergem ou não foram identificados, face à fatura."""
    target=invoice_key(purchase.supplier_nif,purchase.document_type,purchase.document)
    documents=evidence.get("documents",[])
    if documents:
        nifs={nif_key(item["nif"]) for item in documents}; numbers={invoice_key(item["nif"],item["document_type"],item["document"])[1:] for item in documents}
        dates={item["document_date"] for item in documents}; totals={abs(item["amount_cents"]) for item in documents}
    else:
        nifs=set(evidence.get("nifs",[])); numbers={invoice_key(purchase.supplier_nif,item["kind"],item["number"])[1:] for item in evidence.get("numbers",[])}
        dates=set(evidence.get("dates",[])); totals=set(evidence.get("totals",[]))
    result={"matching":[],"divergent":[],"missing":[]}
    def place(label, values, ok):
        result["missing" if not values else "matching" if ok else "divergent"].append(label)
    place("NIF do fornecedor",nifs,nifs=={target[0]})
    place("Tipo e número",numbers,numbers=={target[1:]})
    place("Data",dates,dates=={purchase.document_date})
    place("Total",totals,len(totals)==1 and abs(next(iter(totals))-abs(purchase.amount_cents))<=tolerance)
    currencies=set(evidence.get("currencies",[]))
    if currencies: place("Moeda",currencies,currencies=={"EUR"})
    return result

def supplier_status(result,nif):
    matches=[item for item in result.get("suppliers",[]) if item["nif"]==nif_key(nif)]
    ready=len(matches)==1 and matches[0].get("is_supplier") and matches[0].get("active",True)
    return {"state":"exists" if ready else "review" if matches else "not_found" if result.get("supplier_state")=="checked" else "unknown","candidates":matches}

def verify_documents(db,user,purchases,settings,audit,confirm_demo=False,force=False,parts=("attachments","purchases")):
    check_erp="purchases" in parts; check_drive="attachments" in parts
    skipped={"state":"skipped","message":"","documents":[],"suppliers":[],"supplier_state":"skipped","files":[],"complete":False}
    if check_erp:
        try: erp=live_erp(db,purchases,user,confirm_demo)
        except Exception:
            db.rollback()
            erp={"state":"error","message":"Não foi possível abrir ou consultar a ligação ERP. A verificação dos anexos continua independente.","documents":[],"suppliers":[],"supplier_state":"error"}
    else: erp=skipped
    drive=drive_checks(db,settings,force) if check_drive else skipped
    current_ids=set(erp["documents"])
    erp_rows=list(db.scalars(select(ERPDocument).where(ERPDocument.id.in_(current_ids)))) if current_ids else []
    for purchase in purchases:
        purchase=db.scalar(select(Purchase).where(Purchase.id==purchase.id).with_for_update().execution_options(populate_existing=True))
        check=db.get(InvoiceWorkflowCheck,purchase.id) or InvoiceWorkflowCheck(purchase_id=purchase.id)
        db.add(check); messages=[text for text in (erp.get("message"),drive.get("message")) if text]
        if not check_erp or not check_drive:
            # As mensagens da parte não verificada agora mantêm-se até à próxima verificação dessa parte.
            messages=list(dict.fromkeys([*messages,*json.loads(check.messages or "[]")]))
        check.checked_at=now()
        if check_drive: check.drive_state=drive["state"]
        if check_erp: check.erp_state=erp["state"]
        candidates=[]; link=current_erp_link(db,purchase)
        for item in erp_rows:
            if invoice_key(item.supplier_nif,item.document_type,item.document)!=invoice_key(purchase.supplier_nif,purchase.document_type,purchase.document): continue
            differences=same_erp(purchase,item,settings.tolerance_cents)
            if item.store_id!=purchase.store_id: differences.append("Documento importado noutra loja")
            other=db.scalar(select(DocumentERPLink).where(DocumentERPLink.erp_id==item.id,DocumentERPLink.undone_at.is_(None),DocumentERPLink.purchase_id!=purchase.id))
            if other: differences.append("Já associado a outra fatura")
            candidates.append({"id":item.id,"external_id":item.external_id,"document":item.internal_document or item.document,"differences":differences})
        if erp["state"]=="checked":
            if link:
                if link.erp_id not in current_ids or any(item["id"]==link.erp_id and item["differences"] for item in candidates): check.erp_state="divergence"; messages.append("A associação ERP foi preservada, mas a consulta atual exige revisão.")
                else: check.erp_state="linked"
            elif len(candidates)==1 and not candidates[0]["differences"] and purchase.origin!="efatura_testes":
                target=db.scalar(select(ERPDocument).where(ERPDocument.id==candidates[0]["id"]).with_for_update())
                if not current_erp_link(db,purchase) and not db.scalar(select(DocumentERPLink).where(DocumentERPLink.erp_id==target.id,DocumentERPLink.undone_at.is_(None))):
                    db.add(DocumentERPLink(purchase_id=purchase.id,erp_id=target.id,reason="Correspondência única e segura verificada automaticamente no assistente e-Fatura"))
                    audit(db,user,"efatura_erp_associado_automaticamente",purchase.id,{"erp_id":target.external_id,"tolerance_cents":settings.tolerance_cents},purchase.store_id)
                    check.erp_state="linked"
            else: check.erp_state="divergence" if candidates else "not_found"
        if check_erp: check.erp_candidates=json.dumps(candidates)
        if not check_drive:
            if check_erp: check.supplier=json.dumps(supplier_status(erp,purchase.supplier_nif))
            check.messages=json.dumps(messages)
            audit(db,user,"efatura_verificada",purchase.id,{"erp":check.erp_state,"drive":"nao_verificado","messages":messages},purchase.store_id)
            db.commit(); continue
        available=[]
        rejected=set(json.loads(check.rejected or "[]"))
        for cache in drive["files"]:
            if cache.drive_id in rejected: continue
            evidence=json.loads(cache.evidence); match=compare_evidence(evidence,purchase,settings.tolerance_cents)
            name_key=invoice_key(purchase.supplier_nif,purchase.document_type,purchase.document)[2]
            named=bool(name_key and name_key.replace("/","_") in cache.name.upper()) or bool(purchase.supplier_nif and purchase.supplier_nif in cache.name)
            if match["candidate"] or named:
                available.append({"drive_id":cache.drive_id,"cache_id":cache.id,"name":cache.name,"safe":match["safe"],"content_match":bool(match["candidate"]),"reasons":match["reasons"],"fields":evidence_fields(evidence,purchase,settings.tolerance_cents),"source":evidence.get("source"),"url":"https://drive.google.com/file/d/"+cache.drive_id+"/view"})
            for warning in evidence.get("warnings",[]):
                if warning not in messages: messages.append(warning)
        attached=list(db.scalars(select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id==purchase.id)))
        for item in available:
            # Forte: o conteúdo confirma todos os campos e é o único candidato; possível: o conteúdo
            # identifica o documento, mas há divergências; ambígua: só pelo nome ou vários candidatos.
            item["strength"]="forte" if item["safe"] and len(available)==1 else "possivel" if item["content_match"] and len(available)==1 else "ambigua"
        safe=[item for item in available if item["safe"]]
        # A unique safe file is automatic only after every file in the configured tree was checked.
        if len(safe)==1 and len(available)==1 and drive["complete"] and not attached:
            cache=db.get(InvoiceEvidenceCache,safe[0]["cache_id"])
            other=db.scalar(select(DriveDocumentAttachment).where(DriveDocumentAttachment.account==cache.account,DriveDocumentAttachment.drive_id==cache.drive_id))
            if not other:
                association=attach_reference(db,purchase,cache.account,cache.drive_id,cache.folder,cache.name,cache.mime,cache.digest,user,audit)
                attached=[association]
                validate_evidence(db,user,purchase,"drive:"+association.id,cache.digest,"automatic",audit)
            elif other.purchase_id!=purchase.id: messages.append("O anexo seguro já pertence a outra fatura. Reveja a associação.")
        for association in attached:
            cache=next((item for item in drive["files"] if item.account==association.account and item.drive_id==association.drive_id),None)
            if not cache and drive["state"]=="checked":
                check.drive_state="review"
                messages.append("Um anexo associado não foi localizado nas pastas/conta de pesquisa. A referência foi preservada; abra o ficheiro e reveja a disponibilidade.")
            if cache:
                if association.digest and len(association.digest)==64 and association.digest!=cache.digest:
                    validation=db.get(InvoiceEvidenceValidation,(purchase.id,"drive:"+association.id))
                    if validation: db.delete(validation)
                    for scan in db.scalars(select(DocumentScan).where(DocumentScan.attachment_id==association.id)):
                        scan.validation_status="por_validar"
                    check.drive_state="review"
                    messages.append("O ficheiro de um anexo associado foi alterado. Reveja antes de validar.")
                elif compare_evidence(json.loads(cache.evidence),purchase,settings.tolerance_cents)["safe"]:
                    if not association.digest: association.digest=cache.digest
                    validate_evidence(db,user,purchase,"drive:"+association.id,cache.digest,"automatic",audit)
                association.name=cache.name  # The ID/reference survives renaming.
        for native in db.scalars(select(PurchaseAttachment).where(PurchaseAttachment.purchase_id==purchase.id)):
            item={"id":native.id,"name":native.name,"folder":"local","mimeType":native.mime,"size":len(native.data),"md5Checksum":hashlib.sha256(native.data).hexdigest()}
            try:
                cache=cached_file(db,"local",item,native.data,settings.ocr_enabled)
                if compare_evidence(json.loads(cache.evidence),purchase,settings.tolerance_cents)["safe"]: validate_evidence(db,user,purchase,native.id,cache.digest,"automatic",audit)
            except Exception: messages.append("Não foi possível extrair os dados de um anexo local. Reveja manualmente.")
        check.drive_candidates=json.dumps(available[:30]); check.messages=json.dumps(messages)
        if check_erp: check.supplier=json.dumps(supplier_status(erp,purchase.supplier_nif))
        audit(db,user,"efatura_verificada",purchase.id,{"erp":check.erp_state if check_erp else "nao_verificado","drive":check.drive_state,"attachment_validated":existing_evidence(db,purchase),"messages":messages},purchase.store_id)
        db.commit()
    return {"erp":erp["state"],"drive":drive["state"],"files_read":drive.get("read_count",0)}

def run_checks(db,user,data,stores,audit,source="manual"):
    parts=tuple(getattr(data,"parts",None) or ("attachments","purchases"))
    settings=settings_for(db,True)
    if settings.lease_until and settings.lease_until>now(): raise HTTPException(409,"Já existe uma verificação em curso nesta empresa. Aguarde antes de repetir.")
    run=InvoiceWorkflowRun(user_id=user.id,stores=json.dumps(stores),start_date=str(data.start),end_date=str(data.end),source=source)
    db.add(run); db.flush(); identifier=run.id
    settings.run_id,settings.lease_until=identifier,now()+timedelta(minutes=20); db.commit()
    try:
        purchases=list(db.scalars(select(Purchase).outerjoin(InvoiceWorkflowCheck,InvoiceWorkflowCheck.purchase_id==Purchase.id).where(Purchase.store_id.in_(stores),Purchase.document_date>=str(data.start),Purchase.document_date<=str(data.end)).order_by(InvoiceWorkflowCheck.checked_at.asc().nullsfirst(),Purchase.document_date).limit(200)))
        detail=verify_documents(db,user,purchases,settings,audit,data.confirm_demo,data.force,parts) if purchases else {"message":"Não existem faturas neste período."}
        run=db.get(InvoiceWorkflowRun,identifier); run.status="concluida_com_pendencias" if detail.get("erp") in ("error","unavailable") or detail.get("drive") in ("error","partial","unavailable") else "concluida"
        run.detail=json.dumps({**detail,"checked":len(purchases)}); run.finished_at=now()
        audit(db,user,"efatura_verificacao_concluida",identifier,{"source":source,"status":run.status,**detail})
        db.commit(); return {"run_id":identifier,"status":run.status,"checked":len(purchases),**detail}
    except Exception:
        db.rollback(); run=db.get(InvoiceWorkflowRun,identifier); run.status="falhou"; run.finished_at=now(); run.detail=json.dumps({"error":"Verificação interrompida. As associações já confirmadas foram preservadas; pode repetir."})
        audit(db,user,"efatura_verificacao_falhou",identifier,json.loads(run.detail))
        db.commit(); raise HTTPException(502,"Não foi possível concluir a verificação. As associações confirmadas foram preservadas; pode repetir.") from None
    finally:
        db.rollback(); settings=settings_for(db,True)
        if settings.run_id==identifier: settings.run_id,settings.lease_until=None,None
        db.commit()

def attach_from_drive(db,user,purchase,drive_id,reason,audit,source="manual"):
    """Associa um ficheiro das pastas de pesquisa a uma fatura, depois de o ler e validar."""
    settings=settings_for(db)
    with Session(bind=db.get_bind(),expire_on_commit=False) as auth_db: client,config=authorized_client(auth_db)
    with closing(client):
        info=metadata(client,drive_id)
        if not any(belongs_to_tree(client,root,info) for root in configured_folders(db,settings)): raise HTTPException(422,"O ficheiro não pertence às pastas de pesquisa desta empresa.")
        raw,mime=content(client,drive_id)
    digest=hashlib.sha256(raw).hexdigest()
    link=attach_reference(db,purchase,config.google_email.lower(),drive_id,(info.get("parents") or [""])[0],info["name"],mime,digest,user,audit)
    link.digest,link.name,link.mime=digest,filename(info["name"]),mime
    validate_evidence(db,user,purchase,"drive:"+link.id,digest,source,audit)
    audit(db,user,"efatura_anexo_associado_manualmente",purchase.id,{"drive_id":drive_id,"reason":reason,"source":source},purchase.store_id)
    return link

def register_routes(app,current,authorize,scope,audit):
    def get_purchase(db,user,identifier,write=False):
        row=db.scalar(select(Purchase).where(Purchase.id==identifier).with_for_update() if write else select(Purchase).where(Purchase.id==identifier))
        if not row: raise HTTPException(404,"Fatura indisponível nesta empresa.")
        authorize(user,db,row.store_id,["administrador","gestor"] if write else None)
        return row

    @app.get("/api/invoice-workflow/settings")
    def get_settings(user=Depends(current),db:Session=Depends(db_session)):
        authorize(user,db,roles=["administrador","gestor"])
        settings=settings_for(db); db.commit()
        return {"folders":json.loads(settings.folders),"efatura_folders":json.loads(settings.efatura_folders or "[]"),"include_subfolders":settings.include_subfolders,"effective_folders":configured_folders(db,settings),"tolerance_cents":settings.tolerance_cents,"ocr_enabled":settings.ocr_enabled,"ocr_available":bool(ocr_command()),"interval_minutes":settings.interval_minutes,"window_days":settings.window_days,"demo_confirmed":settings.demo_confirmed,"last_scheduled_at":settings.last_scheduled_at,"erp_capabilities":policy(db)["capabilities"],"creation_available":False,"creation_message":"O conector atual não implementa a criação de fornecedores nem faturas de compra. Pode preparar e exportar uma proposta, sem criar um lançamento no ERP."}

    @app.post("/api/invoice-workflow/settings")
    def save_settings(data:SettingsInput,user=Depends(current),db:Session=Depends(db_session)):
        authorize(user,db,roles=["administrador"])
        if data.interval_minutes and db.info.get("company",{}).get("mode")=="demonstracao" and not data.demo_confirmed: raise HTTPException(422,"Confirme as consultas reais agendadas nesta empresa de demonstração.")
        settings=settings_for(db,True)
        if settings.lease_until and settings.lease_until>now(): raise HTTPException(409,"Aguarde o fim da verificação antes de alterar as pastas.")
        settings.folders=json.dumps(data.folders); settings.efatura_folders=json.dumps(data.efatura_folders); settings.include_subfolders=data.include_subfolders; settings.actor_id=user.id
        for name in ("tolerance_cents","ocr_enabled","interval_minutes","window_days","demo_confirmed"): setattr(settings,name,getattr(data,name))
        audit(db,user,"efatura_configuracao_guardada","default",data.model_dump()); db.commit()
        return {"ok":True}

    @app.get("/api/invoice-workflow")
    def listing(start:date,end:date,store_id:str="",user=Depends(current),db:Session=Depends(db_session)):
        authorize(user,db)
        if start>end or (end-start).days>366: raise HTTPException(422,"Selecione um período de até um ano.")
        stores=scope(user,db)
        if store_id: authorize(user,db,store_id); stores=[store_id] if store_id in stores else []
        purchases=list(db.scalars(select(Purchase).where(Purchase.store_id.in_(stores),Purchase.document_date>=str(start),Purchase.document_date<=str(end)).order_by(Purchase.document_date.desc(),Purchase.document)))
        from .document_projection import WorkflowContext
        context=WorkflowContext(db,purchases)
        rows=[summarize(db,item,context) for item in purchases]
        for row in rows:
            check=context.checks.get(row["id"])
            row["rejected_raw"]=check.rejected if check else "[]"
        from .invoice_conference import enrich
        settings=settings_for(db); rows,mode=enrich(db,rows,purchases,settings,stores,start,end); db.commit()
        counts={}
        for row in rows: counts[row["action"]]=counts.get(row["action"],0)+1
        return {"rows":rows,"mode":mode,"summary":{"total":len(rows),"completed":sum(row["complete"] for row in rows),"pending":sum(not row["complete"] for row in rows),"without_efatura":sum(row["efatura_state"]=="absent" for row in rows),"actions":counts},"creation_available":False}

    @app.post("/api/invoice-workflow/verify")
    def verify(data:VerifyInput,user=Depends(current),db:Session=Depends(db_session)):
        authorize(user,db,roles=["administrador","gestor"])
        stores=list(dict.fromkeys(data.store_ids or scope(user,db)))
        for store in stores:
            authorize(user,db,store)
            if not db.get(Store,store): raise HTTPException(404,"Loja indisponível.")
        return run_checks(db,user,data,stores,audit)

    @app.post("/api/invoice-workflow/{identifier}/validate")
    def review(identifier:str,data:ReviewInput,user=Depends(current),db:Session=Depends(db_session)):
        if not data.confirmed: raise HTTPException(422,"Confirme que abriu e reviu o conteúdo do anexo.")
        purchase=get_purchase(db,user,identifier,True)
        if data.attachment_key.startswith("drive:"):
            link=db.get(DriveDocumentAttachment,data.attachment_key[6:])
            if not link or link.purchase_id!=purchase.id: raise HTTPException(404,"Anexo indisponível.")
            # Explicit review can approve changed bytes; regular views retain the integrity check.
            with Session(bind=db.get_bind(),expire_on_commit=False) as auth_db: client,config=authorized_client(auth_db)
            with closing(client):
                if config.google_email.lower()!=link.account: raise HTTPException(409,"Ligue a conta Google original do anexo para o rever.")
                info=metadata(client,link.drive_id); raw,mime=content(client,link.drive_id)
            digest=hashlib.sha256(raw).hexdigest(); link.name,link.mime=filename(info["name"]),mime
        else:
            link=db.get(PurchaseAttachment,data.attachment_key)
            if not link or link.purchase_id!=purchase.id: raise HTTPException(404,"Anexo indisponível.")
            digest=hashlib.sha256(link.data).hexdigest()
        if isinstance(link,DriveDocumentAttachment): link.digest=digest
        validate_evidence(db,user,purchase,data.attachment_key,digest,"manual",audit)
        check=db.get(InvoiceWorkflowCheck,purchase.id)
        if check and check.drive_state=="review":
            check.drive_state="checked"
            check.messages=json.dumps(["Anexo atualizado revisto e validado manualmente."])
        audit(db,user,"efatura_anexo_revisao_manual",purchase.id,{"attachment_key":data.attachment_key,"reason":data.reason},purchase.store_id)
        db.commit(); return summarize(db,purchase)

    @app.post("/api/invoice-workflow/{identifier}/attach")
    def attach_candidate(identifier:str,data:ReviewInput,user=Depends(current),db:Session=Depends(db_session)):
        if not data.confirmed: raise HTTPException(422,"Confirme a revisão do ficheiro e da correspondência.")
        purchase=get_purchase(db,user,identifier,True)
        attach_from_drive(db,user,purchase,data.attachment_key,data.reason,audit)
        db.commit(); return summarize(db,purchase)

    @app.get("/api/invoice-workflow/{identifier}/proposal")
    def proposal(identifier:str,user=Depends(current),db:Session=Depends(db_session)):
        purchase=get_purchase(db,user,identifier)
        authorize(user,db,purchase.store_id,["administrador","gestor"])
        if not existing_evidence(db,purchase): raise HTTPException(409,"Anexo em falta ou por validar. Procure ou valide o anexo antes de preparar o lançamento.")
        check=db.get(InvoiceWorkflowCheck,purchase.id)
        if current_erp_link(db,purchase): raise HTTPException(409,"A fatura já está associada ao ERP. Não prepare outro lançamento.")
        if not check or check.erp_state!="not_found": raise HTTPException(409,"Verifique o ERP e resolva as divergências antes de preparar uma proposta.")
        supplier=json.loads(check.supplier)
        stored=json.loads(check.proposal)
        supplier_name=(supplier.get("candidates") or [{}])[0].get("name","") if supplier.get("state")=="exists" else purchase.supplier_name
        if supplier_name in ("Entidade não indicada","Fornecedor não indicado"): supplier_name=""
        fields=stored or {"supplier_name":supplier_name,"supplier_nif":purchase.supplier_nif,"document":purchase.document,"document_type":purchase.document_type,"document_date":purchase.document_date,"amount_cents":purchase.amount_cents,"currency":"EUR","address":"","email":"","notes":"","lines":[]}
        missing=["Artigo/classificação e regra de IVA das linhas por confirmar","Contrato de criação de fornecedores e compras não implementado no conector"]
        if not fields.get("supplier_name","").strip(): missing.append("Nome do fornecedor em falta")
        if not fields.get("supplier_nif","").strip(): missing.append("NIF do fornecedor em falta")
        if supplier.get("state") in ("review","unknown"): missing.append("Fornecedor no ERP por verificar")
        return {"fields":fields,"supplier":supplier,"supplier_action":"reuse" if supplier.get("state")=="exists" else "propose_creation" if supplier.get("state")=="not_found" else "verify_first","missing_fields":missing,"creation_available":False,"message":"Reveja fornecedor e fatura em conjunto. Guardar a proposta não cria entidades ou documentos no ERP."}

    @app.post("/api/invoice-workflow/{identifier}/proposal")
    def save_proposal(identifier:str,data:ProposalInput,user=Depends(current),db:Session=Depends(db_session)):
        purchase=get_purchase(db,user,identifier,True)
        if not data.confirmed: raise HTTPException(422,"Confirme a revisão da proposta.")
        if not existing_evidence(db,purchase) or current_erp_link(db,purchase): raise HTTPException(409,"Confirme o anexo e a situação ERP antes de preparar a proposta.")
        settings=settings_for(db)
        proposed=SimpleNamespace(id=purchase.id,store_id=purchase.store_id,supplier_nif=data.supplier_nif,document_type=data.document_type,document=data.document,document_date=str(data.document_date))
        result=live_erp(db,[purchase,proposed],user,settings.demo_confirmed)
        purchase=db.scalar(select(Purchase).where(Purchase.id==identifier).with_for_update().execution_options(populate_existing=True))
        if current_erp_link(db,purchase) or not existing_evidence(db,purchase): raise HTTPException(409,"A associação ou o anexo mudou durante a revisão. Atualize a fatura.")
        if result["state"]!="checked": raise HTTPException(409,"Não foi possível verificar novamente o ERP. A proposta fica pendente.")
        for item in db.scalars(select(ERPDocument).where(ERPDocument.id.in_(result["documents"]))):
            if invoice_key(item.supplier_nif,item.document_type,item.document)==invoice_key(data.supplier_nif,data.document_type,data.document): raise HTTPException(409,"O ERP já contém um candidato para este documento. Reveja a correspondência; nenhum lançamento foi criado.")
        changed=invoice_key(data.supplier_nif,data.document_type,data.document)!=invoice_key(purchase.supplier_nif,purchase.document_type,purchase.document) or str(data.document_date)!=purchase.document_date or data.amount_cents!=purchase.amount_cents
        if changed and len(data.reason.strip())<3: raise HTTPException(422,"Justifique a alteração dos dados importados.")
        check=db.get(InvoiceWorkflowCheck,purchase.id)
        if not check: raise HTTPException(409,"Verifique a fatura antes de preparar a proposta.")
        check.proposal=json.dumps(data.model_dump(mode="json")); check.proposal_reviewed_at,check.proposal_reviewed_by=now(),user.id
        check.supplier=json.dumps(supplier_status(result,data.supplier_nif))
        audit(db,user,"efatura_proposta_conjunta_revista",purchase.id,{"fields":data.model_dump(mode="json"),"supplier":json.loads(check.supplier),"erp_write":False},purchase.store_id)
        db.commit(); return {"status":"reviewed","creation_available":False,"message":"Proposta revista e guardada; não foi criado um lançamento no ERP."}

    @app.get("/api/invoice-workflow/{identifier}/proposal-export")
    def export_proposal(identifier:str,user=Depends(current),db:Session=Depends(db_session)):
        purchase=get_purchase(db,user,identifier)
        check=db.get(InvoiceWorkflowCheck,purchase.id)
        if not check or not check.proposal_reviewed_at or current_erp_link(db,purchase) or not existing_evidence(db,purchase): raise HTTPException(409,"Reveja a proposta e o anexo antes de exportar.")
        payload={"status":"Proposta revista — não lançada no ERP","supplier":json.loads(check.supplier),"invoice":json.loads(check.proposal),"creation_available":False}
        return Response(json.dumps(payload,ensure_ascii=False,indent=2),media_type="application/json",headers={"Content-Disposition":'attachment; filename="proposta-'+purchase.id+'.json"',"Cache-Control":"no-store"})

    @app.post("/api/invoice-workflow/{identifier}/execute")
    def execute(identifier:str,user=Depends(current),db:Session=Depends(db_session)):
        get_purchase(db,user,identifier,True)
        raise HTTPException(409,"A criação de fornecedores e documentos de compras não está implementada neste conector. Nenhuma escrita foi enviada ao ERP.")

    @app.get("/api/invoice-workflow/{identifier}/history")
    def history(identifier:str,user=Depends(current),db:Session=Depends(db_session)):
        purchase=get_purchase(db,user,identifier)
        return [{"id":item.id,"action":item.action,"detail":json.loads(item.detail),"created_at":item.created_at,"user_name":name or "Sistema"} for item,name in db.execute(select(Audit,User.name).outerjoin(User,User.id==Audit.user_id).where(Audit.target_id==purchase.id).order_by(Audit.created_at.desc()).limit(100))]

def register_conference(app,current,authorize,scope,audit):
    import sys
    from .invoice_conference import register_routes as register
    register(app,current,authorize,scope,audit,sys.modules[__name__])

def scheduled_tick():
    from .main import scope, record
    with database.next_platform_session() as core:
        companies=[{"id":item.id,"name":item.name,"mode":item.mode,"schema":item.schema_name,"nif":item.nif} for item in core.scalars(select(Company).where(Company.active==1))]
    for company in companies:
        with Session(database.engine.execution_options(schema_translate_map={None:company["schema"]}),expire_on_commit=False) as db:
            db.info["company"]=company
            settings=db.get(InvoiceWorkflowSettings,"default")
            if not settings or not settings.interval_minutes or not settings.actor_id: continue
            stamp=now()
            if settings.last_scheduled_at and (stamp-settings.last_scheduled_at).total_seconds()<settings.interval_minutes*60: continue
            actor=db.get(User,settings.actor_id)
            if not actor or not actor.active or actor.role!="administrador": continue
            if company["mode"]=="demonstracao" and not settings.demo_confirmed: continue
            settings.last_scheduled_at=stamp; db.commit()
            try:
                today=stamp.astimezone(ZoneInfo("Europe/Lisbon")).date()
                data=VerifyInput(start=today-timedelta(days=settings.window_days-1),end=today,confirm_demo=settings.demo_confirmed)
                run_checks(db,actor,data,scope(actor,db),record,"scheduled")
            except HTTPException: continue
