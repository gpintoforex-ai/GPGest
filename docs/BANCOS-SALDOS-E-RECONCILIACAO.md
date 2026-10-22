# Bancos: saldos, movimentos e reconciliação

## Valores e datas

Em **Bancos → Múltiplas contas** e **Extratos**, o resumo distingue os seguintes valores por conta e moeda:

- **Saldo contabilístico — Saldo pelos registos da aplicação**: saldo inicial datado + entradas − saídas da conta até à data final. Não representa uma certificação da contabilidade oficial. Se faltar saldo inicial válido, ou a moeda/data não corresponderem, apresenta **Sem informação**.
- **Saldo bancário**: saldo constante do último movimento do extrato importado até à data final, acompanhado da data do movimento. Um extrato antigo não é apresentado como saldo atual. O desempate usa a data do movimento, a exportação e a posição da linha no extrato.
- **Diferença**: contabilístico − bancário, calculada apenas quando existem ambos os saldos, na mesma conta, moeda e data. Se o extrato for anterior, apresenta **Comparação indisponível** e permite selecionar a data desse extrato para comparar.
- **Por reconciliar na aplicação** e **no extrato**: número de registos e valor absoluto remanescente no período, com entradas e saídas separadas. Não são saldos disponíveis.
- **Movimentos reconciliados no período**: entradas, saídas e valor líquido efetivamente associado no extrato. A data utilizada é a do movimento bancário. Associações parciais contribuem apenas com o valor confirmado. A secção explica que o indicador não representa dinheiro disponível para gastar.

O saldo inicial representa o início da data de referência. É contabilizado uma vez; os movimentos dessa data são incluídos depois dele. Os saldos são acumulados até à data final, enquanto os pendentes/totais reconciliados respeitam o intervalo escolhido. Os cálculos monetários usam cêntimos inteiros, sem somas de números binários decimais.

Exemplo verificado: **1.000 € + 500 € − 200 € = 1.300 €**. Face a um saldo bancário de **1.250 € na mesma data**, a diferença é **50 €**. Confirmar a correspondência dos movimentos não altera os 1.300 € nem os 1.250 €.

O líquido por reconciliar pode diferir dessa diferença: datas de referência diferentes, movimentos ainda não registados e saldos iniciais incorretos são causas possíveis. O resumo não tenta corrigir os saldos através da reconciliação.

## Origem dos registos

O cálculo reutiliza os movimentos com conta bancária identificada, liquidações locais dirigidas à conta, depósitos entregues ao banco e registos bancários aprovados. Ligações entre pagamento e movimento de origem são tratadas como uma única operação económica. Os movimentos de estorno preservam o original e afetam o saldo na respetiva data.

Movimentos sem conta identificada não são atribuídos automaticamente a uma conta por serem reconciliados. A interface explica essa limitação. Os conectores atuais não disponibilizam um saldo da contabilidade oficial por conta: esses saldos ERP não são inventados a partir de extratos, faturação ou caixas. O resumo identifica explicitamente a origem local. Na previsão de liquidez, um saldo proveniente do extrato passa a estar identificado como **Bancário (extrato)**.

## TPA por liquidar

Recebimentos TPA (e MB WAY com terminal e prazo de liquidação) já registados na caixa não entram no saldo pelos registos até serem associados ao crédito do prestador; a parte associada entra na data da liquidação. O resumo apresenta **TPA por liquidar** em separado. Resolver um crédito como novo recebimento quando existem vendas TPA compatíveis por liquidar é assinalado como registo existente. Ver [faturação e recebimentos](FATURACAO-RECEBIMENTOS.md).

## Reconciliação

Mantêm-se as duas listas, sugestões e histórico existentes. Os depósitos registados no módulo de Depósitos aparecem como movimentos da aplicação na conta de destino; confirmar a associação ao crédito do extrato atualiza o estado do depósito (Reconciliado / Parcialmente reconciliado) e desfazê-la devolve-o a «Por reconciliar». Podem filtrar-se conta, período, estado, intervalo de valor absoluto e descrição/referência/entidade. Os registos sem conta identificada são assinalados pelo contexto e continuam sujeitos à conferência do utilizador.

As seleções mostram os totais de cada lado, a diferença bruta e a diferença ainda não explicada após comissão/diferença justificada. Totais em moedas diferentes não são apresentados como comparáveis. São suportadas associações um para um, um para vários, vários para um e parciais. A confirmação valida novamente os valores remanescentes, contas, moedas, permissões e conflitos concorrentes.

As sugestões comparam valor, data e referência. Não confirmam automaticamente casos ambíguos. As sugestões não misturam registos de contas identificadas diferentes nem moedas diferentes.

Uma comissão indicada no agrupamento é, por defeito, uma explicação da diferença. O registo financeiro da comissão exige a aprovação explícita da opção **Aprovo criar um movimento próprio de comissão**. Sem essa aprovação, confirmar uma reconciliação não cria receitas/despesas nem altera os saldos pelos registos da aplicação. Uma comissão já aprovada permanece como movimento real da aplicação se a associação for desfeita; a sua correção exige estorno explícito, em vez de eliminação silenciosa.

O histórico conserva utilizador, data, movimentos e montantes. **Desfazer** exige permissão e motivo e preserva os registos originais.

## Movimentos em falta

Num movimento bancário pendente, **Resolver movimento em falta** permite preparar recebimento, pagamento, comissão, juros, associação a documento existente ou transferência própria. Também pode manter-se pendente.

A pré-visualização mostra a conta, data, montante, descrição e, numa transferência própria, ambas as pernas. Procura registos existentes antes de criar outro; o utilizador pode selecionar um deles ou declarar explicitamente que o novo registo é distinto. Uma pré-visualização desatualizada é rejeitada. Uma submissão repetida não duplica o registo.

Só **Aprovar registo e associação** cria registos na aplicação e associa o remanescente do extrato. É uma ação diferente de confirmar correspondências entre movimentos já existentes. Não envia dinheiro nem comunica ordens de transferência a um banco.

Uma transferência própria cria duas pernas relacionadas e opostas, na mesma moeda, sem classificação como receita/despesa. Não existe conversão cambial automática. Pagamentos e encargos utilizam também os mecanismos existentes de pagamentos/documentos, sem duplicar saídas de caixa/banco. Um pagamento associado a uma fatura pode ser parcial e atualiza o respetivo saldo documental.

Os registos bancários aprovados não têm edição ou eliminação livre. Depois de desfeitas as associações, **Estornar com motivo** cria um movimento inverso datado e mantém o original. Transferências próprias devem ser corrigidas por uma transferência inversa completa, não por uma perna isolada.

## Migração e validação

A migração **0047** cria `bank_book_entries`, o registo auditável de operações bancárias aprovadas que não dependem de uma sessão de caixa. Reutiliza contas, referências de saldo inicial, extratos, reconciliações, pagamentos e documentos existentes. Importar um extrato continua a criar apenas movimentos bancários de consulta; não cria registos financeiros da aplicação.

Testes em base temporária cobrem fórmulas, diferença de 50 €, ausência de informação, datas/moedas diferentes, reconciliação sem alteração dos saldos, parciais, reversão das associações, comissão aprovada, pré-visualização e duplicados, transferências próprias, pagamento documental, estorno datado, extrato repetido e migração. Incluem-se regressões de reconciliação, depósitos, edição de contas, BPI, Viva Wallet e liquidez.

O teste de interface `scripts/test-bank-balances-ui.py` usa apenas APIs simuladas e verifica cartões, valores, filtros, pré-visualização, aviso de duplicação, seleção de registo existente, temas claro/escuro, contraste e telemóvel.
