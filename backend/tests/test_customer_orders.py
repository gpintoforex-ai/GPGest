from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.customer_orders import CommercialTerms, SimLine, calculate
from app.models import (
    CustomerSupplyLink,
    ProcurementDocument,
    StockEntry,
)
from sqlalchemy import func, select
from test_inventory import login, move, product, stock_case, view  # noqa: F401
from test_platform import create as create_company
from test_platform import enter, platform  # noqa: F401
from test_procurement import case as procurement_case
from test_procurement import confirm, from_source

BASE = "/api/customer-orders"


@pytest.fixture
def case(request, monkeypatch):
    return procurement_case.__wrapped__(request.getfixturevalue("stock_case"), monkeypatch)


def post(c, path, data, code=200):
    r = c.post(BASE + path, json=data)
    assert r.status_code == code, r.text
    return r.json()


def request(case, aid=None, quantity="5", **extra):
    stock, supplier = case
    lines = extra.pop(
        "lines",
        [
            {
                "article_id": aid,
                "description": "Produto solicitado",
                "supplier_id": supplier,
                "quantity": quantity,
            }
        ],
    )
    data = {
        "store_id": stock[4],
        "customer_name": "Cliente " + str(uuid4())[:8],
        "phone": "+351912345678",
        "email": "cliente@example.test",
        "preferred_channel": "whatsapp",
        "contact_allowed": True,
        "request_key": str(uuid4()),
        "lines": lines,
        **extra,
    }
    return post(stock[0], "", data), data


def simulation(case, orders, aid=None, quantity=None, terms=None):
    stock, supplier = case
    allocations = [
        {"line_id": o["lines"][0]["id"], "quantity": o["lines"][0]["quantities"]["to_order"]}
        for o in orders
    ]
    total = quantity or str(sum((D(a["quantity"]) for a in allocations), D(0)))
    data = {
        "store_id": stock[4],
        "supplier_id": supplier,
        "request_key": str(uuid4()),
        "lines": [
            {
                "article_id": aid,
                "description": "Produto solicitado",
                "quantity": total,
                "unit_cost": "10",
                "allocations": allocations,
            }
        ],
    }
    if terms is not None:
        data["terms"] = terms
    return post(stock[0], "/simulations", data), data


def prepared(case, sim):
    return post(
        case[0][0],
        f"/simulations/{sim['id']}/prepare",
        {
            "revision": sim["revision"],
            "warehouse_id": case[0][3][0],
            "request_key": str(uuid4()),
            "confirmed": True,
            "allow_below_minimum": True,
        },
    )


def receive(case, sim, quantity):
    data = {
        "revision": sim["revision"],
        "request_key": str(uuid4()),
        "confirmed": True,
        "document": "GUIA-" + uuid4().hex[:8],
        "quantities": [str(quantity)],
    }
    return post(case[0][0], f"/simulations/{sim['id']}/receive", data), data


def order(case, identifier):
    return next(o for o in case[0][0].get(BASE).json()["orders"] if o["id"] == identifier)


def action(case, row, name, quantity, **extra):
    return post(
        case[0][0],
        f"/lines/{row['lines'][0]['id']}/{name}",
        {
            "revision": row["revision"],
            "request_key": str(uuid4()),
            "quantity": str(quantity),
            **extra,
        },
    )


def test_existing_provisional_multiple_products_and_duplicate_retry(case):
    aid = product(case[0])
    c = case[0][0]
    row, data = request(
        case,
        lines=[
            {"article_id": aid, "description": "Real", "quantity": "2"},
            {"description": "Provisório", "brand": "Marca", "ean": "NOVO-123", "quantity": "3"},
        ],
    )
    assert len(row["lines"]) == 2 and any(line_item["provisional"] for line_item in row["lines"])
    assert post(c, "", data)["id"] == row["id"]
    assert post(c, "", {**data, "request_key": str(uuid4())}, 409)
    provisional = next(line_item for line_item in row["lines"] if line_item["provisional"])
    changed = post(
        c,
        f"/lines/{provisional['id']}/associate",
        {"revision": row["revision"], "request_key": str(uuid4()), "article_id": aid},
    )
    assert not any(line_item["provisional"] for line_item in changed["lines"])
    assert (
        next(line_item for line_item in changed["lines"] if line_item["id"] == provisional["id"])[
            "ean"
        ]
        == "NOVO-123"
    )


