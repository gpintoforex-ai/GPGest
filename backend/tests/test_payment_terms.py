from datetime import date

from app.models import BusinessEntity
from app.payment_terms import due_date
from sqlalchemy import select
from test_operating_modes import tenant
from test_platform import create, enter, platform  # noqa: F401


def test_due_date_by_term_type():
    assert due_date(date(2026, 1, 15), "apos", 30) == date(2026, 2, 14)
    assert due_date(date(2026, 2, 10), "fim_mes", None) == date(2026, 2, 28)
    assert due_date(date(2026, 12, 3), "fim_mes_seguinte", None) == date(2027, 1, 31)
    assert due_date(date(2026, 1, 15), "outro", None) is None


def term(code="30DIAS", name="30 Dias", **updates):
    return {"values": {"code": code, "name": name, "term_type": "apos", "days": 30, "discount_percent": "0", **updates}}


def test_entity_payment_days_follow_the_term(platform):  # noqa: F811
    client, _ = platform
    company = create(client, "prazos-locais")
    client.cookies.clear()
    enter(client, company["slug"])
    created = client.post("/api/tables/payment_terms/records", json=term(discount_percent="2,5"))
    assert created.status_code == 200, created.text
    term_id = created.json()["id"]
    end_of_month = client.post(
        "/api/tables/payment_terms/records",
        json=term(code="FIMMES", name="Fim do mês", term_type="fim_mes", days=10),
    ).json()["id"]
    listed = {row["code"]: row for row in client.get("/api/payment-terms").json()}
    assert listed["30DIAS"]["discount_percent"] == "2.50" and listed["FIMMES"]["days"] is None

    entity = {"name": "Fornecedor prazo", "is_customer": False, "is_supplier": True, "payment_term_id": term_id}
    saved = client.post("/api/entities", json=entity).json()
    assert saved["payment_term_id"] == term_id and saved["payment_days"] == 30
    assert client.post("/api/entities", json={**entity, "payment_term_id": "missing"}).status_code == 422
    # Alterar o prazo atualiza os dias das entidades que o usam.
    assert client.post("/api/tables/payment_terms/records/" + term_id, json=term(days=45)).status_code == 200
    with tenant(company) as db:
        assert db.scalar(select(BusinessEntity.payment_days).where(BusinessEntity.name == "Fornecedor prazo")) == 45
    saved = client.post("/api/entities/" + saved["id"], json={**entity, "payment_term_id": end_of_month}).json()
    assert saved["payment_days"] is None
