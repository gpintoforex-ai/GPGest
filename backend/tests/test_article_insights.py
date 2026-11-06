"""Article overview: actual ledger summaries, period bounds and access control."""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from test_inventory import login, move, product
from test_inventory import stock_case as stock_case  # noqa: F401


def test_overview_uses_real_stock_and_sales_returns_in_last_30_days(stock_case):
    client, _, _, warehouses, _ = stock_case
    today = datetime.now(ZoneInfo("Europe/Lisbon")).date()
    pid = product(stock_case)
    move(stock_case, pid, quantity="20", unit_cost="2", document="FT COMPRA/1")
    sale = move(stock_case, pid, "sale", "3", net_sales="15", document="FT VENDA/1")
    move(stock_case, pid, "customer_return", "1", original_id=sale[0], document="NC TESTE/1")
    move(stock_case, pid, "sale", "2", occurred_on=(today - timedelta(days=30)).isoformat(), net_sales="10")
    body = client.get(f"/api/stock/articles/{pid}/details").json()
    insights = body["insights"]
    assert insights["available"] is True
    assert Decimal(insights["sold_30_days"]) == 2
    assert Decimal(insights["sales_30_days"]) == 10
    assert insights["last_sale"] == today.isoformat()
    assert len(insights["monthly"]) == 6
    assert Decimal(body["current"]["quantity"]) == 16
    assert insights["rotation"] is None
    assert any(row["document"] == "FT VENDA/1" for row in insights["documents"])
    # Another authorized-to-admin store must disappear from an operator's view.
    move(stock_case, pid, quantity="99", unit_cost="9", warehouse_id=warehouses[2], document="RESTRITO")
    login(client, "operator")
    body = client.get(f"/api/stock/articles/{pid}/details").json()
    assert Decimal(body["current"]["quantity"]) == 16
    assert body["current"]["average_cost"] is None
    assert body["insights"]["incoming_quantity"] is None
    assert body["insights"]["sales_30_days"] is None
    assert all(row["document"] != "RESTRITO" for row in body["insights"]["documents"])
    assert all(row["purchased"] is None for row in body["insights"]["monthly"])


def test_services_have_no_invented_stock_sales_metrics(stock_case):
    pid = product(stock_case, service=True)
    body = stock_case[0].get(f"/api/stock/articles/{pid}/details").json()
    assert body["insights"]["available"] is False
    assert body["insights"]["sold_30_days"] is None
    assert body["insights"]["rotation"] is None


def test_incoming_uses_confirmed_orders_minus_receipts(stock_case):
    import json
    from uuid import uuid4
    from sqlalchemy import select
    from app.models import BusinessEntity, ProcurementDocument, ProcurementLink, User, now
    client, ids, factory, _, store = stock_case
    pid = product(stock_case)
    with factory.begin() as db:
        supplier = BusinessEntity(name="Fornecedor de teste", is_supplier=True)
        db.add(supplier)
        db.flush()
        admin_id = db.scalar(select(User.id).where(User.role == "administrador"))
        def document(kind, qty, confirmed=True, status="confirmed"):
            row = ProcurementDocument(kind=kind, store_id=store, supplier_id=supplier.id, user_id=admin_id,
                                      confirmed_at=now() if confirmed else None, status=status, request_key=uuid4().hex,
                                      data=json.dumps({"lines": [{"article_id": pid, "quantity": str(qty)}]}))
            db.add(row)
            db.flush()
            return row
        order = document("order", 10)
        received = document("receipt", 4)
        db.add(ProcurementLink(document_id=received.id, line_index=0, source_id=order.id, source_line=0, quantity=Decimal(4)))
        document("order", 100, confirmed=False, status="draft")
        document("order", 200, status="cancelled")
    body = client.get(f"/api/stock/articles/{pid}/details").json()
    assert Decimal(body["insights"]["incoming_quantity"]) == 6
