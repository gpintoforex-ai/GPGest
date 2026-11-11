# Natureza das rubricas: custos, compras, obrigações fiscais e tesouraria

Nem todas as saídas de dinheiro são custos. Um pagamento de IVA ao Estado é uma **saída de tesouraria** e o
cumprimento de uma **obrigação fiscal**, mas não é um custo operacional: o IVA liquidado nas vendas foi recebido do
cliente por conta do Estado. A natureza de cada rubrica decide em que análise o valor aparece. Os documentos de compra,
os registos bancários, os movimentos e os dados do ERP **nunca são alterados**.

## Configuração (Configurações → Rubricas → Natureza financeira)

Cada rubrica tem uma natureza (por definir, custo operacional, custo com pessoal, compra de mercadorias, investimento,
obrigação fiscal, movimento financeiro) e cinco indicadores:

| Indicador | Onde conta |
|---|---|
| Custos e despesas operacionais | Cartão principal do Dashboard, evolução de vendas e custos, mapa de custos e proveitos |
| Análise de compras e investimentos | Vista «Compras e investimentos» e mapa de custos e proveitos (margem indicativa) |
| Saídas de tesouraria | Vista «Saídas de tesouraria» (pagamentos efetivos por rubrica) |
| Obrigações fiscais | Vista «Obrigações fiscais» (pagamentos ao Estado), pelo valor pago |
| Orçamento de pagamentos | Realizado do orçamento e associação ao orçamento |

- **Por definir** mantém o comportamento anterior: entra nos custos operacionais, na tesouraria e no orçamento. Por
  isso a migração 0087 não muda nenhum número.
- Escolher a natureza propõe os indicadores habituais (por exemplo, obrigação fiscal: fora dos custos operacionais,
  dentro das obrigações fiscais, da tesouraria e do orçamento); cada indicador pode ser ajustado.
- **Sugestões:** rubricas chamadas «IVA» → obrigação fiscal; «Mercadorias» → compra de mercadorias. São só propostas.
  A rubrica «Impostos» (TSU, retenções na fonte, imposto do selo) fica nos custos operacionais: são encargos da
  atividade e de pessoal.

## Simulação e aprovação

1. Altere a natureza ou os indicadores (as linhas alteradas ficam assinaladas) e escolha **Simular**.
2. A simulação mostra, nos últimos 12 meses, o total **antes e depois** de cada vista, do mapa de custos e proveitos e
   do realizado do orçamento do ano, os documentos e movimentos afetados e o que não muda.
3. Só um **administrador** aplica, com motivo. Se a configuração das rubricas mudou entretanto, a aplicação é recusada
   e é preciso simular de novo. Cada rubrica alterada fica na auditoria (`rubrica_natureza_alterada`) com o antes, o
   depois, o motivo e o resumo da simulação.

O gestor pode simular e partilhar o resultado, mas não aplica.

## Vistas no Dashboard

O cartão «Custos, compras e obrigações fiscais» tem quatro vistas, cada uma com a origem dos valores e detalhe até ao
documento ou movimento:

- **Custos e despesas operacionais:** documentos de compra e custos sem documento registados no banco; regime normal =
  base + IVA não dedutível estimado.
- **Compras e investimentos:** documentos de compra das rubricas de compras (o custo das mercadorias vendidas continua a
  vir das margens).
- **Obrigações fiscais:** pagamentos ao Estado pelo valor pago e, no regime normal, o IVA do período: liquidado nas
  vendas, suportado, dedutível estimado e não dedutível (este já está nos custos operacionais).
- **Saídas de tesouraria:** pagamentos efetivos pela data do pagamento (caixa a fornecedores, despesas e saídas;
  pagamentos e encargos do livro bancário), sem transferências internas nem estornos.

## O que não muda

Previsão de liquidez e prazos de pagamento, extratos, livro bancário e reconciliação, documentos e dados do ERP, Mapa
de IVA e previsão de IVA (calculados pelos documentos de venda e compra).

## API

- `GET /api/rubric-nature` — rubricas, naturezas, indicadores e sugestões (administrador e gestor).
- `POST /api/rubric-nature/simulate` — `{changes: [{category_id, nature, in_operating_costs, in_purchases,
  in_treasury, in_fiscal, in_payment_budget}]}`; não grava nada.
- `POST /api/rubric-nature/apply` — as mesmas alterações, `fingerprint` da simulação e `reason` (administrador).
- `GET /api/dashboard/expenses/drilldown?view=operating|purchases|fiscal|treasury` — detalhe por vista.

Testes: `backend/tests/test_rubric_nature.py` e `scripts/test-rubric-nature-ui.py`.
