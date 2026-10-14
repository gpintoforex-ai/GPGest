import io
import json
import zipfile
from datetime import date

import pytest
from app import accounting_delivery
from app.models import AccountingPeriod, Audit, Purchase, PurchaseAttachment
from sqlalchemy import func, select
from test_platform import platform  # noqa: F401
from test_reconciliation import seed

PERIOD = {"vat_period": "mensal", "year": 2026, "number": 10}


@pytest.fixture
def delivery(setup, monkeypatch):
    monkeypatch.setattr(accounting_delivery, "local_today", lambda: date(2026, 11, 2))
    client, factory, _, _, documents, _ = seed(setup, (), (12300, 12900))
    assert client.post("/api/settings/fiscal", json={"vat_period": "mensal"}).status_code == 200
    return client, factory, documents


def test_explicit_close_export_reopen_and_audit(delivery):
    client, factory, documents = delivery
    assert client.post("/api/accounting/delivery-periods/export", json=PERIOD).status_code == 409
    assert (
        client.post(
            "/api/accounting/delivery-periods/close",
            json={**PERIOD, "reason": "Conferido pelo administrador"},
        ).status_code
        == 200
    )
    # Repeating a close does not create a new period or audit event.
    assert (
        client.post(
            "/api/accounting/delivery-periods/close", json={**PERIOD, "reason": "Repetição"}
        ).status_code
        == 200
    )
    response = client.get("/api/accounting/delivery-periods?year=2026")
    october = response.json()["periods"][9]
    assert october["closed_at"] and october["documents"] == 2 and october["without_attachment"] == 2
    # Include all available local originals; missing originals stay in the manifest.
    with factory.begin() as db:
        db.add(
            PurchaseAttachment(
                purchase_id=documents[0].id,
                name="original.pdf",
                mime="application/pdf",
                data=b"%PDF-1.7\noriginal",
            )
        )
    response = client.post("/api/accounting/delivery-periods/export", json=PERIOD)
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {
            "controlo.csv",
            "periodo.json",
            "anexos.csv",
            "pendencias.csv",
            "documentos-erp.csv",
        } <= set(archive.namelist())
        assert any(
            archive.read(name) == b"%PDF-1.7\noriginal"
            for name in archive.namelist()
            if name.endswith(".pdf")
        )
        manifest = json.loads(archive.read("periodo.json"))
        assert manifest["documents"] == 2 and manifest["start"] == "2026-10-01"
        assert len(json.loads(archive.read("documentos.json"))) == 2
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(AccountingPeriod)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.action == "periodo_contabilidade_encerrado")
            )
            == 1
        )
    assert (
        client.post(
            "/api/accounting/delivery-periods/reopen",
            json={**PERIOD, "reason": "Corrigir documentos"},
        ).status_code
        == 200
    )
    assert client.post("/api/accounting/delivery-periods/export", json=PERIOD).status_code == 409
    with factory() as db:
        assert db.get(Purchase, documents[0].id).amount_cents == 12300


def test_calendar_and_regime_are_validated_server_side(delivery):
    client, _, _ = delivery
    assert (
        client.post(
            "/api/accounting/delivery-periods/close",
            json={**PERIOD, "number": 11, "reason": "Ainda em curso"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/accounting/delivery-periods/close",
            json={**PERIOD, "vat_period": "trimestral", "number": 3, "reason": "Regime incorreto"},
        ).status_code
        == 409
    )
    assert client.post("/api/settings/fiscal", json={"vat_period": "trimestral"}).status_code == 200
    result = client.get("/api/accounting/delivery-periods?year=2026").json()
    assert len(result["periods"]) == 4
    assert (
        result["periods"][2]["start"] == "2026-07-01"
        and result["periods"][2]["end"] == "2026-09-30"
    )
    assert (
        client.post(
            "/api/accounting/delivery-periods/close",
            json={
                "vat_period": "trimestral",
                "year": 2026,
                "number": 3,
                "reason": "Trimestre conferido",
            },
        ).status_code
        == 200
    )


def test_operator_cannot_close_or_export_company_dossier(delivery):
    client, _, _ = delivery
    client.post("/api/auth/logout", json={})
    assert (
        client.post(
            "/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}
        ).status_code
        == 200
    )
    assert client.get("/api/accounting/delivery-periods?year=2026").status_code == 403
    assert (
        client.post(
            "/api/accounting/delivery-periods/close", json={**PERIOD, "reason": "Não autorizado"}
        ).status_code
        == 403
    )
    assert client.post("/api/accounting/delivery-periods/export", json=PERIOD).status_code == 403


def test_period_export_is_not_limited_to_200_documents(delivery):
    client, factory, documents = delivery
    with factory.begin() as db:
        for i in range(200):
            db.add(
                Purchase(
                    store_id=documents[0].store_id,
                    supplier_nif="500000000",
                    supplier_name="Fornecedor",
                    document=f"FT LOT/{i}",
                    document_date="2026-10-15",
                    amount_cents=100,
                )
            )
    assert (
        client.post(
            "/api/accounting/delivery-periods/close", json={**PERIOD, "reason": "Dossiê completo"}
        ).status_code
        == 200
    )
    response = client.post("/api/accounting/delivery-periods/export", json=PERIOD)
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert json.loads(archive.read("periodo.json"))["documents"] == 202


def test_closed_period_of_another_company_is_not_exportable(platform, monkeypatch):  # noqa: F811
    from test_platform import create, enter

    client, _ = platform
    monkeypatch.setattr(accounting_delivery, "local_today", lambda: date(2026, 11, 2))
    first = create(client, "delivery-first")
    second = create(client, "delivery-second")
    enter(client, first["slug"])
    assert client.post("/api/settings/fiscal", json={"vat_period": "mensal"}).status_code == 200
    assert (
        client.post(
            "/api/accounting/delivery-periods/close", json={**PERIOD, "reason": "Empresa inicial"}
        ).status_code
        == 200
    )
    enter(client, second["slug"])
    assert client.post("/api/settings/fiscal", json={"vat_period": "mensal"}).status_code == 200
    result = client.get("/api/accounting/delivery-periods?year=2026").json()
    assert result["periods"][9]["closed_at"] is None
    assert client.post("/api/accounting/delivery-periods/export", json=PERIOD).status_code == 409
