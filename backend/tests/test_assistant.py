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

    plan, results, calls, generated, proposal = [], {}, [], [], {}

    def __init__(self, **_):
        self.beta = SimpleNamespace(messages=SimpleNamespace(tool_runner=self.tool_runner, create=self.create))

    def create(self, **params):
        """Geração do descritivo (serviço dos descritivos, chamado por gerar_descritivo)."""
        FakeClient.generated.append(params)
        return SimpleNamespace(stop_reason="end_turn", model=params["model"],
                               content=[SimpleNamespace(type="text", text=json.dumps(FakeClient.proposal))],
                               usage=SimpleNamespace(input_tokens=50, output_tokens=10))

    def tool_runner(self, **params):
        FakeClient.calls.append(params)
        # Ferramentas do servidor da Anthropic (pesquisa na internet) são dicionários e não correm aqui.
        tools = {tool.name: tool for tool in params["tools"] if not isinstance(tool, dict)}

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
    FakeClient.plan, FakeClient.results, FakeClient.calls, FakeClient.generated = [], {}, [], []
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
    assert params["output_config"] == {"effort": "medium"} and len([t for t in params["tools"] if not isinstance(t, dict)]) == 15
    assert {"type": "web_search_20250305", "name": "web_search", "max_uses": 3} in params["tools"]
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


def confirm(case, action):
    """O que o cartão faz ao carregar em «Confirmar»: o pedido da ficha do artigo, com a sessão do utilizador."""
    return case[0].post("/api" + action["request"]["path"], json=action["request"]["body"])


def test_price_change_is_only_proposed_and_saved_after_confirmation(assistant):
    from app.models import StockArticle

    article = product(assistant, name="Óleo de coco biológico 500 ml", sale_net="5")  # 6,15 € com IVA a 23%
    result = ask(assistant, [("procurar_artigos", {"texto": "coco 500"}),
                             ("propor_alteracao_preco", {"artigo_id": article, "preco_com_iva": "8,90 €"})],
                 "Põe o óleo de coco 500 ml a 8,90 €")
    found = FakeClient.results["procurar_artigos"]["artigos"]
    assert [a["id"] for a in found] == [article] and found[0]["preco_com_iva_eur"] == "6.15"
    assert "Ainda não foi gravada" in FakeClient.results["propor_alteracao_preco"]["estado"]
    [action] = result["actions"]
    assert action["type"] == "price" and action["origin"] == "local"
    assert action["before_gross"] == "6.15" and action["after_gross"] == "8.90"
    assert action["request"] == {"path": f"/stock/articles/{article}/price",
                                 "body": {"revision": 1, "sale_net": "7.235772"}}
    # A proposta não grava nada.
    with assistant[2]() as db:
        assert json.loads(db.get(StockArticle, article).data)["sale_net"] == "5"
    # Confirmado: o mesmo pedido da ficha grava o preço (com controlo de revisão e auditoria).
    saved = confirm(assistant, action)
    assert saved.status_code == 200, saved.text
    with assistant[2]() as db:
        assert json.loads(db.get(StockArticle, article).data)["sale_net"] == "7.235772"
    # Confirmar outra vez a mesma proposta (artigo já alterado) é recusado.
    assert confirm(assistant, action).status_code == 409
    from app.models import Audit
    from sqlalchemy import select
    with assistant[2]() as db:
        detail = json.loads(db.scalars(select(Audit).where(Audit.action == "assistente_pergunta")).first().detail)
        assert detail["acoes_propostas"] == [{"tipo": "price", "artigo": found[0]["codigo"], "de": "6.15", "para": "8.90"}]


def test_invalid_price_proposals_return_the_reason_to_the_model(assistant):
    article = product(assistant, name="Chá verde 100 g", sale_net="5")
    result = ask(assistant, [("propor_alteracao_preco", {"artigo_id": article, "preco_com_iva": "abc"})])
    assert "inválido" in FakeClient.results["propor_alteracao_preco"]["erro"] and result["actions"] == []
    ask(assistant, [("propor_alteracao_preco", {"artigo_id": article, "preco_com_iva": "6,15"})])
    assert "já tem o preço" in FakeClient.results["propor_alteracao_preco"]["erro"]
    ask(assistant, [("propor_alteracao_preco", {"artigo_id": article, "preco_com_iva": "0"})])
    assert "maior do que zero" in FakeClient.results["propor_alteracao_preco"]["erro"]
    ask(assistant, [("propor_alteracao_preco", {"artigo_id": "nao-existe", "preco_com_iva": "2"})])
    assert "procurar_artigos" in FakeClient.results["propor_alteracao_preco"]["erro"]
    ask(assistant, [("procurar_artigos", {"texto": "x"})])
    assert "2 caracteres" in FakeClient.results["procurar_artigos"]["erro"]


