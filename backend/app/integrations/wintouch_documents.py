"""Read-only Wintouch purchase-document adapter, validated against local Swagger and live fields."""

from datetime import date

from ..document_identity import number_key
from ..wintouch_sales import amount, document_label, document_total, source_date
from ..wintouch_sync import SyncError, string
from .erp import PurchaseDocument


class WintouchPurchaseAdapter:
    def __init__(self, reader):
        self.reader = reader

    def purchase_documents(self, start: date, end: date):
        for values in snapshot(self.reader, start.isoformat(), end.isoformat()):
            yield PurchaseDocument.model_validate(values)


def snapshot(reader, start, end):
    types = {row["ID"]: row for row in reader.collection("document_types")}
    series = {row["ID"]: row for row in reader.collection("document_series")}
    currencies = {row["ID"]: row for row in reader.collection("currencies")}
    result = []
    for row in reader.collection("product_documents", "DocumentType,DocumentSerie,CEntity"):
        typ = types.get(row.get("DocumentTypeID"), {})
        cls = typ.get("Class")
        if cls not in {32, 33, 34, 35}:
            continue
        day = source_date(row.get("Date"))
        if not start <= day <= end:
            continue
        if currencies.get(row.get("CurrencyID"), {}).get("Code") != "EUR":
            raise SyncError("A conferência de compras suporta apenas documentos em EUR.")
        entity = row.get("CEntity") or {}
        nif = str(row.get("VATNumber") or entity.get("VATNumber") or "").removeprefix("PT").strip()
        number = string(row.get("ExternalDocument"), 100) or ""
        prefix = number_key(number).split(" ")[0]
        kind = (
            prefix
            if prefix in {"FT", "FS", "FR", "NC", "ND"}
            else {32: "FT", 34: "FT", 35: "NC"}.get(cls, "")
        )
        total = document_total(row)
        if kind == "NC":
            total = -abs(total)
        base = (
            sum(amount(row.get(f"BaseValue{i}") or 0) for i in range(1, 5))
            if "BaseValue1" in row
            else None
        )
        tax = (
            sum(amount(row.get(f"VATValue{i}") or 0) for i in range(1, 5))
            if "VATValue1" in row
            else None
        )
        result.append(
            {
                "external_id": string(row.get("ID"), 200, True),
                "supplier_nif": nif,
                "supplier_name": string(
                    row.get("EntityName") or entity.get("Name") or nif or "Fornecedor não indicado",
                    300,
                    True,
                ),
                "document": number,
                "internal_document": document_label(row, types, series),
                "document_type": kind,
                "document_date": day,
                "amount_cents": total,
                "base_cents": base,
                "tax_cents": tax,
                "cancelled": bool(row.get("DeletedDate")),
            }
        )
    return result
