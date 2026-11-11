"""Análise comercial de Faturação e recebimentos: clientes → documentos → artigos → recebimentos. Só leitura.

Origem dos documentos (a mesma do Dashboard, `dashboard.sales_source`):

- modo local: SAF-T de vendas importado (cabeçalhos, linhas, clientes, produtos e recibos);
- modo integrado: vendas Wintouch sincronizadas (documentos, linhas quando sincronizadas, pagamentos, recibos e saldos
  em aberto).

Recebido de cada documento («melhor fonte, sem somar»):

- local: recibos do SAF-T que liquidam o documento + pagamento indicado no próprio documento (FR/FS) ou, se for maior,
  o justificado na conferência com recebimentos registados no GPGest. As duas fontes descrevem o mesmo dinheiro e
  nunca se somam;
- integrado: total do documento − saldo em aberto no ERP (última sincronização).

Vencido só quando há data de vencimento (ERP, ou prazo de pagamento da ficha do cliente); sem prazo, «Pendente».
Notas de crédito subtraem na faturação e não ficam por receber. Documentos anulados ficam fora dos totais. Nada aqui
escreve: consultar não altera documentos fiscais nem dados sincronizados.
"""

import json
import time as clock
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .access import current
from .billing_conference import (
    INVOICE_TYPES,
    STATUS_LABELS,
    conference_usage,
    saft_source,
    scope_for,
)
from .cash_sheet import day as local_day
from .db import db_session
from .models import (
    ArchiveDocument,
    ArchiveLink,
    BankAccount,
    BankBookEntry,
    BillingConference,
    BillingConferenceLine,
    BusinessEntity,
    Movement,
    PaymentTerm,
    Reconciliation,
    ReconciliationLine,
    Register,
    StockArticle,
    StockEntry,
    StockSettings,
    WintouchEntity,
    WintouchProduct,
    WintouchSaleRecord,
)

router = APIRouter()
TYPE_LABELS = {"FT": "Fatura", "FS": "Fatura simplificada", "FR": "Fatura-recibo", "ND": "Nota de débito", "NC": "Nota de crédito",
               "VD": "Venda a dinheiro"}
STATES = {"pago": "Pago", "parcial": "Parcialmente pago", "pendente": "Pendente", "vencido": "Vencido", "anulado": "Anulado",
          "nota_credito": "Nota de crédito", "excluido": "Excluído"}
OPEN_STATES = ("parcial", "pendente", "vencido")
FINAL_CONSUMER = "999999990"
LISBON = ZoneInfo("Europe/Lisbon")
CACHE: dict = {}
CACHE_SECONDS = 30


def fail(message, status=422):
    raise HTTPException(status, message)


def items(value):
    return value if isinstance(value, list) else [] if value in (None, "", {}) else [value]


def number(value):
    try:
        return Decimal(str(value).strip() or "0")
    except (InvalidOperation, TypeError):
        return Decimal(0)


def cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fold(text):
    from .rubric_nature import fold as base
    return base(text)


def today():
    return datetime.now(LISBON).date()


def mode_of(db):
    from .dashboard import sales_source
    return sales_source(db)[0]


# ---------------------------------------------------------------- dados normalizados (com cache curta)


def dataset(db, stores, start, end):
    """Documentos normalizados do período, com linhas, recebimentos e clientes. Cache de 30 s por empresa e filtro,
    invalidada quando muda a sincronização, a importação ou a conferência."""
    mode = mode_of(db)
    company = str(db.info.get("company", {}).get("id") or db.info.get("company", {}).get("nif") or "")
    version = (
        db.scalar(select(func.count(WintouchSaleRecord.id))),
        str(db.scalar(select(func.max(WintouchSaleRecord.synced_at)))),
        db.scalar(select(func.count(BillingConferenceLine.id))),
        str(db.scalar(select(func.max(BillingConference.undone_at)))),
    )
    key = (company, mode, tuple(sorted(stores)), str(start), str(end))
    cached = CACHE.get(key)
    if cached and cached[0] == version and clock.monotonic() - cached[1] < CACHE_SECONDS:
        return cached[2]
    data = (erp_dataset if mode == "erp" else saft_dataset)(db, stores, str(start), str(end))
    data["mode"] = mode or "none"
    if len(CACHE) > 64:
        CACHE.clear()
    CACHE[key] = (version, clock.monotonic(), data)
    return data


def entities_by_nif(db):
    result = {}
    for entity in db.scalars(select(BusinessEntity).where(BusinessEntity.removed.is_(False))):
        nif = (entity.tax_number or "").removeprefix("PT").strip()
        if nif and (nif not in result or entity.is_customer):
            result[nif] = entity
    return result


def due_days(db):
    terms = {t.id: t.days for t in db.scalars(select(PaymentTerm))}
    return lambda entity: entity.payment_days if entity and entity.payment_days is not None else terms.get(entity.payment_term_id) if entity else None


def finish(doc, due, received):
    """Recebido, em dívida, vencimento e estado de um documento."""
    doc["due_date"] = due
    if doc["status"] != "normal":
        doc.update(received_cents=0, pending_cents=0, state="anulado" if doc["status"] == "anulado" else "excluido")
        return doc
    if doc["direction"] < 0:
        doc.update(received_cents=0, pending_cents=0, state="nota_credito")
        return doc
    received = max(0, min(doc["gross_cents"], received))
    pending = doc["gross_cents"] - received
    overdue = bool(pending and due and due < today().isoformat())
    doc.update(received_cents=received, pending_cents=pending,
               state="pago" if not pending else "vencido" if overdue else "parcial" if received else "pendente")
    return doc


