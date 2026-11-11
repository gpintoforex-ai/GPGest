"""Última venda na tabela de Clientes: documento de venda válido mais recente (anulados, notas de crédito e outros
documentos ficam de fora), vários documentos no mesmo dia, indicador de atividade com limites configuráveis,
ordenação, filtros, abertura do documento, atualização após nova sincronização, modo local (SAF-T) e integrado
(ERP). Dados fictícios."""

import json
from datetime import date

import pytest
from app import customer_activity
from app.models import Audit, BusinessEntity, Store, WintouchSaleRecord
from saft_fixtures import customer, invoice, saft
from sqlalchemy import select
from test_saft_sales import confirm as confirm_import
from test_saft_sales import (
    saft_case,  # noqa: F401
    upload,
)


def nif(prefix):
    """NIF fictício válido (dígito de controlo calculado)."""
    digits = [int(c) for c in prefix]
    rest = sum(d * (9 - i) for i, d in enumerate(digits)) % 11
    return prefix + str(0 if rest < 2 else 11 - rest)


ALFA, BETA, GAMA, DELTA = nif("50000011"), nif("50000012"), nif("50000013"), nif("50000014")


def records(client, **params):
    response = client.get("/api/tables/customers/records", params={"size": 200, "status": "all", **params})
    assert response.status_code == 200, response.text
    return response.json()


def by_name(listing):
    return {row["description"]: row for row in listing["rows"]}


@pytest.fixture
def local(saft_case, monkeypatch):  # noqa: F811
    client, ids, factory, store = saft_case
    monkeypatch.setattr("app.billing_conference.policy", lambda db: {"mode": "local"})
    monkeypatch.setattr(customer_activity, "today", lambda: date(2026, 1, 20))
    customer_activity.CACHE.clear()
    invoices = [invoice("FT", 1, "12.30"), invoice("FT", 2, "24.60"),  # dois documentos no mesmo dia: conta o mais recente
                invoice("NC", 3, "12.30"),  # nota de crédito posterior: não substitui a última venda
                invoice("FT", 4, "12.30", status="A", customer_id="C2"),  # só anulado → sem histórico
                invoice("FS", 5, "6.15", customer_id="C3")]
    customers = customer("C1", ALFA, "Cliente Alfa") + customer("C2", BETA, "Cliente Beta") + customer("C3", GAMA, "Cliente Gama")
    row = upload((client, ids, factory, store), saft(invoices, customers=customers))
    assert row["status"] == "ready", row
    assert confirm_import((client, ids, factory, store), row)["status"] == "completed"
    with factory.begin() as db:
        db.add(BusinessEntity(name="Cliente Delta", tax_number=DELTA, is_customer=True))  # nunca comprou
    return client, factory


def test_local_last_sale_rules_states_and_document(local):
    client, _ = local
    listing = records(client)
    rows = by_name(listing)
    alfa, beta, gama, delta = (rows[n]["extra"]["last_sale"] for n in ("Cliente Alfa", "Cliente Beta", "Cliente Gama", "Cliente Delta"))
    assert (alfa["date"], alfa["document"], alfa["type"], alfa["state"], alfa["label"]) == ("2026-01-10", "FT TESTE/2", "FT", "ativo", "Ativo")
    assert (gama["document"], gama["type"]) == ("FS TESTE/5", "FS")
    assert not beta["has_sale"] and beta["state"] == "sem_historico" and "histórico pode estar incompleto" in beta["hint"]
    assert not delta["has_sale"] and rows["Cliente Delta"]["labels"]["last_sale_text"] == "Sem vendas Sem histórico"
    meta = listing["meta"]["last_sale"]
    assert (meta["mode"], meta["data_from"], meta["data_until"], meta["days"]) == ("saft", "2026-01-10", "2026-01-10", [30, 90, 180])
    # Abrir o documento da última venda: a consulta documental da análise comercial.
    detail = client.get("/api/sales/detail", params={"view": "document", "id": alfa["id"], "start": alfa["date"], "end": alfa["date"]})
    assert detail.status_code == 200 and detail.json()["document"] == "FT TESTE/2" and detail.json()["lines"]


