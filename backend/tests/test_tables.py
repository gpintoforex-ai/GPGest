"""Comportamento comum das tabelas: operações locais, dependências, permissões e sincronização."""

import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from app.models import BusinessEntity, ErpLink, Register, User, WintouchEntity, WintouchSyncRun
from sqlalchemy import select
from test_operating_modes import apply, proposed, tenant
from test_platform import create, enter, platform  # noqa: F401


class FakeWintouch:
    """API Wintouch simulada: guarda os registos e todos os pedidos recebidos."""

    def __init__(self):
        self.data = {"payment_terms": {}, "warehouses": {}, "entities": {}, "items": {}, "addresses": {}}
        self.data["countries"] = {
            "pt": {"ID": "pt", "Code": "PT", "Name": "Portugal", "CurrencyID": "eur"},
            "es": {"ID": "es", "Code": "ES", "Name": "Espanha", "CurrencyID": "eur"},
        }
        self.requests = []
        self.fail_next = {}
        # Simula uma Wintouch que ignora as moradas enviadas dentro da entidade.
        self.drop_nested_addresses = False
        # Numeração automática: None reproduz a API desta empresa (null / HTTP 500).
        self.generated_codes = {"customer": None, "supplier": None}
        # Entidades com documentos na Wintouch (has_movements).
        self.with_movements = set()

    def addresses_of(self, entity_id):
        return [a for a in self.data["addresses"].values() if a.get("EntityID") == entity_id]

    def add(self, resource, **row):
        row.setdefault("Version", "v1")
        self.data[resource][row["ID"]] = row
        return row

    def handler(self, request):
        path = request.url.path.split("/api/v1/")[-1].strip("/")
        parts = path.split("/")
        resource, identifier = parts[0], parts[1] if len(parts) > 1 else None
        self.requests.append((request.method, path))
        failure = self.fail_next.pop((request.method, resource), None)
        if failure == "network":
            raise httpx.ConnectError("sem ligação")
        if failure:
            return httpx.Response(failure, json={"Message": "Recusado pelo ERP de teste"})
        store = self.data.setdefault(resource, {})
        if resource == "entities" and identifier in ("generate_code", "generate_code_supplier"):
            code = self.generated_codes["supplier" if identifier.endswith("supplier") else "customer"]
            if code is None and identifier.endswith("supplier"):
                return httpx.Response(500, json={"Message": "Ocorreu uma excepção interna: Nullable object must have a value."})
            return httpx.Response(200, json=code)
        if resource == "entities" and len(parts) == 3 and parts[2] == "has_movements":
            return httpx.Response(200, json=identifier in self.with_movements)
        if resource == "entities" and identifier == "by_vat":
            matches = [
                {"ID": e["ID"], "VATNumber": e.get("VATNumber"), "EntityName": e["Name"]}
                for e in store.values()
                if e.get("VATNumber") == parts[2]
            ]
            return httpx.Response(200, json=matches)
        if resource == "entities" and len(parts) == 3 and parts[2] == "addresses":
            return httpx.Response(200, json=[a for a in self.data["addresses"].values() if a.get("EntityID") == identifier])
        if request.method == "GET" and identifier is None:
            skip = int(request.url.params.get("skip", 0))
            rows = list(store.values())[skip : skip + 200]
            if resource == "entities" and request.url.params.get("embed"):
                # Como a Wintouch real com muitas entidades: a lista com embed é recusada.
                return httpx.Response(500, json={"Message": "O nº de registos é demasiado elevado."})
            return httpx.Response(200, json=rows)
        if request.method == "GET":
            if identifier not in store:
                return httpx.Response(404)
            row = dict(store[identifier])
            if resource == "entities" and "EntityAddresses" in request.url.params.get("embed", ""):
                row["EntityAddresses"] = [a for a in self.data["addresses"].values() if a.get("EntityID") == identifier]
            return httpx.Response(200, json=row)
        body = json.loads(request.content or b"{}", parse_float=Decimal)
        if request.method == "POST":
            body["Version"] = "v1"
            if resource == "entities":
                for address in body.pop("EntityAddresses", []) or []:
                    if not self.drop_nested_addresses:
                        self.data["addresses"][address["ID"]] = {**address, "EntityID": body["ID"]}
            store[body["ID"]] = json.loads(json.dumps(body, default=str))
            return httpx.Response(201, json=store[body["ID"]])
        if request.method == "PUT":
            if identifier not in store:
                return httpx.Response(404)
            if resource == "entities":
                # Como a Wintouch real: cada gravação leva exatamente uma morada principal.
                addresses = body.pop("EntityAddresses", None) or []
                if sum(1 for a in addresses if a.get("IsMainAddress")) != 1:
                    return httpx.Response(422, json={"Message": "A entidade deve ter uma e apenas uma morada principal"})
                for old in [k for k, a in self.data["addresses"].items() if a.get("EntityID") == identifier]:
                    del self.data["addresses"][old]
                for address in addresses:
                    self.data["addresses"][address["ID"]] = {**address, "EntityID": identifier}
            body["Version"] = "v" + str(int(store[identifier]["Version"][1:]) + 1)
            store[identifier] = json.loads(json.dumps(body, default=str))
            return httpx.Response(200, json=store[identifier])
        if request.method == "DELETE":
            store.pop(identifier, None)
            return httpx.Response(204)
        return httpx.Response(405)

    def writes(self):
        return [r for r in self.requests if r[0] != "GET"]


@pytest.fixture
def erp(monkeypatch):
    fake = FakeWintouch()
    real = httpx.Client

    def client(*args, **kwargs):
        kwargs.pop("follow_redirects", None)
        return real(transport=httpx.MockTransport(fake.handler), timeout=5)

    monkeypatch.setattr("app.wintouch_catalog.httpx.Client", client)
    return fake


def company_with_erp(client, slug, writes=True):
    company = create(client, slug)
    client.cookies.clear()
    boot = enter(client, company["slug"])
    apply(client, proposed(allow_writes=writes))
    return company, boot


def make_real(company):
    """Empresa em modo real: a gravação automática no ERP só acontece fora da demonstração."""
    from app import db as database
    from app.platform_models import Company

    with database.next_platform_session() as core:
        core.get(Company, company["id"]).mode = "real"
        core.commit()


def term(code="30DIAS", name="30 Dias", **updates):
    return {"values": {"code": code, "name": name, "term_type": "apos", "days": 30, "discount_percent": "0", **updates}}


