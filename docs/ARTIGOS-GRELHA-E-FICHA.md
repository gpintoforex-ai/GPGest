# Artigos — grelha, fichas e validação

## Organização

O catálogo usa `ArticleGrid.tsx`, com pesquisa por código, designação, códigos de barras, referências e fornecedor. Os filtros avançados incluem família, categoria, marca, fornecedor, estado, origem, stock e validade. Os restantes separadores do módulo conservam os seus fluxos.

A grelha permite ordenar, selecionar artigos da página, escolher 25/50/100 linhas, configurar colunas e usar densidade compacta, normal ou confortável. As preferências são locais ao navegador e separadas por empresa e utilizador. Os custos só são disponibilizados aos perfis autorizados.

A pesquisa, **Filtros avançados** e **Vista** (densidade, linhas por página e colunas) ficam numa só linha; os dois painéis abrem por cima da grelha e fecham com clique fora ou Escape. Os cabeçalhos têm todos o mesmo aspeto; os ordenáveis indicam o sentido (↑/↓). As linhas só mostram o que acrescenta informação: o stock atual e o reservado aparecem quando diferem do disponível; a origem «Wintouch» tem selo próprio, com a data do catálogo; a validade só tem selo quando está próxima ou expirada. No telemóvel, cada artigo é um cartão e o cabeçalho fica como barra de seleção e ordenação.

Clicar no nome do artigo (ou numa zona da linha sem controlos) abre a ficha sem edição. O menu **Ações** de cada linha reúne consultar, editar, duplicar, alterar preço, ativar/desativar e eliminar, conforme as permissões e a origem; abre por cima da grelha, acompanha o deslocamento e fecha com Escape ou clique fora. A duplicação prepara uma nova ficha local: remove código, códigos de barras, identidade e ligação ERP; não copia movimentos, reservas, lotes nem histórico. Só é gravada depois de preencher os dados e guardar.

A ativação/desativação em lote só admite artigos locais, exige confirmação e usa as validações, revisões e auditoria existentes em cada artigo. As operações são independentes: falhas parciais identificam os artigos recusados; as restantes alterações ficam guardadas. A seleção é limpa ao mudar de página, pesquisa ou filtros.

**Exportar página** / **Exportar seleção** gera CSV dos dados identificativos apresentados nessa página, sem custos. Não exporta todo o catálogo implicitamente.

## Ficha

Separadores (numa só linha; deslizam na horizontal quando não cabem): Geral; Preços e rentabilidade; Stock; Compras e fornecedores; Validades e lotes; Integração ERP; Histórico; Descrição e anexos.

**Preços e rentabilidade:** o preço e o IVA editam-se nos campos (nos artigos locais não se repetem por baixo); custo médio, último custo, margem e markup são calculados quando existem preço e custo válidos; a base de IVA desconhecida dos preços ERP continua a impedir cálculos indevidos. Inclui:

- **Histórico do preço de venda** (artigos locais): as últimas 20 mudanças de preço ou IVA, com data e utilizador, reconstituídas da auditoria (criação, edição da ficha e «Alterar preço»). Gravar a ficha sem mudar o preço não cria entrada. Artigos criados antes desta versão só têm histórico a partir da primeira edição. Só o preço e o IVA saem da auditoria.
- **Preços de mercado** (KuantoKusta e outras lojas): [PRECOS-MERCADO.md](PRECOS-MERCADO.md).

**Stock:** stock atual, reservado e disponível (calculados por movimentos), mínimo, nível de reposição e **stock máximo** (0 = sem máximo; tem de ser igual ou superior à reposição e ao mínimo). O máximo é informativo: na grelha, o selo «Acima do máximo» e o filtro de situação de stock com o mesmo nome; não altera encomendas nem reposições automáticas. Fica guardado na ficha do artigo (sem migração).

**Compras e fornecedores:** fornecedor principal e referência; **compras por fornecedor**, a partir das entradas de compra (receções e movimentos de compra) nos armazéns autorizados: número de compras, quantidade, último custo, mínimo/máximo e custo médio ponderado (sem IVA, por unidade), data e documento da última compra. Entradas substituídas por edição de uma receção ou anuladas (estorno) não contam. Sem permissão de consultar custos, aparecem os fornecedores e as quantidades, sem custos.