def test_grouping_multiple_customers_and_replenishment_no_duplicate_demand(case):
    aid = product(case[0])
    first, _ = request(case, aid, "3")
    second, _ = request(case, aid, "4")
    sim, data = simulation(case, [first, second], aid, quantity="10")
    assert sim["totals"]["net_cents"] == 10000
    with case[0][2]() as db:
        assert len(list(db.scalars(select(CustomerSupplyLink)))) == 2
        assert not list(db.scalars(select(ProcurementDocument)))
        assert not list(db.scalars(select(StockEntry)))
    post(case[0][0], "/simulations", {**data, "request_key": str(uuid4())}, 409)
    assert order(case, first["id"])["lines"][0]["quantities"]["to_order"] == "0"


def test_calculator_minimum_discounts_shipping_tiers_and_currencies():
    config = CommercialTerms(
        minimum_cents=25000,
        shipping_cents=500,
        free_shipping_cents=25000,
        discount="10",
        tiers=[{"threshold_cents": 20000, "discount": "5"}],
    )
    small = calculate(
        [SimLine(description="A", quantity=1, unit_cost="185", vat=0)],
        CommercialTerms(minimum_cents=25000, vat=0),
    )
    assert small["missing_cents"] == 6500
    result = calculate([SimLine(description="A", quantity=3, unit_cost="100")], config)
    assert (
        result["discount_cents"] == 4350
        and result["net_cents"] == 25650
        and result["shipping_cents"] == 0
    )
    assert result["missing_cents"] == 0 and result["total_cents"] == 31550
    tax_basis = calculate(
        [SimLine(description="A", quantity=1, unit_cost="220")],
        CommercialTerms(minimum_cents=25000, minimum_basis="net_vat"),
    )
    assert tax_basis["missing_cents"] == 0
    assert (
        calculate(
            [SimLine(description="USD", quantity=1, unit_cost="10")],
            CommercialTerms(currency="USD"),
        )["currency"]
        == "USD"
    )


def test_edit_simulation_changes_quantities_and_releases_demand(case):
    aid = product(case[0])
    row, _ = request(case, aid, "5")
    sim, data = simulation(case, [row], aid)
    data["lines"][0]["quantity"] = "3"
    data["lines"][0]["allocations"][0]["quantity"] = "3"
    data.update(request_key=str(uuid4()), revision=sim["revision"])
    r = case[0][0].put(BASE + "/simulations/" + sim["id"], json=data)
    assert r.status_code == 200, r.text
    assert D(order(case, row["id"])["lines"][0]["quantities"]["to_order"]) == 2
    saved = r.json()
    post(
        case[0][0],
        f"/simulations/{sim['id']}/cancel",
        {"revision": saved["revision"], "request_key": str(uuid4())},
    )
    assert D(order(case, row["id"])["lines"][0]["quantities"]["to_order"]) == 5


def test_partial_receipt_confirmation_never_creates_stock_and_delivery_once(case):
    aid = product(case[0])
    row, _ = request(case, aid, "5")
    sim, _ = simulation(case, [row], aid)
    sim = prepared(case, sim)
    sim = post(
        case[0][0],
        f"/simulations/{sim['id']}/supplier-confirm",
        {
            "revision": sim["revision"],
            "request_key": str(uuid4()),
            "quantities": ["5"],
            "note": "Fornecedor confirmou",
        },
    )
    assert D(order(case, row["id"])["lines"][0]["quantities"]["received"]) == 0
    assert view(case[0], aid)["quantity"] == "0"
    sim, body = receive(case, sim, 2)
    assert post(case[0][0], f"/simulations/{sim['id']}/receive", body)["id"] == sim["id"]
    row = order(case, row["id"])
    q = row["lines"][0]["quantities"]
    assert q["received"] == "2.000000" and D(q["reserved"]) == 2 and row["ready"]
    delivered = {
        "revision": row["revision"],
        "request_key": str(uuid4()),
        "quantity": "2",
        "reason": "Levantamento no balcão",
    }
    url = f"/lines/{row['lines'][0]['id']}/deliver"
    first = post(case[0][0], url, delivered)
    post(case[0][0], url, delivered)
    assert (
        D(first["lines"][0]["quantities"]["delivered"]) == 2
        and D(view(case[0], aid)["quantity"]) == 0
    )
    sim, _ = receive(case, sim, 3)
    final = order(case, row["id"])
    final = action(case, final, "deliver", 3, reason="Levantamento final")
    assert final["closed"] and final["lines"][0]["state"] == "delivered"


