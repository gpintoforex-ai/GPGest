# Centro de reconciliação bancária

## Diagnóstico e implementação

A aplicação já tinha reconciliação explícita, importadores BPI e Viva Wallet, associações parciais e agrupadas, transferências, comissões, reversões e integração dos pagamentos Wintouch. O centro reutiliza essas operações e os componentes `RecordGroup`, `RecordIndicator`, `StatusBadge`, `AppDialog` e a navegação existentes.

Implementação por módulo:

1. Grelha do extrato como entrada principal, conta/período, filtros, indicadores, progresso e evolução mensal.
2. Importação configurável CSV/XLSX e CAMT.053, preservando os importadores bancários existentes.
3. Propostas determinísticas, tolerância configurável, agrupamentos, classificação e conflitos.
4. Revisão e confirmação pelo motor existente; rejeição justificada e auditada.
5. Fecho por conta/período, relatórios e reabertura com histórico.
6. Validação em PostgreSQL isolado e navegador, incluindo computador, tablet e telemóvel.

## Utilização

Em **Financeiro → Reconciliação bancária**, selecionar a conta e o período. Importar um extrato exige pré-visualização e confirmação. CSV e Excel genéricos permitem escolher as colunas, o separador decimal e a ordem das linhas. BPI Net Empresas e Viva Wallet mantêm os seus leitores originais. CAMT.053 deve identificar uma única conta e moeda, com movimentos contabilizados.

As propostas explicam os critérios. **Rever e aceitar** abre a seleção e os valores para confirmação; abrir uma proposta nunca a confirma. A seleção manual permite associações 1:1, 1:n, n:1 e n:n, pagamentos parciais, transferências e diferenças justificadas. Não são criados movimentos no ERP. Um lançamento local em falta ou de comissão exige aprovação específica nas operações existentes.

Na grelha, a coluna **Correspondência (ERP / ZELA)** identifica o documento: a referência e, por baixo, a origem, a entidade (fornecedor ou cliente), a data e o valor. A mesma identificação aparece em cada proposta de **Ver propostas**, com a confiança, e nos movimentos já conciliados. Uma proposta que junta várias faturas mostra o fornecedor comum e cada fatura com a referência, a data e o valor a associar.

### Aceitar sugestões em conjunto

- **Aceitar sugestões (N)**, na barra da seleção, aceita de uma vez a melhor proposta de cada movimento selecionado.
- **Aceitar sugestões fiáveis** seleciona apenas as correspondências exatas ou de confiança alta (80 ou mais), que cumpram duas condições: nenhuma alternativa a menos de 20 pontos e um documento que não seja a melhor proposta de outro movimento.
- Nos dois casos abre uma revisão com o movimento, o documento proposto, a confiança e as alternativas. Só depois de confirmar é que algo é aceite, e qualquer linha pode ser desmarcada.
- Os agrupamentos sem comissão, cuja soma é igual ao movimento (por exemplo, um débito direto que paga duas faturas da EDP), também são aceites em conjunto, com todas as faturas do agrupamento.
- Ficam de fora, para revisão individual:
  - propostas com valores diferentes (pagamento parcial ou comissão);
  - agrupamentos com comissão ou diferença, e propostas com vários movimentos do extrato;
  - transferências que exijam justificação;
  - um documento já proposto para outro movimento da mesma seleção.
- Cada linha é confirmada como uma aceitação individual: as validações do servidor são as mesmas e cada uma fica na auditoria. Se uma falhar (por exemplo, porque os valores mudaram entretanto), as restantes mantêm-se e a mensagem indica qual não foi aceite.

### Entidade na descrição bancária

