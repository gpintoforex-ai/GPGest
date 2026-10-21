# Auditoria e reorganização da interface IAZELA

Data: 21 de outubro de 2026. Marca atual mantida: IAZELA (o pedido refere Zela).

## Âmbito e evidência

Análise estática concluída antes das alterações estruturais: entradas `main.tsx`, todos os destinos de `App.tsx`, administração `Platform.tsx`, páginas públicas, componentes e folhas de estilo. O [inventário completo](AUDITORIA-INTERFACE-INVENTARIO.json) regista os ficheiros, títulos, formulários, diálogos, chamadas API e condições de acesso encontradas. As expressões com interpolação são padrões de rotas, não uma especificação da API.

Esta auditoria identifica riscos pelo código; não equivale a uma certificação visual de todos os estados e perfis. A validação de navegador da implementação fica registada no final deste documento. Existem alterações locais ao Arquivo Digital anteriores a este trabalho; preservá-las, incluindo os seus destinos e permissões.

## Diagnóstico

- A navegação principal possui módulos e submenus, mas estes não aparecem na área de trabalho. O estado da página só existe em memória; voltar/avançar e ligações diretas não selecionam a tarefa.
- Mudar de página desmonta componentes e perde pesquisas, ordenações, páginas de tabelas e contexto. Já existem avisos para formulários alterados e operações em curso: reutilizá-los.
- Dashboard acumula configuração inicial, indicadores, vários gráficos, alertas, ações rápidas e operação de caixa. Liquidez e integrações acumulam processos distintos.
- Email junta SMTP, destinatários, modelos e histórico; o botão Guardar aparece apenas após todos os campos.
- Stock, reconciliação, faturação, mapas e arquivo já têm navegação interna. Preservar as divisões válidas, acrescentando consistência e URLs em vez de reconstruir as regras.
- `InteractionUI` já fornece ActionButton, Notice, AppDialog, ManagedForm, confirmações, foco e deteção de alterações. `RecordList` fornece pesquisa, ordenação, totais e paginação. `MetricCard` e os tokens de tema também são reutilizáveis.
- As tabelas têm wrappers de deslocação horizontal; convertê-las indiscriminadamente em cartões pode ocultar comparações e ações financeiras. Usar rótulos nas células em ecrãs pequenos onde não haja controlos de várias linhas/colunas; preservar tabelas especializadas.

## Matriz de páginas

Legenda: P1 = navegação e áreas extensas; P2 = uniformização; P3 = manter fluxo dedicado. Risco alto assinala formulários ou operações financeiras, sem autorizar mudanças de domínio. Componentes: MT = ModuleTabs; EW = espaço de trabalho por separadores; DL = DetailDrawer/AppDialog; RL = RecordList; UI = componentes existentes.

