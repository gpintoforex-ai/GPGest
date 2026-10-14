import re
import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from app.drive_documents import recursive_pdfs, belongs_to_tree

class DriveTree:
    def __init__(self):
        self.folders={"root":[],"year":["root"],"month":["year"],"outside":[]}
    def get(self,url,params):
        assert params["supportsAllDrives"] is True
        if url.endswith("/files"):
            parent=re.search(r"'([^']+)' in parents",params["q"])[1]
            if "application/vnd.google-apps.folder" in params["q"]:
                items=[{"id":key,"mimeType":"application/vnd.google-apps.folder"} for key,parents in self.folders.items() if parent in parents]
            else:
                items=[{"id":parent+str(i),"name":str(i)+".pdf","mimeType":"application/pdf","size":10} for i in range(51 if parent=="month" else 1)]
            start=int(params.get("pageToken") or 0); size=params["pageSize"]
            return httpx.Response(200,json={"files":items[start:start+size],"nextPageToken":str(start+size) if start+size<len(items) else ""})
        key=url.rsplit("/",1)[-1]
        return httpx.Response(200,json={"id":key,"mimeType":"application/vnd.google-apps.folder","parents":self.folders[key]})

def test_nested_pdf_pagination_and_private_scope(monkeypatch):
    key=Fernet(Fernet.generate_key()); monkeypatch.setattr("app.platform.cipher",lambda:key)
    drive=DriveTree()
    first=recursive_pdfs(drive,"root")
    assert len(first["files"])==50 and any(item["id"].startswith("month") for item in first["files"])
    second=recursive_pdfs(drive,"root",first["nextPageToken"])
    assert len(second["files"])==3 and not second["nextPageToken"]
    assert len({item["id"] for item in first["files"]+second["files"]})==53
    assert belongs_to_tree(drive,"root",{"parents":["month"]})
    assert not belongs_to_tree(drive,"root",{"parents":["outside"]})
    with pytest.raises(HTTPException): recursive_pdfs(drive,"root","forged-token")
    with pytest.raises(HTTPException): recursive_pdfs(drive,"outside",first["nextPageToken"])
