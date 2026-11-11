# Obrigações e prazos

Agenda de responsabilidades da empresa: obrigações fiscais, contratos, viaturas (IUC e inspeções), seguros,
inspeções de equipamentos, licenças e outros prazos. Menu **Obrigações e Prazos** (administrador e gestor),
página `Obrigações e Prazos` (`frontend/src/Obligations.tsx`), API `backend/app/obligations.py`.

## Estado da implementação

| Fase | Estado |
|---|---|
| 1. Auditoria e plano | Concluída |
| 2. Estrutura de obrigações e motor de recorrências | Concluída (migração **0078**) |
| 3. Painel e calendário | Concluída |
| 4. Contratos (pré-aviso de denúncia, fidelização, renovação) | Concluída (migração **0083**) — ver «Contratos» |
| 5. Viaturas (ficha, IUC e inspeção por categoria e idade) | Concluída (migração **0084**) — ver «Viaturas» |
| 6. Obrigações fiscais com modelos de regra «a validar» | Concluída (sem migração) — ver «Modelos fiscais» |
| 7. Alertas internos e por email (adesão do utilizador) | Por fazer: os estados e a confirmação de leitura já existem |
| 8. Integração financeira (previsão de liquidez, orçamento) e documental (arquivo) | Por fazer |

## Regras

- **Obrigação e ocorrências.** A obrigação guarda a regra (`obligations`): categoria, tipo, entidade, rubrica,
  responsável, âmbito (empresa ou estabelecimento), periodicidade, primeira data-limite, fim, alertas, valor
  previsto e taxa de IVA, origem da data. Cada data concreta é uma ocorrência (`obligation_occurrences`) com chave
  única (obrigação, data da regra): ler ou gravar várias vezes nunca duplica. São gravadas até 400 dias à frente.
- **Histórico independente.** Concluir, cancelar ou marcar «não aplicável» altera só essa ocorrência; a seguinte
  continua a existir. Ao mudar a regra (por exemplo, de mensal para trimestral), só as ocorrências futuras ainda
  intocadas são substituídas; as concluídas ou alteradas (data, valor) ficam.
- **Motor de recorrências** (`backend/app/recurrence.py`, comum com o orçamento): única, semanal, mensal,
  bimestral, trimestral, semestral, anual e personalizada (de N em N meses). Meses mais curtos usam o último dia
  do mês a partir do dia original (31 jan → 28 fev → 31 mar).
- **Estados.** Guardados: pendente, concluída, cancelada, não aplicável. Calculados para as pendentes: vencido
  (data passada), urgente (até 7 dias), a aproximar-se (até 30 dias) e agendado.
- **Conclusão só por confirmação.** Um prazo nunca fica concluído por existir um movimento financeiro
  semelhante: a conclusão regista quem, quando, o valor efetivo e uma observação. Reabrir, cancelar e «não
  aplicável» exigem motivo.
- **Datas legais.** Nenhuma data fiscal está fixada no código. Cada obrigação indica a origem da data (manual,
  regra legal, calendário oficial, contrato ou documento), uma referência e se a data foi **validada**; as
  obrigações fiscais começam como «a validar». Uma data concreta pode ser corrigida (prorrogação, por exemplo)
  com a origem da alteração, que fica na ocorrência e na auditoria.
- **Confirmação de leitura** por utilizador (`obligation_reads`).
- **Auditoria**: criação, alteração, mudança de estado da obrigação e todas as ações sobre ocorrências.
- **Concorrência**: revisão na obrigação e em cada ocorrência; uma versão desatualizada é recusada (409).

## Contratos

Separador **Contratos** (`frontend/src/Contracts.tsx`, API `backend/app/contracts.py`, tabela `contracts`).

- **Ficha**: fornecedor ou entidade, tipo, número, âmbito, início, fim do período em curso (data de renovação),
  fim da fidelização, renovação automática (de N em N meses), pré-aviso para denúncia (dias), valor de cada pagamento
  (com IVA) e periodicidade, primeiro pagamento, rubrica, responsável, alertas e observações.
- **Prazo de denúncia** = renovação − pré-aviso. É este prazo que tem os alertas (90, 60, 30, 15, 7 dias e no dia),
  não a data de renovação. Na renovação automática repete-se em cada período; sem renovação automática é um prazo
  único («Fim do contrato») para decidir a tempo. A lista mostra a próxima renovação, o prazo de denúncia com os dias
  em falta e o estado, a fidelização e o custo anual.
- **Motor comum**: cada contrato gere duas obrigações (`data.contract_id`, `role` notice/payment): o prazo de
  denúncia e os pagamentos. Os pagamentos entram na previsão de liquidez como estimativas (e cobrem a despesa
  orçamentada da mesma rubrica e mês). Estas obrigações só se alteram pela ficha do contrato (a edição direta é
  recusada); cada ocorrência trata-se como qualquer prazo (concluir, ler, reagendar).
- **Decisões, nunca automáticas** (com motivo e auditoria): *registar denúncia* (termina na próxima renovação; os
  pagamentos param aí; antes do fim da fidelização exige aceitar a eventual penalização), *renovar* (só sem renovação
  automática: nova data de fim ou mais um período), *terminar* (data efetiva) e *reativar*.

