"""Definições da empresa: regime e periodicidade do IVA e origem dos anexos."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from .access import authorize, current, record, serial
from .db import db_session
from .models import AttachmentIntegrationConfig, CompanyFiscalSettings
from .schemas import Strict

router = APIRouter()


VatRegime = Literal["", "normal", "isento_53", "pequenos_retalhistas"]
VatPeriod = Literal["", "mensal", "trimestral"]
VAT_REGIMES = {
    "normal": "Regime normal",
    "isento_53": "Isenção do artigo 53.º do CIVA",
    "pequenos_retalhistas": "Regime especial dos pequenos retalhistas (art. 60.º do CIVA)",
}


def vat_period_for(regime, period):
    """Periodicidade válida para o regime: os pequenos retalhistas declaram trimestralmente.

    Na isenção do artigo 53.º não há declaração periódica; a periodicidade apenas organiza
    a entrega dos documentos à contabilidade.
    """
    if not regime:
        if period:
            raise HTTPException(422, "Escolha o regime de IVA antes da periodicidade.")
        return ""
    if regime == "pequenos_retalhistas":
        return "trimestral"
    if not period:
        raise HTTPException(422, "Escolha a periodicidade mensal ou trimestral.")
    return period


def save_vat_regime(db, user, regime, period):
    """Guarda regime e periodicidade de IVA da empresa, com registo na auditoria."""
    period = vat_period_for(regime, period)
    config = db.get(CompanyFiscalSettings, "default")
    if not config:
        config = CompanyFiscalSettings(id="default", vat_period="", vat_regime="")
        db.add(config)
    before = {"vat_regime": config.vat_regime, "vat_period": config.vat_period}
    after = {"vat_regime": regime, "vat_period": period}
    if before != after:
        config.vat_regime, config.vat_period = regime, period
        record(db, user, "configuracao_fiscal_alterada", "default", {"antes": before, "depois": after})
    return after


def vat_settings(db):
    config = db.get(CompanyFiscalSettings, "default")
    return {"vat_regime": config.vat_regime if config else "", "vat_period": config.vat_period if config else ""}


class FiscalSettingsEdit(Strict):
    # Sem regime indicado (pedidos anteriores), uma periodicidade corresponde ao regime normal.
    vat_regime: VatRegime | None = None
    vat_period: VatPeriod = ""


@router.get("/api/settings/fiscal")
def fiscal_settings(user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    return vat_settings(db)


@router.post("/api/settings/fiscal")
def save_fiscal_settings(data: FiscalSettingsEdit, user=Depends(current), db: Session = Depends(db_session)):
    authorize(user, db, roles=["administrador", "gestor"])
    regime = data.vat_regime if data.vat_regime is not None else "normal" if data.vat_period else ""
    result = save_vat_regime(db, user, regime, data.vat_period)
    db.commit()
    return result


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
