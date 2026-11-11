"""Avisos da barra do menu: o que pede ação, de todos os módulos, e a página onde se resolve.

Cada grupo só aparece a quem tem acesso ao módulo (os erros de autorização desse módulo são
ignorados) e só quando há casos. As leituras não alteram nada.
"""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .access import authorize, current, scope
from .db import db_session
from .models import ProcurementDocument as Doc
from .models import StockEntry, StockLot, StockWarehouse

router = APIRouter()
MANAGERS = ("administrador", "gestor")
ITEMS = 5


def day(value):
    return "/".join(reversed(str(value)[:10].split("-")))


@router.get("/api/alerts")
def alerts(store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        if store_id not in stores:
            raise HTTPException(403, "Selecione uma loja autorizada.")
        stores = [s for s in stores if s == store_id]
    groups = []
    generated = datetime.now(ZoneInfo("Europe/Lisbon"))

    def add(key, label, items, tone, page, params=None):
        if items:
            groups.append({"id": key, "label": label, "count": len(items), "tone": tone, "page": page,
                           "params": params or {}, "items": items[:ITEMS]})

    def section(read):
        try:
            read()
        except HTTPException:
            db.rollback()  # sem acesso a este módulo: os outros avisos continuam

    def customer_orders():
        from .customer_orders import listing

        orders = listing(store_id=store_id, user=user, db=db)["orders"]
        params = {"area.pedidos-clientes": "pedidos"}
        add("clientes_contactar", "Clientes por contactar (produtos disponíveis)",
            [f"{o['number']} · {o['customer_name']}" for o in orders if o["ready"] and not o["notified"]],
            "warning", "Encomendas de clientes", params)
        add("pedidos_atrasados", "Pedidos de clientes em atraso",
            [f"{o['number']} · {o['customer_name']} · entrega {day(o['due_date'])}" for o in orders if o["late"]],
            "danger", "Encomendas de clientes", params)

    def purchases():
        if user.role not in MANAGERS:
            return
        from .procurement import expiry_return_items, order_status

        today = generated.date().isoformat()
        docs = list(db.scalars(select(Doc).where(Doc.store_id.in_(stores), Doc.status != "cancelled")))
        late, claims = [], []
        for doc in docs:
            payload = json.loads(doc.data)
            label = payload.get("document") or doc.id[:8]
            if doc.kind == "order" and doc.confirmed_at and (payload.get("expected_date") or "9999") < today:
                if order_status(db, doc) in ("confirmed", "partial"):
                    late.append(f"{label} · previsto {day(payload['expected_date'])}")
            if doc.kind == "receipt" and (payload.get("expiry_claim") or {}).get("status") == "pendente":
                claims.append(label)
        add("encomendas_atrasadas", "Encomendas a fornecedores atrasadas", late, "danger", "Receções de mercadoria")
        add("reclamacoes_validade", "Reclamações de validade por fazer", claims, "danger", "Receções de mercadoria")
        returns = expiry_return_items(db, stores)["items"]
        add("devolucoes_validade", "Produtos a devolver por validade",
            [f"{i['code']} · lote {i['lot_number']} · validade {day(i['expiry'])}" for i in returns],
            "warning", "Devoluções a fornecedores")

    def expiring_lots():
        from .inventory import settings

        horizon = (generated.date() + timedelta(days=max(settings(db)["alerts"], default=90))).isoformat()
        warehouses = list(db.scalars(select(StockWarehouse.id).where(StockWarehouse.store_id.in_(stores))))
        if not warehouses:
            return
        rows = db.execute(
            select(StockLot.number, StockLot.expiry, func.sum(StockEntry.quantity))
            .join(StockEntry, StockEntry.lot_id == StockLot.id)
            .where(StockLot.expiry.is_not(None), StockLot.expiry <= horizon, StockEntry.warehouse_id.in_(warehouses))
            .group_by(StockLot.id, StockLot.number, StockLot.expiry)
            .having(func.sum(StockEntry.quantity) > 0)
            .order_by(StockLot.expiry)
        ).all()
        add("lotes_validade", "Lotes em stock a perder a validade",
            [f"Lote {number} · validade {day(expiry)}" for number, expiry, _ in rows], "warning", "Artigos e validades")

    def pending_documents():
        from .archive_api import visible
        from .models import ArchiveDocument
        visibility = visible(user, db)
        if store_id:
            visibility = visibility & (ArchiveDocument.store_id == store_id)
        query = select(ArchiveDocument).where(visibility, ArchiveDocument.duplicate_of.is_(None), ArchiveDocument.review_status == "por_rever")
        count = db.scalar(select(func.count()).select_from(query.subquery()))
        if count:
            rows = db.scalars(query.order_by(ArchiveDocument.received_at.desc(), ArchiveDocument.id).limit(ITEMS))
            groups.append({"id":"documentos_rever","label":"Documentos do arquivo por rever","count":count,"tone":"warning","page":"Todos os documentos","params":{},"items":[row.document_number or row.original_name or "Documento" for row in rows]})

    def deadlines():
        # Obrigações e Prazos: vencidos e próximos 7 dias ainda não lidos por este utilizador.
        from .obligation_alerts import alert_group
        group = alert_group(db, user, stores)
        if group:
            group["items"] = group["items"][:ITEMS]
            groups.append(group)

    for read in (customer_orders, purchases, expiring_lots, pending_documents, deadlines):
        section(read)
    order = {"danger": 0, "warning": 1}
    groups.sort(key=lambda g: order.get(g["tone"], 2))
    return {"groups": groups, "total": sum(g["count"] for g in groups), "generated_at": generated.isoformat(), "unit": "attention_occurrences", "read_only": True}
