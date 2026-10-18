"""The shared documentary dossier, used by downloads and accounting email."""

import io
import zipfile
from decimal import Decimal

import httpx
from fastapi import HTTPException, Response
from sqlalchemy import select

from .document_projection import DocumentContext
from .models import DriveDocumentAttachment, PurchaseAttachment

MAX_DOSSIER = 100_000_000


def zip_bytes(files):
    if sum(len(raw) for raw in files.values()) > MAX_DOSSIER:
        raise HTTPException(413, "Selecione menos documentos: o dossiê excede 100 MB.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            # Fixed metadata makes identical dossiers have identical versions.
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    return output.getvalue()


def document_files(
    rows,
    user,
    db,
    projection,
    record,
    extra_files=None,
    filename="entrega-contabilidade.zip",
    return_files=False,
    allow_missing=False,
):
    from .accounting_delivery import csv_file
    from .drive_documents import read_link

    context = DocumentContext(db, rows)
    files = dict(extra_files or {})
    states, listing, unavailable = [], [], []
    total = sum(len(raw) for raw in files.values())
    for row in rows:
        state = projection(db, row, context=context)
        states.append(state)

        def euros(value):
            return f"{Decimal(value) / 100:.2f}".replace(".", ",")

        listing.append(
            [
                row.id,
                row.supplier_nif,
                row.supplier_name,
                row.document,
                row.document_type,
                row.document_date[8:10]
                + "/"
                + row.document_date[5:7]
                + "/"
                + row.document_date[:4],
                euros(row.base_cents) if row.base_cents is not None else "",
                euros(row.tax_cents),
                euros(row.amount_cents),
                state["availability"],
                state["location"],
                state["attachment_status"],
                state["erp_status"],
                state["treatment"],
                state["accounting"],
                state["payment_status"],
                state["bank_status"],
            ]
        )
        natives = list(
            db.scalars(select(PurchaseAttachment).where(PurchaseAttachment.purchase_id == row.id))
        )
        remotes = list(
            db.scalars(
                select(DriveDocumentAttachment).where(DriveDocumentAttachment.purchase_id == row.id)
            )
        )
        for attachment in [*natives, *remotes]:
            try:
                raw, mime = (
                    read_link(db, attachment)
                    if isinstance(attachment, DriveDocumentAttachment)
                    else (attachment.data, attachment.mime)
                )
            except (HTTPException, httpx.HTTPError):
                if not allow_missing:
                    raise
                unavailable.append(
                    [
                        row.id,
                        row.document,
                        row.supplier_name,
                        attachment.name,
                        "Anexo cloud indisponível; não foi incluído no envio.",
                    ]
                )
                continue
            total += len(raw)
            if total > MAX_DOSSIER:
                raise HTTPException(413, "Selecione menos documentos: o dossiê excede 100 MB.")
            ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}.get(
                mime, ".bin"
            )
            files[f"documentos/{row.id}/{attachment.id}{ext}"] = raw
        if not return_files:
            record(
                db,
                user,
                "pacote_contabilidade_exportado",
                row.id,
                {"delivery_changed": False, "attachments": len(state["attachments"])},
                row.store_id,
            )
    files["controlo.csv"] = csv_file(
        [
            "ID",
            "NIF",
            "Fornecedor",
            "Documento",
            "Tipo",
            "Data",
            "Base EUR",
            "IVA EUR",
            "Total EUR",
            "Disponibilidade",
            "Localização",
            "Anexos",
            "ERP",
            "Tratamento",
            "Contabilidade",
            "Pagamento",
            "Banco",
        ],
        listing,
    )
    files["LEIA-ME.txt"] = (
        "Preparar, exportar ou enviar não marca documentos como contabilizados, reconciliados ou completos. "
        "Os documentos físicos sem anexo devem acompanhar a listagem. Os nomes dos anexos usam identificadores da aplicação.\n"
    ).encode()
    if unavailable:
        files["anexos-indisponiveis.csv"] = csv_file(
            ["ID", "Documento", "Fornecedor", "Nome", "Pendência"], unavailable
        )
    if return_files:
        return files, states, unavailable
    result = zip_bytes(files)
    db.commit()
    return Response(
        result,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
