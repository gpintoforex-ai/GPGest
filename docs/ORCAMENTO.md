# Orçamento e previsão de despesas

Planeamento anual das despesas da empresa ou de um estabelecimento, com comparação entre o previsto e o
realizado. Menu **Financeiro → Orçamento** (administrador e gestor), página `Orçamento`
(`frontend/src/Budget.tsx`), API `backend/app/budget.py`.

O orçamento é **só planeamento**: não cria pagamentos, lançamentos nem movimentos de tesouraria. O realizado, o
pago e o por pagar vêm sempre das fontes que já existem.

## Estado da implementação

| Fase | Estado |
|---|---|
| 1. Auditoria e plano | Concluída |
| 2. Estrutura: orçamentos com versões, centros de custo e rubricas | Concluída (migração **0079**) |
| 3. Registo de despesas previstas (ficha rápida, recorrência, aumentos, exceções) | Concluída |
| 4. Painel e grelhas (previsto vs realizado por rubrica e por mês) | Concluída |
| 5. Ligação de cada documento ou movimento real a uma despesa prevista (proposta e validação) | Concluída (migração **0081**) — ver «Associação ao realizado» |
| 6. Previsão de tesouraria (estimativas na previsão de liquidez, sem duplicar documentos reais) | Por fazer |
| 7. Alertas configuráveis (rubrica acima do orçamento, previsto sem documento, aumentos, não previstas, saldo negativo) | Por fazer |
| 8. Drill-down até aos documentos e relatórios | Por fazer |

## Regras

- **Versões.** Um orçamento por ano e âmbito (empresa ou estabelecimento), com versões: *rascunho* (editável),
  *aprovado* (bloqueado) e *revisto* (aprovado substituído por uma revisão aprovada). «Criar revisão» copia as
  despesas para uma nova versão em rascunho; a versão aprovada continua em vigor até a revisão ser aprovada.
  As versões anteriores ficam consultáveis. Criação, alterações, aprovação e revisões ficam na auditoria.
- **Criar a partir de outro ano.** Copia as despesas previstas, desloca as datas (início, fim, aumentos e
  exceções) para o novo ano e aplica uma percentagem de atualização aos valores.
- **Despesa prevista.** Descrição, rubrica, fornecedor (se conhecido), centro de custo (opcional), valor por
  ocorrência sem IVA (ou com IVA, convertido para sem IVA com `Decimal`), taxa de IVA, periodicidade (única,
  semanal, mensal, bimestral, trimestral, semestral, anual, personalizada), início e fim, dias até ao
  pagamento, conta ou caixa prevista, observações e estado (ativa ou suspensa, que não conta).
- **Aumentos previstos**: percentagem a partir de uma data, acumulável. **Exceções**: uma ocorrência que não
  se realiza ou tem outro valor (sem IVA, não leva os aumentos).
- **Recorrência**: o motor comum das obrigações (`backend/app/recurrence.py`), com fim de mês tratado
  (31 jan → 28 fev → 31 mar). As ocorrências são calculadas, nunca gravadas como movimentos.
- **IVA.** O orçamento compara **custos sem IVA dedutível**, com o critério dos mapas de gestão: no regime
  normal, base mais o IVA não dedutível estimado pela % da rubrica; nos restantes regimes, base mais IVA. Por
  ocorrência mostram-se a base, o IVA, o IVA dedutível previsto, o valor com IVA (a pagar) e o custo. Nunca se
  assume que todo o IVA é dedutível.
- **Realizado**: os mesmos documentos dos mapas de gestão (`management_maps.cost_documents`): compras, documentos
  de compra do ERP sem compra correspondente e custos bancários com rubrica marcados como custo, cada um uma vez,
  pela data do documento. Funciona no modo local e no integrado.
- **Desvios.** Desvio (€) = realizado − orçamentado; desvio (%) = desvio / orçamentado × 100. Com orçamento a
  zero, a percentagem aparece como «n.a.». Positivo: acima do orçamento. Negativo: abaixo; só é **poupança**
  quando o mês está dentro de um período contabilístico fechado.
- **Despesas não previstas**: rubricas com realizado e sem orçamento no ano.
- **Centros de custo**: código (único, em maiúsculas) e nome; inativos ficam no histórico.

## Associação ao realizado

Separador **Associações** e colunas «Realizado» e «Ainda previsto» na grelha (`backend/app/budget_links.py`,
`frontend/src/BudgetMatching.tsx`).

