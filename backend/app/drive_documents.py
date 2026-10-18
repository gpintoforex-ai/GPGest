"""Drive-backed invoice evidence: only references and audit metadata are persisted."""

import hashlib
import json
from contextlib import closing
from types import SimpleNamespace
from urllib.parse import quote

import httpx
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import db_session
from .google_invoices import (
    DRIVE,
    MAX_BYTES,
    api_json,
    authorized_client,
    filename,
    pdf_text,
    score,
    validate_bytes,
)
from .models import DriveDocumentAttachment, Purchase, PurchaseAttachment
from .schemas import Strict


class DriveChoice(Strict):
    drive_id: str = Field(min_length=1, max_length=200)
    confirmed: bool


class SupplierChoice(Strict):
    name: str = Field(min_length=2, max_length=300)
    nif: str = Field(min_length=1, max_length=40)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=60)
    confirmed: bool


class AutoMatchRequest(Strict):
    ids: list[str] = Field(default_factory=list, max_length=200)
    page_token: str = Field(default="", max_length=2000)


class MatchPair(Strict):
    purchase_id: str = Field(max_length=36)
    drive_id: str = Field(max_length=200)


class ConfirmMatches(Strict):
    pairs: list[MatchPair] = Field(min_length=1, max_length=20)
    confirmed: bool

def folder_tree(client, root):
    folders, seen = [root], {root}
    for parent in folders:
        token = ""
        while True:
            listing = api_json(client.get(DRIVE+"/files",params={"q":f"'{parent}' in parents and trashed=false and mimeType='application/vnd.google-apps.folder'","pageSize":1000,"pageToken":token,"fields":"files(id,mimeType),nextPageToken","supportsAllDrives":True,"includeItemsFromAllDrives":True}),"Consultar subpastas Drive")
            for item in sorted(listing.get("files",[]),key=lambda item:item["id"]):
                if item.get("mimeType") == "application/vnd.google-apps.folder" and item["id"] not in seen:
                    seen.add(item["id"]); folders.append(item["id"])
                    if len(folders)>1000: raise HTTPException(422,"A pasta tem mais de 1000 subpastas. Configure uma pasta mais específica.")
            token=listing.get("nextPageToken","")
            if not token: break
    return folders

def recursive_pdfs(client, root, token=""):
    from .platform import cipher
    folders=folder_tree(client,root)
    signature=hashlib.sha256(json.dumps(folders).encode()).hexdigest()
    index,remote=0,""
    if token:
        try:
            state=json.loads(cipher().decrypt(token.encode()))
            if state["root"]!=root or state["tree"]!=signature: raise ValueError()
            index,remote=state["index"],state["remote"]
            if not isinstance(index,int) or not 0<=index<len(folders): raise ValueError()
        except Exception: raise HTTPException(409,"A pasta ou a pesquisa mudou. Inicie novamente a procura de correspondências.") from None
    files=[]
    while index<len(folders) and len(files)<50:
        listing=api_json(client.get(DRIVE+"/files",params={"q":f"'{folders[index]}' in parents and trashed = false and mimeType = 'application/pdf'","pageSize":50-len(files),"pageToken":remote,"fields":"files(id,name,mimeType,size),nextPageToken","orderBy":"modifiedTime desc","supportsAllDrives":True,"includeItemsFromAllDrives":True}),"Cruzar documentos com PDFs Drive")
        files.extend(listing.get("files",[])); remote=listing.get("nextPageToken","")
        if not remote: index+=1
    continuation=cipher().encrypt(json.dumps({"root":root,"tree":signature,"index":index,"remote":remote}).encode()).decode() if index<len(folders) else ""
    return {"files":files,"nextPageToken":continuation}

def belongs_to_tree(client, root, info):
    pending=list(info.get("parents",[])); seen=set()
    for parent in pending:
        if parent==root: return True
        if parent in seen: continue
        seen.add(parent)
        if len(seen)>100: raise HTTPException(422,"A hierarquia da pasta é demasiado profunda.")
        response=client.get(DRIVE+"/files/"+quote(parent,safe=""),params={"fields":"id,mimeType,parents,trashed","supportsAllDrives":True})
        if response.status_code==404: continue
        folder=api_json(response,"Verificar pasta de origem Drive")
        if not folder.get("trashed") and folder.get("mimeType")=="application/vnd.google-apps.folder": pending.extend(folder.get("parents",[]))
    return False


