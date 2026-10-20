"""Company-scoped administrative period closure and documentary dossier export."""

import calendar
import csv
import io
import json
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import db_session
from .models import (
    AccountingPeriod,
    CompanyFiscalSettings,
    DriveDocumentAttachment,
    ERPDocument,
    Purchase,
    PurchaseAttachment,
    BankDeposit,
    DepositAttachment,
    now,
)
from .schemas import Strict


class PeriodRequest(Strict):
    vat_period: Literal["mensal", "trimestral"]
    year: int = Field(ge=2000, le=2100)
    number: int = Field(ge=1, le=12)


class CloseRequest(PeriodRequest):
    reason: str = Field(min_length=3, max_length=2000)


def local_today():
    return datetime.now(ZoneInfo("Europe/Lisbon")).date()


def bounds(mode, year, number):
    if mode == "trimestral" and number > 4:
        raise HTTPException(422, "Selecione um trimestre entre 1 e 4.")
    month = number if mode == "mensal" else (number - 1) * 3 + 1
    last_month = month if mode == "mensal" else month + 2
    return (
        date(year, month, 1).isoformat(),
        date(year, last_month, calendar.monthrange(year, last_month)[1]).isoformat(),
    )


def csv_file(headers, rows):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")

    def safe(value):
        text = "" if value is None else str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text

    writer.writerow(headers)
    writer.writerows([[safe(value) for value in row] for row in rows])
    return output.getvalue().encode("utf-8-sig")