def test_existing_receipt_allocation_does_not_duplicate_stock(case):
    aid = product(case[0])
    row, _ = request(case, aid, "5")
    sim, _ = simulation(case, [row], aid)
    sim = prepared(case, sim)
    source = case[0][0].get("/api/procurement").json()["documents"][0]
    receipt = confirm(case, from_source(case, source, "receipt", 2))
    before = view(case[0], aid)["quantity"]
    data = {
        "revision": sim["revision"],
        "request_key": str(uuid4()),
        "confirmed": True,
        "receipt_id": receipt["id"],
    }
    sim = post(case[0][0], f"/simulations/{sim['id']}/receive", data)
    assert view(case[0], aid)["quantity"] == before
    post(
        case[0][0],
        f"/simulations/{sim['id']}/receive",
        {**data, "revision": sim["revision"], "request_key": str(uuid4())},
        409,
    )


def test_reservations_protect_stock_and_cancel_releases(case):
    aid = product(case[0])
    move(case[0], aid)
    row, _ = request(case, aid, "8")
    row = action(case, row, "reserve", 8, warehouse_id=case[0][3][0])
    response = case[0][0].post(
        "/api/stock/movements",
        json={
            "article_id": aid,
            "warehouse_id": case[0][3][0],
            "kind": "sale",
            "quantity": "4",
            "reason": "Outra venda",
            "request_key": str(uuid4()),
        },
    )
    assert response.status_code == 409, response.text
    row = action(case, row, "cancel", 8, reason="Cliente desistiu")
    assert row["closed"] and row["lines"][0]["state"] == "cancelled"
    assert D(view(case[0], aid)["quantity"]) == 10


def test_cancel_supply_before_arrival_keeps_excess_in_stock(case):
    aid = product(case[0])
    row, _ = request(case, aid, "5")
    sim, _ = simulation(case, [row], aid)
    sim = prepared(case, sim)
    row = order(case, row["id"])
    row = action(case, row, "cancel", 5, reason="Cliente desistiu")
    receive(case, sim, 5)
    assert D(view(case[0], aid)["quantity"]) == 5
    assert not order(case, row["id"])["ready"]


def test_whatsapp_open_not_sent_and_new_arrival_requires_new_contact(case):
    aid = product(case[0])
    row, _ = request(case, aid, "5")
    sim, _ = simulation(case, [row], aid)
    sim = prepared(case, sim)
    sim, _ = receive(case, sim, 2)
    row = order(case, row["id"])
    c = case[0][0]
    data = {
        "revision": row["revision"],
        "request_key": str(uuid4()),
        "channel": "whatsapp",
        "message": "O seu produto chegou.",
    }
    contact = post(c, f"/{row['id']}/contact", data)
    assert (
        contact["url"].startswith("https://wa.me/351912345678?text=")
        and not order(case, row["id"])["notified"]
    )
    row = order(case, row["id"])
    confirmed = post(
        c,
        f"/{row['id']}/contact",
        {
            **data,
            "revision": row["revision"],
            "request_key": str(uuid4()),
            "result": "confirmed",
            "prepared_event_id": contact["event_id"],
        },
    )
    assert confirmed["result"] == "confirmed" and order(case, row["id"])["notified"]
    receive(case, sim, 3)
    assert not order(case, row["id"])["notified"]


def smtp(monkeypatch):
    from app import email_transport
    from app.email_settings import EmailConfigInput

    config = EmailConfigInput(
        enabled=True, host="smtp.example.test", sender_email="loja@example.test"
    ).model_dump()

    def configuration(_):
        return (SimpleNamespace(revision=1, password_ciphertext=""), config)

    monkeypatch.setattr("app.email_settings.configuration", configuration)
    monkeypatch.setattr("app.email_delivery.configuration", configuration)
    monkeypatch.setattr(email_transport, "connect", lambda *_: (object(), None))
    monkeypatch.setattr(email_transport, "disconnect", lambda *_: None)
    calls = []

    def submit(client, sender, recipients, raw, before_data):
        before_data()
        calls.append(raw)

    monkeypatch.setattr(email_transport, "submit", submit)
    return calls


