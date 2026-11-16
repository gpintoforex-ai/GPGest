"""Preços de mercado (KuantoKusta e outras lojas) na ficha do artigo; dados sintéticos."""

from datetime import date, timedelta
from decimal import Decimal

from app.models import Audit, WintouchProduct
from sqlalchemy import select
from test_inventory import erp_product, product
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


def test_market_prices_use_current_erp_gross_price_and_known_vat(stock_case):
    c, _, factory, *_ = stock_case
    erp_id = erp_product(stock_case, "ERP-MERCADO", sale_price_vat=Decimal("26.95"), vat_rate=Decimal(23))
    response = c.post("/api/stock/erp-products", json={"erp_id": erp_id})
    assert response.status_code == 200, response.text
    pid = response.json()["id"]
    data = add(c, pid, store_name="peixe verde", price="19.82", source="outro")
    assert data["summary"]["own_cents"] == 2695
    assert data["summary"]["cheaper_stores"] == 1
    assert data["summary"]["lowest_cents"] - data["summary"]["own_cents"] == -713
    # A comparação acompanha a linha 1 sincronizada, sem usar valores locais antigos ou inventar IVA.
    for gross, net, vat, expected in (
        ("28.00", "5", "23", 2800),
        (None, "21.9106", "23", 2695),
        (None, "21.9106", None, None),
        ("26.95", None, None, 2695),
        ("0", "21.9106", "23", None),
        (None, None, "23", None),
    ):
        with factory.begin() as db:
            mirror = db.get(WintouchProduct, erp_id)
            mirror.sale_price_vat = Decimal(gross) if gross is not None else None
            mirror.sale_price = Decimal(net) if net is not None else None
            mirror.vat_rate = Decimal(vat) if vat is not None else None
        assert c.get(url(pid)).json()["summary"]["own_cents"] == expected



def test_store_page_price_is_read_from_product_data_and_refreshed(stock_case, monkeypatch):
    """Preço lido da página da loja (rede simulada): JSON-LD, meta product:price, vírgula decimal; KuantoKusta não."""
    import json as _json
    import socket

    import httpx
    from app.market_prices import amount, page_price

    assert amount("23,48") == Decimal("23.48") and amount("1.234,50") == Decimal("1234.50") and amount("0") is None
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage"}, {"@type": "Product", "name": "X",
             "offers": {"@type": "Offer", "price": "23.48", "priceCurrency": "EUR"}}]}
    assert page_price(f'<script type="application/ld+json">{_json.dumps(graph)}</script>')[0] == Decimal("23.48")
    assert page_price('<meta property="og:site_name" content="Peixe Verde"><meta property="product:price:amount" '
                      'content="19,82"><meta property="product:price:currency" content="EUR">') == (Decimal("19.82"), "Peixe Verde")
    assert page_price('<meta property="product:price:amount" content="10"><meta property="product:price:currency" '
                      'content="USD">')[0] is None

    c, factory = stock_case[0], stock_case[2]
    pid = product(stock_case, sale_net="21.95", vat_rate="23")
    prices = {"natura": "23.48"}
    pages = {
        "/cerebro": lambda: f'<html><head><meta property="og:site_name" content="natura24"><script type="application/ld+json">'
                            f'{_json.dumps({"@type": "Product", "offers": [{"@type": "Offer", "price": prices["natura"], "priceCurrency": "EUR"}]})}'
                            f'</script></head></html>',
        "/sem-preco": lambda: "<html><body>Produto</body></html>",
    }

    def site(request):
        page = pages.get(request.url.path)
        return httpx.Response(200, text=page()) if page else httpx.Response(403)

    real_resolve, original = socket.getaddrinfo, httpx.Client
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
                        if host.endswith(".pt") else real_resolve(host, *a, **k))
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(site), **kw))
    # «Ler preço da ligação»: preenche o registo, não grava nada.
    read = c.post(url(pid) + "/read", json={"url": "https://www.natura24.pt/cerebro"})
    assert read.status_code == 200, read.text
    assert read.json()["price"] == "23.48" and read.json()["store_name"] == "natura24"
    assert c.get(url(pid)).json()["prices"] == []
    refused = c.post(url(pid) + "/read", json={"url": "https://www.kuantokusta.pt/p/123"})
    assert refused.status_code == 422 and "KuantoKusta" in refused.json()["detail"]
    assert c.post(url(pid) + "/read", json={"url": "https://loja.pt/sem-preco"}).status_code == 422
    # Lojas registadas: com a ligação da loja atualiza; sem ligação ou KuantoKusta fica como está.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    add(c, pid, source="outro", store_name="natura24", price="23.48", observed_on=yesterday, url="https://www.natura24.pt/cerebro")
    add(c, pid, store_name="Loja KK", price="20.00", observed_on=yesterday, url="https://www.kuantokusta.pt/p/1")
    add(c, pid, source="outro", store_name="Sem ligação", price="21.00", observed_on=yesterday)
    add(c, pid, source="outro", store_name="Bloqueia", price="22.00", observed_on=yesterday, url="https://bloqueia.pt/x")
    prices["natura"] = "22,90"
    result = c.post(url(pid) + "/refresh")
    assert result.status_code == 200, result.text
    states = {r["store_name"]: (r["state"], r["price_cents"]) for r in result.json()["refresh"]}
    assert states == {"natura24": ("updated", 2290), "Loja KK": ("skipped", None), "Sem ligação": ("skipped", None),
                      "Bloqueia": ("failed", None)}
    current = next(r for r in result.json()["prices"] if r["store_name"] == "natura24" and r["current"])
    assert current["price_cents"] == 2290 and current["notes"] == "Lido automaticamente da página da loja"
    assert sum(r["store_name"] == "natura24" for r in result.json()["prices"]) == 2  # o histórico mantém-se
    # Repetir no mesmo dia sem mudança de preço não duplica.
    again = {r["store_name"]: r["state"] for r in c.post(url(pid) + "/refresh").json()["refresh"]}
    assert again["natura24"] == "unchanged"
    with factory() as db:
        assert db.scalar(select(Audit).where(Audit.action == "stock_article_market_price_refresh"))



