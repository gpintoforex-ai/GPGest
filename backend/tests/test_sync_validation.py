from decimal import Decimal

import pytest
from app.wintouch_sync import SyncError, normalize


def test_entity_roles_are_explicit_and_unknown_stays_unknown():
    values = normalize(
        "entities", {"ID": "e1", "Name": "Entidade", "IsClient": True, "IsSupplier": True}
    )
    assert values["is_customer"] and values["is_supplier"]
    assert "is_customer" not in normalize("entities", {"ID": "e2", "Name": "Outra"})
    with pytest.raises(SyncError):
        normalize("entities", {"ID": "e3", "Name": "Inválida", "IsSupplier": "false"})


def test_operator_cannot_manage_company_integrations(setup):
    client, _, _ = setup
    assert (
        client.post(
            "/api/auth/login", json={"email": "operator@test.pt", "password": "PasswordTest123!"}
        ).status_code
        == 200
    )
    assert client.get("/api/integrations/wintouch/sync-history").status_code == 403
    assert (
        client.post("/api/integrations/wintouch/sync", json={"resource": "products"}).status_code
        == 403
    )


def test_cost_precision_and_dates_do_not_guess_timezone():
    values = normalize(
        "products",
        {
            "ID": "p1",
            "Code": "P1",
            "Name": "Artigo",
            "LastCostGlobal": Decimal("12.3456"),
            "LastUpdate": "2026-10-11T12:00:00",
        },
    )
    assert values["last_cost"] == Decimal("12.3456")
    assert "source_updated_at" not in values
    with pytest.raises(SyncError):
        normalize(
            "products", {"ID": "p1", "Code": "P1", "Name": "Artigo", "LastCostGlobal": "12.34567"}
        )


def test_entity_payment_conditions_are_imported_per_role():
    values = normalize(
        "entities",
        {
            "ID": "e1",
            "Name": "Fornecedor",
            "IsSupplier": True,
            "PaymentTermID": None,
            "PaymentMethodID": None,
            "SupplierPaymentTermID": "t30",
            "SupplierPaymentMethodID": "mtb",
        },
    )
    assert values["supplier_payment_term_external_id"] == "t30"
    assert values["supplier_payment_method_external_id"] == "mtb"
    assert values["payment_term_external_id"] is None
    assert "payment_term_external_id" not in normalize("entities", {"ID": "e2", "Name": "Outra"})


def test_payment_catalogs_follow_entity_sync_and_failures_only_warn():
    import httpx
    from app.models import WintouchPaymentMethod, WintouchPaymentTerm
    from app.wintouch_sync import sync_payment_catalogs, term_days

    pages = {
        "/payment_terms": [
            {"ID": "t30", "Code": "30DIAS", "Name": "30 Dias", "Days": 30, "TermType": 0},
            {"ID": "tfm", "Code": "FIMDOMES", "Name": "Fim do mês", "Days": None, "TermType": 3},
        ],
        "/payment_methods": [{"ID": "mtb", "Code": "TRANSF", "Name": "Transferência bancária"}],
    }

    def handler(request):
        rows = pages.get(request.url.path.removeprefix("/api"), [])
        skip = int(request.url.params.get("skip", "0"))
        return httpx.Response(200, json=rows[skip:])

    added = []

    class FakeSession:
        def begin_nested(self):
            class Nested:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

            return Nested()

        def scalars(self, _query):
            return []

        def add(self, item):
            added.append(item)

        def flush(self):
            pass

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert sync_payment_catalogs(FakeSession(), client, "https://wt.test/api", "k", "ent", "run") == ""
    terms = {i.external_id: i for i in added if isinstance(i, WintouchPaymentTerm)}
    assert terms["t30"].days == 30 and terms["tfm"].days is None and terms["tfm"].term_type == "3"
    assert [i.name for i in added if isinstance(i, WintouchPaymentMethod)] == ["Transferência bancária"]

    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))) as client:
        warning = sync_payment_catalogs(FakeSession(), client, "https://wt.test/api", "k", "ent", "run")
    assert warning.startswith("Entidades importadas")
    with pytest.raises(SyncError):
        term_days(-1)


def test_erp_payment_view_separates_customer_and_supplier():
    from types import SimpleNamespace

    from app.entities import erp_payment_view

    imported = SimpleNamespace(
        payment_term_external_id=None,
        payment_method_external_id=None,
        supplier_payment_term_external_id="t30",
        supplier_payment_method_external_id="mtb",
    )
    view = erp_payment_view(
        imported,
        {"t30": SimpleNamespace(id="wt-30", external_id="t30", name="30 Dias", days=30)},
        {"mtb": "Transferência bancária"},
        {"t30": "local-30"},
    )
    assert view["erp_supplier_payment_term"] == "30 Dias"
    assert view["erp_supplier_payment_term_id"] == "local-30"
    assert view["erp_supplier_payment_days"] == 30
    assert view["erp_supplier_payment_method"] == "Transferência bancária"
    assert view["erp_customer_payment_term"] is None and view["erp_customer_payment_method"] is None


def test_addresses_read_apart_are_joined_to_their_entities():
    from app.wintouch_sync import addresses_by_entity, normalize, with_addresses

    grouped = addresses_by_entity([
        {"ID": "a1", "EntityID": "e1", "Address1": "Rua A", "City": "Braga", "PostalCode": "4700-001", "IsMainAddress": True},
        {"ID": "a2", "EntityID": "e1", "Address1": "Armazém", "IsMainAddress": False},
        {"ID": "a3", "EntityID": None},
    ])
    joined = with_addresses([{"ID": "e1", "Name": "Com morada"}, {"ID": "e2", "Name": "Sem moradas"}], grouped)
    assert len(joined[0]["EntityAddresses"]) == 2
    assert "EntityAddresses" not in joined[1]  # Não apaga a morada já guardada.
    values = normalize("entities", joined[0])
    assert values["address"] == "Rua A" and values["locality"] == "Braga" and values["postal_code"] == "4700-001"
    assert "address" not in normalize("entities", joined[1])