def test_supplier_email_pdf_preview_and_explicit_send(case, monkeypatch):
    calls = smtp(monkeypatch)
    aid = product(case[0])
    row, _ = request(case, aid)
    sim, _ = simulation(case, [row], aid)
    sim = prepared(case, sim)
    doc = sim["procurement"]
    c = case[0][0]
    base = "/api/procurement/" + doc["id"]
    r = c.post(
        base + "/email-preview",
        json={
            "revision": doc["revision"],
            "to": "supplier@example.test",
            "subject": "Encomenda",
            "message": "Solicitamos confirmação.",
        },
    )
    assert r.status_code == 200, r.text
    email = r.json()
    assert not calls
    r = c.post(
        base + "/email-send/" + email["id"],
        json={"revision": email["revision"], "request_key": str(uuid4()), "confirmed": True},
    )
    assert r.status_code == 200 and r.json()["status"] == "accepted" and len(calls) == 1
    assert order(case, row["id"])["lines"][0]["state"] == "sent"


def test_customer_email_permissions_preview_and_send(case, monkeypatch):
    calls = smtp(monkeypatch)
    aid = product(case[0])
    move(case[0], aid)
    row, _ = request(case, aid, preferred_channel="email")
    row = action(case, row, "reserve", 5, warehouse_id=case[0][3][0])
    body = {
        "revision": row["revision"],
        "request_key": str(uuid4()),
        "channel": "email",
        "message": "Os seus produtos estão disponíveis.",
    }
    result = post(case[0][0], f"/{row['id']}/contact", body)
    email = result["email"]
    assert not calls and not order(case, row["id"])["notified"]
    sent = post(
        case[0][0],
        f"/{row['id']}/email/{email['id']}/send",
        {"revision": email["revision"], "request_key": str(uuid4()), "confirmed": True},
    )
    assert sent["status"] == "accepted" and len(calls) == 1 and order(case, row["id"])["notified"]


def test_contact_requires_preference_authorization_and_availability(case):
    aid = product(case[0])
    row, _ = request(case, aid, contact_allowed=False)
    data = {
        "revision": row["revision"],
        "request_key": str(uuid4()),
        "channel": "whatsapp",
        "message": "Chegou.",
    }
    post(case[0][0], f"/{row['id']}/contact", data, 409)


def test_permissions_store_isolation_and_no_cost_leak(case):
    aid = product(case[0])
    row, _ = request(case, aid)
    sim, _ = simulation(case, [row], aid)
    free, _ = request(case, aid, quantity="1")
    login(case[0][0], "reader")
    post(
        case[0][0],
        "",
        {
            "store_id": case[0][4],
            "customer_name": "Outro",
            "request_key": str(uuid4()),
            "lines": [{"description": "X", "quantity": "1"}],
        },
        403,
    )
    assert case[0][0].get(BASE).json()["simulations"] == []
    login(case[0][0], "operator")
    post(
        case[0][0],
        f"/simulations/{sim['id']}/cancel",
        {"revision": 0, "request_key": str(uuid4())},
        403,
    )
    post(
        case[0][0],
        f"/lines/{free['lines'][0]['id']}/reserve",
        {
            "revision": 0,
            "request_key": str(uuid4()),
            "quantity": "1",
            "warehouse_id": case[0][3][2],
        },
        403,
    )
    assert case[0][0].get(BASE + "?store_id=" + case[0][3][2]).status_code == 403
    assert post(
        case[0][0],
        "/lines/missing/cancel",
        {"request_key": str(uuid4()), "quantity": "1", "reason": "Teste"},
        404,
    )


def test_concurrent_reservation_only_one_claim(case):
    aid = product(case[0])
    move(case[0], aid)
    first, _ = request(case, aid, "8")
    second, _ = request(case, aid, "8")
    c = case[0][0]

    def reserve_one(row):
        return c.post(
            BASE + f"/lines/{row['lines'][0]['id']}/reserve",
            json={
                "revision": 0,
                "request_key": str(uuid4()),
                "quantity": "8",
                "warehouse_id": case[0][3][0],
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(reserve_one, [first, second]))
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_erp_failure_rolls_back_receipt_and_no_false_availability(case, monkeypatch):
    aid = product(case[0])
    row, _ = request(case, aid)
    sim, _ = simulation(case, [row], aid)
    sim = prepared(case, sim)
    monkeypatch.setattr(
        "app.procurement.policy", lambda _: {"mode": "integrated", "authority": {"stock": "erp"}}
    )
    body = {
        "revision": sim["revision"],
        "request_key": str(uuid4()),
        "quantities": ["5"],
        "document": "ERP-1",
        "confirmed": True,
    }
    post(case[0][0], f"/simulations/{sim['id']}/receive", body, 409)
    assert not order(case, row["id"])["ready"] and view(case[0], aid)["quantity"] == "0"
    with case[0][2]() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProcurementDocument)
                .where(ProcurementDocument.kind == "receipt")
            )
            == 0
        )


