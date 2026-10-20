"""Procura de ficheiros de importação (SAF-T, extratos) na pasta Google Drive configurada.

Só lê: nunca move, apaga ou altera ficheiros no Drive. Cada ficheiro escolhido é
confirmado como pertencente à pasta configurada (ou subpastas) antes de ser lido.
"""

import threading
from contextlib import closing
from time import monotonic
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import current
from .db import db_session
from .drive_documents import belongs_to_tree, folder_tree
from .google_invoices import DRIVE, api_json, authorized_client
from .models import AttachmentIntegrationConfig, DocumentImport, GoogleConnection, SaftImport
from .operating_modes import policy

router = APIRouter()

KINDS = {
    "saft": {
        "extensions": (".xml",),
        "mimes": ("text/xml", "application/xml", "application/octet-stream"),
    },
    # Exportações de faturas de compras do portal e-Fatura (não confundir com SAF-T de vendas).
    "efatura": {
        "extensions": (".csv", ".xlsx"),
        "mimes": (
            "text/csv",
            "application/csv",
            "text/x-csv",
            "text/comma-separated-values",
            "text/x-comma-separated-values",
            "text/plain",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "application/octet-stream",
        ),
    },
    "statement": {
        "extensions": (".xlsx", ".xls"),
        "mimes": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "application/octet-stream",
        ),
    },
}
MAX_LISTED = 100
# O aviso de ficheiros novos é verificado periodicamente por cada utilizador: guardar a
# listagem do Drive por empresa evita repetir os pedidos à Google. O estado «importado»
# é sempre calculado na base de dados, para o aviso desaparecer logo após a importação.
CACHE_SECONDS = 600
CACHE = {}
CACHE_LOCK = threading.Lock()
STATEMENT_SOURCES = ("bpi_xlsx", "viva_wallet_xls")


def drive_configured(db):
    """Indica se há pasta Drive e conta Google ligada, sem contactar a Google."""
    config = db.get(AttachmentIntegrationConfig, "default")
    connection = db.get(GoogleConnection, "default")
    return bool(config and config.drive_folder and connection and connection.token_ciphertext)


def matches(kind, name):
    return (name or "").lower().endswith(KINDS[kind]["extensions"])


def list_files(db, kind, roots=None, subfolders=True):
    """Ficheiros do tipo pedido nas pastas indicadas (por omissão, a pasta configurada),
    incluindo ou não as subpastas, mais recentes primeiro."""
    client, config = authorized_client(db)
    with closing(client):
        mimes = " or ".join(f"mimeType = '{mime}'" for mime in KINDS[kind]["mimes"])
        files = []
        roots = list(roots or [config.drive_folder])
        tree = []
        for root in roots:
            tree.extend(folder_tree(client, root) if subfolders else [root])
        for folder in dict.fromkeys(tree):
            token = ""
            while True:
                listing = api_json(
                    client.get(
                        DRIVE + "/files",
                        params={
                            "q": f"'{folder}' in parents and trashed = false and ({mimes})",
                            "pageSize": MAX_LISTED,
                            "pageToken": token,
                            "orderBy": "modifiedTime desc",
                            "fields": "files(id,name,size,modifiedTime,md5Checksum,version,mimeType),nextPageToken",
                            "supportsAllDrives": True,
                            "includeItemsFromAllDrives": True,
                        },
                    ),
                    "Procurar ficheiros no Drive",
                )
                files.extend(item for item in listing.get("files", []) if matches(kind, item.get("name")))
                token = listing.get("nextPageToken", "")
                if not token or len(files) >= MAX_LISTED * 5:
                    break
    files.sort(key=lambda item: item.get("modifiedTime", ""), reverse=True)
    result = [
        {
            "id": item["id"],
            "name": item["name"],
            "size": int(item.get("size") or 0),
            "modified_at": item.get("modifiedTime", ""),
            # A versão Drive muda a cada alteração do conteúdo; o md5 identifica o conteúdo.
            "version": str(item.get("version") or item.get("md5Checksum") or item.get("modifiedTime", "")),
            "md5": item.get("md5Checksum", ""),
        }
        for item in files[:MAX_LISTED]
    ]
    if roots == [config.drive_folder] and subfolders:
        with CACHE_LOCK:
            CACHE[cache_key(db, kind)] = (monotonic() + CACHE_SECONDS, result)
    return [dict(item) for item in result]


def cache_key(db, kind):
    return (db.info.get("company", {}).get("id"), kind)


def cached_files(db, kind):
    with CACHE_LOCK:
        expires, files = CACHE.get(cache_key(db, kind), (0, []))
    if expires > monotonic():
        return [dict(item) for item in files]
    return list_files(db, kind)


