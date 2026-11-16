# ruff: noqa: F811
"""Loja online no catálogo de artigos e na ficha: estado de cada artigo na lista de publicação (por enviar,
sincronizado, retirado, erro, bloqueado), filtro do catálogo e o que cada perfil pode fazer. A loja é simulada
pelas chamadas do conector."""

from test_inventory import login, product, stock_case  # noqa: F401
from test_webshop import connection
from test_webshop_catalog import BASE, feed, item, ok, publish, report


def page(c, **params):
    response = c.get("/api/stock/catalog", params={"page": 1, "size": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()


def web(c, article, **params):
    return next(r for r in page(c, **params)["articles"] if r["id"] == article)["web"]


def ids(c, **params):
    return {r["id"] for r in page(c, **params)["articles"]}


def test_catalog_shows_and_filters_the_shop_list_and_the_sheet_explains_it(stock_case):
    c = stock_case[0]
    listed = product(stock_case, name="Artigo para o site")
    free = product(stock_case, name="Artigo sem preço", sale_net="0")
    conn, headers = connection(stock_case)
    catalog = page(c)
    assert catalog["webshop_connections"] == [{"id": conn["id"], "name": "Raizpura"}]
    assert web(c, listed) == [] and listed in ids(c, web="none")
    sheet = c.get(f"{BASE}/article-publications/{free}").json()["connections"][0]
    assert sheet["publication"] is None and sheet["can_manage"] is True and "Preço" in " ".join(sheet["blockers"])
    # Marcado para o site: aparece na grelha como por enviar e no filtro.
    publish(stock_case, conn, listed)
    state = web(c, listed)[0]
    assert (state["connection"], state["state"], state["status"]) == ("Raizpura", "published", "pending")
    assert ids(c, web="published") & {listed, free} == {listed} and listed not in ids(c, web="none")
    # A loja confirma: sincronizado, com o preço no site.
    report(stock_case, headers, ok(feed(stock_case, headers)[0], 901))
    state = web(c, listed)[0]
    assert state["status"] == "synced" and state["price_gross"] == "6.15" and state["woo_product_id"] == 901
    # Retirado: fica na lista, fora de venda; depois de a loja confirmar, «Retirado da loja».
    pub = item(stock_case, conn, listed)
    assert c.post(f"{BASE}/publications/{pub['id']}/withdraw", json={"revision": pub["revision"]}).status_code == 200
    assert listed in ids(c, web="withdrawn") and listed not in ids(c, web="published")
    assert web(c, listed)[0]["status"] == "pending"
    report(stock_case, headers, ok(feed(stock_case, headers)[0], 901))
    state = web(c, listed)[0]
    assert (state["status"], state["status_label"]) == ("withdrawn", "Retirado da loja")
    sheet = c.get(f"{BASE}/article-publications/{listed}").json()["connections"][0]
    assert sheet["publication"]["state"] == "withdrawn" and sheet["blockers"] == []
    # Perfis sem gestão: veem o estado, mas não podem marcar nem retirar.
    login(c, "operator")
    assert page(c)["webshop_connections"] == []
    sheet = c.get(f"{BASE}/article-publications/{listed}").json()["connections"][0]
    assert sheet["can_manage"] is False and sheet["publication"]["status"] == "withdrawn"
    assert c.post(f"{BASE}/publications", json={"connection_id": conn["id"], "article_id": free}).status_code == 403
    assert c.get(f"{BASE}/article-publications/inexistente").status_code == 404
