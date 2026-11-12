# Faturação, recebimentos e conferência

Em **Vendas → Faturação e recebimentos** há duas áreas: **Análise comercial** (modo local e modo integrado) e
**Conferência** (só no modo local, descrita mais abaixo).

## Análise comercial: clientes → documentos → artigos → recebimentos

Ferramenta de consulta (não altera faturas nem dados sincronizados; nada é registado na auditoria porque nada é
escrito). Responde a: quem comprou, o que comprou, em que documento, por quanto, quanto pagou e quanto falta receber.

- **Origem dos dados:** a mesma do Dashboard. No modo local, o SAF-T de vendas importado (cabeçalhos, linhas,
  clientes, produtos e recibos); no modo integrado, as vendas Wintouch sincronizadas (documentos, linhas quando a
  sincronização as inclui, pagamentos, recibos e saldos em aberto). Cada documento é identificado pela origem, tipo,
  série e número (SAF-T) ou pelo identificador do ERP; consultar não cria nem duplica documentos.
- **Indicadores** (todos clicáveis): faturação sem e com IVA, recebido, por receber, vencido, documentos emitidos,
  clientes com faturação, ticket médio (valor sem IVA das faturas ÷ número de faturas) e prazo médio de recebimento
  (dias entre o documento e o recebimento, ponderado pelo valor). Notas de crédito subtraem na faturação; anulados ficam
  fora dos totais.
- **Recebido de cada documento («melhor fonte, sem somar»):** no modo local, os recibos do SAF-T que liquidam o
  documento e o pagamento indicado no próprio documento (FR/FS) ou, se for maior, o justificado na conferência com os
  recebimentos registados no GPGest — as duas fontes descrevem o mesmo dinheiro e nunca se somam. No modo integrado,
  total − saldo em aberto no ERP (última sincronização). Uma entrada bancária nunca é faturação adicional.
- **Estados:** pago (verde), parcialmente pago (azul), pendente (âmbar), vencido (vermelho), anulado (cinzento) e nota
  de crédito. **Vencido** só quando há data de vencimento: a do ERP ou a data do documento + prazo de pagamento da
  ficha do cliente (Clientes → prazo); sem prazo, fica «Pendente».
- **Gráficos:** faturação e recebimentos por mês (clique → documentos do mês), clientes com mais faturação (→ ficha
  resumida), artigos mais vendidos (→ documentos e clientes compradores) e valores por estado (→ documentos).
- **Pesquisa e filtros:** uma pesquisa por cliente, código, NIF, número do documento, código ou descrição do artigo e
  valor; estado do pagamento; o período e o estabelecimento do topo. Separadores **Documentos**, **Clientes**,
  **Artigos** e **Recebimentos**, com ordenação por coluna e cabeçalho fixo.
- **Painel único com histórico:** clicar num cliente, documento, artigo ou recebimento abre um painel lateral com
  **Voltar** e percurso (pode passar a janela ou ecrã inteiro).
  - **Cliente:** código, NIF, contactos, faturação, recebido, por receber, vencido, documentos e última compra;
    separadores Documentos, Artigos comprados (quantidade, documentos, valor, preço médio, último preço, última
    compra), Evolução das compras (12 meses) e **Deixou de comprar** (artigos dos 12 meses anteriores sem compras no
    período); ações para a ficha completa e para filtrar documentos, artigos e recebimentos.
  - **Documento:** separadores **Resumo** (tipo, número, datas, cliente, NIF, morada, condições, vencimento, valores,
    descontos, IVA, recebido, em dívida e estado), **Artigos** (todas as linhas, com pesquisa; o código abre o artigo e
    «Ficha do artigo» abre a ficha existente), **Recebimentos** (data, origem, meio, conta ou caixa, referência,
    valor e reconciliação; um recibo de várias faturas aparece como tal) e **Documento original** (o PDF do Arquivo
    digital, quando existe; caso contrário, um aviso — nunca se gera um documento que pareça o original).
  - **Artigo:** família, marca, stock atual (se houver ficha associada), último preço e preço médio de venda, custo
    de aquisição e margem estimada (só com custo conhecido; nunca inventados), documentos e clientes compradores.
  - **Recebimento:** dados do recebimento e os documentos que liquida.
- **Regressar sem perder nada:** ao abrir a ficha do artigo ou do cliente, os filtros e o percurso do painel ficam
  guardados na sessão; ao voltar a Faturação e recebimentos, o painel reabre no mesmo ponto.
- **Modo integrado:** sem linhas sincronizadas, o separador Artigos explica-o; o preço unitário das linhas do ERP é
  o valor sem IVA ÷ quantidade e a taxa de IVA por linha não vem na sincronização.

API (só leitura): `GET /api/sales/overview`, `/documents`, `/customers`, `/articles`, `/receipts` e
`/detail?view=document|customer|article|receipt` (todas com `start`, `end` e `store_id`). Testes:
`backend/tests/test_sales_explorer.py` e `scripts/test-sales-explorer-ui.py`.

## Conferência (modo local)

No modo local autónomo, a área **Conferência** trata três conceitos separados:

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
