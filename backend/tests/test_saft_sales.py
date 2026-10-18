import pytest
from app import db as database
from app.main import app
from app.models import (
    BusinessEntity,
    Movement,
    Register,
    SaftImport,
    SaftImportItem,
    WintouchSaleRecord,
)
from app.saft_parser import extract, validate
from saft_fixtures import customer, invoice, payment, receipt, saft, supplier
from sqlalchemy import func, select


@pytest.fixture
def saft_case(setup, monkeypatch):
    client, ids, factory = setup

    def override():
        with factory() as db:
            db.info["company"] = {
                "id": "fictitious",
                "nif": "500000001",
                "name": "Empresa fictícia",
            }
            yield db

    app.dependency_overrides[database.db_session] = override
    monkeypatch.setattr("app.saft_sales.policy", lambda db: {"mode": "local"})
    with factory() as db:
        store = db.get(Register, ids["registers"][0]).store_id
    return client, ids, factory, store


def upload(case, raw=None):
    response = case[0].post(
        "/api/saft/imports",
        data={"store_id": case[3]},
        files={"file": ("teste.xml", raw or saft(), "application/xml")},
    )
    assert response.status_code == 200, response.text
    row = case[0].get("/api/saft/imports/" + response.json()["id"]).json()
    return row


def confirm(case, row, conflicts=None):
    response = case[0].post(
        f"/api/saft/imports/{row['id']}/confirm",
        json={"confirmed": True, "revision": row["revision"], "conflicts": conflicts or {}},
    )
    assert response.status_code == 200, response.text
    return case[0].get("/api/saft/imports/" + row["id"]).json()


def analysis(case, start="2026-01-01", end="2026-01-31"):
    result = case[0].get(
        "/api/saft/analysis", params={"store_id": case[3], "start": start, "end": end}
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_official_xsd_and_incremental_extraction(tmp_path):
    path = tmp_path / "valid.xml"
    path.write_bytes(
        saft([invoice(), invoice("NC", 2), invoice("FR", 3, payments=payment())], [receipt()])
    )
    validate(path)
    records = list(extract(path))
    assert len(records) == 7
    assert any(
        value[2].get("type") == "NC" and value[2]["direction"] == -1
        for kind, value in records
        if kind == "record"
    )


def test_xsd_accepts_reference_to_last_customer_before_suppliers(tmp_path):
    # Exportação Eticadata: «CONSUMIDOR FINAL» é o último cliente, seguido de fornecedores.
    # Com lazy=2 e ~100 clientes, o xmlschema perdia esse cliente nas chaves e rejeitava
    # o ficheiro válido.
    path = tmp_path / "last-customer.xml"
    customers = (
        customer()
        + "".join(customer(f"C{i}", f"5{i:08d}") for i in range(2, 102))
        + customer("CONSUMIDOR FINAL", "999999990", "CONSUMIDOR FINAL")
        + supplier()
    )
    path.write_bytes(
        saft(
            [invoice(), invoice(number=2, customer_id="CONSUMIDOR FINAL")],
            customers=customers,
        )
    )
    validate(path)


def test_initial_repeat_overlapping_period_and_no_ledger_changes(saft_case):
    client, _, factory, _ = saft_case
    row = upload(saft_case)
    assert row["status"] == "ready", row
    assert row["summary"]["counts"]["new"] == 3
    completed = confirm(saft_case, row)
    assert completed["status"] == "completed", completed
    assert upload(saft_case)["id"] == row["id"]
    assert confirm(saft_case, row)["status"] == "completed"
    overlap = upload(saft_case, saft([invoice(), invoice(number=2)]))
    assert overlap["summary"]["counts"]["existing"] == 3
    assert confirm(saft_case, overlap)["summary"]["result"]["imported"] == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WintouchSaleRecord)) == 4
        assert db.scalar(select(func.count()).select_from(Movement)) == 0
    data = analysis(saft_case)
    assert data["billing_total_cents"] == 2460 and data["receipts_total_cents"] == 0
    assert any("indisponível" in item["reason"] for item in data["unavailable"])


