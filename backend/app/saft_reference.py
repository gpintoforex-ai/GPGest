"""Conservative billing/receipt comparison; never participates in cash balances."""

import json
from collections import defaultdict
from types import SimpleNamespace

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB

from .models import CompanySetup, WintouchSaleRecord
from .saft_parser import INVOICE_TYPES


def reference(db, store_ids, start, end):
    from .cash_local import methods
    from .saft_sales import enterprise

    source = enterprise(db)
    # Include cross-period receipts for duplicate detection. Do not assign them
    # to the invoice date. Actual payment dates are filtered only afterwards.
    rows = [
        (SimpleNamespace(id=identifier, store_id=store), data)
        for identifier, store, data in db.execute(
            select(
                WintouchSaleRecord.id,
                WintouchSaleRecord.store_id,
                cast(WintouchSaleRecord.data, JSONB).op("-")("raw"),
            ).where(
                WintouchSaleRecord.enterprise_id == source,
                WintouchSaleRecord.store_id.in_(store_ids),
                WintouchSaleRecord.kind.in_(["saft_invoice", "saft_receipt"]),
                WintouchSaleRecord.active.is_(True),
            )
        )
    ]
    invoices = {data["document"]: (row, data) for row, data in rows if data["kind"] == "invoice"}
    suppressed, ambiguous = set(), set()
    related_by_document = defaultdict(list)
    warnings = []
    for row, data in rows:
        if data["kind"] != "receipt" or data["status"] != "N":
            continue
        for number in data["references"]:
            related_by_document[number].append(row.id)
        inline = [
            invoices[number]
            for number in data["references"]
            if number in invoices and invoices[number][1]["payments"]
        ]
        if inline:
            if (
                len(data["references"]) == 1
                and len(inline) == 1
                and data["payments"]
                and sum(p["amount_cents"] for p in inline[0][1]["payments"])
                == sum(p["amount_cents"] for p in data["payments"])
                and data["direction"] == inline[0][1]["direction"]
            ):
                suppressed.add(inline[0][0].id)
            else:
                ambiguous.add(row.id)
                ambiguous.update(invoice.id for invoice, _ in inline)
    # Multiple receipts referring to the same inline-paid invoice can themselves
    # be duplicates. Leave the entire overlap uncounted until source correction.
    for number, (invoice, data) in invoices.items():
        if data["payments"]:
            related = related_by_document[number]
            if len(related) > 1:
                ambiguous.update([invoice.id, *related])
    settings = db.get(CompanySetup, "default")
    mapping = (
        json.loads(settings.settings or "{}").get("saft_payment_mapping", {}) if settings else {}
    )
    names = methods(db, active_only=True)
    billing, by_method, payments, unavailable = (
        defaultdict(lambda: {"documents": 0, "gross_cents": 0, "net_cents": 0, "tax_cents": 0}),
        defaultdict(int),
        [],
        [],
    )
    for row, data in rows:
        eligible = data["status"] == "N" and (
            data["kind"] == "receipt" or data["type"] in INVOICE_TYPES
        )
        if data["kind"] == "invoice" and start <= data["date"] <= end and eligible:
            total = billing[(data["date"], row.store_id, data["type"])]
            total["documents"] += 1
            for field in ("gross_cents", "net_cents", "tax_cents"):
                total[field] += data[field] * data["direction"]
        if not eligible:
            continue
        reason = ""
        if row.id in ambiguous:
            reason = "Sobreposição ambígua entre pagamentos da fatura e recibos; excluídos para evitar duplicação."
        elif row.id in suppressed:
            reason = "Pagamento representado no recibo associado; não contado novamente na fatura."
        elif not data["payments"]:
            reason = "Informação de recebimento indisponível."
        elif not data["direction"]:
            reason = "Recibo com débitos e créditos sem sentido líquido identificável."
        if reason:
            if start <= data["date"] <= end:
                unavailable.append({"id": row.id, "document": data["document"], "reason": reason})
            continue
        for index, payment in enumerate(data["payments"]):
            if not payment["date"]:
                if start <= data["date"] <= end:
                    unavailable.append(
                        {
                            "id": row.id,
                            "document": data["document"],
                            "reason": "Pagamento sem data identificável; não atribuído ao dia da fatura.",
                        }
                    )
                continue
            if not start <= payment["date"] <= end:
                continue
            key = mapping.get(payment["code"])
            if key not in names:
                key = None
            amount = payment["amount_cents"] * data["direction"]
            by_method[(payment["date"], row.store_id, key, payment["code"])] += amount
            payments.append(
                {
                    "record_id": row.id,
                    "payment_index": index,
                    "import_id": data["import_id"],
                    "document": data["document"],
                    "date": payment["date"],
                    "code": payment["code"],
                    "method": key,
                    "amount_cents": amount,
                    "origin": "SAF-T",
                }
            )
    if ambiguous:
        warnings.append(
            "Existem sobreposições ambíguas, excluídas dos recebimentos. Consulte o detalhe e corrija a origem."
        )
    return {
        "origin": "SAF-T",
        "start": start,
        "end": end,
        "store_ids": store_ids,
        "billing": [
            {"date": key[0], "store_id": key[1], "type": key[2], **value}
            for key, value in sorted(billing.items())
        ],
        "receipts": [
            {
                "date": key[0],
                "store_id": key[1],
                "method": key[2],
                "code": key[3],
                "name": names.get(key[2], "Por classificar"),
                "amount_cents": value,
            }
            for key, value in by_method.items()
        ],
        "payments": payments,
        "unavailable": unavailable,
        "warnings": warnings,
        "billing_total_cents": sum(value["gross_cents"] for value in billing.values()),
        "receipts_total_cents": sum(by_method.values()),
        "limitations": "Referência do estabelecimento, sem identificação da gaveta. Faturação e recebimentos são distintos. Os valores não alteram o realizado manual nem os movimentos da caixa. Códigos não associados ficam por classificar.",
    }
