# ruff: noqa: F811
import importlib
import json

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models import ArticleBrand, ArticleFamily, ArticleSubfamily, StockArticle
from sqlalchemy import select
from test_inventory import login, product, stock_case, view  # noqa: F401
from test_webshop import connection
from test_webshop_catalog import feed, ok, publish, report

BASE = "/api/tables/"


def create(c, table, **values):
    result = c.post(BASE + table + "/records", json={"values": values})
    assert result.status_code == 200, result.text
    return result.json()["id"]


def edit(c, table, identifier, **values):
    result = c.post(BASE + table + "/records/" + identifier, json={"values": values})
    assert result.status_code == 200, result.text
    return result.json()


def test_tables_parent_validation_duplicates_and_catalog(stock_case):
    c = stock_case[0]
    family = create(c, "article_families", name="Suplementos")
    other = create(c, "article_families", name="Alimentação")
    create(c, "article_subfamilies", name="Natural", family_id=family)
    create(c, "article_subfamilies", name="Natural", family_id=other)
    create(c, "article_brands", name="Marca A")
    assert c.post(BASE + "article_families/records", json={"values": {"name": " suplementos "}}).status_code == 409
    assert c.post(BASE + "article_brands/records", json={"values": {"name": " "}}).status_code == 422
    assert c.post(BASE + "article_subfamilies/records", json={"values": {"name": "Sem mãe", "family_id": "missing"}}).status_code == 422
    catalog = c.get("/api/stock/catalog", params={"metadata_only": True}).json()
    assert catalog["classifications"]["family"][0]["name"] == "Alimentação"
    assert "Marca A" in catalog["facets"]["brand"]  # disponível antes de ter artigos
    fields = c.get(BASE + "article_subfamilies").json()["fields"]
    assert fields[0]["type"] == "select" and len(fields[0]["options"]) == 2
    login(c, "operator")
    assert c.get(BASE + "article_brands/records").status_code == 200
    assert c.post(BASE + "article_brands/records", json={"values": {"name": "Recusada"}}).status_code == 403


def test_rename_and_reparent_update_articles_and_incremental_site_feed(stock_case):
    c = stock_case[0]
    article = product(stock_case, family="Suplementos", category="Imunidade", brand="Marca A")
    conn, headers = connection(stock_case)
    publish(stock_case, conn, article)
    sent = feed(stock_case, headers)[0]
    report(stock_case, headers, ok(sent, 501))
    assert feed(stock_case, headers) == []
    catalog = c.get("/api/stock/catalog").json()["classifications"]
    family, subfamily, brand = (catalog[k][0]["id"] for k in ("family", "category", "brand"))
    revision = view(stock_case, article)["revision"]
    edited = edit(c, "article_families", family, name="Bem-estar")
    assert edited["article_revisions"] == [{"id": article, "before": revision, "after": revision + 1}]
    edit(c, "article_brands", brand, name="Marca B")
    other = create(c, "article_families", name="Saúde")
    edit(c, "article_subfamilies", subfamily, name="Defesas", family_id=other)
    updated = feed(stock_case, headers)[0]
    assert (updated["family"], updated["category"], updated["brand"]) == ("Saúde", "Defesas", "Marca B")
    assert updated["hash"] != sent["hash"] and updated["woo_product_id"] == 501
    assert view(stock_case, article)["revision"] == revision + 3
    report(stock_case, headers, ok(updated, 501))
    assert feed(stock_case, headers) == []


def test_inactive_and_dependency_protection(stock_case):
    c = stock_case[0]
    article = product(stock_case, family="Família", category="Subfamília", brand="Marca")
    catalog = c.get("/api/stock/catalog").json()["classifications"]
    for key, table in (("family", "article_families"), ("category", "article_subfamilies"), ("brand", "article_brands")):
        url = BASE + table + "/records/" + catalog[key][0]["id"]
        assert c.post(url + "/remove", json={"scope": "app", "confirmed": True}).status_code == 409
        assert c.post(url + "/status", json={"active": False}).status_code == 200
    # Fichas existentes conservam a classificação inativa; novas utilizações são recusadas.
    saved = view(stock_case, article)
    assert c.put(f"/api/stock/articles/{article}", json={"code": saved["code"], "name": saved["name"],
        "family": "Família", "category": "Subfamília", "brand": "Marca", "revision": saved["revision"]}).status_code == 200
    assert c.post("/api/stock/articles", json={"code": "NEW", "name": "Novo", "brand": "Marca"}).status_code == 422
    unused = create(c, "article_brands", name="Sem artigos")
    assert c.post(BASE + "article_brands/records/" + unused + "/remove", json={"scope": "app", "confirmed": True}).status_code == 200


def test_site_brands_import_is_additive_idempotent_and_keeps_inactive_and_articles(stock_case):
    c, _, factory, *_ = stock_case
    article = product(stock_case, brand="Marca local")
    inactive = create(c, "article_brands", name="Marca inativa")
    c.post(BASE + "article_brands/records/" + inactive + "/status", json={"active": False})
    conn, headers = connection(stock_case)
    body = {"brands": [{"id": 1, "name": "Marca do site"}, {"id": 2, "name": " marca   LOCAL "},
                       {"id": 3, "name": "MARCA INATIVA"}, {"id": 4, "name": "Marca do SITE"},
                       {"id": 5, "name": "X" * 101}]}
    before = view(stock_case, article)
    response = c.post("/api/webshop/brand-catalog", headers=headers, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["connection_id"] == conn["id"]
    assert len(result["created"]) == 1 and len(result["matched"]) == 3 and len(result["skipped"]) == 1
    again = c.post("/api/webshop/brand-catalog", headers=headers, json=body).json()
    assert not again["created"] and len(again["matched"]) == 4
    with factory() as db:
        assert not db.get(ArticleBrand, inactive).active
        assert len(list(db.scalars(select(ArticleBrand)))) == 3
    assert view(stock_case, article)["brand"] == "Marca local"
    assert view(stock_case, article)["revision"] == before["revision"]
    assert c.post("/api/webshop/brand-catalog", json=body).status_code == 401
    invalid = headers | {"Authorization": "Bearer invalid"}
    assert c.post("/api/webshop/brand-catalog", headers=invalid, json=body).status_code == 401
    assert c.post("/api/webshop/brand-catalog", headers=headers,
                  json={"brands": [{"id": 0, "name": "Inválida"}]}).status_code == 422


@pytest.mark.fresh_schema
def test_migration_adopts_existing_classifications_without_changing_articles(stock_case, monkeypatch):
    _, _, factory, *_ = stock_case
    module = importlib.import_module("migrations.versions.0097_article_taxonomy")
    with factory.begin() as db:
        raw = json.dumps({"family": "Legado", "category": "Sub", "brand": "Marca"})
        db.add(StockArticle(code="LEGACY", name="Existente", data=raw, active=True, revision=7))
    with factory().bind.begin() as conn:
        schema = conn.exec_driver_sql("select current_schema()").scalar()
        monkeypatch.setattr(importlib.import_module("migrations.versions.0006_cash_sheet"), "schemas", lambda _: [schema])
        for model in (ArticleSubfamily, ArticleBrand, ArticleFamily):
            model.__table__.drop(conn)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            module.upgrade()  # reexecução segura
    with factory() as db:
        family = db.scalar(select(ArticleFamily))
        assert family.name == "Legado"
        assert db.scalar(select(ArticleSubfamily)).family_id == family.id
        assert db.scalar(select(ArticleBrand)).name == "Marca"
        article = db.scalar(select(StockArticle).where(StockArticle.code == "LEGACY"))
        assert article.revision == 7 and article.data == raw