def test_update_contacts_quantity_and_stale_revision(case):
    aid = product(case[0])
    row, _ = request(case, aid, quantity="5")
    row = action(case, row, "adjust", 7, reason="Cliente acrescentou duas unidades")
    assert D(row["lines"][0]["quantities"]["to_order"]) == 7
    body = {
        k: row[k]
        for k in [
            "customer_name",
            "phone",
            "email",
            "preferred_channel",
            "contact_allowed",
            "due_date",
            "priority",
            "notes",
        ]
    }
    body.update(
        revision=row["revision"],
        request_key=str(uuid4()),
        email="corrigido@example.test",
        priority="urgent",
    )
    response = case[0][0].put(BASE + f"/{row['id']}/details", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "corrigido@example.test"
    response = case[0][0].put(
        BASE + f"/{row['id']}/details", json={**body, "request_key": str(uuid4())}
    )
    assert response.status_code == 409


def test_receipt_overdelivery_rolls_back_and_foreign_currency_not_converted(case):
    aid = product(case[0])
    row, _ = request(case, aid)
    sim, _ = simulation(case, [row], aid, terms={"currency": "USD"})
    post(
        case[0][0],
        f"/simulations/{sim['id']}/prepare",
        {
            "revision": 0,
            "warehouse_id": case[0][3][0],
            "request_key": str(uuid4()),
            "confirmed": True,
        },
        422,
    )
    with case[0][2]() as db:
        assert db.scalar(select(func.count()).select_from(ProcurementDocument)) == 0
    post(
        case[0][0], f"/simulations/{sim['id']}/cancel", {"revision": 0, "request_key": str(uuid4())}
    )
    row = order(case, row["id"])
    sim, _ = simulation(case, [row], aid)
    sim = prepared(case, sim)
    post(
        case[0][0],
        f"/simulations/{sim['id']}/receive",
        {
            "revision": sim["revision"],
            "request_key": str(uuid4()),
            "confirmed": True,
            "document": "Overdelivery",
            "quantities": ["6"],
        },
        409,
    )
    assert D(view(case[0], aid)["quantity"]) == 0


def test_customer_tenant_isolation(request):
    client, _ = request.getfixturevalue("platform")
    create_company(client, "orders-one")
    create_company(client, "orders-two")
    first = enter(client, "orders-one")
    original = post(
        client,
        "",
        {
            "store_id": first["stores"][0]["id"],
            "customer_name": "Cliente reservado",
            "phone": "+351912345678",
            "request_key": str(uuid4()),
            "lines": [{"description": "Pedido privado", "quantity": "1"}],
        },
    )
    enter(client, "orders-two")
    assert client.get(BASE).json()["orders"] == []
    assert client.get(BASE + "/history").json() == []
    post(
        client,
        f"/lines/{original['lines'][0]['id']}/cancel",
        {"revision": 0, "request_key": str(uuid4()), "quantity": "1", "reason": "Não autorizado"},
        404,
    )


def test_customer_migration_is_additive_and_repeatable(case, monkeypatch):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    migration = importlib.import_module("migrations.versions.0057_customer_orders")
    helper = importlib.import_module("migrations.versions.0006_cash_sheet")
    with case[0][2].kw["bind"].begin() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
        monkeypatch.setattr(helper, "schemas", lambda _: [schema])
        before = connection.scalar(text("SELECT count(*) FROM users"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()
            migration.upgrade()
        assert all(
            model.__tablename__ in inspect(connection).get_table_names(schema=schema)
            for model in migration.TABLES
        )
        assert connection.scalar(text("SELECT count(*) FROM users")) == before


def test_release_and_expired_reservations_are_not_available(case):
    from datetime import date, timedelta

    from app.models import StockLot

    aid = product(case[0], lots=True, expiry=True)
    move(case[0], aid, lot_number="TEST-LOT", expiry=str(date.today() + timedelta(days=5)))
    row, _ = request(case, aid, quantity="5")
    row = action(case, row, "reserve", 5, warehouse_id=case[0][3][0])
    with case[0][2].begin() as db:
        lot = db.scalar(select(StockLot).where(StockLot.article_id == aid))
        lot.expiry = str(date.today() - timedelta(days=1))
    row = order(case, row["id"])
    assert not row["ready"] and D(row["lines"][0]["quantities"]["available"]) == 0
    post(
        case[0][0],
        f"/lines/{row['lines'][0]['id']}/deliver",
        {
            "revision": row["revision"],
            "request_key": str(uuid4()),
            "quantity": "1",
            "reason": "Levantamento",
        },
        409,
    )
    row = action(case, row, "release", 5, reason="Lote fora de validade")
    assert (
        D(row["lines"][0]["quantities"]["reserved"]) == 0
        and D(row["lines"][0]["quantities"]["to_order"]) == 5
    )


def test_minimum_requires_exception_and_pricing_matches_procurement(case):
    aid = product(case[0])
    row, _ = request(case, aid)
    sim, _ = simulation(
        case,
        [row],
        aid,
        terms={
            "minimum_cents": 25000,
            "discount": "10.1234",
            "tiers": [{"threshold_cents": 0, "discount": "7.1234"}],
            "shipping_cents": 385,
            "shipping_vat": "23",
        },
    )
    body = {
        "revision": 0,
        "warehouse_id": case[0][3][0],
        "request_key": str(uuid4()),
        "confirmed": True,
    }
    post(case[0][0], f"/simulations/{sim['id']}/prepare", body, 409)
    result = prepared(case, sim)
    assert result["procurement"]["total_cents"] == result["totals"]["total_cents"]


def test_used_articles_cannot_be_deleted_and_entities_keep_history(case):
    aid = product(case[0])
    row, _ = request(case, aid)
    response = case[0][0].delete("/api/stock/articles/" + aid)
    assert response.status_code == 409, response.text
    supplier_id = case[1]
    response = case[0][0].post("/api/entities/local:" + supplier_id + "/remove")
    assert response.status_code == 200, response.text
    assert response.json()["action"] == "deactivated"
    assert order(case, row["id"])["lines"][0]["supplier_id"] == supplier_id


def test_imported_entities_are_reused_without_writing_to_erp(request):
    from app import db as database
    from app.models import BusinessEntity, WintouchEntity
    from app.platform_models import Company
    from sqlalchemy.orm import sessionmaker
    from test_platform import configuration

    client, factory = request.getfixturevalue("platform")
    company = create_company(client, "orders-erp-entity")
    response = client.post(
        "/api/platform/companies/" + company["id"] + "/config",
        json=configuration(company, api_enterprise="orders-source"),
    )
    assert response.status_code == 200, response.text
    with factory() as core:
        schema = core.get(Company, company["id"]).schema_name
    tenant = sessionmaker(database.engine.execution_options(schema_translate_map={None: schema}))
    with tenant.begin() as db:
        imported = WintouchEntity(
            enterprise_id="orders-source",
            external_id="original",
            name="Cliente e fornecedor ERP",
            is_customer=True,
            is_supplier=True,
        )
        other = WintouchEntity(
            enterprise_id="another-source",
            external_id="foreign",
            name="Outra empresa",
            is_customer=True,
            is_supplier=True,
        )
        db.add_all([imported, other])
        db.flush()
        imported_id, other_id = imported.id, other.id
    boot = enter(client, company["slug"])
    catalog = client.get(BASE + "/catalog").json()
    assert catalog["customers"][0]["id"] == "wt:" + imported_id
    supplier_key = catalog["suppliers"][0]["id"]
    post(
        client,
        "/simulations/preview",
        {
            "store_id": boot["stores"][0]["id"],
            "supplier_id": supplier_key,
            "request_key": str(uuid4()),
            "lines": [{"description": "Provisório", "quantity": "1", "unit_cost": "10"}],
        },
    )
    with tenant() as db:
        assert db.scalar(select(func.count()).select_from(BusinessEntity)) == 0
    body = {
        "store_id": boot["stores"][0]["id"],
        "customer_id": "wt:" + imported_id,
        "customer_name": "Cliente e fornecedor ERP",
        "request_key": str(uuid4()),
        "lines": [{"description": "Provisório", "quantity": "1", "supplier_id": supplier_key}],
    }
    row = post(client, "", body)
    assert post(client, "", body)["id"] == row["id"]
    assert row["customer_id"] == row["lines"][0]["supplier_id"]
    with tenant() as db:
        references = list(db.scalars(select(BusinessEntity)))
        assert len(references) == 1 and references[0].wintouch_id == imported_id
        assert db.get(WintouchEntity, imported_id).name == "Cliente e fornecedor ERP"
    post(client, "", {**body, "request_key": str(uuid4()), "customer_id": "wt:" + other_id}, 404)
