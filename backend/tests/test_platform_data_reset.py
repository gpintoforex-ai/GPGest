"""Operational reset must retain catalogs/banks and isolate every company."""

from uuid import uuid4

import pytest
from app import db as database
from app.company_reset import CATALOG_TABLES
from app.models import (
    BankAccount,
    BankInstitution,
    BankTransaction,
    Base,
    BusinessEntity,
    CompanyFiscalSettings,
    DocumentImport,
    DocumentPayment,
    IntegrationBinding,
    LocalPaymentMethod,
    Movement,
    PaymentDocumentLine,
    PaymentMethodBankDefault,
    PlannedMovement,
    Purchase,
    PurchaseAttachment,
    User,
    WintouchEntity,
    WintouchSaleRecord,
    WintouchSyncRun,
    now,
)
from app.platform_models import Company
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from test_platform import create, enter
from test_platform import platform as platform


def test_operational_reset_preserves_tables_and_banks(platform):
    client, core = platform
    company, other = create(client, "reset-operations-a"), create(client, "reset-operations-b")
    boot = enter(client, company["slug"])
    store, register, category = (
        boot["stores"][0]["id"],
        boot["registers"][0]["id"],
        boot["categories"][0]["id"],
    )
    opened = client.post("/api/sessions", json={"register_id": register, "opening": "40"})
    assert opened.status_code == 200, opened.text
    with core() as db:
        original = db.get(Company, company["id"]).schema_name
        other_schema = db.get(Company, other["id"]).schema_name
    bind = database.engine.execution_options(schema_translate_map={None: original})
    with Session(bind) as db:
        user = db.scalar(select(User))
        bank = BankAccount(
            store_id=store,
            name="Conta a preservar",
            bank="Banco UI",
            iban_last4="1234",
            iban_ciphertext="encrypted-test",
        )
        method = LocalPaymentMethod(id="reset-test-method", name="Método a preservar")
        entity = BusinessEntity(name="Fornecedor a preservar")
        batch = DocumentImport(
            store_id=store,
            user_id=user.id,
            filename="efatura.csv",
            fingerprint="abc",
            source="ficheiro_efatura",
        )
        run = WintouchSyncRun(enterprise_id="erp", resource="entities", status="concluida")
        db.add_all(
            [
                bank,
                method,
                entity,
                batch,
                run,
                BankInstitution(name="Banco a preservar"),
                CompanyFiscalSettings(id="default", vat_period="trimestral"),
            ]
        )
        db.flush()
        remote = WintouchEntity(
            enterprise_id="erp", external_id="entity-1", name="Fornecedor ERP", sync_run_id=run.id
        )
        purchase = Purchase(
            store_id=store,
            supplier_nif="500000000",
            supplier_name="Fornecedor",
            document="FT A/1",
            document_type="FT",
            document_date="2026-10-17",
            amount_cents=10000,
            import_id=batch.id,
        )
        payment = DocumentPayment(
            store_id=store,
            payment_date="2026-10-17",
            amount_cents=1000,
            direction=-1,
            request_key=uuid4().hex,
        )
        db.add_all([remote, purchase, payment])
        db.flush()
        db.add_all(
            [
                Movement(
                    session_id=opened.json()["id"],
                    user_id=user.id,
                    category_id=category,
                    kind="venda",
                    payment="numerario",
                    amount_cents=1000,
                    direction=1,
                    occurred_at=now(),
                    request_key=uuid4().hex,
                ),
                BankTransaction(
                    account_id=bank.id,
                    movement_date="2026-10-17",
                    value_date="2026-10-17",
                    description="Movimento a retirar",
                    amount_cents=-1000,
                    balance_cents=9000,
                    fingerprint="transaction",
                    source_account="UI",
                    exported_at="2026-10-17T10:00:00",
                    source_position=1,
                    import_id=batch.id,
                ),
                PurchaseAttachment(
                    purchase_id=purchase.id,
                    name="fatura.pdf",
                    mime="application/pdf",
                    data=b"test-pdf",
                ),
                PaymentDocumentLine(
                    purchase_id=purchase.id, payment_id=payment.id, amount_cents=1000
                ),
                PlannedMovement(
                    store_id=store,
                    category_id=category,
                    entity="Fornecedor",
                    document="Compra a crédito",
                    due_date="2026-10-20",
                    amount_cents=5000,
                    direction=-1,
                ),
                PaymentMethodBankDefault(store_id=store, method_id=method.id, account_id=bank.id),
                IntegrationBinding(
                    company_id=company["id"],
                    connector="wintouch",
                    external_kind="entity",
                    external_id=remote.id,
                    internal_kind="entity",
                    internal_id=entity.id,
                ),
                IntegrationBinding(
                    company_id=company["id"],
                    connector="wintouch",
                    external_kind="purchase",
                    external_id="purchase-1",
                    internal_kind="purchase",
                    internal_id=purchase.id,
                ),
                *[
                    WintouchSaleRecord(
                        enterprise_id="erp",
                        external_id=kind,
                        store_id=store,
                        kind=kind,
                        document_date="2026-10-17",
                        data="{}",
                    )
                    for kind in ("real", "previsto")
                ],
            ]
        )
        db.commit()
        preserved = {
            name: [dict(row) for row in db.execute(Base.metadata.tables[name].select()).mappings()]
            for name in CATALOG_TABLES
        }

    url = f"/api/platform/companies/{company['id']}/reset-movements"
    assert client.post(url, json={"confirmation": "wrong", "acknowledged": True}).status_code == 422
    assert (
        client.post(url, json={"confirmation": company["nif"], "acknowledged": False}).status_code
        == 422
    )
    response = client.post(url, json={"confirmation": company["nif"], "acknowledged": True})
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "movements"
    assert client.get("/api/bootstrap").status_code == 401
    with core() as db:
        replacement = db.get(Company, company["id"]).schema_name
        assert replacement != original
        assert db.get(Company, other["id"]).schema_name == other_schema
    try:
        with Session(
            database.engine.execution_options(schema_translate_map={None: replacement})
        ) as db:
            for table in Base.metadata.sorted_tables:
                rows = [dict(row) for row in db.execute(table.select()).mappings()]
                if table.name not in CATALOG_TABLES:
                    assert rows == [], table.name
                elif table.name == "wt_entities":
                    assert rows[0]["sync_run_id"] is None
                    assert rows[0]["name"] == "Fornecedor ERP"
                elif table.name == "integration_bindings":
                    assert [row["internal_kind"] for row in rows] == ["entity"]
                else:
                    assert rows == preserved[table.name], table.name
        assert enter(client, company["slug"])["accounts"][0]["name"] == "Conta a preservar"
        assert client.get("/api/purchases").json() == []
        assert client.get("/api/movements").json() == []
        assert "movimentos_reinicializados" in client.get("/api/platform/audit").text
        with Session(bind) as archived:
            assert archived.scalar(select(func.count()).select_from(Purchase)) == 1
            assert archived.scalar(select(func.count()).select_from(BankTransaction)) == 1
        client.post("/api/platform/logout", json={})
        assert (
            client.post(
                url, json={"confirmation": company["nif"], "acknowledged": True}
            ).status_code
            == 401
        )
    finally:
        with database.engine.begin() as connection:
            assert original.startswith("gpgest_c_") and len(original) == 41
            connection.execute(text(f'DROP SCHEMA "{original}" CASCADE'))


def test_failed_operational_reset_rolls_back_company_and_schema(platform, monkeypatch):
    from app import company_reset

    client, core = platform
    company = create(client, "failed-reset-operations")
    with core() as db:
        original = db.get(Company, company["id"]).schema_name
        before = set(db.scalars(text("SELECT schema_name FROM information_schema.schemata")))

    def fail_copy(*args):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(company_reset, "copy_company_data", fail_copy)
    with pytest.raises(RuntimeError, match="simulated failure"):
        client.post(
            f"/api/platform/companies/{company['id']}/reset-movements",
            json={"confirmation": company["nif"], "acknowledged": True},
        )
    with core() as db:
        assert db.get(Company, company["id"]).schema_name == original
        assert (
            set(db.scalars(text("SELECT schema_name FROM information_schema.schemata"))) == before
        )
