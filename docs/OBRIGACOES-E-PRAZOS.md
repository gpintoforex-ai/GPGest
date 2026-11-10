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
| 4. Contratos (pré-aviso de denúncia, fidelização, renovação) | Por fazer: hoje regista-se a data de renovação como prazo da categoria Contrato |
| 5. Viaturas (ficha, IUC e inspeção por categoria e idade) | Por fazer: hoje regista-se cada prazo da viatura como obrigação |
| 6. Obrigações fiscais com modelos de regra «a validar» | Por fazer: hoje registam-se manualmente, com a origem da data |
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
