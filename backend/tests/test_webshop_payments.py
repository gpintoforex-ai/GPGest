# ruff: noqa: F811
"""Encomendas online: reserva atómica, prazos por meio de pagamento, expiração, pagamentos autenticados
idempotentes, pagamentos tardios, cancelamentos e área de gestão. Só API local e esquema temporário."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from app.models import CustomerOrderReservation, WebshopOrder, WebshopPaymentEvent, now
from sqlalchemy import func, select
from test_inventory import login, move, product, stock_case  # noqa: F401
from test_webshop import connection, payload

BASE = "/api/webshop"


def order(case, headers, article, quantity=2, code=200, **extra):
    data = payload(article, lines=[{"article_id": article, "quantity": quantity, "total_net_cents": 1000}], **extra)
    response = case[0].post(BASE + "/orders", json=data, headers=headers)
    assert response.status_code == code, response.text
    return data, response.json()


def pay(case, headers, external, key=None, code=200, **extra):
    body = {"event_key": key or "evt-" + uuid4().hex, "provider": "ifthenpay", "method": "mbway_ifthen_for_woocommerce",
            "status": "paid", "transaction_id": "TX-1", "amount_cents": 1230, "verification": "provider_callback",
            **extra}
    response = case[0].post(BASE + f"/orders/{external}/payments", json=body, headers=headers)
    assert response.status_code == code, response.text
    return body, response.json()


def held(case):
    with case[2]() as db:
        return db.scalar(select(func.coalesce(func.sum(CustomerOrderReservation.quantity), 0)))


def overdue(case, external):
    with case[2].begin() as db:
        mapping = db.scalar(select(WebshopOrder).where(WebshopOrder.external_id == external))
        mapping.reservation_expires_at = now() - timedelta(minutes=1)


def available(case, headers, article, quantity=1):
    response = case[0].post(BASE + "/availability", headers=headers,
                            json={"lines": [{"article_id": article, "quantity": quantity}]})
    assert response.status_code == 200, response.text
    return response.json()["lines"][0]["available"]


def test_deadline_by_payment_method_and_confirmation_by_provider_payment(stock_case):
    article = product(stock_case)
    move(stock_case, article, quantity="10")
    _, headers = connection(stock_case)
    assert available(stock_case, headers, article) == 10
    data, state = order(stock_case, headers, article, payment_method="mbway_ifthen_for_woocommerce", status="pending")
    assert state["reservation_state"] == "temporary" and state["payment_state"] == "pending"
    assert not state["ready_for_preparation"] and available(stock_case, headers, article) == 8
    with stock_case[2]() as db:
        mapping = db.scalar(select(WebshopOrder).where(WebshopOrder.external_id == data["external_id"]))
        assert timedelta(minutes=14) < mapping.reservation_expires_at - mapping.created_at <= timedelta(minutes=15)
    other, _ = order(stock_case, headers, article, payment_method="multibanco_ifthen_for_woocommerce", status="on-hold")
    with stock_case[2]() as db:
        mapping = db.scalar(select(WebshopOrder).where(WebshopOrder.external_id == other["external_id"]))
        assert timedelta(hours=47) < mapping.reservation_expires_at - mapping.created_at <= timedelta(hours=48)
    _, paid = pay(stock_case, headers, data["external_id"])
    assert paid["effect"] == "confirmed" and paid["reservation_state"] == "confirmed"
    assert paid["payment_state"] == "paid" and paid["ready_for_preparation"] and paid["reservation_expires_at"] is None
    # Confirmada não expira mais, mesmo depois do prazo inicial.
    overdue(stock_case, data["external_id"])
    assert stock_case[0].get(BASE + "/orders/" + data["external_id"], headers=headers).json()["reservation_state"] == "confirmed"


def test_expiry_releases_reservation_and_late_payment_reserves_again_or_goes_to_review(stock_case):
    article = product(stock_case)
    move(stock_case, article, quantity="2")
    _, headers = connection(stock_case)
    data, _ = order(stock_case, headers, article, payment_method="creditcard_ifthen_for_woocommerce", status="pending")
    assert available(stock_case, headers, article) == 0
    overdue(stock_case, data["external_id"])
    expired = stock_case[0].get(BASE + "/orders/" + data["external_id"], headers=headers).json()
    assert expired["reservation_state"] == "expired" and expired["state"] == "cancelled"
    assert held(stock_case) == 0 and available(stock_case, headers, article) == 2
    # A loja cancela a encomenda expirada; depois chega o pagamento (callback tardio).
    closed = stock_case[0].post(BASE + "/orders", headers=headers, json={**data, "sequence": 2, "status": "cancelled"})
    assert closed.status_code == 200 and closed.json()["reservation_state"] == "expired"
    # Pagamento depois de expirar, com stock livre: volta a reservar e fica confirmada.
    _, late = pay(stock_case, headers, data["external_id"], method="creditcard_ifthen_for_woocommerce")
    assert late["effect"] == "late_reserved" and late["reservation_state"] == "confirmed" and held(stock_case) == 2
    # A loja pode reabrir a encomenda (estava cancelada) porque o pagamento foi verificado e reservado.
    reopened = stock_case[0].post(BASE + "/orders", headers=headers, json={**data, "sequence": 3, "status": "processing"})
    assert reopened.status_code == 200, reopened.text
    # Outra encomenda expira e, entretanto, a última unidade foi vendida: o pagamento tardio fica para revisão.
    move(stock_case, article, quantity="1")
    second, _ = order(stock_case, headers, article, quantity=1, payment_method="mbway_ifthen_for_woocommerce",
                      status="pending", total_cents=1230)
    overdue(stock_case, second["external_id"])
    order(stock_case, headers, article, quantity=1, status="pending")  # outro cliente fica com a unidade
    _, review = pay(stock_case, headers, second["external_id"])
    assert review["effect"] == "late_no_stock" and review["reservation_state"] == "review"
    assert "Reponha o stock ou reembolse" in review["review"]
    alerts = stock_case[0].get("/api/alerts").json()["groups"]
    assert any(g["id"] == "encomendas_online_rever" for g in alerts)


def test_duplicate_and_divergent_payment_events(stock_case):
    article = product(stock_case)
    move(stock_case, article, quantity="5")
    _, headers = connection(stock_case)
    data, _ = order(stock_case, headers, article, payment_method="mbway_ifthen_for_woocommerce", status="pending")
    body, first = pay(stock_case, headers, data["external_id"], key="evt-same-callback")
    body.pop("event_key")
    _, again = pay(stock_case, headers, data["external_id"], key="evt-same-callback", **body)
    assert first["effect"] == "confirmed" and again["duplicate"] is True and again["effect"] == "confirmed"
    pay(stock_case, headers, data["external_id"], key="evt-same-callback", code=409, amount_cents=999)
    _, twice = pay(stock_case, headers, data["external_id"], transaction_id="TX-2")
    assert twice["effect"] == "duplicate_payment" and "Segundo pagamento" in twice["review"]
    with stock_case[2]() as db:
        assert db.scalar(select(func.count()).select_from(WebshopPaymentEvent)) == 2
    # Montante diferente do total: não confirma nada.
    other, _ = order(stock_case, headers, article, payment_method="mbway_ifthen_for_woocommerce", status="pending")
    _, wrong = pay(stock_case, headers, other["external_id"], amount_cents=1000)
    assert wrong["effect"] == "amount_mismatch" and wrong["payment_state"] == "pending"
    assert wrong["reservation_state"] == "temporary"


def test_concurrent_orders_for_the_last_unit_reserve_only_once(stock_case):
    c = stock_case[0]
    article = product(stock_case)
    move(stock_case, article, quantity="1")
    _, headers = connection(stock_case)
    bodies = [payload(article, lines=[{"article_id": article, "quantity": 1, "total_net_cents": 500}],
                      payment_method="mbway_ifthen_for_woocommerce", status="pending") for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(lambda b: c.post(BASE + "/orders", json=b, headers=headers).status_code, bodies))
    assert sorted(codes) == [200, 409, 409, 409] and held(stock_case) == 1
    assert available(stock_case, headers, article) == 0
    # Pagamentos repetidos em paralelo do mesmo callback: um só efeito.
    winner = bodies[codes.index(200)]
    event = {"event_key": "evt-race", "provider": "ifthenpay", "method": "mbway_ifthen_for_woocommerce", "status": "paid",
             "amount_cents": winner["total_cents"], "verification": "provider_callback"}
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda _: c.post(BASE + f"/orders/{winner['external_id']}/payments", json=event,
                                                 headers=headers).json(), range(3)))
    assert sorted(r["duplicate"] for r in replies) == [False, True, True]
    with stock_case[2]() as db:
        assert db.scalar(select(func.count()).select_from(WebshopPaymentEvent)) == 1


def test_cancellations_release_or_flag_paid_orders_and_unpaid_cannot_be_delivered(stock_case):
    from app.customer_orders import QuantityChange, line_action
    from app.models import CustomerOrderLine, User

    c, _, factory, warehouses, _ = stock_case
    article = product(stock_case)
    move(stock_case, article, quantity="6")
    conn, headers = connection(stock_case)
    unpaid, _ = order(stock_case, headers, article, payment_method="multibanco_ifthen_for_woocommerce", status="on-hold")
    with factory() as db:
        mapping = db.scalar(select(WebshopOrder).where(WebshopOrder.external_id == unpaid["external_id"]))
        line = db.scalar(select(CustomerOrderLine).where(CustomerOrderLine.order_id == mapping.order_id))
        user = db.scalar(select(User).where(User.email == "admin@test.pt"))
        try:
            line_action(line.id, "deliver", QuantityChange(request_key=str(uuid4()), revision=1, quantity="1",
                                                            warehouse_id=warehouses[0], reason="Entrega"), user, db)
            raise AssertionError("Entrega de encomenda por pagar aceite")
        except Exception as exc:  # HTTPException 409
            assert "sem pagamento confirmado" in str(getattr(exc, "detail", exc))
    cancelled = c.post(BASE + "/orders", headers=headers, json={**unpaid, "sequence": 2, "status": "cancelled"}).json()
    assert cancelled["reservation_state"] == "released" and not cancelled["review"]
    paid, _ = order(stock_case, headers, article, payment_method="mbway_ifthen_for_woocommerce", status="pending")
    pay(stock_case, headers, paid["external_id"])
    flagged = c.post(BASE + "/orders", headers=headers, json={**paid, "sequence": 2, "status": "cancelled"}).json()
    assert flagged["reservation_state"] == "released" and "confirme o reembolso" in flagged["review"]
    assert held(stock_case) == 0


def test_online_orders_area_filters_history_settings_and_actions(stock_case):
    c = stock_case[0]
    article = product(stock_case)
    move(stock_case, article, quantity="3")
    conn, headers = connection(stock_case)
    saved = c.put(BASE + f"/connections/{conn['id']}/order-settings", json={"deadlines": {"transfer": 1440}})
    assert saved.status_code == 200 and saved.json()["deadlines"]["transfer"] == 1440
    assert c.put(BASE + f"/connections/{conn['id']}/order-settings", json={"deadlines": {"mbway": 1}}).status_code == 422
    data, _ = order(stock_case, headers, article, payment_method="bacs", status="on-hold")
    listing = c.get(BASE + "/online-orders", params={"connection_id": conn["id"]}).json()
    row = listing["items"][0]
    assert row["payment_kind_label"] == "Transferência" and row["customer"]["name"] == "Cliente de teste"
    assert row["lines"][0]["quantity"] == "2.000000" and row["reservation_state"] == "temporary"
    assert listing["connections"][0]["deadlines"]["transfer"] == 1440
    assert c.get(BASE + "/online-orders", params={"payment": "paid"}).json()["items"] == []
    # Transferência confirmada por um administrador da loja.
    pay(stock_case, headers, data["external_id"], method="bacs", provider="manual", verification="admin_confirmation")
    detail = c.get(BASE + f"/online-orders/{row['id']}").json()
    assert [h["action"] for h in detail["history"]][:2] == ["payment_confirmed", "received"]
    assert detail["payments"][0]["verification"] == "admin_confirmation"
    # Prolongar só uma reserva temporária; libertar exige motivo.
    other, _ = order(stock_case, headers, article, quantity=1, payment_method="mbway_ifthen_for_woocommerce",
                     status="pending", total_cents=1230)
    other_id = next(i["id"] for i in c.get(BASE + "/online-orders").json()["items"] if i["external_id"] == other["external_id"])
    assert c.post(BASE + f"/online-orders/{other_id}/extend", json={"note": ""}).status_code == 422
    assert c.post(BASE + f"/online-orders/{other_id}/extend", json={"note": "Cliente pediu mais tempo"}).status_code == 200
    released = c.post(BASE + f"/online-orders/{other_id}/release", json={"note": "Cliente desistiu"}).json()
    assert released["reservation_state"] == "released"
    assert c.post(BASE + f"/online-orders/{other_id}/retry_reservation", json={}).json()["reservation_state"] == "temporary"
    login(c, "operator")
    assert c.get(BASE + "/online-orders").status_code == 200
    assert c.post(BASE + f"/online-orders/{other_id}/release", json={"note": "Sem permissão"}).status_code == 403
    login(c, "admin")


def test_unknown_order_and_other_connection_cannot_pay(stock_case):
    article = product(stock_case)
    move(stock_case, article, quantity="2")
    _, headers = connection(stock_case)
    data, _ = order(stock_case, headers, article, payment_method="mbway_ifthen_for_woocommerce", status="pending")
    _, other = connection(stock_case)
    pay(stock_case, other, data["external_id"], code=404)
    pay(stock_case, headers, "site:inexistente", code=404)
    assert stock_case[0].post(BASE + f"/orders/{data['external_id']}/payments", json={}).status_code == 401