| Página / destino | Estrutura atual e problemas | Estrutura proposta / submenus | Reutilização | Risco | Prioridade |
|---|---|---|---|---|---|
| Dashboard | Configuração, filtros, indicadores, gráficos, alertas e caixa empilhados | Resumo; Evolução; Atenção; Ações rápidas; operação de caixa acessível | EW, MT, MetricCard, gráficos atuais | Médio | P1 |
| Folha de caixa local | Seleção, faturado, contagem, conferência e fecho | Manter processo do dia e seleções; navegação Tesouraria no topo | MT, CashSheetList, diálogos de contagem | Alto | P1 |
| Folha de caixa ERP | Importação, movimentos, fecho | Manter conferência e validação ERP; navegação Tesouraria | MT, CashSheet, SyncPanel | Alto | P1 |
| Movimentos reais | Registos, filtros, resumo por meio | Manter RL; destino próprio dentro de Tesouraria | MT, RL, AttachmentPanel | Alto | P1 |
| Movimentos previstos | Lista, previsão, liquidação | Manter lista e diálogo de liquidação | MT, RL, SettlementConfirmation | Alto | P1 |
| Depósitos | Lista e formulário/detalhe | Manter lista; ações curtas em diálogo existente | MT, AppDialog, filtros atuais | Alto | P1 |
| Previsão de liquidez | Saldos, previsão, exclusões, depósitos e compromissos empilhados | Resumo; Fluxos; Pendências; Compromissos; cenários e detalhes preservados | EW, DL, FilterBar | Alto | P1 |
| Importar SAF-T de vendas | Importação, histórico, análise e detalhe | Importar; Histórico; Análise; detalhes em diálogo | EW, MT, AppDialog | Alto | P1 |
| Faturação e recebimentos | Separadores e conferência existentes | Preservar separadores, filtros, confirmação e reversão | MT, UI | Alto | P2 |
| Contas bancárias | Contas, saldos, extratos e movimentos | Contas e saldos; registos existentes; navegação Bancos | MT, BankOverview, DL | Alto | P1 |
| Extratos | Seleção da conta, importação e movimentos | Manter tarefa única e acesso às contas | MT, RL | Alto | P2 |
| Reconciliação automática | Movimentos; faturas; histórico | Preservar os três modos; navegação por teclado e URL | MT, AppDialog | Alto | P1 |
| Bancos (catálogo) | Lista, criar/editar | Manter tabela e formulário em diálogo | MT, BanksCatalog | Baixo | P2 |
| Conferência documental | Métricas, assistentes, lista e detalhe muito longo | Lista existente; detalhe em painel lateral; processos de importação próprios | MT, DL, MetricCard, InvoiceSidePreview | Alto | P1 |
| Assistente e-Fatura | Processo em seis passos | Manter passos, revisão e confirmação explícita | ConferenceAssistant, UI | Alto | P3 |
| Verificar faturas | Filtros, verificação, histórico e propostas | Manter estados e validações, sem disparar verificações ao navegar | InvoiceWorkflow, UI | Alto | P2 |
| Despesas por rubrica | Filtros, resumo, documentos | Manter consulta e detalhe; navegação Contabilidade | MT, RL | Médio | P2 |
| Mapas de gestão | Separadores existentes | Manter os mapas e exportações | MT, ManagementMaps | Médio | P2 |
| Entrega à contabilidade | Períodos, estados, exportação e email | Manter processo e confirmações por período | MT, AccountingDelivery, AppDialog | Alto | P2 |
| Arquivo — Visão geral | Indicadores e assistente | Resumo no módulo Arquivo digital | MT, Archive, UI | Médio | P1 |
| Caixa de entrada | Lista de recebidos, processamento e seleção em lote | Destino interno com contexto preservado | MT, Archive, RL | Alto | P1 |
| Todos os documentos | Pesquisa, filtros e detalhe | Manter pesquisa; detalhe existente acessível | MT, Archive | Médio | P1 |
| Digitalizar documento | Câmara, recorte, PDF e validação | Manter processo dedicado e acesso móvel | MT, DocumentScanner | Alto | P3 |
| Associações | Propostas, decisão e associação manual | Manter confirmação de propostas | MT, Archive, AppDialog | Alto | P1 |
| Pendências | Listas de vários motivos | Manter motivos e ações diretas | MT, Archive | Médio | P1 |
| Dossiês contabilísticos | Períodos, exportação e detalhe | Manter períodos e exportação | MT, Archive | Alto | P2 |
| Arquivo — Auditoria | Registos de ações | Destino interno reservado à gestão | MT, Archive | Médio | P2 |
| Arquivo — Configurações | Parâmetros e integridade | Destino interno com permissões existentes | MT, Archive, ManagedForm | Alto | P2 |
| Compras e Stock — Visão geral | Resumo e listas recentes | Manter resumo e acessos aos documentos | MT, Procurement | Médio | P1 |
| Encomendas a fornecedores | Lista, linhas, confirmação e email | Manter editor complexo e revisão de email | MT, Procurement, AppDialog | Alto | P1 |
| Receções de mercadoria | Lista e confirmação | Manter editor e confirmação | MT, Procurement | Alto | P1 |
| Compras | Lista e documentos, pagamentos, anexos | Manter edição, associação, importação e validação | MT, Purchases, Procurement | Alto | P1 |
| Devoluções a fornecedores | Lista e documento | Manter editor e movimentos | MT, Procurement | Alto | P1 |
| Movimentos de stock | Histórico de movimentos | Manter filtros e consulta | MT, Procurement | Médio | P2 |
| Clientes | Lista, pesquisa, criação e edição | Manter tabela e diálogo | MT, Entities, EntityCreateDialog | Médio | P2 |
| Fornecedores | Lista, pesquisa, criação e edição | Manter tabela e diálogo | MT, Entities | Médio | P2 |
| Artigos e validades | Painel; Artigos; Movimentos; Lotes; Inventários; Importações; Parâmetros | Preservar sete áreas; uniformizar navegação e URL | MT, Inventory, importadores | Alto | P1 |
| Armazéns | Lista e formulário | Manter criar/editar/remover com proteções | MT, Warehouses, AppDialog | Médio | P2 |
| Rubricas | Catálogo, ERP, propostas e remoção | Manter lista e ações confirmadas | MT, Rubrics | Alto | P2 |
| Caixas | Lista por loja, criação/remoção | Manter tabela e confirmações | MT, UI | Alto | P2 |
| Meios de pagamento | Lista, estado e conta predefinida | Manter retorno à origem e contexto da caixa | MT, PaymentMethods | Alto | P1 |
| Operadores / funcionários | Lista, estado e formulário | Manter lista e formulário | MT, Employees | Médio | P2 |
| Receitas e despesas | Área explicitamente indisponível | Manter aviso e alternativa existente | UI | Baixo | P3 |
| Relatórios | Resumos, auditoria e nota de cálculo | Separar resumo e auditoria; preservar informação financeira | EW, MT, tabelas atuais | Médio | P1 |
| Organização | Lojas/caixas, equipa, segurança | Separadores por tarefa | EW, MT, formulários existentes | Alto | P1 |
| Email | Formulário SMTP, contabilidade, modelos e histórico | Ligação; Destinatários e mensagem; Histórico; guardar sempre acessível | EW, MT, ManagedForm, EmailHistory | Alto | P1 |
| Configuração inicial | Assistente com validações e importação ERP | Manter assistente dedicado | CompanySetupPanel | Alto | P3 |
| Integrações | Conectores, configuração, vínculos, saldos, pendências e histórico | Ligações; Configuração; Associações; Pendências; Histórico | EW, MT, IntegrationSettings | Alto | P1 |
| Regime de IVA | Formulário curto | Manter objetivo único | MT, FiscalSettings | Alto | P2 |
| Aparência | Temas e preferências | Manter tokens, logótipo e seleção | MT, ThemeSettings | Baixo | P2 |
| Anexos | Google, Gmail, pastas e associação | Manter consentimentos e configuração | MT, AttachmentSettings | Alto | P2 |
| Digitalização (parâmetros) | Parâmetros + dispositivos PIN | Parâmetros; Acessos por PIN | EW, MT, ScanSettings, QuickScanAccesses | Alto | P1 |
| Verificação automática e-Fatura | Configuração de regras e periodicidade | Manter formulário e permissões | MT, InvoiceWorkflowSettings | Alto | P2 |
| Plataforma | Empresas, auditoria e configuração legal empilhadas | Empresas; Histórico; Informação legal; criação/edição preservadas | EW, MT, AppDialog, LegalAdmin | Alto | P1 |
| Informação legal (administração) | Identidade, revisão, publicação e pedidos | Manter estados e revisão explícita; nunca publicar dados incompletos | LegalAdmin, ManagedForm | Alto | P3 |
| Site público | Página de apresentação | Manter identidade e ligações legais; scroll editorial apropriado | Brand, HomePage | Baixo | P3 |
| Acesso à empresa / plataforma | Formulário curto | Manter identificação, sessão e PIN separados | Form, PrivacyNotice | Alto | P3 |
| Digitalização rápida por PIN | Emparelhamento, PIN, câmara, PDF | Manter interface móvel dedicada e limites de acesso | QuickScan, quickApi | Alto | P3 |
| Páginas legais e privacidade | Conteúdo público e pedidos individuais | Manter páginas de leitura dedicadas; não converter textos legais em separadores ocultos | Legal, Form | Alto | P3 |

