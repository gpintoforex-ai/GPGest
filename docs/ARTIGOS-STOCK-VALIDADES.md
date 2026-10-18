# Artigos, stock, validades e rentabilidade

Disponível em **Tabelas → Artigos e validades**. O catálogo começa vazio; a aplicação não cria artigos nem quantidades de demonstração.

## Começar no modo local

1. Em **Parâmetros**, crie os armazéns dos estabelecimentos e confirme o tratamento do IVA das compras. Configure os alertas e as permissões por utilizador.
2. Crie artigos ou importe um CSV em **Importações**. Associe as colunas, consulte os artigos e os erros, e confirme. A pré-visualização não cria artigos. Códigos e códigos de barras existentes são recusados; o CSV não substitui fichas nem altera stock.
3. Registe o **Saldo inicial**, com armazém, quantidade, custo conhecido e lote/validade quando aplicável. Deixe o custo vazio quando não o conhece: zero significa custo efetivamente zero.
4. Registe compras, vendas líquidas de descontos e IVA, devoluções, transferências, perdas e ajustes. Os valores não criam documentos fiscais nem lançamentos de tesouraria.
5. Consulte **Painel**, **Lotes e validades**, **Movimentos** e **Inventários**. Os filtros de armazém respeitam os estabelecimentos autorizados ao utilizador. O catálogo é comum à empresa; o custo médio é calculado ao nível da empresa.

Um leitor de códigos de barras que funcione como teclado pode preencher o campo de pesquisa do catálogo. Não foi acrescentada dependência de uma aplicação de câmara externa.

## Regras do registo de stock

- As quantidades são a soma dos movimentos confirmados. Não existe um campo de saldo editável na ficha.
- Cada movimento conserva artigo, unidade, armazém, lote, data do documento, instante de registo, utilizador, motivo, origem, documento, custo aplicado e referência de devolução quando indicada.
- Transferências produzem duas linhas com a mesma referência de operação, uma negativa e outra positiva. Não representam compra, venda ou margem.
- Movimentos confirmados não são apagados nem editados. Corrija por uma devolução ou ajuste justificado. Artigos com histórico são desativados, não eliminados.
- A unidade e o controlo de lotes/validades não mudam num artigo com histórico. A precisão não pode tornar as quantidades antigas irrepresentáveis.
- Stock negativo está bloqueado por defeito. A exceção exige simultaneamente configuração da empresa e permissão individual, e só é permitida em artigos sem lotes. Um saldo negativo deixa de ter custo médio calculável até o conjunto voltar a ser valorizável.
- As escritas do módulo usam uma transação e uma exclusão concorrente por empresa. A chave de operação impede repetições; reutilizar a mesma chave com conteúdo diferente gera conflito. Erros anulam toda a operação, incluindo as duas partes de uma transferência.

## Lotes e prazos

Um lote é identificado pelo artigo e pelo número e pode existir em vários armazéns. As entradas conservam fornecedor, documento, data e custo. A ficha do lote conserva os dados da primeira entrada; as compras posteriores com custos diferentes permanecem rastreáveis no histórico.

As saídas propõem **FEFO**, primeiro a validade mais próxima. Lotes sem data ficam por último. Escolher outro lote exige permissão específica e justificação. Inventários ajustam a posição contada, sem substituir o lote escolhido por FEFO.

Lotes expirados não podem sair por venda ou transferência normal, mesmo se a data do movimento for anterior. Podem ser tratados como perda, devolução ao fornecedor ou ajuste justificado. A data atual e a data do documento são consideradas; mudar de dia nunca remove stock. A entrada de uma devolução pode receber um lote expirado, que continua bloqueado para uma nova venda.

Os filtros distinguem dentro do prazo, a terminar, expirado e sem validade. Os alertas são configuráveis, inicialmente 90/60/30/7 dias. O risco inclui lotes positivos dentro do maior intervalo e os expirados; apresenta quantidade e valor ao custo médio atual, com indicação de custo desconhecido. Não há envio automático de notificações externas.

