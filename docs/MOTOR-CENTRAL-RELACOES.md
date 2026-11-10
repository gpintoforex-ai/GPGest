# Motor central de relações documentais, financeiras e orçamentais

Objetivo: acompanhar cada operação do documento original à liquidação (e-Fatura → PDF → ERP → rubrica →
orçamento → pagamento → banco → tesouraria), sem duplicar registos e mostrando ao utilizador só o que
precisa de intervenção. **Regra:** reutilizar as tabelas e serviços existentes; nada é criado, pago ou
lançado sem a operação própria e confirmação.

## Mapa do que existe (auditoria)

| Relação | Onde já existe |
|---|---|
| e-Fatura ↔ documento ERP | `document_erp_links` (uma ligação ativa por lado, anulável); regra de identidade e divergências em `documents.comparison`; conferência em `invoice_workflow` / `invoice_conference` |
| PDF ↔ fatura | Arquivo digital (`archive_documents`, `archive_links` com pontuação, força, motivos, estado e decisão); anexos próprios, Drive (`drive_document_attachments`), Gmail, digitalizações; extração por QR, texto e OCR |
| Fatura ↔ rubricas | Linhas da compra, `erp_document_rubrics` (repartição), rubrica do fornecedor, `planned_allocations`; mesma repartição nos mapas (`management_maps.purchase_documents`) |
| Fatura ↔ pagamentos | `payment_document_lines` (valor por fatura), `purchase_allocations` (compra ↔ movimento bancário) |
| Banco ↔ registos | Reconciliação (`reconciliations`, propostas determinísticas, agrupamentos, tolerância de datas por conta, `bank_rules`, ignorados, períodos fechados) |
| Previsto ↔ real | Previsão de liquidez (`replaces_key`, `erp_covered`, liquidações locais, confiança) |
| Despesa ↔ orçamento | Orçamento com comparação por rubrica (`budget.py`) — **ligação por despesa em falta** |
| Histórico | `audit`, `archive_events`, revisões com 409 |

## Migrações aprovadas (só tabelas novas)

| Migração | Tabela | Para quê |
|---|---|---|
| 0080 (aplicada) | `classification_rules`, `purchase_rubrics` | Regras de rubrica aprovadas (fornecedor, tipo de documento, palavra-chave, estabelecimento → repartição em %; classificação financeira) e repartição confirmada de compras sem linhas nem documento ERP (até aqui não havia onde a gravar) |
| 0081 (aplicada) | `budget_links` + coluna `budget_lines.origin_line_id` | Parcela do realizado (compra, documento ERP, custo bancário; por rubrica) ↔ ocorrência de uma linha do orçamento, com valor, estado, confiança, motivos, decisão e anulação. A coluna nova (numa tabela criada pela 0079, ainda não publicada) liga a linha copiada numa revisão à de origem, para as associações passarem para a versão aprovada |
| 0082 (aplicada) | `automation_settings` | Automatizações autorizadas e limites das aprovações em lote do Centro de Conferência (por omissão: nada automático). As tolerâncias da reconciliação ficaram nas regras de cada conta, sem tabela nova |

A classificação veio antes da ligação ao orçamento, por isso ficou com o número 0080 (numeração sequencial).

**Reversão:** nenhuma migração altera tabelas ou dados existentes; o `downgrade` de cada uma apaga só a sua
tabela (perdem-se as ligações, regras ou configurações novas). Antes da Neon: `pg_dump` dos esquemas, validação
local e aplicação uma a uma (`alembic upgrade 0080`, …); em caso de falha, `alembic downgrade` para a versão
anterior.

## Estado

