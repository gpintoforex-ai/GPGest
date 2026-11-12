# Movimentos da rubrica: pesquisa, análise e correção das despesas por rubrica

No Dashboard (cartão «Custos, compras e obrigações fiscais»), na página **Despesas por rubrica** e nos
[Mapas de gestão](MAPAS-DE-GESTAO.md) (cada valor da tabela, indicadores, alertas e gráficos), o administrador e o
gestor abrem o painel **Movimentos da rubrica**: todos os registos que formam o valor da rubrica,
com pesquisa, filtros e correção da classificação. Os outros perfis continuam a ver o detalhe de consulta habitual.

## O que mostra

Os mesmos registos e valores do gráfico, na vista escolhida (custos operacionais, compras e investimentos, obrigações
fiscais, saídas de tesouraria):

- data do documento e, quando existe, data do pagamento;
- documento ou movimento, fornecedor/entidade, conta bancária, observações;
- origem (ERP, compra, e-Fatura, banco, caixa) e rubrica (com a cor da rubrica; sub-rubricas como «Mãe › Sub»);
- base, IVA e valor (o valor na rubrica e, se o documento estiver repartido, o total do documento);
- estado: pago / pago em parte / por pagar (documentos), reconciliado / por reconciliar (banco), caixa;
- ações: **Consultar** (detalhe do documento com as linhas e os originais no Arquivo digital), **Editar** (rubrica,
  centro de custo, observações e histórico) e **Associar a um documento** (abre a Reconciliação já pesquisada).

Pesquisa por descrição, fornecedor, documento ou valor; filtros por rubrica (inclui as sub-rubricas), origem, estado,
conta e intervalo de datas. A empresa e o estabelecimento são os do Dashboard. **Exportar movimentos** descarrega os
movimentos filtrados (CSV para Excel).

**Sem duplicações:** cada documento conta uma vez, pela data do documento. O pagamento de uma fatura não é uma segunda
despesa (fica na vista de tesouraria). Mudar a rubrica nunca cria movimentos.

## Correção da rubrica (individual ou em lote)

Selecione um ou mais movimentos, escolha a nova rubrica e **Pré-visualizar**: número de movimentos, rubrica anterior e
nova, valor total e restrições. A aplicação exige um motivo legível e cada registo usa o circuito próprio da sua origem:

| Origem | Como é corrigida | Quem |
|---|---|---|
| Documento do ERP | Repartição de gestão do documento (o ERP não é alterado) | Administrador e gestor |
| Compra sem linhas (incl. e-Fatura) | Repartição de gestão da compra | Administrador e gestor |
| Compra com linhas | Não se corrige aqui: a rubrica vem das linhas (Compras) | — |
| Registo bancário ou saída de caixa | Rubrica de análise (`rubric_reclassifications`): o registo não muda | Administrador; o gestor pede aprovação |

Valores, IVA, datas, documentos, saldos e tesouraria nunca mudam. Depois de aplicar, o gráfico, os totais, as
percentagens, o orçamento mensal (realizado e desvios) e os mapas recalculam com a nova classificação.

Um pagamento de IVA ao Estado não deve ir para uma rubrica operacional: use a rubrica «IVA» com natureza «Obrigação
fiscal» (ver [Natureza das rubricas](NATUREZA-DAS-RUBRICAS.md)); transferências e financiamentos usam a natureza
«Movimento financeiro».

## Histórico, reversão e auditoria

Em **Editar**, o histórico do registo mostra cada alteração (data, utilizador, rubrica anterior e nova, motivo). As
alterações de rubrica podem ser **revertidas** com motivo (a reversão de um pagamento do banco ou da caixa é do
administrador). Auditoria: `rubrica_movimento_alterada`, `classificacao_rubricas`, `pagamento_rubrica_reclassificada`,
`pagamento_rubrica_reposta`, `rubrica_movimento_revertida`, `anotacao_movimento`. O detalhe de cada registo indica a origem
(`despesas_por_rubrica`, `dashboard` ou `mapas_de_gestao`).

## Centro de custo, observações e sub-rubricas

- **Centro de custo e observações** por registo (`record_annotations`): classificação de gestão, independente dos dados
  fiscais e contabilísticos; protegida contra gravações concorrentes (revisão).
- **Sub-rubricas:** em Configurações → Rubricas, «Rubrica-mãe (opcional)» (um só nível). No Dashboard, «Agrupar por
  rubrica-mãe» soma as sub-rubricas na mãe; a categoria contabilística continua a ser do contabilista/ERP.

## API

- `GET /api/rubric-movements?start&end&store_id&view&category` — registos, rubricas, centros de custo e contas.
- `POST /api/rubric-movements/preview` — `{ids, category_id}`; não grava.
- `POST /api/rubric-movements/apply` — `{ids, category_id, fingerprint, reason}`.
- `GET /api/rubric-movements/history?id=` e `POST /api/rubric-movements/revert` — `{id, audit_id, reason}`.
- `PUT /api/rubric-movements/annotation` — `{id, cost_center_id, notes, revision}`.

Migração 0091 (`categories.parent_id`, `record_annotations`). Testes: `backend/tests/test_rubric_movements.py` e
`scripts/test-rubric-movements-ui.py`.
