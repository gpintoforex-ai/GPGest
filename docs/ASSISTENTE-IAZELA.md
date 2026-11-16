# Assistente IAZELA

Perguntas em linguagem natural sobre os dados da empresa, respondidas pela Claude API (Anthropic). Disponível para **administradores e gestores** no ícone ✦ do cabeçalho, ao lado dos avisos.

O assistente **nunca grava sozinho**. Consulta os dados e pode **propor** a alteração do preço de venda de um artigo. A proposta aparece num cartão e só é gravada quando o utilizador carrega em **Confirmar** (ver [Ações com confirmação](#ações-com-confirmação)). Para as restantes ações (encomendar, receber, anexar), indica onde se faz na app.

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

## Privacidade, segurança e custos

- A pergunta e os resultados das ferramentas (por exemplo nomes de clientes, NIF, artigos e valores) são **enviados à Anthropic** para gerar a resposta. O painel avisa disto. Antes de ativar em produção, inclua a Anthropic como subcontratante na política de privacidade e no registo de tratamentos.
- Os textos vindos dos dados (descrições, nomes, documentos) são tratados como dados e nunca como instruções. Como nenhuma ferramenta grava e cada alteração exige a confirmação do utilizador, um texto malicioso nunca altera a empresa sozinho.
- Cada pergunta fica na **auditoria** (`assistente_pergunta`): a pergunta, as ferramentas usadas, o modelo e os tokens.
- Cada pergunta gasta tokens, pagos na conta Anthropic. As instruções fixas ficam em cache. As listas devolvidas pelas ferramentas estão limitadas a 50 linhas.
- A conversa vive só na janela aberta e não é guardada. «Nova conversa» limpa-a.

## Limites

- As vendas por artigo e por cliente dependem das linhas importadas. Vendas Wintouch sincronizadas sem linhas, ou SAF-T sem linhas, ficam fora dos rankings, e a resposta diz quantos documentos ficaram de fora.
- A previsão do IVA é indicativa e não substitui a declaração periódica nem o contabilista.
- A única ação confirmável é alterar o preço de venda. Outras (encomendas, publicação de artigos no site) ficam para fases seguintes, sempre com confirmação explícita.

## Verificação

- `backend/tests/test_assistant.py`: o cliente da Claude API é substituído por um falso que executa as ferramentas sobre dados de teste. Cobre lotes, vendas, clientes, IVA, documentos, erros e auditoria, e as propostas de preço (local e Wintouch simulada): nada é gravado antes de confirmar, a confirmação usa o pedido da ficha, uma proposta desatualizada é recusada e a permissão de alterar preços é respeitada. Nenhum pedido sai para a Anthropic.
- `scripts/test-assistant-ui.py`: painel, sugestões, envio, resposta formatada, fontes consultadas e estado sem chave, com APIs simuladas.
