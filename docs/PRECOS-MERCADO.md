# Preços de mercado (KuantoKusta e lojas)

Na ficha do artigo, em **Preços e rentabilidade**, a secção **Preços de mercado** compara o preço de venda com o que o artigo custa noutras lojas.

## Porque é manual

O KuantoKusta não tem API pública de consulta de preços. A API oficial é para lojas que vendem no marketplace (gerir as próprias ofertas), e o site bloqueia leituras automáticas. A leitura automática da página seria frágil e poderia violar os termos de uso. Por isso:

1. **Pesquisar no KuantoKusta** abre o site numa nova janela, pesquisado pelo primeiro código de barras do artigo (ou pela designação, se não houver).
2. O utilizador regista o que vê: origem (KuantoKusta ou outra loja/site), loja, **preço com IVA**, data da consulta (não pode ser futura), ligação HTTPS opcional e observações.

## Leitura automática na página da loja

Com a ligação da **página do produto na própria loja** (não a do KuantoKusta), o preço lê-se sem o escrever:

- **Ler preço da ligação** (no registo): lê a página e preenche o preço com IVA, a loja (nome do site ou domínio) e a
  data de hoje. Não grava nada: confirme e use «Registar preço».
- **Atualizar preços das lojas** (aparece quando há lojas com ligação): para o preço mais recente de cada loja, lê a
  página e regista uma **observação nova** com a data de hoje e a nota «Lido automaticamente da página da loja». O
  histórico mantém-se; repetir no mesmo dia sem mudança de preço não duplica. O resultado aparece por loja (preço
  anterior → novo, igual, ou o motivo de não ter sido lido). Fica na auditoria
  (`stock_article_market_price_refresh`).
- O preço vem dos dados de produto que as lojas publicam para os motores de pesquisa: `schema.org` Product/Offer em
  JSON-LD (incluindo `@graph` e `AggregateOffer`) ou as etiquetas `product:price:amount`, `og:price:amount` e
  `itemprop="price"`; só em euros. Sem esses dados, a página é recusada e o preço regista-se à mão.
- Não lidas: ligações do **KuantoKusta** (bloqueia leituras automáticas) e lojas sem ligação. Uma loja que recuse o
  pedido aparece com o erro.
- **robots.txt**: antes de ler, a IAZELA consulta o `robots.txt` do site (RFC 9309) e identifica-se como
  `IAZELA` (User-Agent `Mozilla/5.0 (compatible; IAZELA/1.0)`). Proibido para todos (`User-agent: *`) ou para a
  IAZELA (`User-agent: IAZELA`): não lê e pede o registo à mão (na atualização, a loja fica «por registar à mão», não
  como erro). Sem `robots.txt` (404 ou outro 4xx): lê. Site em erro ou inacessível (5xx, falha de rede): não lê.
- Só a pedido (botões); não há leitura agendada. Confirme as condições de utilização de cada loja: se proibirem a
  recolha automática, use para ela só o registo manual.
- Segurança: só HTTPS na porta normal, nunca a rede interna do servidor (também nos redirecionamentos), até 3 MB e
  15 s por página e 512 KB no `robots.txt` (`backend/app/web_fetch.py`, partilhado com as fotografias dos artigos).

## Comparação

- Conta o **preço mais recente de cada loja** (o nome da loja não distingue maiúsculas nem espaços). As observações anteriores ficam na lista como histórico.
- Resumo: o seu preço com IVA, o mais barato (e a loja), o preço médio, o mais caro, quantas lojas vendem abaixo do seu preço e a diferença para o mais barato. Aviso quando todas as lojas registadas vendem abaixo do seu preço.
- Em cada linha, a diferença para o seu preço (positiva: a loja vende mais caro) e, com permissão de consultar custos, **a sua margem a esse preço** (preço sem IVA, pela taxa do artigo, menos o custo médio).
- Nos artigos da Wintouch, usa o PVP **com IVA da linha de preço 1**. Se só existir o valor sem IVA, calcula o PVP quando a taxa de IVA é conhecida; sem estes dados, a diferença fica «Não calculável». O custo médio não é necessário para comparar preços.
- A diferença e a posição acompanham o preço apresentado na ficha, incluindo alterações ainda por guardar. A comparação não grava o novo PVP nem envia alterações à Wintouch. Por exemplo, mercado a 19,82 € e PVP de 26,95 € dão **−7,13 € (−26,5%)**, calculados em relação ao seu PVP.

## Regras

- Valores guardados em **cêntimos** (tabela `article_market_prices`, migração **0069**); preço maior que zero, com até duas casas decimais.
- Consultar: qualquer utilizador com acesso aos artigos. Registar e remover: perfis com permissão de alterar preços (`change_price`, por omissão administrador e gestor). Cada registo e remoção fica na auditoria (`stock_article_market_price`, `stock_article_market_price_removed`).
- Os preços de mercado são só informação: não mudam o preço de venda nem o stock. Remover um artigo remove os seus preços de mercado.

## API

- `GET /api/stock/articles/{id}/market-prices` — lista (mais recente primeiro), resumo, ligação de pesquisa e se o utilizador pode editar.
- `POST /api/stock/articles/{id}/market-prices` — `{source, store_name, price, observed_on, url, notes}`; devolve a lista atualizada.
- `DELETE /api/stock/articles/{id}/market-prices/{price_id}` — devolve a lista atualizada.
- `POST /api/stock/articles/{id}/market-prices/read` — `{url}`; devolve `{price, store_name, url}` sem gravar.
- `POST /api/stock/articles/{id}/market-prices/refresh` — lê as lojas com ligação; devolve a lista e `refresh` por loja.

## Testes

`backend/tests/test_market_prices.py`: preço mais recente por loja, resumo e posição, remoção e auditoria, validações (preço, loja, ligação HTTPS, data futura), permissões e a leitura automática (JSON-LD, meta, vírgula decimal, moeda, KuantoKusta recusado, atualização sem duplicar; rede simulada). Interface: `scripts/test-market-prices-ui.py` e `scripts/test-market-prices-read-ui.py`.
