"""Selective resets: archived original, no ERP writes, protected history and dual roles."""
from uuid import uuid4
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app import db as database
from app.models import BusinessEntity, CustomerOrder, StockArticle, StockEntry, StockWarehouse, User, WintouchProduct
from app.platform_models import Company, PlatformAudit
from test_platform import platform, create, enter


def schema(core, company):
    with core() as db:
        return db.get(Company, company["id"]).schema_name


@pytest.mark.parametrize("scope", ["customers", "suppliers", "articles"])
def test_selective_reset_preserves_history_other_tables_and_archive(platform, scope):
    c, core = platform
    company, other = create(c, "selective-" + scope), create(c, "other-" + scope)
    boot = enter(c, company["slug"])
    original = schema(core, company)
    try:
        with Session(database.engine.execution_options(schema_translate_map={None: original})) as db:
            user = db.scalar(select(User))
            wh = StockWarehouse(store_id=boot["stores"][0]["id"], name="Stock")
            free = StockArticle(code="FREE", name="Artigo sem histórico")
            used = StockArticle(code="USED", name="Artigo com histórico")
            customer = BusinessEntity(name="Cliente livre", is_customer=True)
            supplier = BusinessEntity(name="Fornecedor livre", is_supplier=True)
            both = BusinessEntity(name="Duas funções", is_customer=True, is_supplier=True)
            protected_customer = BusinessEntity(name="Cliente utilizado", is_customer=True)
            protected_supplier = BusinessEntity(name="Fornecedor utilizado", is_supplier=True)
            remote = WintouchProduct(enterprise_id="erp", external_id="remote-free", code="ERP-FREE", name="Só no catálogo ERP")
            db.add_all([wh, free, used, customer, supplier, both, remote, protected_customer, protected_supplier])
            db.flush()
            ids = {"free": free.id, "used": used.id, "customer": customer.id, "supplier": supplier.id, "both": both.id, "remote": remote.id}
            ids.update(protected_customer=protected_customer.id, protected_supplier=protected_supplier.id)
            free.data = '{"supplier_id": "' + protected_supplier.id + '"}'
            db.add(CustomerOrder(store_id=boot["stores"][0]["id"], user_id=user.id, customer_id=protected_customer.id))
            db.add(StockEntry(article_id=used.id, warehouse_id=wh.id, user_id=user.id, kind="opening", quantity=2, occurred_on="2026-01-01", request_key=uuid4().hex, sequence=0, net_sales=0, direct_cost=0, data="{}"))
            db.commit()
        url = f"/api/platform/companies/{company['id']}/reset-catalog/{scope}"
        preview = c.get(url)
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert "actions" not in plan
        body = {"confirmation": company["nif"], "acknowledged": True, "preview_token": plan["token"]}
        assert c.post(url, json={**body, "acknowledged": False}).status_code == 422
        assert c.post(url, json={**body, "confirmation": "errado"}).status_code == 422
        assert schema(core, company) == original
        result = c.post(url, json=body)
        assert result.status_code == 200, result.text
        replacement = schema(core, company)
        assert replacement != original
        with Session(database.engine.execution_options(schema_translate_map={None: replacement})) as db:
            assert db.get(StockArticle, ids["used"])
            assert db.scalar(select(StockEntry))
            assert db.get(BusinessEntity, ids["protected_customer"])
            assert db.get(BusinessEntity, ids["protected_supplier"])
            if scope == "articles":
                assert plan["protected"] == 1 and result.json()["removed"] == 2
                assert db.get(StockArticle, ids["free"]) is None
                assert db.get(WintouchProduct, ids["remote"]) is None
                assert db.get(BusinessEntity, ids["customer"])
                assert db.get(BusinessEntity, ids["supplier"])
            else:
                assert result.json()["shared"] == 1
                removed = ids["customer"] if scope == "customers" else ids["supplier"]
                retained = ids["supplier"] if scope == "customers" else ids["customer"]
                assert db.get(BusinessEntity, removed) is None
                assert db.get(BusinessEntity, retained)
                dual = db.get(BusinessEntity, ids["both"])
                assert dual.is_customer == (scope != "customers")
                assert dual.is_supplier == (scope != "suppliers")
                assert db.get(StockArticle, ids["free"])
        with Session(database.engine.execution_options(schema_translate_map={None: original})) as db:
            assert db.get(StockArticle, ids["free"])
            assert db.get(BusinessEntity, ids["both"]).is_customer
            assert db.get(BusinessEntity, ids["both"]).is_supplier
        with core() as db:
            assert db.scalar(select(PlatformAudit).where(PlatformAudit.company_id == company["id"], PlatformAudit.action.like(f"tabela_reinicializada:{scope}:%")))
        assert schema(core, other) != replacement
        assert c.get("/api/bootstrap").status_code == 401
        assert c.post(url, json=body).status_code == 409  # old preview cannot be replayed
    finally:
        if schema(core, company) != original:
            with database.engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{original}" CASCADE'))


def test_catalog_reset_preview_stale_and_platform_access(platform):
    c, core = platform
    company = create(c, "stale-catalog")
    enter(c, company["slug"])
    original = schema(core, company)
    url = f"/api/platform/companies/{company['id']}/reset-catalog/articles"
    with Session(database.engine.execution_options(schema_translate_map={None: original})) as db:
        db.add(StockArticle(code="P", name="Antes"))
        db.commit()
    plan = c.get(url).json()
    with Session(database.engine.execution_options(schema_translate_map={None: original})) as db:
        db.scalar(select(StockArticle)).name = "Depois"
        db.commit()
    result = c.post(url, json={"confirmation": company["nif"], "acknowledged": True, "preview_token": plan["token"]})
    assert result.status_code == 409
    assert schema(core, company) == original
    assert c.get(url.replace("articles", "invalid")).status_code == 422
    c.post("/api/platform/logout", json={})
    assert c.get(url).status_code == 401
    assert c.post(url, json={"confirmation": company["nif"], "acknowledged": True, "preview_token": plan["token"]}).status_code == 401


def test_catalog_reset_failure_rolls_back_copy_and_company_switch(platform, monkeypatch):
    c, core = platform
    company = create(c, "failed-catalog")
    original = schema(core, company)
    with Session(database.engine.execution_options(schema_translate_map={None: original})) as db:
        db.add(StockArticle(code="ROLLBACK", name="Preservar após falha"))
        db.commit()
    url = f"/api/platform/companies/{company['id']}/reset-catalog/articles"
    plan = c.get(url).json()
    replacement_id = uuid4().hex
    monkeypatch.setattr("app.platform.uid", lambda: replacement_id)
    def interrupted(*args):
        raise RuntimeError("Falha simulada na cópia")
    monkeypatch.setattr("app.catalog_reset.copy_and_reset_catalog", interrupted)
    with pytest.raises(RuntimeError, match="Falha simulada"):
        c.post(url, json={"confirmation": company["nif"], "acknowledged": True, "preview_token": plan["token"]})
    assert schema(core, company) == original
    with database.engine.connect() as connection:
        assert not connection.scalar(text("SELECT 1 FROM pg_namespace WHERE nspname = :name"), {"name": "gpgest_c_" + replacement_id})
    with Session(database.engine.execution_options(schema_translate_map={None: original})) as db:
        assert db.scalar(select(StockArticle)).code == "ROLLBACK"
