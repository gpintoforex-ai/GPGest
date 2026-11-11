# Reformulação aprovada de navegação, apresentação e indicadores

Implementação local, 31/10/2026. A auditoria, a árvore proposta e o protótipo foram apresentados e aprovados antes destas alterações. O nome e os ativos IAZELA foram mantidos.

## Navegação e apresentação

O catálogo `navigationCatalog.ts` centraliza 11 áreas: Dashboard, Encomendas, Artigos, Entidades, Stocks, Compras, Vendas, Financeiro, Contabilidade, Relatórios e Configurações. Entidades reúne Clientes, Fornecedores e Condições comerciais. Financeiro mantém as funções anteriores acessíveis. Conserva os 60 nomes de páginas/aliases existentes e as regras de perfil e modo. Os 64 destinos incluem atalhos de tarefa para os componentes existentes; não são 64 módulos novos.

A pesquisa por Ctrl+K usa o mesmo catálogo. Cada destino contextual atualiza a página, a área e o filtro numa única entrada do histórico. Voltar, avançar e recarregar conservam a tarefa. O menu indica um único destino atual. A proteção existente de formulários e janelas mantém-se.

`Compras` passa a apresentar o módulo de compras existente; `Receitas e despesas` encaminha para os Mapas de gestão reais. Foram removidos os menus exteriores duplicados, mantendo os separadores das tarefas.

As superfícies, o foco, o texto, os controlos e a navegação usam os tokens semânticos comuns nos temas claro e escuro. Azul identifica ações principais e informação; verde, disponibilidade/sucesso; âmbar, pendências; vermelho, situações críticas; roxo, sugestões. `ActionButton` aceita variantes explícitas e continua a suportar os componentes antigos. Por preferência expressa do utilizador em 31/10/2026, a navegação usa verde turquesa escuro e recupera o raio de luz animado no contorno dos menus ativos, em hover e em foco. Com redução de movimento, o contorno fica estático. Os gráficos partilham CSS carregado pelo próprio componente e suportam quantidades sem as apresentar como euros.

## Encomendas e reposição

Em Encomendas encontram-se o Centro de Encomendas, Necessidades de compra, encomendas a fornecedores, receções, notificações e histórico. O simulador também está acessível em Compras. Reutilizam pedidos, reservas, simulações, receções, contactos, conferências e ledger existentes.

O mínimo e os portes grátis são objetivos independentes. Exemplo: mínimo 250 €, portes grátis a 300 €, compra líquida de 250 €: mínimo atingido, ainda faltam 50 € para portes grátis. Os portes grátis usam o líquido das mercadorias após descontos; a base contratual configurada para o mínimo mantém-se. Sem limiar configurado não se apresenta uma meta inventada.

As recomendações permitem 7, 15, 30, 60 e 90 dias ou 7–730 dias personalizados. A precedência é: período escolhido no ecrã → política do artigo → configuração do fornecedor → 90 dias por omissão. O cálculo continua a descontar reservas e compras pendentes e a respeitar cobertura, limites e validade. Uma seleção inválida não pede recomendações; respostas antigas não substituem uma seleção mais recente.

`ready` conserva a possibilidade de levantamento parcial. O novo `fully_ready` exige disponibilidade para todas as quantidades ainda em aberto. A interface identifica disponibilidade parcial; só pedidos completamente disponíveis entram na notificação automática. O contacto manual parcial e as restantes autorizações e proteções de repetição mantêm-se.

Não foi acrescentado envio automático de encomendas a fornecedores. A preparação, aprovação e envio explícitos existentes continuam obrigatórios. A escrita ERP de encomendas a fornecedores não foi presumida a partir da capacidade de escrever artigos ou faturas.

## Indicadores com origem verificável

Relatórios → Indicadores de encomendas, ou o separador Indicadores das encomendas, consulta `GET /api/order-cycle/analytics`. O Dashboard inclui um atalho para estes indicadores. A API é de leitura e exige perfil de gestão, autorização para custos e âmbito das lojas permitidas.

| Indicador | População e origem |
|---|---|
| Criados, pendentes, atrasados, distribuição por estado | Pedidos criados no período, pela data de registo em Lisboa; apresenta o estado atual, não uma reconstrução histórica. |
| Evolução | Criação pela data de registo e conclusão pela data do evento de entrega, identificadas como populações diferentes. |
| Procura por artigo | Quantidades pedidas no período, descontando cancelamentos atuais; referências provisórias são identificadas. |
| Rotatividade | Saídas físicas locais de venda, excluindo transferências e correções. Mostra os movimentos de origem, até 50 por artigo, com truncagem explícita. |
| Compras previstas | Rascunhos atuais recalculados pelo motor comercial existente, separados por moeda e fora do filtro de datas. Não são compras comprometidas. |
| Prazo médio de satisfação | Tempo entre registo e última entrega completa. Exclui cancelamentos e durações negativas. |
| Entregas dentro do prazo | Entregas completas comparadas com o prazo guardado no evento da entrega; apresenta numerador e denominador. |

