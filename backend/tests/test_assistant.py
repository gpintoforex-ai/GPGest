"""Assistente IAZELA: ferramentas de consulta com dados reais do esquema de teste e o cliente da Claude
API substituído por um falso (nenhum pedido sai para a Anthropic)."""

import json
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.models import CompanyFiscalSettings, Purchase, WintouchProduct, WintouchSaleRecord
from test_inventory import move, product, stock_case  # noqa: F401


class FakeClient:
    """Substitui anthropic.Anthropic: executa as ferramentas pedidas em `plan` e devolve uma resposta."""

    plan, results, calls = [], {}, []

    def __init__(self, **_):
        self.beta = SimpleNamespace(messages=SimpleNamespace(tool_runner=self.tool_runner))

    def tool_runner(self, **params):
        FakeClient.calls.append(params)
        tools = {tool.name: tool for tool in params["tools"]}

        def run():
            for name, args in FakeClient.plan:
                FakeClient.results[name] = json.loads(tools[name].call(args))
            yield SimpleNamespace(stop_reason="end_turn", model=params["model"],
                                  content=[SimpleNamespace(type="text", text="Resposta de teste.")],
                                  usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0))
        return run()


@pytest.fixture
def assistant(stock_case, monkeypatch):  # noqa: F811
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    FakeClient.plan, FakeClient.results, FakeClient.calls = [], {}, []
    return stock_case


def ask(case, plan, question="Pergunta de teste"):
    FakeClient.plan = plan
    response = case[0].post("/api/assistente/mensagens", json={"messages": [{"role": "user", "content": question}], "store_id": case[4]})
    assert response.status_code == 200, response.text
    return response.json()


def sale(case, day, customer, nif, lines, gross, tax=0, direction=1):
    with case[2].begin() as db:
        db.add(WintouchSaleRecord(
            enterprise_id="erp-teste", external_id="documento:" + uuid4().hex, store_id=case[4], kind="documento",
            document_date=day, data=json.dumps({
                "document_group": "vendas", "entity": customer, "entity_nif": nif, "amount_cents": gross,
                "net_cents": gross - tax, "tax_cents": tax, "direction": direction, "document": "FS " + uuid4().hex[:4],
                "lines": [{"product_id": p, "quantity": q, "net": n} for p, q, n in lines],
            })))


def test_without_api_key_the_assistant_explains_how_to_configure(stock_case, monkeypatch):  # noqa: F811
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = stock_case[0]
    state = c.get("/api/assistente/estado").json()
    assert state["available"] is False and "ANTHROPIC_API_KEY" in state["message"]
    refused = c.post("/api/assistente/mensagens", json={"messages": [{"role": "user", "content": "Olá"}]})
    assert refused.status_code == 409 and "ANTHROPIC_API_KEY" in refused.json()["detail"]


def test_answer_uses_the_model_with_fallback_and_records_the_question(assistant):
    result = ask(assistant, [], "Quanto vendi este mês?")
    assert result["reply"] == "Resposta de teste." and result["usage"]["input_tokens"] == 100
    params = FakeClient.calls[0]
    assert params["model"] == "claude-opus-5-5" and params["fallbacks"] == "default"
    assert params["output_config"] == {"effort": "medium"} and len(params["tools"]) == 8
    assert "português de Portugal" in params["system"][0]["text"] and "Hoje é" in params["system"][1]["text"]
    from app.models import Audit
    from sqlalchemy import select
    with assistant[2]() as db:
        audit = db.scalar(select(Audit).where(Audit.action == "assistente_pergunta"))
        assert json.loads(audit.detail)["pergunta"] == "Quanto vendi este mês?"
    # A última mensagem tem de ser a pergunta do utilizador.
    wrong = assistant[0].post("/api/assistente/mensagens", json={"messages": [{"role": "assistant", "content": "x"}]})
    assert wrong.status_code == 422


def test_expiring_lots_come_from_the_stock_lots_with_the_user_permissions(assistant):
    pid = product(assistant, name="Chá de validade curta", lots=True, expiry=True)
    move(assistant, pid, quantity="4", unit_cost="2", lot_number="L-CURTO", expiry=(date.today() + timedelta(days=20)).isoformat())
    later = product(assistant, name="Chá de validade longa", lots=True, expiry=True)
    move(assistant, later, quantity="4", unit_cost="2", lot_number="L-LONGO", expiry=(date.today() + timedelta(days=400)).isoformat())
    ask(assistant, [("artigos_a_terminar_validade", {"dias": 60})])
    found = FakeClient.results["artigos_a_terminar_validade"]
    assert [lot["artigo"] for lot in found["lotes"]] == ["Chá de validade curta"]
    assert found["lotes"][0]["lote"] == "L-CURTO" and found["lotes"][0]["dias"] == 20 and found["lotes"][0]["armazem"] == "Principal"