- **Valor diferente** (parcial ou comissão): só há proposta quando a descrição contém a referência do documento ou o nome da entidade. Datas próximas não chegam.
- **Outra entidade conhecida** (por exemplo, o extrato diz «BIOCEUTICA»): não são propostos registos de outros fornecedores ou clientes, mesmo com o valor igual. A exceção é quando a referência do documento aparece na descrição.
- **Como se compara o nome:** a entidade é reconhecida pelas palavras do nome. Não contam as formas jurídicas («Lda», «Unipessoal»), as palavras genéricas («Farmácia», «Produtos») nem os processadores de pagamento («IFTHENPAY», «HIPAY», «PAGSERV»).
- **Agrupamentos:** uma soma de faturas nunca junta fornecedores diferentes.
- **Débito direto de várias faturas** (por exemplo, eletricidade e serviço da EDP): a soma de 2 ou 3 faturas também é procurada fora da tolerância, até um ano antes do movimento. Só entram faturas do fornecedor referido na descrição bancária, começando pelas mais recentes. Faturas do mesmo dia somam mais 10 pontos.
- **Faturas pagas mais tarde:** a tolerância de datas da conta (10 dias por omissão) não se aplica a uma fatura de fornecedor quando se cumprem quatro condições. O valor é igual; a descrição bancária refere a entidade ou a referência; o pagamento é posterior à fatura; e a diferença é de no máximo um ano. A proposta indica a data da fatura e quantos dias depois foi paga. Pagamentos até 45 dias depois da fatura (débito direto ou pagamento a 30 dias) recebem mais 10 pontos, para a fatura mais recente aparecer primeiro.
- **Marcas curtas:** a primeira palavra do nome com 3 letras (MEO, EDP, NOS, CTT) também identifica a entidade, por exemplo em «DD MEO, SA».
- **O período escolhido no ecrã** apenas filtra a lista de movimentos e não limita a procura de documentos para as sugestões.

### Filtrar por valor

- **Pesquisa:** também aceita um valor («60», «60,00» ou «1.234,56»). Encontra os movimentos com esse montante, ou com esse valor ainda por associar, além das descrições e referências que contenham o texto.
- **Valor mínimo e Valor máximo:** em «Tipo de movimento» limitam a lista pelo montante absoluto, tanto de entradas como de saídas.

Uma correspondência exata exige valor disponível, referência identificável e conta explícita consistentes. Uma fatura com valor coincidente permanece uma proposta provável. Alternativas concorrentes são conflitos. Rejeitar exige motivo e não altera os saldos; uma alteração económica posterior pode produzir uma nova proposta. Associações anteriores e os seus remanescentes são respeitados.

### Movimentos sem documento (impostos, vencimentos, encargos, transferências)

- **Resolver:** na grelha, um movimento sem proposta tem o botão **Resolver**, que abre a janela «Resolver movimento bancário em falta». Os tratamentos são:
  - pagamento ou recebimento, opcionalmente com documento;
  - comissão bancária;
  - juros;
  - transferência entre contas próprias.
- **Rubrica, funcionário e custo** (migração **0077**): sem documento, o registo pode ter a rubrica (Impostos, Segurança Social, Pessoal…) e, nos pagamentos, o funcionário.
  - **Conta como custo nos mapas de gestão e no Dashboard:** só nos pagamentos e comissões com rubrica.
  - **Quando desmarcar:** nos pagamentos que não são custo, como o IVA, o IRS retido ou o capital de empréstimos.
  - **Transferências entre contas próprias:** nunca têm rubrica nem custo.
- **Confirmação:** o registo continua a exigir pré-visualização, procura de registos parecidos (para não duplicar), confirmação e auditoria. Fica associado ao movimento do extrato.
- **Regras por descrição** (tabela `bank_rules`):
  - **Criar a partir de um movimento:** ao resolver, «Guardar como regra para os próximos movimentos com o mesmo texto» guarda o tratamento. O texto proposto são as primeiras palavras da descrição, até à primeira com algarismos (por exemplo «DD SEG SOCIAL»).
  - **Âmbito:** por omissão, a regra aplica-se só à conta do movimento.
  - **Gerir:** em **Ações rápidas → Regras por descrição** criam-se, alteram-se, desativam-se e removem-se regras. Cada alteração fica na auditoria (`banco_regra_criada`, `banco_regra_alterada`, `banco_regra_removida`).
- **Sugestões por regra:**
  - **Correspondência:** quando a descrição de um movimento por reconciliar contém o texto de uma regra ativa da mesma conta (ou de todas as contas) e o sentido coincide, a ZELA propõe o tratamento.
  - **Prioridade:** a regra só é proposta quando não há proposta de documento. Com várias regras, ganha a de texto mais longo.
  - **Registar:** o botão abre a janela «Resolver» preenchida.
  - **Em conjunto:** estas sugestões também são aceites em «Aceitar sugestões» e «Aceitar sugestões fiáveis», pelo mesmo circuito de pré-visualização e confirmação. Se existirem registos parecidos na aplicação, o movimento fica para revisão individual.