- **Parcela**: cada documento do realizado (compra, documento do ERP sem compra, custo bancário com rubrica), por
  rubrica, ao custo dos mapas de gestão, só nas rubricas que entram no orçamento de pagamentos
  ([natureza das rubricas](NATUREZA-DAS-RUBRICAS.md)). Só parcelas positivas e com rubrica; notas de crédito e parcelas sem
  rubrica contam apenas na comparação por rubrica.
- **Propostas** (calculadas na leitura, nada é gravado ao consultar), só para despesas previstas ativas da mesma
  rubrica e ocorrências dentro de uma janela de datas (semanal 4 dias, mensal 20, trimestral 45, anual 75…):
  rubrica 40; fornecedor da despesa prevista com o mesmo NIF +30 (NIF diferente exclui) ou o mesmo nome +20; data
  até 7 dias +15 (mesmo mês +10); valor até 5 % do previsto +15 (até 20 % +8); ocorrência já realizada −15.
  Forte ≥ 85 %, provável ≥ 65 %; duas ocorrências com confiança próxima = «várias possibilidades».
- **Confirmar**: individual, «Escolher…» (outra ocorrência e valor parcial) ou em lote (só as selecionadas; tudo
  ou nada). O valor associado de uma parcela nunca ultrapassa o seu custo na versão (a mesma despesa não conta
  duas vezes); várias despesas podem realizar a mesma previsão e uma despesa pode repartir-se por várias
  ocorrências. **Rejeitar** grava a decisão para a proposta não voltar; **anular** exige motivo e fica no histórico.
  Tudo na auditoria. Bloqueio transacional por orçamento.
- **Só a versão em vigor** (aprovada; sem aprovada, o rascunho) recebe associações. O orçamento aprovado não é
  alterado. Ao aprovar uma revisão, as associações confirmadas passam para as linhas copiadas (`origin_line_id`);
  o rascunho mostra entretanto o realizado das linhas de origem; a versão revista mantém as suas.
- **Execução por despesa prevista**: realizado (por mês e por ocorrência), pago e por pagar (compras pelos
  pagamentos e reconciliação já registados; custos bancários são pagamentos; documentos só do ERP com pagamento
  desconhecido), ainda previsto e ocorrências passadas sem documento. A ficha da despesa mostra os documentos
  associados com o estado de pagamento e permite anular.
- **Despesas sem previsão**: parcelas sem despesa prevista compatível (rubrica sem orçamento, fornecedor ou data).

## Painel

- **Indicadores** (abrem o detalhe): orçamento anual, previsto e realizado até ao mês de análise, desvio
  acumulado, despesas não previstas e valor a pagar com IVA no ano (com o IVA dedutível previsto).
- **Visão geral**: previsto vs realizado por mês, acumulado no ano, evolução dos desvios, orçamento por rubrica
  e por fornecedor. Cada gráfico tem a vista em tabela.
- **Previsto vs realizado**: por rubrica, no mês, acumulado e anual, filtrável pela situação.
- **Despesas previstas**: grelha com pesquisa, filtro por rubrica, ordenação, totais e exportação CSV (`;`, UTF-8);
  a ficha mostra as ocorrências do ano (data, pagamento, base, IVA, dedutível, com IVA, custo).
- **Centros de custo**: criar, editar e desativar.

## API

- `GET /api/budgets/overview?year=&store_id=&month=&budget_id=` — versão em vigor (último aprovado; senão o
  rascunho), indicadores, meses, rubricas, fornecedores, despesas previstas e opções.
- `POST /api/budgets` (`copy_from_id`, `update_percent`), `PUT /api/budgets/{id}`,
  `POST /api/budgets/{id}/approve`, `POST /api/budgets/{id}/revise`.
- `POST /api/budgets/{id}/lines`, `PUT /api/budget-lines/{id}`, `DELETE /api/budget-lines/{id}?revision=` (só em rascunho).
- `GET|POST /api/cost-centers`, `PUT /api/cost-centers/{id}`.

Concorrência: revisão no orçamento e em cada despesa (409 numa versão desatualizada); a criação de versões do
mesmo ano e âmbito é serializada com um bloqueio transacional PostgreSQL.

Validação: `backend/tests/test_budget.py`, `backend/tests/test_budget_links.py`, `scripts/test-budget-ui.py` e `scripts/test-navigation.cjs`.
