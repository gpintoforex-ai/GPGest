# ruff: noqa: F811
"""Publicação do catálogo IAZELA na loja online: criação, atualização, retirada, recuperação de falhas e
prevenção de duplicados. Só API local e esquema temporário; a loja é simulada pelas chamadas do conector."""

from uuid import uuid4

from app.models import StockArticle, WebshopPublication, WebshopPublicationEvent
from sqlalchemy import select
from test_inventory import login, move, product, stock_case, view  # noqa: F401
from test_webshop import connection

BASE = "/api/webshop"


def ean(body):
    """EAN-13 válido a partir de 12 algarismos."""
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body))
    return body + str((10 - total % 10) % 10)


def publish(case, conn, article, code=200, **extra):
    response = case[0].post(BASE + "/publications", json={"connection_id": conn["id"], "article_id": article, **extra})
    assert response.status_code == code, response.text
    return response.json()


def listing(case, conn):
    return case[0].get(BASE + "/publications", params={"connection_id": conn["id"]}).json()


def item(case, conn, article):
    return next(i for i in listing(case, conn)["items"] if i["article_id"] == article)


def feed(case, headers):
    response = case[0].get(BASE + "/publications-feed", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def report(case, headers, *results):
    response = case[0].post(BASE + "/publications-results", headers=headers, json={"results": list(results)})
    assert response.status_code == 200, response.text
    return response.json()["results"]


def ok(entry, woo_id, **extra):
    return {"article_id": entry["article_id"], "hash": entry["hash"], "ok": True, "woo_product_id": woo_id,
            "woo_status": "publish" if entry["state"] == "published" else "draft", **extra}


def remote(case, headers, products, final=True):
    response = case[0].post(BASE + "/remote-products", headers=headers,
                            json={"cycle": str(uuid4()), "final": final, "products": products})
    assert response.status_code == 200, response.text
    return response.json()


def test_creation_sends_once_and_records_mapping_history_and_stock(stock_case):
    c = stock_case[0]
    code = ean("560123456789")
    article = product(stock_case, barcodes=[code], brand="Marca", family="Suplementos",
                      photo="https://example.test/a.jpg", web_summary="Resumo para o site")
    move(stock_case, article, quantity="7")
    conn, headers = connection(stock_case)
    created = publish(stock_case, conn, article)
    assert created["sku"] == view(stock_case, article)["code"] and created["status"] == "pending"
    assert created["price_net"] == "5" and created["price_gross"] == "6.15" and created["gtin"] == code
    sent = feed(stock_case, headers)
    assert len(sent) == 1
    entry = sent[0]
    assert entry["state"] == "published" and entry["woo_product_id"] is None and entry["brand"] == "Marca"
    assert entry["images"] == ["https://example.test/a.jpg"] and entry["summary"] == "Resumo para o site"
    assert entry["price_net"] == "5.000000" and entry["vat_rate"] == "23" and entry["price_gross"] == "6.15"
    # A loja cria o produto em rascunho por falta de informação alimentar: fica visível no IAZELA.
    report(stock_case, headers, ok(entry, 501, woo_status="draft", food_missing=["Dose diária recomendada"]))
    assert feed(stock_case, headers) == []
    saved = item(stock_case, conn, article)
    assert saved["status"] == "synced" and saved["woo_product_id"] == 501 and saved["synced_at"]
    assert saved["food_missing"] == ["Dose diária recomendada"] and saved["woo_status"] == "draft"
    stock = c.get(BASE + "/publications-stock", headers=headers).json()["items"]
    assert stock == [{"article_id": article, "available": 7}]
    actions = [e["action"] for e in c.get(BASE + f"/publications/{saved['id']}/history").json()]
    assert actions == ["synced", "published"]
    # Repetir o mesmo resultado não altera nada nem cria outra associação.
    report(stock_case, headers, ok(entry, 501))
    assert feed(stock_case, headers) == [] and item(stock_case, conn, article)["woo_product_id"] == 501


def test_updates_of_article_and_channel_price_are_sent_incrementally(stock_case):
    c = stock_case[0]
    article, other = product(stock_case), product(stock_case)
    conn, headers = connection(stock_case)
    for identifier in (article, other):
        publish(stock_case, conn, identifier)
    sent = feed(stock_case, headers)
    report(stock_case, headers, *(ok(e, 600 + i) for i, e in enumerate(sent)))
    assert feed(stock_case, headers) == []
    # Preço do canal: só este artigo volta a ser enviado, com o novo preço e a margem calculada.
    current = item(stock_case, conn, article)
    edited = c.put(BASE + f"/publications/{current['id']}",
                   json={"revision": current["revision"], "sku": current["sku"], "price_net": "7.50"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["price_net"] == "7.50" and edited.json()["base_price_net"] == "5"
    sent = feed(stock_case, headers)
    assert [e["article_id"] for e in sent] == [article] and sent[0]["price_net"] == "7.500000"
    assert sent[0]["woo_product_id"] in (600, 601)
    # Revisão antiga recusada (alteração concorrente).
    assert c.put(BASE + f"/publications/{current['id']}",
                 json={"revision": current["revision"], "sku": current["sku"]}).status_code == 409
    report(stock_case, headers, ok(sent[0], sent[0]["woo_product_id"]))
    # Alteração na ficha do artigo (nome e descrição) também gera envio.
    row = view(stock_case, other)
    saved = c.put(f"/api/stock/articles/{other}", json={"code": row["code"], "name": "Nome novo",
                                                       "description": "Descrição nova", "sale_net": "5",
                                                       "revision": row["revision"]})
    assert saved.status_code == 200, saved.text
    sent = feed(stock_case, headers)
    assert [e["article_id"] for e in sent] == [other] and sent[0]["name"] == "Nome novo"
    # A loja preservou a descrição editada localmente: fica registado.
    report(stock_case, headers, ok(sent[0], sent[0]["woo_product_id"], preserved=["description"]))
    assert item(stock_case, conn, other)["preserved"] == ["description"]


def test_withdrawal_and_inactive_article_leave_the_shop_without_deleting(stock_case):
    c = stock_case[0]
    article = product(stock_case)
    conn, headers = connection(stock_case)
    pub = publish(stock_case, conn, article)
    report(stock_case, headers, ok(feed(stock_case, headers)[0], 700))
    withdrawn = c.post(BASE + f"/publications/{pub['id']}/withdraw", json={"revision": pub["revision"]})
    assert withdrawn.status_code == 200 and withdrawn.json()["state"] == "withdrawn"
    sent = feed(stock_case, headers)
    assert sent[0]["state"] == "withdrawn" and sent[0]["woo_product_id"] == 700
    report(stock_case, headers, ok(sent[0], 700))
    assert c.get(BASE + "/publications-stock", headers=headers).json()["items"] == []
    again = c.post(BASE + f"/publications/{pub['id']}/publish", json={"revision": withdrawn.json()["revision"]})
    assert again.status_code == 200 and feed(stock_case, headers)[0]["state"] == "published"
    report(stock_case, headers, ok(feed(stock_case, headers)[0], 700))
    # Artigo desativado: a publicação mantém-se, mas a loja recebe a retirada e a lista mostra o bloqueio.
    with stock_case[2].begin() as db:
        db.get(StockArticle, article).active = False
    sent = feed(stock_case, headers)
    assert sent[0]["state"] == "withdrawn"
    blocked = item(stock_case, conn, article)
    assert blocked["status"] == "blocked" and blocked["blockers"] == ["Artigo inativo."]
    with stock_case[2]() as db:
        assert db.scalar(select(WebshopPublication).where(WebshopPublication.article_id == article))


def test_duplicates_are_refused_and_existing_shop_products_need_explicit_association(stock_case):
    c = stock_case[0]
    code = ean("560765432109")
    article, twin, free = product(stock_case, barcodes=[code]), product(stock_case), product(stock_case)
    conn, headers = connection(stock_case)
    # Produtos que já existem na loja: um com o mesmo SKU de `free`, outro com o EAN de `article`.
    remote(stock_case, headers, [
        {"woo_product_id": 900, "sku": view(stock_case, free)["code"], "name": "Produto antigo"},
        {"woo_product_id": 901, "sku": "OUTRO", "gtin": code, "name": "Mesmo EAN", "status": "publish"},
    ])
    assert "associe-o" in publish(stock_case, conn, free, 409)["detail"]
    assert "mesmo EAN" in publish(stock_case, conn, article, 409)["detail"]
    suggestions = c.get(BASE + "/publications-suggestions", params={"connection_id": conn["id"]}).json()
    by_id = {s["woo_product_id"]: s for s in suggestions}
    assert by_id[901]["status"] == "suggested" and by_id[901]["candidates"][0]["article_id"] == article
    assert by_id[900]["status"] == "suggested" and by_id[900]["candidates"][0]["by"] == ["SKU"]
    linked = c.post(BASE + "/publications/associate",
                    json={"connection_id": conn["id"], "article_id": article, "woo_product_id": 901})
    assert linked.status_code == 200, linked.text
    assert linked.json()["woo_product_id"] == 901 and linked.json()["sku"] == "OUTRO"
    # O mesmo produto da loja não pode ir para outro artigo; o mesmo artigo não se publica duas vezes.
    assert c.post(BASE + "/publications/associate",
                  json={"connection_id": conn["id"], "article_id": twin, "woo_product_id": 901}).status_code == 409
    publish(stock_case, conn, article, 409)
    # SKU repetido noutro artigo.
    assert "já está na publicação" in publish(stock_case, conn, twin, 409, sku="OUTRO")["detail"]
    # A loja comunica um produto já usado por outra publicação: erro registado, sem nova associação.
    created = publish(stock_case, conn, twin, sku="TWIN-1")
    entry = next(e for e in feed(stock_case, headers) if e["article_id"] == twin)
    answer = report(stock_case, headers, ok(entry, 901))
    assert answer[0]["accepted"] is False
    failed = item(stock_case, conn, twin)
    assert failed["status"] == "error" and failed["woo_product_id"] is None and "duplicação" in failed["error"]
    with stock_case[2]() as db:
        assert len(list(db.scalars(select(WebshopPublication).where(
            WebshopPublication.connection_id == conn["id"])))) == 2
        assert db.get(WebshopPublication, created["id"]).woo_product_id is None


def test_ambiguous_ean_is_never_associated_automatically(stock_case):
    c = stock_case[0]
    code = ean("560111222333")
    product(stock_case, barcodes=[code])
    second = product(stock_case)
    with stock_case[2].begin() as db:
        # Dados antigos: o mesmo EAN em dois artigos (a ficha atual já o impede).
        row = db.get(StockArticle, second)
        row.data = row.data.replace('"barcodes": []', f'"barcodes": ["{code}"]')
    conn, headers = connection(stock_case)
    remote(stock_case, headers, [{"woo_product_id": 950, "sku": "X-1", "gtin": code, "name": "Ambíguo"}])
    suggestion = c.get(BASE + "/publications-suggestions", params={"connection_id": conn["id"]}).json()[0]
    assert suggestion["status"] == "ambiguous" and len(suggestion["candidates"]) == 2
    with stock_case[2]() as db:
        assert db.scalar(select(WebshopPublication).where(WebshopPublication.woo_product_id == 950)) is None


def test_products_created_by_the_connector_are_recognised_by_exact_identity(stock_case):
    article, draft = product(stock_case), product(stock_case)
    conn, headers = connection(stock_case)
    result = remote(stock_case, headers, [
        {"woo_product_id": 1001, "sku": "SITE-1", "name": "À venda", "status": "publish", "article_id": article},
        {"woo_product_id": 1002, "sku": "SITE-2", "name": "Rascunho", "status": "draft", "article_id": draft},
    ])
    assert result["linked"] == 1
    imported = item(stock_case, conn, article)
    assert imported["woo_product_id"] == 1001 and imported["sku"] == "SITE-1" and imported["state"] == "published"
    assert all(i["article_id"] != draft for i in listing(stock_case, conn)["items"])
    # Repetir a comunicação não cria outra publicação; o rascunho só se associa ao publicar o artigo, e então
    # reutiliza o produto que o conector já tinha criado (sem pedir associação nem criar outro).
    remote(stock_case, headers, [{"woo_product_id": 1001, "sku": "SITE-1", "status": "publish", "article_id": article},
                                 {"woo_product_id": 1002, "sku": "SITE-2", "status": "draft", "article_id": draft}])
    assert len(listing(stock_case, conn)["items"]) == 1
    assert publish(stock_case, conn, draft, sku="SITE-2")["woo_product_id"] == 1002
    sent = {e["article_id"]: e["woo_product_id"] for e in feed(stock_case, headers)}
    assert sent == {article: 1001, draft: 1002}


def test_failures_are_retried_later_and_resync_forces_a_new_attempt(stock_case):
    c = stock_case[0]
    article = product(stock_case)
    conn, headers = connection(stock_case)
    publish(stock_case, conn, article)
    entry = feed(stock_case, headers)[0]
    report(stock_case, headers, {"article_id": article, "hash": entry["hash"], "ok": False,
                                 "error": "Não foi possível descarregar a imagem."})
    failed = item(stock_case, conn, article)
    assert failed["status"] == "error" and failed["attempts"] == 1 and failed["retry_at"]
    assert feed(stock_case, headers) == []
    retried = c.post(BASE + f"/publications/{failed['id']}/resync", json={"revision": failed["revision"]})
    assert retried.status_code == 200 and retried.json()["attempts"] == 0
    entry = feed(stock_case, headers)[0]
    report(stock_case, headers, ok(entry, 1200))
    assert item(stock_case, conn, article)["status"] == "synced"
    with stock_case[2]() as db:
        actions = [e.action for e in db.scalars(select(WebshopPublicationEvent))]
    assert actions.count("sync_error") == 1 and "resync" in actions


def test_publication_requires_a_known_price_and_manager_profile(stock_case):
    c = stock_case[0]
    free_item = product(stock_case, sale_net="0")
    conn, headers = connection(stock_case)
    assert "Preço de venda desconhecido" in publish(stock_case, conn, free_item, 422)["detail"]
    # Preço do canal resolve o preço em falta.
    created = publish(stock_case, conn, free_item, price_net="3.20")
    assert created["price_net"] == "3.20" and created["status"] == "pending"
    service = product(stock_case, service=True)
    assert "Serviços" in publish(stock_case, conn, service, 422)["detail"]
    candidates = c.get(BASE + "/publications/articles", params={"connection_id": conn["id"]}).json()
    assert free_item not in [a["article_id"] for a in candidates]
    login(c, "operator")
    assert c.get(BASE + "/publications", params={"connection_id": conn["id"]}).status_code == 403
    login(c, "admin")
    # Outra ligação não vê as publicações desta.
    _, other = connection(stock_case)
    assert c.get(BASE + "/publications-feed", headers=other).json()["items"] == []
    assert c.get(BASE + "/publications-feed").status_code == 401
