from app.models import StockArticle, StockEntry, StockSettings
from saft_fixtures import invoice, saft
from sqlalchemy import func, select
from test_saft_sales import confirm, saft_case, upload  # noqa: F401


def imported(case):
    row = upload(case, saft([invoice()]))
    assert confirm(case, row)["status"] == "completed"


def test_articles_are_proposed_and_created_only_on_confirmation(saft_case):  # noqa: F811
    client, _, factory, _ = saft_case
    imported(saft_case)
    data = client.get("/api/stock/saft-articles").json()
    proposal = next(p for p in data["proposals"] if p["code"] == "P1")
    assert proposal["action"] == "create" and proposal["name"] == "Serviço fictício"
    assert proposal["vat_rate"] == "23" and proposal["unit"] == "UN" and proposal["lines"] == 1
    # Serviço: proposto, mas desmarcado e com aviso de que não costuma ter stock.
    assert proposal["selected"] is False and proposal["warnings"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(StockArticle)) == 0  # Propor não cria nada.
    body = {"confirmed": True, "items": [{"code": "P1", "action": "create", "name": "Serviço de teste", "unit": "UN", "vat_rate": "23", "sale_net": "10"}]}
    result = client.post("/api/stock/saft-articles", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["created"] == 1
    with factory() as db:
        article = db.scalar(select(StockArticle).where(StockArticle.code == "P1"))
        assert article.name == "Serviço de teste"
        assert db.scalar(select(func.count()).select_from(StockEntry)) == 0  # Sem movimentos de stock.
        import json

        assert json.loads(db.get(StockSettings, "default").data)["saft_mapping"]["P1"] == article.id
    assert not [p for p in client.get("/api/stock/saft-articles").json()["proposals"] if p["code"] == "P1"]
    assert client.post("/api/stock/saft-articles", json=body).status_code == 409  # Não duplica.


def test_existing_article_with_same_code_is_proposed_for_linking(saft_case):  # noqa: F811
    client, _, factory, _ = saft_case
    with factory.begin() as db:
        existing = StockArticle(code="P1", name="Artigo existente", data="{}")
        db.add(existing)
        db.flush()
        existing_id = existing.id
    imported(saft_case)
    proposal = next(p for p in client.get("/api/stock/saft-articles").json()["proposals"] if p["code"] == "P1")
    assert proposal["action"] == "link" and proposal["article_id"] == existing_id and proposal["reason"] == "Mesmo código"
    create = client.post("/api/stock/saft-articles", json={"confirmed": True, "items": [{"code": "P1", "action": "create", "name": "Outro"}]})
    assert create.status_code == 409  # Nunca cria um segundo artigo com o mesmo código.
    link = client.post("/api/stock/saft-articles", json={"confirmed": True, "items": [{"code": "P1", "action": "link", "article_id": existing_id}]})
    assert link.status_code == 200 and link.json()["linked"] == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(StockArticle)) == 1
