"""Bounded, read-only search over the current company; never retrieves file contents or costs."""

import unicodedata

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, cast, func, or_, select, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from .access import authorize, current, scope
from .archive_api import visible as archive_visible
from .db import db_session, request_company
from .models import (
    ArchiveDocument,
    BusinessEntity,
    CustomerOrder,
    StockArticle,
    WintouchEntity,
    WintouchProduct,
)
from .operating_modes import policy
from .table_framework import context

router = APIRouter(prefix="/api/workspace", tags=["Pesquisa"])
LIMIT = 5
ACCENTS = "áàâãäéèêëíìîïóòôõöúùûüçñ"
PLAIN = "aaaaaeeeeiiiiooooouuuucn"


def normalized(value):
    return "".join(
        c for c in unicodedata.normalize("NFD", value.lower()) if not unicodedata.combining(c)
    )


def matching(terms, *columns):
    # Explicit capitals also work in a C-collation test/database; delete common NFD marks.
    haystack = func.translate(
        func.lower(func.concat_ws(" ", *columns)),
        ACCENTS + ACCENTS.upper() + "\u0300\u0301\u0302\u0303\u0308\u0327",
        PLAIN + PLAIN,
    )
    # Escape LIKE metacharacters: '%' and '_' are literal search terms, not wildcards.
    return and_(*(haystack.contains(term, autoescape=True) for term in terms))


def target(page, kind, identifier, **params):
    return {"page": page, "params": {"consulta.tipo": kind, "consulta.id": identifier, **params}}


def entity_queries(db, terms, role):
    flag = "is_customer" if role == "customers" else "is_supplier"
    local = select(
        ("local:" + BusinessEntity.id).label("identifier"),
        BusinessEntity.name.label("name"),
        BusinessEntity.tax_number.label("reference"),
        BusinessEntity.active.label("active"),
    ).where(
        BusinessEntity.wintouch_id.is_(None),
        BusinessEntity.removed.is_(False),
        getattr(BusinessEntity, flag).is_(True),
        matching(terms, BusinessEntity.name, BusinessEntity.tax_number),
    )
    company = request_company(db)
    enterprise = company.api_enterprise if company else None
    if not enterprise:
        return local.subquery()
    config = policy(db)
    external_primary = (
        config["mode"] == "integrated"
        and config.get("authority", {}).get("entities", "erp") == "erp"
    )
    ext = WintouchEntity
    name = ext.name if external_primary else func.coalesce(BusinessEntity.name, ext.name)
    reference = (
        ext.tax_number
        if external_primary
        else func.coalesce(BusinessEntity.tax_number, ext.tax_number)
    )

    def effective(field):
        if external_primary:
            return case(
                (BusinessEntity.flags_local.is_(True), getattr(BusinessEntity, field)),
                else_=getattr(ext, field),
            )
        return func.coalesce(getattr(BusinessEntity, field), getattr(ext, field))

    imported = (
        select(
            ("wt:" + ext.id).label("identifier"),
            name.label("name"),
            reference.label("reference"),
            effective("active").label("active"),
        )
        .outerjoin(BusinessEntity, BusinessEntity.wintouch_id == ext.id)
        .where(
            ext.enterprise_id == enterprise,
            ext.erp_deleted_at.is_(None),
            or_(BusinessEntity.id.is_(None), BusinessEntity.removed.is_(False)),
            effective(flag).is_(True),
            matching(terms, name, reference),
        )
    )
    return union_all(local, imported).subquery()


