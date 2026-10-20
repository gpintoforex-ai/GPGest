"""Reusable per-company SMTP configuration, with encrypted credentials."""

import json
import re
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session
from .models import CompanyEmailSettings, now
from .platform import cipher
from .schemas import Strict

SUBJECT = "Documentação contabilística — {empresa} — {periodo}"
BODY = """Exmos. Senhores,

Enviamos a documentação contabilística referente ao período {periodo} da empresa {empresa}.

Documentos incluídos:
{resumo_documentacao}

Documentos ou situações pendentes:
{resumo_pendencias_ou_sem_pendencias}

Agradecemos a confirmação da receção e a indicação de qualquer documentação adicional necessária.

Com os melhores cumprimentos,
{assinatura_empresa}"""


def smtp_security_error(port, security):
    if port == 587 and security == "ssl":
        return "A porta 587 exige STARTTLS. Selecione STARTTLS ou utilize a porta 465 com SSL / TLS."
    if port == 465 and security == "starttls":
        return "A porta 465 exige SSL / TLS. Selecione SSL / TLS ou utilize a porta 587 com STARTTLS."
    return None


def email_address(value):
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",
        value,
    ):
        raise ValueError("Indique um endereço de email válido, sem nome ou separadores.")
    return value


class EmailConfigInput(Strict):
    revision: int = Field(default=0, ge=0)
    enabled: bool = False
    host: str = Field(default="", max_length=250)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "ssl"] = "starttls"
    username: str = Field(default="", max_length=250)
    password: str = Field(default="", max_length=500)
    clear_password: bool = False
    sender_name: str = Field(default="IAZELA", max_length=200)
    sender_email: str = Field(default="", max_length=254)
    max_message_bytes: int = Field(default=20_000_000, ge=64_000, le=100_000_000)
    accountant_name: str = Field(default="", max_length=200)
    accountant_email: str = Field(default="", max_length=254)
    additional_to: list[str] = Field(default_factory=list, max_length=49)
    cc: list[str] = Field(default_factory=list, max_length=50)
    subject_template: str = Field(default=SUBJECT, min_length=1, max_length=500)
    message_template: str = Field(default=BODY, min_length=1, max_length=20_000)
    signature: str = Field(default="", max_length=5000)
    periodicity: Literal["mensal", "trimestral", "personalizada"] = "mensal"
    custom_period_note: str = Field(default="", max_length=1000)

    @field_validator(
        "sender_name",
        "sender_email",
        "accountant_name",
        "accountant_email",
        "subject_template",
        "host",
        "username",
    )
    @classmethod
    def no_header_breaks(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Este campo não aceita quebras de linha nem caracteres de controlo.")
        return value.strip()

    @field_validator("sender_email", "accountant_email")
    @classmethod
    def optional_email(cls, value):
        return email_address(value) if value else ""

    @field_validator("additional_to", "cc")
    @classmethod
    def addresses(cls, values):
        return list(dict.fromkeys(email_address(value) for value in values))

    @model_validator(mode="after")
    def ready(self):
        if error := smtp_security_error(self.port, self.security):
            raise ValueError(error)
        if self.host and (
            any(char.isspace() for char in self.host) or any(char in self.host for char in "/@?#")
        ):
            raise ValueError("Indique apenas o nome do servidor SMTP, sem URL ou credenciais.")
        if self.enabled and (not self.host or not self.sender_email):
            raise ValueError("Para ativar o SMTP, indique o servidor e o email do remetente.")
        return self


def configuration(db):
    row = db.get(CompanyEmailSettings, "default")
    defaults = EmailConfigInput().model_dump(exclude={"password", "clear_password", "revision"})
    return row, {**defaults, **(json.loads(row.settings) if row else {})}


def view(db):
    row, config = configuration(db)
    return {
        **config,
        "revision": row.revision if row else 0,
        "has_password": bool(row and row.password_ciphertext),
        "manual_only": True,
    }


def register_routes(app, current, authorize, audit):
    @app.get("/api/settings/email")
    def get_settings(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        return view(db)

    @app.post("/api/settings/email")
    def save_settings(
        data: EmailConfigInput, user=Depends(current), db: Session = Depends(db_session)
    ):
        authorize(user, db, roles=["administrador"])
        row = db.scalar(
            select(CompanyEmailSettings)
            .where(CompanyEmailSettings.id == "default")
            .with_for_update()
        )
        if data.revision != (row.revision if row else 0):
            raise HTTPException(
                409, "A configuração foi alterada entretanto. Volte a abrir Email antes de guardar."
            )
        if row is None:
            row = CompanyEmailSettings(id="default", revision=0)
            db.add(row)
        if data.clear_password:
            row.password_ciphertext = ""
        elif data.password:
            row.password_ciphertext = cipher().encrypt(data.password.encode()).decode()
        row.settings = json.dumps(
            data.model_dump(exclude={"password", "clear_password", "revision"}), ensure_ascii=False
        )
        row.revision += 1
        row.updated_by = user.id
        row.updated_at = now()
        audit(
            db,
            user,
            "email_configuracao_guardada",
            "default",
            {
                "revision": row.revision,
                "enabled": data.enabled,
                "password_changed": bool(data.password or data.clear_password),
            },
        )
        db.commit()
        return view(db)

    @app.post("/api/settings/email/test")
    def test_connection(user=Depends(current), db: Session = Depends(db_session)):
        from .email_transport import DeliveryFailure, connect, disconnect

        authorize(user, db, roles=["administrador"])
        row, config = configuration(db)
        if not row or not config["host"] or not config["sender_email"]:
            raise HTTPException(422, "Guarde primeiro o servidor e o remetente SMTP.")
        if error := smtp_security_error(config["port"], config["security"]):
            raise HTTPException(422, error + " Guarde a configuração antes de testar novamente.")
        try:
            password = (
                cipher().decrypt(row.password_ciphertext.encode()).decode()
                if row.password_ciphertext
                else ""
            )
            client, limit = connect(config, password)
            disconnect(client)
        except DeliveryFailure as exc:
            raise HTTPException(502, {"public_message": str(exc)}) from None
        except Exception:
            raise HTTPException(
                503,
                {"public_message": "Não foi possível abrir a credencial SMTP guardada. Volte a gravar a palavra-passe em Email."},
            ) from None
        audit(db, user, "email_ligacao_testada", "default", {"server_limit_bytes": limit})
        db.commit()
        return {
            "ok": True,
            "server_limit_bytes": limit,
            "message": "Ligação e autenticação verificadas. Nenhum email foi enviado.",
        }
