"""Centro de Conferência: secções agregadas sem escrita, aprovações seguras pelas rotas de lote existentes e
automatizações autorizadas (só administrador, por omissão desligadas, nunca financeiras)."""

from uuid import uuid4

from app.models import Audit, BankAccount, BankTransaction, BudgetLink, PurchaseRubric
from sqlalchemy import func, select
from test_budget_links import PAPER_NIF, case  # noqa: F401


def bank_line(factory, store, amount=-5000):
    with factory.begin() as db:
        account = BankAccount(store_id=store, name="Conta", bank="Banco", iban_last4="0001", currency="EUR")
        db.add(account)
        db.flush()
        db.add(BankTransaction(account_id=account.id, movement_date="2026-10-20", value_date="2026-10-20", description="TRF FORNECEDOR",
                               amount_cents=amount, balance_cents=0, fingerprint=uuid4().hex, source_account="", exported_at="2026-10-20T12:00:00", source_position=0))


def sections(client):
    data = client.get("/api/conference/overview?start=2026-08-12&end=2026-11-10")
    assert data.status_code == 200, data.text
    return data.json(), {s["key"]: s for s in data.json()["sections"]}


def test_overview_aggregates_every_module_without_writing(case):  # noqa: F811
    client, factory, ids = case
    bank_line(factory, ids["store"])
    with factory() as db:
        audits = db.scalar(select(func.count()).select_from(Audit))
    data, by_key = sections(client)
    assert by_key["orcamento"]["count"] == 3 and by_key["orcamento"]["safe"]["count"] == 2  # Renda out/nov (99 %); eletricidade 70 %.
    assert by_key["sem_orcamento"]["count"] == 1 and by_key["sem_orcamento"]["amount_cents"] == 3000
    assert by_key["rubricas"]["count"] == 4  # Todas só com a rubrica do fornecedor.
    assert by_key["reconciliacao"]["count"] == 1 and by_key["reconciliacao"]["amount_cents"] == 5000
    assert by_key["novos"]["count"] == 4 and by_key["faltas"]["count"] == 4  # Sem PDF.
    assert by_key["reconciliacao"]["page"] == "Reconciliação automática" and by_key["orcamento"]["params"] == {"area.orcamento": "associacoes"}
    assert data["automation"]["classification"]["enabled"] is False and data["automation"]["frequency"] == "manual"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Audit)) == audits


def test_safe_approvals_reuse_the_existing_batch_routes(case):  # noqa: F811
    client, factory, ids = case
    _, by_key = sections(client)
    safe = by_key["orcamento"]["safe"]
    confirmed = client.post("/api" + safe["endpoint"], json=safe["body"])
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"] == 2
    _, after = sections(client)
    assert after["orcamento"]["count"] == 1 and after["orcamento"]["safe"] is None
    assert client.post("/api" + safe["endpoint"], json=safe["body"]).status_code == 409  # Repetir não conta duas vezes.


def test_automation_is_admin_only_off_by_default_and_applies_only_authorized_operations(case):  # noqa: F811
    client, factory, ids = case
    path = "/api/conference/automation"
    assert client.post(path + "/run").status_code == 409  # Nada ativo.
    rule = {"name": "Papelaria", "supplier_nif": PAPER_NIF, "document_type": "FT", "store_id": ids["store"], "shares": [{"category_id": ids["rent"], "value": "100"}]}
    assert client.post("/api/classification/rules", json=rule).status_code == 200
    body = {"revision": 0, "frequency": "diaria", "classification": {"enabled": True, "min_confidence": 95}, "budget_links": {"enabled": True, "min_confidence": 99}}
    assert client.put(path, json={**body, "revision": 3}).status_code == 409
    assert client.put(path, json={**body, "classification": {"enabled": True, "min_confidence": 80}}).status_code == 422
    saved = client.put(path, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["authorized_by"] == "Admin" and saved.json()["revision"] == 1
    result = client.post(path + "/run")
    assert result.status_code == 200, result.text
    # Regra com 3 critérios (97 %) aplicada; renda de outubro e novembro associadas (99 %); eletricidade (70 %) fica.
    assert result.json()["classified"] == 1 and result.json()["linked"] == 2 and not result.json()["errors"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseRubric)) == 1
        links = list(db.scalars(select(BudgetLink).where(BudgetLink.status == "confirmada")))
        assert len(links) == 2 and all("Automatização autorizada" in link.reasons for link in links)
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "automatizacoes_executadas")) == 1
    state = client.get(path).json()
    assert state["last_run_at"] and state["last_result"]["linked"] == 2
    # Executar outra vez não repete nada.
    assert client.post(path + "/run").json() == {"classified": 0, "linked": 0, "errors": []}
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get("/api/conference/overview").status_code == 403
    assert client.put(path, json={**body, "revision": 1}).status_code == 403
