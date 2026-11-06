# Uniformização de fichas — clientes, fornecedores e encomendas

## Análise e implementação por módulo

As entidades usam `RecordDialog`, os campos declarados pelo servidor e as APIs de Tabelas. Os pedidos de clientes e as simulações usam `CustomerOrders`; os documentos de compras usam `Procurement`. Já existiam reservas, associação de artigos provisórios, propostas agrupadas por fornecedor, sugestões de reposição, mínimo/portes, confirmação explícita, email, receções parciais e associação aos clientes. Essas regras e ações foram conservadas.

A implementação avançou pela ordem Cliente → Fornecedor → Pedidos de clientes → Encomendas a fornecedores, com validação de cada etapa antes de avançar.

## Componentes partilhados

Reutilizados da Ficha de Artigo: `RecordHeader`, `RecordNavigation`, `RecordGroup`, `RecordIndicator`, `RecordHistory` e os respetivos estilos. `RecordWorkspace` compõe estes componentes numa ficha com cabeçalho fixo, corpo com deslocação e navegação lateral em computador ou horizontal em telemóvel. A seleção usa turquesa claro e a gravação usa verde.

Os controlos dos separadores ficam montados para preservar o rascunho. A validação revela o separador de um campo inválido. Continuam a ser usados `AppDialog`, `ManagedForm` e `Form`, incluindo proteção de alterações por guardar, estado ocupado e apresentação de erros.

## Resultado por módulo

- **Cliente:** Geral, Contactos, Condições Comerciais, Vendas, Encomendas, Conta Corrente, Documentos e Histórico. Encomendas associadas por identificador; documentos do arquivo com correspondência exata do NIF; estados de integração e dependências conservados.
- **Fornecedor:** Geral, Contactos, Condições de Compra, Artigos, Encomendas, Compras, Conta Corrente e Histórico. Condições de pagamento, mínimos, portes e devoluções por validade continuam editáveis pelas mesmas regras. Indicadores de mínimo sem portes, prazo, encomendas pendentes e número de compras confirmadas nos últimos 30 dias.
- **Pedidos de clientes:** edição em Geral e contactos / Artigos; consulta em Geral / Artigos e disponibilidade / Contacto e levantamento / Histórico. Quantidades e estados continuam a vir do servidor. Reservar, libertar, associar artigo/fornecedor, entregar, cancelar e contactar mantêm as operações existentes.
- **Encomendas a fornecedores:** simulação com resumo, artigos, clientes associados e acompanhamento; documentos de encomenda com identificação, artigos, clientes/receções, operações, documentos e histórico. Os cálculos, sugestões de rotação e comparações de cenários existentes permanecem disponíveis. Guardar um rascunho não confirma a encomenda. Confirmar, enviar email e receber continuam a exigir as ações explícitas existentes.

## Dados, permissões e limites

- O detalhe da entidade fornece `related_entity_id`, resolvido apenas por ID local ou pela ligação explícita ao espelho ERP. Consultar não cria a referência local nem chama a API ERP. Nome e NIF iguais não fazem uma associação automática de encomendas.
- As consultas relacionadas reutilizam as APIs existentes, com os respetivos limites de lojas, permissões e ocultação de custos. A ausência de permissão ou falha de consulta aparece como indisponibilidade, não como zero.
- **Faturação, saldo, dívida vencida e conta corrente consolidada ainda necessitam de desenvolvimento.** Os movimentos financeiros existentes não têm todos uma ligação inequívoca à entidade, e os documentos de faturação têm várias origens. É necessária uma associação por entidade e reconciliação de origens antes de somar valores sem duplicar documentos ou atribuir dívidas a homónimos. Os separadores e indicadores assinalam esta indisponibilidade.
- O separador Artigos do fornecedor mostra os artigos cujo fornecedor principal corresponde ao identificador da ficha. A agregação de todas as condições negociadas com fornecedores alternativos necessita de uma consulta própria.
- A consulta do arquivo considera os primeiros 200 resultados da pesquisa pelo NIF e apresenta apenas correspondências exatas. O limite é explicado na ficha. O histórico de pedidos usa a API existente, limitada aos 1000 eventos recentes das lojas autorizadas. O histórico de fornecedor mostra registos de encomendas/compras e informação de integração; não substitui uma auditoria completa de alterações de campos.
- Não houve migração de dados nem novas escritas no ERP. Os testes de integração usaram um ERP simulado.

