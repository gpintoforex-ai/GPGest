# Faturação, recebimentos e conferência

Em **Tesouraria → Faturação e recebimentos** (modo local autónomo), a aplicação trata três conceitos separados:

| Conceito | O que é | Origem |
|---|---|---|
| **Faturação** | Documentos de venda: totais com e sem IVA, estados e notas de crédito. | Importação SAF-T (PT) |
| **Movimentos reais** | Recebimentos, pagamentos e transferências efetivamente registados. | Caixas, fechos locais, lançamentos manuais, registos bancários aprovados |
| **Conferência** | Associações entre documentos e recebimentos já existentes, com valores justificados e pendentes. | Confirmação do utilizador |

Um documento de venda **nunca** cria um recebimento. Associar registos existentes **nunca** cria movimentos.

## Importação SAF-T

O único modo é **Importar para análise de faturação e conferência**: importa documentos, clientes e artigos, preserva o ficheiro, a origem e o histórico, e não gera entradas em caixa, banco ou movimentos reais. Não substitui o realizado das caixas nem movimenta stock (o stock SAF-T tem aplicação própria, explícita). Reimportar o mesmo conteúdo reutiliza a importação; períodos sobrepostos são resolvidos por identidade do documento, com revisão de conflitos. Os pagamentos indicados no ficheiro ficam apenas como apoio à conferência.

## Indicadores

O painel e o topo da conferência mostram, sempre em separado:

- **Faturação líquida de IVA** e **Total faturado com IVA**: documentos normais (FT, FS, FR, ND, NC) pela data do documento; notas de crédito subtraem; anulados e outros estados são excluídos.
- **Recebimentos do período**: recebimentos menos reembolsos, pela data do movimento.
- **Pagamentos do período**: fornecedores, despesas e encargos bancários.
- **Saldos**: numerário das caixas abertas e bancos pelos registos da aplicação.
- **TPA / MB WAY por liquidar**: recebimentos de cartão já registados que aguardam o prestador.
- **Em conferência**: valor dos documentos ainda por justificar.

**Por justificar/conferir** = total faturado com IVA − recebimentos líquidos (mesma base, com IVA). Pode resultar de vendas a crédito, recebimentos de períodos anteriores, adiantamentos, diferenças de datas ou dados em falta. Não é, por si só, dívida de clientes nem erro de caixa.

Exemplo: SAF-T com 1.000 € faturados e caixa com 900 € recebidos mostra 1.000 € de faturação, 900 € de recebimentos e 100 € por justificar/conferir, sem criar entradas.

## Associar documentos a recebimentos

Em **Documentos** e **Recebimentos**, indique o valor a justificar de cada documento e o valor a associar de cada recebimento; os dois totais têm de ser iguais. Suporta:

- um documento com vários recebimentos;
- vários documentos com um movimento agregado do fecho de caixa;
- valores parciais (o remanescente fica por justificar).

Faturas associam-se a recebimentos; notas de crédito a reembolsos. Documentos anulados não podem ser associados. Valores coincidentes, sozinhos, não bastam: sem outra evidência (número do documento no recebimento, ou data e meio iguais ao pagamento indicado no SAF-T) é obrigatório indicar a evidência nas notas. Os critérios usados ficam guardados. Um recebimento associado não pode ser estornado nem o seu fecho reaberto sem desfazer primeiro a conferência.

**Sugestões** mostram propostas com critérios; nada é associado automaticamente. **Rever e associar** apenas prepara a seleção.

## Totais diários

Compara, por dia, estabelecimento e meio, os pagamentos indicados no SAF-T com os recebimentos registados. **Confirmar totais** regista uma conferência agregada; uma diferença exige justificação. A conferência agregada **não** marca cada documento como recebido: os documentos ficam como *Conferido em total diário*, com o valor ainda por justificar individualmente.

## Rever possíveis recebimentos em falta

Para documentos com pagamento indicado no SAF-T, mostra documento, datas, valor, meio e caixa de destino. Antes de propor, procura recebimentos individuais compatíveis, totais do dia já registados no mesmo meio, conferências agregadas e associações anteriores, e apresenta os avisos. Sem data de pagamento ou com código não associado a um meio, não propõe (não assume numerário nem usa a data da fatura). **Registar recebimento** exige uma caixa aberta, confirmação explícita e, havendo avisos, a declaração de que é um recebimento distinto. O recebimento fica com a data de hoje (regra das caixas); a data de pagamento do SAF-T fica nas notas. Repetir o pedido não duplica.

## TPA, MB WAY e bancos

Um recebimento por TPA é registado uma vez, na caixa. Fica **por liquidar** até ser associado, na reconciliação, ao crédito do prestador no extrato; só então entra no saldo bancário pelos registos, na data da liquidação. A comissão é explicada na reconciliação e só se torna movimento com a aprovação explícita já existente. Resolver um crédito bancário como novo recebimento quando existem vendas TPA compatíveis por liquidar é assinalado como possível duplicação e exige confirmação. O mesmo princípio aplica-se ao MB WAY quando existe terminal MB WAY com prazo de liquidação. Um terminal configurado com 0 dias de liquidação torna o cartão imediato.

Nos **Movimentos reais**, as linhas de extrato aparecem apenas para consulta, fora dos totais: o mesmo dinheiro já está nos movimentos registados (ex.: venda TPA) ou deve ser registado em Bancos → Extratos.

## Possíveis duplicados históricos

**Possíveis duplicados** pré-visualiza, sem alterar nada: créditos bancários aprovados como recebimento compatíveis com vendas TPA já registadas; recebimentos idênticos (mesma caixa, dia, meio, valor e documento); recebimentos manuais iguais a movimentos ERP importados. Verifica também que nenhuma importação SAF-T gerou movimentos. **Corrigir por estorno** exige permissão, motivo e confirmação, e usa o estorno existente: o original e o estorno ficam no histórico. Movimentos reconciliados, em conferência ou em caixas fechadas exigem primeiro desfazer a associação ou reabrir a caixa.

## Dados e migração

A migração **0048** cria `billing_conferences` e `billing_conference_lines`. Não altera nem apaga dados existentes. O saldo bancário pelos registos passa a excluir recebimentos TPA ainda não liquidados (apresentados em separado); é uma regra de cálculo, sem alteração de registos.
