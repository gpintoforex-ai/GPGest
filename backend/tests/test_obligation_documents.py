"""Obrigações: documentos do arquivo (comprovativos, contratos, viaturas) e associação a registos reais, que não
conclui o prazo e faz sair a estimativa da previsão de liquidez."""

from datetime import date

import pytest
from app import budget as budget_module
from app import liquidity, obligations
from app.models import ArchiveDocument, Purchase, Store
from sqlalchemy import select


@pytest.fixture
def case(setup, monkeypatch):
    for module in (budget_module, liquidity, obligations):
        monkeypatch.setattr(module, "today", lambda: date(2026, 11, 11))
    client, _, factory = setup
    created = client.post("/api/obligations", json={"category": "seguro", "title": "Seguro multirriscos", "entity_name": "Fidelidade", "periodicity": "unica",
                                                    "start_date": "2026-11-20", "amount_cents": 42000})
    assert created.status_code == 200, created.text
    occurrence = created.json()["occurrences"][0]
    with factory.begin() as db:
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        db.add_all([Purchase(store_id=store, supplier_nif="500918880", supplier_name="Fidelidade - Companhia de Seguros", document="AP 1", document_type="FT",
                             document_date="2026-11-15", amount_cents=42000, tax_cents=0, base_cents=42000, origin="efatura"),
                    Purchase(store_id=store, supplier_nif="503504564", supplier_name="Elétrica Lusa", document="FT 9", document_type="FT",
                             document_date="2026-11-16", amount_cents=42000, tax_cents=0, base_cents=42000, origin="efatura")])
        doc = ArchiveDocument(source_key="upload:apolice", origin="upload", storage="blob", storage_ref="x", original_name="apolice.pdf", mime="application/pdf")
        db.add(doc)
        db.flush()
        document_id = doc.id
    return client, factory, occurrence, document_id


def test_archive_documents_attach_to_deadlines_contracts_and_vehicles(case):
    client, _, occurrence, document_id = case
    linked = client.post("/api/archive/links/manual", json={"document_id": document_id, "target_kind": "obligation_occurrence", "target_id": occurrence["id"]})
    assert linked.status_code == 200, linked.text
    assert linked.json()["target"]["label"] == "Seguro multirriscos" and linked.json()["page"] == "Obrigações e Prazos"
    docs = client.get(f"/api/obligations/documents?kind=obligation_occurrence&id={occurrence['id']}").json()
    assert [d["name"] for d in docs] == ["apolice.pdf"] and docs[0]["file_url"] == f"/api/archive/documents/{document_id}/file"
    listed = next(o for o in client.get("/api/obligations/occurrences").json() if o["id"] == occurrence["id"])
    assert listed["documents"] == 1 and listed["links"] == 0
    contract = client.post("/api/contracts", json={"entity_name": "MEO", "contract_type": "Telecomunicações", "start_date": "2026-01-01"}).json()
    found = client.get("/api/archive/targets?kind=contract&q=MEO").json()
    assert [t["id"] for t in found] == [contract["id"]] and found[0]["detail"].startswith("Contrato")
    assert client.post("/api/archive/links/manual", json={"document_id": document_id, "target_kind": "contract", "target_id": contract["id"]}).status_code == 200
    assert [d["name"] for d in client.get(f"/api/obligations/documents?kind=contract&id={contract['id']}").json()] == ["apolice.pdf"]
    assert client.get("/api/obligations/documents?kind=vehicle&id=nao-existe").status_code == 404


def test_real_records_are_proposed_linked_without_completing_and_leave_the_forecast(case):
    client, factory, occurrence, _ = case
    data = client.get(f"/api/obligations/occurrences/{occurrence['id']}/matches")
    assert data.status_code == 200, data.text
    proposals = data.json()["proposals"]
    assert [p["entity"] for p in proposals] == ["Fidelidade - Companhia de Seguros"]  # A Elétrica (outra entidade) não é proposta.
    best = proposals[0]
    assert best["score"] == 90 and best["source_kind"] == "purchase" and "Valor igual ao previsto" in best["reasons"]
    forecast = client.post("/api/liquidity/forecast", json={"start": "2026-11-11", "end": "2026-12-31"}).json()
    assert any(e["key"] == "obligation:" + occurrence["id"] for e in forecast["scheduled"])
    other = client.post(f"/api/obligations/occurrences/{occurrence['id']}/links", json={"source_kind": "purchase", "source_id": "inexistente"})
    assert other.status_code == 422
    linked = client.post(f"/api/obligations/occurrences/{occurrence['id']}/links", json={"source_kind": "purchase", "source_id": best["source_id"]})
    assert linked.status_code == 200, linked.text
    assert len(linked.json()["links"]) == 1 and linked.json()["proposals"] == [] and linked.json()["status"] == "pendente"  # Não conclui.
    assert client.post(f"/api/obligations/occurrences/{occurrence['id']}/links", json={"source_kind": "purchase", "source_id": best["source_id"]}).status_code in (409, 422)
    forecast = client.post("/api/liquidity/forecast", json={"start": "2026-11-11", "end": "2026-12-31"}).json()
    pending = {p["key"]: p["exclusion"] for p in forecast["pending"]}
    assert pending["obligation:" + occurrence["id"]] == "Substituída pelo registo real associado ao prazo"
    listed = next(o for o in client.get("/api/obligations/occurrences").json() if o["id"] == occurrence["id"])
    assert listed["links"] == 1 and listed["status"] == "pendente"
    link_id = linked.json()["links"][0]["id"]
    assert client.post(f"/api/obligation-links/{link_id}/undo", json={"reason": ""}).status_code == 422
    undone = client.post(f"/api/obligation-links/{link_id}/undo", json={"reason": "Fatura de outra apólice"}).json()
    assert undone["links"][0]["undo_reason"] == "Fatura de outra apólice" and undone["proposals"]
    forecast = client.post("/api/liquidity/forecast", json={"start": "2026-11-11", "end": "2026-12-31"}).json()
    assert any(e["key"] == "obligation:" + occurrence["id"] for e in forecast["scheduled"])
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}).status_code == 200
    assert client.get(f"/api/obligations/occurrences/{occurrence['id']}/matches").status_code == 403
