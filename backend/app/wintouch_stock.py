"""Saídas de stock das vendas Wintouch, aplicadas a seguir à importação de vendas.

A Wintouch não controla stock: as quantidades são locais. Cada linha de artigo de uma fatura,
fatura-recibo ou fatura simplificada importada gera uma saída «Venda Wintouch»; uma nota de crédito
devolve ao stock ao custo da venda original, quando essa venda já foi integrada. As regras são as
das vendas SAF-T: só artigos associados, sem lotes, a partir do início do controlo de stock.
Movimentos confirmados não se alteram: um documento anulado gera devoluções ligadas às saídas
(estorno) e um documento alterado depois de integrado fica em conflito para revisão.
"""

import hashlib
import json
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from .inventory import MovementInput, document_key, lock, post, require, settings, stock_from_erp
from .models import (
    ErpLink,
    StockArticle,
    StockEntry,
    StockWarehouse,
    WintouchProduct,
    WintouchSaleRecord,
)

SALE_CLASSES = (3, 5, 40)
CREDIT_CLASS = 6
PREFIX = "wt-stock:"


def line_key(enterprise, line_id, void=False):
    scope = hashlib.sha256(enterprise.encode()).hexdigest()[:12]
    return f"{PREFIX}{scope}:{'void:' if void else ''}{line_id}"


def fingerprint(line, document_class):
    data = {k: line.get(k) for k in ("product_id", "warehouse_id", "quantity", "net", "batch", "source_line")}
    return hashlib.sha256(json.dumps({**data, "class": document_class}, sort_keys=True).encode()).hexdigest()


