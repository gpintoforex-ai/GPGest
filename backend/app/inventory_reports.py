"""Reports over the ledger; unknown costs are excluded with explicit coverage."""

import json
from datetime import date
from decimal import Decimal as D

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import db_session
from .inventory import average, balances, permissions, settings, stock_from_erp, warehouse
from .models import StockArticle, StockEntry, StockLot, StockWarehouse


def profitability(net, cost):
    margin = net - cost if cost is not None else None
    return {
        "margin": str(margin) if margin is not None else None,
        "margin_pct": str(margin / net * 100) if margin is not None and net else None,
        "markup_pct": str(margin / cost * 100) if margin is not None and cost else None,
    }


def register_reports(app, current, authorize, scope):
    @app.get("/api/stock/report")
    def report(
        start: date,
        end: date,
        warehouse_id: str = "",
        article_id: str = "",
        category: str = "",
        brand: str = "",
        supplier_id: str = "",
        user=Depends(current),
        db: Session = Depends(db_session),
    ):
        from .inventory import fail

        if start > end:
            fail("O início do período deve ser anterior ao fim.")
        config = settings(db)
        cost_visible = permissions(user, config)["view_cost"]
        allowed = [
            w.id
            for w in db.scalars(
                select(StockWarehouse).where(StockWarehouse.store_id.in_(scope(user, db)))
            )
        ]
        if warehouse_id:
            warehouse(db, user, warehouse_id, authorize)
            allowed = [warehouse_id]
        ledger = list(db.scalars(select(StockEntry).order_by(StockEntry.created_at, StockEntry.id)))
        lots = {lot.id: lot for lot in db.scalars(select(StockLot))}
        grouped = {}
        for movement in ledger:
            grouped.setdefault(movement.article_id, []).append(movement)
        rows = []
        totals = {
            "stock_value": D(0),
            "risk_value": D(0),
            "net_sales": D(0),
            "covered_sales": D(0),
            "gross_margin": D(0),
            "loss_value": D(0),
            "direct_cost": D(0),
        }
        coverage = {
            "stock_unknown": 0,
            "risk_unknown": 0,
            "sales_known": 0,
            "sales_unknown": 0,
            "loss_unknown": 0,
            "erp_stock_unavailable": 0,
        }
        erp_stock = stock_from_erp(db)
        for product in db.scalars(select(StockArticle)):
            data = json.loads(product.data)
            if (
                article_id
                and product.id != article_id
                or category
                and data.get("category") != category
                or brand
                and data.get("brand") != brand
                or supplier_id
                and data.get("supplier_id") != supplier_id
            ):
                continue
            if data.get("service"):
                continue  # Serviços não têm stock.
            if product.erp_id and erp_stock:
                coverage["erp_stock_unavailable"] += 1
                continue
            history = grouped.get(product.id, [])
            cost, _ = average(history)
            visible = [r for r in history if r.warehouse_id in allowed]
            quantity = sum((r.quantity for r in visible), D(0))
            period = [r for r in visible if start.isoformat() <= r.occurred_on <= end.isoformat()]
            sales = [r for r in period if r.kind in ("sale", "customer_return")]
            known = [r for r in sales if r.unit_cost is not None]
            net = sum((r.net_sales for r in sales), D(0))
            covered = sum((r.net_sales for r in known), D(0))
            cogs = sum((-r.quantity * r.unit_cost for r in known), D(0))
            margin = covered - cogs
            losses = [r for r in period if r.kind in ("loss", "expired")]
            loss = sum((-r.quantity * r.unit_cost for r in losses if r.unit_cost is not None), D(0))
            direct = sum((r.direct_cost for r in period), D(0))
            risk_quantity, expired_quantity = D(0), D(0)
            for (_, lot_id), qty in balances(visible).items():
                lot = lots.get(lot_id)
                if lot and lot.expiry:
                    days = (date.fromisoformat(lot.expiry) - date.today()).days
                    if qty > 0 and days <= max(config["alerts"], default=90):
                        risk_quantity += qty
                        if days < 0:
                            expired_quantity += qty
            coverage["stock_unknown"] += int(quantity != 0 and cost is None)
            coverage["risk_unknown"] += int(risk_quantity > 0 and cost is None)
            coverage["sales_known"] += len(known)
            coverage["sales_unknown"] += len(sales) - len(known)
            coverage["loss_unknown"] += len([r for r in losses if r.unit_cost is None])
            if cost is not None:
                totals["stock_value"] += quantity * cost
                totals["risk_value"] += risk_quantity * cost
            totals["net_sales"] += net
            totals["covered_sales"] += covered
            totals["gross_margin"] += margin
            totals["loss_value"] += loss
            totals["direct_cost"] += direct
            minimum, reorder = D(data.get("minimum", 0)), D(data.get("reorder", 0))
            sold = sum((-r.quantity for r in sales), D(0))
            forecast = max(sold, D(0)) / max((end - start).days + 1, 1) * 30
            sale_price = D(data.get("sale_net", 0))
            actual = profitability(covered, cogs) if known else profitability(D(0), None)
            rows.append(
                {
                    "id": product.id,
                    "code": product.code,
                    "name": product.name,
                    "active": product.active,
                    "category": data.get("category", ""),
                    "quantity": str(quantity),
                    "minimum": str(minimum),
                    "below_minimum": quantity < minimum,
                    "replenishment": str(
                        max(reorder, forecast) - quantity
                        if max(reorder, forecast) > quantity
                        else 0
                    ),
                    "pending_orders": None,
                    "forecast_30_days": str(forecast),
                    "risk_quantity": str(risk_quantity),
                    "expired_quantity": str(expired_quantity),
                    "excess_with_expiry": risk_quantity > 0 and quantity > max(reorder, forecast),
                    "net_sales": str(net),
                    "no_sales": not any(r.kind == "sale" for r in sales),
                    "cost": str(cost) if cost_visible and cost is not None else None,
                    "stock_value": str(quantity * cost)
                    if cost_visible and cost is not None
                    else None,
                    "gross_margin": str(margin) if cost_visible and known else None,
                    "actual": actual if cost_visible else None,
                    "projected": profitability(sale_price, cost) if cost_visible else None,
                    "loss": str(loss) if cost_visible else None,
                    "contribution": str(margin - direct - loss) if cost_visible and known else None,
                    "unknown_sales": len(sales) - len(known),
                }
            )
        return {
            "rows": rows,
            "totals": {
                k: str(v) if cost_visible or k == "net_sales" else None for k, v in totals.items()
            },
            "coverage": coverage if cost_visible else None,
            "limitations": [
                "Stock a custo atual; vendas e perdas filtradas pelo período. Custo médio ponderado móvel, calculado pela ordem de registo.",
                "Margem bruta e contribuição após custos conhecidos não representam lucro líquido. Custos desconhecidos excluídos dos totais de margem.",
                "Encomendas pendentes indisponíveis: a sugestão usa o mínimo/reposição e a média diária de vendas líquidas do período × 30.",
                "SAF-T sem associação/controlo de stock mantém a análise fiscal em Importar SAF-T de vendas; não está incluído na margem realizada deste registo de stock.",
            ],
        }
