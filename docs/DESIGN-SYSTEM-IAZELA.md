# Design System IAZELA — fase B

Implementação local de 31/10/2026, baseada no Centro de Encomendas aprovado. Marca, logótipo, navegação turquesa e efeito de luz preservados. Não altera regras financeiras, contratos de API nem dados de negócio.

## Camadas e compatibilidade

`components/BusinessUI.css` é carregado depois das camadas globais existentes em `main.tsx`. Define componentes delimitados por classes `ui-*`, sem acrescentar seletores de tabelas/formulários genéricos. A única ponte de disposição é `.doc-overview-grid:has(> .ui-metric)`: os cartões do adaptador existente passam a ter contorno e espaçamento próprios, preservando os títulos, valores e descrições. As grelhas especializadas conservam os seus controlos.

`contrast.css` continua a ser a origem das superfícies, cores de texto e pares semânticos claros/escuros. O fundo claro é `#F6F8FC`, o texto principal `#14213D` e os campos permanecem brancos. Os componentes novos usam aliases `--ui-*`; não definem outra paleta concorrente. As variantes sólidas das etapas/filtros usam cores ajustadas à legibilidade de texto branco.

`BusinessUI.tsx` conserva os adaptadores e estados usados em toda a aplicação. `OverviewUI.tsx` separa os cartões interativos, filtros, progresso, etapas e sugestões usados pelos painéis, para acompanhar o carregamento do respetivo módulo. Não foram acrescentadas dependências.

As camadas antigas não foram removidas em bloco: continuam a servir os módulos ainda não migrados. As fases seguintes devem migrar os consumidores antes de retirar estilos que lhes pertençam. O catálogo não é uma rota da aplicação autenticada nem uma funcionalidade de negócio.

## Contratos dos componentes

| Componente | Contrato |
|---|---|
| `KpiCard` | Recebe valor, descrição, tom, ícone, estado e ação opcional. Zero permanece zero; valor ausente é «—». Carregamento, vazio, erro e restrição têm texto próprio e não exibem o valor fornecido como se estivesse disponível. A ação fica desativada nesses estados. |
| `MetricCard` | Adaptador compatível dos módulos existentes: conserva `label`, `value`, `description`, `tone`, `className` e a estrutura `h3/strong/p`. Usa a mesma linguagem visual. |
| `StatusBadge` | Texto e ícone opcional; tons neutral/info/success/warning/danger/suggestion/accent. Mantém a API anterior e acrescenta variantes de densidade, título e classes para adaptação. Não atribui estado por conta própria. |
| `ProgressGoal` | Recebe valor e objetivo na mesma unidade. Valor desconhecido é progresso indeterminado; objetivo ausente, zero ou inválido não é apresentado como uma percentagem calculada. A barra limita o preenchimento entre zero e o objetivo; o texto pode mostrar ultrapassagem real. Não calcula mínimos comerciais, moeda, IVA ou portes. |
| `QuickFilters` | Seleção controlada pelo módulo, `aria-pressed`, contagens incluindo zero e estados desativados. Setas/Home/End movem o foco entre opções disponíveis; Enter/Espaço ativam. A seleção tem fundo sólido e texto legível sem depender de um gradiente. |
| `ProcessSteps` | Lista ordenada com texto, número, tom e ações facultativas. O passo atual só existe quando o módulo o fornece; não se inventa o estado do processo a partir da posição. |
| `ModuleOverview` | Título, descrição, ações e conteúdo, sem carregamento de dados ou regras de negócio. O cabeçalho é isolado dos estilos antigos da barra superior. |
| `ContextSuggestion` | Conteúdo e evidências fornecidas pelo módulo. A origem pode ser expandida; a ação é uma callback explícita e fica bloqueada quando não há evidência. O componente não consulta serviços nem executa operações ao montar. A autorização final continua no fluxo/API. |
| `SyncStatus` | Recebe estado real, rótulo facultativo e data facultativa de última sincronização bem-sucedida. Não deduz sincronização pela origem ERP ou pela data de abertura do ecrã. |

| `DataTable` | Listagens com os dados todos no navegador: pesquisa sem acentos, ordenação por coluna, paginação de 10 a 100, filtros próprios do módulo (`toolbar`), colunas numéricas alinhadas à direita e exportação CSV (`exportName` + `exportValue` por coluna; separador «;», BOM UTF-8, proteção contra fórmulas) dos resultados filtrados de todas as páginas. A primeira consulta mostra «A carregar…» e uma falha mostra o erro, nunca «sem registos»; após uma falha de atualização as linhas anteriores continuam visíveis. Grelhas financeiras, editores de linhas e listas paginadas no servidor mantêm os seus controlos. |

`ActionButton`, `AppDialog`, `ManagedForm`, `DetailDrawer` e `ModuleTabs` mantêm os seus mecanismos existentes. As variantes explícitas de botões têm agora uma apresentação desativada comum. Não foi criada uma segunda biblioteca de janelas ou formulários.