As novas entregas guardam `due_date_at_delivery` no evento já existente. Entregas antigas sem esse dado ficam fora do denominador da pontualidade: o prazo atual do pedido não substitui um prazo histórico desconhecido. Sem amostra, a interface mostra ausência de dados. Não há migração nem preenchimento retroativo especulativo.

Os cartões abrem os pedidos correspondentes; a procura abre os pedidos de origem, a rotatividade abre os movimentos e os rascunhos abrem o simulador existente. As definições e os limites são visíveis no próprio ecrã.

## Validação e limites

- Compilação TypeScript/Vite e verificação dos componentes de interação.
- Testes de mínimos/portes, disponibilidade parcial, notificações, eventos históricos, populações, ausência de efeitos de escrita, permissões, isolamento entre lojas e precedência dos períodos.
- Regressões de pedidos, compras e escrita Wintouch; testes existentes do ciclo e do centro de abastecimento.
- Teste do catálogo: 11 áreas, todos os destinos antigos, 12 combinações de perfil/modo e correspondência dos atalhos com o destino atual.
- Navegador com APIs fictícias integralmente intercetadas, em 1440, 768 e 390 px, nos dois temas: pesquisa por teclado, histórico, recarregamento, detalhe de indicadores e movimentos, sem erros de execução, escrita real ou transbordo horizontal.
- Nove pares de tokens de texto/estado verificados em cada tema com contraste mínimo 4,5:1. Esta amostra não constitui certificação integral de todos os ecrãs antigos.

Os indicadores leem os pedidos autorizados e não têm ainda paginação/agregação dedicada para históricos muito grandes. O ensaio de interface não certifica serviços externos. Não foram enviados emails, feitas escritas ERP, alterada a base de produção ou publicada esta versão. As alterações anteriores da árvore de trabalho foram preservadas.


## Referência final do Centro de Encomendas

A referência fornecida em imagem foi aplicada ao módulo real, mantendo o nome e o logótipo IAZELA. `OrderCenterBoard.tsx` apresenta o percurso em cinco etapas, seis indicadores e oito blocos: pedidos pendentes, necessidades por fornecedor, artigos a encomendar, sugestões, simulador, receções, contactos e resumo. As funcionalidades detalhadas anteriores continuam acessíveis em «Detalhes de clientes, stock e cobertura». Os ícones e o efeito de luz turquesa da navegação foram preservados.

A pesquisa global do cabeçalho procura destinos e registos autorizados; o filtro próprio do painel pesquisa os pedidos, artigos, clientes e fornecedores do centro. A loja é a mesma do espaço de trabalho; mudar de loja respeita a proteção de alterações pendentes e invalida respostas anteriores. Os pedidos prontos distinguem disponibilidade integral e parcial. O painel respeita as autorizações existentes: operadores conservam a navegação dos pedidos sem consultar indicadores de custos nem recomendações de compra.

Selecionar sugestões só altera a composição após «Adicionar à simulação». Para um artigo existente, usa-se o maior valor entre a quantidade base e a proposta, preservando as atribuições aos clientes. Trocar de fornecedor limpa a seleção. O cálculo vem da pré-visualização existente; enquanto está pendente ou falha, não se afirma que mínimos/portes foram atingidos e não se permite avançar para essa simulação. Mínimo de compra e portes grátis continuam independentes.

O resumo conta artigos distintos e compras preparadas não canceladas no período selecionado; os montantes são separados por moeda. Entregas e pontualidade usam os eventos reais e os prazos históricos disponíveis, pela data em Lisboa. Não se inventa satisfação de clientes nem se substituem dados ausentes pelos números da imagem.

Verificação de interface adicional em 1536, 1011, 768 e 390 px, nos dois temas, com APIs fictícias intercetadas. Inclui pesquisa, filtros, mudança de fornecedor, propostas sem duplicação, preservação de atribuições, abertura do simulador e ausência de erros/transbordo. Os cenários adicionais verificam falha da pré-visualização, âmbito da loja e permissões de operador. As capturas de validação contêm dados fictícios e não representam uma empresa real.


A migração dos módulos e a assistência contextual dos lotes E–H estão descritas em `MIGRACAO-GLOBAL-FASES-E-H.md`, com as populações dos indicadores, percursos de revisão, preservação das confirmações e validação.
