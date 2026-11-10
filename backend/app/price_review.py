"""Post-receipt price review. Price writes are separate from stock posting.

Dois custos por artigo da receção (ambos sem IVA dedutível, com o IVA não recuperável quando a
empresa não o recupera, como no stock):

- custo de referência: valor líquido ÷ unidades faturadas, sem repartir o bónus. É a única base do
  PVP sugerido e da comparação com a compra anterior;
- custo efetivo: valor líquido ÷ (faturadas + bónus), o custo em stock. Só serve para mostrar a margem
  efetiva da receção e o ganho do bónus; nunca reduz o PVP sugerido.
"""

import json
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal as D
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import inventory as stock
from .db import db_session
from .models import ProcurementDocument, StockArticle, StockEntry, StockWarehouse, WintouchProduct
from .schemas import Strict


CENT = D("0.01")
PRICE_ROLES = ["administrador", "gestor"]


class Proposal(Strict):
    article_id: str
    article_revision: int = Field(ge=0)
    method: Literal["margin", "markup", "increase", "manual"]
    value: D = Field(ge=0, le=10**9, decimal_places=6)
    confirmed_net: D | None = Field(default=None, gt=0, le=10**9, decimal_places=6)


class ReviewInput(Strict):
    receipt_revision: int = Field(ge=0)
    rounding: Literal["net_cent", "0.05", "0.10", "0.50", "1.00"] = "net_cent"
    lines: list[Proposal] = Field(default_factory=list, max_length=500)


class ApplyReview(ReviewInput):
    request_key: str = Field(min_length=1, max_length=100)
    confirmed: Literal[True]
    finish: bool = False


def fail(message, status=409):
    raise HTTPException(status, message)


def gross(net, vat):
    return (net * (1 + vat / 100)).quantize(CENT, rounding=ROUND_HALF_UP) if net is not None and vat is not None else None


def margin(net, cost):
    return (net - cost) / net * 100 if net and cost is not None else None


def proposed_price(cost, current, vat, method, value, rounding):
    """PVP sem IVA proposto. `cost` é sempre o custo de referência (sem repartir bónus)."""
    if method == "margin":
        if cost is None or value >= 100:
            fail("Sem custo de referência ou margem igual ou superior a 100%: indique o PVP manualmente.", 422)
        exact = cost / (1 - value / 100)
    elif method == "markup":
        if cost is None:
            fail("Sem custo de referência: indique o PVP manualmente.", 422)
        exact = cost * (1 + value / 100)
    elif method == "increase":
        if current is None:
            fail("A percentagem de aumento requer um PVP atual.", 422)
        exact = current * (1 + value / 100)
    else:
        exact = value
    if not 0 < exact <= 10**9:
        fail("O preço proposto tem de ser superior a zero e estar dentro do limite permitido.", 422)
    if rounding == "net_cent":
        net = exact.quantize(CENT, rounding=ROUND_CEILING if method in ("margin", "markup") else ROUND_HALF_UP)
    else:
        if vat is None:
            fail("O arredondamento com IVA requer a taxa de venda.", 422)
        step = D(rounding)
        factor = 1 + vat / 100
        target = (exact * factor / step).to_integral_value(rounding=ROUND_CEILING) * step
        net = (target / factor).quantize(D("0.000001"), rounding=ROUND_CEILING)
    if not 0 < net <= 10**9:
        fail("O preço proposto tem de ser superior a zero e estar dentro do limite permitido.", 422)
    return net


def grouped_entries(db, payload):
    ids = {identifier for line in payload["lines"] for identifier in line.get("stock_entry_ids", [])}
    groups = {}
    for entry in db.scalars(select(StockEntry).where(StockEntry.id.in_(ids), StockEntry.kind == "purchase", StockEntry.quantity > 0)):
        groups.setdefault(entry.article_id, []).append(entry)
    return groups


def effective_cost(entries):
    """Valor líquido ÷ todas as unidades recebidas (bónus incluído): o custo em stock."""
    if any(entry.unit_cost is None or entry.unit_cost < 0 for entry in entries):
        return None
    return sum(entry.quantity * entry.unit_cost for entry in entries) / sum(entry.quantity for entry in entries)


def billed_units(entry):
    """Unidades faturadas de uma entrada de compra; as unidades de bónus não contam.

    O preço do documento (sem repartir ofertas) dá as unidades pagas: valor ÷ preço. Uma entrada a
    preço 0 (linha de oferta) só tem bónus. Sem preço do documento, a entrada vale ao seu custo.
    """
    price = stock.purchase_price(entry)
    if price is None or price <= 0 or not entry.unit_cost:
        return D(0)
    return (entry.quantity * entry.unit_cost / price).quantize(D("0.000001"), rounding=ROUND_HALF_UP)