def metadata(client, drive_id):
    info = api_json(
        client.get(
            DRIVE + "/files/" + quote(drive_id, safe=""),
            params={"fields": "id,name,mimeType,size,parents,trashed", "supportsAllDrives": True},
        ),
        "Consultar ficheiro Drive",
    )
    if info.get("trashed") or info.get("mimeType") not in {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }:
        raise HTTPException(422, "Selecione um PDF, JPG ou PNG disponível no Drive.")
    if int(info.get("size", 0)) > MAX_BYTES:
        raise HTTPException(422, "O anexo excede 5 MB.")
    return info


def content(client, drive_id):
    chunks, total = [], 0
    with client.stream(
        "GET", DRIVE + "/files/" + quote(drive_id, safe=""), params={"alt": "media", "supportsAllDrives": True}
    ) as response:
        if response.status_code >= 400:
            response.read()
            api_json(response, "Ler anexo Drive")
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > MAX_BYTES:
                raise HTTPException(422, "O anexo excede 5 MB.")
            chunks.append(chunk)
    raw = b"".join(chunks)
    return raw, validate_bytes(raw)


def read_link(db, link):
    # Token renewal must not commit the caller's document/export transaction.
    with Session(bind=db.get_bind(), expire_on_commit=False) as auth_db:
        client, config = authorized_client(auth_db)
    if config.google_email.lower() != link.account:
        client.close()
        raise HTTPException(409, "Ligue a conta Google original deste anexo para o consultar.")
    try:
        with closing(client):
            metadata(client, link.drive_id)
            raw, mime = content(client, link.drive_id)
            if len(getattr(link, "digest", "")) == 64 and hashlib.sha256(raw).hexdigest() != link.digest:
                raise HTTPException(409, "O anexo no Drive foi alterado. Reveja a associação antes de o utilizar.")
            return raw, mime
    except httpx.HTTPError:
        raise HTTPException(
            502, "Não foi possível ler o ficheiro no Drive. Tente novamente."
        ) from None


def attach_reference(db, purchase, account, drive_id, folder, name, mime, digest, user, record):
    db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtext(db.info["company"]["id"] + ":drive:" + drive_id)
            )
        )
    )
    existing = db.scalar(
        select(DriveDocumentAttachment).where(
            DriveDocumentAttachment.account == account, DriveDocumentAttachment.drive_id == drive_id
        )
    )
    if existing:
        if existing.purchase_id != purchase.id:
            raise HTTPException(409, "Este ficheiro Drive já está associado a outra fatura.")
        return existing
    count = db.scalar(
        select(func.count())
        .select_from(DriveDocumentAttachment)
        .where(DriveDocumentAttachment.purchase_id == purchase.id)
    )
    count += db.scalar(
        select(func.count())
        .select_from(PurchaseAttachment)
        .where(PurchaseAttachment.purchase_id == purchase.id)
    )
    if count >= 5:
        raise HTTPException(422, "A fatura já tem cinco anexos.")
    link = DriveDocumentAttachment(
        purchase_id=purchase.id,
        account=account,
        drive_id=drive_id,
        drive_folder=folder,
        name=filename(name),
        mime=mime,
        digest=digest,
    )
    db.add(link)
    db.flush()
    record(
        db,
        user,
        "documento_compra_anexado",
        purchase.id,
        {"attachment_id": "drive:" + link.id, "origin": "drive_reference", "drive_id": drive_id},
        purchase.store_id,
    )
    return link


