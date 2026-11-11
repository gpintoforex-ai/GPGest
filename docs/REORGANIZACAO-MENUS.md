# Reorganização dos menus — 3 de novembro de 2026

## Problema corrigido

“Encomendas de clientes” e “Necessidades de compra” abriam o mesmo centro. A diferença de filtro só afetava os detalhes expansíveis, deixando o painel principal igual. “Artigos e preços” e “Artigos e validades” também tinham exatamente o mesmo destino.

## Organização aplicada

**Encomendas**

1. Visão geral: acompanhamento de todo o circuito.
2. Pedidos de clientes: lista de pedidos, com filtros de abertos, disponíveis, concluídos e todos.
3. Planeamento de compras: separadores Necessidades e Simulações. As necessidades abrem diretamente, com o filtro “Por comprar”; a visão geral do centro não é repetida nesta vista.
4. Encomendas a fornecedores: documentos emitidos e acompanhamento.
5. Levantamentos e contactos: pedidos disponíveis para levantar.
6. Histórico: registos de operações e fecho.

**Artigos:** Catálogo de artigos e Lotes e validades. O catálogo substitui os dois atalhos iguais.

**Compras:** Resumo de compras, Compras, Receções de mercadoria e Devoluções a fornecedores. A simulação fica no Planeamento de compras.

**Entidades:** Clientes e Fornecedores. As condições comerciais continuam nas fichas e têm uma ação na lista de fornecedores, disponível aos perfis de gestão.

**Financeiro:** tarefas diárias e acompanhamento bancário. Meios de pagamento e Prazos de pagamento passam para Configurações, juntamente com Rubricas.

**Contabilidade:** conferência, despesas, entrega, digitalização e dossiês mantêm destinos próprios. “Arquivo documental” reúne Todos os documentos, Caixa de entrada, Visão geral, Associações e Pendências em separadores. Auditoria e Configurações do arquivo mantêm os acessos anteriores.

Dashboard, Stocks, Vendas e Relatórios conservam os respetivos destinos.

## Compatibilidade e permissões

- Os identificadores de páginas e APIs existentes são conservados.
- As vistas agrupadas continuam acessíveis por pesquisa global e por endereço direto, através de destinos relacionados no catálogo de navegação.
- Os links antigos de simulações transitam para o separador Simulações do planeamento. O antigo filtro de necessidades abre os detalhes correspondentes.
- O menu ativo e o contexto do cabeçalho acompanham a vista apresentada, incluindo nas vistas agrupadas e nos comandos Voltar/Avançar do navegador.
- A abertura direta do planeamento seleciona uma loja autorizada quando ainda não existe seleção. A vista Necessidades apresenta apenas o seu seletor de loja e um botão Atualizar, evitando controlos redundantes ou uma indicação de “Todas as lojas” sobre um cálculo de loja única.
- Os perfis e modos continuam a filtrar tanto os destinos principais como os relacionados. O planeamento não alarga permissões de consulta de custos.
- Não foram alteradas regras de encomenda, confirmação, stock, faturação ou integração com ERP. Os testes de navegação não criam encomendas nem enviam mensagens.

## Ficheiros principais

- `frontend/src/navigationCatalog.ts`: organização, destinos relacionados, permissões e menu ativo.
- `frontend/src/App.tsx`: pesquisa dos destinos agrupados, contexto do cabeçalho e acesso às condições comerciais.
- `frontend/src/CustomerOrders.tsx`: planeamento com Necessidades/Simulações e compatibilidade dos links antigos.
- `frontend/src/SupplyCenter.tsx`: apresentação direta das necessidades, sem repetir o centro geral.
- `frontend/src/Archive.tsx`: separadores do arquivo documental e catálogo de páginas partilhado.
- `frontend/src/tables/TablePage.tsx`: ação de condições comerciais na lista de fornecedores.
- `frontend/src/components/WorkspaceNavigation.tsx`: ícones e atalhos adaptados aos novos nomes.

## Verificações

- `scripts/test-navigation.cjs`: páginas antigas alcançáveis, identificadores únicos, ausência de destinos principais duplicados, permissões em 12 combinações de perfil/modo e correspondência do menu ativo.
- `scripts/test-navigation-reorganization-ui.py`: destinos distintos, filtros, planeamento, condições comerciais, arquivo, pesquisa dos destinos agrupados, histórico do navegador e abertura direta com loja consistente, em computador e telemóvel, com APIs simuladas.
- `scripts/test-customer-orders-ui.py`: criação/edição de pedidos, simulações, rascunhos e associação documental com dados de teste isolados.
- `npm run build`: compilação TypeScript e produção.

As capturas da reorganização ficam em `.local/navigation-reorganization/`.