def saft_lines(raw, direction):
    lines = []
    for index, line in enumerate(items(raw.get("Line"))):
        if not isinstance(line, dict):
            continue
        amount = number(line.get("CreditAmount") or line.get("DebitAmount"))
        tax = line.get("Tax") if isinstance(line.get("Tax"), dict) else {}
        rate = number(tax.get("TaxPercentage")) if tax.get("TaxPercentage") not in (None, "") else None
        net = cents(amount) * direction
        vat = cents(amount * rate / 100) * direction if rate is not None else None
        lines.append({
            "index": index + 1, "code": str(line.get("ProductCode") or ""), "description": str(line.get("ProductDescription") or line.get("Description") or ""),
            "quantity": str(number(line.get("Quantity")) * direction), "unit": str(line.get("UnitOfMeasure") or ""),
            "unit_price": str(number(line.get("UnitPrice"))), "discount_cents": cents(number(line.get("SettlementAmount"))),
            "vat_rate": str(rate) if rate is not None else None, "net_cents": net, "vat_cents": vat,
            "gross_cents": net + vat if vat is not None else None,
        })
    return lines


def saft_dataset(db, stores, start, end):
    source = saft_source(db)
    customers, products, docs = {}, {}, []
    if not source or not stores:
        return {"documents": docs, "customers": customers, "products": products, "receipts": [], "notes": []}
    for row in db.scalars(select(WintouchSaleRecord).where(WintouchSaleRecord.enterprise_id == source, WintouchSaleRecord.active.is_(True),
                                                           WintouchSaleRecord.kind.in_(("saft_customer", "saft_product")))):
        raw = json.loads(row.data).get("raw") or {}
        if row.kind == "saft_customer":
            address = raw.get("BillingAddress") if isinstance(raw.get("BillingAddress"), dict) else {}
            code = str(raw.get("CustomerID") or "")
            customers[code] = {"code": code, "name": str(raw.get("CompanyName") or code), "nif": str(raw.get("CustomerTaxID") or ""),
                               "address": ", ".join(str(v) for v in (address.get("AddressDetail"), address.get("PostalCode"), address.get("City")) if v),
                               "phone": str(raw.get("Telephone") or ""), "email": str(raw.get("Email") or "")}
        else:
            code = str(raw.get("ProductCode") or "")
            products[code] = {"code": code, "description": str(raw.get("ProductDescription") or ""), "family": str(raw.get("ProductGroup") or ""),
                              "type": str(raw.get("ProductType") or "")}
    entities, days = entities_by_nif(db), due_days(db)
    for row in db.scalars(select(WintouchSaleRecord).where(
            WintouchSaleRecord.enterprise_id == source, WintouchSaleRecord.kind == "saft_invoice", WintouchSaleRecord.active.is_(True),
            WintouchSaleRecord.store_id.in_(stores), WintouchSaleRecord.document_date >= start, WintouchSaleRecord.document_date <= end)):
        data = json.loads(row.data)
        raw = data.get("raw") or {}
        status = data.get("status", "")
        eligible = status == "N" and data.get("type") in INVOICE_TYPES
        customer = customers.get(data.get("customer_id", "")) or {"code": data.get("customer_id", ""), "name": data.get("customer_id") or "Cliente não indicado", "nif": ""}
        direction = data.get("direction", 1) or 1
        totals = raw.get("DocumentTotals") if isinstance(raw.get("DocumentTotals"), dict) else {}
        settlement = totals.get("Settlement") if isinstance(totals.get("Settlement"), dict) else {}
        lines = saft_lines(raw, direction)
        docs.append({
            "id": row.id, "origin": "saft", "store_id": row.store_id, "date": data["date"], "type": data["type"],
            "type_label": TYPE_LABELS.get(data["type"], data["type"]), "series": data.get("series", ""), "number": data.get("number", ""),
            "document": data["document"], "key": f"saft:{data['type']}:{data.get('series', '')}:{data.get('number', '')}",
            "customer_key": "c:" + customer["code"], "customer_code": customer["code"], "customer_name": customer["name"], "customer_nif": customer["nif"],
            "status": "normal" if eligible else "anulado" if status == "A" else "excluido",
            "status_label": "Normal" if status == "N" else STATUS_LABELS.get(status, "Excluído"),
            "direction": direction, "net_cents": data["net_cents"], "tax_cents": data["tax_cents"], "gross_cents": data["gross_cents"],
            "discount_cents": cents(number(settlement.get("SettlementAmount"))) + sum(line["discount_cents"] for line in lines),
            "payment_terms": "Pronto pagamento" if data.get("payments") else "",
            "immediate": [{"date": p.get("date") or data["date"], "amount_cents": p["amount_cents"], "code": p["code"]} for p in data.get("payments", [])],
            "lines": lines, "line_count": data.get("line_count", len(lines)),
        })
    # Recibos do SAF-T (Payments): o valor de cada linha liquida o documento de origem; o total com IVA do recibo
    # reparte-se pelas linhas na proporção do valor sem IVA.
    receipts, settled = [], defaultdict(list)
    numbers = {doc["document"] for doc in docs}
    for row in db.scalars(select(WintouchSaleRecord).where(
            WintouchSaleRecord.enterprise_id == source, WintouchSaleRecord.kind == "saft_receipt", WintouchSaleRecord.active.is_(True),
            WintouchSaleRecord.store_id.in_(stores), WintouchSaleRecord.document_date >= start)):
        data = json.loads(row.data)
        if data.get("status") == "A":
            continue
        raw = data.get("raw") or {}
        parts = []
        for line in items(raw.get("Line")):
            if not isinstance(line, dict):
                continue
            amount = number(line.get("CreditAmount") or line.get("DebitAmount"))
            for source_doc in items(line.get("SourceDocumentID")):
                origin = source_doc.get("OriginatingON") if isinstance(source_doc, dict) else None
                if origin:
                    parts.append((str(origin), amount))
        weight = sum(amount for _, amount in parts) or Decimal(1)
        left, allocated = data["gross_cents"], []
        for index, (origin, amount) in enumerate(parts):
            value = left if index == len(parts) - 1 else cents(Decimal(data["gross_cents"]) * amount / weight / 100)
            left -= value
            allocated.append({"document": origin, "amount_cents": value})
        methods = ", ".join(dict.fromkeys(p["code"] for p in data.get("payments", []) if p.get("code")))
        receipt = {"id": row.id, "source": "saft_receipt", "source_label": "Recibo (SAF-T)", "document": data["document"], "date": data["date"],
                   "amount_cents": data["gross_cents"], "method": methods, "account": "", "reference": data["document"],
                   "reconciliation": None, "store_id": row.store_id, "documents": allocated}
        receipts.append(receipt)
        for part in allocated:
            if part["document"] in numbers:
                settled[part["document"]].append({**receipt, "allocated_cents": part["amount_cents"]})
    # Conferência com os recebimentos registados no GPGest (caixa e banco).
    used, _ = conference_usage(db)
    conference = conference_receipts(db, [doc["id"] for doc in docs])
    for doc in docs:
        entity = entities.get(doc["customer_nif"]) if doc["customer_nif"] and doc["customer_nif"] != FINAL_CONSUMER else None
        term = days(entity)
        due = (date.fromisoformat(doc["date"]) + timedelta(days=term)).isoformat() if term is not None and not doc["immediate"] else None
        if term is not None and not doc["payment_terms"]:
            doc["payment_terms"] = f"{term} dia(s)" if term else "Pronto pagamento"
        doc["entity_id"] = entity.id if entity else None
        doc["receipts"] = ([{"id": f"{doc['id']}:{i}", "source": "documento", "source_label": "Pagamento no documento", "document": doc["document"],
                             "date": p["date"], "amount_cents": p["amount_cents"], "allocated_cents": p["amount_cents"], "method": p["code"],
                             "account": "", "reference": doc["document"], "reconciliation": None} for i, p in enumerate(doc["immediate"])]
                          + settled.get(doc["document"], []))
        from_saft = sum(r["allocated_cents"] for r in doc["receipts"])
        justified = used["document", "saft", doc["id"]]
        doc["conference"] = conference.get(doc["id"], [])
        doc["received_source"] = "conferencia" if justified > from_saft else "saft" if from_saft else ""
        finish(doc, due, max(from_saft, justified))
        dates = [r["date"] for r in doc["receipts"]] + [c["date"] for c in doc["conference"]]
        doc["last_receipt_date"] = max(dates) if dates and doc["received_cents"] else None
    return {"documents": docs, "customers": customers, "products": products, "receipts": receipts,
            "notes": ["Recebido: recibos e pagamentos indicados no SAF-T ou, se for maior, o justificado na conferência com os recebimentos "
                      "registados no GPGest (nunca somados).", "Vencido só com prazo de pagamento na ficha do cliente."]}


