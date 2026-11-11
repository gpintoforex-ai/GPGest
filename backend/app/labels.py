"""Etiquetas de preço: definições da empresa (folha, dimensões, conteúdo e logótipo).

As definições ficam numa linha própria de `stock_settings` (id «labels»), com revisão e auditoria.
A impressão é feita no navegador a partir das linhas da receção e do PVP atual dos artigos;
o servidor não gera documentos nem altera preços.
"""

import json
import re
from decimal import Decimal as D

from fastapi import Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from .db import db_session
from .models import StockSettings
from .schemas import Strict

ROW = "labels"
# Por omissão: folha A4 com 5 × 13 etiquetas de 3,81 × 2,20 cm (como na Wintouch).
DEFAULTS = {
    "page_width": "21.00", "page_height": "29.70", "margin_top": "1.00", "margin_left": "0.20",
    "columns": 5, "rows": 13, "label_width": "3.81", "label_height": "2.20", "gap_horizontal": "0.40", "gap_vertical": "0.00",
    "show_logo": True, "show_name": True, "uppercase_name": True, "show_price": True, "show_code": True,
    "price_note": "P.V.P C/ IVA à taxa em vigor", "footer": "", "logo": "",
}
LOGO_LIMIT = 400_000  # Caracteres do data URL (≈ 300 KB de imagem).
Measure = D


class LabelSettings(Strict):
    revision: int = Field(ge=0)
    page_width: Measure = Field(gt=0, le=100, decimal_places=2)
    page_height: Measure = Field(gt=0, le=100, decimal_places=2)
    margin_top: Measure = Field(ge=0, le=50, decimal_places=2)
    margin_left: Measure = Field(ge=0, le=50, decimal_places=2)
    columns: int = Field(ge=1, le=20)
    rows: int = Field(ge=1, le=60)
    label_width: Measure = Field(gt=0, le=50, decimal_places=2)
    label_height: Measure = Field(gt=0, le=50, decimal_places=2)
    gap_horizontal: Measure = Field(ge=0, le=20, decimal_places=2)
    gap_vertical: Measure = Field(ge=0, le=20, decimal_places=2)
    show_logo: bool
    show_name: bool
    uppercase_name: bool
    show_price: bool
    show_code: bool
    price_note: str = Field(max_length=80)
    footer: str = Field(max_length=80)
    logo: str = Field(max_length=LOGO_LIMIT)

    @field_validator("logo")
    @classmethod
    def image(cls, value):
        if value and not re.fullmatch(r"data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+", value):
            raise ValueError("O logótipo tem de ser uma imagem PNG, JPEG, WebP ou GIF.")
        return value


def settings(db):
    row = db.get(StockSettings, ROW)
    return {**DEFAULTS, **(json.loads(row.data) if row else {}), "revision": row.revision if row else 0}


def register_routes(app, current, authorize, record):
    @app.get("/api/stock/label-settings")
    def read(user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db)
        return settings(db)

    @app.post("/api/stock/label-settings")
    def save(data: LabelSettings, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador", "gestor"])
        row = db.get(StockSettings, ROW, with_for_update=True)
        current_revision = row.revision if row else 0
        if data.revision != current_revision:
            raise HTTPException(409, "As definições das etiquetas mudaram. Volte a abrir e repita a alteração.")
        page_used_width = data.margin_left + data.columns * data.label_width + (data.columns - 1) * data.gap_horizontal
        page_used_height = data.margin_top + data.rows * data.label_height + (data.rows - 1) * data.gap_vertical
        if page_used_width > data.page_width or page_used_height > data.page_height:
            raise HTTPException(422, "As etiquetas não cabem na folha: reveja as margens, o número, as dimensões e as distâncias.")
        values = data.model_dump(mode="json", exclude={"revision"})
        if not row:
            row = StockSettings(id=ROW, revision=0, data="{}")
            db.add(row)
        row.data = json.dumps(values, ensure_ascii=False)
        row.revision = current_revision + 1
        # Auditoria sem a imagem (só se indica se há logótipo).
        record(db, user, "etiquetas_configuradas", ROW, {**{k: v for k, v in values.items() if k != "logo"}, "logo": bool(values["logo"])})
        db.commit()
        return settings(db)
