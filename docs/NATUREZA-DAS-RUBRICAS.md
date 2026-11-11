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
- **Sugestões** (só propostas, pelo nome da rubrica): «IVA» → obrigação fiscal; «Mercadorias» → compra de mercadorias;
  «Imobilizado», «Investimentos», «Equipamento», «Obras» → investimento; «Transferências», «Fundos», «Suprimentos»,
  «Empréstimos», «Financiamento» → movimento financeiro; «Vencimentos», «Salários», «Ordenados», «Remunerações» →
  custo com pessoal (os números não mudam). «Retenções…» → obrigação fiscal (a entrega ao Estado não é custo novo). A rubrica «Impostos» (TSU, retenções na fonte, imposto do selo) fica nos
  custos operacionais: são encargos da atividade e de pessoal.

## Simulação e aprovação

1. Altere a natureza ou os indicadores (as linhas alteradas ficam assinaladas) e escolha **Simular**.
2. A simulação mostra, nos últimos 12 meses, o total **antes e depois** de cada vista, do mapa de custos e proveitos e
   do realizado do orçamento do ano, os documentos e movimentos afetados e o que não muda.
3. Só um **administrador** aplica, com motivo. Se a configuração das rubricas mudou entretanto, a aplicação é recusada
   e é preciso simular de novo. Cada rubrica alterada fica na auditoria (`rubrica_natureza_alterada`) com o antes, o
   depois, o motivo e o resumo da simulação.

**Motivo:** fica na auditoria, por isso tem de ser legível: pelo menos duas palavras reais (com vogal, ou siglas em
maiúsculas como TSU), uma delas com quatro ou mais letras. Textos como «jjjjjkkjk» são recusados. Há motivos habituais
para escolher com um clique, que podem ser editados.

**Pedidos de aprovação:** o gestor simula e **pede aprovação ao administrador**, com motivo. O pedido guarda as
alterações e o resumo da simulação; nada muda até ser aprovado. O administrador vê o aviso na barra do menu
(«Pedidos de alteração de rubricas por aprovar») e, no cartão **Pedidos de aprovação**, escolhe **Rever e aprovar**: as
alterações são carregadas, simuladas de novo, e **Aprovar e aplicar** aplica e marca o pedido como aprovado (se o
administrador ajustar o pedido, fica «aprovado com alterações»). Também pode **recusar** com motivo; quem pediu pode
**cancelar** enquanto está por aprovar. O mesmo vale para pedidos de reclassificação de pagamentos ao Estado. Pedidos,
aprovações, recusas e cancelamentos ficam na auditoria (`rubrica_pedido_*`).

**Histórico:** o separador mostra cada aplicação (data, utilizador, motivo, rubricas e indicadores que mudaram, se veio
de um pedido). **Repor a configuração anterior** carrega na tabela os valores anteriores a essa aplicação; é preciso
simular e aplicar como qualquer outra alteração.

**Outra empresa:** **Exportar configuração** descarrega um ficheiro JSON com a natureza e os indicadores de cada rubrica;
**Importar de outra empresa** carrega-o na tabela pelas rubricas com o mesmo nome (sem acentos nem maiúsculas) e indica
as que não têm correspondência. Nada é gravado sem simular e aplicar.

**Avisos da simulação** (não bloqueiam): rubrica que deixa de aparecer em todas as vistas; rubrica que sai dos custos
operacionais sem entrar em compras nem em obrigações fiscais (exceto movimento financeiro); alteração sem efeito porque
a rubrica não tem pagamentos nem documentos de custo no período (por exemplo, «Vendas», usada em entradas); natureza
incoerente com os indicadores (obrigação fiscal fora das obrigações fiscais, natureza fora dos custos que continua nos
custos operacionais).

## Pagamentos ao Estado noutras rubricas

Quando os pagamentos de IVA foram registados numa rubrica de custos (por exemplo, «Impostos», junto com a TSU), mudar a
natureza da rubrica não chega: tiraria também a TSU dos custos. O cartão **Pagamentos ao Estado noutras rubricas**
(no mesmo separador) trata cada pagamento:

1. **Candidatos** (últimos 24 meses, pagamentos bancários e saídas de caixa fora das rubricas fiscais), pela descrição:
   menciona IVA → indício forte; pagamento ao Estado/AT sem indicar o imposto → indício médio; na rubrica «Impostos»
   sem indicar TSU, Segurança Social ou retenções → indício fraco. TSU, Segurança Social, IUC, IRS, IRC, retenções,
   DMR, imposto do selo, IMI, IMT e derrama nunca são propostos. Nada é selecionado sozinho.
2. **Rubrica fiscal:** se não existir nenhuma rubrica de obrigação fiscal, o administrador cria a rubrica «IVA» com um
   clique (natureza obrigação fiscal, fora dos custos operacionais).