def register_routes(app, current, authorize, record):
    def doc(db, user, key):
        purchase = db.get(Purchase, key)
        if not purchase:
            raise HTTPException(404, "Documento não encontrado.")
        authorize(user, db, purchase.store_id, ["administrador", "gestor"])
        return purchase

    @app.post("/api/documents/assistant/drive-match")
    def auto_match(
        data: AutoMatchRequest, user=Depends(current), db: Session = Depends(db_session)
    ):
        purchases = [doc(db, user, key) for key in dict.fromkeys(data.ids)]
        client, config = authorized_client(db)
        results = []
        with closing(client):
            listing = recursive_pdfs(client, config.drive_folder, data.page_token)
            for f in listing.get("files", []):
                if int(f.get("size", 0)) > MAX_BYTES:
                    continue
                linked = db.scalar(
                    select(DriveDocumentAttachment).where(
                        DriveDocumentAttachment.account == config.google_email.lower(),
                        DriveDocumentAttachment.drive_id == f["id"],
                    )
                )
                if linked:
                    results.append(
                        {
                            "drive_id": f["id"],
                            "name": f["name"],
                            "status": "associado",
                            "purchase_id": linked.purchase_id,
                        }
                    )
                    continue
                try:
                    raw, mime = content(client, f["id"])
                    evidence = SimpleNamespace(
                        name=f["name"], subject="", extracted_text=pdf_text(raw, mime)
                    )
                    ranked = []
                    for purchase in purchases:
                        confidence, reasons = score(evidence, purchase)
                        if confidence:
                            ranked.append(
                                {
                                    "purchase_id": purchase.id,
                                    "document": purchase.document,
                                    "supplier": purchase.supplier_name,
                                    "confidence": confidence,
                                    "reasons": reasons,
                                }
                            )
                    ranked.sort(key=lambda r: -r["confidence"])
                    best = ranked[0] if ranked else None
                    confident = bool(
                        best
                        and best["confidence"] >= 70
                        and "Número do documento encontrado" in best["reasons"]
                        and (len(ranked) < 2 or best["confidence"] - ranked[1]["confidence"] >= 20)
                    )
                    results.append(
                        {
                            "drive_id": f["id"],
                            "name": f["name"],
                            "status": "sugerido" if confident else "por_rever",
                            "match": best if confident else None,
                            "candidates": ranked[:3],
                        }
                    )
                except (HTTPException, httpx.HTTPError) as exc:
                    results.append(
                        {
                            "drive_id": f["id"],
                            "name": f["name"],
                            "status": "falhou",
                            "error": exc.detail
                            if isinstance(exc, HTTPException)
                            else "Não foi possível ler o PDF.",
                        }
                    )
        return {"results": results, "next_page": listing.get("nextPageToken", "")}

    @app.post("/api/documents/assistant/drive-match-confirm")
    def confirm_matches(
        data: ConfirmMatches, user=Depends(current), db: Session = Depends(db_session)
    ):
        if not data.confirmed:
            raise HTTPException(422, "Confirme as associações propostas.")
        results = []
        for pair in data.pairs:
            try:
                purchase = doc(db, user, pair.purchase_id)
                client, config = authorized_client(db)
                with closing(client):
                    info = metadata(client, pair.drive_id)
                    if not belongs_to_tree(client, config.drive_folder, info):
                        raise HTTPException(422, "O ficheiro não pertence à pasta configurada.")
                    raw, mime = content(client, pair.drive_id)
                    confidence, reasons = score(
                        SimpleNamespace(
                            name=info["name"], subject="", extracted_text=pdf_text(raw, mime)
                        ),
                        purchase,
                    )
                    if confidence < 70 or "Número do documento encontrado" not in reasons:
                        raise HTTPException(
                            409,
                            "A correspondência não é suficientemente segura. Reveja o PDF individualmente.",
                        )
                purchase = db.scalar(
                    select(Purchase).where(Purchase.id == pair.purchase_id).with_for_update()
                )
                link = attach_reference(
                    db,
                    purchase,
                    config.google_email.lower(),
                    pair.drive_id,
                    config.drive_folder,
                    info["name"],
                    mime,
                    hashlib.sha256(raw).hexdigest(),
                    user,
                    record,
                )
                db.commit()
                results.append(
                    {
                        "drive_id": pair.drive_id,
                        "status": "associado",
                        "attachment_id": "drive:" + link.id,
                    }
                )
            except (HTTPException, httpx.HTTPError) as exc:
                db.rollback()
                results.append(
                    {
                        "drive_id": pair.drive_id,
                        "status": "falhou",
                        "error": exc.detail
                        if isinstance(exc, HTTPException)
                        else "Falha de comunicação Drive.",
                    }
                )
        return {"results": results}

    @app.get("/api/documents/{key}/assistant/drive")
    def find_files(
        key: str, page_token: str = "", user=Depends(current), db: Session = Depends(db_session)
    ):
        purchase = doc(db, user, key)
        client, config = authorized_client(db)
        try:
            with closing(client):
                listing = api_json(
                    client.get(
                        DRIVE + "/files",
                        params={
                            "q": "'"
                            + config.drive_folder
                            + "' in parents and trashed = false and (mimeType = 'application/pdf' or mimeType = 'image/png' or mimeType = 'image/jpeg')",
                            "pageSize": 50,
                            "pageToken": page_token,
                            "fields": "files(id,name,mimeType,size),nextPageToken",
                            "orderBy": "modifiedTime desc",
                        },
                    ),
                    "Procurar anexos no Drive",
                )
                files = []
                for f in listing.get("files", []):
                    if int(f.get("size", 0)) > MAX_BYTES:
                        continue
                    confidence, reasons = score(
                        SimpleNamespace(name=f["name"], subject="", extracted_text=""), purchase
                    )
                    files.append(
                        {
                            "id": f["id"],
                            "name": f["name"],
                            "confidence": confidence,
                            "reasons": reasons,
                            "url": "https://drive.google.com/file/d/" + f["id"] + "/view",
                        }
                    )
                return {
                    "files": sorted(files, key=lambda f: -f["confidence"]),
                    "next_page": listing.get("nextPageToken", ""),
                }
        except httpx.HTTPError:
            raise HTTPException(502, "Não foi possível consultar o Drive.") from None

    @app.post("/api/documents/{key}/assistant/drive-analyze")
    def analyze(
        key: str, data: DriveChoice, user=Depends(current), db: Session = Depends(db_session)
    ):
        purchase = doc(db, user, key)
        client, config = authorized_client(db)
        with closing(client):
            info = metadata(client, data.drive_id)
            if not belongs_to_tree(client, config.drive_folder, info):
                raise HTTPException(422, "O ficheiro não pertence à pasta configurada.")
            raw, mime = content(client, data.drive_id)
            confidence, reasons = score(
                SimpleNamespace(name=info["name"], subject="", extracted_text=pdf_text(raw, mime)),
                purchase,
            )
            return {
                "confidence": confidence,
                "reasons": reasons,
                "name": info["name"],
                "ocr": False,
            }

    @app.post("/api/documents/{key}/assistant/drive-attach")
    def attach(
        key: str, data: DriveChoice, user=Depends(current), db: Session = Depends(db_session)
    ):
        purchase = doc(db, user, key)
        if not data.confirmed:
            raise HTTPException(422, "Confirme que o anexo pertence ao documento.")
        client, config = authorized_client(db)
        with closing(client):
            info = metadata(client, data.drive_id)
            if not belongs_to_tree(client, config.drive_folder, info):
                raise HTTPException(422, "O ficheiro não pertence à pasta configurada.")
            raw, mime = content(client, data.drive_id)
        # Bytes are used transiently for validation; never written to a database column.
        purchase = db.scalar(select(Purchase).where(Purchase.id == key).with_for_update())
        link = attach_reference(
            db,
            purchase,
            config.google_email.lower(),
            data.drive_id,
            config.drive_folder,
            info["name"],
            mime,
            hashlib.sha256(raw).hexdigest(),
            user,
            record,
        )
        db.commit()
        return {"ok": True, "attachment_id": "drive:" + link.id}

    @app.post("/api/documents/{key}/assistant/supplier")
    def ensure_supplier(
        key: str, data: SupplierChoice, user=Depends(current), db: Session = Depends(db_session)
    ):
        purchase = doc(db, user, key)
        if not data.confirmed or data.nif.strip() != purchase.supplier_nif.strip():
            raise HTTPException(422, "Confirme o fornecedor e o NIF do documento.")
        from .entities import EntityEdit, create_entity, edit_entity, list_entities

        db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtext(db.info["company"]["id"] + ":supplier:" + data.nif.strip())
                )
            )
        )
        entities = [
            e
            for e in list_entities(user=user, db=db)
            if (e["tax_number"] or "").strip() == data.nif.strip()
        ]
        existing = next((e for e in entities if e["is_supplier"] and e["active"]), None)
        if existing:
            return {"created": False, "entity": existing}
        existing = entities[0] if entities else None
        payload = EntityEdit(
            category_id=existing.get("category_id") if existing else None,
            name=existing["name"] if existing else data.name,
            tax_number=data.nif,
            email=(existing["email"] or "") if existing else data.email,
            phone=(existing["phone"] or "") if existing else data.phone,
            is_customer=bool(existing and existing["is_customer"]),
            is_supplier=True,
            active=True,
        )
        result = (
            edit_entity(existing["id"], payload, user=user, db=db)
            if existing
            else create_entity(payload, user=user, db=db)
        )
        return {"created": not bool(existing), "entity": result}

    @app.get("/api/drive-attachments/{key}/view")
    def preview(key: str, user=Depends(current), db: Session = Depends(db_session)):
        link = db.get(DriveDocumentAttachment, key)
        if not link:
            raise HTTPException(404, "Anexo não encontrado.")
        purchase = db.get(Purchase, link.purchase_id)
        authorize(user, db, purchase.store_id)
        raw, mime = read_link(db, link)
        return Response(
            raw,
            media_type=mime,
            headers={"Content-Security-Policy": "sandbox", "Content-Disposition": "inline"},
        )

    @app.post("/api/drive-attachments/{key}/remove")
    def remove(key: str, user=Depends(current), db: Session = Depends(db_session)):
        link = db.get(DriveDocumentAttachment, key)
        if not link:
            raise HTTPException(404, "Anexo não encontrado.")
        purchase = doc(db, user, link.purchase_id)
        record(
            db,
            user,
            "documento_compra_anexo_removido",
            purchase.id,
            {
                "attachment_id": "drive:" + link.id,
                "drive_id": link.drive_id,
                "drive_file_deleted": False,
            },
            purchase.store_id,
        )
        db.delete(link)
        db.commit()
        return {"ok": True}
