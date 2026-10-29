from test_platform import create, enter
from test_platform import platform as platform  # noqa: F401


def test_inventory_company_isolation_and_foreign_identifiers(platform):
    client, _ = platform
    a, b = create(client, "stock-tenant-a"), create(client, "stock-tenant-b")
    client.cookies.clear()
    boot = enter(client, a["slug"])
    warehouse = client.post(
        "/api/stock/warehouses", json={"store_id": boot["stores"][0]["id"], "name": "Armazém A"}
    )
    assert warehouse.status_code == 200, warehouse.text
    wid = warehouse.json()["id"]
    article = client.post("/api/stock/articles", json={"code": "A", "name": "Empresa A"})
    assert article.status_code == 200, article.text
    pid = article.json()["id"]
    operation = {
        "article_id": pid,
        "warehouse_id": wid,
        "kind": "opening",
        "quantity": "2",
        "reason": "Saldo de A",
        "request_key": "only-tenant-a",
    }
    assert client.post("/api/stock/movements", json=operation).status_code == 200
    assert len(client.get("/api/stock/catalog").json()["articles"]) == 1
    enter(client, b["slug"])
    catalog = client.get("/api/stock/catalog").json()
    assert catalog["articles"] == [] and catalog["warehouses"] == []
    assert client.get("/api/stock/catalog?page=1").json()["total"] == 0
    assert client.get("/api/stock/movements").json()["rows"] == []
    assert client.post("/api/stock/movements", json=operation).status_code == 404
    assert client.delete(f"/api/stock/articles/{pid}").status_code == 404
    assert client.post(
        f"/api/stock/warehouses/{wid}/delete", json={"confirmed": True}
    ).status_code == 404
    assert client.get(f"/api/stock/lots?warehouse_id={wid}").status_code == 404
    other = client.post("/api/stock/articles", json={"code": "A", "name": "Empresa B"})
    assert other.status_code == 200
    enter(client, a["slug"])
    assert client.get("/api/stock/catalog").json()["articles"][0]["name"] == "Empresa A"
