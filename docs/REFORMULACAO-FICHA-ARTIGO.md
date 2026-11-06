# Ficha de artigo — reformulação pela referência visual

## Estrutura analisada e preservada

- `ArticleGrid` gere pesquisa, consulta, duplicação e eliminação confirmada; `Inventory` abre os diálogos e chama as APIs.
- `ArticleForm` mantém o rascunho canónico de `articleValues`, valida campos e usa o `AppDialog` para proteger alterações pendentes. `MarketPrices` e `ArticleSupplierTerms` conservam os seus serviços próprios.
- `StockArticle` guarda identidade, revisão, ligação ERP e dados JSON da ficha; `StockEntry` mantém os movimentos. Preços ERP vêm de `WintouchProduct`. Não foram criadas colunas, tabelas ou migrações.
- Mantêm-se as regras de código/barras únicos, HTTPS, limites de campos, lotes/validade, limites de stock e revisão concorrente. Os campos de origem ERP continuam bloqueados. Eliminar artigos permanece na grelha, com confirmação e impedimento quando há histórico/associações.

## Organização visual

Cabeçalho com guardar/duplicar/ajuda; identificação e fotografia; cinco indicadores; navegação esquerda; cartões de preços, identificação, informação rápida, observações e estados. As áreas existentes continuam acessíveis, incluindo lotes, preços de mercado, condições por fornecedor, anexos e auditoria. Vendas e Compras acrescentam resumos e referências dos movimentos existentes.

O turquesa claro do logótipo é aplicado à seleção e aos destaques da ficha, com texto escuro para contraste e verde, amarelo, azul e vermelho nos indicadores. Claro/escuro e ecrãs de computador, tablet e telemóvel têm disposição própria. Mantém-se IAZELA como marca.

`components/RecordSheet.tsx` disponibiliza cabeçalho, navegação acessível, grupos, indicadores e histórico reutilizáveis. `StatusBadge`, `SyncStatus`, `Field`, `ActionButton` e a proteção de rascunhos são reutilizados sem reformular outros módulos.

## Indicadores e limites dos dados

- Quantidades e custo: as mesmas regras do catálogo e dos armazéns autorizados. Custos são ocultados quando falta permissão.
- Vendido em 30 dias: movimentos `sale` e `customer_return`, pela data do movimento, incluindo hoje e os 29 dias anteriores. Não é o top de vendas do Dashboard: esse pode ler linhas ERP ainda pendentes de aplicar ao stock. A origem está indicada na ficha.
- Em encomenda: quantidades aceites de encomendas confirmadas menos receções confirmadas não anuladas, usando `procurement.accepted/used`. Não inclui rascunhos ou encomendas anuladas.
- Evolução: seis meses de quantidades no ledger autorizado. Documentos: até 20 movimentos recentes, com acesso ao histórico completo pelo módulo de movimentos.
- Margem: `(preço sem IVA − custo médio) / preço sem IVA`; preços/custos desconhecidos ou preço zero não produzem uma percentagem fictícia.
- Peso, volume, prazo genérico de validade, etiquetas comerciais personalizadas, tabelas adicionais de preços e rotação por stock médio não têm dados próprios. São identificados como indisponíveis; não foram copiados os valores da imagem.
- Fotografia: endereço HTTPS existente, sem novo alojamento de imagens. Retirar a associação preserva o ficheiro de origem e só é gravado ao guardar.
- Duplicar sugere um código livre nos artigos conhecidos; a API volta a validar a unicidade ao guardar, incluindo artigos fora da página atual.
- Atualizar consulta novos valores e estados de sincronização sem substituir o rascunho. Não há novas escritas nem sincronizações automáticas com o ERP.

## Validação

- Backend: testes existentes de inventário e fornecedores; testes de resumos reais, devoluções, limite de 30 dias, armazéns/permissões, serviços e encomendas parcialmente recebidas.
- Valores: `node scripts/test-article-values.cjs` verifica IVA, margens, dados desconhecidos, validação e duplicação canónica.
- Fluxos: `scripts/test-inventory-ui.py` cobre criação, edição, gravação, artigos ERP, preços, histórico, condições comerciais, lotes e proteção de rascunhos, com APIs fictícias interceptadas.
- Visual e interações: `scripts/test-article-layout-ui.py`, em 1440/1024/390 px e temas claro/escuro, cobre navegação, fotografia, indicadores, rascunhos, duplicação e pesquisa por barras. Capturas em `.local/article-layout` usam dados de teste.
- Compilação: `npm run build` no frontend.

## Ficheiros desta reformulação

- Ficha e apresentação: `frontend/src/ArticleForm.tsx`, `frontend/src/ArticleForm.css`, `frontend/src/components/RecordSheet.tsx` e `RecordSheet.css`.
- Integração com o catálogo e duplicação: `frontend/src/Inventory.tsx`, `ArticleGrid.tsx` e `articleValues.ts`.
- Consultas derivadas: `backend/app/inventory.py` e `backend/app/article_insights.py`.
- Validação: `backend/tests/test_article_insights.py`, `scripts/test-article-values.cjs`, `scripts/test-inventory-ui.py` e `scripts/test-article-layout-ui.py`.
- Documentação: este relatório e `docs/ARTIGOS-GRELHA-E-FICHA.md`.

Resultados: 29 testes existentes de inventário/fornecedores e 3 testes dos novos resumos passaram; verificação de cálculos/duplicação e compilação também passaram. Os fluxos de interface usam APIs fictícias interceptadas; não iniciam sincronizações reais nem alteram artigos de produção.

Os seis cenários visuais (1440/1024/390 px, claro/escuro) passaram, incluindo consulta sem gravação, fotografia, teclado, proteção de rascunhos, duplicação e pesquisa por barras. Os fluxos existentes de criação/edição, gravação, preços, fornecedores, lotes e artigos ERP também passaram. A API local foi verificada com HTTP 200 e executa código posterior à alteração da rota de detalhes.