def mark_imported(db, kind, files):
    """Acrescenta `imported` a cada ficheiro: indicação pelo nome (e tamanho, no SAF-T)."""
    names = [item["name"][:200] for item in files]
    if kind == "saft":
        imported = {
            (row.filename, row.size_bytes)
            for row in db.scalars(
                select(SaftImport).where(SaftImport.filename.in_(names), SaftImport.status != "failed")
            )
        }
        for item in files:
            item["imported"] = (item["name"][:200], item["size"]) in imported
    else:
        imported = set(
            db.scalars(
                select(DocumentImport.filename).where(
                    DocumentImport.source.in_(STATEMENT_SOURCES), DocumentImport.filename.in_(names)
                )
            )
        )
        for item in files:
            item["imported"] = item["name"][:200] in imported
    return files


@router.get("/api/drive/status")
def drive_status(user=Depends(current), db: Session = Depends(db_session)):
    """Resposta imediata para as páginas avisarem que vão procurar ficheiros no Drive."""
    return {"configured": drive_configured(db)}


@router.get("/api/drive/pending-imports")
def pending_imports(user=Depends(current), db: Session = Depends(db_session)):
    """Número de ficheiros no Drive ainda por importar, para o aviso da aplicação."""
    kinds = []
    if user.role in {"administrador", "gestor"}:
        kinds.append("statement")
    if user.role == "administrador" and policy(db)["mode"] == "local":
        kinds.append("saft")
    if not kinds or not drive_configured(db):
        return {"configured": False, "pending": {}}
    pending = {}
    try:
        for kind in kinds:
            files = mark_imported(db, kind, cached_files(db, kind))
            pending[kind] = sum(not item["imported"] for item in files)
    except HTTPException as exc:
        # O aviso nunca bloqueia a aplicação: a falha aparece nas páginas de importação.
        return {"configured": True, "pending": pending, "error": exc.detail}
    return {"configured": True, "pending": pending}


def download(db, kind, drive_id, limit, write, roots=None, subfolders=True, meta=None):
    """Valida o ficheiro escolhido e envia o conteúdo, em blocos, para `write`.

    Devolve (nome, tamanho) e, se indicado, preenche `meta` com os dados Drive (ID, versão).
    Recusa ficheiros fora das pastas autorizadas ou acima de `limit`.
    """
    client, config = authorized_client(db)
    with closing(client):
        info = api_json(
            client.get(
                DRIVE + "/files/" + quote(drive_id, safe=""),
                params={"fields": "id,name,mimeType,size,parents,trashed,version,md5Checksum,modifiedTime", "supportsAllDrives": True},
            ),
            "Consultar ficheiro Drive",
        )
        if info.get("trashed") or not matches(kind, info.get("name")):
            raise HTTPException(422, "Selecione um ficheiro do tipo indicado disponível no Drive.")
        default = not roots
        roots = list(roots or [config.drive_folder])
        inside = (
            any(belongs_to_tree(client, root, info) for root in roots)
            if subfolders
            else bool(set(info.get("parents") or []) & set(roots))
        )
        if not inside:
            raise HTTPException(422, "O ficheiro não pertence à pasta configurada." if default else "O ficheiro não pertence às pastas autorizadas.")
        if meta is not None:
            meta.update(
                id=info["id"],
                version=str(info.get("version") or info.get("md5Checksum") or info.get("modifiedTime", "")),
                modified_at=info.get("modifiedTime", ""),
            )
        if int(info.get("size") or 0) > limit:
            raise HTTPException(413, "O ficheiro excede o limite permitido para esta importação.")
        total = stream_content(client, drive_id, limit, write)
    return info["name"], total


def stream_content(client, drive_id, limit, write):
    """Envia o conteúdo do ficheiro Drive, em blocos, para `write`; devolve o tamanho lido."""
    total = 0
    with client.stream(
        "GET",
        DRIVE + "/files/" + quote(drive_id, safe=""),
        params={"alt": "media", "supportsAllDrives": True},
    ) as response:
        if response.status_code >= 400:
            response.read()
            api_json(response, "Ler ficheiro Drive")
        for chunk in response.iter_bytes(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, "O ficheiro excede o limite permitido para esta importação.")
            write(chunk)
    return total


def read_files(db, ids, limit):
    """Lê ficheiros já obtidos por `list_files` (portanto dentro das pastas autorizadas), com um
    único cliente Google. Devolve (id, conteúdo ou None, erro) por ficheiro, sem interromper os restantes."""
    client, _ = authorized_client(db)
    results = []
    with closing(client):
        for drive_id in ids:
            chunks = []
            try:
                stream_content(client, drive_id, limit, chunks.append)
                results.append((drive_id, b"".join(chunks), ""))
            except HTTPException as exc:
                results.append((drive_id, None, str(exc.detail)))
    return results
