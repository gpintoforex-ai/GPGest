# Preços de mercado (KuantoKusta)

Na ficha do artigo, o separador **Preços de mercado** compara o preço de venda com o que o artigo custa noutras lojas. É também acessível pelo botão **Comparar com o mercado (KuantoKusta)** em «Preços e rentabilidade».

## Porque é manual

O KuantoKusta não tem API pública de consulta de preços. A API oficial é para lojas que vendem no marketplace (gerir as próprias ofertas), e o site bloqueia leituras automáticas. A leitura automática da página seria frágil e poderia violar os termos de uso. Por isso:

1. **Pesquisar no KuantoKusta** abre o site numa nova janela, pesquisado pelo primeiro código de barras do artigo (ou pela designação, se não houver).
2. O utilizador regista o que vê: origem (KuantoKusta ou outra loja/site), loja, **preço com IVA**, data da consulta (não pode ser futura), ligação HTTPS opcional e observações.

## Comparação

- Conta o **preço mais recente de cada loja** (o nome da loja não distingue maiúsculas nem espaços). As observações anteriores ficam na lista como histórico.
- Resumo: o seu preço com IVA, o mais barato (e a loja), o preço médio, o mais caro, quantas lojas vendem abaixo do seu preço e a diferença para o mais barato. Aviso quando todas as lojas registadas vendem abaixo do seu preço.
- Em cada linha, a diferença para o seu preço (positiva: a loja vende mais caro) e, com permissão de consultar custos, **a sua margem a esse preço** (preço sem IVA, pela taxa do artigo, menos o custo médio).
- Nos artigos da Wintouch, o preço do ERP não tem a base de IVA identificada: o seu preço e a margem ficam «Não calculável»; os preços de mercado registam-se na mesma.

## Regras

- Valores guardados em **cêntimos** (tabela `article_market_prices`, migração **0069**); preço maior que zero, com até duas casas decimais.
- Consultar: qualquer utilizador com acesso aos artigos. Registar e remover: perfis com permissão de alterar preços (`change_price`, por omissão administrador e gestor). Cada registo e remoção fica na auditoria (`stock_article_market_price`, `stock_article_market_price_removed`).
- Os preços de mercado são só informação: não mudam o preço de venda nem o stock. Remover um artigo remove os seus preços de mercado.

## API

- `GET /api/stock/articles/{id}/market-prices` — lista (mais recente primeiro), resumo, ligação de pesquisa e se o utilizador pode editar.
- `POST /api/stock/articles/{id}/market-prices` — `{source, store_name, price, observed_on, url, notes}`; devolve a lista atualizada.
- `DELETE /api/stock/articles/{id}/market-prices/{price_id}` — devolve a lista atualizada.

## Testes

`backend/tests/test_market_prices.py`: preço mais recente por loja, resumo e posição, remoção e auditoria, validações (preço, loja, ligação HTTPS, data futura) e permissões.