## Integrações nesta fase

- Centro de Encomendas: cartões KPI, etiquetas, dois grupos de filtros e barras de mínimo reutilizam os componentes comuns. Quantidades, filtragem, atribuições a clientes, simulações e chamadas de API conservam os serviços anteriores.
- Catálogos: `ActiveBadge`, `OriginBadge` e `SyncBadge` delegam nos componentes comuns e conservam os rótulos e estados recebidos.
- Os módulos que já importam `MetricCard` e `StatusBadge` recebem a apresentação através dos adaptadores, sem uma reescrita dos seus processos.
- Etapas, resumo de módulo e sugestão com evidência ficam disponíveis para as fases seguintes e são exercitados pelo catálogo de validação. Não se apresenta esta base como a reformulação completa das fases C–H.

## Catálogo e validação

No Vite local: `/test-fixtures/business-ui.html`. Os exemplos estão assinalados como fictícios, não consultam a API e não fazem parte da entrada de compilação de produção. Importam os componentes e as camadas CSS reais; não são uma réplica em HTML separada.

`scripts/test-business-ui.py` verifica em 1536/768/390 px e nos dois temas: zero e estados indisponíveis, contraste das etiquetas/seleções/campos, seleção sem imagem de fundo, filtros por teclado, progresso desconhecido, revisão de evidência, pesquisa da grelha, campo branco, seleção de coluna e proteção de alterações/foco nos diálogos. Qualquer chamada à API durante o catálogo é intercetada e faz falhar o teste.

Regressões relevantes: `test-order-center-board-ui.py`, `test-supply-center-ui.py`, `test-approved-dashboard-ui.py`, `test-inventory-ui.py`, `test-tables-ui.py`, `test-customer-orders-ui.py`, além de compilação TypeScript/Vite, `check:ui` e catálogo de navegação. Os testes de interface usam dados e transportes substituídos; não realizam movimentos financeiros, envios ou escrita ERP reais.

## Reversão

Antes desta fase foi guardado no diretório de trabalho do Codex um arquivo do código de referência, incluindo os ficheiros não versionados da auditoria. O arquivo exclui credenciais, base de dados, anexos e dependências. A reversão deve aplicar-se aos ficheiros desta fase, sem repor globalmente a árvore e sem eliminar alterações anteriores. Nenhuma migração de base de dados é necessária para esta fase.

## Resumos operacionais e evidência (E–H)

`OperationsOverview` adapta os dados existentes a `ModuleOverview`, `KpiCard` e `ContextSuggestion`, com estados de carregamento/erro distintos de zero. A origem e o âmbito são explícitos. A hora opcional identifica a resposta recebida, não uma sincronização do conector. Não faz consultas ou escritas por si próprio. `MetricCard` admite conteúdo complementar para conservar explicações e ações dos cartões bancários. O percurso de etapas adapta-se a várias linhas, incluindo o ciclo completo de oito etapas no detalhe do pedido. Ver `MIGRACAO-GLOBAL-FASES-E-H.md`.

## Grelhas comuns — fase 1, lote 1

Passaram para o `DataTable` comum: auditoria do Arquivo digital, histórico da plataforma, acessos por PIN e últimas digitalizações rápidas, ficheiros no Google Drive (com filtro Por importar / Já importados), histórico de emails, últimas sincronizações ERP e histórico de importações SAF-T (com filtro de estado). Os dados, as chamadas à API, as ações por linha e as confirmações são os mesmos; acrescentam-se pesquisa, ordenação, paginação, exportação CSV onde é útil e estados de carregamento/erro distintos de vazio. Testes: `scripts/test-business-ui.py` (contrato da grelha em 1536/768/390 px, claro e escuro) e `scripts/test-common-grids-ui.py` (histórico SAF-T com API intercetada). Os editores de linhas, as conferências financeiras e as listas paginadas no servidor ficam para lotes seguintes ou mantêm controlos próprios.

## Grelhas comuns — fase 1, lote 2

Artigos e stock: relatório por artigo (mantém a ordem por prioridade; colunas de custo só com permissão), lotes com stock (filtro de validade na barra de filtros), inventários físicos e artigos sincronizados do ERP (filtro Por associar / Associados). Operadores / funcionários: a pesquisa própria passa a ser a da grelha; o filtro Tipo de registo mantém-se. Verificação de faturas e-Fatura: os filtros rápidos mantêm-se e a paginação própria passa para a grelha, que acrescenta pesquisa, ordenação e CSV. O seletor de linhas por página tem o mesmo nome acessível das Tabelas («Registos por página»).

Ficam com controlos próprios: movimentos de stock (paginados no servidor), pré-visualizações de importação, editores de associação e os passos do assistente e-Fatura. Testes: `test-inventory-ui.py`, `test-invoice-workflow-ui.py` e `test-employees-ui.py` (este último atualizado para a navegação por endereço e para a confirmação de inativação; a secção final de fecho de caixa ainda falha com 422 do servidor e está por investigar).