3. **Pré-visualizar** os pagamentos escolhidos: antes e depois de cada vista, do mapa de custos e proveitos e do
   orçamento de pagamentos, no período desde o pagamento mais antigo.
4. **Reclassificar** (administrador, com motivo): grava à parte (`rubric_reclassifications`) a rubrica que as análises
   passam a usar. **O registo bancário ou de caixa não muda** (valor, data, descrição, rubrica original, marcação de
   custo, reconciliação). Cada pagamento fica na auditoria (`pagamento_rubrica_reclassificada`). Se os registos ou as
   rubricas mudaram desde a pré-visualização, a reclassificação é recusada.
5. **Histórico:** cada reclassificação pode ser **anulada** com motivo (`pagamento_rubrica_reposta`), e para um pagamento
   bancário pode criar uma **regra bancária** com o texto da descrição sem números (editável) para que, na reconciliação,
   os pagamentos seguintes sejam propostos já na rubrica fiscal (só proposta; continua a ser confirmada).

A vista **Obrigações fiscais** conta os pagamentos ao Estado pelo valor pago a partir dos registos bancários e das saídas
de caixa nas rubricas fiscais, mesmo os registados como «não conta como custo».

## Vistas no Dashboard

O cartão «Custos, compras e obrigações fiscais» tem quatro vistas, cada uma com a origem dos valores e detalhe até ao
documento ou movimento:

- **Custos e despesas operacionais:** documentos de compra e custos sem documento registados no banco; regime normal =
  base + IVA não dedutível estimado.
- **Compras e investimentos:** documentos de compra das rubricas de compras (o custo das mercadorias vendidas continua a
  vir das margens).
- **Obrigações fiscais:** pagamentos ao Estado pelo valor pago (registos bancários e saídas de caixa nas rubricas
  fiscais, contem ou não como custo, e documentos nessas rubricas) e, no regime normal, o IVA do período: liquidado nas
  vendas, suportado, dedutível estimado e não dedutível (este já está nos custos operacionais).
- **Saídas de tesouraria:** pagamentos efetivos pela data do pagamento (caixa a fornecedores, despesas e saídas;
  pagamentos e encargos do livro bancário), sem transferências internas nem estornos.

**Mudanças recentes na classificação:** nos 180 dias seguintes a uma mudança de natureza ou a reclassificações, o cartão
mostra uma nota (por exemplo, «Desde 11/11/2026, IVA deixou de contar nos custos operacionais») com ligação ao
histórico. Os dois períodos comparados usam sempre a configuração atual; relatórios exportados antes podem ter outros
totais.

## O que não muda

Previsão de liquidez e prazos de pagamento, extratos, livro bancário e reconciliação, documentos e dados do ERP, Mapa
de IVA e previsão de IVA (calculados pelos documentos de venda e compra).

## API

- `GET /api/rubric-nature` — rubricas, naturezas, indicadores e sugestões (administrador e gestor).
- `POST /api/rubric-nature/simulate` — `{changes: [{category_id, nature, in_operating_costs, in_purchases,
  in_treasury, in_fiscal, in_payment_budget}]}`; não grava nada.
- `POST /api/rubric-nature/apply` — as mesmas alterações, `fingerprint` da simulação e `reason` (administrador).
- `GET /api/dashboard/expenses/drilldown?view=operating|purchases|fiscal|treasury` — detalhe por vista.
- `GET /api/rubric-nature/fiscal-payments` — candidatos, rubricas fiscais e histórico de reclassificações (administrador
  e gestor).
- `POST /api/rubric-nature/fiscal-rubric` — `{name}` (por omissão «IVA»); cria a rubrica fiscal (administrador).
- `POST /api/rubric-nature/reclassify/preview` — `{items: ["bank:<id>" | "move:<id>"], category_id}`; não grava nada.
- `POST /api/rubric-nature/reclassify` — o mesmo, `fingerprint` da pré-visualização e `reason` (administrador).
- `POST /api/rubric-nature/reclassifications/{id}/undo` — `{reason}` (administrador).

- `GET /api/rubric-nature/requests` — pedidos por aprovar e decididos nos últimos 90 dias.
- `POST /api/rubric-nature/requests` — `{kind: "nature", changes, reason}` ou `{kind: "reclassification", items,
  category_id, reason}` (administrador e gestor); aprovar = `apply`/`reclassify` com `request_id` (administrador).
- `POST /api/rubric-nature/requests/{id}/reject` — `{reason}` (administrador); `.../cancel` (quem pediu).

Migrações: 0087 (natureza e indicadores), 0088 (`rubric_reclassifications`) e 0089 (`rubric_requests`); sem linhas,
nada muda.

Testes: `backend/tests/test_rubric_nature.py`, `backend/tests/test_fiscal_payments.py`,
`backend/tests/test_rubric_requests.py` e `scripts/test-rubric-nature-ui.py`.
