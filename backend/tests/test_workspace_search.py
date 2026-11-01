import json
from types import SimpleNamespace

from app import workspace_search
from app.db import db_session
from app.main import app
from app.models import (
    ArchiveDocument,
    ArchiveLink,
    Audit,
    BusinessEntity,
    CustomerOrder,
    Movement,
    Register,
    StockArticle,
    StockEntry,
    Store,
    User,
    WintouchEntity,
    WintouchProduct,
)
from conftest import PASSWORD_HASH, create_test_schema, drop_test_schema
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker


def seed(setup):
    client, ids, factory = setup
    with factory.begin() as db:
        own = db.get(Register, ids["registers"][0]).store_id
        other = db.get(Register, ids["registers"][2]).store_id
        articles = [
            StockArticle(
                code=f"AG{i:03}",
                name="Água Mineral " + str(i),
                data=json.dumps(
                    {
                        "barcodes": ["5600000000000"],
                        "supplier_reference": "REF-AGUA",
                        "private_cost": "CUSTO-PRIVADO",
                    }
                ),
            )
            for i in range(8)
        ]
        customer = BusinessEntity(
            name="Água Cliente", tax_number="999000111", is_customer=True, is_supplier=False
        )
        supplier = BusinessEntity(
            name="Água Fornecedor", tax_number="999000222", is_customer=False, is_supplier=True
        )
        removed = BusinessEntity(name="Água Removida", is_customer=True, removed=True)
        orders = [
            CustomerOrder(
                store_id=store,
                user_id=ids["operator"],
                data=json.dumps(
                    {
                        "number": number,
                        "customer_name": "Água Cliente",
                        "secret_notes": "NOTA-PRIVADA",
                    }
                ),
            )
            for store, number in [(own, "PC-AGUA-A"), (other, "PC-AGUA-B")]
        ]
        documents = [
            ArchiveDocument(
                source_key=f"search-{index}",
                origin="upload",
                store_id=store,
                storage="fixture",
                storage_ref="CAMINHO-PRIVADO",
                original_name=f"Água documento {index}.pdf",
                entity_name="Fornecedor fictício",
            )
            for index, store in enumerate([own, other, None])
        ]
        db.add_all([*articles, customer, supplier, removed, *orders, *documents])
        db.flush()
        return {
            "own": own,
            "other": other,
            "article": articles[0].id,
            "customer": "local:" + customer.id,
            "order": orders[0].id,
            "document": documents[0].id,
        }


def groups(response):
    assert response.status_code == 200, response.text
    return {group["id"]: group for group in response.json()["groups"]}


def test_search_is_bounded_literal_and_returns_safe_targets(setup):
    client, _, factory = setup
    rows = seed(setup)
    result = groups(
        client.get("/api/workspace/search", params={"q": "agua", "store_id": rows["own"]})
    )
    assert len(result["articles"]["items"]) == 5 and result["articles"]["more"]
    assert result["customers"]["items"][0]["id"] == rows["customer"]
    assert len(result["orders"]["items"]) == 1
    assert result["orders"]["items"][0]["target"]["params"]["consulta.id"] == rows["order"]
    assert len(result["documents"]["items"]) == 1
    serialized = json.dumps(result)
    for secret in [
        "CUSTO-PRIVADO",
        "CAMINHO-PRIVADO",
        "NOTA-PRIVADA",
        "password_hash",
        "storage_ref",
    ]:
        assert secret not in serialized
    assert "Água Removida" not in serialized
    with factory.begin() as db:
        db.add(StockArticle(code="LITERAL", name="Literal %_ pesquisável", data="{}"))
    result = groups(client.get("/api/workspace/search", params={"q": "%_"}))
    assert [row["label"] for row in result["articles"]["items"]] == ["Literal %_ pesquisável"]
    assert all(
        not group["items"]
        for group in groups(
            client.get("/api/workspace/search", params={"q": "' OR 1=1 --"})
        ).values()
    )
    detail = client.get(
        "/api/stock/catalog", params={"record_id": rows["article"], "page": 1, "size": 1}
    )
    assert detail.status_code == 200, detail.text
    assert [row["id"] for row in detail.json()["articles"]] == [rows["article"]]


def test_query_validation_and_authentication(setup):
    client, _, _ = setup
    assert client.get("/api/workspace/search", params={"q": " a "}).json()["groups"] == []
    assert client.get("/api/workspace/search", params={"q": "x" * 101}).status_code == 422
    assert (
        client.get(
            "/api/workspace/search", params={"q": "agua", "store_id": "not-a-store"}
        ).status_code
        == 403
    )
    client.cookies.clear()
    assert client.get("/api/workspace/search", params={"q": "agua"}).status_code == 401


