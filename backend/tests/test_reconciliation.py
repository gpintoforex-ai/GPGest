import json
from datetime import datetime, timezone

from app.models import (
    Audit,
    BankAccount,
    BankTransaction,
    CashSession,
    Movement,
    Purchase,
    Register,
    WintouchSaleRecord,
)
from app.reconciliation import suggestion
from sqlalchemy import func, select


def seed(setup, bank_amounts=(-10000,), purchase_amounts=(10000,), movement_amounts=(), card=False):
    client, ids, factory = setup
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        accounts = [
            BankAccount(store_id=store, name=f"Banco {i}", bank="BPI", iban_last4="1234")
            for i in range(len(bank_amounts))
        ]
        db.add_all(accounts)
        db.flush()
        banks = [
            BankTransaction(
                account_id=a.id,
                movement_date="2026-10-10",
                value_date="2026-10-10",
                description="Empresa FT A/1",
                amount_cents=n,
                balance_cents=0,
                fingerprint=str(i),
                source_account="",
                exported_at="",
                source_position=i,
            )
            for i, (a, n) in enumerate(zip(accounts, bank_amounts))
        ]
        purchases = [
            Purchase(
                store_id=store,
                supplier_nif=f"50000000{i}",
                supplier_name="Empresa",
                document=f"FT A/{i + 1}",
                document_date="2026-10-10",
                amount_cents=n,
                origin="manual",
            )
            for i, n in enumerate(purchase_amounts)
        ]
        session = CashSession(
            register_id=ids["registers"][0], operator_id=ids["operator"], opening_cents=0
        )
        db.add(session)
        db.flush()
        movements = [
            Movement(
                session_id=session.id,
                user_id=ids["operator"],
                category_id=ids["category"],
                kind="venda",
                payment="cartao" if card else "transferencia",
                direction=1 if n > 0 else -1,
                amount_cents=abs(n),
                occurred_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
                request_key=f"test-{i}",
            )
            for i, n in enumerate(movement_amounts)
        ]
        db.add_all(banks + purchases + movements)
        db.flush()
        return client, factory, store, banks, purchases, movements


def line(source, row, n):
    return {"source": source, "source_id": row.id, "amount_cents": n}


def confirm(client, lines, **extra):
    return client.post("/api/reconciliation/confirm", json={"lines": lines, **extra})


def test_partial_many_to_one_and_undo_with_shared_purchase_balances(setup):
    c, factory, store, banks, purchases, _ = seed(setup, (-10000,), (6000, 8000))
    links = [
        line("bank", banks[0], 10000),
        line("purchase", purchases[0], 6000),
        line("purchase", purchases[1], 4000),
    ]
    overview = c.get("/api/reconciliation").json()
    assert overview["suggestions"]
    with factory() as db:
        assert not db.scalar(
            select(func.count())
            .select_from(Audit)
            .where(Audit.action == "reconciliacao_confirmada")
        )
    result = confirm(c, links)
    assert result.status_code == 200, result.text
    assert confirm(c, links).status_code == 409
    assert (
        c.post(
            f"/api/purchases/{purchases[1].id}/allocate",
            json={"transaction_id": banks[0].id, "amount": "1"},
        ).status_code
        == 409
    )
    overview = c.get("/api/reconciliation").json()
    assert overview["banks"][0]["status"] == "reconciliado"
    assert (
        next(r for r in overview["internals"] if r["id"] == purchases[1].id)["available_cents"]
        == 4000
    )
    assert (
        next(r for r in c.get("/api/purchases").json() if r["id"] == purchases[1].id)["paid_cents"]
        == 4000
    )
    path = f"/api/reconciliation/{result.json()['id']}/undo"
    assert c.post(path, json={"reason": "Correção do pagamento"}).status_code == 200
    assert c.post(path, json={"reason": "Correção repetida"}).status_code == 409
    assert c.get("/api/reconciliation").json()["banks"][0]["available_cents"] == 10000
    with factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.action == "reconciliacao_desfeita")
            )
            == 1
        )