- **Mapas:** os pagamentos e comissões com rubrica e marcados como custo entram nos mapas de gestão (Custos e proveitos), nas Despesas por rubrica do Dashboard e no ecrã de despesas por rubrica, pela data do movimento e sem IVA. Ver [MAPAS-DE-GESTAO.md](MAPAS-DE-GESTAO.md).

## Integridade e permissões

- Conta, loja, moeda, sentido, disponibilidade e estado da origem são validados no servidor. Documentos associados a origens ERP anuladas e recebimentos ERP inativos não são elegíveis.
- O motor existente bloqueia os registos durante a confirmação e impede dupla utilização do mesmo pagamento, incluindo os aliases de pagamentos de documentos.
- O conteúdo original das novas importações é cifrado com a infraestrutura existente, com SHA-256 verificado no download e acesso limitado à conta autorizada. Importações antigas sem conteúdo guardado não apresentam um download fictício.
- Os fingerprints preservam a multiplicidade de linhas economicamente idênticas. A deteção entre formatos também compara data, descrição, valor, referência compatível e saldo fornecido. Saldos desconhecidos permanecem `null`.
- Consulta e relatórios respeitam os acessos às lojas. Importação, confirmação, rejeição, regras, fecho e reabertura requerem gestor ou administrador. Fecho excecional com pendências requer administrador e justificação detalhada.

## Saldos e fecho

O saldo pelos registos da aplicação resulta da referência inicial configurada e dos movimentos com conta identificada. Não é apresentado como um saldo contabilístico oficial do ERP. Só existe comparação quando conta, moeda e data final são comparáveis; falta de informação nunca é representada por zero.

O saldo inicial inferido do extrato resulta apenas de uma sequência de movimentos importados com saldos contínuos. Não substitui um saldo inicial oficial independente, nem garante que tenham sido importados todos os movimentos bancários do período.

O fecho guarda um snapshot com assinatura dos dados. O servidor recalcula os valores, verifica a assinatura e impede sobreposição de períodos fechados. Não permite fecho futuro ou sem saldos comparáveis. Diferenças por resolver e movimentos bancários pendentes bloqueiam o fecho normal; uma exceção administrativa fica na auditoria.

Associações, reversões e novas linhas de extrato num período fechado exigem reabertura. O mesmo controlo abrange associações legadas de compras e depósitos. Alterações posteriores nas origens, incluindo sincronização ERP ou correções de tesouraria, tornam o snapshot desatualizado: o original mantém-se preservado e o ecrã pede nova conferência. O fecho da reconciliação não congela o ERP.

Relatórios PDF/CSV incluem conta, período, moeda, origem dos saldos, pendências, trânsito, reconciliações e justificações. O relatório do fecho usa o snapshot, mesmo quando os dados atuais mudaram. O CSV protege campos textuais contra execução como fórmulas.

## Ficheiros principais

- Backend: `reconciliation.py`, `reconciliation_matching.py`, `reconciliation_center.py`, `statement_import.py`, `banks.py`, `bank_balances.py`, `purchases.py`, `deposits.py`, `models.py` e registo do router em `main.py`.
- Migração aditiva: `backend/migrations/versions/0075_reconciliation_center.py`.
- Frontend: `Reconciliation.tsx`, `ReconciliationDashboard.tsx`, `BankReconciliationPeriod.tsx`, `BankStatements.tsx`, `Reconciliation.css`, `navigationCatalog.ts` e o tom de sugestão em `components/RecordSheet.css`.
- Validação nova: `backend/tests/test_reconciliation_center.py`, `scripts/test-reconciliation-center-ui.py`; regressão funcional em `scripts/test-reconciliation-ui.py`.

## Validação e limites

