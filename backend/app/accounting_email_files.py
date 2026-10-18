"""Collect accounting email material through the existing dossier builders."""

import hashlib
import json
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from .accounting_delivery import csv_file, dossier_reports
from .document_projection import DocumentContext, WorkflowContext
from .dossier_package import MAX_DOSSIER
from .models import (
    Attachment,
    BankAccount,
    BankTransaction,
    CashSession,
    DocumentPayment,
    ERPDocument,
    Movement,
    Purchase,
    Register,
)

CATEGORIES = [
    "compras",
    "notas_credito",
    "outros",
    "fechos",
    "depositos",
    "extratos",
    "pagamentos",
    "pendencias",
]


def collect(db, user, start, end, store_ids, categories, projection, build_package):
    from fastapi import HTTPException

    from .deposits import dossier_files
    from .document_scanning import dossier_scan_files

    purchases = list(
        db.scalars(
            select(Purchase)
            .where(
                Purchase.store_id.in_(store_ids),
                Purchase.document_date >= str(start),
                Purchase.document_date <= str(end),
            )
            .order_by(Purchase.document_date, Purchase.id)
        )
    )
    chosen = [
        row
        for row in purchases
        if (
            "notas_credito"
            if row.document_type == "NC"
            else "compras"
            if row.document_type in {"FT", "FR", "FS"}
            else "outros"
        )
        in categories
    ]
    context = DocumentContext(db, purchases)
    states = [projection(db, row, context=context) for row in purchases]
    pending = [
        {
            "id": row["id"],
            "document": row["document"],
            "supplier": row["supplier_name"],
            "missing_attachment": not row["attachments"],
            "erp": row["erp_status"],
            "reason": "Anexo em falta"
            if not row["attachments"]
            else "Correspondência ERP por confirmar ou com divergências",
        }
        for row in states
        if not row["attachments"] or row["erp_status"] != "confirmada"
    ]
    from .invoice_workflow import summarize

    workflow_context = WorkflowContext(db, purchases)
    for purchase in purchases:
        state = summarize(db, purchase, workflow_context)
        if state["attachments"] and state["attachment_state"] != "validated":
            pending.append(
                {
                    "id": purchase.id,
                    "document": purchase.document,
                    "supplier": purchase.supplier_name,
                    "reason": "Conteúdo do anexo por validar ou com divergências face à fatura.",
                }
            )
        for message in state["messages"]:
            pending.append(
                {
                    "id": purchase.id,
                    "document": purchase.document,
                    "supplier": purchase.supplier_name,
                    "reason": message,
                }
            )
    files, _, unavailable = build_package(chosen, user, db, return_files=True, allow_missing=True)
    if not chosen:
        files = {}
    for row in unavailable:
        pending.append(
            {
                "id": row[0],
                "document": row[1],
                "supplier": row[2],
                "missing_attachment": True,
                "reason": row[4],
            }
        )
    if "pendencias" in categories:
        erp = list(
            db.scalars(
                select(ERPDocument).where(
                    ERPDocument.store_id.in_(store_ids),
                    ERPDocument.document_date >= str(start),
                    ERPDocument.document_date <= str(end),
                )
            )
        )
        period = SimpleNamespace(
            start=str(start),
            end=str(end),
            vat_period="personalizada",
            closed_at=None,
            closed_by=None,
            reason="Preparação de envio manual; não altera o estado contabilístico.",
            exported_at=None,
        )
        files.update(dossier_reports(db, purchases, erp, states, period))
    if "depositos" in categories:
        files.update(dossier_files(db, str(start), str(end), store_ids))
    if "outros" in categories:
        files.update(dossier_scan_files(db, str(start), str(end), store_ids))
    source_metadata = {}
    if "fechos" in categories:
        from .main import close_report

        sheet_day = func.coalesce(
            CashSession.sheet_day,
            func.to_char(func.timezone("Europe/Lisbon", CashSession.opened_at), "YYYY-MM-DD"),
        )
        for session in db.scalars(
            select(CashSession)
            .join(Register)
            .where(
                Register.store_id.in_(store_ids),
                CashSession.closed_at.is_not(None),
                sheet_day >= str(start),
                sheet_day <= str(end),
            )
        ):
            key = f"fechos/{session.id}.pdf"
            files[key] = close_report(session.id, user=user, db=db).body
            source_metadata[key] = {
                "document": "Fecho de caixa "
                + (session.sheet_day or session.opened_at.date().isoformat()),
                "date": session.sheet_day or session.opened_at.date().isoformat(),
                "source_id": session.id,
            }
    if "extratos" in categories:
        for account in db.scalars(select(BankAccount).where(BankAccount.store_id.in_(store_ids))):
            transactions = list(
                db.scalars(
                    select(BankTransaction)
                    .where(
                        BankTransaction.account_id == account.id,
                        BankTransaction.movement_date >= str(start),
                        BankTransaction.movement_date <= str(end),
                    )
                    .order_by(
                        BankTransaction.movement_date,
                        BankTransaction.source_position.desc(),
                        BankTransaction.id,
                    )
                )
            )
            if transactions:
                key = f"extratos/{account.id}.csv"
                files[key] = csv_file(
                    [
                        "Conta",
                        "Banco",
                        "Data",
                        "Data valor",
                        "Descrição",
                        "Montante cêntimos",
                        "Saldo cêntimos",
                    ],
                    [
                        [
                            account.name,
                            account.bank,
                            row.movement_date,
                            row.value_date,
                            row.description,
                            row.amount_cents,
                            row.balance_cents,
                        ]
                        for row in transactions
                    ],
                )
                source_metadata[key] = {
                    "document": "Movimentos de extrato importados · " + account.name,
                    "source_id": account.id,
                    "note": "CSV gerado dos movimentos importados. O ficheiro bancário original não está guardado na aplicação.",
                }
    if "pagamentos" in categories:
        payments = list(
            db.scalars(
                select(DocumentPayment).where(
                    DocumentPayment.store_id.in_(store_ids),
                    DocumentPayment.payment_date >= str(start),
                    DocumentPayment.payment_date <= str(end),
                )
            )
        )
        if payments:
            files["pagamentos/indice.csv"] = csv_file(
                ["ID", "Data", "Entidade", "Referência", "Montante cêntimos", "Método", "Origem"],
                [
                    [
                        row.id,
                        row.payment_date,
                        row.entity,
                        row.reference,
                        row.amount_cents * row.direction,
                        row.method,
                        row.source,
                    ]
                    for row in payments
                ],
            )
        since = datetime.combine(start, time.min, ZoneInfo("Europe/Lisbon"))
        until = datetime.combine(end + timedelta(days=1), time.min, ZoneInfo("Europe/Lisbon"))
        for attachment, movement in db.execute(
            select(Attachment, Movement)
            .join(Movement, Attachment.movement_id == Movement.id)
            .outerjoin(CashSession, Movement.session_id == CashSession.id)
            .outerjoin(Register, CashSession.register_id == Register.id)
            .outerjoin(BankAccount, Movement.bank_account_id == BankAccount.id)
            .where(
                or_(Register.store_id.in_(store_ids), BankAccount.store_id.in_(store_ids)),
                Movement.occurred_at >= since,
                Movement.occurred_at < until,
            )
        ):
            extension = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}.get(
                attachment.mime, ".bin"
            )
            key = f"pagamentos/{movement.id}/{attachment.id}{extension}"
            files[key] = attachment.data
            source_metadata[key] = {
                "supplier": movement.entity,
                "document": movement.document,
                "date": movement.occurred_at.date().isoformat(),
                "amount_cents": movement.amount_cents * movement.direction,
                "original_name": attachment.name,
                "source_id": movement.id,
            }
    for key, raw in list(files.items()):
        if key.endswith("pendencias.json"):
            for item in json.loads(raw):
                pending.append(
                    {
                        "id": item.get("id", ""),
                        "document": item.get("numero", item.get("name", "")),
                        "reason": item.get("pendencia", item.get("reason", "Por rever")),
                    }
                )
    if "pendencias" in categories:
        files["pendencias-envio.csv"] = csv_file(
            ["ID", "Documento", "Fornecedor", "Situação"],
            [
                [row.get("id"), row.get("document"), row.get("supplier"), row.get("reason")]
                for row in pending
            ],
        )
    if sum(len(raw) for raw in files.values()) > MAX_DOSSIER or len(files) > 1000:
        raise HTTPException(
            413, "O dossiê excede 100 MB ou 1000 ficheiros. Reduza o período ou as categorias."
        )
    by_purchase = {row.id: row for row in purchases}
    by_state = {row["id"]: row for row in states}
    metadata = []
    for key, raw in sorted(list(files.items())):
        info = {
            "key": key,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "category": key.split("/")[0] if "/" in key else "relatorios",
            "original_name": key.split("/")[-1],
            **source_metadata.get(key, {}),
        }
        if key.startswith("documentos/"):
            identifier = key.split("/")[1]
            row = by_purchase[identifier]
            original = next(
                (
                    item["name"]
                    for item in by_state[identifier]["attachments"]
                    if item["id"].removeprefix("drive:") == key.split("/")[-1].rsplit(".", 1)[0]
                ),
                info["original_name"],
            )
            info.update(
                supplier=row.supplier_name,
                nif=row.supplier_nif,
                document=row.document,
                date=row.document_date,
                amount_cents=row.amount_cents,
                source_id=row.id,
                original_name=original,
            )
            category = (
                "notas_credito"
                if row.document_type == "NC"
                else "compras"
                if row.document_type in {"FT", "FR", "FS"}
                else "outros"
            )
            organized_key = category + "/" + key
            files[organized_key] = files.pop(key)
            info.update(key=organized_key, category=category)
        metadata.append(info)
    if "anexos.csv" in files:
        # The shared inventory must reference this selection's actual organized paths.
        files["anexos.csv"] = csv_file(
            ["Documento ID", "Anexo ID", "Nome original", "Ficheiro no ZIP"],
            [
                [
                    item["source_id"],
                    item["key"].split("/")[-1].rsplit(".", 1)[0],
                    item["original_name"],
                    item["key"],
                ]
                for item in metadata
                if "/documentos/" in item["key"]
            ],
        )
        item = next(item for item in metadata if item["key"] == "anexos.csv")
        item.update(
            size_bytes=len(files["anexos.csv"]),
            sha256=hashlib.sha256(files["anexos.csv"]).hexdigest(),
        )
    return files, {
        "files": metadata,
        "pending": pending,
        "categories": categories,
        "company": {key: db.info.get("company", {}).get(key, "") for key in ("id", "name", "nif")},
    }
