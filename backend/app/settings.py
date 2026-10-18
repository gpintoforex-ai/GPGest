"""Definições da empresa: periodicidade do IVA e origem dos anexos."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from .access import authorize, current, record, serial
from .db import db_session
from .models import AttachmentIntegrationConfig
from .schemas import Strict

router = APIRouter()


class FiscalSettingsEdit(Strict):
    vat_period: Literal["", "mensal", "trimestral"]


@router.get("/api/settings/fiscal")
def fiscal_settings(user=Depends(current), db: Session = Depends(db_session)):
    from .models import CompanyFiscalSettings
    authorize(user, db, roles=["administrador", "gestor"])
    config = db.get(CompanyFiscalSettings, "default")
    return {"vat_period": config.vat_period if config else ""}


@router.post("/api/settings/fiscal")
def save_fiscal_settings(data: FiscalSettingsEdit, user=Depends(current), db: Session = Depends(db_session)):
    from .models import CompanyFiscalSettings
    authorize(user, db, roles=["administrador", "gestor"])
    config = db.get(CompanyFiscalSettings, "default")
    if not config:
        config = CompanyFiscalSettings(id="default")
        db.add(config)
    before = config.vat_period
    config.vat_period = data.vat_period
    record(db, user, "configuracao_fiscal_alterada", "default", {"antes": before, "depois": data.model_dump()})
    db.commit()
    return {"vat_period": config.vat_period}


class AttachmentConfigEdit(Strict):
    outlook_email: str = Field(max_length=200)
    google_email: str = Field(max_length=200)
    drive_folder: str = Field(max_length=300)
    mail_folder: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_settings(self):
        import re

        for field in ("outlook_email", "google_email"):
            value = getattr(self, field).strip().lower()
            if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                raise ValueError("Email invalid")
            setattr(self, field, value)
        folder = self.drive_folder.strip()
        if folder.startswith("https://drive.google.com/drive/folders/"):
            folder = folder.split("/folders/", 1)[1].split("?", 1)[0].strip("/")
        if folder and not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", folder):
            raise ValueError("Invalid Google Drive folder")
        self.drive_folder = folder
        self.mail_folder = self.mail_folder.strip()
        if not self.mail_folder:
            raise ValueError("Mail folder required")
        return self


@router.get("/api/settings/attachments")
def attachment_settings(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    config = db.get(AttachmentIntegrationConfig, "default")
    return {"config": serial(config) if config else {}, "connected": False}


@router.post("/api/settings/attachments")
def save_attachment_settings(
    data: AttachmentConfigEdit, user=Depends(current), db: Session = Depends(db_session)
):
    authorize(user, db, roles=["administrador", "gestor"])
    config = db.get(AttachmentIntegrationConfig, "default")
    if not config:
        config = AttachmentIntegrationConfig(id="default")
        db.add(config)
    for name, value in data.model_dump().items():
        setattr(config, name, value)
    record(db, user, "configuracao_anexos_alterada", "default", data.model_dump())
    db.commit()
    return {"ok": True}