def test_one_to_many_legacy_allocation_is_respected(setup):
    c, _, _, banks, purchases, _ = seed(setup, (-6000, -4000), (10000,))
    assert (
        c.post(
            f"/api/purchases/{purchases[0].id}/allocate",
            json={"transaction_id": banks[0].id, "amount": "60"},
        ).status_code
        == 200
    )
    assert (
        confirm(c, [line("bank", banks[1], 4000), line("purchase", purchases[0], 5000)]).status_code
        == 409
    )
    assert (
        confirm(c, [line("bank", banks[1], 4000), line("purchase", purchases[0], 4000)]).status_code
        == 200
    )
    assert c.get("/api/purchases").json()[0]["paid_cents"] == 10000


def test_card_gross_commission_net_and_no_automatic_movements(setup):
    c, factory, _, banks, _, moves = seed(setup, (9800,), (), (6000, 4000), card=True)
    links = [
        line("bank", banks[0], 9800),
        line("movement", moves[0], 6000),
        line("movement", moves[1], 4000),
    ]
    assert confirm(c, links, kind="cartao").status_code == 422
    assert confirm(c, links, kind="cartao", commission_cents=200).status_code == 422
    result = confirm(c, links, kind="cartao", commission_cents=200, reason="Comissão TPA do banco")
    assert result.status_code == 200, result.text
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 2
    group = c.get("/api/reconciliation").json()["groups"][0]
    assert group["commission_cents"] == 200
    assert group["gross_cents"] == 10000 and group["net_cents"] == 9800
    assert (
        c.post(
            f"/api/movements/{moves[0].id}/reverse", json={"reason": "Corrigir venda"}
        ).status_code
        == 409
    )


def test_difference_ignore_restore_permissions_and_cross_store(setup):
    c, factory, _, banks, purchases, _ = seed(setup, (-9900,), (10000,))
    path = f"/api/reconciliation/ignore/bank/{banks[0].id}"
    assert c.post(path, json={"reason": "  "}).status_code == 422
    assert c.post(path, json={"reason": "Movimento a investigar"}).status_code == 200
    links = [line("bank", banks[0], 9900), line("purchase", purchases[0], 10000)]
    assert (
        confirm(c, links, difference_cents=100, reason="Arredondamento acordado").status_code == 409
    )
    assert (
        c.post(
            f"/api/purchases/{purchases[0].id}/allocate",
            json={"transaction_id": banks[0].id, "amount": "1"},
        ).status_code
        == 409
    )
    assert c.post(f"/api/reconciliation/restore/bank/{banks[0].id}", json={}).status_code == 200
    assert (
        confirm(c, links, difference_cents=100, reason="Arredondamento acordado").status_code == 200
    )
    assert c.post(path, json={"reason": "Não aplicável"}).status_code == 409
    c.post("/api/auth/logout")
    c.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert confirm(c, links).status_code == 403
    assert c.get("/api/reconciliation").status_code == 200
    c.post("/api/auth/logout")
    c.post("/api/auth/login", json={"email": "admin@test.pt", "password": "PasswordTest123!"})
    with factory.begin() as db:
        p = db.get(Purchase, purchases[0].id)
        p.store_id = db.get(Register, setup[1]["registers"][2]).store_id
    assert confirm(c, links).status_code == 422


def test_transfer_between_own_accounts_and_deposit(setup):
    c, factory, _, banks, _, moves = seed(setup, (-10000, 10000), (), (10000,))
    links = [line("bank", b, 10000) for b in banks]
    assert (
        confirm(
            c, links, kind="transferencia", reason="Transferência entre contas próprias"
        ).status_code
        == 200
    )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Movement)) == 1
    group = c.get("/api/reconciliation").json()["groups"][0]
    c.post(f"/api/reconciliation/{group['id']}/undo", json={"reason": "Conferência do depósito"})
    with factory.begin() as db:
        m = db.get(Movement, moves[0].id)
        m.kind = "deposito"
        m.direction = -1
        m.payment = "numerario"
    deposit = [line("bank", banks[1], 10000), line("movement", moves[0], 10000)]
    assert confirm(c, deposit).status_code == 422
    assert confirm(c, deposit, kind="transferencia", reason="Depósito próprio").status_code == 200


