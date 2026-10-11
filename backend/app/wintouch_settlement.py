"""Explicit settlement creation; never retry an ambiguous remote write."""
from uuid import uuid4
from decimal import Decimal

import httpx
from fastapi import HTTPException
from .wintouch_sales import Reader, amount, payment_record
from .wintouch_sync import SyncError


def options(reader, direction):
    types = [r for r in reader.collection("document_types")
             if r.get("Class") == (18 if direction == 1 else 38)
             and r.get("CashFlowMode") == direction]
    ids = {r["ID"] for r in types}
    series = [r for r in reader.collection("document_series", "Serie") if r.get("DocumentTypeID") in ids]
    states = {r["ID"]: r for r in reader.collection("credit_account_states")}
    methods = []
    for method in reader.collection("payment_methods"):
        try:
            if payment_record({"PaymentMethodID": method["ID"], "Amount": "1"},
                              {"occurred_at": "2026-01-01"}, {method["ID"]: method}, states):
                methods.append(method)
        except SyncError:
            continue
    return {"types": [{"id": r["ID"], "name": r.get("Name") or r.get("Code")} for r in types],
            "series": [{"id": r["ID"], "type_id": r["DocumentTypeID"],
                        "name": r.get("Code") or (r.get("Serie") or {}).get("Code") or r["ID"]}
                       for r in series],
            "methods": [{"id": r["ID"], "name": r["Name"],
                         "requires_number": r.get("PaymentMethodType") == 7 or r.get("SaftPaymentMechanism") == "CH"}
                        for r in methods]}


def payload(reader, pending, data, direction, settlement_id):
    catalogs = options(reader, direction)
    if data.document_type_id not in {r["id"] for r in catalogs["types"]}:
        raise HTTPException(422, "Documento de liquidação inválido.")
    if not any(r["id"] == data.document_serie_id and r["type_id"] == data.document_type_id for r in catalogs["series"]):
        raise HTTPException(422, "Série inválida para este documento.")
    if data.payment_method_id not in {r["id"] for r in catalogs["methods"]}:
        raise HTTPException(422, "Meio de pagamento não confirmado como liquidação.")
    method = next(r for r in catalogs["methods"] if r["id"] == data.payment_method_id)
    payment_number = getattr(data, "payment_number", "").strip()
    if method["requires_number"] and not payment_number:
        raise HTTPException(422, "Introduza o número do cheque.")
    value = amount(data.amount.replace(",", "."))
    outstanding = amount(pending["StateValue"])
    if not 0 < value <= outstanding:
        raise HTTPException(422, "O valor deve ser positivo e não exceder o saldo atual Wintouch.")
    if amount(pending.get("StateWithholdedValue") or 0):
        raise HTTPException(422, "Este documento tem retenções; liquide-o na plataforma Wintouch.")
    document = reader.get("product_documents/" + pending["SourceProductDocumentMasterID"])
    if document.get("DeletedDate"):
        raise HTTPException(409, "O documento foi anulado na Wintouch.")
    currencies = {r["ID"]: r for r in reader.collection("currencies")}
    if currencies.get(document.get("CurrencyID"), {}).get("Code") != "EUR":
        raise HTTPException(422, "A liquidação suporta apenas documentos em euros.")
    stamp = data.occurred_at.isoformat()
    return {"ID": settlement_id, "DocumentTypeID": data.document_type_id,
            "DocumentSerieID": data.document_serie_id, "Date": stamp,
            "EntityID": pending["EntityID"], "CurrencyID": document["CurrencyID"],
            "SectorID": document.get("SectorID"), "Amount": Decimal(value) / 100,
            "ExchangeRate": 1, "Discount": 0, "WithHolded": 0,
            "Notes": data.notes,
            "Payments": [{"ID": str(uuid4()), "SettlementMasterID": settlement_id,
                          "PaymentMethodID": data.payment_method_id, "Amount": Decimal(value) / 100,
                          "PaymentMethodNumber": payment_number or None,
                          "PaymentMethodDate": stamp, "PaymentMethodDueDate": stamp}],
            "SettlementDetails": [{"ID": str(uuid4()), "SettlementMasterID": settlement_id,
                "Row": 0, "SourceProductDocumentMasterID": pending["SourceProductDocumentMasterID"],
                "PreviousCreditAccountStateID": pending["CreditAccountStateID"],
                "Installment": pending.get("Installment"), "SourcePaymentID": pending.get("SourcePaymentID"),
                "PayableReceivableDueDate": pending["DueDate"], "PreviousStateValue": Decimal(outstanding) / 100,
                "Amount": Decimal(value) / 100, "Discount": 0, "WithHolded": 0,
                "Version": pending.get("Version")}]}


def encode_payload(value):
    """Emit decimal JSON numbers without converting money to float."""
    import json
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + ":" + encode_payload(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(encode_payload(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def rejection_message(response):
    """Wintouch validation errors may be lists or nested field errors."""
    def messages(value):
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [item for child in value for item in messages(child)]
        if isinstance(value, dict):
            preferred = [k for k in value if k.lower() in {
                "message", "messages", "error", "errors", "detail", "details",
                "modelstate", "exceptionmessage", "errormessage", "description", "title",
            }]
            return [item for key in (preferred or list(value)) for item in messages(value[key])]
        return []
    try:
        found = list(dict.fromkeys(messages(response.json())))
    except ValueError:
        found = []
    if found:
        return " · ".join(found)[:2000]
    text = response.text.strip()
    if text and not text.startswith("<"):
        return text[:2000]
    return f"Pedido recusado (HTTP {response.status_code}); a origem não devolveu uma descrição do erro."