def conference_receipts(db, doc_ids):
    """{documento: [recebimentos GPGest associados na conferência]} (só conferências individuais ativas)."""
    if not doc_ids:
        return {}
    pairs = list(db.execute(select(BillingConferenceLine.conference_id, BillingConferenceLine.source_id, BillingConferenceLine.amount_cents)
                            .join(BillingConference).where(BillingConference.undone_at.is_(None), BillingConference.kind == "individual",
                                                           BillingConferenceLine.side == "document", BillingConferenceLine.source_id.in_(doc_ids))))
    if not pairs:
        return {}
    conferences = {c for c, _, _ in pairs}
    lines = list(db.scalars(select(BillingConferenceLine).where(BillingConferenceLine.conference_id.in_(conferences), BillingConferenceLine.side == "receipt")))
    moves = {m.id: m for m in db.scalars(select(Movement).where(Movement.id.in_([line.source_id for line in lines if line.source == "movement"])))}
    books = {b.id: b for b in db.scalars(select(BankBookEntry).where(BankBookEntry.id.in_([line.source_id for line in lines if line.source == "book"])))}
    registers = {r.id: r for r in db.scalars(select(Register))}
    accounts = {a.id: a for a in db.scalars(select(BankAccount))}
    reconciled = {(s, i) for s, i in db.execute(select(ReconciliationLine.source, ReconciliationLine.source_id).join(Reconciliation).where(
        Reconciliation.undone_at.is_(None), ReconciliationLine.source_id.in_([line.source_id for line in lines])))}
    created = dict(db.execute(select(BillingConference.id, BillingConference.created_at).where(BillingConference.id.in_(conferences))).all())
    result = defaultdict(list)
    for conference_id, doc_id, justified in pairs:
        own = [line for line in lines if line.conference_id == conference_id]
        weight, left = sum(line.amount_cents for line in own) or 1, justified
        for index, line in enumerate(own):
            share = left if index == len(own) - 1 else round(justified * line.amount_cents / weight)
            left -= share
            if line.source == "movement" and (move := moves.get(line.source_id)):
                from .billing_conference import movement_store
                _, register = movement_store(db, move, registers)
                account = accounts.get(move.bank_account_id) if move.bank_account_id else None
                where = registers[register].name if register in registers else f"{account.bank} · {account.name}" if account else "Caixa"
                state = "caixa" if move.payment == "numerario" else "reconciliado" if ("movement", move.id) in reconciled else "por_reconciliar"
                item = {"date": local_day(move.occurred_at), "amount_cents": move.amount_cents, "method": move.payment, "account": where,
                        "reference": move.document or move.reference or "", "reconciliation": state, "movement_id": move.id}
            elif line.source == "book" and (entry := books.get(line.source_id)):
                account = accounts.get(entry.account_id)
                item = {"date": entry.movement_date, "amount_cents": abs(entry.amount_cents), "method": "transferencia",
                        "account": f"{account.bank} · {account.name}" if account else "Banco", "reference": entry.reference or entry.description,
                        "reconciliation": "reconciliado" if entry.source_bank_id or ("book", entry.id) in reconciled else "por_reconciliar",
                        "movement_id": entry.id}
            else:
                continue
            result[doc_id].append({"id": f"{conference_id}:{line.source_id}", "source": "conferencia", "source_label": "Recebimento GPGest (conferência)",
                                   "document": "", "allocated_cents": share, "conference_date": created[conference_id].isoformat()[:10]
                                   if created.get(conference_id) else None, **item})
    return result


