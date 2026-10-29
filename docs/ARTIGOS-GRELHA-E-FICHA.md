# Artigos — grelha, fichas e validação

## Organização

O catálogo usa `ArticleGrid.tsx`, com pesquisa por código, designação, códigos de barras, referências e fornecedor. Os filtros avançados incluem família, categoria, marca, fornecedor, estado, origem, stock e validade. Os restantes separadores do módulo conservam os seus fluxos.

A grelha permite ordenar, selecionar artigos da página, escolher 25/50/100 linhas, configurar colunas e usar densidade compacta, normal ou confortável. As preferências são locais ao navegador e separadas por empresa e utilizador. Os custos só são disponibilizados aos perfis autorizados.

**Consultar** abre a ficha sem edição. **Mais ações** reúne editar, duplicar, alterar preço, ativar/desativar e eliminar, conforme as permissões e a origem. A duplicação prepara uma nova ficha local: remove código, códigos de barras, identidade e ligação ERP; não copia movimentos, reservas, lotes nem histórico. Só é gravada depois de preencher os dados e guardar.

A ativação/desativação em lote só admite artigos locais, exige confirmação e usa as validações, revisões e auditoria existentes em cada artigo. As operações são independentes: falhas parciais identificam os artigos recusados; as restantes alterações ficam guardadas. A seleção é limpa ao mudar de página, pesquisa ou filtros.

**Exportar página** / **Exportar seleção** gera CSV dos dados identificativos apresentados nessa página, sem custos. Não exporta todo o catálogo implicitamente.

## Ficha

Separadores: Geral; Preços e rentabilidade; Stock; Compras e fornecedores; Validades e lotes; Integração ERP; Histórico; Descrição e anexos; Preços de mercado ([PRECOS-MERCADO.md](PRECOS-MERCADO.md)).

O preço e IVA são editados em Preços e rentabilidade. Margem e markup são calculados quando existem preço e custo válidos; a base de IVA desconhecida dos preços ERP continua a impedir cálculos indevidos. Stock permanece calculado por movimentos. Os anexos e imagens existentes são preservados.

O histórico mostra as últimas 30 alterações suportadas, com utilizador e data. Não foi criada uma tabela de histórico de preços, stock máximo nem condições comerciais por múltiplos fornecedores.

## API e desempenho

`GET /api/stock/catalog` preserva o contrato completo usado pelos restantes fluxos. Aceita também:

- `metadata_only=true`: permissões, fornecedores, armazéns e opções de filtros, sem carregar movimentos;
- `page`, `size` (máximo 100), `q`, `sort`, `direction`;
- `family`, `category`, `brand`, `supplier_id`, `active`, `origin`, `stock`, `expiry`.

Na consulta paginada, pesquisa, filtros de identificação, contagem e ordenação são aplicados em PostgreSQL antes de carregar o histórico da página. As validades consideram apenas lotes com saldo positivo em armazéns ativos autorizados e a data de Lisboa.

Filtros de saldo ou validade precisam de avaliar os movimentos de todos os candidatos antes de paginar. Para catálogos com histórico muito extenso, a evolução seguinte é estudar agregações e índices com medições, preservando o custo médio histórico. Não foram introduzidas migrações nem substituídas regras do stock.

A origem ERP e a data de receção do catálogo não garantem que não tenham ocorrido alterações posteriores na origem. Os estados apresentados não inventam uma sincronização individual bidirecional.

## Testes

- `node scripts/test-article-values.cjs`: cálculos, validação e payloads.
- `backend/tests/test_article_catalog.py`: paginação e filtros com 1 000/10 000 artigos fictícios, carregamento do histórico, permissões e validades por loja.
- `backend/tests/test_inventory.py` e `test_inventory_tenants.py`: regras e isolamento existentes.
- `scripts/test-inventory-ui.py`: regressões de fichas, movimentos, lotes e importações, com APIs simuladas.
- `scripts/test-article-grid-ui.cjs`: 10 000 artigos simulados, navegação, consulta, duplicação, lote e preferências; gera imagens de desktop, tema escuro e telemóvel em `.local/stock-grid-ui`.

O teste Node requer Playwright (`PLAYWRIGHT_NODE_PATH` permite indicar uma instalação existente). `GPGEST_UI_URL` permite apontar os testes de interface para um servidor Vite isolado. Todos os pedidos da aplicação são intercetados no teste Node.