Alterar uma validade exige permissão e motivo, conservados na auditoria, e não altera quantidades.

## Inventário físico e concorrência

Uma sessão guarda o stock esperado **no instante de início**, por armazém, artigo e lote. Pode abranger categoria ou um artigo; o backend também aceita seleção de vários artigos.

Guarde o rascunho antes de confirmar, se necessário. Todas as posições precisam de contagem e as diferenças precisam de motivo. A confirmação usa:

`ajuste = contado − esperado no início`

Exemplo: esperado 10, contado 8, entrada posterior de 5 → ajuste −2 e saldo final 13. A entrada de 5 é preservada. A contagem deve referir-se ao instante inicial; se o utilizador já contou entradas posteriores, deve reconciliar a contagem antes de confirmar. Se o ajuste provocar stock negativo entretanto, a confirmação é recusada e é necessário rever a posição.

Uma segunda confirmação não cria novos ajustes. Novos lotes surgidos após o início não são artificialmente zerados pela sessão antiga.

## Custos, IVA e margem

Método local: **custo médio ponderado móvel**, pela ordem de registo das operações. A data do documento pode ser anterior, mas não reabre nem recalcula custos históricos.

`novo custo médio = (quantidade existente × custo médio + quantidade comprada × custo de entrada) ÷ quantidade total`

O custo das saídas é fixado no movimento. FEFO escolhe a mercadoria física; não transforma o método de custo em custo por lote. Transferências mantêm o conjunto de custo da empresa. Uma entrada sem custo torna o conjunto não calculável até esgotar esse conjunto; uma compra conhecida posterior não inventa o custo das unidades desconhecidas.

As compras são introduzidas sem IVA. Se o IVA não for recuperável na configuração do módulo, a entrada incorpora o IVA do artigo no custo. Esta escolha afeta novas entradas e não altera movimentos anteriores. Não foi criado um motor de afetação parcial de IVA por compra: se a empresa tiver regimes mistos, é necessária revisão dessa regra antes de usar os custos como informação contabilística.

- **Margem bruta (€)** = vendas líquidas cobertas − custo dos artigos vendidos.
- **Margem bruta (%)** = margem bruta ÷ venda líquida × 100.
- **Acréscimo (%)** = margem bruta ÷ custo × 100.
- **Contribuição conhecida** = margem bruta coberta − custos adicionais conhecidos − perdas conhecidas do período.

Devoluções de cliente ligadas a uma venda recuperam proporcionalmente o custo e o líquido dessa venda. A quantidade devolvida não pode exceder a original. Sem referência, o custo tem de ser indicado ou fica desconhecido. Uma devolução a fornecedor baixa stock ao custo médio; a referência à compra original limita a quantidade e preserva a rastreabilidade.

Quantidades usam até 6 casas decimais, custos aplicados até 8 e valores líquidos de movimento até 6. A apresentação monetária usa euros com duas casas. Divisão por zero ou custo desconhecido resulta em **Não calculável**. O painel separa vendas sem custo, margem coberta, custo desconhecido em stock/perdas e stock ERP indisponível. Margem e contribuição não são lucro líquido.

O painel distingue stock atual de resultados do período. Inclui reposição, validade, artigos sem vendas, maiores/menores margens e simulador de desconto. Reposição = diferença positiva entre o maior de nível de reposição e previsão de 30 dias, e o saldo atual. A previsão usa a média diária das vendas líquidas do período selecionado. Encomendas pendentes não estão disponíveis num contrato validado e são apresentadas como indisponíveis. As sugestões não encomendam nem mudam preços.

## ERP e SAF-T