def apply_sales_stock(db, user, authorize, enterprise, store_id, start, end, warehouse_id=None, progress=None):
    """Aplica ao stock as linhas das vendas Wintouch importadas no período. Devolve o resumo."""
    config = settings(db)
    if stock_from_erp(db):
        return {"status": "inativo", "message": "O stock desta empresa é controlado pelo ERP."}
    if not config["control_start"]:
        return {
            "status": "inativo",
            "message": "Defina a data de início do controlo de stock (Artigos → Parâmetros) para as vendas Wintouch movimentarem o stock.",
        }
    require(user, config, "adjust_stock")
    lock(db)
    chosen = db.get(StockWarehouse, warehouse_id) if warehouse_id else None
    if warehouse_id and (not chosen or chosen.store_id != store_id or not chosen.active):
        raise HTTPException(422, "Escolha um armazém ativo do estabelecimento da importação.")
    local = list(db.scalars(select(StockWarehouse).where(StockWarehouse.store_id == store_id, StockWarehouse.active.is_(True))))
    linked = {
        link.external_id: link.local_id
        for link in db.scalars(select(ErpLink).where(
            ErpLink.provider == "wintouch", ErpLink.enterprise_id == enterprise, ErpLink.table_key == "warehouses"
        ))
    }
    articles = {
        product.external_id: article
        for article, product in db.execute(
            select(StockArticle, WintouchProduct)
            .join(WintouchProduct, WintouchProduct.id == StockArticle.erp_id)
            .where(WintouchProduct.enterprise_id == enterprise)
        )
    }
    posted = defaultdict(list)  # request_key → movimentos
    by_record = defaultdict(list)  # documento importado → movimentos gerados a partir dele
    for entry in db.scalars(select(StockEntry).where(StockEntry.request_key.like(PREFIX + "%"))):
        posted[entry.request_key].append(entry)
        by_record[json.loads(entry.data).get("source_record")].append(entry)
    records = list(db.scalars(select(WintouchSaleRecord).where(
        WintouchSaleRecord.enterprise_id == enterprise,
        WintouchSaleRecord.store_id == store_id,
        WintouchSaleRecord.kind == "documento",
        WintouchSaleRecord.document_date >= max(start.isoformat(), config["control_start"]),
        WintouchSaleRecord.document_date <= end.isoformat(),
    )))
    # Vendas antes das notas de crédito do mesmo dia: a devolução precisa da saída original.
    records.sort(key=lambda r: (r.document_date, json.loads(r.data).get("document_class") == CREDIT_CLASS, r.external_id))
    result = {"applied": 0, "returns": 0, "reversed": 0, "pending": [], "conflicts": []}

    def pending(document, line, reason):
        product = articles.get(line.get("product_id")) if line else None
        result["pending"].append({"document": document, "article": product.name if product else None, "reason": reason})

    def conflict(document, reason):
        if not any(c["document"] == document and c["reason"] == reason for c in result["conflicts"]):
            result["conflicts"].append({"document": document, "reason": reason})

    def attempt(record, document, line, document_class, data, counter):
        # Cada linha numa subtransação: uma linha recusada (ex.: stock insuficiente) não anula as outras.
        try:
            with db.begin_nested():
                movements = post(db, user, data, authorize, source="Wintouch")
                for movement in movements:
                    detail = json.loads(movement.data)
                    detail.update(source_record=record.id, source_line=line.get("id"), line_fingerprint=fingerprint(line, document_class))
                    movement.data = json.dumps(detail, ensure_ascii=False, default=str)
                db.flush()
        except HTTPException as exc:
            pending(document, line, exc.detail if isinstance(exc.detail, str) else "Movimento recusado pelas regras de stock.")
            return
        result[counter] += 1
        for movement in movements:
            posted[movement.request_key].append(movement)
            by_record[record.id].append(movement)

    for index, record in enumerate(records):
        if progress:
            progress("A aplicar as vendas ao stock", index + 1, len(records))
        detail = json.loads(record.data)
        document = detail.get("document") or record.external_id
        document_class = detail.get("document_class")
        own = by_record.get(record.id, [])
        if detail.get("cancelled") or not record.active:
            # Estorno: cada saída ainda não devolvida volta ao stock, ligada à venda original.
            for entry in [e for e in own if e.kind == "sale"]:
                void = line_key(enterprise, entry.id, void=True)
                returned = sum((e.quantity for rows in posted.values() for e in rows if e.original_id == entry.id and e.kind == "customer_return"), Decimal(0))
                if void in posted or abs(entry.quantity) - returned <= 0:
                    continue
                line = {"id": json.loads(entry.data).get("source_line")}
                attempt(record, document, line, document_class, MovementInput(
                    article_id=entry.article_id, warehouse_id=entry.warehouse_id, kind="customer_return",
                    quantity=abs(entry.quantity) - returned, original_id=entry.id, occurred_on=entry.occurred_on,
                    document=document, reason="Estorno: documento anulado na Wintouch", request_key=void,
                ), "reversed")
            if any(e.kind == "customer_return" and ":void:" not in e.request_key for e in own):
                conflict(document, "Nota de crédito anulada depois de devolver ao stock: reveja e registe o acerto manualmente.")
            continue
        if document_class not in SALE_CLASSES + (CREDIT_CLASS,):
            continue
        lines = detail.get("lines")
        if not isinstance(lines, list):
            pending(document, None, "Linhas do documento por ler: repita a importação.")
            continue
        current = {line_key(enterprise, line["id"]) for line in lines}
        if any(":void:" not in e.request_key and e.request_key not in current for e in own):
            conflict(document, "Linha removida na Wintouch depois de integrada no stock: reveja e registe o acerto manualmente.")
        for line in lines:
            key = line_key(enterprise, line["id"])
            if key in posted:
                if json.loads(posted[key][0].data).get("line_fingerprint") != fingerprint(line, document_class):
                    conflict(document, "Linha alterada na Wintouch depois de integrada no stock: reveja e registe o acerto manualmente.")
                continue
            product = articles.get(line["product_id"])
            if not product:
                pending(document, line, "Artigo do ERP sem ficha local associada.")
                continue
            meta = json.loads(product.data)
            if meta.get("service"):
                continue
            if meta.get("lots") or line.get("batch"):
                pending(document, line, "Artigo com lotes: registe a saída manualmente, com o lote.")
                continue
            amount = Decimal(line["quantity"])
            if amount <= 0:
                continue
            if document_class == CREDIT_CLASS:
                sale = next((e for e in posted.get(line_key(enterprise, line.get("source_line") or ""), []) if e.kind == "sale"), None)
                if not sale:
                    pending(document, line, "Nota de crédito sem venda original integrada no stock: registe a devolução manualmente.")
                    continue
                attempt(record, document, line, document_class, MovementInput(
                    article_id=product.id, warehouse_id=sale.warehouse_id, kind="customer_return", quantity=amount,
                    original_id=sale.id, occurred_on=record.document_date, document=document,
                    reason="Nota de crédito Wintouch", request_key=key,
                ), "returns")
                continue
            target = chosen or next((w for w in local if w.id == linked.get(line.get("warehouse_id"))), None) or (local[0] if len(local) == 1 else None)
            if not target:
                pending(document, line, "Armazém por definir: escolha o armazém na importação ou associe os armazéns ao ERP.")
                continue
            if any(
                document_key(json.loads(e.data).get("document", "")) == document_key(document) and not e.request_key.startswith(PREFIX)
                for e in db.scalars(select(StockEntry).where(StockEntry.article_id == product.id, StockEntry.kind == "sale"))
            ):
                pending(document, line, "Já existe uma venda manual deste documento e artigo: não foi criada uma duplicação.")
                continue
            attempt(record, document, line, document_class, MovementInput(
                article_id=product.id, warehouse_id=target.id, kind="sale", quantity=amount,
                net_sales=max(Decimal(line["net"]), Decimal(0)), occurred_on=record.document_date,
                document=document, reason="Venda Wintouch", request_key=key,
            ), "applied")
    return {
        "status": "aplicado",
        "control_start": config["control_start"],
        "applied": result["applied"],
        "returns": result["returns"],
        "reversed": result["reversed"],
        "pending_count": len(result["pending"]),
        "pending": result["pending"][:50],
        "conflict_count": len(result["conflicts"]),
        "conflicts": result["conflicts"][:50],
    }