def reference_cost(entries):
    """Valor líquido ÷ unidades faturadas (bónus excluído). None sem unidades faturadas (oferta total)."""
    if any(entry.unit_cost is None or entry.unit_cost < 0 for entry in entries):
        return None
    billed = sum(billed_units(entry) for entry in entries)
    if billed <= 0:
        return None
    return (sum(entry.quantity * entry.unit_cost for entry in entries) / billed).quantize(D("0.00000001"), rounding=ROUND_HALF_UP)


def costs(entries):
    """Custos e quantidades da receção de um artigo, e o ganho do bónus ao custo de referência."""
    reference, effective = reference_cost(entries), effective_cost(entries)
    received = sum(entry.quantity for entry in entries)
    billed = sum(billed_units(entry) for entry in entries) if reference is not None else None
    bonus = max(received - billed, D(0)) if billed is not None else None
    return {
        "reference_cost": reference, "effective_cost": effective, "received_quantity": received,
        "billed_quantity": billed, "bonus_quantity": bonus,
        # Ganho do bónus: as unidades oferecidas valorizadas ao custo de referência.
        "bonus_gain": reference * bonus if reference is not None and bonus else D(0) if reference is not None else None,
        "bonus_gain_unit": reference - effective if reference is not None and effective is not None else None,
    }


def default_pricing(config, family):
    """Método e valor por omissão: os da família ou, sem eles, os da empresa."""
    method = config["pricing_method_by_family"].get(family) or config["pricing_method"]
    if method == "markup":
        return "markup", D(config["markup_by_family"].get(family, config["markup_default"]))
    return "margin", D(config["margin_by_family"].get(family, config["margin_minimum"]))


def previous_costs(db, groups, receipt_id, stores):
    warehouses = select(StockWarehouse.id).where(StockWarehouse.store_id.in_(stores))
    cancelled = set(db.scalars(select(ProcurementDocument.id).where(ProcurementDocument.status == "cancelled")))
    reversals = {json.loads(entry.data).get("reversal_of") for entry in db.scalars(select(StockEntry).where(
        StockEntry.article_id.in_(groups), StockEntry.data.like('%"reversal_of"%')))}
    cutoff = {article: min(entry.created_at for entry in entries) for article, entries in groups.items()}
    origins, latest = {}, {}
    for entry in db.scalars(select(StockEntry).where(
        StockEntry.article_id.in_(groups), StockEntry.warehouse_id.in_(warehouses), StockEntry.kind == "purchase",
        StockEntry.quantity > 0, StockEntry.unit_cost >= 0, StockEntry.created_at < max(cutoff.values()),
    ).order_by(StockEntry.created_at.desc(), StockEntry.id.desc()).execution_options(yield_per=100)):
        if entry.id in reversals or entry.created_at >= cutoff[entry.article_id]:
            continue
        origin = json.loads(entry.data).get("procurement_id")
        if origin == receipt_id or origin in cancelled:
            continue
        key = ("receipt", origin) if origin else ("entry", entry.id)
        if origins.setdefault(entry.article_id, key) == key:
            latest.setdefault(entry.article_id, []).append(entry)
    # Custo de referência da última compra válida: compara-se sempre sem bónus com sem bónus.
    return {article: reference_cost(entries) for article, entries in latest.items()}


def article_values(db, article):
    meta = json.loads(article.data)
    if not article.erp_id:
        return D(meta.get("sale_net", 0)) or None, D(meta.get("vat_rate", 0)), None
    erp = db.get(WintouchProduct, article.erp_id)
    if not erp:
        return None, None, None
    net, vat, with_vat = erp.sale_price, erp.vat_rate, erp.sale_price_vat
    if net is None and with_vat is not None and vat is not None:
        net = with_vat / (1 + vat / 100)
    return net or None, vat, with_vat


