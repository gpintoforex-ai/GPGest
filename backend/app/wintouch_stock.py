"""Movimentos de stock dos documentos Wintouch, aplicados a seguir à importação de vendas.

A Wintouch não controla stock: as quantidades são locais. Cada documento cujo tipo a Wintouch marca
como movimentando stock (`IsStockEnabled`) é aplicado segundo o seu sentido (`StockMode`): vendas e
saídas baixam o stock, compras e entradas aumentam-no, transferências mudam de armazém. Uma devolução
(nota de crédito, devolução a fornecedor) volta ao custo do movimento original, quando este já foi
integrado. Os lotes indicados pela Wintouch são respeitados; sem lote, a saída de um artigo com lotes
segue o FEFO. Só artigos associados e a partir do início do controlo de stock.
Movimentos confirmados não se alteram: um documento anulado gera o movimento inverso ligado ao
original (estorno) e um documento alterado depois de integrado fica em conflito para revisão.

As compras entram no stock só pela receção na aplicação (com lote, validade e custo conferidos): uma compra
da Wintouch já recebida na aplicação (associação ERP ou mesma identidade NIF + tipo + número) é ignorada
para o stock; as outras ficam por rever, sem entrada automática, para o mesmo stock não entrar duas vezes.
Pela mesma razão, uma devolução a fornecedor da Wintouch sem a compra original integrada fica por rever
(regista-se em «Devolver mercadoria» na aplicação).
"""

import hashlib
import json
from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from .inventory import MovementInput, document_key, lock, post, require, settings, stock_from_erp
from .models import (
    DocumentERPLink,
    ERPDocument,
    ErpLink,
    ProcurementDocument,
    Purchase,
    StockArticle,
    StockEntry,
    StockLot,
    StockWarehouse,
    WintouchProduct,
    WintouchSaleRecord,
)

PREFIX = "wt-stock:"
ZERO = Decimal(0)
# Classes de documento Wintouch (validadas na instalação) e a operação local correspondente.
SALES = {3, 5, 7, 19, 27, 39, 40}  # faturas, guias a cliente, pedidos, amostras, contas de mesa, FS
CUSTOMER_RETURNS = {4, 6, 8, 28}  # devoluções, notas de crédito, devoluções de guia e de amostra
PURCHASES = {32, 34, 36}  # faturas de compra e guias de entrada
SUPPLIER_RETURNS = {33, 35, 37}  # devoluções e notas de crédito de fornecedor
LABELS = {
    "sale": "Venda Wintouch",
    "customer_return": "Devolução de cliente Wintouch",
    "purchase": "Compra Wintouch",
    "supplier_return": "Devolução a fornecedor Wintouch",
    "entry": "Entrada de stock Wintouch",
    "exit": "Saída de stock Wintouch",
    "transfer": "Transferência Wintouch",
}
# Ordem no mesmo dia: entradas antes das saídas que delas dependem; devoluções no fim.
ORDER = {"purchase": 0, "entry": 0, "transfer": 1, "sale": 2, "exit": 2, "customer_return": 3, "supplier_return": 3}


def received_purchases(db, enterprise):
    """IDs Wintouch das compras já recebidas na aplicação (receção ou fatura de compras confirmada)."""
    from .document_identity import identity

    local = set(db.scalars(select(ProcurementDocument.purchase_id).where(
        ProcurementDocument.kind.in_(("receipt", "invoice")), ProcurementDocument.confirmed_at.is_not(None),
        ProcurementDocument.status != "cancelled", ProcurementDocument.purchase_id.is_not(None))))
    if not local:
        return set()
    identities = {identity(p.supplier_nif, p.document_type, p.document)
                  for p in db.scalars(select(Purchase).where(Purchase.id.in_(local)))}
    linked = set(db.scalars(select(DocumentERPLink.erp_id).where(
        DocumentERPLink.undone_at.is_(None), DocumentERPLink.purchase_id.in_(local))))
    return {e.external_id for e in db.scalars(select(ERPDocument).where(ERPDocument.enterprise_id == enterprise))
            if e.id in linked or identity(e.supplier_nif, e.document_type, e.document) in identities}


def operation(document_class, stock_mode):
    if document_class in SALES:
        return "sale"
    if document_class in CUSTOMER_RETURNS:
        return "customer_return"
    if document_class in PURCHASES:
        return "purchase"
    if document_class in SUPPLIER_RETURNS:
        return "supplier_return"
    if stock_mode == 2:
        return "transfer"
    if stock_mode == 1:
        return "entry"
    if stock_mode == -1:
        return "exit"
    return None