## Ficheiros alterados nesta implementação

- `frontend/src/components/RecordWorkspace.tsx` — composição reutilizável e validação entre separadores.
- `frontend/src/components/RecordWorkspace.css` — organização, cabeçalho fixo, turquesa e adaptação responsiva.
- `frontend/src/tables/EntityRecordSheet.tsx` — fichas de cliente e fornecedor e consultas relacionadas.
- `frontend/src/tables/RecordDialog.tsx` — integração das fichas, preservando campos, bloqueios ERP, payload, NIF/VIES e gravação.
- `frontend/src/tables/types.ts` — referência local de relacionamento no detalhe.
- `frontend/src/CustomerOrders.tsx` — fichas de pedidos, simulações e encomendas emitidas.
- `frontend/src/Procurement.tsx` — rascunho e consulta de encomendas a fornecedores; restantes tipos de compra conservados.
- `backend/app/table_framework.py` — referência local de entidade, somente leitura.
- `backend/tests/test_tables.py` — associação explícita e ausência de criação/chamadas ERP na consulta.
- `scripts/test-entity-record-ui.py` — testes isolados das duas entidades.
- `scripts/test-customer-orders-ui.py`, `scripts/test-procurement-ui.py`, `scripts/test-supplier-dialog-ui.py` — adaptação dos testes aos novos separadores.
- Este documento.

Os componentes da Ficha de Artigo já existentes foram reutilizados sem alterar o seu comportamento. O repositório tinha outras alterações anteriores, que foram conservadas.

## Validação

- TypeScript e compilação de produção (`npm run build`) concluídos. O Vite mantém um aviso de tamanho de bundle, sem falha de compilação.
- Testes das entidades em 1440×900, 1024×768 e 390×844: separadores, dados relacionados, gravação, flags ocultas, validação de email num separador inativo, NIF exato no arquivo, bloqueios ERP, modo de consulta e ausência de escritas adicionais.
- Fornecedor em 1366×657, 1280×600 e 1440×900: os oito separadores conservam os limites da janela, com deslocação no corpo.
- Pedidos de clientes em 1440 e 390 px: criação/edição, produtos provisórios, agrupamento por fornecedor, sugestões e bonificações, simulação de mínimos, proteção do rascunho, arquivo e conferência documental. Uma execução móvel teve uma falha de temporização ao fechar durante o cálculo; a execução móvel isolada seguinte passou sem alteração da proteção de saída.
- Compras: formulário, artigo, rascunho, receções e devoluções por validade; computador/telemóvel, modos claro/escuro, contraste e ausência de transbordo horizontal.
- Servidor: três testes selecionados de Tabelas passaram, incluindo operações locais, bloqueios/sincronização com ERP simulado e identidade de entidades sem associação pelo nome. Base de demonstração e esquemas isolados.
- Servidor local atualizado preservando o ambiente efetivo do processo; verificação de saúde HTTP 200.

As imagens de verificação das entidades ficam em `.local/entity-record-ui/`; as restantes imagens ficam nas pastas dos respetivos testes. Não foram criadas encomendas nem enviados emails reais durante a validação.

## Extensão do visual a toda a aplicação — 3 de novembro de 2026

O estilo das fichas passou a ser a referência global, através de `frontend/src/application-visual.css`, carregado em `frontend/src/main.tsx`. Este ficheiro define os mesmos tons de turquesa, superfícies, contornos, sombras e cantos para os componentes partilhados. Os menus e separadores selecionados usam turquesa claro com texto escuro; as ações principais de gravação usam verde; os estados informativos, avisos, erros e identidades de pagamento conservam as cores semânticas.

A alteração abrange o menu principal, os separadores dos módulos, botões, campos, tabelas, cartões e janelas. Inclui os modos claro e escuro e mantém as superfícies explicitamente claras/escuras. A descrição dos temas em `frontend/src/ThemeSettings.tsx` foi atualizada. Não altera navegação, dados ou regras de negócio.

`scripts/test-application-visual-ui.py` verifica Dashboard, Clientes, Conferência documental e Contas bancárias em 1440 e 390 px, nos dois temas: seleção do menu, carregamento dos módulos, contraste do texto, ausência de transbordo horizontal, fecho do menu por Escape e ausência de escritas nas APIs. As capturas ficam em `.local/application-visual/`. Os testes usam dados isolados. Os formulários de compras são também verificados pelo teste existente `scripts/test-procurement-ui.py`.