def register_routes(app, current, authorize, scope, audit):
    def access(db, user):
        authorize(user, db, roles=PRICE_ROLES)
        config = stock.settings(db)
        stock.require(user, config, "view_cost")
        return config, set(scope(user, db))

    def receipt(db, identifier, stores):
        row = db.get(ProcurementDocument, identifier)
        if not row or row.kind != "receipt" or row.store_id not in stores or not row.confirmed_at or row.status == "cancelled":
            fail("Receção confirmada indisponível.", 404)
        payload = json.loads(row.data)
        if "price_review" not in payload:
            fail("Esta receção não tem artigos para revisão de preços.", 404)
        return row, payload, payload["price_review"]

    @app.get("/api/price-reviews")
    def pending(user=Depends(current), db: Session = Depends(db_session)):
        _, stores = access(db, user)
        result = []
        for row in db.scalars(select(ProcurementDocument).where(
            ProcurementDocument.store_id.in_(stores), ProcurementDocument.kind == "receipt", ProcurementDocument.confirmed_at.is_not(None),
            ProcurementDocument.status != "cancelled", ProcurementDocument.data.like('%"price_review"%'),
        ).order_by(ProcurementDocument.confirmed_at.desc())):
            payload = json.loads(row.data)
            pending_ids = payload.get("price_review", {}).get("pending", [])
            if pending_ids:
                result.append({"id": row.id, "document": payload.get("document"), "date": payload.get("document_date"), "count": len(pending_ids)})
        return result

    @app.get("/api/price-reviews/{identifier}")
    def details(identifier: str, user=Depends(current), db: Session = Depends(db_session)):
        config, stores = access(db, user)
        row, payload, review = receipt(db, identifier, stores)
        groups = grouped_entries(db, payload)
        history = previous_costs(db, groups, row.id, stores) if groups else {}
        from .wintouch_price_write import availability
        can_change = stock.permissions(user, config)["change_price"]
        erp_write, erp_reason = availability(db, user)
        lines = []
        for article_id, entries in groups.items():
            article = db.get(StockArticle, article_id)
            meta = json.loads(article.data)
            family = meta.get("family")
            values, last = costs(entries), history.get(article_id)
            cost, effective = values["reference_cost"], values["effective_cost"]
            current_net, vat, current_gross = article_values(db, article)
            minimum = D(config["margin_by_family"].get(family, config["margin_minimum"]))
            method, value = default_pricing(config, family)
            try:
                suggestion = proposed_price(cost, current_net, vat, method, value, "net_cent") if cost else None
            except HTTPException:
                suggestion = None
            lines.append({
                "id": article_id, "code": article.code, "name": article.name, "revision": article.revision,
                "quantity": values["received_quantity"], "unit": meta.get("unit", "un"), "family": family or "",
                **values,
                # `cost` é o custo de referência (compatibilidade com quem lia o campo antigo).
                "cost": cost, "last_cost": last,
                "delta": cost - last if cost is not None and last is not None else None,
                "delta_percent": (cost - last) / last * 100 if cost is not None and last else None,
                "net": current_net, "gross": current_gross if current_gross is not None else gross(current_net, vat), "vat": vat,
                # Margens ao PVP atual: comercial (custo de referência) e efetiva da receção (com bónus).
                "margin": margin(current_net, cost), "effective_margin": margin(current_net, effective), "minimum": minimum,
                "default_method": method, "default_value": value, "suggested": suggestion,
                "suggested_gross": gross(suggestion, vat), "suggested_margin": margin(suggestion, cost),
                "suggested_effective_margin": margin(suggestion, effective),
                "erp": bool(article.erp_id), "active": article.active,
                "editable": can_change and article.active and (not article.erp_id or erp_write),
                "pending": article_id in review["pending"],
            })
        return {"id": row.id, "document": payload.get("document"), "revision": row.revision,
                "lines": json.loads(stock.dump(lines)), "cost_alert": config["cost_alert_percent"], "can_change": can_change,
                "erp_write": erp_write, "erp_reason": erp_reason}

    def proposals(db, data, row, payload, review, *, for_write=False, erp_write=False):
        if data.receipt_revision != row.revision:
            fail("A receção mudou. Volte a abrir a revisão.")
        groups = grouped_entries(db, payload)
        seen, result = set(), []
        for item in data.lines:
            if item.article_id in seen or item.article_id not in review["pending"] or item.article_id not in groups:
                fail("A seleção contém um artigo duplicado ou já revisto.")
            seen.add(item.article_id)
            # Artigo Wintouch: só se escreve quando o envio para a Wintouch está disponível (a ficha local não muda).
            article = stock.article(db, item.article_id, for_write and not erp_write)
            if (for_write and not article.active) or article.revision != item.article_revision:
                fail(f"O artigo «{article.name}» mudou. Atualize a revisão.")
            before, vat, _ = article_values(db, article)
            values = costs(groups[item.article_id])
            cost, effective = values["reference_cost"], values["effective_cost"]
            after = proposed_price(cost, before, vat, item.method, item.value, data.rounding)
            # Todas as margens usam o mesmo PVP sem IVA: comercial (referência) e efetiva (com bónus).
            result.append({"id": article.id, "code": article.code, "name": article.name, "method": item.method, "before": before,
                           "before_gross": gross(before, vat), "after": after, "after_gross": gross(after, vat),
                           "difference": after - before if before is not None else None,
                           "reference_cost": cost, "effective_cost": effective,
                           "before_margin": margin(before, cost), "after_margin": margin(after, cost),
                           "before_effective_margin": margin(before, effective), "after_effective_margin": margin(after, effective),
                           "bonus_gain": values["bonus_gain"],
                           "erp": bool(article.erp_id),
                           "destination": ("Wintouch · envio e confirmação" if erp_write else "Wintouch · alteração na origem")
                           if article.erp_id else "Catálogo ZELA"})
        return result

    @app.post("/api/price-reviews/{identifier}/preview")
    def preview(identifier: str, data: ReviewInput, user=Depends(current), db: Session = Depends(db_session)):
        from .wintouch_price_write import availability
        _, stores = access(db, user)
        row, payload, review = receipt(db, identifier, stores)
        return json.loads(stock.dump(proposals(db, data, row, payload, review, erp_write=availability(db, user)[0])))

    @app.post("/api/price-reviews/{identifier}/apply")
    def apply(identifier: str, data: ApplyReview, user=Depends(current), db: Session = Depends(db_session)):
        config, stores = access(db, user)
        stock.require(user, config, "change_price")
        stock.lock(db)
        row, payload, review = receipt(db, identifier, stores)
        request = data.model_dump(mode="json", exclude={"request_key"})
        if previous := review["batches"].get(data.request_key):
            if previous["request"] != request:
                fail("Esta referência já identifica outra revisão de preços.")
            return previous["result"]
        if not data.lines and not data.finish:
            fail("Selecione os artigos a atualizar.", 422)
        from .wintouch_price_write import availability, push_prices
        erp_write, _ = availability(db, user)
        updates = proposals(db, data, row, payload, review, for_write=True, erp_write=erp_write)
        for proposal, update in zip(data.lines, updates, strict=True):
            if proposal.confirmed_net != update["after"]:
                fail("A proposta mudou. Reveja os valores antes de confirmar.")
        reviewed, sending = set(), []
        for update in updates:
            article = db.get(StockArticle, update["id"])
            if article.erp_id:
                product = db.get(WintouchProduct, article.erp_id)
                if not product or update["after_gross"] is None:
                    fail(f"«{article.name}»: falta o artigo ou a taxa de IVA da Wintouch. Sincronize os artigos antes de enviar o preço.")
                sending.append((article, product, update))
                continue
            meta = json.loads(article.data)
            if update["before"] != update["after"]:
                audit(db, user, "stock_article_price", article.id,
                      {"before": meta.get("sale_net"), "after": str(update["after"]), "receipt_id": row.id,
                       "origin": "revisao_de_precos_da_rececao"}, row.store_id)
                article.data = stock.dump({**meta, "sale_net": str(update["after"])})
                article.revision += 1
            reviewed.add(article.id)
        erp_report = []
        if sending:
            # Preço da Wintouch: enviado artigo a artigo e confirmado por leitura; só os confirmados ficam revistos.
            sent = push_prices(db, user, [{"product": product, "net": update["after"], "gross": update["after_gross"]} for _, product, update in sending])
            for article, product, update in sending:
                outcome = sent[product.id]
                audit(db, user, "wintouch_price_write", article.id,
                      {"erp_id": product.external_id, "before_net": str(update["before"]), "before_gross": str(update["before_gross"]),
                       "after_net": str(outcome["net"]), "after_gross": str(outcome["gross"]), "state": outcome["state"],
                       "error": outcome["error"], "receipt_id": row.id, "origin": "revisao_de_precos_da_rececao"}, row.store_id)
                if outcome["state"] == "confirmed":
                    reviewed.add(article.id)
                erp_report.append({"code": article.code, "name": article.name, "state": outcome["state"], "error": outcome["error"]})
        review["pending"] = [] if data.finish else [item for item in review["pending"] if item not in reviewed]
        result = {"updated": len(reviewed), "pending": len(review["pending"]), "erp": erp_report}
        review["batches"][data.request_key] = {"request": request, "result": result}
        row.data = stock.dump(payload)
        audit(db, user, "revisao_precos_rececao", row.id, result, row.store_id)
        db.commit()
        return result