def line_key(enterprise, line_id, void=False):
    scope = hashlib.sha256(enterprise.encode()).hexdigest()[:12]
    return f"{PREFIX}{scope}:{'void:' if void else ''}{line_id}"


def fingerprint(line, document_class):
    keys = ("product_id", "warehouse_id", "quantity", "net", "batch", "batch_number", "source_line")
    data = {k: line.get(k) for k in keys}
    return hashlib.sha256(json.dumps({**data, "class": document_class}, sort_keys=True).encode()).hexdigest()


def apply_sales_stock(db, user, authorize, enterprise, store_id, start, end, warehouse_id=None, progress=None):
    """Aplica ao stock as linhas dos documentos Wintouch importados no período. Devolve o resumo."""
    config = settings(db)
    if stock_from_erp(db):
        return {"status": "inativo", "message": "O stock desta empresa é controlado pelo ERP."}
    if not config["control_start"]:
        return {
            "status": "inativo",
            "message": "Defina a data de início do controlo de stock (Artigos → Parâmetros) para os documentos Wintouch movimentarem o stock.",
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
    received = received_purchases(db, enterprise)
    posted = defaultdict(list)  # request_key → movimentos
    by_record = defaultdict(list)  # documento importado → movimentos gerados a partir dele
    for entry in db.scalars(select(StockEntry).where(StockEntry.request_key.like(PREFIX + "%"))):
        posted[entry.request_key].append(entry)
        by_record[json.loads(entry.data).get("source_record")].append(entry)
    records = []
    for record in db.scalars(select(WintouchSaleRecord).where(
        WintouchSaleRecord.enterprise_id == enterprise,
        WintouchSaleRecord.store_id == store_id,
        WintouchSaleRecord.kind.in_(["documento", "stock"]),
        WintouchSaleRecord.document_date >= max(start.isoformat(), config["control_start"]),
        WintouchSaleRecord.document_date <= end.isoformat(),
    )):
        detail = json.loads(record.data)
        kind = operation(detail.get("document_class"), detail.get("stock_mode"))
        if kind:
            records.append((record, detail, kind))
    records.sort(key=lambda item: (item[0].document_date, ORDER[item[2]], item[1].get("occurred_at") or "", item[0].external_id))
    result = {"applied": 0, "entries": 0, "exits": 0, "transfers": 0, "returns": 0, "reversed": 0, "pending": [], "conflicts": [],
              "already_received": []}
    counters = {"sale": "applied", "purchase": "entries", "entry": "entries", "exit": "exits", "transfer": "transfers",
                "customer_return": "returns", "supplier_return": "returns"}

    def pending(document, line, reason):
        product = articles.get(line.get("product_id")) if line else None
        result["pending"].append({"document": document, "article": product.name if product else None, "reason": reason})

    def conflict(document, reason):
        if not any(c["document"] == document and c["reason"] == reason for c in result["conflicts"]):
            result["conflicts"].append({"document": document, "reason": reason})

    def returned(entry, kind):
        return sum((abs(e.quantity) for rows in posted.values() for e in rows if e.original_id == entry.id and e.kind == kind), ZERO)

    def warehouse_for(external):
        return next((w for w in local if w.id == linked.get(external)), None)

    def insufficient(item, reason):
        """Diz em que armazém faltou stock e onde há stock do artigo (ex.: armazém sem ligação ao ERP)."""
        names = {w.id: w.name for w in local}
        here = names.get(item.warehouse_id, "indicado")
        others = []
        for warehouse in local:
            if warehouse.id == item.warehouse_id:
                continue
            quantity = db.scalar(select(func.coalesce(func.sum(StockEntry.quantity), 0)).where(
                StockEntry.article_id == item.article_id, StockEntry.warehouse_id == warehouse.id))
            if quantity > 0:
                others.append(f"{quantity.normalize():f} em «{warehouse.name}»")
        if not others:
            return f"{reason} Armazém: «{here}»."
        return (f"Sem stock suficiente no armazém «{here}», o das saídas Wintouch; há {', '.join(others)}. "
                "Transfira o stock para esse armazém ou escolha o armazém das saídas na sincronização.")

    def attempt(record, document, line, document_class, data, counter):
        # Cada linha numa subtransação: uma linha recusada (ex.: stock insuficiente) não anula as outras.
        # `data` pode ser uma lista (ex.: devolução repartida pelos lotes do movimento original).
        try:
            with db.begin_nested():
                movements = []
                for item in data if isinstance(data, list) else [data]:
                    movements += post(db, user, item, authorize, source="Wintouch")
                for movement in movements:
                    detail = json.loads(movement.data)
                    detail.update(source_record=record.id, source_line=line.get("id"), line_fingerprint=fingerprint(line, document_class))
                    movement.data = json.dumps(detail, ensure_ascii=False, default=str)
                db.flush()
        except HTTPException as exc:
            reason = exc.detail if isinstance(exc.detail, str) else "Movimento recusado pelas regras de stock."
            first = (data if isinstance(data, list) else [data])[0]
            if reason.startswith("Stock disponível insuficiente"):
                reason = insufficient(first, reason)
            pending(document, line, reason)
            return
        result[counter] += 1
        for movement in movements:
            posted[movement.request_key].append(movement)
            by_record[record.id].append(movement)

    def returns_of(originals, return_kind, amount, base, key):
        """Devolução repartida pelos movimentos originais (ex.: vários lotes), até à quantidade em falta."""
        parts, left = [], amount
        for original in originals:
            take = min(left, abs(original.quantity) - returned(original, return_kind))
            if take <= 0:
                continue
            parts.append(MovementInput(**base, warehouse_id=original.warehouse_id, kind=return_kind, quantity=take,
                                       original_id=original.id, request_key=key if not parts else f"{key}~{len(parts)}",
                                       override_reason="Lote do movimento original Wintouch"))
            left -= take
            if left <= 0:
                break
        return parts, left

    for index, (record, detail, kind) in enumerate(records):
        if progress:
            progress("A aplicar os documentos ao stock", index + 1, len(records))
        document = detail.get("document") or record.external_id
        document_class = detail.get("document_class")
        own = by_record.get(record.id, [])
        if detail.get("cancelled") or not record.active:
            # Estorno: movimento inverso ligado a cada movimento ainda não revertido; os restantes
            # (devoluções, transferências) ficam para revisão manual.
            for entry in own:
                void = line_key(enterprise, entry.id, void=True)
                if ":void:" in entry.request_key or void in posted:
                    continue
                line = {"id": json.loads(entry.data).get("source_line")}
                common = {"article_id": entry.article_id, "warehouse_id": entry.warehouse_id, "occurred_on": entry.occurred_on,
                          "document": document, "reason": "Estorno: documento anulado na Wintouch", "request_key": void}
                if entry.kind == "sale":
                    left = abs(entry.quantity) - returned(entry, "customer_return")
                    data = MovementInput(**common, kind="customer_return", quantity=left, original_id=entry.id) if left > 0 else None
                elif entry.kind == "purchase":
                    left = abs(entry.quantity) - returned(entry, "supplier_return")
                    data = MovementInput(**common, kind="supplier_return", quantity=left, original_id=entry.id,
                                         override_reason="Lote do movimento original Wintouch") if left > 0 else None
                elif entry.kind == "adjustment":
                    data = MovementInput(**common, kind="adjustment", quantity=abs(entry.quantity), lot_id=entry.lot_id,
                                         adjustment_out=entry.quantity > 0, unit_cost=entry.unit_cost if entry.quantity < 0 else None,
                                         override_reason="Lote do movimento original Wintouch")
                else:
                    conflict(document, "Documento anulado na Wintouch depois de integrado no stock: reveja e registe o acerto manualmente.")
                    continue
                if data:
                    attempt(record, document, line, document_class, data, "reversed")
            continue
        if kind == "purchase" and not own:
            if detail.get("source_document_id") in received:
                result["already_received"].append(document)
            else:
                pending(document, None, "Compra na Wintouch sem receção na aplicação: as compras entram no stock só pela "
                        "receção (Compras → Receções de mercadoria), com lote e validade. Registe a receção.")
            continue
        lines = detail.get("lines")
        if not isinstance(lines, list):
            pending(document, None, "Linhas do documento por ler: repita a importação.")
            continue
        current = {line_key(enterprise, line["id"]) for line in lines}
        if any(":void:" not in e.request_key and e.request_key.split("~")[0] not in current for e in own):
            conflict(document, "Linha removida na Wintouch depois de integrada no stock: reveja e registe o acerto manualmente.")
        for line in lines:
            try:
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
                amount = Decimal(line["quantity"])
                if meta.get("service") or amount <= 0:
                    continue
                inbound = kind in ("purchase", "entry", "customer_return")
                # Lotes: o da Wintouch quando o artigo controla lotes cá; sem lote, as saídas seguem o FEFO.
                # Num artigo sem lotes cá, o lote da Wintouch é ignorado (só contam as quantidades).
                lot = {}
                if meta.get("lots") and line.get("batch_number"):
                    if inbound:
                        lot = {"lot_number": line["batch_number"][:100], "expiry": line.get("batch_expiry")}
                    else:
                        found = db.scalar(select(StockLot).where(StockLot.article_id == product.id, StockLot.number == line["batch_number"]))
                        if not found:
                            pending(document, line, f"Lote «{line['batch_number']}» sem entrada registada no stock: registe primeiro a entrada desse lote.")
                            continue
                        lot = {"lot_id": found.id, "override_reason": "Lote indicado no documento Wintouch"}
                elif meta.get("lots") and inbound and kind != "customer_return":
                    pending(document, line, "Artigo com lotes: a entrada Wintouch não indica o lote. Registe a entrada manualmente, com o lote.")
                    continue
                net = Decimal(line["net"]).quantize(Decimal("0.000001"))
                unit_cost = (net / amount).quantize(Decimal("0.00000001")) if net > 0 else None
                base = {"article_id": product.id, "occurred_on": record.document_date, "document": document, "reason": LABELS[kind]}
                if kind in ("customer_return", "supplier_return"):
                    original_kind = "sale" if kind == "customer_return" else "purchase"
                    originals = [e for e in posted.get(line_key(enterprise, line.get("source_line") or ""), []) if e.kind == original_kind]
                    if originals:
                        parts, left = returns_of(originals, kind, amount, base, key)
                        if left > 0:
                            pending(document, line, "A devolução excede o movimento original integrado no stock: registe a diferença manualmente.")
                            continue
                        attempt(record, document, line, document_class, parts, counters[kind])
                        continue
                    if kind == "customer_return":
                        pending(document, line, "Devolução sem venda original integrada no stock: registe a devolução manualmente.")
                        continue
                    pending(document, line, "Devolução a fornecedor na Wintouch: registe-a na aplicação («Devolver mercadoria» "
                            "na receção), para sair o lote recebido.")
                    continue
                target = chosen or warehouse_for(line.get("warehouse_id")) or (local[0] if len(local) == 1 else None)
                if kind == "transfer":
                    origin = warehouse_for(detail.get("source_warehouse_id"))
                    destination = warehouse_for(line.get("warehouse_id"))
                    if not origin or not destination or origin.id == destination.id:
                        pending(document, line, "Transferência com armazéns por associar ao ERP (Tabelas → Armazéns): registe-a manualmente.")
                        continue
                    data = MovementInput(**base, **lot, warehouse_id=origin.id,
                                         target_id=destination.id, kind="transfer", quantity=amount, request_key=key)
                    attempt(record, document, line, document_class, data, counters[kind])
                    continue
                if not target:
                    pending(document, line, "Armazém por definir: escolha o armazém na importação ou associe os armazéns ao ERP.")
                    continue
                values = base | lot | {"warehouse_id": target.id, "quantity": amount, "request_key": key}
                if kind == "sale":
                    if any(
                        document_key(json.loads(e.data).get("document", "")) == document_key(document) and not e.request_key.startswith(PREFIX)
                        for e in db.scalars(select(StockEntry).where(StockEntry.article_id == product.id, StockEntry.kind == "sale"))
                    ):
                        pending(document, line, "Já existe uma venda manual deste documento e artigo: não foi criada uma duplicação.")
                        continue
                    data = MovementInput(**values, kind="sale", net_sales=max(net, ZERO))
                elif kind == "purchase":
                    data = MovementInput(**values, kind="purchase", unit_cost=unit_cost)
                elif kind == "entry":
                    data = MovementInput(**values, kind="adjustment", unit_cost=unit_cost)
                elif kind == "exit":
                    data = MovementInput(**values, kind="adjustment", adjustment_out=True)
                attempt(record, document, line, document_class, data, counters[kind])
            except ValidationError:
                pending(document, line, "Valores da linha fora dos limites aceites pelo stock (quantidade ou valor): registe o movimento manualmente.")
    return {
        "status": "aplicado",
        "control_start": config["control_start"],
        **{k: result[k] for k in ("applied", "entries", "exits", "transfers", "returns", "reversed")},
        "already_received_count": len(result["already_received"]),
        "already_received": result["already_received"][:50],
        "pending_count": len(result["pending"]),
        "pending": result["pending"][:50],
        "conflict_count": len(result["conflicts"]),
        "conflicts": result["conflicts"][:50],
    }