def test_sales_tools_compare_rank_customers_and_lapsed_clients(assistant):
    with assistant[2].begin() as db:
        db.add_all([WintouchProduct(enterprise_id="erp-teste", external_id="p1", code="MEL1", name="Mel biológico"),
                    WintouchProduct(enterprise_id="erp-teste", external_id="p2", code="CHA1", name="Chá verde")])
    today = date.today()
    recent, old = (today - timedelta(days=3)).isoformat(), (today - timedelta(days=200)).isoformat()
    sale(assistant, recent, "Maria Silva", "123456789", [("p1", "2", "10"), ("p2", "1", "4")], 1722, 322)
    sale(assistant, recent, "Consumidor final", "999999990", [("p2", "5", "20")], 2460, 460)
    sale(assistant, old, "João Antigo", "234567890", [("p1", "1", "5")], 615, 115)
    start, end = (today - timedelta(days=30)).isoformat(), today.isoformat()
    ask(assistant, [
        ("comparativo_vendas", {"inicio": start, "fim": end}),
        ("artigos_mais_vendidos", {"inicio": start, "fim": end, "limite": 5}),
        ("compras_do_cliente", {"cliente": "maria"}),
        ("compras_do_cliente", {"cliente": "234567890"}),
        ("clientes_sem_compras", {"dias": 90}),
    ])
    results = FakeClient.results
    assert results["comparativo_vendas"]["periodo"]["com_iva_eur"] == "41.82"
    assert results["comparativo_vendas"]["fonte"] == "vendas sincronizadas da Wintouch"
    ranked = results["artigos_mais_vendidos"]["artigos"]
    assert [(a["artigo"], a["quantidade"]) for a in ranked] == [("Chá verde", "6"), ("Mel biológico", "2")]
    assert ranked[0]["valor_sem_iva_eur"] == "24.00"
    # O último pedido de compras_do_cliente é o do NIF: o cliente antigo, com a sua compra.
    assert results["compras_do_cliente"]["cliente"] == "João Antigo" and results["compras_do_cliente"]["artigos"][0]["artigo"] == "Mel biológico"
    lapsed = results["clientes_sem_compras"]
    # Consumidor final nunca conta como cliente; a Maria comprou há 3 dias.
    assert [c["cliente"] for c in lapsed["clientes"]] == ["João Antigo"] and lapsed["clientes"][0]["dias_sem_comprar"] == 200


def test_customer_search_with_several_matches_asks_to_choose(assistant):
    day = (date.today() - timedelta(days=2)).isoformat()
    sale(assistant, day, "Ana Costa", "111111111", [], 100)
    sale(assistant, day, "Ana Martins", "222222222", [], 100)
    ask(assistant, [("compras_do_cliente", {"cliente": "ana"})])
    result = FakeClient.results["compras_do_cliente"]
    assert {c["nome"] for c in result["clientes_possiveis"]} == {"Ana Costa", "Ana Martins"}


def test_vat_forecast_and_documents_missing_for_accounting(assistant):
    with assistant[2].begin() as db:
        db.merge(CompanyFiscalSettings(id="default", vat_regime="normal", vat_period="mensal"))
        db.add(Purchase(store_id=assistant[4], supplier_nif="507475500", supplier_name="Fornecedor sem papel", document="FT A/1",
                        document_type="FT", document_date=date.today().isoformat(), amount_cents=12300, base_cents=10000,
                        tax_cents=2300, origin="efatura"))
    sale(assistant, date.today().isoformat(), "Maria Silva", "123456789", [], 12300, 2300)
    ask(assistant, [("previsao_iva", {}), ("documentos_em_falta_contabilidade", {})])
    vat = FakeClient.results["previsao_iva"]
    assert vat["periodicidade"] == "mensal" and vat["periodos"][0]["iva_liquidado_eur"] == "23.00"
    missing = FakeClient.results["documentos_em_falta_contabilidade"]
    assert missing["sem_copia"] == 1 and missing["em_falta"][0]["fornecedor"] == "Fornecedor sem papel"
    assert missing["em_falta"][0]["motivo"] == "Sem anexo nem papel registado"


def test_tool_errors_reach_the_model_instead_of_failing_the_request(assistant):
    ask(assistant, [("artigos_mais_vendidos", {"inicio": "ontem", "fim": "hoje"}),
                    ("produtos_a_encomendar", {"fornecedor": "Fornecedor que não existe"})])
    assert "Data inválida" in FakeClient.results["artigos_mais_vendidos"]["erro"]
    assert "não encontrado" in FakeClient.results["produtos_a_encomendar"]["erro"]


def test_account_without_credits_gets_a_clear_message(assistant, monkeypatch):
    import anthropic
    import httpx2

    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}}

    def refuse(self, **params):
        raise anthropic.BadRequestError("credit", response=httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")), body=body)

    monkeypatch.setattr(FakeClient, "tool_runner", refuse)
    response = assistant[0].post("/api/assistente/mensagens", json={"messages": [{"role": "user", "content": "Olá"}]})
    assert response.status_code == 402 and "não tem créditos" in response.json()["detail"]