def erp_dataset(db, stores, start, end):
    docs, receipts = [], []
    if not stores:
        return {"documents": docs, "customers": {}, "products": {}, "receipts": receipts, "notes": []}
    wt_entities = {}
    for entity in db.scalars(select(WintouchEntity)):
        nif = (entity.tax_number or "").removeprefix("PT").strip()
        if nif:
            wt_entities.setdefault(nif, entity)
    products = {p.external_id: p for p in db.scalars(select(WintouchProduct))}
    entities = entities_by_nif(db)
    open_balance = {}
    for row in db.scalars(select(WintouchSaleRecord).where(WintouchSaleRecord.kind == "previsto", WintouchSaleRecord.active.is_(True),
                                                           WintouchSaleRecord.store_id.in_(stores))):
        data = json.loads(row.data)
        if data.get("document_group") == "vendas" and data.get("source_document_id"):
            current = open_balance.setdefault(data["source_document_id"], {"amount_cents": 0, "due_date": data.get("due_date")})
            current["amount_cents"] += int(data.get("amount_cents") or 0)
    payments = defaultdict(list)
    for row in db.scalars(select(WintouchSaleRecord).where(WintouchSaleRecord.kind == "real", WintouchSaleRecord.active.is_(True),
                                                           WintouchSaleRecord.store_id.in_(stores), WintouchSaleRecord.document_date >= start)):
        data = json.loads(row.data)
        if data.get("document_group") != "vendas":
            continue
        item = {"id": row.id, "source": "erp", "source_label": "Recibo (ERP)" if data.get("source_invoice_ids") else "Pagamento no documento (ERP)",
                "document": data.get("document", ""), "date": str(data.get("occurred_at") or row.document_date)[:10],
                "amount_cents": int(data.get("amount_cents") or 0) * (1 if (data.get("direction") or 1) > 0 else -1),
                "method": data.get("payment", ""), "account": "", "reference": data.get("document_reference") or data.get("document", ""),
                "reconciliation": None, "store_id": row.store_id, "targets": data.get("source_invoice_ids") or [data.get("source_document_id")]}
        receipts.append(item)
        for target in item["targets"]:
            payments[target].append(item)
    if receipts:
        reconciled = set(db.scalars(select(ReconciliationLine.source_id).join(Reconciliation).where(
            Reconciliation.undone_at.is_(None), ReconciliationLine.source == "wintouch", ReconciliationLine.source_id.in_([r["id"] for r in receipts]))))
        for item in receipts:
            item["reconciliation"] = "reconciliado" if item["id"] in reconciled else "caixa" if "numer" in fold(item["method"]) else "por_reconciliar"
    for row in db.scalars(select(WintouchSaleRecord).where(
            WintouchSaleRecord.kind == "documento", WintouchSaleRecord.active.is_(True), WintouchSaleRecord.store_id.in_(stores),
            WintouchSaleRecord.document_date >= start, WintouchSaleRecord.document_date <= end)):
        data = json.loads(row.data)
        if data.get("document_group") != "vendas":
            continue
        nif = str(data.get("entity_nif") or "").strip()
        wt = wt_entities.get(nif)
        name = data.get("entity") or "Cliente não indicado"
        direction = data.get("direction") or 1
        gross = int(data.get("amount_cents") or 0)
        net = data.get("net_cents")
        label = data.get("document", "")
        series, _, num = label.rpartition("/")
        lines = []
        for index, line in enumerate(data.get("lines") or []):
            product = products.get(line.get("product_id"))
            quantity = number(line.get("quantity"))
            value = cents(number(line.get("net")))
            lines.append({"index": index + 1, "code": product.code if product else str(line.get("product_id") or ""),
                          "description": product.name if product else "Artigo do ERP", "quantity": str(quantity * direction),
                          "unit": product.unit if product and product.unit else "", "unit_price": str((number(line.get("net")) / quantity).quantize(Decimal("0.0001")))
                          if quantity else None, "discount_cents": None, "vat_rate": None, "net_cents": value * direction, "vat_cents": None,
                          "gross_cents": None, "product_id": line.get("product_id")})
        entity = entities.get(nif) if nif and nif != FINAL_CONSUMER else None
        doc = {
            "id": row.id, "origin": "erp", "store_id": row.store_id, "date": row.document_date, "type": data.get("type", ""),
            "type_label": TYPE_LABELS.get(data.get("type", ""), data.get("type", "")), "series": series, "number": num or label,
            "document": label, "key": f"erp:{data.get('source_document_id')}", "source_document_id": data.get("source_document_id"),
            "customer_key": "n:" + nif if nif and nif != FINAL_CONSUMER else "e:" + fold(name), "customer_code": (wt.code if wt else "") or "",
            "customer_name": name, "customer_nif": nif, "status": "anulado" if data.get("cancelled") else "normal",
            "status_label": "Anulado" if data.get("cancelled") else "Normal", "direction": direction,
            "net_cents": net if net is not None else None, "tax_cents": data.get("tax_cents"), "gross_cents": gross,
            "discount_cents": None, "payment_terms": "", "lines": lines, "line_count": len(lines), "entity_id": entity.id if entity else None,
            "conference": [],
        }
        opened = open_balance.get(data.get("source_document_id"))
        doc["receipts"] = [{**p, "allocated_cents": p["amount_cents"] if len(p["targets"]) == 1 else None} for p in payments.get(data.get("source_document_id"), [])]
        due = opened["due_date"] if opened else None
        received = gross - opened["amount_cents"] if opened else gross
        doc["received_source"] = "erp"
        doc["payment_terms"] = "A crédito" if opened else "Liquidado"
        finish(doc, due, received)
        dates = [r["date"] for r in doc["receipts"]]
        doc["last_receipt_date"] = max(dates) if dates and doc["received_cents"] else None
        docs.append(doc)
    customers = {}
    for doc in docs:
        wt = wt_entities.get(doc["customer_nif"])
        customers.setdefault(doc["customer_key"], {"code": doc["customer_code"], "name": doc["customer_name"], "nif": doc["customer_nif"],
                                                   "address": (wt.address if wt else "") or "", "phone": (wt.phone if wt else "") or "",
                                                   "email": (wt.email if wt else "") or ""})
    return {"documents": docs, "customers": customers, "products": {p.code: {"code": p.code, "description": p.name, "family": "", "type": ""}
                                                                    for p in products.values()},
            "receipts": receipts,
            "notes": ["Recebido e em dívida segundo o saldo em aberto do ERP na última sincronização; vencimento do ERP.",
                      "As linhas dos documentos só existem quando a sincronização das vendas inclui os artigos."]}