def test_reader_cannot_search_restricted_entity_tables_or_other_stores(setup):
    client, _, _ = setup
    rows = seed(setup)
    assert (
        client.post(
            "/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"}
        ).status_code
        == 200
    )
    result = groups(client.get("/api/workspace/search", params={"q": "agua"}))
    assert "customers" not in result and "suppliers" not in result
    assert [r["label"] for r in result["orders"]["items"]] == ["PC-AGUA-A"]
    assert len(result["documents"]["items"]) == 1
    assert (
        client.get(
            "/api/workspace/search", params={"q": "agua", "store_id": rows["other"]}
        ).status_code
        == 403
    )
    assert client.get("/api/alerts", params={"store_id": rows["other"]}).status_code == 403


def test_erp_projection_matches_authority_and_omits_deleted_entities(setup, monkeypatch):
    client, _, factory = setup
    monkeypatch.setattr(
        workspace_search, "request_company", lambda db: SimpleNamespace(api_enterprise="ERP-A")
    )
    mode = {"mode": "integrated", "authority": {"entities": "erp"}}
    monkeypatch.setattr(workspace_search, "policy", lambda db: mode)
    with factory.begin() as db:
        foreign = WintouchEntity(
            enterprise_id="ERP-B",
            external_id="foreign",
            name="Árvore de outra ligação",
            is_customer=True,
            active=True,
        )
        entity = WintouchEntity(
            enterprise_id="ERP-A",
            external_id="supplier",
            name="Árvore ERP",
            is_customer=True,
            is_supplier=False,
            active=True,
        )
        product = WintouchProduct(
            enterprise_id="ERP-A",
            external_id="product",
            code="ERP-ART",
            name="Árvore artigo ERP",
            active=True,
            last_cost=999,
        )
        db.add_all([entity, foreign, product])
        db.flush()
        override = BusinessEntity(
            name="Nome local",
            wintouch_id=entity.id,
            is_customer=False,
            is_supplier=True,
            flags_local=True,
            active=True,
        )
        article = StockArticle(code="OLD", name="Nome anterior", erp_id=product.id, data="{}")
        db.add_all([override, article])
        db.flush()
        entity_id = entity.id
        override_id = override.id
    result = groups(client.get("/api/workspace/search", params={"q": "arvore"}))
    assert not result["customers"]["items"]
    assert [row["id"] for row in result["suppliers"]["items"]] == ["wt:" + entity_id]
    assert result["articles"]["items"][0]["label"] == "Árvore artigo ERP"
    assert "outra ligação" not in json.dumps(result, ensure_ascii=False)
    mode["mode"] = "local"
    result = groups(client.get("/api/workspace/search", params={"q": "nome local"}))
    assert result["suppliers"]["items"][0]["label"] == "Nome local"
    with factory.begin() as db:
        db.get(BusinessEntity, override_id).removed = True
    assert not groups(client.get("/api/workspace/search", params={"q": "nome local"}))["suppliers"][
        "items"
    ]


def test_search_has_no_data_writes(setup):
    client, _, factory = setup
    seed(setup)
    models = [Movement, StockEntry, Audit, CustomerOrder, ArchiveLink]

    def snapshot():
        with factory() as db:
            return [db.scalar(select(func.count()).select_from(model)) for model in models]

    before = snapshot()
    assert client.get("/api/workspace/search", params={"q": "agua"}).json()["read_only"]
    assert snapshot() == before


def test_search_isolated_between_company_schemas(setup):
    client, _, _ = setup
    seed(setup)
    original = app.dependency_overrides[db_session]
    schema, engine = create_test_schema()
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            db.add_all(
                [
                    Store(name="Empresa isolada"),
                    User(
                        name="Admin B",
                        email="admin-b@test.pt",
                        role="administrador",
                        password_hash=PASSWORD_HASH,
                    ),
                    StockArticle(code="OUTRA", name="Água de outra empresa", data="{}"),
                ]
            )

        def override():
            with factory() as db:
                yield db

        app.dependency_overrides[db_session] = override
        client.cookies.clear()
        assert (
            client.post(
                "/api/auth/login", json={"email": "admin-b@test.pt", "password": "PasswordTest123!"}
            ).status_code
            == 200
        )
        result = groups(client.get("/api/workspace/search", params={"q": "agua"}))
        assert [row["label"] for row in result["articles"]["items"]] == ["Água de outra empresa"]
        assert not result["orders"]["items"] and not result["documents"]["items"]
    finally:
        app.dependency_overrides[db_session] = original
        drop_test_schema(schema, engine)


def test_alert_archive_counts_follow_visibility_without_writing(setup):
    client, _, _ = setup
    rows = seed(setup)
    result = client.get("/api/alerts", params={"store_id": rows["own"]})
    assert result.status_code == 200, result.text
    group = next(g for g in result.json()["groups"] if g["id"] == "documentos_rever")
    assert group["count"] == 1
    assert result.json()["unit"] == "attention_occurrences" and result.json()["read_only"]