def test_sorting_filters_search_and_thresholds(local, monkeypatch):
    client, factory = local
    ordered = [r["description"] for r in records(client, sort="last_sale", direction="asc")["rows"]]
    assert ordered.index("Cliente Beta") < ordered.index("Cliente Alfa") and ordered.index("Cliente Delta") < ordered.index("Cliente Gama")
    assert {r["description"] for r in records(client, activity="recent30")["rows"]} == {"Cliente Alfa", "Cliente Gama"}
    assert {r["description"] for r in records(client, activity="never")["rows"]} >= {"Cliente Beta", "Cliente Delta"}
    assert {r["description"] for r in records(client, sale_from="2026-01-01", sale_to="2026-01-31")["rows"]} == {"Cliente Alfa", "Cliente Gama"}
    assert [r["description"] for r in records(client, q="10/01/2026 ativo")["rows"]].count("Cliente Alfa") == 1
    # 203 dias depois: inativo; os filtros de «sem vendas há mais de N dias» incluem quem nunca comprou.
    monkeypatch.setattr(customer_activity, "today", lambda: date(2026, 8, 1))
    rows = by_name(records(client))
    assert rows["Cliente Alfa"]["extra"]["last_sale"]["state"] == "inativo"
    assert rows["Cliente Alfa"]["extra"]["last_sale"]["stale"] is True  # dados só até janeiro
    assert {"Cliente Alfa", "Cliente Delta"} <= {r["description"] for r in records(client, activity="over180")["rows"]}
    # Limites configuráveis (só administrador, com revisão e auditoria).
    current = client.get("/api/customers/activity-settings").json()
    assert current["days"] == [30, 90, 180] and current["can_edit"]
    assert client.put("/api/customers/activity-settings", json={"days": [90, 30, 400], "revision": 0}).status_code == 422
    assert client.put("/api/customers/activity-settings", json={"days": [60, 200, 400], "revision": 0}).json()["days"] == [60, 200, 400]
    assert client.put("/api/customers/activity-settings", json={"days": [60, 200, 400], "revision": 0}).status_code == 409
    assert by_name(records(client))["Cliente Alfa"]["extra"]["last_sale"]["state"] == "risco"
    with factory() as db:
        assert db.scalar(select(Audit).where(Audit.action == "atividade_clientes_limites"))


@pytest.fixture
def erp(setup, monkeypatch):
    client, _, factory = setup
    monkeypatch.setattr(customer_activity, "today", lambda: date(2026, 11, 11))
    customer_activity.CACHE.clear()
    with factory.begin() as db:
        store = db.scalar(select(Store).where(Store.name == "Teste")).id
        db.add_all([BusinessEntity(name="Cliente Alfa", tax_number=ALFA, is_customer=True),
                    BusinessEntity(name="Loja Sem NIF", tax_number="", is_customer=True),
                    BusinessEntity(name="Cliente Delta", tax_number=DELTA, is_customer=True)])

        def doc(key, day, entity, nif_value, direction=1, cancelled=False, number=None):
            db.add(WintouchSaleRecord(enterprise_id="wt", external_id="document:" + key, store_id=store, kind="documento", document_date=day,
                                      data=json.dumps({"document_group": "vendas", "document": number or f"FT 1/{key}", "type": "FT" if direction > 0 else "NC",
                                                       "entity": entity, "entity_nif": nif_value, "direction": direction, "cancelled": cancelled,
                                                       "amount_cents": 1230, "net_cents": 1000, "tax_cents": 230, "source_document_id": key})))
        doc("1", "2026-10-30", "Cliente Alfa", ALFA)
        doc("2", "2026-11-02", "Cliente Alfa", ALFA, direction=-1, number="NC 1/2")  # nota de crédito posterior
        doc("3", "2026-11-05", "Cliente Alfa", ALFA, cancelled=True)  # anulado posterior
        doc("4", "2026-05-01", "Loja sem NIF", "")  # sem NIF: liga pelo nome
    return client, factory, store


def test_integrated_mode_and_refresh_after_sync(erp):
    client, factory, store = erp
    rows = by_name(records(client))
    alfa = rows["Cliente Alfa"]["extra"]["last_sale"]
    assert (alfa["date"], alfa["document"], alfa["state"]) == ("2026-10-30", "FT 1/1", "ativo")
    assert rows["Loja Sem NIF"]["extra"]["last_sale"]["state"] == "inativo"
    assert rows["Cliente Delta"]["extra"]["last_sale"]["has_sale"] is False
    # Nova venda sincronizada: a tabela atualiza sem esperar pela cache.
    with factory.begin() as db:
        db.add(WintouchSaleRecord(enterprise_id="wt", external_id="document:9", store_id=store, kind="documento", document_date="2026-11-10",
                                  data=json.dumps({"document_group": "vendas", "document": "FR 1/9", "type": "FR", "entity": "Cliente Delta",
                                                   "entity_nif": DELTA, "direction": 1, "amount_cents": 615, "source_document_id": "9"})))
    delta = by_name(records(client))["Cliente Delta"]["extra"]["last_sale"]
    assert (delta["date"], delta["document"], delta["state"]) == ("2026-11-10", "FR 1/9", "ativo")
    assert records(client)["meta"]["last_sale"]["mode"] == "erp"
