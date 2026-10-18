import csv
import io
import os
import smtplib
import zipfile
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import pytest
from app.models import (
    Audit,
    CompanyEmailSettings,
    EmailDispatch,
    Purchase,
    PurchaseAttachment,
    Register,
)
from cryptography.fernet import Fernet
from sqlalchemy import func, select


class FakeSMTP:
    def __init__(self):
        self.raws = []
        self.envelopes = []
        self.server_limit = None
        self.fail_at = None
        self.uncertain = False
        self.refused = False
        self.data_calls = 0
        self.connect_calls = 0

    def has_extn(self, name):
        return name == "size"

    def mail(self, sender, options=None):
        self.envelopes.append((sender, options))
        return 250, b"ok"

    def rcpt(self, recipient):
        return (550, b"refused") if self.refused else (250, b"ok")

    def rset(self):
        return 250, b"ok"

    def data(self, raw):
        self.data_calls += 1
        if self.fail_at == self.data_calls:
            if self.uncertain:
                raise smtplib.SMTPServerDisconnected("Fictitious disconnect")
            raise smtplib.SMTPDataError(550, b"Fictitious rejection")
        self.raws.append(raw)
        return 250, b"accepted"

    def quit(self):
        return 221, b"bye"

    def close(self):
        pass


@pytest.fixture
def email_case(setup, monkeypatch):
    client, ids, factory = setup
    monkeypatch.setenv("PLATFORM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    smtp = FakeSMTP()

    def connect(config, password):
        assert config["host"] == "smtp.example.test"
        assert password == "FictitiousSmtpSecret"
        smtp.connect_calls += 1
        return smtp, smtp.server_limit

    monkeypatch.setattr("app.email_transport.connect", connect)
    with factory.begin() as db:
        store = db.get(Register, ids["registers"][0]).store_id
        purchases = [
            Purchase(
                store_id=store,
                supplier_nif="500000001",
                supplier_name="Fornecedor fictício",
                document=f"TESTE/{index}",
                document_type=kind,
                document_date="2026-01-10",
                amount_cents=12300,
                tax_cents=2300,
                origin="efatura",
            )
            for index, kind in enumerate(["FT", "NC"])
        ]
        db.add_all(purchases)
        db.flush()
        native = PurchaseAttachment(
            purchase_id=purchases[0].id,
            name="original.pdf",
            mime="application/pdf",
            data=b"%PDF-1.7 original fictitious bytes",
        )
        db.add(native)
        db.flush()
        purchase_ids, attachment_id = [row.id for row in purchases], native.id
    result = client.post(
        "/api/settings/email",
        json={
            "enabled": True,
            "host": "smtp.example.test",
            "sender_email": "zela@example.test",
            "password": "FictitiousSmtpSecret",
            "username": "test",
            "accountant_name": "Gabinete fictício",
            "accountant_email": "contabilista@example.test",
        },
    )
    assert result.status_code == 200, result.text
    return client, factory, smtp, store, purchase_ids, attachment_id


def prepare(case, **changes):
    response = case[0].post(
        "/api/accounting/email/prepare",
        json={
            "request_key": str(uuid4()),
            "start": "2026-01-01",
            "end": "2026-01-31",
            "store_ids": [case[3]],
            "categories": ["compras", "notas_credito", "pendencias"],
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def preview(case, row, **changes):
    response = case[0].post(
        f"/api/emails/{row['id']}/preview",
        json={**row["draft"], "revision": row["revision"], **changes},
    )
    assert response.status_code == 200, response.text
    return response.json()


def send(case, row, **changes):
    return case[0].post(
        f"/api/emails/{row['id']}/send",
        json={
            "confirmed": True,
            "revision": row["revision"],
            "request_key": str(uuid4()),
            **changes,
        },
    )


def update_config(case, **changes):
    client = case[0]
    current = client.get("/api/settings/email").json()
    current.pop("has_password")
    current.pop("manual_only")
    response = client.post("/api/settings/email", json={**current, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def test_settings_secret_preservation_templates_and_periods(email_case):
    client, factory, smtp, *_ = email_case
    result = client.get("/api/settings/email")
    assert "FictitiousSmtpSecret" not in result.text and "password_ciphertext" not in result.text
    assert result.json()["has_password"] and result.json()["manual_only"]
    config = update_config(email_case, signature="Empresa fictícia", periodicity="trimestral")
    assert config["has_password"]
    periods = client.get("/api/accounting/email/periods?year=2026").json()
    assert len(periods["periods"]) == 4 and periods["periods"][0]["end"] == "2026-03-31"
    update_config(
        email_case, periodicity="personalizada", custom_period_note="Rever a cada seis semanas"
    )
    assert client.get("/api/accounting/email/periods?year=2026").json()["periods"] == []
    assert client.post("/api/settings/email/test", json={}).json()["ok"]
    assert not smtp.raws
    with factory() as db:
        assert (
            "FictitiousSmtpSecret"
            not in db.get(CompanyEmailSettings, "default").password_ciphertext
        )
        assert all("FictitiousSmtpSecret" not in row.detail for row in db.scalars(select(Audit)))
    assert (
        client.post(
            "/api/settings/email",
            json={"sender_email": "a@example.test\r\nBcc: victim@example.test"},
        ).status_code
        == 422
    )


def test_prepare_missing_original_snapshot_send_receipt_and_idempotency(email_case):
    client, factory, smtp, _, purchases, attachment_id = email_case
    key = str(uuid4())
    row = prepare(email_case, request_key=key)
    assert row["status"] == "prepared" and smtp.connect_calls == 0
    assert any(item.get("missing_attachment") for item in row["manifest"]["pending"])
    assert prepare(email_case, request_key=key)["id"] == row["id"]
    assert (
        client.post(
            f"/api/emails/{row['id']}/send",
            json={"revision": row["revision"], "request_key": str(uuid4()), "confirmed": False},
        ).status_code
        == 422
    )
    with factory.begin() as db:
        db.get(PurchaseAttachment, attachment_id).data = b"changed source after preparation"
    row = preview(email_case, row)
    result = send(email_case, row)
    assert result.status_code == 200, result.text
    accepted = result.json()
    assert accepted["status"] == "accepted" and not accepted["received_at"]
    assert len(smtp.raws) == 1
    message = BytesParser(policy=policy.default).parsebytes(smtp.raws[0])
    zipped = next(message.iter_attachments()).get_payload(decode=True)
    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        assert any(
            archive.read(name) == b"%PDF-1.7 original fictitious bytes"
            for name in archive.namelist()
            if name.endswith(".pdf")
        )
        assert any(name.endswith("indice-envio.csv") for name in archive.namelist())
        assert any(name.endswith("pendencias-envio.csv") for name in archive.namelist())
        assert len(archive.namelist()) == len(set(archive.namelist()))
        inventory_name = next(name for name in archive.namelist() if name.endswith("/anexos.csv"))
        prefix = inventory_name.rsplit("/", 1)[0]
        inventory = csv.reader(
            io.StringIO(archive.read(inventory_name).decode("utf-8-sig")), delimiter=";"
        )
        next(inventory)
        assert all(prefix + "/" + item[3] in archive.namelist() for item in inventory)
    assert send(email_case, row).json()["status"] == "accepted" and len(smtp.raws) == 1
    assert (
        client.post(
            f"/api/emails/{row['id']}/receipt",
            json={"confirmed": True, "note": "Confirmado por resposta do gabinete"},
        ).json()["status"]
        == "received"
    )
    with factory() as db:
        assert db.get(Purchase, purchases[0]).amount_cents == 12300
        assert db.scalar(select(func.count()).select_from(EmailDispatch)) == 1
    download = client.get(
        f"/api/emails/{row['id']}/files/compras/documentos/{purchases[0]}/{attachment_id}.pdf"
    )
    assert download.content == b"%PDF-1.7 original fictitious bytes"
    assert client.get(f"/api/emails/{row['id']}/files/../../not-present").status_code in (404, 422)


def test_complementary_selection_only_new_or_changed_versions(email_case):
    client, factory, _, _, _, attachment_id = email_case
    first = preview(email_case, prepare(email_case))
    assert send(email_case, first).json()["status"] == "accepted"
    again = prepare(email_case)
    original = next(
        item for item in again["manifest"]["files"] if item["key"].endswith(attachment_id + ".pdf")
    )
    assert original["previous_dispatch"] == first["id"]
    assert original["key"] not in again["draft"]["selected_keys"]
    with factory.begin() as db:
        db.get(PurchaseAttachment, attachment_id).data = b"%PDF-1.7 revised fictitious original"
    revised = prepare(email_case)
    file = next(item for item in revised["manifest"]["files"] if item["key"] == original["key"])
    assert not file["previous_dispatch"] and file["key"] in revised["draft"]["selected_keys"]


def large_dossier(case):
    _, factory, _, _, purchases, attachment_id = case
    with factory.begin() as db:
        db.get(PurchaseAttachment, attachment_id).data = os.urandom(24_000)
        db.add_all(
            [
                PurchaseAttachment(
                    purchase_id=purchases[0],
                    name="same-name.pdf",
                    mime="application/pdf",
                    data=os.urandom(24_000),
                )
                for _ in range(2)
            ]
        )
    return prepare(case, categories=["compras"])


def test_server_size_preflight_requires_split_ack_before_any_data(email_case):
    _, _, smtp, *_ = email_case
    row = preview(email_case, large_dossier(email_case))
    assert row["manifest"]["plan"]["messages"] == 1
    smtp.server_limit = 64_000
    result = send(email_case, row).json()
    assert result["status"] == "prepared" and not smtp.raws
    assert result["manifest"]["plan"]["requires_split_confirmation"]
    row = preview(email_case, result, allow_split=True)
    assert row["manifest"]["plan"]["messages"] > 1
    result = send(email_case, row).json()
    assert result["status"] == "accepted"
    assert all(len(raw) <= smtp.server_limit for raw in smtp.raws)
    assert all(
        "[" in str(BytesParser(policy=policy.default).parsebytes(raw)["Subject"])
        for raw in smtp.raws
    )


def test_partial_failure_retry_excludes_accepted_message_files(email_case):
    client, _, smtp, *_ = email_case
    update_config(email_case, max_message_bytes=64_000)
    row = preview(email_case, large_dossier(email_case), allow_split=True)
    smtp.fail_at = 2
    result = send(email_case, row).json()
    assert result["status"] == "failed" and not result["uncertain"]
    accepted_keys = {
        key for part in result["parts"] if part["status"] == "accepted" for key in part["file_keys"]
    }
    assert accepted_keys
    retried = client.post(
        f"/api/emails/{row['id']}/retry", json={"request_key": str(uuid4())}
    ).json()
    assert not accepted_keys.intersection(retried["draft"]["selected_keys"])
    smtp.fail_at = None
    retried = preview(email_case, retried)
    assert send(email_case, retried).json()["status"] == "accepted"


def test_uncertain_delivery_never_automatically_repeats(email_case):
    client, _, smtp, *_ = email_case
    row = preview(email_case, prepare(email_case))
    smtp.fail_at = 1
    smtp.uncertain = True
    result = send(email_case, row).json()
    assert result["status"] == "failed" and result["uncertain"]
    assert send(email_case, row).status_code == 409 and smtp.data_calls == 1
    assert (
        client.post(
            f"/api/emails/{row['id']}/retry", json={"request_key": str(uuid4())}
        ).status_code
        == 409
    )
    result = client.post(
        f"/api/emails/{row['id']}/retry",
        json={"request_key": str(uuid4()), "acknowledge_uncertain": True},
    )
    assert (
        result.status_code == 200 and result.json()["status"] == "prepared" and smtp.data_calls == 1
    )


def test_refused_recipient_sends_no_content_and_normal_retry_is_possible(email_case):
    client, _, smtp, *_ = email_case
    row = preview(email_case, prepare(email_case))
    smtp.refused = True
    assert send(email_case, row).json()["status"] == "failed"
    assert smtp.data_calls == 0
    retry = client.post(f"/api/emails/{row['id']}/retry", json={"request_key": str(uuid4())}).json()
    smtp.refused = False
    assert send(email_case, preview(email_case, retry)).json()["status"] == "accepted"


def test_individual_attachments_and_config_revision_guard(email_case):
    _, _, smtp, *_ = email_case
    row = preview(email_case, prepare(email_case), format="individual")
    update_config(email_case, sender_name="Outro remetente")
    assert send(email_case, row).status_code == 409 and not smtp.raws
    row = preview(email_case, row)
    assert send(email_case, row).json()["status"] == "accepted"
    message = BytesParser(policy=policy.default).parsebytes(smtp.raws[0])
    names = [item.get_filename() for item in message.iter_attachments()]
    assert len(names) == len(set(names)) and "indice-envio.csv" in names


def test_scope_permissions_and_missing_history(email_case):
    client, _, smtp, _, _, _ = email_case
    row = prepare(email_case)
    assert (
        client.post(
            "/api/accounting/email/prepare",
            json={
                "request_key": str(uuid4()),
                "start": "2026-01-01",
                "end": "2026-01-31",
                "store_ids": ["missing"],
            },
        ).status_code
        == 404
    )
    assert client.get("/api/emails/missing").status_code == 404
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "reader@test.pt", "password": "PasswordTest123!"})
    for path in ("/api/settings/email", "/api/emails", f"/api/emails/{row['id']}"):
        assert client.get(path).status_code == 403
    assert send(email_case, row).status_code == 403
    assert not smtp.raws


def test_single_oversized_file_and_interrupted_attempt_recovery(email_case):
    client, factory, smtp, _, purchases, attachment_id = email_case
    update_config(email_case, max_message_bytes=64_000)
    with factory.begin() as db:
        db.get(PurchaseAttachment, attachment_id).data = os.urandom(90_000)
    row = prepare(email_case, categories=["compras"])
    response = client.post(
        f"/api/emails/{row['id']}/preview", json={**row["draft"], "revision": row["revision"]}
    )
    assert response.status_code == 413 and not smtp.raws
    from app.models import now

    with factory.begin() as db:
        stored = db.get(EmailDispatch, row["id"])
        stored.status = "sending"
        stored.started_at = now()
    path = f"/api/emails/{row['id']}/recover"
    body = {"confirmed": True, "note": "Confirmação manual fictícia"}
    assert client.post(path, json=body).status_code == 409
    with factory.begin() as db:
        db.get(EmailDispatch, row["id"]).started_at = now() - timedelta(minutes=21)
    recovered = client.post(path, json=body).json()
    assert recovered["status"] == "failed" and recovered["uncertain"]


def test_store_and_document_category_filtering(email_case):
    _, factory, _, store, _, _ = email_case
    with factory.begin() as db:
        other_store = db.scalar(select(Register.store_id).where(Register.store_id != store))
        other = Purchase(
            store_id=other_store,
            supplier_nif="500000002",
            supplier_name="Outra loja",
            document="OUTRA/1",
            document_type="FT",
            document_date="2026-01-10",
            amount_cents=100,
            tax_cents=0,
            origin="efatura",
        )
        db.add(other)
        db.flush()
        db.add(
            PurchaseAttachment(
                purchase_id=other.id,
                name="privado.pdf",
                mime="application/pdf",
                data=b"Other store",
            )
        )
    row = prepare(email_case, categories=["compras"])
    assert all(file.get("document") != "OUTRA/1" for file in row["manifest"]["files"])
    assert not any(file.get("document") == "TESTE/1" for file in row["manifest"]["files"])
    assert all(item.get("supplier") != "Outra loja" for item in row["manifest"]["pending"])


def test_smtp_tls_and_size_negotiation_without_sending(monkeypatch):
    from app import email_transport

    calls = []

    class Connection:
        esmtp_features = {"size": "123456"}

        def __init__(self, host, port, **kwargs):
            assert host == "smtp.example.test" and kwargs["timeout"] == 15
            calls.append("connect")

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, context):
            assert context.check_hostname
            calls.append("tls")

        def login(self, username, password):
            assert username == "test" and password == "fictitious"
            calls.append("login")

    monkeypatch.setattr(smtplib, "SMTP", Connection)
    _, limit = email_transport.connect(
        {"host": "smtp.example.test", "port": 587, "security": "starttls", "username": "test"},
        "fictitious",
    )
    assert limit == 123456 and calls == ["connect", "ehlo", "tls", "ehlo", "login"]


def test_all_categories_and_cloud_failure_preserve_available_documents(email_case, monkeypatch):
    from app.accounting_email_files import CATEGORIES
    from app.email_delivery import frozen_files
    from app.models import (
        Attachment,
        BankAccount,
        BankDeposit,
        BankTransaction,
        CashSession,
        Category,
        DepositAttachment,
        DocumentPayment,
        DocumentScan,
        Movement,
        User,
        now,
    )
    from fastapi import HTTPException

    _, factory, _, store, _, _ = email_case
    with factory.begin() as db:
        user = db.scalar(select(User).where(User.role == "administrador"))
        register = db.scalar(select(Register).where(Register.store_id == store))
        account = BankAccount(
            store_id=store, name="Conta fictícia", bank="Banco fictício", iban_last4="1234"
        )
        db.add(account)
        db.flush()
        session = CashSession(
            register_id=register.id,
            operator_id=user.id,
            opening_cents=4000,
            sheet_day="2026-01-10",
            closed_at=now(),
            expected_cents=4000,
            counted_cents=4000,
        )
        db.add(session)
        db.flush()
        movement = Movement(
            session_id=session.id,
            user_id=user.id,
            category_id=db.scalar(select(Category.id)),
            kind="despesa",
            payment="numerario",
            amount_cents=500,
            direction=-1,
            occurred_at=session.opened_at.replace(year=2026, month=1, day=10),
            request_key=str(uuid4()),
        )
        db.add(movement)
        db.flush()
        deposit = BankDeposit(
            number="TEST-1",
            request_key=str(uuid4()),
            store_id=store,
            register_id=register.id,
            account_id=account.id,
            user_id=user.id,
            preparation_date="2026-01-10",
            cash_amount=10,
        )
        db.add(deposit)
        db.flush()
        db.add_all(
            [
                Attachment(
                    movement_id=movement.id,
                    name="comprovativo.pdf",
                    mime="application/pdf",
                    data=b"payment original",
                ),
                DepositAttachment(
                    deposit_id=deposit.id,
                    name="deposito.pdf",
                    mime="application/pdf",
                    data=b"deposit original",
                ),
                DocumentPayment(
                    store_id=store,
                    payment_date="2026-01-10",
                    amount_cents=500,
                    direction=-1,
                    request_key=str(uuid4()),
                ),
                BankTransaction(
                    account_id=account.id,
                    movement_date="2026-01-10",
                    value_date="2026-01-10",
                    description="Operação fictícia",
                    amount_cents=-500,
                    balance_cents=1000,
                    fingerprint="a" * 64,
                    source_account="TEST",
                    exported_at="2026-01-31",
                    source_position=1,
                ),
                DocumentScan(
                    request_key=str(uuid4()),
                    store_id=store,
                    user_id=user.id,
                    name="cloud.pdf",
                    digest="b" * 64,
                    size=10,
                    pages=1,
                    document_date="2026-01-10",
                    data='{"metadata": {"document": "SCAN/1"}}',
                    status="cloud_saved",
                    expires_at=now() + timedelta(days=1),
                ),
            ]
        )

    def unavailable(*args):
        raise HTTPException(503, "Simulated private Drive unavailable")

    monkeypatch.setattr("app.document_scanning.read_link", unavailable)
    row = prepare(email_case, categories=CATEGORIES)
    keys = [item["key"] for item in row["manifest"]["files"]]
    assert any(key.startswith("fechos/") for key in keys)
    assert any(key.startswith("extratos/") for key in keys)
    assert any(key.startswith("compras/") for key in keys)
    assert any("cloud indisponível" in item["reason"] for item in row["manifest"]["pending"])
    with factory() as db:
        files = frozen_files(db.get(EmailDispatch, row["id"]))
    assert b"payment original" in files.values() and b"deposit original" in files.values()