def dossier_reports(db, rows, erp, state, period):
    files = {
        "documentos.json": json.dumps(
            jsonable_encoder(state), ensure_ascii=False, indent=2
        ).encode("utf-8"),
        "periodo.json": json.dumps(
            {
                "company": db.info.get("company", {}),
                "vat_period": period.vat_period,
                "start": period.start,
                "end": period.end,
                "closed_at": period.closed_at.isoformat() if period.closed_at else None,
                "closed_by": period.closed_by,
                "reason": period.reason,
                "exported_at": getattr(period, "exported_at", now().isoformat()),
                "documents": len(rows),
                "erp_documents": len(erp),
                "scope": "Documentos e anexos atualmente disponíveis na IAZELA para este período. O fecho de entrega não bloqueia alterações aos documentos nem confirma obrigações fiscais.",
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
        "documentos-erp.csv": csv_file(
            [
                "ID",
                "Empresa ERP",
                "Fornecedor",
                "NIF",
                "Documento",
                "Referência interna",
                "Data",
                "Total cêntimos",
                "Anulado",
            ],
            [
                (
                    r.id,
                    r.enterprise_id,
                    r.supplier_name,
                    r.supplier_nif,
                    r.document,
                    r.internal_document,
                    r.document_date,
                    r.amount_cents,
                    r.cancelled,
                )
                for r in erp
            ],
        ),
        "pendencias.csv": csv_file(
            ["ID", "Documento", "Fornecedor", "Anexo", "ERP", "Tratamento", "Entrega"],
            [
                (
                    r["id"],
                    r["document"],
                    r["supplier_name"],
                    r["attachment_status"],
                    r["erp_status"],
                    r["treatment"],
                    r["accounting"],
                )
                for r in state
                if r["attachment_status"] == "sem_anexo"
                or r["erp_status"] != "confirmada"
                or r["accounting"] != "confirmado"
            ],
        ),
        "anexos.csv": csv_file(
            ["Documento ID", "Anexo ID", "Nome original", "Ficheiro no ZIP"],
            [
                (
                    r["id"],
                    a["id"],
                    a["name"],
                    f"documentos/{r['id']}/{a['id'].removeprefix('drive:')}{ {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg'}.get(a['mime'], '') }",
                )
                for r in state
                for a in r["attachments"]
            ],
        ),
    }
    return files


def register_routes(app, current, authorize, record, projection, build_package):
    from .accounting_email import register_routes as register_accounting_email_routes
    register_accounting_email_routes(app, current, authorize, record, projection, build_package)

    def period_row(db, data, lock=False):
        query = select(AccountingPeriod).where(
            AccountingPeriod.vat_period == data.vat_period,
            AccountingPeriod.year == data.year,
            AccountingPeriod.number == data.number,
        )
        return db.scalar(query.with_for_update() if lock else query)

    def summary(db, mode, year, number):
        start, end = bounds(mode, year, number)
        matching = [Purchase.document_date >= start, Purchase.document_date <= end]
        count = db.scalar(select(func.count()).select_from(Purchase).where(*matching))
        missing = db.scalar(
            select(func.count())
            .select_from(Purchase)
            .where(
                *matching,
                ~select(PurchaseAttachment.id)
                .where(PurchaseAttachment.purchase_id == Purchase.id)
                .exists(),
                ~select(DriveDocumentAttachment.id)
                .where(DriveDocumentAttachment.purchase_id == Purchase.id)
                .exists(),
            )
        )
        closed = period_row(db, PeriodRequest(vat_period=mode, year=year, number=number))
        from .deposits import period_condition
        deposit_match = [period_condition(start, end)]
        count += db.scalar(select(func.count()).select_from(BankDeposit).where(*deposit_match)) or 0
        missing += db.scalar(select(func.count()).select_from(BankDeposit).where(*deposit_match, BankDeposit.status != "cancelado",
            ~select(DepositAttachment.id).where(DepositAttachment.deposit_id == BankDeposit.id, DepositAttachment.validated_by.is_not(None)).exists())) or 0
        return {
            "vat_period": mode,
            "year": year,
            "number": number,
            "start": start,
            "end": end,
            "documents": count,
            "without_attachment": missing,
            "calendar_ended": end < local_today().isoformat(),
            "closed_at": closed.closed_at if closed else None,
            "closed_by": closed.closed_by if closed else None,
            "reason": closed.reason if closed else "",
        }

    @app.get("/api/accounting/delivery-periods")
    def periods(
        year: int = Query(ge=2000, le=2100),
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        authorize(user, db, roles=["administrador"])
        config = db.get(CompanyFiscalSettings, "default")
        mode = config.vat_period if config else ""
        regime = config.vat_regime if config else ""
        if not mode:
            return {"vat_period": "", "vat_regime": regime, "periods": []}
        entries = [summary(db, mode, year, n) for n in range(1, 13 if mode == "mensal" else 5)]
        # Preserve access to previously closed periods after a change of VAT regime.
        for old in db.scalars(
            select(AccountingPeriod).where(
                AccountingPeriod.year == year,
                AccountingPeriod.vat_period != mode,
                AccountingPeriod.closed_at.is_not(None),
            )
        ):
            entries.append(summary(db, old.vat_period, year, old.number))
        return {"vat_period": mode, "vat_regime": regime, "periods": entries}

    @app.post("/api/accounting/delivery-periods/close")
    def close(data: CloseRequest, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        if len(data.reason.strip()) < 3:
            raise HTTPException(422, "Indique uma justificação para o fecho.")
        start, end = bounds(data.vat_period, data.year, data.number)
        config = db.scalar(
            select(CompanyFiscalSettings)
            .where(CompanyFiscalSettings.id == "default")
            .with_for_update()
        )
        if not config or config.vat_period != data.vat_period:
            raise HTTPException(409, "O período não corresponde ao regime de IVA configurado.")
        if end >= local_today().isoformat():
            raise HTTPException(409, "O período ainda não terminou no calendário.")
        period = period_row(db, data, True)
        if period and period.closed_at:
            return summary(db, data.vat_period, data.year, data.number)
        overlap = db.scalar(
            select(AccountingPeriod).where(
                AccountingPeriod.closed_at.is_not(None),
                AccountingPeriod.start <= end,
                AccountingPeriod.end >= start,
            )
        )
        if overlap:
            raise HTTPException(
                409,
                "Já existe um período encerrado que abrange estas datas. Reabra-o antes de alterar a periodicidade.",
            )
        if period is None:
            period = AccountingPeriod(
                vat_period=data.vat_period, year=data.year, number=data.number, start=start, end=end
            )
            db.add(period)
        period.closed_at, period.closed_by, period.reason = now(), user.id, data.reason.strip()
        db.flush()
        record(
            db,
            user,
            "periodo_contabilidade_encerrado",
            period.id,
            {"start": start, "end": end, "reason": period.reason},
        )
        db.commit()
        return summary(db, data.vat_period, data.year, data.number)

    @app.post("/api/accounting/delivery-periods/reopen")
    def reopen(data: CloseRequest, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        if len(data.reason.strip()) < 3:
            raise HTTPException(422, "Justifique a reabertura.")
        period = period_row(db, data, True)
        if not period or not period.closed_at:
            raise HTTPException(409, "O período não está encerrado.")
        record(
            db,
            user,
            "periodo_contabilidade_reaberto",
            period.id,
            {"reason": data.reason.strip(), "closed_at": period.closed_at.isoformat()},
        )
        period.closed_at, period.closed_by, period.reason = None, None, data.reason.strip()
        db.commit()
        return {"status": "reaberto"}

    @app.post("/api/accounting/delivery-periods/export")
    def export(data: PeriodRequest, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        period = period_row(db, data, True)
        if not period or not period.closed_at:
            raise HTTPException(
                409, "O administrador tem de confirmar o fecho antes de exportar o dossiê."
            )
        rows = list(
            db.scalars(
                select(Purchase)
                .where(
                    Purchase.document_date >= period.start,
                    Purchase.document_date <= period.end,
                )
                .order_by(Purchase.document_date, Purchase.id)
            )
        )
        erp = list(
            db.scalars(
                select(ERPDocument)
                .where(
                    ERPDocument.document_date >= period.start,
                    ERPDocument.document_date <= period.end,
                )
                .order_by(ERPDocument.document_date, ERPDocument.id)
            )
        )
        state = [projection(db, row) for row in rows]
        files = dossier_reports(db, rows, erp, state, period)

        record(
            db,
            user,
            "dossie_contabilidade_exportado",
            period.id,
            {
                "start": period.start,
                "end": period.end,
                "documents": len(rows),
                "erp_documents": len(erp),
            },
        )
        from .deposits import dossier_files
        files.update(dossier_files(db, period.start, period.end))
        from .document_scanning import dossier_scan_files
        files.update(dossier_scan_files(db, period.start, period.end))
        return build_package(
            rows, user, db, files, f"dossie-{data.vat_period}-{data.year}-{data.number:02}.zip"
        )