def test_credit_notes_cancellations_and_explicit_payment_dates(saft_case):
    row = upload(
        saft_case,
        saft(
            [
                invoice("FR", 1, payments=payment(day="2026-01-20")),
                invoice("NC", 2, payments=payment(day="2026-01-21")),
                invoice(number=3, status="A"),
            ]
        ),
    )
    assert row["status"] == "ready", row
    assert row["summary"]["counts"]["cancelled"] == 1
    assert confirm(saft_case, row)["status"] == "completed"
    data = analysis(saft_case)
    assert data["billing_total_cents"] == 0 and data["receipts_total_cents"] == 0
    assert sorted(item["amount_cents"] for item in data["payments"]) == [-1230, 1230]
    assert not analysis(saft_case, "2026-01-10", "2026-01-10")["payments"]
    assert all(item["method"] is None for item in data["payments"])
    result = saft_case[0].post("/api/saft/payment-mapping", json={"mapping": {"NU": "numerario"}})
    assert result.status_code == 200
    assert analysis(saft_case)["receipts"][0]["method"] == "numerario"


def test_receipt_dedup_and_ambiguous_overlap(saft_case):
    row = upload(
        saft_case, saft([invoice("FR", payments=payment())], [receipt(reference="FR TESTE/1")])
    )
    assert row["status"] == "ready", row
    assert confirm(saft_case, row)["status"] == "completed"
    data = analysis(saft_case)
    assert data["receipts_total_cents"] == 1230 and len(data["payments"]) == 1
    assert data["payments"][0]["document"].startswith("RG")
    row = upload(
        saft_case,
        saft(
            [invoice("FR", payments=payment())],
            [
                receipt(reference="FR TESTE/1"),
                receipt(number=2, reference="FR TESTE/1", value="6.15"),
            ],
        ),
    )
    assert confirm(saft_case, row)["status"] == "completed"
    assert analysis(saft_case)["receipts_total_cents"] == 0
    assert analysis(saft_case)["warnings"]


def test_conflicts_keep_replace_versions_and_preserve_absent_documents(saft_case):
    first = confirm(saft_case, upload(saft_case, saft([invoice(), invoice(number=2)])))
    row = upload(saft_case, saft([invoice(status="A")]))
    assert row["summary"]["counts"]["conflict"] == 1
    items = saft_case[0].get(f"/api/saft/imports/{row['id']}/items?conflicts=true").json()["items"]
    assert len(items) == 1 and items[0]["previous"]["status"] == "N"
    assert (
        saft_case[0]
        .post(
            f"/api/saft/imports/{row['id']}/confirm",
            json={"confirmed": True, "revision": row["revision"], "conflicts": {}},
        )
        .status_code
        == 422
    )
    assert confirm(saft_case, row, {items[0]["id"]: "replace"})["status"] == "completed"
    old = saft_case[0].get(f"/api/saft/imports/{first['id']}/items?kind=invoice").json()["items"]
    assert all(item["data"]["status"] == "N" for item in old)
    assert analysis(saft_case)["billing_total_cents"] == 1230


def test_final_consumers_keep_source_identity_and_real_clients_reuse(saft_case):
    customers = (
        customer("C1", "999999990", "Pessoa A")
        + customer("C2", "999999990", "Pessoa B")
        + customer("C3", "500000002", "Empresa A")
        + customer("C4", "500000002", "Empresa A")
    )
    row = upload(saft_case, saft(customers=customers))
    assert confirm(saft_case, row)["status"] == "completed"
    with saft_case[2]() as db:
        assert db.scalar(select(func.count()).select_from(BusinessEntity)) == 3


@pytest.mark.parametrize(
    "raw,fragment",
    [
        (saft(nif="500000003"), "NIF"),
        (b"<invalid>", "Namespace"),
        (saft().replace(b"PT_1.04_01", b"PT_1.03_01"), "versão"),
        (
            saft().replace(b"<GrossTotal>12.30</GrossTotal>", b"<GrossTotal>-12.30</GrossTotal>"),
            "Estrutura",
        ),
        (saft().replace(b"<SourceID>TEST</SourceID>", b""), "Estrutura"),
        (
            b'<!DOCTYPE AuditFile [<!ENTITY x SYSTEM "file:///never-read">]>'
            + saft().split(b"?>", 1)[1],
            "XML",
        ),
    ],
)
def test_invalid_files_import_nothing(saft_case, raw, fragment):
    row = upload(saft_case, raw)
    assert row["status"] == "failed" and fragment.lower() in row["error"].lower(), row
    with saft_case[2]() as db:
        assert db.scalar(select(func.count()).select_from(SaftImportItem)) == 0
        assert db.scalar(select(func.count()).select_from(WintouchSaleRecord)) == 0