def test_wintouch_card_and_duplicate_selection(setup):
    c, factory, store, banks, _, _ = seed(setup, (9900,), ())
    with factory.begin() as db:
        sale = WintouchSaleRecord(
            enterprise_id="test",
            external_id="payment:1",
            store_id=store,
            kind="real",
            document_date="2026-10-10",
            data=json.dumps(
                {
                    "amount_cents": 10000,
                    "direction": 1,
                    "payment": "Visa",
                    "payment_code": "CD",
                    "entity": "Cliente",
                }
            ),
        )
        db.add(sale)
        db.flush()
        sale_id = sale.id
    links = [
        line("bank", banks[0], 9900),
        {"source": "wintouch", "source_id": sale_id, "amount_cents": 10000},
    ]
    assert (
        confirm(
            c, links + [links[1]], kind="cartao", commission_cents=100, reason="Comissão Visa"
        ).status_code
        == 422
    )
    assert (
        confirm(c, links, kind="cartao", commission_cents=100, reason="Comissão Visa").status_code
        == 200
    )


def test_suggestion_explains_confidence_and_direction():
    bank = {
        "id": "b",
        "store_id": "s",
        "amount_cents": -100,
        "available_cents": 100,
        "date": "2026-10-10",
        "description": "Empresa FT A/1",
    }
    purchase = {
        "id": "p",
        "source": "purchase",
        "store_id": "s",
        "amount_cents": -100,
        "available_cents": 100,
        "date": "2026-10-10",
        "description": "FT A/1",
        "reference": "FT A/1",
        "entity": "Empresa",
    }
    result = suggestion(bank, purchase)
    assert result["confidence"] == "Alta" and len(result["reasons"]) >= 3
    assert suggestion(bank, {**purchase, "amount_cents": 100}) is None
    assert suggestion(bank, {**purchase, "store_id": "other"}) is None


def test_one_purchase_to_multiple_bank_movements_and_card_batch_suggestion(setup):
    c, _, _, banks, purchases, _ = seed(setup, (-6000, -4000), (10000,))
    result = confirm(
        c,
        [
            line("bank", banks[0], 6000),
            line("bank", banks[1], 4000),
            line("purchase", purchases[0], 10000),
        ],
    )
    assert result.status_code == 200, result.text
    assert all(r["status"] == "reconciliado" for r in c.get("/api/reconciliation").json()["banks"])
    row = c.get("/api/purchases").json()[0]
    assert row["reconciliations"][0]["id"] == result.json()["id"]


def test_concurrent_confirmation_cannot_allocate_same_money_twice(setup):
    from concurrent.futures import ThreadPoolExecutor

    c, _, _, banks, purchases, _ = seed(setup)
    links = [line("bank", banks[0], 10000), line("purchase", purchases[0], 10000)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm(c, links).status_code, range(2)))
    assert sorted(results) == [200, 409]
    assert c.get("/api/purchases").json()[0]["paid_cents"] == 10000


def test_repeated_partial_confirmation_does_not_duplicate_association(setup):
    c, _, _, banks, purchases, _ = seed(setup)
    links = [line("bank", banks[0], 2500), line("purchase", purchases[0], 2500)]
    result = confirm(c, links)
    assert result.status_code == 200
    assert confirm(c, links).status_code == 409
    assert c.get("/api/purchases").json()[0]["paid_cents"] == 2500
    assert (
        c.post(
            f"/api/reconciliation/{result.json()['id']}/undo",
            json={"reason": "Corrigir a associação"},
        ).status_code
        == 200
    )
    assert confirm(c, links).status_code == 200
