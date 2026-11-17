# Assistente IAZELA

Perguntas em linguagem natural sobre os dados da empresa, respondidas pela Claude API (Anthropic). Disponível para **administradores e gestores** no ícone ✦ do cabeçalho, ao lado dos avisos.

O assistente **nunca grava sozinho**. Consulta os dados e pode **propor** alterações: o preço de venda, a fotografia e o rótulo da ficha, o descritivo para o site e a publicação na loja online. Cada proposta aparece num cartão e só é gravada quando o utilizador carrega em **Confirmar** (ver [Ações com confirmação](#ações-com-confirmação)). Para as restantes ações (encomendar, receber, anexar), indica onde se faz na app.

Para fazer as mesmas consultas no chat do próprio Claude (claude.ai e apps), ver o [conector MCP](CONECTOR-CLAUDE-MCP.md).

## Ativação

1. Crie uma chave em [console.anthropic.com](https://console.anthropic.com/) e escreva-a no `.env` do servidor: `ANTHROPIC_API_KEY=...` (o `.env` nunca vai para o Git).
2. Reinicie a API. Sem a chave, o painel explica como configurar e não envia nada.

O modelo é `claude-opus-5-5`, com esforço médio. Se o modelo recusar um pedido, a API responde com um modelo alternativo (`fallbacks: "default"`).

## O que sabe responder

| Pergunta | Ferramenta | Fonte (as mesmas leituras do ecrã) |
|---|---|---|
| Artigos a terminar a validade | `artigos_a_terminar_validade` | Lotes em stock (`/api/stock/lots`), armazéns autorizados |
| Produtos a encomendar | `produtos_a_encomendar` | Reposição do centro de abastecimento (`/api/order-cycle/recommendations`), com bonificações |
| Comparativo de vendas | `comparativo_vendas` | Faturação do Dashboard (SAF-T ou vendas Wintouch), por mês; por omissão compara com o mesmo período do ano anterior |
| Artigos mais vendidos | `artigos_mais_vendidos` | Linhas dos documentos de venda (devoluções subtraem) |
| Produtos que o cliente X já comprou | `compras_do_cliente` | Documentos de venda do cliente (nome ou NIF); com vários clientes parecidos, pede para escolher |
| Clientes que não compram há X dias | `clientes_sem_compras` | Última compra de cada cliente identificado; consumidor final (NIF genérico) não conta |
| Previsão do IVA a pagar | `previsao_iva` | Mapa de IVA dos mapas de gestão (IVA liquidado − IVA dedutível estimado), pelo período de IVA da empresa |
| Documentos em falta na contabilidade | `documentos_em_falta_contabilidade` | Conferência documental: não localizados, solicitados ao fornecedor ou sem anexo nem papel; e por entregar |
| Encontrar um artigo e o preço atual | `procurar_artigos` | Artigos ativos por nome (todas as palavras), código ou código de barras; preço com IVA da Wintouch ou da ficha local |
| Alterar o preço de venda | `propor_alteracao_preco` | Só proposta: ver [Ações com confirmação](#ações-com-confirmação) |
| Guardar a fotografia ou o rótulo na ficha | `propor_imagem` | Só proposta: a fotografia anexada ou uma imagem HTTPS (ver [Artigo para o site a partir de uma fotografia](#artigo-para-o-site-a-partir-de-uma-fotografia)) |
| Imagens da página de um produto | `imagens_da_pagina` | Imagens que a página declara (og:image, schema.org Product), com o leitor seguro da app e o robots.txt do site |
| Descritivo para o site | `gerar_descritivo` | [Descritivos do site](DESCRITIVOS-SITE-IA.md): proposta pendente, aprovada no cartão |
| Publicar na loja online | `propor_publicacao` | Só proposta: validada como no catálogo da loja online |
| Encontrar a página do produto | `web_search` | Pesquisa na internet da Anthropic (até 3 por pergunta) |
| Mostrar um gráfico | `mostrar_grafico` | Só desenha dados já devolvidos pelas outras ferramentas: ver [Gráficos](#gráficos) |

As ferramentas usam as permissões e as lojas do utilizador. As que têm ecrã próprio chamam esse endpoint GET no próprio processo, com a sessão do utilizador, para os números serem iguais aos do ecrã. As de vendas por cliente e por artigo leem os documentos de venda das lojas autorizadas, com os critérios do Dashboard. A loja escolhida no topo da app limita as consultas.

## Ações com confirmação

Exemplo: «Põe o óleo de coco 500 ml a 8,90 €» ou «Aumenta 5 % o chá verde».

1. O assistente encontra o artigo com `procurar_artigos`. Se houver mais de um possível, pergunta qual é.
2. `propor_alteracao_preco` valida a proposta no servidor com as regras da ficha: permissão de alterar preços, artigo ativo, preço maior do que zero com 2 casas decimais e diferente do atual. Nos artigos da Wintouch, a escrita de preços tem de estar disponível. **Não grava nada.**
3. O painel mostra um cartão com o preço atual e o novo (com IVA) e os botões **Confirmar** e **Cancelar**.
4. **Confirmar** faz, com a sessão do utilizador, o mesmo pedido que a ficha do artigo:
   - Artigo da Wintouch: `POST /api/stock/articles/{id}/erp-price` com o preço anterior (`expected_gross`). O preço é enviado à Wintouch e só fica gravado depois de lido de volta; a loja online recebe-o na sincronização seguinte. Se o preço mudou entretanto, o pedido é recusado.
   - Artigo local: `POST /api/stock/articles/{id}/price` com a revisão do artigo e o preço sem IVA calculado no servidor (`Decimal`, 6 casas). Se o artigo mudou entretanto, o pedido é recusado.
5. A auditoria regista a proposta (`assistente_pergunta`, campo `acoes_propostas`) e a gravação (`wintouch_price_write` ou `stock_article_price`).

O estado de cada proposta (confirmada, cancelada, falhada ou por confirmar) segue no histórico da conversa, para o assistente saber o que foi gravado. Cada resposta propõe no máximo 20 alterações, cada uma confirmada à parte.

## Artigo para o site a partir de uma fotografia

Botão da câmara no painel (no telemóvel abre a câmara; no computador, um ficheiro). A fotografia é reduzida no navegador (lado maior até 2000 px, JPEG) e segue com a mensagem. No servidor é normalizada como na ficha, os códigos de barras EAN/UPC visíveis são lidos (`zxing-cpp`) e indicados ao modelo, e a fotografia é enviada à Anthropic com a pergunta. Só a última mensagem leva a fotografia.

Exemplo: tirar uma fotografia à embalagem e pedir «Cria este artigo para o site». O assistente segue esta ordem, sempre com cartões:

1. Encontra o artigo (`procurar_artigos`, pelo código de barras ou pelo nome). Se não existir, diz que tem de ser criado na app.
2. **Imagem** (`propor_imagem`): a fotografia anexada, como fotografia do produto ou como rótulo. O cartão mostra a pré-visualização e as dimensões. **Confirmar** envia a fotografia pelo mesmo pedido da ficha (`PUT /api/stock/articles/{id}/images/{papel}`) e substitui a imagem atual desse tipo.
3. **Imagem melhor** (se pedida): o assistente pesquisa na internet a página do produto, de preferência no site da marca ou do fornecedor, lê as imagens que a página declara (`imagens_da_pagina`) e propõe 1 a 3. A imagem é descarregada e validada no servidor para a pré-visualização (o navegador não contacta o site); **Confirmar** guarda-a pela importação por endereço (`POST …/images/{papel}/from-url`). O cartão indica o site de origem: as imagens de terceiros podem ter direitos de autor, e as do fabricante ou do fornecedor, ou a fotografia própria, são as mais seguras.
4. **Descritivo** (`gerar_descritivo`): só depois de as imagens estarem confirmadas (com uma imagem por confirmar na mesma resposta, é recusado). Usa o serviço dos [descritivos do site](DESCRITIVOS-SITE-IA.md) e as suas regras; a proposta fica pendente e a ficha não muda. O cartão mostra as frases a confirmar (alegações de saúde, advertências) e a informação em falta, e deixa editar todos os campos. **Aprovar e gravar** usa o endpoint de aprovação, com controlo de revisão; **Descartar** descarta a proposta.
5. **Publicação** (`propor_publicacao`): valida como o catálogo da loja online (artigo ativo, à unidade, com preço e IVA; não publicado já) e avisa se falta descritivo ou fotografia. **Confirmar** cria a publicação (`POST /api/webshop/publications`) ou repõe uma retirada; a loja recebe o produto na sincronização seguinte. Conflitos de SKU ou EAN com produtos já existentes na loja são recusados pelo catálogo e aparecem no cartão.

Permissões: imagens e descritivo exigem a permissão de editar artigos; a publicação, gerir a loja online da loja da ligação.

Pesquisa na internet: está ativa por omissão e custa à parte na conta Anthropic (por pesquisa, além dos tokens). Para a desligar, `ASSISTENTE_PESQUISA_WEB=0` no `.env`. Se a organização da conta Anthropic não permitir a pesquisa na internet, a pergunta devolve o erro da Anthropic; ative-a na consola ou desligue-a aqui.

## Gráficos

Exemplo: «Mostra num gráfico a faturação dos últimos 12 meses comparada com o ano anterior».

- O assistente consulta primeiro os dados (por exemplo `comparativo_vendas`) e depois chama `mostrar_grafico`. Sem uma consulta antes, na mesma pergunta, o gráfico é recusado.
- Tipos: **colunas** (comparar meses ou categorias), **linha** (evolução) e **barras** (ranking horizontal). Colunas e linha têm até 2 séries (por exemplo, este ano e o anterior); barras têm uma. No máximo 36 categorias e 4 gráficos por resposta.
- Unidades: euros (convertidos no servidor para cêntimos inteiros, com `Decimal`), quantidade ou percentagem.
- O painel usa os componentes do dashboard (`DashboardCharts.tsx`): legenda, valores ao passar o rato, tocar ou navegar com o teclado, e **Ver tabela** com todos os valores.
- As instruções do modelo dizem que os valores vêm só dos resultados das ferramentas (somas e agrupamentos indicados na resposta). Para os números exatos, abra **Ver tabela** ou o ecrã de origem.

## Privacidade, segurança e custos

- A pergunta e os resultados das ferramentas (por exemplo nomes de clientes, NIF, artigos e valores) são **enviados à Anthropic** para gerar a resposta. O painel avisa disto. Antes de ativar em produção, inclua a Anthropic como subcontratante na política de privacidade e no registo de tratamentos.
- Os textos vindos dos dados (descrições, nomes, documentos) são tratados como dados e nunca como instruções. Como nenhuma ferramenta grava e cada alteração exige a confirmação do utilizador, um texto malicioso nunca altera a empresa sozinho.
- Cada pergunta fica na **auditoria** (`assistente_pergunta`): a pergunta, as ferramentas usadas, o modelo e os tokens.
- Cada pergunta gasta tokens, pagos na conta Anthropic. As instruções fixas ficam em cache. As listas devolvidas pelas ferramentas estão limitadas a 50 linhas.
- A conversa vive só na janela aberta e não é guardada. «Nova conversa» limpa-a.

## Limites

- As vendas por artigo e por cliente dependem das linhas importadas. Vendas Wintouch sincronizadas sem linhas, ou SAF-T sem linhas, ficam fora dos rankings, e a resposta diz quantos documentos ficaram de fora.
- A previsão do IVA é indicativa e não substitui a declaração periódica nem o contabilista.
- As ações confirmáveis são: preço de venda, imagens da ficha, descritivo do site e publicação na loja online. O assistente não cria artigos novos: um artigo que ainda não existe cria-se na app ou importa-se do ERP. Outras ações (encomendas, por exemplo) ficam para fases seguintes, sempre com confirmação explícita.

## Verificação

- `backend/tests/test_assistant.py`: o cliente da Claude API é substituído por um falso que executa as ferramentas sobre dados de teste. Cobre lotes, vendas, clientes, IVA, documentos, erros e auditoria, e as propostas de preço (local e Wintouch simulada): nada é gravado antes de confirmar, a confirmação usa o pedido da ficha, uma proposta desatualizada é recusada e a permissão de alterar preços é respeitada. Nenhum pedido sai para a Anthropic.
- `backend/tests/test_assistant_catalog.py`: fotografia anexada (código de barras lido, enviada ao modelo, guardada só ao confirmar), imagem por endereço com pré-visualização, imagens de uma página, descritivo gerado e aprovado com edição, publicação validada e criada só ao confirmar, e pesquisa na internet desligável. Sites, loja e Claude API simulados.
- `backend/tests/test_assistant.py` também cobre os gráficos: recusa sem consulta prévia, euros em cêntimos inteiros e validação (séries, categorias, valores).
- `scripts/test-assistant-ui.py`: painel, sugestões, envio, resposta formatada, fontes consultadas, estado sem chave, cartões de preço (confirmar grava, cancelar não grava, o estado segue no histórico), fotografia anexada, descritivo editável e publicação, e gráfico com tabela, com APIs simuladas.
