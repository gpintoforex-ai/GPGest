"""Regras de correspondência configuráveis: tolerância de valor (cêntimos e %), exceções por fornecedor,
confiança mínima para propor e limites do lote. As regras só geram propostas; nada fica pago sem confirmação."""

import json

from app.models import Audit
from app.reconciliation import suggestion
from app.reconciliation_center import effective
from sqlalchemy import select
from test_bank_balances import bank, case, movement  # noqa: F401

BANK = {"id": "b", "store_id": "s", "amount_cents": -9950, "available_cents": 9950, "date": "2026-10-10", "description": "DD 00123 SEPA"}
INVOICE = {"id": "p", "source": "purchase", "store_id": "s", "amount_cents": -10000, "available_cents": 10000, "date": "2026-10-10",
           "description": "FT X/1", "reference": "FT X/1", "entity": "Fornecedor Desconhecido"}


def test_amount_tolerance_turns_a_near_value_into_a_proposal_of_the_smaller_amount():
    assert suggestion(BANK, INVOICE) is None  # Valor diferente sem referência nem entidade: nada.
    assert suggestion(BANK, INVOICE, amount_tolerance=49) is None
    result = suggestion(BANK, INVOICE, amount_tolerance=50)
    assert result["score"] == 55 and result["amount_cents"] == 9950  # Nunca se propõe mais do que o banco pagou.
    assert "Diferença de 0,50 € dentro da tolerância da conta" in result["reasons"]
    # Fora da tolerância de datas só faturas pagas depois, com entidade identificada.
    late = {**BANK, "date": "2026-11-05", "description": "DD Fornecedor Desconhecido"}
    assert suggestion(late, INVOICE, tolerance=10, amount_tolerance=50)["amount_cents"] == 9950


def test_effective_rules_use_the_larger_limit_and_supplier_exceptions():
    policy = {"date_tolerance_days": 10, "amount_tolerance_cents": 20, "amount_tolerance_percent": "1.5", "suppliers": []}
    assert effective(policy, INVOICE) == (10, 150, None)  # 1,5 % de 100,00 € > 0,20 €.
    policy["suppliers"] = [{"match": "fornecedor desconhecido", "date_tolerance_days": 45, "amount_tolerance_cents": None, "note": ""},
                           {"match": "outro", "date_tolerance_days": 2, "amount_tolerance_cents": 0, "note": ""}]
    assert effective(policy, INVOICE) == (45, 150, "fornecedor desconhecido")
    assert effective(policy, {**INVOICE, "entity": "Outro, Lda"}) == (2, 0, "outro")


def test_rules_round_trip_validation_audit_and_minimum_score(case):  # noqa: F811
    client, ids, factory = case
    path = f"/api/reconciliation/accounts/{ids['account']}/rules"
    defaults = client.get(path).json()
    assert defaults == {"date_tolerance_days": 10, "grouped_proposals": True, "amount_tolerance_cents": 0, "amount_tolerance_percent": "0",
                        "min_score": 30, "safe_score": 80, "safe_gap": 20, "suppliers": []}
    assert client.put(path, json={**defaults, "min_score": 70, "safe_score": 60}).status_code == 422
    assert client.put(path, json={**defaults, "amount_tolerance_percent": "11"}).status_code == 422
    assert client.put(path, json={**defaults, "suppliers": [{"match": "ab"}]}).status_code == 422
    rules = {**defaults, "amount_tolerance_cents": 100, "suppliers": [{"match": "EDP Comercial", "date_tolerance_days": 40, "note": "Débito direto"}]}
    saved = client.put(path, json=rules)
    assert saved.status_code == 200, saved.text
    assert saved.json()["suppliers"][0] == {"match": "EDP Comercial", "date_tolerance_days": 40, "amount_tolerance_cents": None, "note": "Débito direto"}
    with factory() as db:
        detail = json.loads(db.scalar(select(Audit.detail).where(Audit.action == "reconciliacao_regras").order_by(Audit.created_at.desc()).limit(1)))
        assert detail["antes"]["amount_tolerance_cents"] == 0 and detail["depois"]["amount_tolerance_cents"] == 100
    # Movimento e registo com 0,80 € de diferença: proposta pela tolerância; com confiança mínima alta, nenhuma.
    m = movement(case, 25000)
    b = bank(case, 24920)
    proposals = [p for p in client.get("/api/reconciliation").json()["suggestions"] if p["bank_id"] == b and p["source_id"] == m]
    assert proposals and proposals[0]["amount_cents"] == 24920 and any("tolerância" in r for r in proposals[0]["reasons"])
    assert client.get("/api/reconciliation").json()["rules"][ids["account"]]["amount_tolerance_cents"] == 100
    assert client.put(path, json={**rules, "min_score": 95, "safe_score": 95}).status_code == 200
    assert not [p for p in client.get("/api/reconciliation").json()["suggestions"] if p["bank_id"] == b and p["source_id"] == m]
    # Nada foi reconciliado: o movimento continua disponível por inteiro.
    assert next(r for r in client.get("/api/reconciliation").json()["banks"] if r["id"] == b)["available_cents"] == 24920