# ---------------------------------------------------------------- vistas


def receipt_key(receipt):
    return receipt["source"] + ":" + str(receipt.get("movement_id") or receipt["id"])


def allocated(receipt):
    return receipt["allocated_cents"] if receipt.get("allocated_cents") is not None else receipt.get("amount_cents") or 0


def received_by_month(docs):
    """Recebimentos por mês (data do recebimento) dos documentos do período; cada registo conta uma vez quando o
    valor por documento não é conhecido (recibo do ERP de várias faturas). Usa a mesma fonte do recebido do documento."""
    result, seen = defaultdict(int), set()
    for doc in counted(docs):
        if doc["direction"] < 0:
            continue
        for receipt in doc["conference"] if doc.get("received_source") == "conferencia" else doc["receipts"]:
            if receipt.get("allocated_cents") is None:
                if receipt_key(receipt) in seen:
                    continue
                seen.add(receipt_key(receipt))
            result[(receipt.get("date") or "")[:7]] += allocated(receipt)
    return result


def customer_info(data, doc_or_key):
    key = doc_or_key if isinstance(doc_or_key, str) else doc_or_key["customer_key"]
    if data["mode"] != "erp" and key.startswith("c:"):
        return data["customers"].get(key[2:]) or {"code": key[2:], "name": key[2:], "nif": ""}
    return data["customers"].get(key) or {}


def row_view(doc):
    keep = ("id", "origin", "store_id", "date", "type", "type_label", "series", "number", "document", "key", "customer_key", "customer_code",
            "customer_name", "customer_nif", "status", "status_label", "direction", "net_cents", "tax_cents", "gross_cents", "received_cents",
            "pending_cents", "due_date", "state", "line_count", "last_receipt_date")
    return {k: doc.get(k) for k in keep} | {"state_label": STATES[doc["state"]]}


def counted(docs):
    return [d for d in docs if d["status"] == "normal"]


def amount_of(text):
    value = (text or "").strip().replace("€", "").replace(" ", "")
    value = value.replace(".", "").replace(",", ".") if "," in value else value
    try:
        return cents(Decimal(value)) if value and any(c.isdigit() for c in value) and all(c.isdigit() or c in ".-" for c in value) else None
    except InvalidOperation:
        return None


def matches(doc, q):
    if not q:
        return True
    text = fold(q)
    amount = amount_of(q)
    if amount is not None and amount in (doc["gross_cents"], doc["net_cents"], doc["pending_cents"]):
        return True
    haystack = fold(f"{doc['document']} {doc['customer_name']} {doc['customer_code']} {doc['customer_nif']}")
    if text in haystack:
        return True
    return any(text in fold(f"{line['code']} {line['description']}") for line in doc["lines"])


def filtered(data, q="", state="", customer="", article="", month="", day=""):
    result = []
    for doc in data["documents"]:
        if customer and doc["customer_key"] != customer:
            continue
        if month and not doc["date"].startswith(month) or day and doc["date"] != day:
            continue
        if state == "em_divida" and doc["state"] not in OPEN_STATES or state and state != "em_divida" and doc["state"] != state:
            continue
        if article and not any(line["code"] == article for line in doc["lines"]):
            continue
        if not matches(doc, q):
            continue
        result.append(doc)
    return result


def months_between(start, end):
    keys, year, month = [], int(start[:4]), int(start[5:7])
    while (year, month) <= (int(end[:4]), int(end[5:7])):
        keys.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


def receipt_days(docs):
    """Prazo médio de recebimento (dias entre o documento e o recebimento, ponderado pelo valor)."""
    total = weight = 0
    for doc in docs:
        for receipt in doc["conference"] if doc.get("received_source") == "conferencia" else doc["receipts"]:
            value = allocated(receipt)
            if value > 0 and receipt.get("date"):
                total += value * max(0, (date.fromisoformat(receipt["date"][:10]) - date.fromisoformat(doc["date"])).days)
                weight += value
    return round(total / weight, 1) if weight else None


