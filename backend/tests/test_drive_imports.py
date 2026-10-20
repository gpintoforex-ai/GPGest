import re
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
from app.models import BankAccount, BankTransaction, Register
from saft_fixtures import saft
from sqlalchemy import func, select
from test_saft_sales import saft_case  # noqa: F401
from test_viva_import import sample

FOLDER = "application/vnd.google-apps.folder"


class FakeDrive:
    """Drive simulado: root/sub dentro da pasta configurada, outside fora dela."""

    def __init__(self):
        self.folders = {"root": [], "sub": ["root"], "outside": []}
        self.files = {
            "saft-jan": ("SAF-T jan.xml", "text/xml", "root", saft()),
            "saft-sub": ("SAF-T fev.xml", "application/octet-stream", "sub", saft()),
            "notes": ("notas.txt", "application/octet-stream", "root", b"x"),
            "pdf": ("fatura.pdf", "application/pdf", "root", b"%PDF"),
            "viva": ("ExtratoWallet_202601.xls", "application/vnd.ms-excel", "sub", sample()),
            "alheio": ("alheio.xml", "text/xml", "outside", saft()),
        }

    def get(self, url, params):
        assert params["supportsAllDrives"] is True
        if url.endswith("/files"):
            parent = re.search(r"'([^']+)' in parents", params["q"])[1]
            if FOLDER in params["q"]:
                items = [
                    {"id": key, "mimeType": FOLDER}
                    for key, parents in self.folders.items()
                    if parent in parents
                ]
            else:
                items = [
                    {"id": key, "name": name, "size": str(len(raw)), "modifiedTime": f"2026-01-0{i}T10:00:00Z"}
                    for i, (key, (name, mime, folder, raw)) in enumerate(self.files.items(), 1)
                    if folder == parent and f"mimeType = '{mime}'" in params["q"]
                ]
            return httpx.Response(200, json={"files": items, "nextPageToken": ""})
        key = url.rsplit("/", 1)[-1]
        if key in self.folders:
            return httpx.Response(200, json={"id": key, "mimeType": FOLDER, "parents": self.folders[key]})
        name, mime, folder, raw = self.files[key]
        return httpx.Response(
            200,
            json={"id": key, "name": name, "mimeType": mime, "size": str(len(raw)), "parents": [folder], "trashed": False},
        )

    @contextmanager
    def stream(self, method, url, params):
        assert params["alt"] == "media"
        raw = self.files[url.rsplit("/", 1)[-1]][3]
        yield SimpleNamespace(
            status_code=200,
            read=lambda: raw,
            iter_bytes=lambda size: (raw[i : i + size] for i in range(0, len(raw), size)),
        )

    def close(self):
        pass


def connect(monkeypatch, configured=True):
    drive = FakeDrive()
    config = SimpleNamespace(drive_folder="root")
    monkeypatch.setattr("app.drive_imports.authorized_client", lambda db: (drive, config))
    monkeypatch.setattr("app.saft_sales.drive_configured", lambda db: configured)
    monkeypatch.setattr("app.banks.drive_configured", lambda db: configured)
    monkeypatch.setattr("app.drive_imports.drive_configured", lambda db: configured)
    monkeypatch.setattr("app.drive_imports.policy", lambda db: {"mode": "local"})
    monkeypatch.setattr("app.drive_imports.CACHE", {})
    return drive