## Arquitetura adotada

1. Reutilizar a barra lateral e os seus módulos. Acrescentar navegação interna derivada dos destinos reais e das permissões existentes, sem opções de negócio fictícias.
2. Usar URLs com `pagina` e parâmetros de área; suportar histórico do navegador, ligações diretas e valores inválidos com retorno seguro. Não gravar filtros financeiros nem credenciais no armazenamento persistente.
3. Montar destinos/áreas apenas após a primeira visita e conservar o contexto durante a sessão. O cache visual é separado por empresa/utilizador/perfil/loja e descartado ao terminar sessão ou mudar de âmbito. A navegação não acrescenta credenciais nem documentos ao localStorage; o emparelhamento por PIN mantém o armazenamento de dispositivo já existente e a sessão curta em memória.
4. Separadores acessíveis: setas, Home/End, foco visível, rótulos e painéis associados; deslocação horizontal em telemóvel. Não depender da cor.
5. Converter apenas detalhes apropriados em painel lateral, reutilizando o modal nativo para foco, Escape e bloqueio do fundo. Preservar editores complexos e confirmações financeiras.
6. Preservar regras, API, autorização, valores em cêntimos, importações, estados e confirmações. Não executar operações financeiras ou enviar emails como parte da validação visual.

## Critérios de validação

