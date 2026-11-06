"""Movimentos sem documento: rubrica, funcionário e custo na resolução; regras por descrição; mapas."""
from uuid import uuid4

from app.models import Audit, BankBookEntry, BankRule, Employee
from sqlalchemy import select
from test_bank_balances import bank, case, preview  # noqa: F401


def employee(case):  # noqa: F811
    with case[2].begin() as db:
        row = Employee(name="Ana Funcionária")
        db.add(row)
        db.flush()
        return row.id


def resolve(case, bank_id, **extra):  # noqa: F811
    data, p = preview(case, bank_id, **extra)
    response = case[0].post(f"/api/bank-transactions/{bank_id}/resolve",
                            json={**data, "preview_token": p["preview_token"], "request_key": str(uuid4()), "confirmed": True})
    assert response.status_code == 200, response.text
    return p


def test_resolution_with_rubric_employee_cost_and_rule_feeds_suggestions_and_maps(case):  # noqa: F811
    client, ids, factory = case
    person = employee(case)
    salary = bank(case, -85000, stamp="2026-01-28", description="TRF VENCIMENTO JANEIRO ANA")
    p = resolve(case, salary, description="Vencimento de janeiro", category_id=ids["category"], employee_id=person,
                counts_as_cost=True, rule={"name": "Vencimento Ana", "pattern": "TRF Vencimento"})
    assert p["category_name"] and p["employee_name"] == "Ana Funcionária" and p["counts_as_cost"] is True
    with factory() as db:
        entry = db.scalar(select(BankBookEntry).where(BankBookEntry.source_bank_id == salary))
        assert (entry.category_id, entry.employee_id, entry.counts_as_cost) == (ids["category"], person, True)
        rule = db.scalar(select(BankRule))
        assert (rule.pattern, rule.account_id, rule.direction, rule.kind) == ("trf vencimento", ids["account"], -1, "payment")
        assert db.scalar(select(Audit).where(Audit.action == "banco_regra_criada", Audit.target_id == rule.id))
    # O mês seguinte: a regra propõe o tratamento; um movimento de entrada ou de outra conta não.
    february = bank(case, -85000, stamp="2026-02-27", description="TRF VENCIMENTO FEVEREIRO ANA")
    bank(case, 85000, stamp="2026-02-27", description="TRF VENCIMENTO DEVOLVIDO")
    bank(case, -85000, stamp="2026-02-27", description="TRF VENCIMENTO OUTRA CONTA", account=ids["other"])
    overview = client.get("/api/reconciliation").json()
    proposed = {s["bank_id"]: s["rule"] for s in overview["rule_suggestions"]}
    assert list(proposed) == [february]
    assert proposed[february]["employee_name"] == "Ana Funcionária" and proposed[february]["counts_as_cost"] is True
    # Custo sem documento nos mapas de gestão, no Dashboard (despesas) e nas despesas por rubrica.
    costs = client.get("/api/management/costs-revenues", params={"start": "2026-01-01", "end": "2026-01-31"}).json()
    rubric = next(c for c in costs["costs"] if c["category_id"] == ids["category"])
    assert rubric["values"]["2026-01"] == 85000
    expenses = client.get("/api/rubric-expenses", params={"start": "2026-01-01", "end": "2026-01-31"}).json()
    document = next(d for d in expenses["documents"] if d["source"] == "Banco")
    assert document["amount_cents"] == 85000 and document["supplier_name"] == "Vencimento de janeiro"
    # Sem «conta como custo» (ex.: pagamento de IVA), não entra nos custos.
    tax = bank(case, -30000, stamp="2026-01-20", description="PAG ESTADO IVA")
    resolve(case, tax, description="IVA do 4.º trimestre", category_id=ids["category"])
    costs = client.get("/api/management/costs-revenues", params={"start": "2026-01-01", "end": "2026-01-31"}).json()
    assert next(c for c in costs["costs"] if c["category_id"] == ids["category"])["values"]["2026-01"] == 85000


def test_resolution_classification_is_validated(case):  # noqa: F811
    client, ids, _ = case
    outgoing = bank(case, -5000, description="DD SEG SOCIAL 123")
    base = {"kind": "payment", "description": "Segurança Social", "reference": ""}

    def refused(**extra):
        response = client.post(f"/api/bank-transactions/{outgoing}/resolution-preview", json={**base, **extra})
        assert response.status_code == 422, response.text
        return response.json()["detail"]

    assert "rubrica" in refused(counts_as_cost=True)
    assert "transferência" in refused(kind="transfer", target_account_id=ids["other"], category_id=ids["category"]).lower()
    assert "descrição deste movimento" in refused(rule={"name": "Segurança Social", "pattern": "IRS"})
    assert "Rubrica não encontrada" in refused(category_id=str(uuid4()))
    incoming = bank(case, 5000, description="JUROS")
    response = client.post(f"/api/bank-transactions/{incoming}/resolution-preview",
                           json={"kind": "interest", "description": "Juros", "category_id": ids["category"], "counts_as_cost": True})
    assert response.status_code == 422 and "custo" in response.json()["detail"]


def test_rules_are_managed_and_the_longest_text_wins(case):  # noqa: F811
    client, ids, factory = case
    body = {"name": "Segurança Social", "pattern": "seg social", "kind": "payment", "direction": -1,
            "category_id": ids["category"], "counts_as_cost": True}
    assert client.post("/api/bank-rules", json={**body, "direction": 1}).status_code == 422
    assert client.post("/api/bank-rules", json={**body, "pattern": "ss"}).status_code == 422
    general = client.post("/api/bank-rules", json=body).json()["id"]
    specific = client.post("/api/bank-rules", json={**body, "name": "SS gerência", "pattern": "seg social gerencia",
                                                    "account_id": ids["account"], "counts_as_cost": False}).json()["id"]
    rules = {r["id"]: r for r in client.get("/api/bank-rules").json()}
    assert rules[general]["account_name"] == "Todas as contas" and rules[specific]["kind_label"] == "Pagamento"
    one = bank(case, -10000, description="DD SEG SOCIAL GERENCIA 01")
    two = bank(case, -20000, description="DD SEG. SOCIAL 02")
    suggested = {s["bank_id"]: s["rule"]["id"] for s in client.get("/api/reconciliation").json()["rule_suggestions"]}
    assert suggested == {one: specific, two: general}
    # Desativar, alterar e remover ficam na auditoria; regra inativa não propõe.
    assert client.post(f"/api/bank-rules/{specific}", json={**body, "name": "SS gerência", "pattern": "seg social gerencia",
                                                            "account_id": ids["account"], "active": False}).status_code == 200
    suggested = {s["bank_id"]: s["rule"]["id"] for s in client.get("/api/reconciliation").json()["rule_suggestions"]}
    assert suggested == {one: general, two: general}
    assert client.post(f"/api/bank-rules/{general}/remove").status_code == 200
    with factory() as db:
        actions = set(db.scalars(select(Audit.action).where(Audit.action.like("banco_regra_%"))))
        assert actions == {"banco_regra_criada", "banco_regra_alterada", "banco_regra_removida"}
    assert client.get("/api/reconciliation").json()["rule_suggestions"] == []