| Passo | Estado |
|---|---|
| Auditoria e plano | Concluído |
| 1. Rastreabilidade (e-Fatura ↔ PDF ↔ ERP e restantes ligações), só de leitura | Concluído — sem migrações |
| 2. Classificação inteligente de rubricas (0080) | Concluído — migração aplicada e reversão verificada localmente |
| 3. Associação ao orçamento (0081) | Concluído — migração aplicada e reversão verificada localmente; ver [ORCAMENTO.md](ORCAMENTO.md#associação-ao-realizado) |
| 4. Reconciliação com tolerâncias e limites configuráveis | Concluído — sem migração (regras da conta); ver [RECONCILIACAO-BANCARIA.md](RECONCILIACAO-BANCARIA.md) |
| 5. Previsão de tesouraria com prioridades e horizontes de 7 a 365 dias | Concluído — sem migração; ver [PREVISAO-LIQUIDEZ.md](PREVISAO-LIQUIDEZ.md#prioridades-compromissos-e-estimativas) |
| 6. Centro de Conferência e automatizações (0082) | Concluído — migração aplicada e reversão verificada localmente |

## Rastreabilidade das operações (passo 1)

Menu **Contabilidade → Rastreabilidade das operações** (administrador e gestor), página `Rastreabilidade`
(`frontend/src/OperationTrace.tsx`), API `backend/app/operation_trace.py`. Só de leitura.

- **Lista** do período (por omissão, os últimos três meses): compras e documentos que só existem no ERP (os
  mesmos dos mapas de gestão, sem duplicar os já ligados). Por omissão mostra só as que precisam de intervenção,
  primeiro as mais recentes. Resumo por ligação com filtro por estado.
- **Estado de cada ligação**: confirmada, provável, várias possibilidades, em falta, divergência ou não aplicável.
  - *e-Fatura*: confirmada se a compra veio da AT ou de um ficheiro e-Fatura; senão «ainda não visto».
  - *PDF*: anexos da compra ou relação confirmada no arquivo; uma proposta do arquivo é provável, várias são
    «várias possibilidades»; total do PDF diferente é divergência.
  - *ERP*: a regra da Conferência documental (NIF e número; tipo, data e total como diferenças). No modo local é
    «não aplicável».
  - *Rubrica*: linhas da compra ou rubrica do documento: confirmada; rubrica do fornecedor: provável (sugestão);
    sem rubrica, total ou parcial: em falta.
  - *Pagamento* e *banco*: os estados da Conferência documental (pago, parcial, por pagar; reconciliado, parcial,
    pendente), com o vencimento do previsto e a indicação de vencido.
- **Ficha**: percurso da operação, PDF ou imagem ao lado dos dados do documento, do ERP e da extração do PDF (com
  as diferenças assinaladas), rubricas, vencimento, pagamentos e histórico. As correções fazem-se na Conferência
  documental, no Arquivo ou na Reconciliação.

Reutiliza `documents.projection` (exposta em `app.state.document_projection`), `management_maps.purchase_documents`
e `rubric_expenses.erp_shares`, para que os estados e valores sejam os mesmos em todos os ecrãs.

Validação: `backend/tests/test_operation_trace.py`, `scripts/test-operation-trace-ui.py`, `scripts/test-navigation.cjs`.

## Classificação inteligente de rubricas (passo 2)

Menu **Contabilidade → Classificação de rubricas** (administrador e gestor), página `Classificação de rubricas`
(`frontend/src/Classification.tsx`), API `backend/app/classification.py`. Também acessível pela ficha da
rastreabilidade («Classificar…»).

- **Por classificar**: documentos do período (compras e documentos só do ERP) sem classificação confirmada: por
  classificar ou só com a rubrica da ficha do fornecedor. Compras com linhas lançadas classificam-se nas linhas,
  em Compras.
- **Sugestões** (até quatro, por confiança):
  - *Regra aprovada* que corresponde a todos os seus critérios: 82 % + 5 % por critério (máx. 98 %); as mais
    específicas primeiro.
  - *Histórico do fornecedor* (últimos 24 documentos classificados: documentos ERP, compras classificadas e
    compras com linhas): a repartição mais frequente, com 45 % + 45 % × (frequência).
  - *Ficha do fornecedor*: 65 %.
  - A mesma repartição vinda de várias fontes reforça a confiança (+3 %) e junta os motivos.
- **Aplicar**: por percentagem ou por valor; a soma tem de ser o total do documento (por percentagem, os cêntimos
  são distribuídos pelos maiores restos, sem perdas). Grava no documento ERP (ou no ERP ligado à compra) em
  `erp_document_rubrics`, ou na compra em `purchase_rubrics`. O ERP nunca é alterado. Fica na auditoria
  (`classificacao_rubricas`, com antes e depois).
- **Lote**: aceita só as sugestões selecionadas («Selecionar as seguras» marca as de confiança ≥ 85 %); o servidor
  valida cada uma e aplica tudo ou nada. No lote não se criam regras.
- **Regras aprovadas**: criadas ao classificar («Guardar como regra aprovada»: mesmo fornecedor, tipo de
  documento, estabelecimento, palavra-chave) ou no separador Regras; repartição em percentagem (soma 100 %),
  pelo menos um critério, ativar/desativar, contagem de utilizações e revisão (409).
- **Financeira, não fiscal nem contabilística**: as rubricas são de gestão. A dedução do IVA vem da % de cada
  rubrica; a classificação contabilística (SNC) é do contabilista ou do ERP e não é alterada.
- **Efeito**: mapas de gestão, despesas por rubrica, dashboard e rastreabilidade passam a usar a repartição
  confirmada (base e IVA na mesma proporção; num documento ERP com outro total, proporcional ao total do ERP).

Validação: `backend/tests/test_classification.py`, `scripts/test-classification-ui.py`.

## Centro de Conferência e automatizações (passo 6)

Menu **Contabilidade → Centro de Conferência** (administrador e gestor), página `Centro de Conferência`
(`frontend/src/Conference.tsx`), API `backend/app/conference.py`.

- **Por resolver**: um cartão por tipo de situação, com o número, os primeiros casos e «Abrir» no módulo onde se
  resolve: documentos novos (7 dias), documentos em falta (PDF ou ERP), diferenças de valores, associações
  documentais propostas, rubricas por classificar, despesas por associar ao orçamento, despesas sem orçamento,
  movimentos bancários por reconciliar, relações do arquivo por confirmar e prazos vencidos ou a 7 dias. Só
  agrega o que os módulos já calculam; consultar não grava nada.
- **Aprovar as seguras**: na classificação e no orçamento, as propostas com confiança igual ou superior ao limite
  configurado são enviadas às rotas de lote existentes, que voltam a validar cada item (tudo ou nada).
- **Automatizações** (só o administrador altera; por omissão desligadas): aplicar a classificação sugerida por uma
  regra aprovada (confiança mínima 85–99 %, 95 % por omissão) e associar ao orçamento as despesas com proposta forte
  sem alternativa (99 % por omissão). Executam «agora» ou uma vez por dia (agendador), em nome de quem as autorizou,
  com auditoria (`automatizacoes_executadas`, motivo «Automatização autorizada» nas associações). Desativar remove a
  autorização.
- **Nunca automático**: pagamentos e liquidações, reconciliação bancária, lançamentos e alterações de documentos,
  escritas no ERP e conclusão de prazos das Obrigações.

Validação: `backend/tests/test_conference.py`, `scripts/test-conference-ui.py`, `scripts/test-navigation.cjs`.