def test_price_proposal_respects_the_change_price_permission(assistant, monkeypatch):
    from app import inventory

    article = product(assistant, name="Mel de rosmaninho", sale_net="5")
    monkeypatch.setattr(inventory, "permissions", lambda user, config: {"change_price": False})
    result = ask(assistant, [("propor_alteracao_preco", {"artigo_id": article, "preco_com_iva": "9"})])
    assert "não permite" in FakeClient.results["propor_alteracao_preco"]["erro"] and result["actions"] == []


def test_erp_price_proposal_uses_the_sheet_request_with_the_previous_price(assistant, monkeypatch):
    from decimal import Decimal as D

    from test_article_price import wintouch
    from test_inventory import erp_product

    c, factory = assistant[0], assistant[2]
    erp_id = erp_product(assistant, "ERP-AGENTE", vat_rate=D(23), sale_price_vat=D("6.15"))
    article = c.post("/api/stock/erp-products", json={"erp_id": erp_id}).json()["id"]
    # Sem escrita de preços na Wintouch: o motivo segue para o modelo e não há cartão.
    result = ask(assistant, [("propor_alteracao_preco", {"artigo_id": article, "preco_com_iva": "7,38"})])
    assert FakeClient.results["propor_alteracao_preco"]["erro"] and result["actions"] == []
    with factory() as db:
        external = db.get(WintouchProduct, erp_id).external_id
    remote, sent = {"Row": 1, "PriceWithoutVAT": "5", "PriceWithVAT": "6.15"}, []
    wintouch(monkeypatch, external, remote, {"value": "apply"}, sent)
    [action] = ask(assistant, [("propor_alteracao_preco", {"artigo_id": article, "preco_com_iva": "7,38"})])["actions"]
    assert action["origin"] == "erp" and action["before_gross"] == "6.15" and action["after_gross"] == "7.38"
    assert action["request"]["body"] == {"gross": "7.38", "expected_gross": "6.1500"}
    assert sent == []  # nada foi enviado à Wintouch antes da confirmação
    confirmed = confirm(assistant, action)
    assert confirmed.status_code == 200 and confirmed.json()["state"] == "confirmed"
    with factory() as db:
        assert db.get(WintouchProduct, erp_id).sale_price_vat == D("7.38")


@pytest.mark.parametrize("text,expected", [("8,90", "8.90"), ("8.9 €", "8.90"), ("1 234,50", "1234.50"), ("1.234,50", "1234.50")])
def test_prices_written_by_the_model_are_read_without_floats(text, expected):
    from decimal import Decimal

    from app.assistant import parse_euros
    assert parse_euros(text) == Decimal(expected)


def test_charts_show_only_data_already_consulted(assistant):
    chart = {"titulo": "Faturação por mês, 2026", "tipo": "colunas", "unidade": "euros", "categorias": ["out", "nov"],
             "series": [{"nome": "2026", "valores": ["1234.56", 99.9]}, {"nome": "2025", "valores": [None, "10,5"]}],
             "nota": "Vendas Wintouch, com IVA"}
    # Sem consultar dados primeiro, o gráfico é recusado.
    alone = ask(assistant, [("mostrar_grafico", chart)])
    assert "Consulte primeiro" in FakeClient.results["mostrar_grafico"]["erro"] and alone["charts"] == []
    result = ask(assistant, [("artigos_a_terminar_validade", {"dias": 60}), ("mostrar_grafico", chart)])
    [shown] = result["charts"]
    # Euros em cêntimos inteiros (sem float); categorias e séries pela ordem dada.
    assert shown == {"title": "Faturação por mês, 2026", "kind": "colunas", "unit": "euros", "categories": ["out", "nov"],
                     "series": [{"name": "2026", "values": [123456, 9990]}, {"name": "2025", "values": [None, 1050]}],
                     "note": "Vendas Wintouch, com IVA"}
    assert "mostrar_grafico" in [t["name"] for t in result["tools"]]


@pytest.mark.parametrize("change,message", [
    ({"categorias": ["a"]}, "um valor por categoria"),
    ({"tipo": "barras"}, "uma série"),
    ({"series": [{"nome": "x", "valores": ["abc", 1]}]}, "inválido"),
    ({"series": [{"nome": "x", "valores": [None, None]}]}, "não tem valores"),
    ({"titulo": " "}, "título"),
])
def test_invalid_charts_return_the_reason_to_the_model(assistant, change, message):
    chart = {"titulo": "Teste", "tipo": "colunas", "unidade": "quantidade", "categorias": ["a", "b"],
             "series": [{"nome": "s1", "valores": [1, 2]}, {"nome": "s2", "valores": [3, 4]}], **change}
    result = ask(assistant, [("artigos_a_terminar_validade", {"dias": 60}), ("mostrar_grafico", chart)])
    assert message in FakeClient.results["mostrar_grafico"]["erro"] and result["charts"] == []