def test_atomic_failure_retry_and_permissions(saft_case, monkeypatch):
    from app import saft_sales

    client, _, factory, _ = saft_case
    row = upload(saft_case)
    original = saft_sales.customer

    def broken(*args):
        raise RuntimeError("Simulated failure")

    monkeypatch.setattr(saft_sales, "customer", broken)
    assert confirm(saft_case, row)["status"] == "failed"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WintouchSaleRecord)) == 0
    monkeypatch.setattr(saft_sales, "customer", original)
    refreshed = client.post(f"/api/saft/imports/{row['id']}/review", json={}).json()
    assert confirm(saft_case, refreshed)["status"] == "completed"
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert client.get("/api/saft/imports").status_code == 403
    assert (
        client.post(
            "/api/saft/imports",
            data={"store_id": saft_case[3]},
            files={"file": ("test.xml", saft())},
        ).status_code
        == 403
    )


def test_size_limit_duplicate_identities_and_cp1252(saft_case, monkeypatch):
    client, _, factory, _ = saft_case
    monkeypatch.setattr("app.saft_sales.MAX_BYTES", 64000)
    oversized = client.post(
        "/api/saft/imports",
        data={"store_id": saft_case[3]},
        files={"file": ("grande.xml", b"x" * 65000)},
    )
    assert oversized.status_code == 413
    duplicate = upload(saft_case, saft([invoice(), invoice()]))
    assert duplicate["status"] == "failed"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WintouchSaleRecord)) == 0
    cp1252 = (
        saft()
        .decode("utf-8")
        .replace('encoding="UTF-8"', 'encoding="Windows-1252"')
        .encode("cp1252")
    )
    row = upload(saft_case, cp1252)
    assert row["status"] == "ready", row
    assert confirm(saft_case, row)["status"] == "completed"


def test_reference_permissions_sheet_and_changed_nif(saft_case):
    from app.models import CashSession, User
    from app.saft_sales import confirm_worker

    client, ids, factory, store = saft_case
    row = upload(saft_case)
    # A changed NIF cannot use a previously validated file under a new source.
    with factory() as db:
        db.info["company"] = {"id": "fictitious", "nif": "500000003"}
        original = db.get(SaftImport, row["id"])
        original.status = "importing"
        db.commit()
    confirm_worker(
        factory.kw["bind"],
        {"company": {"id": "fictitious", "nif": "500000003"}},
        row["id"],
        {},
        lambda *args: None,
    )
    failed = client.get("/api/saft/imports/" + row["id"]).json()
    assert failed["status"] == "failed" and "NIF" in failed["error"]
    refreshed = client.post(f"/api/saft/imports/{row['id']}/review", json={}).json()
    assert confirm(saft_case, refreshed)["status"] == "completed"
    with factory.begin() as db:
        operator = db.scalar(select(User.id).where(User.role == "operador"))
        sheet = CashSession(
            register_id=ids["registers"][0],
            operator_id=operator,
            opening_cents=4000,
            sheet_day="2026-01-10",
        )
        other = CashSession(
            register_id=ids["registers"][2],
            operator_id=operator,
            opening_cents=4000,
            sheet_day="2026-01-10",
        )
        db.add_all([sheet, other])
        db.flush()
        a, b = sheet.id, other.id
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    assert client.get(f"/api/sessions/{a}/saft-reference").json()["billing_total_cents"] == 1230
    assert client.get(f"/api/sessions/{b}/saft-reference").status_code == 403


def test_incremental_multiple_upload_and_database_batches(saft_case):
    raw = saft([invoice(number=index) for index in range(1, 1001)])
    assert len(raw) > 1_000_000
    row = upload(saft_case, raw)
    assert row["status"] == "ready", row
    assert row["summary"]["counts"]["invoice"] == 1000
    result = confirm(saft_case, row)
    assert result["status"] == "completed", result
    assert result["summary"]["result"]["imported"] == 1002
    assert analysis(saft_case)["billing_total_cents"] == 1000 * 1230