@router.get("/search")
def search(
    q: str = Query("", max_length=100),
    store_id: str = "",
    user=Depends(current),
    db: Session = Depends(db_session),
):
    stores = scope(user, db)
    if store_id:
        authorize(user, db, store_id)
        if store_id not in stores:
            from fastapi import HTTPException

            raise HTTPException(403, "Selecione uma loja autorizada.")
        stores = [store_id]
    query = " ".join(q.split())
    if len(query) < 2:
        return {"query": query, "groups": [], "read_only": True}
    terms = normalized(query).split()
    groups = []

    def add(kind, label, rows, render):
        groups.append(
            {
                "id": kind,
                "label": label,
                "more": len(rows) > LIMIT,
                "items": [render(row) for row in rows[:LIMIT]],
            }
        )

    # Article identity matches the catalogue projection; no stock history or prices are loaded.
    payload = cast(StockArticle.data, JSONB)
    code = func.coalesce(WintouchProduct.code, StockArticle.code)
    name = func.coalesce(WintouchProduct.name, StockArticle.name)
    active = func.coalesce(WintouchProduct.active, StockArticle.active)
    rows = db.execute(
        select(StockArticle.id, code.label("code"), name.label("name"), active.label("active"))
        .outerjoin(WintouchProduct, WintouchProduct.id == StockArticle.erp_id)
        .where(
            matching(
                terms,
                code,
                name,
                payload["reference"].astext,
                payload["barcodes"].astext,
                payload["supplier_reference"].astext,
            )
        )
        .order_by(func.lower(name), StockArticle.id)
        .limit(LIMIT + 1)
    ).all()
    add(
        "articles",
        "Artigos",
        rows,
        lambda row: {
            "id": row.id,
            "label": row.name,
            "detail": f"{row.code}{' · Inativo' if not row.active else ''}",
            "target": target(
                "Artigos e validades",
                "article",
                row.id,
                **{
                    "area.stock": "Artigos",
                    "pesquisa.artigos": row.code,
                    "menu.destino": "comercial:Artigos e preços",
                    "menu.area": "artigos",
                },
            ),
        },
    )

    for kind, label, page in [
        ("customers", "Clientes", "Clientes"),
        ("suppliers", "Fornecedores", "Fornecedores"),
    ]:
        ctx = context(kind, user, db)
        if not ctx.spec.allowed(ctx, "view"):
            continue
        entities = entity_queries(db, terms, kind)
        rows = db.execute(
            select(entities)
            .order_by(func.lower(entities.c.name), entities.c.identifier)
            .limit(LIMIT + 1)
        ).all()
        add(
            kind,
            label,
            rows,
            lambda row, k=kind, p=page: {
                "id": row.identifier,
                "label": row.name,
                "detail": f"{row.reference or 'NIF não indicado'}{' · Inativo' if not row.active else ''}",
                "target": target(p, k, row.identifier),
            },
        )

    order_data = cast(CustomerOrder.data, JSONB)
    number = func.coalesce(
        order_data["number"].astext, "PC-" + func.upper(func.substr(CustomerOrder.id, 1, 8))
    )
    rows = db.execute(
        select(
            CustomerOrder.id,
            number.label("number"),
            order_data["customer_name"].astext.label("customer"),
        )
        .where(
            CustomerOrder.store_id.in_(stores),
            matching(terms, number, order_data["customer_name"].astext),
        )
        .order_by(CustomerOrder.created_at.desc(), CustomerOrder.id)
        .limit(LIMIT + 1)
    ).all()
    add(
        "orders",
        "Encomendas de clientes",
        rows,
        lambda row: {
            "id": row.id,
            "label": row.number,
            "detail": row.customer or "Cliente não indicado",
            "target": target(
                "Encomendas de clientes",
                "order",
                row.id,
                **{
                    "area.pedidos-clientes": "pedidos",
                    "filtro.pedidos": "todos",
                    "menu.destino": "comercial:Encomendas de clientes",
                    "menu.area": "encomendas",
                },
            ),
        },
    )

    visibility = archive_visible(user, db)
    if store_id:
        visibility = and_(visibility, ArchiveDocument.store_id == store_id)
    rows = db.execute(
        select(
            ArchiveDocument.id,
            ArchiveDocument.original_name,
            ArchiveDocument.document_number,
            ArchiveDocument.entity_name,
        )
        .where(
            visibility,
            matching(
                terms,
                ArchiveDocument.original_name,
                ArchiveDocument.document_number,
                ArchiveDocument.entity_name,
                ArchiveDocument.entity_nif,
            ),
        )
        .order_by(ArchiveDocument.received_at.desc(), ArchiveDocument.id)
        .limit(LIMIT + 1)
    ).all()
    add(
        "documents",
        "Documentos do arquivo",
        rows,
        lambda row: {
            "id": row.id,
            "label": row.document_number or row.original_name or "Documento",
            "detail": row.entity_name or row.original_name,
            "target": target("Todos os documentos", "document", row.id),
        },
    )
    return {"query": query, "groups": groups, "read_only": True}
