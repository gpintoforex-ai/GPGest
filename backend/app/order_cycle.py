"""Explainable replenishment and three-way review over the existing order/stock/archive ledgers."""

import hashlib
import json
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal as D
from difflib import SequenceMatcher
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import archive
from . import customer_orders as orders
from . import inventory as stock
from . import procurement as buying
from .access import authorize, current, record, scope
from .db import db_session
from .invoice_evidence import invoice_key, nif_key
from .models import (
    ArchiveDocument,
    ArchiveJob,
    AttachmentIntegrationConfig,
    BusinessEntity,
    CustomerOrder,
    CustomerOrderEvent,
    CustomerOrderLine,
    CustomerPurchaseSimulation,
    ERPDocument,
    OrderCycleSettings,
    OrderDocumentReview,
    OrderReplenishmentPolicy,
    ProcurementDocument,
    Purchase,
    StockArticle,
    StockEntry,
    StockLot,
    StockWarehouse,
    User,
    WintouchEntity,
    WintouchProduct,
    now,
)
from .operating_modes import policy
from .schemas import Strict

router = APIRouter(prefix="/api/order-cycle", tags=["Ciclo de encomendas"])
ZERO = D(0)


class Replenishment(orders.Change):
    store_id: str
    article_id: str
    safety: D = Field(default=ZERO, ge=0, le=10**9, decimal_places=6)
    coverage_days: int = Field(default=14, ge=1, le=365)
    history_days: int = Field(default=90, ge=7, le=730)
    shelf_days: int | None = Field(default=None, ge=1, le=3650)
    seasonal_factor: D = Field(default=D(1), ge=0, le=10, decimal_places=3)
    budget_cents: int | None = Field(default=None, ge=0, le=10**12)


class LinkOriginal(orders.Change):
    simulation_id: str
    document_id: str
    receipt_id: str | None = None


class BilledLine(Strict):
    article_id: str | None = None
    reference: str = Field(default="", max_length=100)
    description: str = Field(min_length=1, max_length=500)
    quantity: D = Field(gt=0, le=10**9, decimal_places=6)
    price: D = Field(ge=0, le=10**9, decimal_places=6)
    discount: D = Field(default=ZERO, ge=0, le=100, decimal_places=4)
    vat: D = Field(ge=0, le=100, decimal_places=3)


class ReviewInput(orders.Change):
    lines: list[BilledLine] = Field(min_length=1, max_length=200)
    supplier_nif: str = Field(max_length=40)
    document: str = Field(min_length=1, max_length=100)
    document_type: str = Field(default="FT", pattern="^(FT|FS|FR|ND)$")
    document_date: date
    due_date: date
    currency: str = Field(default="EUR", pattern="^[A-Z]{3}$")
    total_cents: int = Field(ge=0, le=10**12)
    tax_cents: int = Field(ge=0, le=10**12)
    note: str = Field(default="", max_length=2000)


class Decision(orders.Change):
    fingerprint: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=3, max_length=2000)
    confirmed: bool = False
    confirm_demo: bool = False


class Automation(orders.Change):
    enabled: bool = False
    auto_erp_purchases: bool = False
    template: str = Field(
        default="Olá {nome}, a sua encomenda {numero} já tem produtos disponíveis para levantamento. Obrigado pela preferência.",
        min_length=10,
        max_length=5000,
    )


class ERPPrepare(Decision):
    kind: str = Field(pattern="^(product|purchase)$")
    proposal_index: int = Field(default=0, ge=0, le=199)
    unit_id: str = ""
    vat_id: str = ""
    category_id: str = ""
    price_policy_id: str = ""
    document_type_id: str = ""
    document_serie_id: str = ""
    warehouse_id: str = ""
    sector_id: str = ""
    currency_id: str = ""


def external_started(row, kind="purchase"):
    return any(
        o["state"] in ("sending", "uncertain") or (o["kind"] == kind and o["state"] == "confirmed")
        for o in json.loads(row.data).get("erp_operations", [])
    )