def summary(docs):
    valid = counted(docs)
    net = sum((d["net_cents"] or 0) * d["direction"] for d in valid)
    gross = sum(d["gross_cents"] * d["direction"] for d in valid)
    invoices = [d for d in valid if d["direction"] > 0]
    return {"net_cents": net, "gross_cents": gross, "received_cents": sum(d["received_cents"] for d in invoices),
            "pending_cents": sum(d["pending_cents"] for d in invoices),
            "overdue_cents": sum(d["pending_cents"] for d in invoices if d["state"] == "vencido"),
            "credit_notes_cents": sum(d["gross_cents"] for d in valid if d["direction"] < 0),
            "documents": len(valid), "invoices": len(invoices), "cancelled": len(docs) - len(valid),
            "customers": len({d["customer_key"] for d in valid}),
            # Ticket médio: valor sem IVA das faturas (sem notas de crédito) ÷ número de faturas.
            "average_ticket_cents": round(sum(d["net_cents"] or 0 for d in invoices) / len(invoices)) if invoices else None,
            "average_receipt_days": receipt_days(invoices),
            "net_missing": sum(1 for d in valid if d["net_cents"] is None)}


def customers_of(docs, data):
    groups = defaultdict(list)
    for doc in docs:
        groups[doc["customer_key"]].append(doc)
    rows = []
    for key, group in groups.items():
        info = customer_info(data, key)
        totals = summary(group)
        rows.append({"key": key, "code": info.get("code") or group[0]["customer_code"], "name": info.get("name") or group[0]["customer_name"],
                     "nif": info.get("nif") or group[0]["customer_nif"], **totals,
                     "last_date": max(d["date"] for d in group), "entity_id": group[0].get("entity_id")})
    return rows


def articles_of(docs, data):
    groups = {}
    for doc in counted(docs):
        for line in doc["lines"]:
            key = line["code"] or line["description"]
            row = groups.setdefault(key, {"code": line["code"], "description": line["description"], "quantity": Decimal(0), "net_cents": 0,
                                          "documents": set(), "customers": set(), "last_date": "", "last_unit_price": None})
            row["quantity"] += number(line["quantity"])
            row["net_cents"] += line["net_cents"]
            row["documents"].add(doc["id"])
            row["customers"].add(doc["customer_key"])
            if doc["date"] >= row["last_date"] and doc["direction"] > 0:
                row["last_date"] = doc["date"]
                row["last_unit_price"] = line["unit_price"]
    rows = []
    for row in groups.values():
        quantity = row["quantity"]
        rows.append({**row, "quantity": str(quantity.normalize() if quantity else 0), "documents": len(row["documents"]), "customers": len(row["customers"]),
                     "average_price_cents": cents(Decimal(row["net_cents"]) / quantity / 100) if quantity else None,
                     "family": (data["products"].get(row["code"]) or {}).get("family", "")})
    return rows


def page(rows, sort, direction, offset, limit, default):
    key = sort or default
    reverse = direction != "asc"
    rows = sorted(rows, key=lambda r: (r.get(key) is None, r.get(key) if not isinstance(r.get(key), str) else fold(r.get(key))), reverse=reverse)
    if reverse:  # Valores em falta sempre no fim.
        rows = [r for r in rows if r.get(key) is not None] + [r for r in rows if r.get(key) is None]
    return rows[offset:offset + limit], len(rows)


def context(db, user, start, end, store_id):
    stores, start, end = scope_for(db, user, store_id, start, end)
    return dataset(db, stores, start, end), start, end


