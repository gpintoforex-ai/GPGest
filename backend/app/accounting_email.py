"""Manual accounting dossier preparation, integrated into the existing module."""

import json
from datetime import date
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .accounting_email_files import CATEGORIES, collect
from .db import db_session
from .dossier_package import zip_bytes
from .email_delivery import dispatch_view, prior_versions
from .email_settings import configuration
from .models import EmailDispatch, Store, now, uid
from .platform import cipher
from .schemas import Strict


class PrepareInput(Strict):
    request_key: str = Field(min_length=10, max_length=100)
    start: date
    end: date
    store_ids: list[str] = Field(default_factory=list, max_length=100)
    categories: list[
        Literal[
            "compras",
            "notas_credito",
            "outros",
            "fechos",
            "depositos",
            "extratos",
            "pagamentos",
            "pendencias",
        ]
    ] = Field(default_factory=lambda: list(CATEGORIES), min_length=1, max_length=8)

    @model_validator(mode="after")
    def period(self):
        if self.start > self.end or (self.end - self.start).days > 366:
            raise ValueError("Selecione um período de até um ano.")
        return self


def register_routes(app, current, authorize, audit, projection, build_package):
    from .email_delivery import register_routes as delivery_routes
    from .email_settings import register_routes as settings_routes

    settings_routes(app, current, authorize, audit)
    delivery_routes(app, current, authorize, audit)

    @app.get("/api/accounting/email/periods")
    def periods(
        year: int = Query(ge=2000, le=2100),
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        from .accounting_delivery import bounds, local_today

        authorize(user, db, roles=["administrador"])
        _, config = configuration(db)
        mode = config["periodicity"]
        entries = []
        if mode != "personalizada":
            sent = list(
                db.scalars(
                    select(EmailDispatch).where(EmailDispatch.status.in_(["accepted", "received"]))
                )
            )
            for number in range(1, 13 if mode == "mensal" else 5):
                start, end = bounds(mode, year, number)
                entries.append(
                    {
                        "start": start,
                        "end": end,
                        "number": number,
                        "calendar_ended": end < str(local_today()),
                        "accepted_dispatches": sum(
                            row.start_date == start and row.end_date == end for row in sent
                        ),
                    }
                )
        return {
            "periodicity": mode,
            "note": config["custom_period_note"],
            "manual_only": True,
            "periods": entries,
        }

    @app.post("/api/accounting/email/prepare")
    def prepare(data: PrepareInput, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        existing = db.scalar(
            select(EmailDispatch).where(EmailDispatch.request_key == data.request_key)
        )
        if existing:
            return dispatch_view(db, existing)
        stores = list(dict.fromkeys(data.store_ids or db.scalars(select(Store.id))))
        if not stores:
            raise HTTPException(422, "Selecione pelo menos um estabelecimento.")
        for identifier in stores:
            authorize(user, db, identifier, ["administrador"])
            if not db.get(Store, identifier):
                raise HTTPException(404, "Estabelecimento não encontrado nesta empresa.")
        _, config = configuration(db)
        files, manifest = collect(
            db, user, data.start, data.end, stores, data.categories, projection, build_package
        )
        if not files:
            raise HTTPException(
                422,
                "Não existem ficheiros disponíveis nesta seleção. Inclua o relatório de pendências ou altere o período.",
            )
        company = db.info.get("company", {}).get("name") or "Empresa selecionada"
        to = list(
            dict.fromkeys(
                ([config["accountant_email"]] if config["accountant_email"] else [])
                + config["additional_to"]
            )
        )
        previous = prior_versions(db, [*to, *config["cc"]])
        for file in manifest["files"]:
            file["previous_dispatch"] = previous.get((file["key"], file["sha256"]))
        period = data.start.strftime("%d/%m/%Y") + " a " + data.end.strftime("%d/%m/%Y")
        pending = manifest["pending"]
        text_pending = "\n".join(
            f"- {item.get('document') or item.get('id')}: {item['reason']}" for item in pending[:20]
        )
        if len(pending) > 20:
            text_pending += (
                f"\n- Mais {len(pending) - 20} situações identificadas no relatório de pendências."
            )
        replacements = {
            "empresa": company,
            "periodo": period,
            "resumo_documentacao": f"{len(files)} ficheiros disponíveis; categorias: {', '.join(data.categories)}. O índice identifica os documentos efetivamente anexados a cada mensagem.",
            "resumo_pendencias_ou_sem_pendencias": text_pending
            or "Sem pendências identificadas na documentação disponível.",
            "assinatura_empresa": config["signature"] or company,
        }

        def render(template):
            for key, value in replacements.items():
                template = template.replace("{" + key + "}", value)
            return template

        draft = {
            "to": to,
            "cc": config["cc"],
            "subject": render(config["subject_template"]),
            "message": render(config["message_template"]),
            "format": "zip",
            "allow_split": False,
            "selected_keys": [
                file["key"] for file in manifest["files"] if not file["previous_dispatch"]
            ],
        }
        row = EmailDispatch(
            id=uid(),
            request_key=data.request_key,
            created_by=user.id,
            start_date=str(data.start),
            end_date=str(data.end),
            store_ids=json.dumps(stores),
            manifest=json.dumps(manifest, ensure_ascii=False),
            draft=json.dumps(draft, ensure_ascii=False),
            payload_ciphertext=cipher().encrypt(zip_bytes(files)),
            created_at=now(),
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.scalar(
                select(EmailDispatch).where(EmailDispatch.request_key == data.request_key)
            )
            if existing:
                return dispatch_view(db, existing)
            raise
        audit(
            db,
            user,
            "email_dossie_preparado",
            row.id,
            {
                "start": str(data.start),
                "end": str(data.end),
                "store_ids": stores,
                "file_count": len(files),
                "pending_count": len(pending),
            },
        )
        db.commit()
        return dispatch_view(db, row)