def test_store_robots_txt_is_respected(stock_case, monkeypatch):
    """robots.txt (RFC 9309): proibido para todos ou para a IAZELA não se lê; sem ficheiro lê-se; site em erro não."""
    import json as _json
    import socket

    import httpx

    c = stock_case[0]
    pid = product(stock_case, sale_net="21.95", vat_rate="23")
    page = '<script type="application/ld+json">' + _json.dumps({"@type": "Product", "offers": {"price": "9.99"}}) + "</script>"
    robots = {"proibe.pt": (200, "User-agent: *\nDisallow: /"),
              "so-iazela.pt": (200, "User-agent: IAZELA\nDisallow: /produto\n\nUser-agent: *\nAllow: /"),
              "permite.pt": (200, "User-agent: *\nDisallow: /carrinho"),
              "sem-robots.pt": (404, ""), "em-erro.pt": (503, "")}
    agents = []

    def site(request):
        if request.url.path == "/robots.txt":
            agents.append(request.headers["user-agent"])
            status, text = robots[request.url.host]
            return httpx.Response(status, text=text)
        return httpx.Response(200, text=page)

    real_resolve, original = socket.getaddrinfo, httpx.Client
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
                        if host.endswith(".pt") else real_resolve(host, *a, **k))
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(site), **kw))

    def read(host, path="/produto/1"):
        return c.post(url(pid) + "/read", json={"url": f"https://{host}{path}"})

    assert read("permite.pt").json()["price"] == "9.99"
    assert read("sem-robots.pt").json()["price"] == "9.99"
    assert read("so-iazela.pt", "/outra/1").json()["price"] == "9.99"
    for host in ("proibe.pt", "so-iazela.pt", "em-erro.pt"):
        refused = read(host)
        assert refused.status_code == 403 and "robots.txt" in refused.json()["detail"], (host, refused.text)
    assert agents and all("IAZELA/1.0" in agent for agent in agents)  # identifica-se honestamente
    # Na atualização, a loja que não permite fica por registar à mão (não é um erro).
    add(c, pid, source="outro", store_name="Proíbe", price="10.00", observed_on=(date.today() - timedelta(days=1)).isoformat(),
        url="https://proibe.pt/produto/1")
    result = c.post(url(pid) + "/refresh").json()["refresh"]
    assert result[0]["state"] == "skipped" and "robots.txt" in result[0]["error"]
