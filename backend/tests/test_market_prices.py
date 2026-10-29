"""Preços de mercado (KuantoKusta e outras lojas) na ficha do artigo; dados sintéticos."""

from datetime import date, timedelta

from app.models import Audit
from sqlalchemy import select
from test_inventory import product
from test_inventory import stock_case as stock_case


def url(pid):
    return f"/api/stock/articles/{pid}/market-prices"


def add(c, pid, **extra):
    data = {"store_name": "Loja A", "price": "6.50", "observed_on": date.today().isoformat(), **extra}
    response = c.post(url(pid), json=data)
    assert response.status_code == 200, response.text
    return response.json()


def test_market_prices_compare_latest_price_of_each_store(stock_case):
    c, _, factory, _, _ = stock_case
    # 5 € sem IVA a 23 % → 6,15 € com IVA.
    pid = product(stock_case, barcodes=["5601234567890"])
    empty = c.get(url(pid)).json()
    assert empty["prices"] == [] and empty["summary"]["stores"] == 0
    assert empty["summary"]["own_cents"] == 615
    assert empty["search_url"] == "https://www.kuantokusta.pt/search?q=5601234567890"
    assert empty["can_edit"]

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    add(c, pid, store_name="Loja A", price="7.00", observed_on=yesterday)
    add(c, pid, store_name="loja  a", price="6.50")  # mesma loja, mais recente
    add(c, pid, store_name="Loja B", price="5.99", url="https://exemplo.pt/artigo")
    result = add(c, pid, store_name="Loja C", price="8", source="outro", notes="Promoção")

    assert len(result["prices"]) == 4
    old = next(p for p in result["prices"] if p["price_cents"] == 700)
    assert not old["current"]
    s = result["summary"]
    assert s["stores"] == 3
    assert (s["lowest_cents"], s["highest_cents"], s["average_cents"]) == (599, 800, 683)
    assert s["lowest_store"] == "Loja B" and s["cheaper_stores"] == 1

    removed = next(p for p in result["prices"] if p["store_name"] == "Loja B")
    after = c.delete(f"{url(pid)}/{removed['id']}").json()
    assert after["summary"]["lowest_cents"] == 650 and after["summary"]["cheaper_stores"] == 0
    assert c.delete(f"{url(pid)}/{removed['id']}").status_code == 404
    with factory() as db:
        actions = list(db.scalars(select(Audit.action).where(Audit.target_id == pid)))
    assert actions.count("stock_article_market_price") == 4
    assert "stock_article_market_price_removed" in actions


def test_market_prices_validate_input_and_permissions(stock_case):
    c, _, _, _, _ = stock_case
    pid = product(stock_case)
    today = date.today()
    for bad in (
        {"price": "0"},
        {"price": "1.234"},
        {"store_name": "  "},
        {"url": "http://inseguro.pt"},
        {"observed_on": (today + timedelta(days=2)).isoformat()},
    ):
        data = {"store_name": "Loja", "price": "3", "observed_on": today.isoformat(), **bad}
        assert c.post(url(pid), json=data).status_code == 422, bad
    assert c.get(url("nao-existe")).status_code == 404

    entry = add(c, pid)["prices"][0]
    assert c.post(
        "/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}
    ).status_code == 200
    seen = c.get(url(pid)).json()
    assert len(seen["prices"]) == 1 and not seen["can_edit"]
    assert c.post(url(pid), json={"store_name": "X", "price": "2", "observed_on": today.isoformat()}).status_code == 403
    assert c.delete(f"{url(pid)}/{entry['id']}").status_code == 403
