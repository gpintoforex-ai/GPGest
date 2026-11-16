# ruff: noqa: F811
"""Produto associado apagado na loja: a publicação fica com erro e com a indicação de produto em falta; só o operador
pede um produto novo (nunca automático); a loja recebe a publicação sem associação, cria outro produto e a IAZELA
passa a usá-lo. Histórico e auditoria. A loja é simulada pelas chamadas do conector."""

from app.models import Audit, WebshopRemoteProduct
from sqlalchemy import select
from test_inventory import product, stock_case  # noqa: F401
from test_webshop import connection
from test_webshop_catalog import BASE, feed, item, ok, publish, remote, report

GONE = "O produto associado (#801) já não existe na loja."


def test_deleted_shop_product_is_recreated_only_on_request(stock_case):
    c, factory = stock_case[0], stock_case[2]
    article = product(stock_case, name="Própolis spray")
    conn, headers = connection(stock_case)
    publish(stock_case, conn, article)
    report(stock_case, headers, ok(feed(stock_case, headers)[0], 801))
    remote(stock_case, headers, [{"woo_product_id": 801, "sku": item(stock_case, conn, article)["sku"], "gtin": "",
                                  "name": "Própolis spray", "status": "draft", "article_id": article}])
    pub = item(stock_case, conn, article)
    assert pub["woo_product_id"] == 801 and pub["product_missing"] is False
    # Sem associação, o pedido de produto novo é recusado.
    other = product(stock_case, name="Outro artigo")
    publish(stock_case, conn, other)
    fresh = item(stock_case, conn, other)
    assert c.post(f"{BASE}/publications/{fresh['id']}/recreate", json={"revision": fresh["revision"]}).status_code == 409
    report(stock_case, headers, ok(next(i for i in feed(stock_case, headers) if i["article_id"] == other), 802))
    # O produto foi apagado na loja: a loja devolve o erro e nada se resolve sozinho.
    assert c.post(f"{BASE}/publications/{pub['id']}/resync", json={"revision": pub["revision"]}).status_code == 200
    sent = feed(stock_case, headers)[0]
    assert sent["woo_product_id"] == 801
    report(stock_case, headers, {"article_id": article, "hash": sent["hash"], "ok": False, "error": GONE})
    pub = item(stock_case, conn, article)
    assert pub["status"] == "error" and pub["product_missing"] is True and pub["woo_product_id"] == 801
    # O operador pede um produto novo: a loja recebe a publicação sem associação.
    asked = c.post(f"{BASE}/publications/{pub['id']}/recreate", json={"revision": pub["revision"]})
    assert asked.status_code == 200, asked.text
    assert asked.json()["woo_product_id"] is None and asked.json()["error"] == "" and asked.json()["status"] == "pending"
    with factory() as db:
        assert not db.scalar(select(WebshopRemoteProduct).where(WebshopRemoteProduct.woo_product_id == 801))
    sent = feed(stock_case, headers)
    assert [(i["article_id"], i["woo_product_id"]) for i in sent] == [(article, None)]
    report(stock_case, headers, ok(sent[0], 803))
    pub = item(stock_case, conn, article)
    assert (pub["status"], pub["woo_product_id"], pub["product_missing"]) == ("synced", 803, False)
    assert feed(stock_case, headers) == []
    history = c.get(f"{BASE}/publications/{pub['id']}/history").json()
    recreated = next(h for h in history if h["action"] == "recreated")
    assert recreated["data"]["previous_woo_product_id"] == 801
    with factory() as db:
        assert db.scalar(select(Audit).where(Audit.action == "webshop_publication_recreate"))


def test_operator_override_replaces_text_preserved_in_the_shop(stock_case):
    c = stock_case[0]
    article = product(stock_case, name="Própolis gotas", description="Texto da IAZELA")
    conn, headers = connection(stock_case)
    publish(stock_case, conn, article)
    first = feed(stock_case, headers)[0]
    assert "override" not in first
    report(stock_case, headers, ok(first, 811, preserved=["description", "short_description"]))
    pub = item(stock_case, conn, article)
    assert pub["preserved"] == ["description", "short_description"] and pub["override_pending"] is False
    asked = c.post(f"{BASE}/publications/{pub['id']}/override", json={"revision": pub["revision"]})
    assert asked.status_code == 200 and asked.json()["override_pending"] is True
    sent = feed(stock_case, headers)
    assert len(sent) == 1 and sent[0]["override"] is True
    # Uma falha não perde o pedido; a confirmação da loja retira-o.
    report(stock_case, headers, {"article_id": article, "hash": sent[0]["hash"], "ok": False, "error": "Loja ocupada."})
    assert item(stock_case, conn, article)["override_pending"] is True
    c.post(f"{BASE}/publications/{pub['id']}/resync", json={"revision": item(stock_case, conn, article)["revision"]})
    again = feed(stock_case, headers)
    assert again[0]["override"] is True
    report(stock_case, headers, ok(again[0], 811))
    done = item(stock_case, conn, article)
    assert done["override_pending"] is False and done["preserved"] == [] and done["status"] == "synced"
    assert feed(stock_case, headers) == []
    history = c.get(f"{BASE}/publications/{pub['id']}/history").json()
    assert any(h["action"] == "override" and h["data"]["preserved"] == ["description", "short_description"] for h in history)
