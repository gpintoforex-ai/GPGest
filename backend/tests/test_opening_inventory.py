"""Inventário inicial valorizado: pré-visualização, importação atómica e custo para a margem."""

from decimal import Decimal

from app.dashboard import article_costs
from app.models import StockArticle, StockEntry
from sqlalchemy import func, select
from test_inventory import product, stock_case  # noqa: F401

MAPPING = {"code": "Código", "quantity": "Quantidade", "unit_cost": "Custo"}


def preview(client, content, **extra):
    response = client.post("/api/stock/opening/preview", json={"content": content, "mapping": MAPPING, "occurred_on": "2026-01-01",
                                                               "reason": "Contagem de abertura", **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_opening_inventory_is_previewed_imported_once_and_gives_known_cost(stock_case):  # noqa: F811
    client, _, factory, warehouses, _ = stock_case
    first, second = product(stock_case), product(stock_case)
    with factory() as db:
        codes = [db.get(StockArticle, first).code, db.get(StockArticle, second).code]
    bad = preview(client, f"Código;Quantidade;Custo\n{codes[0]};10;2,50\nNAOEXISTE;1;1\n{codes[1]};3;\n{codes[0]};1;1\n", warehouse_id=warehouses[0])
    messages = [e["message"] for e in bad["errors"]]
    assert not bad["can_confirm"] and any("sem ficha" in m for m in messages) and any("Custo unitário em falta" in m for m in messages)
    assert any("repetidos" in m for m in messages)
    good = preview(client, f"Código;Quantidade;Custo\n{codes[0]};10;2,50\n{codes[1]};4;0\n", warehouse_id=warehouses[0])
    assert good["can_confirm"] and good["total_value"] == "25.00" and len(good["warnings"]) == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(StockEntry)) == 0  # a pré-visualização não grava
    done = client.post(f"/api/stock/opening/{good['id']}/confirm", json={"confirmed": True})
    assert done.status_code == 200 and done.json()["imported"] == 2
    assert client.post(f"/api/stock/opening/{good['id']}/confirm", json={"confirmed": True}).json()["already_applied"]
    with factory() as db:
        entries = list(db.scalars(select(StockEntry).where(StockEntry.kind == "opening")))
        assert len(entries) == 2 and {str(e.occurred_on) for e in entries} == {"2026-01-01"}
        costs = article_costs(db, [first, second])
        assert costs[first] == Decimal("2.5") and costs[second] == 0
    again = preview(client, f"Código;Quantidade;Custo\n{codes[0]};1;3\n", warehouse_id=warehouses[0])
    assert not again["can_confirm"] and "já tem stock inicial" in again["errors"][0]["message"]
    other_store = preview(client, f"Código;Quantidade;Custo\n{codes[0]};1;3\n", warehouse_id=warehouses[1])
    assert other_store["can_confirm"]