@router.get("/reviews/{identifier}/wintouch-options")
def wintouch_options(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    get_review(db, user, identifier)
    from .wintouch_order_write import connection, options

    with connection(db, user) as reader:
        catalogs = options(reader)
    return {
        key: [
            {
                "id": r["ID"],
                "name": r.get("Name") or r.get("Code") or r["ID"],
                "rate": r.get("TaxRate"),
                "class": r.get("Class"),
                "type_id": r.get("DocumentTypeID"),
            }
            for r in rows
        ]
        for key, rows in catalogs.items()
    }


@router.post("/reviews/{identifier}/erp-prepare")
def prepare_erp(
    identifier: str, data: ERPPrepare, user=Depends(current), db: Session = Depends(db_session)
):
    row, sim, doc = get_review(db, user, identifier)
    digest, prior = orders.replay(db, user, "erp_prepare", data, identifier)
    if prior:
        return view(db, user, row)
    orders.revision(row, data)
    compared = compare(db, user, row, sim, doc)
    if data.fingerprint != compared["fingerprint"] or not data.confirmed or row.procurement_id:
        orders.fail("A conferência mudou, falta confirmação ou existe uma compra local.", 409)
    payload = json.loads(row.data)
    if any(o["state"] in ("sending", "uncertain") for o in payload.get("erp_operations", [])):
        orders.fail("Verifique a operação ERP em curso/incerta antes de preparar outra.", 409)
    if any(
        o["kind"] == data.kind
        and o.get("proposal_index", 0) == data.proposal_index
        and o["state"] in ("sending", "uncertain", "confirmed")
        for o in payload.get("erp_operations", [])
    ):
        orders.fail(
            "Já existe uma tentativa ERP para esta origem. Verifique o resultado pelo identificador existente.",
            409,
        )
    from .db import request_company
    from .wintouch_order_write import connection, options, product_payload, purchase_payload

    with connection(
        db,
        user,
        write=True,
        resource="products" if data.kind == "product" else "purchase_documents",
        confirm_demo=data.confirm_demo,
    ) as reader:
        catalogs = options(reader)
        identifier_erp = str(uuid4())
        mapping = data.model_dump()
        if data.kind == "product":
            proposals = payload.get("article_proposals", [])
            if (
                data.proposal_index >= len(proposals)
                or proposals[data.proposal_index]["state"] != "blocked_connector"
            ):
                orders.fail("Guarde uma proposta de artigo antes de preparar a criação ERP.")
            article = proposals[data.proposal_index]["article"]
            stock.save_article(db, user, stock.ArticleInput(**article), preview=True)
            remote_products = list(reader.collection("products", "ProductBarCodes"))
            for remote in remote_products:
                if remote.get("Code", "").casefold() == article["code"].casefold() or set(
                    article.get("barcodes", [])
                ) & {b.get("BarCode") for b in remote.get("ProductBarCodes", [])}:
                    orders.fail(
                        "O código ou EAN já existe no ERP. Associe o artigo existente.", 409
                    )
            outgoing = product_payload(article, mapping, catalogs, identifier_erp)
        else:
            if not compared["approved"] or not payload.get("review"):
                orders.fail("Aprove primeiro os artigos e valores da conferência atual.", 409)
            saved = payload["review"]
            billed_quantities = {}
            physical_quantities = {}
            for line in compared["rows"]:
                aid = line.get("article_id")
                billed_quantities[aid] = billed_quantities.get(aid, ZERO) + D(line["quantity"])
                physical_quantities[aid] = D(line["received"])
            if any(
                not aid or q > physical_quantities.get(aid, ZERO)
                for aid, q in billed_quantities.items()
            ):
                orders.fail(
                    "Resolva os artigos e quantidades faturadas acima da receção física antes de preparar a compra ERP.",
                    409,
                )
            if (
                compared["totals"]["total_cents"] != saved["total_cents"]
                or compared["totals"]["tax_cents"] != saved["tax_cents"]
                or doc.duplicate_of
                or compared["fiscal_duplicate"]
            ):
                orders.fail("Resolva os totais fiscais e documentos duplicados.", 409)
            supplier = db.get(BusinessEntity, sim.supplier_id)
            imported = (
                db.get(WintouchEntity, supplier.wintouch_id) if supplier.wintouch_id else None
            )
            company = request_company(db)
            if not imported or imported.enterprise_id != company.api_enterprise:
                orders.fail("Associe o fornecedor existente no Wintouch desta empresa.")
            entity = reader.get("entities/" + quote(imported.external_id, safe=""))
            # Query the current ERP before preparing a new fiscal identity.
            for existing in reader.collection("product_documents", "CEntity,DocumentType"):
                if existing.get("DeletedDate"):
                    continue
                typ = existing.get("DocumentType") or {}
                if typ.get("Class") in (32, 33, 34) and invoice_key(
                    (existing.get("CEntity") or {}).get("VATNumber") or existing.get("VATNumber"),
                    saved["document_type"],
                    existing.get("ExternalDocument"),
                ) == invoice_key(saved["supplier_nif"], saved["document_type"], saved["document"]):
                    orders.fail(
                        "A fatura já existe no Wintouch. Associe o documento existente em Conferência documental.",
                        409,
                    )
            products = {}
            for line in saved["lines"]:
                article = stock.article(db, line["article_id"]) if line.get("article_id") else None
                external = (
                    db.get(WintouchProduct, article.erp_id) if article and article.erp_id else None
                )
                if not external or external.enterprise_id != company.api_enterprise:
                    orders.fail("Associe todos os artigos definitivos desta empresa ERP.")
                products[article.id] = reader.get(
                    "products/" + quote(external.external_id, safe="")
                )
            outgoing = purchase_payload(saved, mapping, catalogs, entity, products, identifier_erp)
        origin = reader.origin_fingerprint
    for previous in payload.get("erp_operations", []):
        if (
            previous["kind"] == data.kind
            and previous.get("proposal_index", 0) == data.proposal_index
            and previous["state"] == "prepared"
        ):
            previous["state"] = "cancelled"
    # Round-trip Decimal through JSON as strings for storage; writer serializes numeric fields with Decimal.
    operation = {
        "id": identifier_erp,
        "kind": data.kind,
        "proposal_index": data.proposal_index,
        "origin": origin,
        "payload": outgoing,
        "state": "prepared",
        "fingerprint": compared["fingerprint"],
        "created_by": user.id,
        "total_cents": payload.get("review", {}).get("total_cents"),
        "tax_cents": payload.get("review", {}).get("tax_cents"),
        "supplier_nif": payload.get("review", {}).get("supplier_nif"),
        "at": now().isoformat(),
        "confirm_demo": data.confirm_demo,
    }
    payload.setdefault("erp_operations", []).append(operation)
    row.data = orders.dump(payload)
    row.revision += 1
    orders.event(
        db,
        user,
        "erp_prepare",
        data,
        digest,
        row.id,
        sim.store_id,
        detail={"operation_id": identifier_erp, "kind": data.kind},
    )
    db.commit()
    return view(db, user, row)


@router.post("/reviews/{identifier}/erp-send/{operation_id}")
def send_erp(
    identifier: str,
    operation_id: str,
    data: Decision,
    user=Depends(current),
    db: Session = Depends(db_session),
):
    row, sim, doc = get_review(db, user, identifier)
    orders.management(db, user, sim.store_id)
    digest, prior = orders.replay(db, user, "erp_send", data, operation_id)
    if prior:
        return view(db, user, row)
    orders.revision(row, data)
    payload = json.loads(row.data)
    operation = next(
        (o for o in payload.get("erp_operations", []) if o["id"] == operation_id), None
    )
    if any(
        o["id"] != operation_id and o["state"] in ("sending", "uncertain")
        for o in payload.get("erp_operations", [])
    ):
        orders.fail("Verifique primeiro a outra operação ERP em curso/incerta.", 409)
    if not operation or not data.confirmed:
        orders.fail("Confirme a operação ERP preparada.")
    if operation["state"] == "cancelled":
        orders.fail("Esta preparação ERP foi substituída. Utilize a preparação atual.", 409)
    compared = compare(db, user, row, sim, doc)
    if operation["state"] == "prepared" and (
        operation["fingerprint"] != compared["fingerprint"]
        or data.fingerprint != compared["fingerprint"]
        or (operation["kind"] == "purchase" and not compared["approved"])
    ):
        orders.fail("A conferência mudou. Prepare novamente antes de enviar.", 409)
    from .wintouch_order_write import connection, dispatch_once

    orders.event(
        db,
        user,
        "erp_send",
        data,
        digest,
        row.id,
        sim.store_id,
        detail={"operation_id": operation_id},
    )

    def persist(value):
        row.data = orders.dump(payload)
        row.revision += 1
        record(
            db,
            user,
            "encomenda_erp_" + value["state"],
            row.id,
            {"operation_id": value["id"], "kind": value["kind"], "error": value.get("error", "")},
            sim.store_id,
        )
        db.commit()

    with connection(
        db,
        user,
        write=True,
        resource="products" if operation["kind"] == "product" else "purchase_documents",
        confirm_demo=data.confirm_demo,
    ) as reader:
        # A sending/uncertain operation is only reconciled, never posted twice.
        result = dispatch_once(
            reader, operation, persist, reconcile=operation["state"] != "prepared"
        )
    if result["state"] == "confirmed" and result["kind"] == "product":
        stock.lock(db)
        db.expire_all()
        payload = json.loads(row.data)
        proposed = payload["article_proposals"][result["proposal_index"]]
        proposed.update(state="confirmed_erp", erp_id=result["external_id"])
        from .db import request_company
        from .wintouch_sync import normalize

        company = request_company(db)
        external = db.scalar(
            select(WintouchProduct).where(
                WintouchProduct.enterprise_id == company.api_enterprise,
                WintouchProduct.external_id == result["external_id"],
            )
        )
        if not external:
            external = WintouchProduct(
                enterprise_id=company.api_enterprise,
                **normalize("products", result["verified_product"]),
            )
            db.add(external)
            db.flush()
        local = db.scalar(select(StockArticle).where(StockArticle.erp_id == external.id))
        if not local:
            # Code/name have been confirmed by GET; this is a reference to that same ERP article.
            if db.scalar(select(StockArticle.id).where(StockArticle.code == external.code)):
                orders.fail(
                    "Artigo confirmado no ERP, mas o código local já existe. Associe a referência existente antes de continuar.",
                    409,
                )
            local = StockArticle(
                code=external.code,
                name=external.name,
                erp_id=external.id,
                data=orders.dump(proposed["article"]),
            )
            db.add(local)
            db.flush()
        proposed["article_id"] = local.id
        if payload.get("review"):
            payload["review"]["lines"][proposed["line_index"]]["article_id"] = local.id
            payload.pop("approval", None)
        customer_line_id = proposed.get("customer_line_id")
        if customer_line_id:
            line, customer = orders.get_line(db, user, customer_line_id)
            if (
                not line.article_id
                and customer.store_id == sim.store_id
                and not orders.active_supplies(db, line.id)
                and not orders.reserved(db, line.id)
            ):
                line.article_id = local.id
                customer.revision += 1
        row.data = orders.dump(payload)
        db.commit()
    elif result["state"] == "confirmed" and result["kind"] == "purchase":
        from .db import request_company

        company = request_company(db)
        saved = json.loads(row.data)["review"]
        external = db.scalar(
            select(ERPDocument).where(
                ERPDocument.enterprise_id == company.api_enterprise,
                ERPDocument.external_id == result["external_id"],
            )
        )
        if not external:
            external = ERPDocument(
                enterprise_id=company.api_enterprise,
                external_id=result["external_id"],
                store_id=sim.store_id,
                supplier_nif=saved["supplier_nif"],
                supplier_name=db.get(BusinessEntity, sim.supplier_id).name,
                document=saved["document"],
                internal_document=result["external_id"],
                document_type=saved["document_type"],
                document_date=saved["document_date"],
                amount_cents=saved["total_cents"],
                tax_cents=saved["tax_cents"],
                base_cents=saved["total_cents"] - saved["tax_cents"],
            )
            db.add(external)
            db.flush()
        archive.existing_link(db, doc, "erp_document", external.id)
        row.status = "posted_erp"
    db.commit()
    return view(db, user, row)


class ArticleProposal(orders.Change):
    line_index: int = Field(ge=0, le=199)
    article: stock.ArticleInput
    create_local: bool = False
    customer_line_id: str | None = None


@router.post("/reviews/{identifier}/article-proposal")
def propose_article(
    identifier: str, data: ArticleProposal, user=Depends(current), db: Session = Depends(db_session)
):
    row, sim, doc = get_review(db, user, identifier)
    digest, prior = orders.replay(db, user, "article_proposal", data, identifier)
    if prior:
        return view(db, user, row)
    orders.revision(row, data)
    if row.procurement_id or external_started(row):
        orders.fail("A compra já está lançada.", 409)
    compared = compare(db, user, row, sim, doc)
    if data.line_index >= len(compared["rows"]):
        orders.fail("Linha de origem inexistente.")
    if not data.article.category.strip() or data.article.supplier_id != sim.supplier_id:
        orders.fail("Complete categoria e fornecedor desta encomenda.")
    payload = json.loads(row.data)
    item = {
        "line_index": data.line_index,
        "article": data.article.model_dump(mode="json"),
        "state": "blocked_connector",
        "erp_id": None,
        "customer_line_id": data.customer_line_id,
    }
    if data.create_local:
        if policy(db)["mode"] != "local":
            orders.fail(
                "Criar um artigo local exige modo local; no modo integrado guarde a proposta ERP.",
                409,
            )
        article = stock.save_article(db, user, data.article)
        db.flush()
        item.update(state="created_local", article_id=article.id)
        if payload.get("review"):
            payload["review"]["lines"][data.line_index]["article_id"] = article.id
        if data.customer_line_id:
            line, customer = orders.get_line(db, user, data.customer_line_id)
            if (
                customer.store_id != sim.store_id
                or line.supplier_id != sim.supplier_id
                or line.article_id
                or orders.reserved(db, line.id)
                or orders.active_supplies(db, line.id)
            ):
                orders.fail(
                    "O pedido original não permite associação automática. Resolva as atribuições existentes.",
                    409,
                )
            line.article_id = article.id
            customer.revision += 1
    payload.setdefault("article_proposals", []).append(item)
    payload.pop("approval", None)
    payload.pop("proposal", None)
    row.data = orders.dump(payload)
    row.revision += 1
    orders.event(db, user, "article_proposal", data, digest, row.id, sim.store_id, detail=item)
    db.commit()
    return view(db, user, row)


def warehouses(db, user, store_id):
    authorize(user, db, store_id)
    return list(db.scalars(select(StockWarehouse.id).where(StockWarehouse.store_id == store_id)))


@router.post("/replenishment")
def save_replenishment(
    data: Replenishment, user=Depends(current), db: Session = Depends(db_session)
):
    orders.management(db, user, data.store_id)
    digest, prior = orders.replay(db, user, "replenishment", data)
    if prior:
        return {"ok": True}
    stock.article(db, data.article_id)
    row = db.scalar(
        select(OrderReplenishmentPolicy).where(
            OrderReplenishmentPolicy.store_id == data.store_id,
            OrderReplenishmentPolicy.article_id == data.article_id,
        )
    )
    if (row.revision if row else 0) != data.revision:
        orders.fail("As condições de reposição mudaram. Atualize.", 409)
    if not row:
        row = OrderReplenishmentPolicy(store_id=data.store_id, article_id=data.article_id)
        db.add(row)
    row.data = orders.dump(data.model_dump(mode="json", exclude={"request_key", "revision"}))
    row.revision = (row.revision or 0) + 1
    db.flush()
    orders.event(db, user, "replenishment", data, digest, row.id, data.store_id)
    db.commit()
    return {"ok": True, "revision": row.revision}


def stock_context(db, user, store_id):
    wh = warehouses(db, user, store_id)
    entries = list(db.scalars(select(StockEntry).where(StockEntry.warehouse_id.in_(wh))))
    lots = {line_item.id: line_item for line_item in db.scalars(select(StockLot))}
    holds = {}
    from .models import CustomerOrderReservation

    for r in db.scalars(
        select(CustomerOrderReservation).where(
            CustomerOrderReservation.warehouse_id.in_(wh), CustomerOrderReservation.quantity > 0
        )
    ):
        line = db.get(CustomerOrderLine, r.line_id)
        if line.article_id:
            key = (line.article_id, r.warehouse_id, r.lot_id)
            holds[key] = holds.get(key, ZERO) + r.quantity
    balances = {}
    for e in entries:
        key = (e.article_id, e.warehouse_id, e.lot_id)
        balances[key] = balances.get(key, ZERO) + e.quantity
    free, expiring = {}, {}
    for (aid, wh_id, lot_id), amount in balances.items():
        lot = lots.get(lot_id)
        expiry = lot.expiry if lot else None
        if expiry and expiry < date.today().isoformat():
            continue
        free[aid] = free.get(aid, ZERO) + max(ZERO, amount - holds.get((aid, wh_id, lot_id), ZERO))
        if expiry and expiry <= (date.today() + timedelta(days=30)).isoformat():
            expiring[aid] = expiring.get(aid, ZERO) + max(ZERO, amount)
    incoming, prices = {}, {}
    for doc in db.scalars(
        select(ProcurementDocument)
        .where(
            ProcurementDocument.store_id == store_id,
            ProcurementDocument.confirmed_at.is_not(None),
            ProcurementDocument.status != "cancelled",
        )
        .order_by(ProcurementDocument.created_at)
    ):
        for index, line in enumerate(json.loads(doc.data)["lines"]):
            aid = line.get("article_id")
            if not aid:
                continue
            if doc.kind == "order":
                incoming[aid] = incoming.get(aid, ZERO) + max(
                    ZERO, buying.accepted(line) - buying.used(db, doc.id, index, "receipt")
                )
            if doc.kind in ("receipt", "invoice"):
                prices[aid] = buying.net_price(line)
    demand = {}
    for line in db.scalars(
        select(CustomerOrderLine).join(CustomerOrder).where(CustomerOrder.store_id == store_id)
    ):
        if line.article_id:
            q = orders.quantities(db, line)
            demand[line.article_id] = demand.get(line.article_id, ZERO) + max(
                ZERO,
                q["requested"] - q["cancelled"] - q["delivered"] - q["reserved"] - q["simulated"],
            )
    return free, incoming, demand, expiring, prices, entries


@router.get("/recommendations")
def recommendations(
    store_id: str, supplier_id: str = "", user=Depends(current), db: Session = Depends(db_session)
):
    orders.management(db, user, store_id)
    free, incoming, demand, expiring, prices, entries = stock_context(db, user, store_id)
    policies = {
        r.article_id: r
        for r in db.scalars(
            select(OrderReplenishmentPolicy).where(OrderReplenishmentPolicy.store_id == store_id)
        )
    }
    results = []
    for article in db.scalars(select(StockArticle).order_by(StockArticle.name)):
        if not article.active:
            continue
        meta = json.loads(article.data)
        preferred = meta.get("supplier_id")
        if supplier_id and preferred != supplier_id:
            continue
        row = policies.get(article.id)
        config = (
            Replenishment(**json.loads(row.data), request_key="read-only")
            if row
            else Replenishment(store_id=store_id, article_id=article.id, request_key="read-only")
        )
        terms = orders.terms(db, preferred)
        start = (date.today() - timedelta(days=config.history_days)).isoformat()
        # Only actual sale exits establish demand. Transfers and stock corrections do not.
        sales = sum(
            (
                -e.quantity
                for e in entries
                if e.article_id == article.id
                and e.kind == "sale"
                and start <= e.occurred_on <= date.today().isoformat()
                and e.quantity < 0
            ),
            ZERO,
        )
        daily = sales / D(config.history_days) * config.seasonal_factor
        minimum = D(str(meta.get("minimum", 0)))
        target = max(
            minimum,
            D(str(meta.get("reorder", 0))),
            config.safety + daily * (terms.lead_days + config.coverage_days),
        )
        wanted = max(
            ZERO,
            target
            + demand.get(article.id, ZERO)
            - free.get(article.id, ZERO)
            - incoming.get(article.id, ZERO),
        )
        quantum = D(1).scaleb(-meta.get("precision", 0))
        quantity = wanted.quantize(quantum, rounding="ROUND_UP")
        reasons = [
            f"Stock livre {free.get(article.id, ZERO)}; em trânsito {incoming.get(article.id, ZERO)}; procura de clientes sem reserva física (exclui simulações) {demand.get(article.id, ZERO)}.",
            f"Vendas físicas nos últimos {config.history_days} dias: {sales}; prazo do fornecedor: {terms.lead_days} dias.",
            f"Objetivo de cobertura {target.quantize(D('.000001'))}; mínimo {minimum}; segurança {config.safety}; fator sazonal configurado {config.seasonal_factor}.",
        ]
        warnings = []
        if article.erp_id or policy(db).get("authority", {}).get("stock") == "erp":
            warnings.append(
                "Stock ERP não consultável pelo conector atual; recomendação indisponível."
            )
            quantity = ZERO
        if not preferred:
            warnings.append("Artigo sem fornecedor preferencial.")
        if expiring.get(article.id, ZERO):
            warnings.append(
                f"Existem {expiring[article.id]} unidades a vencer em 30 dias; reveja o risco de excesso."
            )
        if config.shelf_days:
            cap = max(
                ZERO,
                daily * max(0, config.shelf_days - terms.lead_days)
                - free.get(article.id, ZERO)
                - incoming.get(article.id, ZERO),
            )
            cap = cap.quantize(quantum, rounding="ROUND_DOWN")
            customer_need = max(ZERO, demand.get(article.id, ZERO) - free.get(article.id, ZERO))
            if quantity > max(customer_need, cap):
                quantity = max(customer_need, cap).quantize(quantum, rounding="ROUND_UP")
                warnings.append("Quantidade limitada pela validade prevista e procura observada.")
        elif meta.get("expiry"):
            warnings.append("Configure dias de validade antes de recomendar reposição extra.")
            quantity = min(
                quantity, max(ZERO, demand.get(article.id, ZERO) - free.get(article.id, ZERO))
            )
        cost = D(str(terms.prices.get(article.id, prices.get(article.id, 0))))
        if not cost:
            warnings.append("Preço de compra desconhecido; indique um custo antes da simulação.")
        capital = buying.money(quantity * cost)
        if config.budget_cents is not None and capital > config.budget_cents and cost:
            quantity = min(
                quantity,
                (D(config.budget_cents) / 100 / cost).quantize(quantum, rounding="ROUND_DOWN"),
            )
            capital = buying.money(quantity * cost)
            warnings.append("Quantidade limitada pelo teto de capital configurado.")
        sale = D(str(meta.get("sale_net", 0)))
        margin = sale - cost if cost and sale else None
        if margin is not None and margin < 0:
            warnings.append("Margem unitária negativa ao preço conhecido; reveja antes de comprar.")
        results.append(
            {
                "article_id": article.id,
                "code": article.code,
                "description": article.name,
                "supplier_id": preferred,
                "free": str(free.get(article.id, ZERO)),
                "incoming": str(incoming.get(article.id, ZERO)),
                "customer_demand": str(demand.get(article.id, ZERO)),
                "daily_sales": str(daily.quantize(D(".000001"))),
                "quantity": str(quantity),
                "unit_cost": str(cost),
                "capital_cents": capital,
                "margin": str(margin) if margin is not None else None,
                "vat": str(meta.get("vat_rate", 23)),
                "reasons": reasons,
                "warnings": warnings,
                "priority": float(daily) / (1 + float(free.get(article.id, ZERO))),
                "policy": config.model_dump(mode="json"),
                "revision": row.revision if row else 0,
            }
        )
    return {
        "items": sorted(results, key=lambda r: (-r["priority"], r["description"])),
        "read_only": True,
        "basis": "Movimentos físicos locais; sem procura inferida quando não existe histórico.",
    }


@router.get("/stock")
def availability(store_id: str, user=Depends(current), db: Session = Depends(db_session)):
    free, incoming, demand, _, _, _ = stock_context(db, user, store_id)
    identifiers = free.keys() | incoming.keys() | demand.keys()
    articles = {
        a.id: a for a in db.scalars(select(StockArticle).where(StockArticle.id.in_(identifiers)))
    }
    return {
        "items": [
            {
                "article_id": aid,
                "code": articles[aid].code,
                "name": articles[aid].name,
                "free": str(free.get(aid, ZERO)),
                "incoming": str(incoming.get(aid, ZERO)),
                "to_order": str(demand.get(aid, ZERO)),
            }
            for aid in identifiers
        ],
        "erp_stock_available": False,
    }


def get_review(db, user, identifier):
    row = db.get(OrderDocumentReview, identifier)
    if not row:
        orders.fail("Conferência não encontrada nesta empresa.", 404)
    sim = orders.get_sim(db, user, row.simulation_id)
    return row, sim, db.get(ArchiveDocument, row.document_id)


def candidates(db, user, sim, reference, description):
    wanted = stock.document_key(reference)
    result = []
    for a in db.scalars(select(StockArticle).where(StockArticle.active.is_(True))):
        meta = json.loads(a.data)
        identifiers = [a.code, *meta.get("barcodes", [])]
        if meta.get("supplier_id") == sim.supplier_id:
            identifiers += [meta.get("supplier_reference", "")]
        exact = bool(wanted) and wanted in {stock.document_key(v) for v in identifiers if v}
        similar = SequenceMatcher(None, a.name.casefold(), description.casefold()).ratio()
        if exact or similar >= 0.6:
            result.append(
                {
                    "article_id": a.id,
                    "code": a.code,
                    "name": a.name,
                    "exact": exact,
                    "score": 1 if exact else round(similar, 3),
                }
            )
    return sorted(result, key=lambda x: (-x["score"], x["code"]))[:8]


def source_snapshot(db, row, sim, doc):
    order = db.get(ProcurementDocument, sim.procurement_id)
    receipts = []
    if order:
        for receipt in db.scalars(
            select(ProcurementDocument).where(
                ProcurementDocument.store_id == sim.store_id,
                ProcurementDocument.kind == "receipt",
                ProcurementDocument.confirmed_at.is_not(None),
                ProcurementDocument.status != "cancelled",
            )
        ):
            lines = json.loads(receipt.data)["lines"]
            if any(line_item.get("source_id") == order.id for line_item in lines):
                receipts.append(
                    {"id": receipt.id, "revision": receipt.revision, "data": receipt.data}
                )
    receipts.sort(key=lambda x: x["id"])
    return {
        "order": order.data if order else None,
        "order_revision": order.revision if order else None,
        "order_status": order.status if order else None,
        "receipts": receipts,
        "original_sha256": doc.sha256,
        "extraction": doc.extraction,
        "corrections": doc.corrections,
        "identity": [
            doc.entity_nif,
            doc.document_number,
            doc.issue_date,
            doc.total_cents,
            doc.tax_cents,
            doc.currency,
            doc.duplicate_of,
        ],
        "review": {
            k: v
            for k, v in json.loads(row.data).items()
            if k not in ("approval", "proposal", "pdf_md5", "erp_operations")
        },
    }


def compare(db, user, row, sim, doc):
    snap = source_snapshot(db, row, sim, doc)
    fingerprint = hashlib.sha256(orders.dump(snap).encode()).hexdigest()
    payload = json.loads(row.data)
    saved = payload.get("review")
    evidence = json.loads(doc.extraction or "{}")
    extracted = evidence.get(
        "purchase_lines", {"lines": [], "warnings": ["Extração de linhas pendente."]}
    )
    billed = saved["lines"] if saved else extracted.get("lines", [])
    source = json.loads(snap["order"]) if snap["order"] else {"lines": []}
    ordered, received = {}, {}
    for line in source["lines"]:
        key = line.get("article_id") or "service:" + line["description"].casefold()
        ordered.setdefault(key, []).append(line)
    receipt_lines = []
    for receipt in snap["receipts"]:
        if row.receipt_id and receipt["id"] != row.receipt_id:
            continue
        for index, line in enumerate(json.loads(receipt["data"])["lines"]):
            if not sim.procurement_id or line.get("source_id") != sim.procurement_id:
                continue
            key = line["article_id"]
            received[key] = received.get(key, ZERO) + buying.accepted(line)
            receipt_lines.append({"receipt_id": receipt["id"], "index": index, "line": line})
    discrepancies, rows, sums = [], [], {"total_cents": 0, "tax_cents": 0}
    if not saved:
        discrepancies.append("Transcreva ou confirme os dados extraídos do documento original.")
    if doc.duplicate_of or doc.processing_status == "duplicado":
        discrepancies.append("Documento original duplicado.")
    if not sim.procurement_id or snap["order_status"] == "cancelled":
        discrepancies.append("Encomenda ao fornecedor inexistente ou cancelada.")
    supplier = db.get(BusinessEntity, sim.supplier_id)
    nif = (saved or {}).get("supplier_nif", doc.entity_nif)
    if not nif or not supplier.tax_number or nif_key(nif) != nif_key(supplier.tax_number):
        discrepancies.append("NIF do fornecedor ausente ou divergente.")
    if (saved or {}).get("currency", doc.currency) != "EUR":
        discrepancies.append("Moeda sem suporte no circuito de compras.")
    key = invoice_key(
        nif,
        (saved or {}).get("document_type", doc.document_code),
        (saved or {}).get("document", doc.document_number),
    )
    fiscal_duplicate = False
    if not key[2]:
        discrepancies.append("Número fiscal não confirmado.")
    for other in db.scalars(select(OrderDocumentReview).where(OrderDocumentReview.id != row.id)):
        raw = json.loads(other.data).get("review", {})
        if (
            raw
            and invoice_key(raw.get("supplier_nif"), raw.get("document_type"), raw.get("document"))
            == key
        ):
            discrepancies.append("A mesma identidade fiscal está noutra conferência.")
            fiscal_duplicate = True
            break
    own_purchase = db.get(ProcurementDocument, row.procurement_id) if row.procurement_id else None
    own_erp_ids = {
        o.get("external_id")
        for o in payload.get("erp_operations", [])
        if o["kind"] == "purchase" and o["state"] == "confirmed"
    }
    if key[0] and key[2]:
        for existing in db.scalars(select(Purchase)):
            if own_purchase and existing.id == own_purchase.purchase_id:
                continue
            if invoice_key(existing.supplier_nif, existing.document_type, existing.document) == key:
                fiscal_duplicate = True
        for existing in db.scalars(select(ERPDocument).where(ERPDocument.cancelled.is_(False))):
            if (
                existing.external_id not in own_erp_ids
                and invoice_key(existing.supplier_nif, existing.document_type, existing.document)
                == key
            ):
                fiscal_duplicate = True
    if fiscal_duplicate:
        discrepancies.append(
            "Identidade fiscal já registada nesta empresa. Resolva a duplicação antes de lançar."
        )
    billed_totals = {}
    for i, line in enumerate(billed):
        aid = line.get("article_id")
        matches = candidates(db, user, sim, line.get("reference", ""), line.get("description", ""))
        # Exact matches are proposals until the operator saves the reviewed mapping.
        exact = [m for m in matches if m["exact"]]
        suggested = aid or (exact[0]["article_id"] if len(exact) == 1 else None)
        if not aid:
            discrepancies.append(f"Linha {i + 1}: artigo por identificar/confirmar.")
        original = ordered.get(aid, [])
        q, price, discount, vat = (
            D(str(line.get(k, 0))) for k in ("quantity", "price", "discount", "vat")
        )
        net = price * (1 - discount / 100)
        base = buying.money(q * net)
        tax = buying.money(D(base) / 100 * vat / 100)
        sums["total_cents"] += base + tax
        sums["tax_cents"] += tax
        if aid:
            billed_totals[aid] = billed_totals.get(aid, ZERO) + q
        notes = []
        if not original:
            notes.append("Artigo não encomendado ou novo.")
        elif any(
            buying.net_price(line_item) != net or D(line_item["vat"]) != vat
            for line_item in original
        ):
            notes.append("Preço/desconto/IVA diferente do encomendado.")
        rows.append(
            {
                "index": i,
                **line,
                "suggested_article_id": suggested,
                "candidates": matches,
                "ordered": str(sum((buying.accepted(line_item) for line_item in original), ZERO)),
                "received": str(received.get(aid, ZERO)),
                "billed": str(q),
                "notes": notes,
            }
        )
        discrepancies += [f"Linha {i + 1}: {n}" for n in notes]
    for aid, amount in billed_totals.items():
        if amount > received.get(aid, ZERO):
            discrepancies.append("Quantidade faturada superior à receção física: " + aid)
        if amount != sum((buying.accepted(line_item) for line_item in ordered.get(aid, [])), ZERO):
            discrepancies.append("Faturação parcial ou quantidade diferente da encomenda: " + aid)
    missing = [
        {
            "article_id": aid,
            "description": lines[0]["description"],
            "ordered": str(sum((buying.accepted(line_item) for line_item in lines), ZERO)),
            "received": str(received.get(aid, ZERO)),
            "billed": "0",
        }
        for aid, lines in ordered.items()
        if aid not in billed_totals
    ]
    if missing:
        discrepancies.append("Existem linhas encomendadas ausentes desta fatura.")
    if saved:
        if sums["total_cents"] != saved["total_cents"] or sums["tax_cents"] != saved["tax_cents"]:
            discrepancies.append("Soma das linhas/IVA diferente dos totais fiscais declarados.")
        for field, original_value in (
            ("total_cents", doc.total_cents),
            ("tax_cents", doc.tax_cents),
        ):
            if original_value is not None and saved[field] != original_value:
                discrepancies.append("Total revisto diferente do extraído do original: " + field)
    if not billed:
        discrepancies.append("Documento sem linhas de artigos conferíveis.")
    approval = payload.get("approval", {})
    return {
        "rows": rows,
        "missing": missing,
        "discrepancies": list(dict.fromkeys(discrepancies)),
        "totals": sums,
        "fingerprint": fingerprint,
        "approved": approval.get("fingerprint") == fingerprint,
        "fiscal_duplicate": fiscal_duplicate,
        "automatic_allowed": saved is not None and not discrepancies,
        "warnings": extracted.get("warnings", []),
        "receipt_lines": receipt_lines,
    }


def view(db, user, row):
    row, sim, doc = get_review(db, user, row.id)
    result = compare(db, user, row, sim, doc)
    jobs = list(db.scalars(select(ArchiveJob).where(ArchiveJob.document_id == doc.id)))
    return {
        "id": row.id,
        "revision": row.revision,
        "status": row.status,
        "simulation_id": sim.id,
        "supplier_id": sim.supplier_id,
        "supplier_name": db.get(BusinessEntity, sim.supplier_id).name,
        "store_id": sim.store_id,
        "receipt_id": row.receipt_id,
        "document_id": doc.id,
        "document_name": doc.original_name,
        "processing_status": doc.processing_status,
        "file_url": f"/api/archive/documents/{doc.id}/file",
        "procurement_id": row.procurement_id,
        "cloud_status": row.cloud_status,
        "cloud_error": row.cloud_error,
        "cloud_id": row.drive_id,
        "jobs": [
            {"kind": j.kind, "status": j.status, "error": j.error, "attempts": j.attempts}
            for j in jobs
        ],
        "review": json.loads(row.data).get("review")
        or {
            "supplier_nif": doc.entity_nif,
            "document": doc.document_number,
            "document_type": doc.document_code or "FT",
            "document_date": doc.issue_date or date.today().isoformat(),
            "due_date": doc.issue_date or date.today().isoformat(),
            "currency": doc.currency,
            "total_cents": doc.total_cents or 0,
            "tax_cents": doc.tax_cents or 0,
            "note": "",
        },
        "proposal": json.loads(row.data).get("proposal"),
        "article_proposals": json.loads(row.data).get("article_proposals", []),
        "comparison": result,
        "erp_operations": json.loads(row.data).get("erp_operations", []),
        "erp_creation_available": policy(db).get("mode") == "integrated"
        and policy(db).get("provider") == "wintouch"
        and policy(db).get("active")
        and policy(db).get("writes"),
        "erp_message": "Wintouch Cloud: prepare e confirme cada escrita. Resultados incertos são recuperados por consulta do identificador. Sem autorização de escrita, mantenha a proposta para aprovação.",
    }


@router.get("/reviews")
def reviews(store_id: str = "", user=Depends(current), db: Session = Depends(db_session)):
    orders.management(db, user, store_id or None)
    stores = scope(user, db)
    if store_id:
        stores = [store_id]
    sims = list(
        db.scalars(
            select(CustomerPurchaseSimulation).where(
                CustomerPurchaseSimulation.store_id.in_(stores)
            )
        )
    )
    rows = list(
        db.scalars(
            select(OrderDocumentReview)
            .where(OrderDocumentReview.simulation_id.in_([s.id for s in sims]))
            .order_by(OrderDocumentReview.created_at.desc())
        )
    )
    return {"items": [view(db, user, r) for r in rows]}


@router.post("/reviews")
def link_original(data: LinkOriginal, user=Depends(current), db: Session = Depends(db_session)):
    sim = orders.get_sim(db, user, data.simulation_id)
    digest, prior = orders.replay(db, user, "original_link", data)
    if prior:
        return view(db, user, db.get(OrderDocumentReview, prior.target_id))
    from .archive_api import get_doc

    doc = get_doc(db, user, data.document_id)
    if doc.store_id != sim.store_id or not sim.procurement_id:
        orders.fail("Associe um original da mesma loja a uma encomenda preparada.")
    existing = db.scalar(
        select(OrderDocumentReview).where(OrderDocumentReview.document_id == doc.id)
    )
    if existing:
        if existing.simulation_id == sim.id and existing.receipt_id == data.receipt_id:
            return view(db, user, existing)
        orders.fail("Este original já está associado a outra conferência.", 409)
    if data.receipt_id:
        receipt = db.get(ProcurementDocument, data.receipt_id)
        if (
            not receipt
            or receipt.status == "cancelled"
            or not receipt.confirmed_at
            or receipt.kind != "receipt"
            or receipt.store_id != sim.store_id
            or receipt.supplier_id != sim.supplier_id
            or not any(
                line_item.get("source_id") == sim.procurement_id
                for line_item in json.loads(receipt.data)["lines"]
            )
        ):
            orders.fail("Escolha uma receção confirmada desta encomenda.")
    row = OrderDocumentReview(
        document_id=doc.id,
        simulation_id=sim.id,
        receipt_id=data.receipt_id,
        user_id=user.id,
        name=doc.original_name,
        mime=doc.mime,
        size=doc.size,
        digest=doc.sha256,
    )
    db.add(row)
    db.flush()
    archive.existing_link(
        db, doc, "procurement", sim.procurement_id, "Encomenda ao fornecedor conferida neste ciclo"
    )
    config = db.get(AttachmentIntegrationConfig, "default")
    if doc.storage == "drive":
        row.cloud_status = "confirmed"
        row.drive_id, row.account, row.drive_folder = (
            doc.storage_ref,
            doc.storage_account,
            doc.storage_folder,
        )
    elif config and config.drive_folder and config.google_email:
        archive.queue(db, doc.id, "order_cloud")
    else:
        row.cloud_status = "not_configured"
    if doc.processing_status != "processado":
        archive.queue(db, doc.id, "extract")
    orders.event(
        db,
        user,
        "original_link",
        data,
        digest,
        row.id,
        sim.store_id,
        detail={"document_id": doc.id, "simulation_id": sim.id},
    )
    archive.event(
        db, doc.id, user, "encomenda_associada", {"simulation_id": sim.id, "review_id": row.id}
    )
    db.commit()
    return view(db, user, row)


@router.put("/reviews/{identifier}")
def save_review(
    identifier: str, data: ReviewInput, user=Depends(current), db: Session = Depends(db_session)
):
    row, sim, doc = get_review(db, user, identifier)
    digest, prior = orders.replay(db, user, "review_save", data, identifier)
    if prior:
        return view(db, user, row)
    orders.revision(row, data)
    if row.procurement_id or external_started(row):
        orders.fail("A compra já foi lançada. Use o circuito de correção de compras.", 409)
    if data.document_date > date.today() or data.due_date < data.document_date:
        orders.fail("Confirme as datas de emissão e vencimento.")
    for line in data.lines:
        if line.article_id:
            article = stock.article(db, line.article_id)
            if not article.active:
                orders.fail("Artigo descontinuado; escolha um artigo ativo.")
    payload = json.loads(row.data)
    payload["review"] = data.model_dump(mode="json", exclude={"request_key", "revision"})
    payload.pop("approval", None)
    payload.pop("proposal", None)
    row.data, row.status = orders.dump(payload), "reviewed"
    row.revision += 1
    orders.event(db, user, "review_save", data, digest, row.id, sim.store_id)
    db.commit()
    return view(db, user, row)


@router.post("/reviews/{identifier}/approve")
def approve(
    identifier: str, data: Decision, user=Depends(current), db: Session = Depends(db_session)
):
    row, sim, doc = get_review(db, user, identifier)
    digest, prior = orders.replay(db, user, "review_approve", data, identifier)
    if prior:
        return view(db, user, row)
    orders.revision(row, data)
    if row.procurement_id or external_started(row):
        orders.fail("Existe um lançamento ou uma tentativa ERP por resolver.", 409)
    compared = compare(db, user, row, sim, doc)
    if compared["fingerprint"] != data.fingerprint or not data.confirmed:
        orders.fail("Os dados mudaram ou falta confirmação explícita. Volte a conferir.", 409)
    payload = json.loads(row.data)
    if not payload.get("review") or not compared["rows"]:
        orders.fail("Guarde primeiro os dados e artigos conferidos.")
    payload["approval"] = {
        "fingerprint": data.fingerprint,
        "reason": data.reason,
        "user_id": user.id,
        "at": now().isoformat(),
        "discrepancies": compared["discrepancies"],
    }
    row.data, row.status = orders.dump(payload), "approved"
    row.revision += 1
    orders.event(
        db, user, "review_approve", data, digest, row.id, sim.store_id, detail=payload["approval"]
    )
    db.commit()
    return view(db, user, row)


@router.post("/reviews/{identifier}/proposal")
def proposal(
    identifier: str, data: Decision, user=Depends(current), db: Session = Depends(db_session)
):
    row, sim, doc = get_review(db, user, identifier)
    digest, prior = orders.replay(db, user, "erp_proposal", data, identifier)
    if prior:
        return view(db, user, row)
    orders.revision(row, data)
    compared = compare(db, user, row, sim, doc)
    if not compared["approved"] or data.fingerprint != compared["fingerprint"]:
        orders.fail("Aprove a conferência atual antes de propor o lançamento.", 409)
    payload = json.loads(row.data)
    payload["proposal"] = {
        "state": "blocked_connector",
        "reason": "Falta contrato de criação de compras validado no conector ERP.",
        "review": payload["review"],
        "article_proposals": payload.get("article_proposals", []),
        "original_id": doc.id,
        "order_id": sim.procurement_id,
        "erp_id": None,
        "idempotency_key": "order-review:" + row.id,
        "at": now().isoformat(),
    }
    row.data, row.status = orders.dump(payload), "proposed"
    row.revision += 1
    orders.event(
        db,
        user,
        "erp_proposal",
        data,
        digest,
        row.id,
        sim.store_id,
        detail={"state": "blocked_connector"},
    )
    db.commit()
    return view(db, user, row)


@router.get("/reviews/{identifier}/proposal")
def export_proposal(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
    row, _, _ = get_review(db, user, identifier)
    payload = json.loads(row.data).get("proposal")
    if not payload:
        orders.fail("Prepare primeiro a proposta.", 404)
    from fastapi.responses import JSONResponse

    return JSONResponse(
        payload,
        headers={
            "Content-Disposition": f'attachment; filename="proposta-{row.id}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/reviews/{identifier}/post-local")
def post_local(
    identifier: str, data: Decision, user=Depends(current), db: Session = Depends(db_session)
):
    row, sim, doc = get_review(db, user, identifier)
    digest, prior = orders.replay(db, user, "review_post", data, identifier)
    if prior:
        return view(db, user, row)
    orders.revision(row, data)
    if policy(db)["mode"] != "local" or not data.confirmed or external_started(row):
        orders.fail("O lançamento local exige modo local e confirmação explícita.", 409)
    compared = compare(db, user, row, sim, doc)
    if (
        not compared["approved"]
        or compared["fingerprint"] != data.fingerprint
        or row.procurement_id
    ):
        orders.fail("Conferência alterada, não aprovada ou já lançada.", 409)
    saved = json.loads(row.data)["review"]
    supplier = db.get(BusinessEntity, sim.supplier_id)
    if (
        not saved["supplier_nif"]
        or nif_key(saved["supplier_nif"]) != nif_key(supplier.tax_number)
        or doc.duplicate_of
        or compared["fiscal_duplicate"]
        or saved["currency"] != "EUR"
    ):
        orders.fail("Resolva a identidade fiscal, duplicação e moeda antes de lançar.", 409)
    if any(saved[k] != compared["totals"][k] for k in ("total_cents", "tax_cents")):
        orders.fail("Resolva os totais fiscais antes de lançar.", 409)
    available = [
        {
            **item,
            "left": max(
                ZERO,
                buying.accepted(item["line"])
                - buying.used(db, item["receipt_id"], item["index"], "invoice"),
            ),
        }
        for item in compared["receipt_lines"]
    ]
    lines = []
    for billed in saved["lines"]:
        article = stock.article(db, billed["article_id"]) if billed.get("article_id") else None
        if not article:
            orders.fail("Associe todos os artigos reais antes de lançar.")
        q = D(billed["quantity"])
        for part in available:
            if part["line"]["article_id"] != article.id or not part["left"]:
                continue
            used = min(q, part["left"])
            lines.append(
                buying.Line(
                    article_id=article.id,
                    description=billed["description"],
                    unit=json.loads(article.data).get("unit", "un"),
                    quantity=used,
                    price=billed["price"],
                    discount=billed["discount"],
                    vat=billed["vat"],
                    warehouse_id=part["line"]["warehouse_id"],
                    source_id=part["receipt_id"],
                    source_line=part["index"],
                )
            )
            part["left"] -= used
            q -= used
            if not q:
                break
        if q:
            orders.fail(
                "A quantidade faturada não cabe nas receções físicas ainda por faturar.", 409
            )
    payload = buying.Draft(
        kind="invoice",
        store_id=sim.store_id,
        supplier_id=sim.supplier_id,
        document=saved["document"],
        document_type=saved["document_type"],
        document_date=saved["document_date"],
        due_date=saved["due_date"],
        notes=saved["note"],
        request_key="review:" + row.id,
        lines=lines,
    )
    buying.validate(db, user, payload, authorize)
    raw = payload.model_dump(mode="json")
    raw.update(buying.totals(raw["lines"]))
    if raw["total_cents"] != saved["total_cents"] or raw["tax_cents"] != saved["tax_cents"]:
        orders.fail(
            "O fracionamento das receções altera o arredondamento fiscal. Reveja as linhas antes de lançar.",
            409,
        )
    purchase = ProcurementDocument(
        kind="invoice",
        store_id=sim.store_id,
        supplier_id=sim.supplier_id,
        user_id=user.id,
        request_key=payload.request_key,
        data=orders.dump(raw),
    )
    db.add(purchase)
    db.flush()
    buying.confirm_document(
        db,
        user,
        purchase,
        buying.Confirm(revision=0, acknowledge_differences=True),
        authorize,
        record,
    )
    row.procurement_id, row.status = purchase.id, "posted_local"
    row.revision += 1
    archive.existing_link(db, doc, "purchase", purchase.purchase_id)
    orders.event(
        db,
        user,
        "review_post",
        data,
        digest,
        row.id,
        sim.store_id,
        detail={"procurement_id": purchase.id},
    )
    db.commit()
    return view(db, user, row)


@router.post("/reviews/{identifier}/retry")
def retry(
    identifier: str, data: orders.Change, user=Depends(current), db: Session = Depends(db_session)
):
    row, sim, doc = get_review(db, user, identifier)
    digest, prior = orders.replay(db, user, "review_retry", data, identifier)
    if not prior:
        orders.revision(row, data)
        for kind in ("extract", "order_cloud"):
            job = db.scalar(
                select(ArchiveJob).where(ArchiveJob.document_id == doc.id, ArchiveJob.kind == kind)
            )
            if job and job.status == "em_curso":
                orders.fail("O processamento ainda está em curso.", 409)
            if kind != "order_cloud" or row.cloud_status != "confirmed":
                archive.queue(db, doc.id, kind, reset=True)
        row.revision += 1
        orders.event(db, user, "review_retry", data, digest, row.id, sim.store_id)
        db.commit()
    return view(db, user, row)


def transfer_original(db, doc):
    """Uses the established Drive generated-ID protocol; retains encrypted local original."""
    from .google_invoices import DRIVE, api_json, authorized_client, upload
    from .scan_storage import GoogleDriveStorage

    row = db.scalar(select(OrderDocumentReview).where(OrderDocumentReview.document_id == doc.id))
    if not row or row.cloud_status == "confirmed":
        return
    raw, mime = archive.read_bytes(db, doc)
    archive.verify(db, doc, raw)
    client, config = authorized_client(db)
    with closing(client):
        if not config.drive_folder:
            orders.fail("Configure a pasta cloud em Anexos.", 409)
        if row.account and row.account.casefold() != config.google_email.casefold():
            orders.fail("Recupere a conta cloud original antes de repetir.", 409)
        row.account = config.google_email
        row.name, row.mime, row.size, row.digest = doc.original_name, mime, len(raw), doc.sha256
        db.commit()
        GoogleDriveStorage(client).check_folder(config.drive_folder)
        upload(client, db, row, config.drive_folder, db.info.get("company", {}).get("id", ""), raw)
        info = api_json(
            client.get(
                DRIVE + "/files/" + quote(row.drive_id, safe=""),
                params={
                    "fields": "id,size,mimeType,md5Checksum,appProperties,parents,trashed",
                    "supportsAllDrives": True,
                },
            ),
            "Confirmar original da encomenda",
        )
        if (
            info.get("trashed")
            or info.get("id") != row.drive_id
            or info.get("mimeType") != mime
            or int(info.get("size", -1)) != len(raw)
            or info.get("md5Checksum") != hashlib.md5(raw).hexdigest()
            or info.get("appProperties", {}).get("gpgest_hash") != doc.sha256
            or row.drive_folder not in info.get("parents", [])
        ):
            raise archive.TransientError(
                "O Drive ainda não confirmou o original completo; nova tentativa usa o mesmo identificador."
            )
    row.cloud_status, row.cloud_error = "confirmed", ""
    archive.event(db, doc.id, None, "original_cloud_confirmado", {"cloud_id": row.drive_id})


@router.get("/automation/{store_id}")
def read_automation(store_id: str, user=Depends(current), db: Session = Depends(db_session)):
    orders.management(db, user, store_id)
    row = db.get(OrderCycleSettings, store_id)
    return {
        **(
            json.loads(row.data)
            if row
            else Automation(request_key="read-only").model_dump(mode="json")
        ),
        "revision": row.revision if row else 0,
    }


@router.post("/automation/{store_id}")
def save_automation(
    store_id: str, data: Automation, user=Depends(current), db: Session = Depends(db_session)
):
    authorize(user, db, store_id, ["administrador"])
    digest, prior = orders.replay(db, user, "auto_notify_config", data, store_id)
    if prior:
        return {"ok": True}
    row = db.get(OrderCycleSettings, store_id)
    if (row.revision if row else 0) != data.revision:
        orders.fail("Configuração alterada. Atualize.", 409)
    if not row:
        row = OrderCycleSettings(store_id=store_id, user_id=user.id)
        db.add(row)
    row.user_id, row.revision, row.data = (
        user.id,
        (row.revision or 0) + 1,
        orders.dump(data.model_dump(mode="json", exclude={"request_key", "revision"})),
    )
    orders.event(db, user, "auto_notify_config", data, digest, store_id, store_id)
    db.commit()
    return {"ok": True}


def automated_notifications(db, limit=10):
    from .email_delivery import SendInput

    sent, failed = 0, 0
    for config in db.scalars(select(OrderCycleSettings)):
        settings = json.loads(config.data)
        actor = db.get(User, config.user_id)
        if (
            not settings.get("enabled")
            or not actor
            or not actor.active
            or actor.role != "administrador"
        ):
            continue
        for row in db.scalars(
            select(CustomerOrder).where(CustomerOrder.store_id == config.store_id)
        ):
            if sent + failed >= limit:
                return {"sent": sent, "failed": failed}
            key = None
            try:
                authorize(actor, db, row.store_id, orders.WRITE)
                stock.lock(db)
                db.expire_all()
                original = json.loads(row.data)
                state = orders.order_view(db, row)
                if (
                    not original["contact_allowed"]
                    or original["preferred_channel"] != "email"
                    or not state["ready"]
                    or state["notified"]
                ):
                    db.rollback()
                    continue
                availability = {
                    line_item["id"]: str(
                        D(line_item["quantities"]["available"])
                        + D(line_item["quantities"]["delivered"])
                    )
                    for line_item in state["lines"]
                    if D(line_item["quantities"]["available"]) > 0
                }
                token = hashlib.sha256(
                    orders.dump(
                        {
                            "availability": availability,
                            "email": original["email"],
                            "config_revision": config.revision,
                        }
                    ).encode()
                ).hexdigest()
                key = "auto-ready:" + row.id + ":" + token[:32]
                prior = db.scalar(
                    select(CustomerOrderEvent).where(CustomerOrderEvent.request_key == key)
                )
                # Never retry a failed/uncertain/accepted transport automatically, even with a new scheduler run.
                if prior:
                    db.rollback()
                    continue
                message = (
                    settings["template"]
                    .replace("{nome}", original["customer_name"])
                    .replace("{numero}", state["number"])
                )
                prepared = orders.prepare_contact(
                    row.id,
                    orders.Contact(
                        request_key=key, revision=row.revision, channel="email", message=message
                    ),
                    actor,
                    db,
                )
                mail = prepared["email"]
                orders.send_contact(
                    row.id,
                    mail["id"],
                    SendInput(revision=mail["revision"], request_key=key + ":send", confirmed=True),
                    actor,
                    db,
                )
                sent += 1
            except Exception:
                db.rollback()
                failed += 1
                if key and not db.scalar(
                    select(CustomerOrderEvent).where(CustomerOrderEvent.request_key == key)
                ):
                    change = orders.Change(request_key=key)
                    digest, prior = orders.replay(db, actor, "auto_notify_error", change, row.id)
                    if not prior:
                        orders.event(
                            db,
                            actor,
                            "auto_notify_error",
                            change,
                            digest,
                            row.id,
                            row.store_id,
                            row.id,
                            detail={
                                "result": "failed",
                                "message": "Não foi possível preparar o email automático. Reveja SMTP, contactos e autorização antes de repetir manualmente.",
                            },
                        )
                        db.commit()
    return {"sent": sent, "failed": failed}


def scheduled_tick():
    from . import db as database
    from .platform_models import Company

    with database.next_platform_session() as core:
        companies = list(core.scalars(select(Company).where(Company.active == 1)))
        for company in companies:
            core.expunge(company)
    for company in companies:
        try:
            with database.company_db(company) as db:
                automated_notifications(db)
                automated_erp(db)
        except Exception:
            continue


def automated_erp(db, limit=5):
    count = 0
    for config in db.scalars(select(OrderCycleSettings)):
        if not json.loads(config.data).get("auto_erp_purchases"):
            continue
        actor = db.get(User, config.user_id)
        if not actor or not actor.active or actor.role != "administrador":
            continue
        for review in db.scalars(
            select(OrderDocumentReview)
            .join(CustomerPurchaseSimulation)
            .where(CustomerPurchaseSimulation.store_id == config.store_id)
        ):
            if count >= limit:
                return count
            try:
                row, sim, doc = get_review(db, actor, review.id)
                compared = compare(db, actor, row, sim, doc)
                if not compared["approved"] or not compared["automatic_allowed"]:
                    continue
                for operation in json.loads(row.data).get("erp_operations", []):
                    if (
                        operation["kind"] != "purchase"
                        or operation["state"] != "prepared"
                        or operation["fingerprint"] != compared["fingerprint"]
                        or operation.get("automatic_attempted")
                    ):
                        continue
                    payload = json.loads(row.data)
                    next(o for o in payload["erp_operations"] if o["id"] == operation["id"])[
                        "automatic_attempted"
                    ] = True
                    row.data = orders.dump(payload)
                    row.revision += 1
                    db.commit()
                    data = Decision(
                        request_key="auto-erp:" + operation["id"],
                        revision=row.revision,
                        fingerprint=compared["fingerprint"],
                        confirmed=True,
                        confirm_demo=bool(operation.get("confirm_demo")),
                        reason="Envio automático autorizado pelo administrador da loja",
                    )
                    send_erp(row.id, operation["id"], data, actor, db)
                    count += 1
            except Exception:
                db.rollback()
                payload = json.loads(review.data)
                for operation in payload.get("erp_operations", []):
                    if operation.get("automatic_attempted") and operation["state"] == "prepared":
                        operation["error"] = (
                            "Não foi possível iniciar o envio automático. Reveja a ligação, permissões e conferência; repita manualmente."
                        )
                review.data = orders.dump(payload)
                db.commit()
    return count