def test_saft_files_are_found_in_subfolders_and_imported_from_drive(saft_case, monkeypatch):  # noqa: F811
    client, _, _, store = saft_case
    connect(monkeypatch)
    assert client.get("/api/drive/status").json() == {"configured": True}
    listing = client.get("/api/saft/drive-files")
    assert listing.status_code == 200, listing.text
    files = listing.json()["files"]
    assert listing.json()["configured"] is True
    # Só XML da pasta configurada e subpastas, mais recentes primeiro; nunca fora da pasta.
    assert [item["name"] for item in files] == ["SAF-T fev.xml", "SAF-T jan.xml"]
    assert not any(item["imported"] for item in files)

    response = client.post("/api/saft/imports/drive", json={"store_id": store, "drive_id": "saft-jan"})
    assert response.status_code == 200, response.text
    row = client.get("/api/saft/imports/" + response.json()["id"]).json()
    assert row["filename"] == "SAF-T jan.xml" and row["status"] == "ready"

    again = client.post("/api/saft/imports/drive", json={"store_id": store, "drive_id": "saft-jan"})
    assert again.json()["id"] == row["id"]  # Mesmo conteúdo: reutiliza a importação existente.
    imported = {item["name"]: item["imported"] for item in client.get("/api/saft/drive-files").json()["files"]}
    assert imported == {"SAF-T fev.xml": False, "SAF-T jan.xml": True}

    outside = client.post("/api/saft/imports/drive", json={"store_id": store, "drive_id": "alheio"})
    assert outside.status_code == 422 and "pasta configurada" in outside.json()["detail"]
    wrong_kind = client.post("/api/saft/imports/drive", json={"store_id": store, "drive_id": "viva"})
    assert wrong_kind.status_code == 422


def test_without_drive_configuration_nothing_is_requested_from_google(saft_case, monkeypatch):  # noqa: F811
    client = saft_case[0]
    connect(monkeypatch, configured=False)
    monkeypatch.setattr("app.drive_imports.authorized_client", lambda db: (_ for _ in ()).throw(AssertionError()))
    assert client.get("/api/saft/drive-files").json() == {"configured": False, "files": []}
    assert client.get("/api/drive/status").json() == {"configured": False}


def test_bank_statement_preview_and_confirm_from_drive(setup, monkeypatch):
    client, ids, factory = setup
    connect(monkeypatch)
    with factory.begin() as db:
        account = BankAccount(
            store_id=db.get(Register, ids["registers"][0]).store_id,
            name="Viva Wallet", bank="Viva Wallet", iban_last4="1234",
        )
        db.add(account)
        db.flush()
        account_id = account.id
    files = client.get(f"/api/accounts/{account_id}/drive-statements").json()["files"]
    assert [(item["name"], item["imported"]) for item in files] == [("ExtratoWallet_202601.xls", False)]

    path = f"/api/accounts/{account_id}/import-statement/drive"
    preview = client.post(path + "?confirm=false", json={"drive_id": "viva"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["format"] == "Viva Wallet" and preview.json()["new_count"] == 3
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(BankTransaction)) == 0
    assert client.post(path + "?confirm=true", json={"drive_id": "viva"}).json()["new_count"] == 3
    files = client.get(f"/api/accounts/{account_id}/drive-statements").json()["files"]
    assert files[0]["imported"] is True
    assert client.post(path, json={"drive_id": "saft-jan"}).status_code == 422


def test_pending_notice_counts_new_files_uses_cache_and_updates_after_import(saft_case, monkeypatch):  # noqa: F811
    client, _, _, store = saft_case
    drive = connect(monkeypatch)
    calls = []
    original = drive.get
    monkeypatch.setattr(drive, "get", lambda url, params: calls.append(url) or original(url, params))

    first = client.get("/api/drive/pending-imports").json()
    assert first == {"configured": True, "pending": {"statement": 1, "saft": 2}}
    requests = len(calls)
    assert requests
    # Verificações seguintes usam a listagem guardada: nenhum pedido novo à Google.
    assert client.get("/api/drive/pending-imports").json() == first
    assert len(calls) == requests

    client.post("/api/saft/imports/drive", json={"store_id": store, "drive_id": "saft-jan"})
    # O estado «importado» vem da base de dados: o aviso atualiza logo, sem esperar pela cache.
    assert client.get("/api/drive/pending-imports").json()["pending"]["saft"] == 1


def test_pending_notice_never_blocks_and_respects_roles(setup, monkeypatch):
    from fastapi import HTTPException

    client = setup[0]
    connect(monkeypatch)

    def expired(db):
        raise HTTPException(409, "Volte a ligar a conta Google.")

    monkeypatch.setattr("app.drive_imports.authorized_client", expired)
    failed = client.get("/api/drive/pending-imports")
    assert failed.status_code == 200
    assert failed.json()["error"] == "Volte a ligar a conta Google."

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}
    ).status_code == 200
    assert client.get("/api/drive/pending-imports").json() == {"configured": False, "pending": {}}