Compilação TypeScript/Vite, verificador de interações e testes de navegador focados em URLs/histórico, estado de filtros, acesso a todas as áreas, teclado, formulários entre separadores, desktop/telemóvel, ausência de overflow global e erros de consola. A simulação de API nos testes deve ficar limitada ao contexto isolado do navegador; nunca substituir os dados da aplicação.

## Estado confirmado nesta revisão

Levantamento atual: 132 ficheiros de interface e estilos, 84 componentes TSX e 386 pontos de chamada API. O inventário atualizado inclui estados, formulários, ações, permissões, dependências, localização das chamadas e hashes anteriores à intervenção. A matriz acima cobre destinos e processos; os componentes auxiliares constam integralmente do inventário.

Preservar os filtros compactos e a seleção de trimestre, o Arquivo Digital, os logótipos transparentes, temas e contraste, os modos sugestões/manual da reconciliação, as explicações de falhas SMTP e a digitalização com deteção de margens em direto. O levantamento foi executado novamente; os ficheiros de segurança anteriores são apenas uma referência técnica para reutilização de componentes.

Prioridades: navegação e conservação de contexto; dashboard; liquidez; bancos e reconciliação; detalhe documental; arquivo; email, ERP, organização, SAF-T e administração. Manter dedicados os processos de caixa, câmara, importação, confirmação financeira e páginas legais.

## Validação desta revisão

Os resultados abaixo foram executados nesta revisão. A aplicação usa os dados e API existentes; os dados fictícios ficam limitados aos testes isolados.

## Implementação

| Área | Resultado |
|---|---|
| Navegação | Módulos laterais preservados; destinos reais disponíveis também no topo da área de trabalho, com as permissões existentes. URLs `pagina` e `area.<nome>`, voltar/avançar, destino inválido com retorno seguro e acesso direto. |
| Contexto | Montagem após primeira visita; filtros, paginação e formulários mantidos em memória. O contexto é separado por empresa, utilizador, perfil e loja. Mudar de loja, sair, fechar um editor e abandonar o navegador conservam as proteções contra perda de trabalho. |
| Dashboard | Resumo do negócio, Evolução, Atenção, Ações rápidas e Operação de caixa. Personalização, indicadores, gráficos, períodos e comparação preservados. |
| Tesouraria | Navegação entre caixa, movimentos, depósitos, previsões e faturação. Liquidez dividida em Resumo, Recebimentos e pagamentos, Pendências e Compromissos; saldos de origem, cenários, simulações, exportação e liquidação preservados. |
| Bancos | Navegação interna entre contas, extratos e reconciliação. Mantidos filtros por conta/período, diferenças, seleção de movimentos, pré-visualização de duplicações, confirmação, reversão e os modos sugestões/manual existentes. |
| Contabilidade | Acesso interno a conferência documental, despesas, mapas e entrega. Detalhe documental num painel lateral com Dados e ERP, Original e localização, Despesa e contabilidade, Pagamentos e banco e Histórico. Os assistentes complexos mantêm os seus passos. |
| Arquivo Digital | Destinos internos e contexto de pesquisa conservado entre documentos, associações e outras áreas. Separadores de estado e de motivos de pendência mantidos; ações e formulários reutilizam os controlos comuns. |
| Stock e mapas | Áreas existentes preservadas, com navegação partilhada e URL. Catálogo, lotes, inventários, importações, parâmetros e mapas continuam acessíveis. |
| Email | Ligação SMTP, Destinatários, Mensagem e assinatura e Histórico. Campos e rascunhos partilhados entre áreas, barra Guardar acessível e validação que abre a área do campo inválido. O teste usa a configuração guardada, como anteriormente. |
| ERP | Conectores e Configuração ERP; esta divide Ligação, Associações e saldos, Pendências e Histórico. Consultas, pré-visualização, permissões e confirmações explícitas preservadas. |
| SAF-T | Importar, Histórico, Artigos, Meios de pagamento e Análise; revisão de conflitos, mapeamentos, repetição após falha e exportações preservados. |
| Administração | Organização por lojas/caixas, equipa e segurança/dados; parâmetros de digitalização e acessos por PIN separados; plataforma por empresas, histórico e informação legal. Não se publica informação legal automaticamente. |
| Tabelas | Catálogos simples de caixas e auditoria com pesquisa, ordenação, paginação e cartões rotulados em telemóvel. Tabelas financeiras especializadas mantêm a sua apresentação e controlos. |
| Digitalização | Processo móvel dedicado e emparelhamento preservados. Não se carrega o dashboard durante a sessão PIN. Alterações simultâneas à câmara, deteção em direto e respetivos estilos foram mantidas, sem sobreposição. |

### Componentes e acessibilidade