@router.get("/api/sales/overview")
def overview(start: date, end: date, store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    data, start, end = context(db, user, start, end, store_id)
    docs = data["documents"]
    # Recebimentos por mês: pela data do recebimento (documentos do período e recibos que os liquidam).
    by_month = received_by_month(docs)
    months = [{"key": key, **{k: v for k, v in summary([d for d in docs if d["date"].startswith(key)]).items()
                              if k in ("net_cents", "gross_cents", "pending_cents", "documents")}, "received_cents": by_month.get(key, 0)}
              for key in months_between(start, end)]
    customers = sorted(customers_of(counted(docs), data), key=lambda r: -r["net_cents"])
    articles = sorted(articles_of(docs, data), key=lambda r: -r["net_cents"])
    states = defaultdict(lambda: {"documents": 0, "pending_cents": 0, "gross_cents": 0})
    for doc in docs:
        states[doc["state"]]["documents"] += 1
        states[doc["state"]]["pending_cents"] += doc["pending_cents"]
        states[doc["state"]]["gross_cents"] += doc["gross_cents"]
    return {"mode": data["mode"], "kpis": summary(docs), "months": months, "top_customers": customers[:10],
            "top_articles": articles[:10], "has_lines": any(d["lines"] for d in docs),
            "states": [{"key": k, "label": STATES[k], **v} for k, v in states.items()], "notes": data["notes"]}


@router.get("/api/sales/documents")
def documents(start: date, end: date, store_id: str = "", q: str = "", state: str = "", customer: str = "", article: str = "", month: str = "",
              day: str = "", sort: str = "", direction: str = "desc", offset: int = 0, limit: int = 50,
              user=Depends(current), db: Session = Depends(db_session)):
    data, start, end = context(db, user, start, end, store_id)
    rows = filtered(data, q, state, customer, article, month, day)
    shown, total = page([row_view(d) for d in rows], sort, direction, max(0, offset), min(max(limit, 1), 200), "date")
    return {"rows": shown, "total": total, "totals": summary(rows), "offset": offset}


@router.get("/api/sales/customers")
def customers(start: date, end: date, store_id: str = "", q: str = "", sort: str = "", direction: str = "desc", offset: int = 0, limit: int = 50,
              user=Depends(current), db: Session = Depends(db_session)):
    data, start, end = context(db, user, start, end, store_id)
    text = fold(q)
    rows = [r for r in customers_of(counted(data["documents"]), data) if not text or text in fold(f"{r['code']} {r['name']} {r['nif']}")]
    shown, total = page(rows, sort, direction, max(0, offset), min(max(limit, 1), 200), "net_cents")
    return {"rows": shown, "total": total}


@router.get("/api/sales/articles")
def articles(start: date, end: date, store_id: str = "", q: str = "", customer: str = "", sort: str = "", direction: str = "desc",
             offset: int = 0, limit: int = 50, user=Depends(current), db: Session = Depends(db_session)):
    data, start, end = context(db, user, start, end, store_id)
    docs = [d for d in data["documents"] if not customer or d["customer_key"] == customer]
    text = fold(q)
    rows = [r for r in articles_of(docs, data) if not text or text in fold(f"{r['code']} {r['description']}")]
    shown, total = page(rows, sort, direction, max(0, offset), min(max(limit, 1), 200), "net_cents")
    return {"rows": shown, "total": total, "has_lines": any(d["lines"] for d in docs)}


@router.get("/api/sales/receipts")
def receipts(start: date, end: date, store_id: str = "", q: str = "", offset: int = 0, limit: int = 50,
             user=Depends(current), db: Session = Depends(db_session)):
    """Recebimentos que liquidam documentos do período, cada registo uma vez, com os documentos liquidados."""
    data, start, end = context(db, user, start, end, store_id)
    rows = {}
    for doc in counted(data["documents"]):
        for receipt in doc["receipts"] + doc["conference"]:
            key = receipt_key(receipt)
            row = rows.setdefault(key, {**{k: receipt.get(k) for k in ("source", "source_label", "document", "date", "amount_cents", "method", "account",
                                                                        "reference", "reconciliation")}, "id": key, "documents": []})
            row["documents"].append({"id": doc["id"], "document": doc["document"], "customer_name": doc["customer_name"], "customer_key": doc["customer_key"],
                                     "allocated_cents": receipt.get("allocated_cents"), "state": doc["state"]})
    text = fold(q)
    result = [r for r in rows.values() if not text or text in fold(" ".join([r.get("document") or "", r.get("reference") or "", r.get("method") or "",
                                                                             *[f"{d['document']} {d['customer_name']}" for d in r["documents"]]]))]
    result.sort(key=lambda r: r.get("date") or "", reverse=True)
    return {"rows": result[max(0, offset):max(0, offset) + min(max(limit, 1), 200)], "total": len(result),
            "note": "Cada recebimento aparece uma vez, com os documentos que liquida. Um recibo do SAF-T/ERP e um recebimento registado no GPGest "
                    "associado ao mesmo documento descrevem o mesmo dinheiro: o recebido do documento usa a maior das fontes, sem somar."}


# ---------------------------------------------------------------- detalhe (painel com histórico)


def article_link(db, data, code, product_id=None):
    if data["mode"] == "erp":
        product = db.scalar(select(WintouchProduct).where(or_(WintouchProduct.external_id == product_id, WintouchProduct.code == code)).limit(1))
        article = db.scalar(select(StockArticle).where(StockArticle.erp_id == product.id)) if product else None
        return article, product
    settings = db.get(StockSettings, "default")
    mapping = (json.loads(settings.data or "{}").get("saft_mapping") or {}) if settings else {}
    article = db.get(StockArticle, mapping[code]) if mapping.get(code) else db.scalar(select(StockArticle).where(StockArticle.code == code))
    return article, None


def document_detail(db, user, data, identifier):
    doc = next((d for d in data["documents"] if d["id"] == identifier), None)
    if not doc:
        fail("Documento fora do período, do estabelecimento ou sem acesso.", 404)
    info = customer_info(data, doc)
    links = {code: article_link(db, data, code, line.get("product_id"))[0] for line in doc["lines"] for code in [line["code"]] if code}
    lines = [{**line, "article_id": links[line["code"]].id if links.get(line["code"]) else None} for line in doc["lines"]]
    totals = {"net_cents": sum(line["net_cents"] for line in lines), "vat_cents": sum(line["vat_cents"] or 0 for line in lines)}
    number_key = fold(doc["document"]).replace(" ", "")
    originals = list(db.scalars(select(ArchiveLink.document_id).where(ArchiveLink.target_id == doc["id"], ArchiveLink.status == "confirmada")))
    for archived in db.scalars(select(ArchiveDocument).where(func.lower(func.replace(ArchiveDocument.document_number, " ", "")) == number_key)):
        if archived.id not in originals:
            originals.append(archived.id)
    return {
        "view": "document", **row_view(doc), "customer": info, "entity_id": doc.get("entity_id"), "payment_terms": doc.get("payment_terms"),
        "discount_cents": doc.get("discount_cents"), "lines": lines, "lines_totals": totals,
        "lines_match": doc["net_cents"] is None or not lines or abs(abs(totals["net_cents"]) - (doc["net_cents"] or 0)) <= max(2, len(lines)),
        "receipts": doc["receipts"], "conference": doc["conference"], "received_source": doc.get("received_source"),
        "originals": originals,
        "original_note": None if originals else "Sem PDF original no Arquivo digital para este documento. Os dados acima são os do "
                                                 + ("ERP" if data["mode"] == "erp" else "SAF-T") + " (não são uma cópia do original).",
    }


def customer_detail(db, data, history, key, start, end):
    docs = [d for d in data["documents"] if d["customer_key"] == key]
    past = [d for d in history["documents"] if d["customer_key"] == key]
    info = customer_info(data, key) or customer_info(history, key)
    if not docs and not past:
        fail("Cliente sem documentos no período ou sem acesso.", 404)
    sample = (docs or past)[0]
    months = [{"key": k, "net_cents": sum((d["net_cents"] or 0) * d["direction"] for d in counted(past) if d["date"].startswith(k))}
              for k in months_between(history["start"], end)]
    current_articles = {r["code"] for r in articles_of(docs, data)}
    before = [d for d in past if d["date"] < start]
    stopped = sorted([r for r in articles_of(before, history) if r["code"] not in current_articles and r["net_cents"] > 0],
                     key=lambda r: -r["net_cents"])[:15]
    return {"view": "customer", "key": key, "code": info.get("code") or sample["customer_code"], "name": info.get("name") or sample["customer_name"],
            "nif": info.get("nif") or sample["customer_nif"], "address": info.get("address", ""), "phone": info.get("phone", ""), "email": info.get("email", ""),
            "entity_id": sample.get("entity_id"), "totals": summary(docs), "last_date": max((d["date"] for d in counted(past or docs)), default=None),
            "history": months, "documents": [row_view(d) for d in sorted(docs, key=lambda d: d["date"], reverse=True)][:100],
            "articles": sorted(articles_of(docs, data), key=lambda r: -r["net_cents"])[:100], "stopped": stopped}


def article_detail(db, user, data, code):
    docs = [d for d in counted(data["documents"]) if any(line["code"] == code for line in d["lines"])]
    if not docs:
        fail("Artigo sem vendas no período.", 404)
    product_id = next(line.get("product_id") for d in docs for line in d["lines"] if line["code"] == code)
    article, product = article_link(db, data, code, product_id)
    rows = articles_of(docs, data)
    row = next(r for r in rows if r["code"] == code)
    details = json.loads(article.data or "{}") if article else {}
    stock = cost = None
    if article:
        stock = str(db.scalar(select(func.coalesce(func.sum(StockEntry.quantity), 0)).where(StockEntry.article_id == article.id)))
    if user.role in ("administrador", "gestor"):
        if article:
            from .inventory import average, entries
            try:
                cost = average(entries(db, article.id))[1]
            except Exception:  # noqa: BLE001 — custo indisponível não impede a consulta
                cost = None
        if cost is None and product and product.last_cost:
            cost = product.last_cost
    price = Decimal(row["average_price_cents"]) / 100 if row["average_price_cents"] else None
    margin = round(float((price - Decimal(cost)) * 100 / price), 1) if price and cost is not None and price > 0 else None
    lines = []
    for doc in sorted(docs, key=lambda d: d["date"], reverse=True)[:100]:
        for line in doc["lines"]:
            if line["code"] == code:
                lines.append({"document_id": doc["id"], "document": doc["document"], "date": doc["date"], "customer_name": doc["customer_name"],
                              "customer_key": doc["customer_key"], "quantity": line["quantity"], "unit_price": line["unit_price"], "net_cents": line["net_cents"]})
    buyers = sorted(customers_of(docs, data), key=lambda r: -r["net_cents"])[:50]
    product_info = data["products"].get(code) or {}
    return {"view": "article", "code": code, "description": row["description"], "family": details.get("family") or product_info.get("family") or "",
            "brand": details.get("brand") or "", "article_id": article.id if article else None, "stock": stock, "unit": details.get("unit") or "",
            "cost": str(cost) if cost is not None else None, "last_unit_price": row["last_unit_price"], "average_price_cents": row["average_price_cents"],
            "margin_percent": margin, "quantity": row["quantity"], "net_cents": row["net_cents"], "documents": row["documents"], "customers": row["customers"],
            "lines": lines, "buyers": buyers,
            "cost_note": None if cost is not None else "Custo de aquisição desconhecido: a margem não é calculada."}


def receipt_detail(data, identifier):
    """Recebimento → documentos que liquida (com o valor de cada um, quando conhecido)."""
    found, targets = None, []
    for doc in counted(data["documents"]):
        for receipt in doc["receipts"] + doc["conference"]:
            if receipt_key(receipt) == identifier:
                found = found or receipt
                targets.append({**row_view(doc), "allocated_cents": receipt.get("allocated_cents")})
    if not found:
        fail("Recebimento não encontrado no período.", 404)
    return {"view": "receipt", "id": identifier, **{k: found.get(k) for k in ("source", "source_label", "document", "date", "amount_cents", "method",
                                                                               "account", "reference", "reconciliation", "movement_id")},
            "documents": targets}


@router.get("/api/sales/detail")
def detail(start: date, end: date, view: str, store_id: str = "", id: str = "", key: str = "", code: str = "",
           user=Depends(current), db: Session = Depends(db_session)):
    stores, first, last = scope_for(db, user, store_id, start, end)
    data = dataset(db, stores, first, last)
    if view == "document":
        return document_detail(db, user, data, id)
    if view == "customer":
        # Histórico de 12 meses até ao fim do período (e o período, se for mais longo): evolução e artigos que deixou de comprar.
        since = min(end - timedelta(days=364), start).replace(day=1)
        history = {**dataset(db, stores, str(since), last), "start": str(since)}
        return customer_detail(db, data, history, key, first, last)
    if view == "article":
        return article_detail(db, user, data, code)
    if view == "receipt":
        return receipt_detail(data, id)
    fail("Vista inválida.")

