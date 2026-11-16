# ruff: noqa: F811
"""PVP de um artigo da Wintouch alterado na ficha: envio à Wintouch (API simulada) confirmado por leitura, recusa e
envio não confirmado sem efeito local, conflito com um preço alterado entretanto, permissões, auditoria e preço
novo enviado à loja online na sincronização seguinte. Nenhum pedido sai para a Wintouch."""

import json
from contextlib import contextmanager
from decimal import Decimal as D

import httpx
from app import wintouch_price_write as writer
from app.models import Audit, WintouchProduct
from app.wintouch_sales import Reader
from sqlalchemy import select
from test_inventory import erp_product, login, product, stock_case  # noqa: F401
from test_webshop import connection
from test_webshop_catalog import feed, ok, publish, report


def wintouch(monkeypatch, external, remote, mode, sent):
    def transport(request):
        if request.method == "PATCH":
            sent.append(json.loads(request.content))
            if mode["value"] == "reject":
                return httpx.Response(400, json={"Message": "Preço inválido na Wintouch"})
            if mode["value"] == "apply":
                body = sent[-1][0]
                remote.update(PriceWithoutVAT=str(body["PriceWithoutVAT"]), PriceWithVAT=str(body["PriceWithVAT"]))
            return httpx.Response(200, json={})
        assert request.url.path.endswith(f"/products/{external}/prices")
        return httpx.Response(200, json=[remote])

    @contextmanager
    def reader(db, user, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            yield Reader(client, "https://erp.test/api/v1", "tenant", "key")

    monkeypatch.setattr(writer, "availability", lambda db, user: (True, ""))
    monkeypatch.setattr(writer, "connection", reader)


def test_erp_price_from_the_sheet_is_confirmed_in_wintouch_and_reaches_the_shop(stock_case, monkeypatch):
    c, factory = stock_case[0], stock_case[2]
    erp_id = erp_product(stock_case, "ERP-PVP", vat_rate=D(23), sale_price_vat=D("6.15"))
    article = c.post("/api/stock/erp-products", json={"erp_id": erp_id}).json()["id"]
    path = f"/api/stock/articles/{article}/erp-price"
    # Sem ligação Wintouch com escrita (ambiente de teste): explica o motivo e não envia nada.
    blocked = c.get(path).json()
    assert blocked["available"] is False and blocked["reason"] and blocked["gross"] == "6.1500"
    assert c.post(path, json={"gross": "7.38"}).status_code == 409
    # Publicado na loja com o preço base do artigo.
    conn, headers = connection(stock_case)
    publish(stock_case, conn, article)
    entry = feed(stock_case, headers)[0]
    assert entry["price_gross"] == "6.15"
    report(stock_case, headers, ok(entry, 801))
    with factory() as db:
        external = db.get(WintouchProduct, erp_id).external_id
    remote, mode, sent = {"Row": 1, "PriceWithoutVAT": "5", "PriceWithVAT": "6.15"}, {"value": "reject"}, []
    wintouch(monkeypatch, external, remote, mode, sent)
    current = c.get(path).json()
    assert current["available"] is True and current["channels"] == [
        {"connection": "Raizpura", "state": "published", "own_price": False, "price_net": None}]
    # Recusa e envio não confirmado: o preço local não muda e a loja não recebe nada.
    rejected = c.post(path, json={"gross": "7.38", "expected_gross": "6.15"})
    assert rejected.status_code == 200 and rejected.json()["state"] == "failed"
    assert rejected.json()["error"] == "Preço inválido na Wintouch" and rejected.json()["gross"] == "6.1500"
    assert sent[-1] == [{"ID": external, "RowPrice": 1, "PriceWithoutVAT": 6.0, "PriceWithVAT": 7.38}]
    mode["value"] = "ignore"
    assert c.post(path, json={"gross": "7.38", "expected_gross": "6.15"}).json()["state"] == "uncertain"
    assert feed(stock_case, headers) == []
    # Confirmado por leitura: o preço local passa a ser o da Wintouch e a loja recebe-o.
    mode["value"] = "apply"
    confirmed = c.post(path, json={"gross": "7.38", "expected_gross": "6.15"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["state"] == "confirmed" and D(confirmed.json()["gross"]) == D("7.38")
    with factory() as db:
        product_row = db.get(WintouchProduct, erp_id)
        assert product_row.sale_price == D("6.0000") and product_row.sale_price_vat == D("7.38")
        states = [json.loads(a.detail)["state"] for a in db.scalars(
            select(Audit).where(Audit.target_id == article, Audit.action == "wintouch_price_write"))]
        assert sorted(states) == ["confirmed", "failed", "uncertain"]
    changed = feed(stock_case, headers)
    assert len(changed) == 1 and changed[0]["price_gross"] == "7.38" and changed[0]["price_net"] == "6.000000"
    # Preço alterado entretanto na Wintouch: não se sobrepõe sem rever.
    stale = c.post(path, json={"gross": "8.00", "expected_gross": "6.15"})
    assert stale.status_code == 409 and "mudou" in stale.json()["detail"]
    # Artigos locais e perfis sem permissão de alterar preços.
    local = product(stock_case)
    assert c.post(f"/api/stock/articles/{local}/erp-price", json={"gross": "2"}).status_code == 409
    assert c.post(path, json={"gross": "0"}).status_code == 422
    login(c, "operator")
    assert c.post(path, json={"gross": "9"}).status_code == 403