`WorkspaceUI` disponibiliza ModuleTabs, TaskWorkspace, PageHeader, SectionHeader, LoadingState, ErrorState e DetailDrawer, reutilizando o diálogo nativo já existente para foco, Escape e proteção de rascunhos. `DataTable` disponibiliza tabela/cartões, pesquisa, FilterBar e ActionToolbar. MetricCard, StatusBadge, Empty e confirmAction/FeedbackHost mantêm as funções de resumo, estado, ausência de dados e confirmação, sem duplicar essas regras.

Os filtros compactos continuam na apresentação comum, incluindo os novos FilterBar. Cabeçalho compacto, ligação para saltar à área de trabalho, foco visível, setas/Home/End nos separadores, deslocação horizontal em ecrãs pequenos e respeito por movimento reduzido. Mantêm-se os logótipos e os temas; o contraste dos nomes dos assistentes em fundo escuro foi ajustado.

### Testes executados

- `npm --prefix frontend run build`: TypeScript e Vite passaram, incluindo a repetição final após as alterações simultâneas à câmara.
- `npm --prefix frontend run check:ui`: passou — 38 janelas, 650 botões, 100 formulários e 214 mensagens verificados.
- `test-workspace-ui.py`: passou a 1440 px/claro, 820 px/escuro e 390 px/escuro; histórico, URLs, teclado, rascunhos entre áreas e páginas, validação de campo oculto, confirmação de descarte, painel lateral, pesquisa, paginação e cartões. Sem overflow global nem erros de consola.
- `test-workspace-modules-ui.py`: liquidez, ERP, stock, SAF-T e arquivo passaram a 1440 e 390 px; áreas, pesquisa preservada, histórico e ausência de operações financeiras reais.
- `test-workspace-navigation-ui.py`: passou — destino inválido, alias de módulo e bloqueio de áreas reservadas antes da montagem para o perfil de consulta.
- `test-bank-balances-ui.py`: passou — saldos por data, diferenças, filtro por conta, duplicações, seleção de movimento existente e contraste claro/escuro/móvel.
- `test-liquidity-pagination-ui.py`: passou — paginação, filtros e totais das páginas.
- `test-document-control-ui.py`: passou em esquema PostgreSQL isolado — e-Fatura, associação ERP, disponibilidade, pagamento parcial, proposta com várias taxas, exportação, confirmação em lote e layout móvel. O teste foi adaptado aos separadores do painel lateral.
- `test-saft-sales-ui.py`: passou — ficheiro, revisão, conflitos, preservação após falha, confirmação, histórico, mapeamento, análise e móvel, com API intercetada.
- `test-operating-modes-ui.py`: passou — local, integrado em leitura, confirmação explícita e preservação do modo após falha, em desktop e móvel.
- `test-legal-ui.py`: passou — bloqueio de publicação incompleta, preferências, pedidos de direitos, aceitação separada, exportação de perfil, administração, teclado e móvel, sem pedidos a terceiros.
- `test-workspace-quickscan-ui.py`: passou — PIN, galeria, recorte, cancelar saída preservando páginas e terminar sessão; sem backend ou upload real. O teste acompanha o botão Cancelar da versão atual da digitalização.
- `test-edge-detection.mjs`: passou — rotação, perspetiva, texto, ruído, iluminação, retrato, ausência de documento e contornos inválidos.
- Comparação estática dos 386 pontos de chamada API anteriores: nenhum padrão removido. Esta verificação complementa os testes; não prova todas as combinações de dados e perfis.
- Verificação dos filtros compactos: datas e trimestres funcionais, sem overflow horizontal a 320, 390, 1024 e 1508 px nos temas claro e escuro; o painel de referência conserva cerca de 120 px de altura em desktop. `git diff --check` passou.

Imagens desta execução em `.local/workspace-ui/`. Cópia de segurança anterior a esta revisão em `.local/interface-20261021/before/`; não é publicada nem usada como dados da aplicação.

### Limites

Os rascunhos são memória da sessão, não documentos persistentes: é necessário guardar antes de atualizar ou fechar o navegador. Manter áreas visitadas aumenta o uso de memória e conserva os mecanismos de atualização que já existiam. A mudança de loja ou de âmbito reinicia o contexto.

Não foram alterados cálculos financeiros, contratos de API nem integrações de backend. Os testes usam respostas intercetadas ou um esquema isolado; não representam validação com todos os ERP, contas bancárias, equipamentos ou dados de produção. O código inicial da primeira compilação desta revisão passou de aproximadamente 1 017 kB para 442 kB sem compressão, distribuído por módulos; não é uma medição de tempo numa rede móvel.
