"""Storage contract for scans; Google Drive uses the existing company OAuth client."""
from typing import Protocol
from urllib.parse import quote
from fastapi import HTTPException
from sqlalchemy import select
from .google_invoices import DRIVE, api_json, upload
from .models import ScanCloudFolder

class ScanStorage(Protocol):
    def check_folder(self, folder: str): ...
    def folder(self, db, account: str, parent: str, name: str) -> str: ...
    def save(self, db, item, company_id: str, raw: bytes | None): ...

class GoogleDriveStorage:
    def __init__(self, client):
        self.client = client

    def check_folder(self, folder):
        info = api_json(self.client.get(DRIVE + "/files/" + quote(folder, safe=""), params={"fields": "id,mimeType,trashed,capabilities(canAddChildren)", "supportsAllDrives": True}), "Verificar pasta Drive")
        if info.get("trashed") or info.get("mimeType") != "application/vnd.google-apps.folder" or not info.get("capabilities", {}).get("canAddChildren"):
            raise HTTPException(409, "A pasta configurada não permite guardar ficheiros. Reveja Configurações > Anexos e a permissão de escrita no Drive.")
        return info

    def folder(self, db, account, parent, name):
        # Persist a generated ID before creation, including folders, to recover uncertain writes.
        row = db.scalar(select(ScanCloudFolder).where(ScanCloudFolder.account == account, ScanCloudFolder.parent_id == parent, ScanCloudFolder.name == name).with_for_update())
        if not row:
            escaped = name.replace("\\", "\\\\").replace("'", "\\'")
            found = api_json(self.client.get(DRIVE + "/files", params={"q": f"'{parent}' in parents and name='{escaped}' and mimeType='application/vnd.google-apps.folder' and trashed=false", "fields": "files(id)", "pageSize": 100, "supportsAllDrives": True, "includeItemsFromAllDrives": True}), "Consultar subpasta Drive").get("files", [])
            if len(found) > 1:
                raise HTTPException(409, "Existem subpastas com o mesmo nome no Drive. Reveja a organização antes de enviar.")
            identifier = found[0]["id"] if found else api_json(self.client.get(DRIVE + "/files/generateIds", params={"count": 1, "space": "drive"}), "Preparar subpasta Drive")["ids"][0]
            row = ScanCloudFolder(account=account, parent_id=parent, name=name, drive_id=identifier)
            db.add(row); db.commit()
        check = self.client.get(DRIVE + "/files/" + quote(row.drive_id, safe=""), params={"fields": "id,parents,trashed", "supportsAllDrives": True})
        if check.status_code == 404:
            result = self.client.post(DRIVE + "/files", params={"fields": "id", "supportsAllDrives": True}, json={"id": row.drive_id, "name": name, "parents": [parent], "mimeType": "application/vnd.google-apps.folder"})
            if result.status_code != 409:
                api_json(result, "Criar subpasta Drive")
        elif check.status_code >= 400:
            api_json(check, "Verificar subpasta Drive")
        elif check.json().get("trashed") or parent not in check.json().get("parents", []):
            raise HTTPException(409, "A subpasta foi removida ou movida. Reveja a configuração.")
        self.check_folder(row.drive_id)
        return row.drive_id

    def save(self, db, item, company_id, raw):
        if raw is None:
            if not item.drive_id:
                raise HTTPException(410, "O PDF temporário expirou. Volte a gerar o documento.")
            response = self.client.get(DRIVE + "/files/" + quote(item.drive_id, safe=""), params={"fields": "id", "supportsAllDrives": True})
            if response.status_code == 404:
                raise HTTPException(410, "O PDF temporário expirou e o envio não está na cloud. Volte a gerar o documento.")
            api_json(response, "Recuperar envio Drive")
        else:
            upload(self.client, db, item, item.drive_folder, company_id, raw)
        info = api_json(self.client.get(DRIVE + "/files/" + quote(item.drive_id, safe=""), params={"fields": "id,name,size,mimeType,parents,trashed,appProperties,md5Checksum", "supportsAllDrives": True}), "Confirmar digitalização Drive")
        import json
        if info.get("id") != item.drive_id or info.get("trashed") or item.drive_folder not in info.get("parents", []) or info.get("mimeType") != "application/pdf" or int(info.get("size", -1)) != item.size or info.get("appProperties", {}).get("gpgest_hash") != item.digest or info.get("md5Checksum") != json.loads(item.data)["pdf_md5"]:
            raise HTTPException(409, "O serviço ainda não confirmou o PDF completo. O envio fica por verificar; repetir usa o mesmo identificador.")
        return info

PROVIDERS = {"google_drive": GoogleDriveStorage}
