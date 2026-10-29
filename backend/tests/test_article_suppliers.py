"""Condições por fornecedor de cada artigo; dados sintéticos."""

from app.models import Audit, BusinessEntity
from sqlalchemy import select
from test_inventory import login, product
from test_inventory import stock_case as stock_case


def suppliers(factory):
    with factory.begin() as db:
        rows = [
            BusinessEntity(name="Fornecedor Alfa", is_supplier=True),
            BusinessEntity(name="Fornecedor Beta", is_supplier=True),
            BusinessEntity(name="Só cliente", is_customer=True),
        ]
        db.add_all(rows)
        db.flush()
        return [r.id for r in rows]


def url(pid, supplier=""):
    return f"/api/stock/articles/{pid}/supplier-terms" + (f"/{supplier}" if supplier else "")


def test_terms_per_supplier_are_saved_listed_and_removed(stock_case):
    client, _, factory, _, _ = stock_case
    alpha, beta, customer = suppliers(factory)
    pid = product(stock_case, supplier_id=beta)
    assert client.get(url(pid)).json() == {"terms": [], "can_edit": True, "cost_visible": True}

    saved = client.put(url(pid, alpha), json={
        "supplier_reference": " ALF  001 ", "agreed_cost": "2.125", "valid_until": "2026-12-31",
        "lead_days": 5, "min_quantity": "12", "pack_quantity": "6", "notes": "Desconto de volume",
    })
    assert saved.status_code == 200, saved.text
    terms = client.put(url(pid, beta), json={"supplier_reference": "BET-9"}).json()["terms"]
    # O fornecedor principal aparece primeiro.
    assert [t["supplier_name"] for t in terms] == ["Fornecedor Beta", "Fornecedor Alfa"]
    assert terms[0]["main"] and not terms[1]["main"]
    a = terms[1]
    assert a["supplier_reference"] == "ALF 001" and float(a["agreed_cost"]) == 2.125
    assert (a["valid_until"], a["lead_days"], float(a["min_quantity"]), float(a["pack_quantity"])) == (
        "2026-12-31", 5, 12, 6)
    assert a["revision"] == 1 and a["updated_by"]

    # Revisão desatualizada é recusada; a certa atualiza.
    assert client.put(url(pid, alpha), json={"revision": 0, "agreed_cost": "2"}).status_code == 409
    updated = client.put(url(pid, alpha), json={"revision": 1, "agreed_cost": "2"}).json()["terms"]
    assert float(next(t for t in updated if t["supplier_id"] == alpha)["agreed_cost"]) == 2

    for bad in (
        {"agreed_cost": "-1"}, {"lead_days": -1}, {"min_quantity": "0"}, {"pack_quantity": "0"},
        {"supplier_reference": "x" * 101},
    ):
        assert client.put(url(pid, alpha), json={"revision": 2, **bad}).status_code == 422, bad
    assert client.put(url(pid, customer), json={}).status_code == 422
    assert client.put(url("nao-existe", alpha), json={}).status_code == 404

    remaining = client.delete(url(pid, alpha)).json()["terms"]
    assert [t["supplier_id"] for t in remaining] == [beta]
    assert client.delete(url(pid, alpha)).status_code == 404
    with factory() as db:
        actions = list(db.scalars(select(Audit.action).where(Audit.target_id == pid)))
    assert actions.count("stock_article_supplier_terms") == 3
    assert "stock_article_supplier_terms_removed" in actions


def test_terms_respect_cost_and_edit_permissions(stock_case):
    client, _, factory, _, _ = stock_case
    alpha, _, _ = suppliers(factory)
    pid = product(stock_case)
    client.put(url(pid, alpha), json={"supplier_reference": "ALF", "agreed_cost": "3"})
    login(client, "reader")
    seen = client.get(url(pid)).json()
    assert not seen["can_edit"] and not seen["cost_visible"]
    assert seen["terms"][0]["agreed_cost"] is None and seen["terms"][0]["supplier_reference"] == "ALF"
    assert client.put(url(pid, alpha), json={"revision": 1}).status_code == 403
    assert client.delete(url(pid, alpha)).status_code == 403


def test_supplier_with_article_terms_counts_as_referenced(stock_case):
    from app.entities import annotate_entity_movement_history

    client, _, factory, _, _ = stock_case
    alpha, beta, _ = suppliers(factory)
    pid = product(stock_case)
    client.put(url(pid, alpha), json={})
    with factory() as db:
        rows = [{"id": alpha, "name": "Fornecedor Alfa"}, {"id": beta, "name": "Fornecedor Beta"}]
        annotate_entity_movement_history(rows, db)
    assert rows[0]["has_movements"] and not rows[1]["has_movements"]