## Viaturas

Separador **Veículos** (`frontend/src/Vehicles.tsx`, API `backend/app/vehicles.py`, tabela `vehicles`).

- **Ficha**: matrícula (normalizada para AA-00-BB; única), marca, modelo, data da primeira matrícula, categoria,
  combustível, âmbito, responsável, IUC (valor previsto), inspeção (última inspeção ou regra manual, custo), seguro
  (seguradora, apólice, renovação, prémio e periodicidade), próxima revisão, contrato de leasing/renting, rubrica e
  alertas.
- **Regras pré-configuradas como modelos «a validar»** (origem «regra legal», até serem marcadas como validadas na
  ficha; qualquer ocorrência pode ser reagendada com a origem):
  - *IUC*: anual, até ao último dia do mês da matrícula (confirmar no Portal das Finanças). Ao registar a viatura
    começa no próximo prazo; os anteriores supõem-se pagos.
  - *Inspeção*: na data de aniversário da matrícula. Ligeiro de passageiros: 4, 6 e 8 anos e depois anual; ligeiro de
    mercadorias: 2 anos e depois anual; pesados, táxis, ambulâncias, transporte escolar e instrução: anual a partir do
    1.º ano (confirmar no IMT/centro de inspeção). Motociclos, reboques e outros: regra manual (próxima data e, se
    houver, intervalo em anos). Uma inspeção feita até 3 meses antes da data-limite conta para essa data.
- **Inspeção seguinte**: concluir a inspeção (com o valor pago) regista-a como última e cria a próxima; o histórico fica.
  Uma inspeção em atraso aparece como vencida, mesmo antiga (os prazos únicos geram-se sempre).
- **Venda ou abate** (com motivo): os prazos futuros deixam de ser gerados; reativar volta a gerá-los.
- As obrigações da viatura (IUC, inspeção, seguro, revisão) só se alteram pela ficha; os valores entram na previsão de
  liquidez como estimativas.

## Modelos fiscais

Botão **Modelos fiscais** (`frontend/src/FiscalTemplates.tsx`, API `backend/app/fiscal_templates.py`).

- Catálogo de modelos com a regra habitual e a fonte a confirmar: IVA (declaração e pagamento, mensal ou
  trimestral; regime dos pequenos retalhistas), comunicação das faturas, retenções na fonte, DMR, Segurança Social
  (declaração e pagamento), fundos de compensação, IRC (Modelo 22 e três pagamentos por conta), IES, comunicação
  de inventários, Modelo 10 e IMI. O IUC gere-se na ficha de cada viatura.
- **Proposta pela empresa**: pré-seleciona os que se aplicam ao regime e periodicidade de IVA configurados
  (Configurações → Regime de IVA) e, para DMR e Segurança Social, à existência de funcionários ativos. Os
  condicionais (IMI, Modelo 10, comunicação de faturas, retenções, fundos de compensação) ficam por escolher.
- **Criação só do escolhido**, com a próxima data-limite calculada a partir de hoje e a recorrência do modelo; cada
  obrigação fica com origem «regra legal», **por validar**, a regra e a fonte na nota, e alertas a 30, 15, 7 dias e no
  dia. Um modelo já criado não se duplica (`data.template`). Dias não úteis e prorrogações corrigem-se reagendando a
  ocorrência com a origem.

## Painel


- **Indicadores** (cada um abre a lista filtrada): vencidas, prazos a expirar (≤ 7 dias), próximos 30 e 90 dias
  (com o valor previsto), em dia (obrigações ativas sem prazos vencidos; concluídas no ano) e total.
- **Separadores**: Visão geral, Todos os prazos, Fiscais, Contratos, Veículos, Inspeções, Outros prazos
  (seguros, licenças e outros) e Calendário.
- **Visão geral**: próximos prazos (vencidos primeiro, com «…» para concluir, alterar a data-limite, não
  aplicável, cancelar, confirmar leitura e abrir a ficha), calendário mensal, categorias, prazos por mês (6
  meses), obrigações fiscais principais (com «data a validar») e contratos em renovação (90 dias).
- **Lista**: pesquisa, filtros por estado e responsável, valores previsto e efetivo, origem da data.
- **Ficha** (painel lateral): dados da regra, alertas (90, 60, 30, 15, 7 dias e no dia), origem da data e
  histórico das ocorrências com as respetivas ações.

## API

- `GET /api/obligations/overview?store_id=&month=AAAA-MM` — indicadores, próximos prazos, calendário, categorias.
- `GET /api/obligations/occurrences?category=&state_filter=&responsible_id=&start=&end=&q=` — lista e detalhe.
- `GET /api/obligations`, `GET /api/obligations/{id}` — obrigações e histórico.
- `POST /api/obligations`, `PUT /api/obligations/{id}`, `POST /api/obligations/{id}/status` (ativa, suspensa, terminada).
- `POST /api/obligations/occurrences/{id}/{complete|reopen|cancel|not-applicable|reschedule|read}`.

Validação: `backend/tests/test_obligations.py`, `scripts/test-obligations-ui.py` e `scripts/test-navigation.cjs`.