def plan(client, table, direction, **extra):
    response = client.post(f"/api/tables/{table}/sync/plan", json={"direction": direction, "confirm_demo": True, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def execute(client, table, planned, keys=None, **extra):
    body = {
        "direction": planned["direction"],
        "ids": planned["ids"],
        "options": planned["options"],
        "token": planned["token"],
        "items": keys or [i["key"] for i in planned["items"]],
        "confirm_demo": True,
        **extra,
    }
    started = client.post(f"/api/tables/{table}/sync/execute", json=body)
    if started.status_code != 200:
        return started
    # A execução corre em segundo plano: segue o progresso até terminar.
    run = started.json()
    deadline = time.monotonic() + 60
    while run["status"] == "em_curso":
        assert time.monotonic() < deadline, run
        time.sleep(0.05)
        polled = client.get(f"/api/tables/{table}/sync/runs/{run['id']}")
        assert polled.status_code == 200, polled.text
        run = polled.json()
    return Finished(409 if run["status"] == "falhou" else 200, run)


class Finished:
    """Resultado final de uma sincronização, com a mesma forma de uma resposta HTTP."""

    def __init__(self, status_code, data):
        self.status_code, self.data, self.text = status_code, data, str(data)

    def json(self):
        return self.data


def records(client, table, **params):
    response = client.get(f"/api/tables/{table}/records", params={"status": "all", **params})
    assert response.status_code == 200, response.text
    return response.json()["rows"]


def test_local_operations_dependencies_and_inactive_rule(platform):  # noqa: F811
    client, _ = platform
    company = create(client, "tabelas-locais")
    client.cookies.clear()
    boot = enter(client, company["slug"])
    tables = {t["key"]: t for t in client.get("/api/tables").json()}
    assert {"customers", "suppliers", "banks", "warehouses", "categories", "registers", "payment_terms"} <= set(tables)
    assert tables["payment_terms"]["erp"]["integrated"] is False and tables["payment_terms"]["permissions"]["create"]

    created = client.post("/api/tables/payment_terms/records", json=term(code=" 30dias "))
    assert created.status_code == 200, created.text
    term_id = created.json()["id"]
    assert client.post("/api/tables/payment_terms/records", json=term()).status_code == 409
    assert client.post("/api/tables/payment_terms/records", json=term(code="X", days=None)).status_code == 422
    row = records(client, "payment_terms")[0]
    assert row["code"] == "30DIAS" and row["sync_state"] == "apenas_local" and row["origin"] == "local"

    # Filtros, pesquisa e paginação comuns.
    client.post("/api/tables/payment_terms/records", json=term(code="FIMMES", name="Fim do mês", term_type="fim_mes"))
    assert [r["code"] for r in records(client, "payment_terms", q="fim")] == ["FIMMES"]
    page = client.get("/api/tables/payment_terms/records", params={"size": 1, "sort": "code", "direction": "desc"}).json()
    assert page["pages"] == 2 and page["rows"][0]["code"] == "FIMMES"

    # Remoção bloqueada por dependências, com proposta de desativação.
    entity = {"name": "Fornecedor prazo", "is_customer": False, "is_supplier": True, "payment_term_id": term_id}
    supplier = client.post("/api/entities", json=entity).json()
    blocked = client.post(f"/api/tables/payment_terms/records/{term_id}/remove", json={"scope": "app", "confirmed": True})
    assert blocked.status_code == 409 and "desativ" in blocked.json()["detail"]
    details = client.get(f"/api/tables/payment_terms/records/{term_id}/details").json()
    assert details["dependencies"] == [{"label": "clientes/fornecedores que o usam", "count": 1}]

    # Inativo: preserva-se na ficha que já o tinha, mas não pode ser escolhido de novo.
    assert client.post(f"/api/tables/payment_terms/records/{term_id}/status", json={"active": False}).status_code == 200
    assert [r["code"] for r in records(client, "payment_terms", status="inactive")] == ["30DIAS"]
    assert client.post("/api/entities/" + supplier["id"], json=entity).status_code == 200
    other = client.post("/api/entities", json={**entity, "name": "Outro fornecedor"})
    assert other.status_code == 422 and "inativo" in other.json()["detail"]

    # Rubrica inativa não entra em novos movimentos previstos.
    category = client.post("/api/tables/categories/records", json={"values": {"name": "Rubrica antiga", "icon": "tag", "color": "#245fc5"}}).json()
    assert client.post(f"/api/tables/categories/records/{category['id']}/status", json={"active": False}).status_code == 200
    planned = client.post(
        "/api/planned-movements",
        json={"store_id": boot["stores"][0]["id"], "category_id": category["id"], "kind": "despesa", "description": "x", "amount": "10.00", "due_date": "2026-11-01"},
    )
    assert planned.status_code == 422

    # Caixa inativa não abre folhas de caixa; a remoção sem dependências é permitida.
    register = boot["registers"][0]["id"]
    assert client.post(f"/api/tables/registers/records/{register}/status", json={"active": False}).status_code == 200
    opened = client.post("/api/sessions", json={"register_id": register, "opening": "0.00"})
    assert opened.status_code == 409 and "inativa" in opened.json()["detail"]
    with tenant(company) as db:
        assert db.get(Register, register).active is False

    # Sem integração: a sincronização explica o motivo.
    refused = client.post("/api/tables/payment_terms/sync/plan", json={"direction": "import"})
    assert refused.status_code == 409 and "modo local" in refused.json()["detail"]
    removed = client.post("/api/tables/payment_terms/records/" + end_of_month_id(client) + "/remove", json={"scope": "app", "confirmed": True})
    assert removed.status_code == 200 and removed.json()["removed"]


def end_of_month_id(client):
    return next(r["id"] for r in records(client, "payment_terms") if r["code"] == "FIMMES")


def test_permissions_are_checked_on_the_server(platform):  # noqa: F811
    client, _ = platform
    company = create(client, "tabelas-perfis")
    client.cookies.clear()
    boot = enter(client, company["slug"])
    with tenant(company) as db:
        db.get(User, boot["user"]["id"]).role = "operador"
        db.commit()
    assert client.get("/api/tables/payment_terms/records").status_code == 200
    assert client.post("/api/tables/payment_terms/records", json=term()).status_code == 403
    tables = {t["key"]: t for t in client.get("/api/tables").json()}
    assert tables["payment_terms"]["permissions"]["create"] is False


def test_import_is_idempotent_links_by_code_and_reports_conflicts(platform, erp):  # noqa: F811
    client, _ = platform
    company, _ = company_with_erp(client, "tabelas-importar", writes=False)
    client.post("/api/tables/payment_terms/records", json=term(code="60DIAS", name="60 Dias", days=60))
    client.post("/api/tables/payment_terms/records", json=term(code="90DIAS", name="Noventa", days=90))
    client.post("/api/tables/payment_terms/records", json=term(code="PP", name="Pronto pagamento", days=0))
    erp.add("payment_terms", ID="t30", Code="30DIAS", Name="30 Dias", Days=30, TermType=0, Discount=None)
    erp.add("payment_terms", ID="t60", Code="60DIAS", Name="60 Dias", Days=60, TermType=0, Discount=None)
    erp.add("payment_terms", ID="t90", Code="90DIAS", Name="90 Dias", Days=90, TermType=0, Discount=None)
    erp.add("payment_terms", ID="tfm", Code="FIMMES", Name="Fim do mês", Days=None, TermType=3, Discount=None)
    erp.add("payment_terms", ID="tpp", Code="PRONTO", Name="Pronto pagamento", Days=0, TermType=0, Discount=None)

    planned = plan(client, "payment_terms", "import")
    actions = {i["label"]: i["action"] for i in planned["items"]}
    assert actions["30DIAS — 30 Dias"] == "criar_local"
    assert actions["60DIAS — 60 Dias"] == "ligar"  # mesmo código e mesmos dados
    assert actions["90DIAS — 90 Dias"] == "conflito"  # mesmo código, nome diferente
    # Mesma descrição sem código comum: nunca é ligado automaticamente.
    assert actions["PRONTO — Pronto pagamento"] == "conflito"
    assert planned["summary"]["criar"] == 2 and planned["summary"]["conflito"] == 2
    assert not erp.writes()

    result = execute(client, "payment_terms", planned)
    assert result.status_code == 200, result.text
    assert result.json()["failed"] == 0
    rows = {r["code"]: r for r in records(client, "payment_terms")}
    assert rows["30DIAS"]["origin"] == "erp" and rows["30DIAS"]["sync_state"] == "sincronizado"
    assert rows["60DIAS"]["erp_id"] == "t60" and rows["90DIAS"]["sync_state"] == "conflito"
    assert rows["90DIAS"]["description"] == "Noventa"  # conflito não substitui o local
    assert rows["PP"]["erp_id"] == ""
    assert not erp.writes()

    # Repetir não volta a criar nada.
    again = plan(client, "payment_terms", "import")
    assert again["summary"]["criar"] == 0
    assert len(records(client, "payment_terms")) == 5

    # Resolução explícita: prevalece o ERP.
    conflict = next(i for i in again["items"] if i["action"] == "conflito" and i["local_id"] == rows["90DIAS"]["id"])
    assert execute(client, "payment_terms", again, [conflict["key"]], resolutions={conflict["key"]: "erp"}).json()["ok"] == 1
    assert {r["code"]: r for r in records(client, "payment_terms")}["90DIAS"]["description"] == "90 Dias"

    # A ausência no ERP não elimina na IAZELA.
    erp.data["payment_terms"].pop("t30")
    absent = plan(client, "payment_terms", "import")
    missing = next(i for i in absent["items"] if i["local_id"] == rows["30DIAS"]["id"])
    assert missing["action"] == "ignorar" and "não é eliminado" in missing["reason"]
    execute(client, "payment_terms", absent)
    assert "30DIAS" in {r["code"] for r in records(client, "payment_terms")}

    # Pré-visualização desatualizada.
    stale = plan(client, "payment_terms", "import")
    erp.add("payment_terms", ID="tnew", Code="NOVO", Name="Novo", Days=10, TermType=0)
    assert execute(client, "payment_terms", stale).status_code == 409


def test_export_create_update_conflict_failure_and_recovery(platform, erp):  # noqa: F811
    client, _ = platform
    company, _ = company_with_erp(client, "tabelas-enviar", writes=False)
    local = client.post("/api/tables/payment_terms/records", json=term(code="45DIAS", name="45 Dias", days=45)).json()

    # Escrita não autorizada: o motivo aparece e nada é enviado.
    status = {t["key"]: t for t in client.get("/api/tables").json()}["payment_terms"]["erp"]
    assert status["operations"]["create"]["supported"] is False
    assert "não está autorizada" in status["operations"]["create"]["reason"]
    blocked = plan(client, "payment_terms", "export")
    assert blocked["items"][0]["action"] == "ignorar" and "não está autorizada" in blocked["items"][0]["reason"]

    apply(client, proposed(allow_writes=True))
    # O ERP recusa: fica erro, nada sincronizado, e pode repetir com o mesmo identificador.
    erp.fail_next[("POST", "payment_terms")] = 400
    planned = plan(client, "payment_terms", "export", ids=[local["id"]])
    assert [i["action"] for i in planned["items"]] == ["criar_erp"]
    outcome = execute(client, "payment_terms", planned).json()
    assert outcome["failed"] == 1 and outcome["retry"] == [local["id"]]
    row = records(client, "payment_terms")[0]
    assert row["sync_state"] == "erro" and "Recusado" in row["last_error"]
    with tenant(company) as db:
        first_id = db.scalar(select(ErpLink.external_id))
    retry = plan(client, "payment_terms", "export", ids=[local["id"]])
    assert execute(client, "payment_terms", retry).json()["ok"] == 1
    assert list(erp.data["payment_terms"]) == [first_id]
    row = records(client, "payment_terms")[0]
    assert row["sync_state"] == "sincronizado" and row["erp_code"] == "45DIAS" and row["last_sync_at"]

    # Alteração local: fica pendente até ser enviada e confirmada.
    client.post(f"/api/tables/payment_terms/records/{local['id']}", json=term(code="45DIAS", name="45 dias líquidos", days=45))
    assert records(client, "payment_terms")[0]["sync_state"] == "pendente"
    update = plan(client, "payment_terms", "export")
    assert [i["action"] for i in update["items"]] == ["atualizar_erp"]
    assert execute(client, "payment_terms", update).json()["ok"] == 1
    assert erp.data["payment_terms"][first_id]["Name"] == "45 dias líquidos"
    assert records(client, "payment_terms")[0]["sync_state"] == "sincronizado"

    # Alterado nos dois lados: conflito; com a regra «prevalece a IAZELA» é enviado.
    client.post(f"/api/tables/payment_terms/records/{local['id']}", json=term(code="45DIAS", name="Local", days=45))
    erp.data["payment_terms"][first_id].update(Name="Remoto", Version="v9")
    conflict = plan(client, "payment_terms", "sync")
    assert [i["action"] for i in conflict["items"]] == ["conflito"]
    assert client.post("/api/tables/payment_terms/settings", json={"conflict_policy": "local"}).status_code == 200
    resolved = plan(client, "payment_terms", "sync")
    assert [i["action"] for i in resolved["items"]] == ["atualizar_erp"]
    assert execute(client, "payment_terms", resolved).json()["ok"] == 1
    assert erp.data["payment_terms"][first_id]["Name"] == "Local"

    # Falha de ligação durante a criação: resultado incerto, verificado sem repetir o POST.
    # (Com a escrita autorizada, criar grava logo no ERP: a falha acontece na própria criação.)
    make_real(company)
    erp.fail_next[("POST", "payment_terms")] = "network"
    second = client.post("/api/tables/payment_terms/records", json=term(code="15DIAS", name="15 Dias", days=15)).json()
    assert second["erp"]["ok"] is False and "Ficou só na IAZELA" in second["erp"]["message"], second
    assert {r["code"]: r for r in records(client, "payment_terms")}["15DIAS"]["sync_state"] == "erro"
    # Simula que o pedido chegou ao ERP apesar da falha.
    with tenant(company) as db:
        sent_id = db.scalar(select(ErpLink.external_id).where(ErpLink.local_id == second["id"]))
    erp.add("payment_terms", ID=sent_id, Code="15DIAS", Name="15 Dias", Days=15, TermType=0, Discount=None)
    posts_before = sum(1 for r in erp.requests if r[0] == "POST")
    check = plan(client, "payment_terms", "export", ids=[second["id"]])
    assert [i["action"] for i in check["items"]] == ["verificar"]
    assert execute(client, "payment_terms", check).json()["ok"] == 1
    assert sum(1 for r in erp.requests if r[0] == "POST") == posts_before
    assert {r["code"]: r for r in records(client, "payment_terms")}["15DIAS"]["sync_state"] == "sincronizado"

    # Remover do ERP é explícito; remover de ambos respeita as dependências locais.
    removed = client.post(f"/api/tables/payment_terms/records/{second['id']}/remove", json={"scope": "erp", "confirmed": True, "confirm_demo": True})
    assert removed.status_code == 200, removed.text
    assert sent_id not in erp.data["payment_terms"]
    row = {r["code"]: r for r in records(client, "payment_terms")}["15DIAS"]
    assert row["sync_state"] == "apenas_local"
    client.post("/api/entities", json={"name": "Usa 45", "is_customer": True, "is_supplier": False, "payment_term_id": local["id"]})
    deletes_before = len([r for r in erp.requests if r[0] == "DELETE"])
    both = client.post(f"/api/tables/payment_terms/records/{local['id']}/remove", json={"scope": "both", "confirmed": True, "confirm_demo": True})
    assert both.status_code == 409
    assert len([r for r in erp.requests if r[0] == "DELETE"]) == deletes_before


def test_warehouses_import_with_store_and_deactivate_in_erp(platform, erp):  # noqa: F811
    client, _ = platform
    company, boot = company_with_erp(client, "tabelas-armazens", writes=True)
    erp.add("warehouses", ID="w1", Code="ARM1", Name="Armazém central", IsDisabled=None)
    planned = plan(client, "warehouses", "import")
    assert execute(client, "warehouses", planned).json()["ok"] == 1
    row = next(r for r in records(client, "warehouses") if r["code"] == "ARM1")
    assert row["values"]["store_id"] == boot["stores"][0]["id"] and row["origin"] == "erp"
    off = client.post(f"/api/tables/warehouses/records/{row['id']}/remove", json={"scope": "erp", "erp_action": "deactivate", "confirmed": True, "confirm_demo": True})
    assert off.status_code == 200, off.text
    assert erp.data["warehouses"]["w1"]["IsDisabled"] is True
    # Um armazém inativo não aceita novos movimentos.
    assert client.post(f"/api/tables/warehouses/records/{row['id']}/status", json={"active": False}).status_code == 200
    plan_after = plan(client, "warehouses", "sync")
    assert {i["action"] for i in plan_after["items"]} == {"ignorar"}


def test_entities_update_in_erp_respects_managed_fields(platform, erp):  # noqa: F811
    client, _ = platform
    company, _ = company_with_erp(client, "tabelas-entidades", writes=True)
    erp.add("payment_terms", ID="t30", Code="30DIAS", Name="30 Dias", Days=30, TermType=0, Discount=None)
    execute(client, "payment_terms", plan(client, "payment_terms", "import"))
    local_term = records(client, "payment_terms")[0]["id"]
    erp.add("entities", ID="e1", Code="F1", Name="Fornecedor ERP", VATNumber="500000000", IsSupplier=True, IsClient=False, Enabled=True)
    erp.data["addresses"]["a1"] = {"ID": "a1", "EntityID": "e1", "Address1": "Rua do ERP, 5", "Address2": "", "City": "Braga", "PostalCode": "4700-001", "Telephone": "253000000", "Email": None, "IsMainAddress": True}
    imported = plan(client, "suppliers", "import")
    assert [i["action"] for i in imported["items"]] == ["criar_local"]
    outcome = execute(client, "suppliers", imported).json()
    assert outcome["ok"] == 1, outcome
    with tenant(company) as db:
        mirror = db.scalar(select(WintouchEntity))
        key = "wt:" + mirror.id
    row = records(client, "suppliers")[0]
    assert row["id"] == key and row["origin"] == "erp" and row["sync_state"] == "sincronizado"
    # A morada principal da Wintouch é importada e mostrada.
    assert (row["values"]["address"], row["values"]["locality"], row["values"]["postal_code"], row["values"]["phone"]) == ("Rua do ERP, 5", "Braga", "4700-001", "253000000")

    # O nome é gerido pelo ERP; o prazo e o estado podem ser enviados.
    values = {k: row["values"][k] for k in ("tax_number", "name", "email", "phone", "address", "locality", "postal_code", "category_id", "is_customer", "is_supplier")}
    locked = client.post(f"/api/tables/suppliers/records/{key}", json={"values": {**values, "name": "Outro nome", "payment_term_id": None}})
    assert locked.status_code == 409
    saved = client.post(f"/api/tables/suppliers/records/{key}", json={"values": {**values, "payment_term_id": local_term}})
    assert saved.status_code == 200, saved.text
    assert records(client, "suppliers")[0]["sync_state"] == "pendente"
    sent = plan(client, "suppliers", "export")
    assert [i["action"] for i in sent["items"]] == ["atualizar_erp"]
    assert execute(client, "suppliers", sent).json()["ok"] == 1
    assert erp.data["entities"]["e1"]["SupplierPaymentTermID"] == "t30"
    assert erp.data["entities"]["e1"]["Name"] == "Fornecedor ERP"
    # A gravação reenviou a morada principal sem a alterar.
    assert erp.addresses_of("e1")[0]["Address1"] == "Rua do ERP, 5"

    # Morada alterada na IAZELA: fica pendente, é enviada e confirmada; a ficha mantém-na.
    row = records(client, "suppliers")[0]
    values = {k: row["values"][k] for k in ("tax_number", "name", "email", "phone", "address", "locality", "postal_code", "category_id", "payment_term_id", "is_customer", "is_supplier")}
    edited = client.post(f"/api/tables/suppliers/records/{key}", json={"values": {**values, "address": "Avenida Nova, 10", "email": "geral@erp.pt"}})
    assert edited.status_code == 200, edited.text
    row = records(client, "suppliers")[0]
    assert row["values"]["address"] == "Avenida Nova, 10" and row["sync_state"] == "pendente"
    sent = plan(client, "suppliers", "export")
    assert [c["field"] for c in sent["items"][0]["changes"]] == ["address", "email"]
    assert execute(client, "suppliers", sent).json()["ok"] == 1
    main = erp.addresses_of("e1")[0]
    assert main["Address1"] == "Avenida Nova, 10" and main["Email"] == "geral@erp.pt" and main["City"] == "Braga"
    row = records(client, "suppliers")[0]
    assert row["values"]["address"] == "Avenida Nova, 10" and row["sync_state"] == "sincronizado"
    assert records(client, "suppliers")[0]["sync_state"] == "sincronizado"
    # Criar e eliminar entidades no ERP estão disponíveis (a eliminação verifica os documentos na Wintouch).
    operations = {t["key"]: t for t in client.get("/api/tables").json()}["suppliers"]["erp"]["operations"]
    assert operations["create"]["supported"] is True and operations["delete"]["supported"] is True
    with tenant(company) as db:
        assert db.scalar(select(BusinessEntity).where(BusinessEntity.wintouch_id == mirror.id)).flags_local is False


def test_create_supplier_in_wintouch_with_code_country_address_and_recovery(platform, erp):  # noqa: F811
    client, _ = platform
    company, _ = company_with_erp(client, "tabelas-criar-entidades", writes=True)
    erp.add("payment_terms", ID="t30", Code="30DIAS", Name="30 Dias", Days=30, TermType=0, Discount=None)
    execute(client, "payment_terms", plan(client, "payment_terms", "import"))
    term_id = records(client, "payment_terms")[0]["id"]
    erp.add("entities", ID="e1", Code="F0104", Name="Já na Wintouch", VATNumber="500000001", IsSupplier=True, IsClient=False, Enabled=True)
    erp.add("entities", ID="e2", Code="C0007", Name="Cliente ERP", VATNumber="500000002", IsSupplier=False, IsClient=True, Enabled=True)
    supplier = {
        "name": "Fornecedor Novo, Lda", "tax_number": "509999990", "is_customer": False, "is_supplier": True,
        "address": "Rua Nova, 1", "locality": "Porto", "postal_code": "4000-001", "email": "geral@novo.pt",
        "phone": "220000000", "payment_term_id": term_id,
    }
    local = client.post("/api/entities", json=supplier).json()

    planned = plan(client, "suppliers", "export", ids=[local["id"]])
    assert [i["action"] for i in planned["items"]] == ["criar_erp"]
    outcome = execute(client, "suppliers", planned).json()
    assert outcome["ok"] == 1, outcome
    assert "F0105" in outcome["results"][0]["message"]
    created = next(e for e in erp.data["entities"].values() if e["Name"] == "Fornecedor Novo, Lda")
    assert created["Code"] == "F0105" and created["CountryID"] == "pt" and created["CurrencyID"] == "eur"
    assert created["SupplierPaymentTermID"] == "t30" and created["IsSupplier"] is True and created["IsClient"] is False
    address = next(a for a in erp.data["addresses"].values() if a["EntityID"] == created["ID"])
    assert address["Address1"] == "Rua Nova, 1" and address["City"] == "Porto" and address["IsMainAddress"] is True
    assert address["Email"] == "geral@novo.pt" and address["Telephone"] == "220000000"
    # A entidade da IAZELA passa a estar ligada à da Wintouch (sem duplicar a ficha).
    rows = {r["description"]: r for r in records(client, "suppliers")}
    assert rows["Fornecedor Novo, Lda"]["origin"] == "erp" and rows["Fornecedor Novo, Lda"]["erp_code"] == "F0105"
    assert rows["Fornecedor Novo, Lda"]["sync_state"] == "sincronizado"
    with tenant(company) as db:
        entity = db.scalar(select(BusinessEntity).where(BusinessEntity.name == "Fornecedor Novo, Lda"))
        assert entity.wintouch_id and db.get(WintouchEntity, entity.wintouch_id).external_id == created["ID"]
        assert not db.scalar(select(ErpLink).where(ErpLink.local_id.like("local:%")))

    # NIF já existente na Wintouch: não cria outra entidade.
    twin = client.post("/api/entities", json={**supplier, "name": "Duplicado", "tax_number": "500000001"}).json()
    blocked = plan(client, "suppliers", "export", ids=[twin["id"]])
    assert blocked["items"][0]["action"] == "ignorar" and "mesmo NIF" in blocked["items"][0]["reason"]

    # Falha de ligação na criação: verifica e conclui sem um segundo POST; morada criada à parte.
    erp.drop_nested_addresses = True
    third = client.post("/api/entities", json={**supplier, "name": "Terceiro, Lda", "tax_number": "509999991"}).json()
    erp.fail_next[("POST", "entities")] = "network"
    failed = execute(client, "suppliers", plan(client, "suppliers", "export", ids=[third["id"]])).json()
    assert failed["failed"] == 1
    assert {r["description"]: r for r in records(client, "suppliers")}["Terceiro, Lda"]["sync_state"] == "erro"
    with tenant(company) as db:
        reserved = db.scalar(select(ErpLink).where(ErpLink.local_id == third["id"]))
        assert reserved.external_code == "F0106" and reserved.state == "a_enviar"
        sent_id = reserved.external_id
    # O pedido tinha chegado à Wintouch apesar da falha.
    erp.add("entities", ID=sent_id, Code="F0106", Name="Terceiro, Lda", VATNumber="509999991", IsSupplier=True, IsClient=False, Enabled=True, SupplierPaymentTermID="t30")
    posts = sum(1 for r in erp.requests if r == ("POST", "entities"))
    check = plan(client, "suppliers", "export", ids=[third["id"]])
    assert [i["action"] for i in check["items"]] == ["verificar"]
    assert execute(client, "suppliers", check).json()["ok"] == 1
    assert sum(1 for r in erp.requests if r == ("POST", "entities")) == posts
    assert any(r == ("POST", "addresses") for r in erp.requests)
    assert any(a["EntityID"] == sent_id and a["IsMainAddress"] for a in erp.data["addresses"].values())
    assert len([e for e in erp.data["entities"].values() if e["Name"] == "Terceiro, Lda"]) == 1

    # Cliente: usa a série dos clientes (C…).
    customer = client.post("/api/entities", json={"name": "Cliente Novo", "tax_number": "", "is_customer": True, "is_supplier": False}).json()
    assert execute(client, "customers", plan(client, "customers", "export", ids=[customer["id"]])).json()["ok"] == 1
    assert next(e for e in erp.data["entities"].values() if e["Name"] == "Cliente Novo")["Code"] == "C0008"

    # Com a numeração automática ativa na Wintouch, o código proposto pela Wintouch é o usado.
    erp.generated_codes["supplier"] = "FORN-900"
    fourth = client.post("/api/entities", json={**supplier, "name": "Quarto, Lda", "tax_number": "509999992"}).json()
    assert execute(client, "suppliers", plan(client, "suppliers", "export", ids=[fourth["id"]])).json()["ok"] == 1
    assert next(e for e in erp.data["entities"].values() if e["Name"] == "Quarto, Lda")["Code"] == "FORN-900"
    # Um código proposto que já existe nunca é reutilizado.
    fifth = client.post("/api/entities", json={**supplier, "name": "Quinto, Lda", "tax_number": "509999993"}).json()
    assert execute(client, "suppliers", plan(client, "suppliers", "export", ids=[fifth["id"]])).json()["ok"] == 1
    assert next(e for e in erp.data["entities"].values() if e["Name"] == "Quinto, Lda")["Code"] == "F0107"


def test_remove_erp_entity_from_both_sides(platform, erp):  # noqa: F811
    client, _ = platform
    company, _ = company_with_erp(client, "tabelas-remover-entidades", writes=True)
    for identifier, name in (("e1", "Sem documentos"), ("e2", "Com documentos"), ("e3", "Fica local")):
        erp.add("entities", ID=identifier, Code="F" + identifier, Name=name, VATNumber="50000000" + identifier[-1], IsSupplier=True, IsClient=False, Enabled=True)
        erp.data["addresses"]["a" + identifier] = {"ID": "a" + identifier, "EntityID": identifier, "Address1": "Rua " + name, "City": "Lisboa", "PostalCode": "1000-001", "IsMainAddress": True}
    erp.with_movements.add("e2")
    execute(client, "suppliers", plan(client, "suppliers", "import"))
    rows = {r["description"]: r for r in records(client, "suppliers")}
    remove = lambda row, scope, action="delete": client.post(  # noqa: E731
        f"/api/tables/suppliers/records/{row['id']}/remove",
        json={"scope": scope, "erp_action": action, "confirmed": True, "confirm_demo": True},
    )

    # Só na IAZELA não é permitido para entidades do ERP: a mensagem indica «Remover de ambos».
    only_app = remove(rows["Sem documentos"], "app")
    assert only_app.status_code == 409 and "Remover de ambos" in only_app.json()["detail"]

    # Remover de ambos: eliminada na Wintouch e deixa de aparecer na IAZELA.
    both = remove(rows["Sem documentos"], "both")
    assert both.status_code == 200, both.text
    assert "e1" not in erp.data["entities"]
    assert "Sem documentos" not in {r["description"] for r in records(client, "suppliers")}

    # Com documentos na Wintouch: não é eliminada; a desativação continua disponível.
    blocked = remove(rows["Com documentos"], "both")
    assert blocked.status_code == 502 and "documentos" in blocked.json()["detail"] and "Desativar no ERP" in blocked.json()["detail"]
    assert "e2" in erp.data["entities"]
    off = remove(rows["Com documentos"], "erp", "deactivate")
    assert off.status_code == 200, off.text
    assert erp.data["entities"]["e2"]["Enabled"] is False and erp.addresses_of("e2")

    # Remover só do ERP: a ficha fica na IAZELA como «Apenas local», com a morada.
    keep = remove(rows["Fica local"], "erp")
    assert keep.status_code == 200, keep.text
    assert "e3" not in erp.data["entities"]
    local = next(r for r in records(client, "suppliers") if r["description"] == "Fica local")
    assert local["id"].startswith("local:") and local["sync_state"] == "apenas_local"
    assert local["values"]["address"] == "Rua Fica local"
    # Uma nova importação não volta a mostrar as entidades eliminadas.
    execute(client, "suppliers", plan(client, "suppliers", "import"))
    assert sorted(r["description"] for r in records(client, "suppliers")) == ["Com documentos", "Fica local"]


def test_new_records_are_saved_in_erp_and_sync_receives_for_managers(platform, erp):  # noqa: F811
    client, _ = platform
    company, boot = company_with_erp(client, "tabelas-gravar-ao-criar", writes=True)
    make_real(company)
    erp.add("entities", ID="e1", Code="F0009", Name="Existente", VATNumber="500000001", IsSupplier=True, IsClient=False, Enabled=True)
    erp.data["addresses"]["a1"] = {"ID": "a1", "EntityID": "e1", "Address1": "Rua A", "IsMainAddress": True}
    erp.add("payment_terms", ID="t30", Code="30DIAS", Name="30 Dias", Days=30, TermType=0, Discount=None)
    execute(client, "payment_terms", plan(client, "payment_terms", "import"))
    term_id = records(client, "payment_terms")[0]["id"]
    # Meio da Wintouch associado à «Transferência» da IAZELA (Configuração inicial).
    with tenant(company) as db:
        from app.models import WintouchPaymentMethod

        db.add(WintouchPaymentMethod(enterprise_id="demo-erp", external_id="mtb", name="Transferência bancária", local_payment="transferencia"))
        db.commit()
    rubric = boot["categories"][0]["id"]
    values = {
        "tax_number": "509999990", "name": "Gravado ao criar", "email": "", "phone": "", "address": "Rua B, 2",
        "locality": "Faro", "postal_code": "8000-001", "category_id": rubric, "payment_term_id": term_id,
        "payment_method_id": "transferencia", "is_customer": False, "is_supplier": True,
    }
    # Rubrica, prazo e meio de pagamento são obrigatórios ao criar.
    for missing, label in (("category_id", "Rubrica"), ("payment_term_id", "Prazo de pagamento"), ("payment_method_id", "Meio de pagamento")):
        refused = client.post("/api/tables/suppliers/records", json={"values": {**values, missing: None}})
        assert refused.status_code == 422 and label in refused.json()["detail"]
    created = client.post("/api/tables/suppliers/records", json={"values": values})
    assert created.status_code == 200, created.text
    assert created.json()["erp"]["ok"] is True and "F0010" in created.json()["erp"]["message"], created.json()
    new = next(e for e in erp.data["entities"].values() if e["Name"] == "Gravado ao criar")
    assert erp.addresses_of(new["ID"])[0]["City"] == "Faro"
    assert new["SupplierPaymentTermID"] == "t30" and new["SupplierPaymentMethodID"] == "mtb"
    row = next(r for r in records(client, "suppliers") if r["description"] == "Gravado ao criar")
    assert row["origin"] == "erp" and row["sync_state"] == "sincronizado" and row["erp_code"] == "F0010"
    assert row["values"]["payment_method_id"] == "transferencia" and row["labels"]["payment_method_id"] == "Transferência"
    # As opções do campo vêm da tabela «Meios de pagamento».
    described = client.get("/api/tables/suppliers").json()
    method_field = next(f for f in described["fields"] if f["name"] == "payment_method_id")
    assert {"value": "transferencia", "label": "Transferência", "active": True} in method_field["options"]

    # NIF que já existe na Wintouch: fica só na IAZELA com o motivo, sem criar duplicado.
    twin = client.post("/api/tables/suppliers/records", json={"values": {**values, "name": "Gémeo", "tax_number": "500000001"}}).json()
    assert twin["erp"]["ok"] is False and "mesmo NIF" in twin["erp"]["message"]
    assert len([e for e in erp.data["entities"].values() if e.get("VATNumber") == "500000001"]) == 1

    # Registos antigos só da IAZELA: propostos na sincronização, mas opcionais (desmarcados).
    client.post("/api/entities", json={**values, "name": "Antigo local", "tax_number": "509999997"})
    synced = plan(client, "suppliers", "sync")
    old = next(i for i in synced["items"] if i["label"] == "Antigo local")
    assert old["action"] == "criar_erp" and old["optional"] is True
    # O gémeo (mesmo NIF) é proposto para ligar à entidade da Wintouch, nunca para criar outra.
    assert not any(i["label"] == "Gémeo" and i["action"] == "criar_erp" for i in synced["items"])

    # Gestor: sincroniza para receber; gravar no ERP continua a exigir administrador.
    with tenant(company) as db:
        db.get(User, boot["user"]["id"]).role = "gestor"
        db.commit()
    erp.add("entities", ID="e3", Code="F0011", Name="Nova no ERP", VATNumber="500000003", IsSupplier=True, IsClient=False, Enabled=True)
    received = plan(client, "suppliers", "sync")
    keys = [i["key"] for i in received["items"] if i["action"] in ("criar_local", "atualizar_local", "criar_erp")]
    outcome = execute(client, "suppliers", received, keys).json()
    by_action = {r["action"]: r for r in outcome["results"]}
    assert by_action["criar_local"]["ok"] is True
    assert by_action["criar_erp"]["ok"] is False and "administrador" in by_action["criar_erp"]["message"]
    assert "Nova no ERP" in {r["description"] for r in records(client, "suppliers")}
    local_only = client.post("/api/tables/suppliers/records", json={"values": {**values, "name": "Do gestor", "tax_number": "509999998"}}).json()
    assert local_only["erp"]["ok"] is False and "administrador" in local_only["erp"]["message"]


def test_sync_runs_in_background_with_progress_and_releases_interrupted_runs(platform, erp):  # noqa: F811
    client, _ = platform
    company, _ = company_with_erp(client, "tabelas-progresso", writes=False)
    for code in ("30DIAS", "60DIAS", "90DIAS"):
        erp.add("payment_terms", ID="t" + code, Code=code, Name=code, Days=int(code[:2]), TermType=0, Discount=None)
    # Uma sincronização interrompida (servidor reiniciado) não bloqueia as seguintes para sempre.
    with tenant(company) as db:
        db.add(WintouchSyncRun(
            enterprise_id="demo-erp", resource="tabela:payment_terms", status="em_curso",
            started_at=datetime.now(UTC) - timedelta(hours=1),
        ))
        db.commit()
    assert client.get("/api/tables/payment_terms/sync/running").json() is None

    outcome = execute(client, "payment_terms", plan(client, "payment_terms", "import"))
    assert outcome.status_code == 200, outcome.text
    run = outcome.json()
    assert run["status"] == "concluida" and run["total"] == run["done"] == 3 and run["ok"] == 3
    assert {r["label"] for r in run["results"]} == {"30DIAS — 30DIAS", "60DIAS — 60DIAS", "90DIAS — 90DIAS"}
    with tenant(company) as db:
        statuses = sorted(r.status for r in db.scalars(select(WintouchSyncRun)))
    assert statuses == ["concluida", "falhou"]
    assert client.get("/api/tables/payment_terms/sync/runs/desconhecida").status_code == 404


def test_erp_code_is_unique_and_a_recreated_entity_updates_the_existing(platform, erp):  # noqa: F811
    client, _ = platform
    company, _ = company_with_erp(client, "tabelas-codigo-unico", writes=True)
    erp.add("entities", ID="e1", Code="F7", Name="Fornecedor Antigo", VATNumber="500000000", IsSupplier=True, IsClient=False, Enabled=True)
    assert execute(client, "suppliers", plan(client, "suppliers", "import")).json()["ok"] == 1
    with tenant(company) as db:
        original = db.scalar(select(WintouchEntity)).id
    # Na Wintouch a entidade foi recriada: mesmo código e NIF, outro identificador e outro nome.
    del erp.data["entities"]["e1"]
    erp.add("entities", ID="e2", Code="f7", Name="Fornecedor Renovado", VATNumber="PT500000000", IsSupplier=True, IsClient=False, Enabled=True)
    planned = plan(client, "suppliers", "import")
    keys = [i["key"] for i in planned["items"] if i["action"] == "criar_local"]
    assert execute(client, "suppliers", planned, keys=keys).json()["ok"] == 1
    with tenant(company) as db:
        active = list(db.scalars(select(WintouchEntity).where(WintouchEntity.erp_deleted_at.is_(None))))
        # Não nasce outra entidade: a existente fica com o identificador e os dados atuais do ERP.
        assert [(m.id, m.external_id, m.name) for m in active] == [(original, "e2", "Fornecedor Renovado")]
    assert [r["values"]["name"] for r in records(client, "suppliers")] == ["Fornecedor Renovado"]
    # O mesmo código em duas entidades que existem na Wintouch: recusado, sem duplicar na IAZELA.
    erp.add("entities", ID="e3", Code="F7", Name="Outro com o mesmo código", VATNumber="500000001", IsSupplier=True, IsClient=False, Enabled=True)
    planned = plan(client, "suppliers", "import")
    keys = [i["key"] for i in planned["items"] if i["action"] == "criar_local"]
    outcome = execute(client, "suppliers", planned, keys=keys).json()
    assert outcome["ok"] == 0 and "repetido na Wintouch" in str(outcome)
    with tenant(company) as db:
        assert len(list(db.scalars(select(WintouchEntity).where(WintouchEntity.erp_deleted_at.is_(None))))) == 1


def test_full_entity_sync_keeps_one_entity_per_erp_code(platform, erp):  # noqa: F811
    from app.models import BusinessEntity
    from app.wintouch_sync import reconcile_entity_codes

    client, _ = platform
    company, _ = company_with_erp(client, "sync-codigo-unico", writes=False)
    with tenant(company) as db:
        enterprise = "demo-erp"
        rows = {
            # F1: a existente (com ficha local) e a importada agora com outro identificador.
            "old1": WintouchEntity(enterprise_id=enterprise, external_id="x1", code="F1", name="Antigo", tax_number="500000000", is_supplier=True),
            "new1": WintouchEntity(enterprise_id=enterprise, external_id="y1", code="F1", name="Atual", tax_number="500000000", is_supplier=True),
            # F2: as duas já existiam (repetição anterior); só a «y2» continua na Wintouch.
            "old2": WintouchEntity(enterprise_id=enterprise, external_id="x2", code="F2", name="Dois", tax_number="500000001", is_supplier=True),
            "new2": WintouchEntity(enterprise_id=enterprise, external_id="y2", code="F2", name="Dois", tax_number="500000001", is_supplier=True),
            # F3: repetido na própria Wintouch (as duas vieram na leitura).
            "a3": WintouchEntity(enterprise_id=enterprise, external_id="a3", code="F3", name="Três A", is_supplier=True),
            "b3": WintouchEntity(enterprise_id=enterprise, external_id="b3", code="F3", name="Três B", is_supplier=True),
        }
        db.add_all(rows.values())
        db.flush()
        ids = {key: row.id for key, row in rows.items()}
        db.add_all([BusinessEntity(name="Antigo", wintouch_id=ids["old1"], is_supplier=True),
                    BusinessEntity(name="Dois", wintouch_id=ids["old2"], is_supplier=True)])
        db.flush()
        repeated = reconcile_entity_codes(db, enterprise, {"y1", "y2", "a3", "b3"}, {"y1"})
        db.commit()
    assert repeated == ["F3"]
    with tenant(company) as db:
        active = {m.code: m for m in db.scalars(select(WintouchEntity).where(WintouchEntity.erp_deleted_at.is_(None))) if m.code != "F3"}
        # F1: a entidade existente fica, com o identificador e o nome atuais da Wintouch.
        assert (active["F1"].id, active["F1"].external_id, active["F1"].name) == (ids["old1"], "y1", "Atual")
        # F2: a que já não existe no ERP é retirada e a ficha local passa para a atual.
        assert active["F2"].id == ids["new2"] and db.get(WintouchEntity, ids["old2"]).erp_deleted_at is not None
        assert db.scalar(select(BusinessEntity).where(BusinessEntity.name == "Dois")).wintouch_id == ids["new2"]
        assert len([m for m in db.scalars(select(WintouchEntity)) if m.code == "F3" and m.erp_deleted_at is None]) == 2


def test_setup_requires_syncing_erp_tables_and_counts_a_clean_comparison(platform, erp):  # noqa: F811
    from app.models import CompanySetup, now

    client, _ = platform
    company, _ = company_with_erp(client, "setup-tabelas-erp", writes=False)
    with tenant(company) as db:
        db.add(CompanySetup(id="default", step="summary", completed_at=now()))
        db.commit()
    status = client.get("/api/setup/status").json()
    tables = {t["key"]: t for t in status["table_sync"]}
    # Pela ordem das dependências: prazos antes das entidades.
    keys = list(tables)
    assert keys.index("payment_terms") < keys.index("suppliers") and all(not t["synced_at"] for t in tables.values())
    assert any(p.startswith("Sincronizar as tabelas com o ERP:") and "Prazos de pagamento" in p for p in status["pending"])
    # Prazos com alterações: só contam depois de executar.
    erp.add("payment_terms", ID="t30", Code="30DIAS", Name="30 Dias", Days=30, TermType=0, Discount=None)
    execute(client, "payment_terms", plan(client, "payment_terms", "sync"))
    for _ in range(50):
        synced = {t["key"]: t for t in client.get("/api/setup/status").json()["table_sync"]}
        if synced["payment_terms"]["synced_at"]:
            break
        time.sleep(0.1)
    assert synced["payment_terms"]["synced_at"]
    # Armazéns sem diferenças: a comparação basta para ficar sincronizada.
    assert all(i["action"] == "ignorar" for i in plan(client, "warehouses", "sync")["items"])
    synced = {t["key"]: t for t in client.get("/api/setup/status").json()["table_sync"]}
    assert synced["warehouses"]["synced_at"] and not synced["suppliers"]["synced_at"]