**Condições por fornecedor** (tabela `article_supplier_terms`, migração **0070**): para cada fornecedor do artigo, a referência nesse fornecedor, o preço acordado sem IVA por unidade e a data de validade, o prazo de entrega (dias), a quantidade mínima e o múltiplo de encomenda, e observações. Um registo por artigo e fornecedor; o fornecedor principal aparece primeiro. Cada linha compara o preço acordado com o último custo real das compras desse fornecedor (ex.: «+11,1 % face ao acordado») e assinala o preço expirado. Gravam-se pela própria secção (Enter grava as condições, não a ficha), com revisão para evitar sobreposições, e ficam na auditoria (`stock_article_supplier_terms`, `stock_article_supplier_terms_removed`). Editar exige a permissão de ajustar artigos; o preço acordado só é visível e editável com a permissão de consultar custos (sem ela, o preço existente mantém-se). Funciona também nos artigos da Wintouch (o conector não tem estas condições). O preço acordado é um custo unitário com a precisão dos custos de stock, não um movimento de dinheiro. Um fornecedor com condições registadas conta como referenciado e não pode ser eliminado em Entidades. As condições ainda não são usadas para propor preços nas encomendas ou receções. As condições gerais do fornecedor (prazo e encomenda mínima) continuam na ficha do fornecedor.

O histórico mostra as últimas 30 alterações suportadas, com utilizador e data. Stock permanece calculado por movimentos. Os anexos e imagens existentes são preservados.

## API e desempenho

`GET /api/stock/catalog` preserva o contrato completo usado pelos restantes fluxos. Aceita também:

- `metadata_only=true`: permissões, fornecedores, armazéns e opções de filtros, sem carregar movimentos;
- `page`, `size` (máximo 100), `q`, `sort`, `direction`;
- `family`, `category`, `brand`, `supplier_id`, `active`, `origin`, `stock`, `expiry`.

Na consulta paginada, pesquisa, filtros de identificação, contagem e ordenação são aplicados em PostgreSQL antes de carregar o histórico da página. As validades consideram apenas lotes com saldo positivo em armazéns ativos autorizados e a data de Lisboa.

Os serviços (portes, mão de obra…) não movimentam stock: na grelha aparecem como «Serviço», sem alertas de stock, e ficam fora dos filtros de situação de stock. A verificação de código e códigos de barras repetidos ao gravar não lê as fichas de todos os artigos: o código compara-se sem distinguir maiúsculas (incluindo acentuadas) e os códigos de barras são procurados no PostgreSQL.

O catálogo completo (sem `page`), usado por Compras, Encomendas de clientes, Ciclo de encomendas e «Registar movimento», lê os movimentos como linhas simples (sem objetos ORM) e responde com o mesmo JSON gerado diretamente (teste de equivalência em `test_article_catalog.py`). Com 4 000 artigos e 20 000 movimentos fictícios, passou de cerca de 1,3 s para cerca de 0,9 s; a página da grelha demora menos de 0,1 s. O custo restante cresce com o número de movimentos; a evolução seguinte é estes ecrãs pesquisarem artigos por página em vez de carregarem o catálogo inteiro.

Filtros de saldo ou validade precisam de avaliar os movimentos de todos os candidatos antes de paginar. Para catálogos com histórico muito extenso, a evolução seguinte é estudar agregações e índices com medições, preservando o custo médio histórico. Não foram introduzidas migrações nem substituídas regras do stock.

A origem ERP e a data de receção do catálogo não garantem que não tenham ocorrido alterações posteriores na origem. Os estados apresentados não inventam uma sincronização individual bidirecional.

## Testes

- `node scripts/test-article-values.cjs`: cálculos, validação e payloads.
- `backend/tests/test_article_catalog.py`: paginação e filtros com 1 000/10 000 artigos fictícios, carregamento do histórico, permissões e validades por loja.
- `backend/tests/test_inventory.py` e `test_inventory_tenants.py`: regras e isolamento existentes.
- `scripts/test-inventory-ui.py`: regressões de fichas, movimentos, lotes e importações, com APIs simuladas.
- `scripts/test-article-grid-ui.cjs`: 10 000 artigos simulados, navegação, consulta, duplicação, lote e preferências; gera imagens de desktop, tema escuro e telemóvel em `.local/stock-grid-ui`.

O teste Node requer Playwright (`PLAYWRIGHT_NODE_PATH` permite indicar uma instalação existente). `GPGEST_UI_URL` permite apontar os testes de interface para um servidor Vite isolado. Todos os pedidos da aplicação são intercetados no teste Node.
