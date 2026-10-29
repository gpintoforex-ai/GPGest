"""Condições por fornecedor de cada artigo: referência, preço acordado, prazo e quantidades.

Complementa o fornecedor principal da ficha (um só) e as condições gerais de encomenda do
fornecedor (supplier_order_terms). O preço acordado é um custo unitário sem IVA, como os custos
de stock; só é visível e editável com a permissão de consultar custos.
"""

import json
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import current, record
from .db import db_session
from .inventory import article, dump, fail, permissions, require, settings
from .models import ArticleSupplierTerms, BusinessEntity, User
from .schemas import Strict

router = APIRouter()


class TermsInput(Strict):
    revision: int = Field(default=0, ge=0)
    supplier_reference: str = Field(default="", max_length=100)
    agreed_cost: Decimal | None = Field(default=None, ge=0, le=10**9, max_digits=18, decimal_places=6)
    valid_until: date | None = None
    lead_days: int | None = Field(default=None, ge=0, le=3650)
    min_quantity: Decimal | None = Field(default=None, gt=0, le=10**12, max_digits=18, decimal_places=6)
    pack_quantity: Decimal | None = Field(default=None, gt=0, le=10**12, max_digits=18, decimal_places=6)
    notes: str = Field(default="", max_length=300)

    @field_validator("supplier_reference", "notes")
    @classmethod
    def strip(cls, value):
        return " ".join(value.split())


def text(value):
    return None if value is None else str(value)


def view(row, names, main, cost_visible):
    return {
        "supplier_id": row.supplier_id,
        "supplier_name": names.get(row.supplier_id, "Fornecedor removido"),
        "main": row.supplier_id == main,
        "supplier_reference": row.supplier_reference,
        "agreed_cost": text(row.agreed_cost) if cost_visible else None,
        "valid_until": row.valid_until,
        "lead_days": row.lead_days,
        "min_quantity": text(row.min_quantity),
        "pack_quantity": text(row.pack_quantity),
        "notes": row.notes,
        "revision": row.revision,
        "updated_at": row.updated_at,
        "updated_by": names.get(row.updated_by),
    }


def listing(db, user, product):
    rights = permissions(user, settings(db))
    rows = list(
        db.scalars(select(ArticleSupplierTerms).where(ArticleSupplierTerms.article_id == product.id))
    )
    ids = {r.supplier_id for r in rows} | {r.updated_by for r in rows}
    names = {e.id: e.name for e in db.scalars(select(BusinessEntity).where(BusinessEntity.id.in_(ids)))}
    names |= {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids)))}
    main = json.loads(product.data or "{}").get("supplier_id")
    terms = sorted(
        (view(r, names, main, rights["view_cost"]) for r in rows),
        key=lambda t: (not t["main"], t["supplier_name"].casefold()),
    )
    return {"terms": terms, "can_edit": rights["adjust_stock"], "cost_visible": rights["view_cost"]}


@router.get("/api/stock/articles/{identifier}/supplier-terms")
def supplier_terms(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    return listing(db, user, article(db, identifier))


@router.put("/api/stock/articles/{identifier}/supplier-terms/{supplier_id}")
def save_terms(
    identifier: str,
    supplier_id: str,
    data: TermsInput,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    config = settings(db)
    require(user, config, "adjust_stock")
    # Condições locais: também nos artigos do ERP (o conector não as tem).
    product = article(db, identifier)
    supplier = db.get(BusinessEntity, supplier_id)
    if not supplier or not supplier.is_supplier or supplier.removed:
        fail("Selecione um fornecedor desta empresa.", 422)
    row = db.scalar(
        select(ArticleSupplierTerms)
        .where(
            ArticleSupplierTerms.article_id == product.id,
            ArticleSupplierTerms.supplier_id == supplier_id,
        )
        .with_for_update()
    )
    if row and row.revision != data.revision:
        fail("As condições deste fornecedor mudaram entretanto. Atualize antes de gravar.", 409)
    if not row and data.revision:
        fail("As condições deste fornecedor foram removidas entretanto. Atualize a ficha.", 409)
    cost_visible = permissions(user, config)["view_cost"]
    if not cost_visible and data.agreed_cost is not None:
        fail("O seu perfil não permite definir o preço acordado.", 403)
    before = (
        {"agreed_cost": text(row.agreed_cost), "supplier_reference": row.supplier_reference}
        if row
        else None
    )
    if not row:
        row = ArticleSupplierTerms(article_id=product.id, supplier_id=supplier_id, revision=0)
        db.add(row)
    row.supplier_reference = data.supplier_reference
    if cost_visible:
        # Sem permissão de custos o preço acordado existente mantém-se.
        row.agreed_cost = data.agreed_cost
    row.valid_until = data.valid_until.isoformat() if data.valid_until else None
    row.lead_days = data.lead_days
    row.min_quantity = data.min_quantity
    row.pack_quantity = data.pack_quantity
    row.notes = data.notes
    row.revision += 1
    row.updated_by = user.id
    db.flush()
    record(
        db,
        user,
        "stock_article_supplier_terms",
        product.id,
        {
            "fornecedor": supplier.name,
            "antes": before,
            "depois": {"agreed_cost": text(row.agreed_cost), "supplier_reference": row.supplier_reference},
        },
    )
    db.commit()
    return listing(db, user, product)


@router.delete("/api/stock/articles/{identifier}/supplier-terms/{supplier_id}")
def remove_terms(
    identifier: str, supplier_id: str, user=Depends(current), db: Session = Depends(db_session)
):
    require(user, settings(db), "adjust_stock")
    product = article(db, identifier)
    row = db.scalar(
        select(ArticleSupplierTerms).where(
            ArticleSupplierTerms.article_id == product.id,
            ArticleSupplierTerms.supplier_id == supplier_id,
        )
    )
    if not row:
        fail("Não há condições deste fornecedor neste artigo.", 404)
    supplier = db.get(BusinessEntity, supplier_id)
    record(
        db,
        user,
        "stock_article_supplier_terms_removed",
        product.id,
        {"fornecedor": supplier.name if supplier else supplier_id, "condicoes": dump(view(row, {}, None, True))},
    )
    db.delete(row)
    db.commit()
    return listing(db, user, product)