Os testes abrangem importação repetida, linhas iguais, mapeamento, separadores decimais, moeda incorreta, ficheiro original e permissões, CAMT, propostas concorrentes, referência e conta explícitas, documentos anulados, agrupamentos e parciais, transferências, comissões, reversões, fecho concorrente, assinatura desatualizada, exceção administrativa, relatórios e reabertura. São ainda executadas as regressões de depósitos, pagamentos sincronizados e falha de sincronização com rollback.

A pesquisa automática de somas tem limites explícitos: até 200 movimentos/candidatos, 12 candidatos por combinação, grupos de dois ou três registos internos e pares de linhas bancárias. O ecrã avisa quando a pesquisa é limitada. A seleção manual continua disponível; a pesquisa não promete resolver todas as combinações.

OFX/MT940 e formatos proprietários adicionais precisam de leitores específicos e amostras de validação. O ficheiro Excel genérico usa a primeira folha e recusa fórmulas; Excel legado `.xls` é suportado pelo leitor Viva existente, não por um leitor genérico. Não há escrita financeira automática no ERP nem um serviço de IA a tomar decisões de reconciliação.

O cálculo determinístico usa os espelhos locais existentes da integração: uma falha de comunicação não cria registos fictícios nem substitui dados por zero. A atualização/sincronização ERP mantém a sua própria operação e histórico existentes.

### Verificação desta entrega

A composição visual foi posteriormente corrigida para seguir a imagem de referência: cabeçalho compacto com ícone bancário, conta e quatro indicadores na mesma faixa, filtros com ícones à esquerda, separadores e pesquisa integrados na grelha, seleção de linhas, ações contextuais, gráfico circular e cartões de evolução mensal/ações rápidas lado a lado. O fecho detalhado mantém-se disponível numa secção expansível. A entrada pelo menu abre explicitamente este painel, mesmo depois de uma consulta às vistas de seleção manual ou histórico.

Os testes visuais verificam também a posição da grelha perto do topo, o alinhamento dos três cartões no computador, a abertura do fecho e a passagem da seleção múltipla para a revisão sem gravação automática. As capturas de teste usam dados de demonstração identificados, sem inserir dados na instalação do utilizador.

A grelha tem deslocação interna e cabeçalho fixo. Foi validada com 64 movimentos de demonstração para manter os gráficos e as ações rápidas próximos, em vez de alongar a página por dezenas de linhas. A compilação TypeScript/Vite, os testes de navegação, o teste funcional completo e os testes visuais em 1440/768/390 píxeis passaram após esta correção.

- 92 testes PostgreSQL passaram: reconciliação, centro/fecho, saldos, importadores BPI/Viva, depósitos, pagamentos sincronizados, liquidação Wintouch e rollback após falha de sincronização.
- `scripts/test-reconciliation-center-ui.py` passou em 1440, 768 e 390 píxeis: grelha, conta/período, pesquisa, filtros e revisão de agrupamento sem confirmação automática.
- `scripts/test-reconciliation-ui.py` passou com backend e esquema PostgreSQL próprios: confirmar, justificar, desfazer, ignorar/reativar, importar faturas e usar em telemóvel.
- `scripts/test-reconciliation-batch-ui.py` (1440 e 390 píxeis, APIs simuladas): identificação da entidade, sugestões fiáveis, exclusões da aceitação em conjunto, confirmação linha a linha e falha parcial.
- `backend/tests/test_bank_rules.py`: rubrica, funcionário, custo e regra na resolução; sugestões por regra (sentido, conta, texto mais longo, regra inativa); gestão e auditoria das regras; custos sem documento nos mapas e nas despesas por rubrica.
- `scripts/test-reconciliation-rules-ui.py` (1440 e 390 píxeis, APIs simuladas): registar pela regra, Resolver com «Guardar como regra», aceitação em conjunto pelas regras e gestão das regras.
- Compilação TypeScript/Vite passou; permanece o aviso de tamanho de alguns bundles.
- O verificador global `npm run check:ui` ainda assinala elementos nativos preexistentes em `Assistant.tsx`, `WebshopSettings.tsx` e no indicador partilhado `RecordSheet.tsx`. Não assinalou os novos módulos de reconciliação. Estas ocorrências não foram substituídas nesta alteração.
- Migração 0075 aplicada nos três esquemas da instalação local; servidor reiniciado e saúde verificada.