**ERP:** o catálogo sincronizado existente é reutilizado por associação explícita. O artigo e preço continuam a ser consultados do registo ERP original. A base de IVA do preço não é presumida quando o conector não a identifica. A ficha ERP não aceita edição local ou movimentos de stock. O conector Wintouch atual não implementa quantidades de stock: são sempre **indisponíveis**, independentemente de a sincronização de catálogo terminar, falhar ou estar pendente. Não existe escrita automática para o ERP.

**SAF-T:** a importação fiscal existente continua a servir a análise de vendas sem gerar stock. A passagem para stock exige:

1. Saldo inicial e custos conhecidos.
2. Associação explícita código SAF-T → artigo local, em Importações.
3. Data de início do controlo, guardada pelo administrador.
4. Seleção do armazém e período, confirmação da política e confirmação das saídas.

Só são aplicadas vendas FT/FS/FR normais, com quantidade e líquido identificados, do estabelecimento do armazém e posteriores ao início de controlo. A operação é atómica. Artigos sem associação, artigos com lotes e notas de crédito exigem movimentos manuais; as linhas excluídas são indicadas. O SAF-T não fornece compras, stock inicial, lotes ou validades.

Documento/linha tem uma identidade estável entre ficheiros. A repetição não baixa stock novamente. Uma venda manual do mesmo documento/artigo impede a integração automática, e uma venda manual depois de integrar esse documento também é recusada. Use referências exatas de documento; diferenças apenas em espaços ou maiúsculas são normalizadas na comparação.

Uma versão alterada, anulada ou com linha removida sinaliza conflito, preservando os movimentos anteriores. **Rever conflito** regista utilizador, versão, decisão e justificação, sem nova saída nem reescrita do custo. Se houver correção física/financeira necessária, registe-a primeiro através dos movimentos próprios. Uma nova alteração da origem exige nova revisão.

## Permissões e persistência

Permissões distintas: consultar custos, alterar preços, gerir artigos/movimentos, confirmar inventários, gerir lotes, exceção de saldo negativo e alteração FEFO. Há uma operação de preço separada da gestão de stock. O administrador mantém todas; o gestor herda gestão sem exceção negativa; os restantes perfis começam sem escrita nem consulta de custos.

O backend valida permissões, empresa, armazéns e referências. Custos não autorizados não são apenas ocultados visualmente: são retirados das respostas. A configuração é isolada no esquema da empresa.

Migração **0044**, após 0043:

```powershell
Push-Location backend
..\.venv\Scripts\python.exe -m alembic upgrade head
Pop-Location
```

Use o procedimento habitual do projeto para carregar o perfil da base e o diretório Alembic. A migração só acrescenta `stock_articles`, `stock_warehouses`, `stock_lots`, `stock_entries`, `stock_inventories`, `stock_settings` e `stock_imports`, em todos os esquemas ativos. Novas empresas recebem as mesmas tabelas. A reinicialização de movimentos preserva artigos, armazéns e parâmetros e limpa movimentos, lotes, inventários e importações, mantendo o esquema anterior arquivado pelo mecanismo existente.

## Verificação automatizada

- `backend/tests/test_inventory.py`: custos diferentes, custo congelado, devoluções, perda, transferência, FEFO, expirados, custos e validades desconhecidos, IVA, denominadores zero, inventário concorrente, saídas simultâneas, permissões, CSV e repetição/revisão SAF-T.
- `backend/tests/test_inventory_tenants.py`: duas empresas, mesmo código, identificadores cruzados recusados.
- `scripts/test-inventory-ui.py`: APIs fictícias interceptadas; computador e 390 px; X/Escape, alterações por guardar, foco, validação sem perder dados, quantidade inicial 1 e edição de validade. Não escreve dados reais.
- Migração repetível e regressão da reinicialização operacional verificadas numa base descartável.

Os testes de backend exigem uma base PostgreSQL exclusiva de testes e `APP_MODE=demonstracao`, segundo as fixtures do projeto. Nunca executar testes de mutação contra a base real.
