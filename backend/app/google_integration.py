import hashlib
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

import httpx
from cryptography.fernet import InvalidToken
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session, engine, platform_db
from .models import AttachmentIntegrationConfig, GoogleConnection, User
from .platform import cipher
from .platform_models import Company

CALLBACK = "http://localhost:5174/api/integrations/attachments/google/callback"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/userinfo.email"


def credentials():
    try:
        data = json.loads(
            (Path(__file__).resolve().parents[2] / ".local" / "google.json").read_text(
                encoding="utf-8-sig"
            )
        )["web"]
        if (
            not data.get("client_id")
            or not data.get("client_secret")
            or CALLBACK not in data.get("redirect_uris", [])
        ):
            raise ValueError()
        return data
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, "Credenciais Google ausentes ou incorretas no servidor.") from None


def register_routes(app, current, authorize, record):
    @app.get("/api/integrations/attachments/google/status")
    def status(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        connection = db.get(GoogleConnection, "default")
        try:
            credentials()
            configured = True
        except HTTPException:
            configured = False
        return {
            "configured": configured,
            "connected": bool(connection and connection.token_ciphertext),
            "email": connection.email if connection else "",
            "sync_available": True,
        }

    @app.post("/api/integrations/attachments/google/connect")
    def connect(
        request: Request,
        response: Response,
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        authorize(user, db, roles=["administrador", "gestor"])
        browser_origin = request.headers.get("origin")
        if browser_origin != "http://localhost:5174" and not (
            browser_origin is None and request.url.hostname == "localhost"
        ):
            raise HTTPException(
                422, "Abra http://localhost:5174 e entre na empresa antes de ligar a conta Google."
            )
        config = db.get(AttachmentIntegrationConfig, "default")
        if not config or not config.google_email or not config.drive_folder:
            raise HTTPException(422, "Guarde a conta Google e a pasta de destino primeiro.")
        creds = credentials()
        nonce = secrets.token_urlsafe(32)
        state = (
            cipher()
            .encrypt(
                json.dumps(
                    {"company": db.info["company"]["id"], "user": user.id, "nonce": nonce}
                ).encode()
            )
            .decode()
        )
        connection = db.get(GoogleConnection, "default")
        if not connection:
            connection = GoogleConnection(id="default")
            db.add(connection)
        connection.state_hash = hashlib.sha256(state.encode()).hexdigest()
        db.commit()
        response.set_cookie(
            "gpgest_google_nonce",
            nonce,
            httponly=True,
            secure=os.getenv("COOKIE_SECURE") == "true",
            samesite="lax",
            max_age=600,
            path="/api/integrations/attachments/google",
        )
        return {
            "url": "https://accounts.google.com/o/oauth2/v2/auth?"
            + urlencode(
                {
                    "client_id": creds["client_id"],
                    "redirect_uri": CALLBACK,
                    "response_type": "code",
                    "scope": SCOPES,
                    "access_type": "offline",
                    "prompt": "consent",
                    "state": state,
                    "login_hint": config.google_email,
                }
            )
        }

    @app.get("/api/integrations/attachments/google/callback")
    def callback(
        request: Request,
        state: str = "",
        code: str = "",
        error: str = "",
        core: Session = Depends(platform_db),
    ):
        try:
            payload = json.loads(cipher().decrypt(state.encode(), ttl=600))
        except (InvalidToken, ValueError, KeyError):
            raise HTTPException(
                400, "Autorização expirada ou inválida. Volte a ligar a conta na IAZELA."
            ) from None
        if not secrets.compare_digest(
            request.cookies.get("gpgest_google_nonce", ""), payload.get("nonce", "")
        ):
            raise HTTPException(400, "Conclua a ligacao no mesmo navegador onde iniciou.")
        company = core.get(Company, payload.get("company"))
        if not company or company.removed_at or not company.active:
            raise HTTPException(400, "Empresa indisponível.")
        with Session(
            engine.execution_options(schema_translate_map={None: company.schema_name})
        ) as db:
            connection = db.scalar(
                select(GoogleConnection).where(GoogleConnection.id == "default").with_for_update()
            )
            user = db.get(User, payload.get("user"))
            if (
                not connection
                or not secrets.compare_digest(
                    connection.state_hash, hashlib.sha256(state.encode()).hexdigest()
                )
                or not user
                or not user.active
                or user.role not in {"administrador", "gestor"}
            ):
                raise HTTPException(400, "Autorização inválida ou já utilizada.")
            connection.state_hash = ""
            db.commit()
            if error or not code:
                raise HTTPException(
                    400, "A autorização Google foi cancelada. Pode tentar novamente na IAZELA."
                )
            creds = credentials()
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.post(
                        "https://oauth2.googleapis.com/token",
                        data={
                            "client_id": creds["client_id"],
                            "client_secret": creds["client_secret"],
                            "redirect_uri": CALLBACK,
                            "code": code,
                            "grant_type": "authorization_code",
                        },
                    )
                    if response.status_code != 200:
                        raise ValueError()
                    tokens = response.json()
                    granted = set(tokens.get("scope", "").split())
                    if not set(SCOPES.split()).issubset(granted) or not tokens.get("refresh_token"):
                        raise ValueError()
                    profile = client.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": "Bearer " + tokens["access_token"]},
                    )
                    if profile.status_code != 200:
                        raise ValueError()
                    email = profile.json().get("email", "").lower()
                    config = db.get(AttachmentIntegrationConfig, "default")
                    if not config or email != config.google_email.lower():
                        raise HTTPException(
                            422,
                            "A conta autorizada não corresponde ? conta Google configurada na empresa.",
                        )
            except (httpx.HTTPError, ValueError, KeyError):
                raise HTTPException(
                    502,
                    "Não foi possível concluir a autorização Google. Confirme as permissões e tente novamente.",
                ) from None
            connection.token_ciphertext = cipher().encrypt(json.dumps(tokens).encode()).decode()
            connection.email = email
            record(db, user, "google_conta_ligada", "default", {"email": email})
            db.commit()
        return HTMLResponse(
            "<html><meta name='referrer' content='no-referrer'><title>IAZELA</title><h1>Conta Google ligada</h1><p>Autorização guardada. Volte à IAZELA para consultar emails e importar anexos.</p><a href='http://127.0.0.1:5174'>Voltar à IAZELA</a></html>",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.post("/api/integrations/attachments/google/disconnect")
    def disconnect(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        connection = db.get(GoogleConnection, "default")
        revoked = False
        if connection and connection.token_ciphertext:
            try:
                tokens = json.loads(cipher().decrypt(connection.token_ciphertext.encode()))
                token = tokens.get("refresh_token") or tokens.get("access_token")
                if token:
                    with httpx.Client(timeout=8) as client:
                        revoked = client.post("https://oauth2.googleapis.com/revoke", data={"token": token}).status_code == 200
            except (httpx.HTTPError, ValueError, InvalidToken, HTTPException):
                pass
        if connection:
            connection.token_ciphertext = ""
            connection.state_hash = ""
            connection.email = ""
        record(db, user, "google_conta_desligada", "default", {"remote_revoked": revoked})
        db.commit()
        return {"ok": True, "remote_revoked": revoked, "message": "Ligação local removida e autorização revogada no Google." if revoked else "Ligação local removida. Não foi possível confirmar a revogação no Google; remova também o acesso nas ligações da sua conta Google."}
