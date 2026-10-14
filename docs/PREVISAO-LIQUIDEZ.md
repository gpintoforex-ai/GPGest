# Previsão de liquidez

## Íris · Tesouraria

O banco é obrigatório e limita a proposta à loja da conta escolhida. Antes de
criar pagamentos, use **Validar saldo do banco**. A avaliação mostra saldo
utilizado, saldo após a seleção, outras saídas, menor saldo no horizonte,
reserva mínima e valor em falta. Não inclui recebimentos futuros; saídas sem
conta associada da mesma loja são reservadas neste banco por prudência. Os
valores parcialmente liquidados só entram pelo remanescente, sem duplicação.
Um saldo indisponível, desatualizado ou abaixo da reserva bloqueia a ação.
Os saldos estimados são identificados; a avaliação usa os dados guardados.

A validação repete-se antes de cada registo local e de cada envio ERP. Os
pagamentos ERP confirmados nesta proposta ficam reservados na avaliação enquanto
se prossegue, por prudência. O banco selecionado é o banco previsto na proposta;
no formulário ERP é necessário confirmar que o meio de pagamento utiliza a
mesma conta na configuração do ERP. Não se inventa um mapeamento bancário.

Em **Recebimentos e pagamentos previstos**, administradores e gestores podem
abrir **Íris · Propor liquidação**. Escolha a entidade, pagamentos a fornecedores
ou recebimentos de clientes e **Apenas documentos vencidos**. Um documento está
vencido quando o vencimento é anterior ao dia atual. Pode indicar um valor a
distribuir; para incluir documentos ainda por vencer, desmarque essa opção.

**Propor liquidação** consulta os saldos reais em aberto e distribui o valor
do vencimento mais antigo para o mais recente. O último documento pode receber
uma liquidação parcial. A proposta mostra entidade, documento, saldo, valor e
destino local/ERP. Não cria pagamentos nesta fase, nem usa valores simulados.

Reveja e selecione os documentos. Para pagamentos locais, escolha conta/caixa,
rubrica, data e justificação; confirme que foram realizados. **Criar pagamentos
propostos** regista as liquidações locais selecionadas, com referências estáveis
para impedir duplicação em repetições. Uma caixa exige folha aberta e saldo
suficiente; pagamentos bancários ficam por reconciliar.

Os documentos ERP abrem o formulário existente de liquidação, com o valor
proposto, documento, série, meio e confirmação explícita. Depois de confirmar,
use **Continuar pagamentos pendentes** para prosseguir. Uma ligação apenas de
leitura ou indisponível não gera automaticamente pagamentos locais. Falhas
interrompem o processo, preservando os pagamentos já confirmados; o conjunto
não é uma transação única com anulação automática.

Abra **Tesouraria > Previsão de liquidez**. A empresa é a selecionada na aplicação; o filtro superior de estabelecimento e as permissões existentes limitam os dados. O módulo é de previsão de dinheiro, não de resultados ou faturação.

## Utilização

1. Escolha 7, 15, 30, 60 ou 90 dias, ou um período personalizado até 366 dias desde hoje. Se o início for posterior a hoje, os movimentos intermédios transportam o saldo até ao início escolhido.
2. Selecione as contas e caixas. Os valores de moedas distintas ficam em grupos separados, sem conversão cambial. Confira **Saldos de origem**, incluindo data do saldo, atualização, fonte e qualidade.
3. Reveja **Pendências e movimentos excluídos**. Um documento vencido, ou um depósito sem estimativa de disponibilização, não recebe automaticamente a data de hoje.
4. Use **Ajustar previsão** para definir data, conta e confiança. O vencimento e o valor da origem não são alterados. Se os dados da origem mudarem, o ajuste continua guardado, mas o movimento fica excluído até à revisão explícita. Uma redução de valor que corresponda exatamente a liquidações ERP confirmadas na auditoria mantém a data e a conta previstas, contando apenas o restante por liquidar. Alterações de vencimento, sentido ou moeda continuam a exigir revisão.
5. Crie compromissos manuais, financiamento, despesas ou transferências entre duas contas. As recorrências mensais, trimestrais e anuais usam o dia original, limitado ao último dia de cada mês: 31/01, 28/02, 31/03. Podem ter data final e ser desativadas. A data de vencimento mantém-se separada da data esperada.
6. Para evitar duplicar uma recorrência com uma fatura, ajuste a fatura e selecione a ocorrência em **Substitui uma recorrência**. Essa ocorrência passa à lista de excluídos enquanto a fatura estiver em aberto. A associação não altera nem liquida os documentos.
7. **Simular** permite alterar data, valor, conta ou excluir um movimento. **Guardar / duplicar cenário** conserva uma nova versão, com possibilidade de adicionar entrada, despesa ou transferência simulada. A comparação apresenta os resultados face à Base. Partilhar exige acesso aos mesmos estabelecimentos abrangidos pelo cenário.
8. Clique no número de movimentos de um dia para consultar a explicação do saldo. **Ver origem** mostra os dados locais ou a última versão sincronizada do documento, antes dos ajustes.
9. **Configurar reserva** define a reserva por moeda e o prazo, em dias, a partir do qual um saldo é assinalado como desatualizado. **CSV** exporta indicadores diários, premissas, atualização, movimentos, exclusões e qualidade dos saldos, em UTF-8 com BOM e separador ponto e vírgula. O ficheiro pode ser aberto no Excel.

## Fontes e cálculo

- Bancos: último `BankTransaction.balance_cents` até hoje, por conta, reutilizando a regra da página de extratos: exportação mais recente e primeira posição dessa exportação. Não soma saldos de vários extratos nem cria saldos bancários a partir de faturas. É saldo **contabilístico de extrato**, não confirmação em tempo real de saldo disponível.
- Caixas: serviço já usado pelos depósitos para o acumulado de movimentos reais, acrescido uma única vez do fundo a manter da última folha. Inclui cheques atribuíveis à caixa e desconta os depósitos confirmados. O saldo é **estimado**; saldos repetidos de abertura e «Valor do turno» não são novas entradas. Cheques físicos podem não estar imediatamente disponíveis no banco. Sem folha nem movimentos suficientes, o saldo é identificado como indisponível.
- Previstos locais: `PlannedMovement` menos liquidações `PlannedSettlement` ativas, reutilizando `planned_summary`. Estornos preservam a regra existente de liquidação. Um previsto totalmente liquidado não continua como futuro.
- ERP: registos Wintouch `kind=previsto`, ativos, da empresa selecionada. O valor é o saldo em aberto do estado da conta corrente (`StateValue`), não o total original da fatura. Inclui pendências e vencimentos disponibilizados pela integração. As fontes são adaptadas para o mesmo contrato de previsão; não há chamadas ou escritas no ERP a partir deste módulo.
- Depósitos preparados: saída da caixa e entrada no destino na data prevista, sem variação consolidada quando ambas as contas estão incluídas. Depósitos em trânsito/entregues: valor restante por creditar aparece separado e como entrada bancária apenas quando existe uma data esperada explícita. Os créditos reconciliados são descontados da previsão. Uma alteração de valor por crédito parcial exige revisão do ajuste, evitando presumir que a estimativa antiga foi reconfirmada.
- Compromissos e cenários: tabelas independentes, sem movimentos contabilísticos ou reais gerados. Transferências internas produzem duas pernas; na seleção consolidada o efeito líquido é zero, em seleções parciais aparece a entrada ou saída correspondente.

Por dia: `saldo final = saldo inicial + entradas - saídas`. O saldo final transita para o dia seguinte. Os cálculos usam cêntimos inteiros e valores decimais na leitura do acumulado. Movimentos sem conta podem entrar no consolidado de todas as contas; na seleção parcial ficam excluídos com indicação do motivo. Uma conta sem saldo não é inventada: os indicadores são identificados como **parciais**, com a limitação visível.

## Permissões e auditoria

- Todos os perfis autenticados podem consultar os estabelecimentos autorizados.
- Administrador, gestor e operador podem criar previsões manuais, ajustar estimativas e guardar cenários privados.
- Administrador e gestor podem gerir recorrências e partilhar cenários.
- Só o administrador configura reservas.

Todas as alterações exigem justificação e registam utilizador, data e detalhe na auditoria existente. A migração **0030** cria `liquidity_settings`, `liquidity_manual`, `liquidity_adjustments` e `liquidity_scenarios`, em cada esquema da empresa, e adiciona moeda (EUR por defeito) a contas e caixas. As simulações e os ajustes nunca mudam faturas, movimentos reais, reconciliações ou prazos contratuais.

## Limitações reais

- Não existe ligação bancária em tempo real; é necessário importar extratos atualizados. A qualidade é indicada pela data do saldo, atualização e prazo configurável.
- Os documentos ERP têm a qualidade e abrangência da última sincronização existente. Uma previsão não sincroniza automaticamente dados reais, não pressupõe sucesso da integração e não inventa prestações/acordos inexistentes no contrato atual da Wintouch.
- O contrato financeiro atual do GPGest é em EUR por defeito. Uma previsão manual pode indicar outra moeda; só pode ser associada a uma conta com essa mesma moeda. Não há taxas de câmbio automáticas ou conversão de extratos EUR para outras moedas.
- O saldo de caixa não é uma nova contagem física, nem prova de disponibilidade dos cheques. Recebimentos por cheque sem atribuição segura a uma caixa não são considerados nesse saldo.
- Não prevê taxas bancárias, impostos, atrasos ou percentagens por conta própria. Os atrasos conservadores são introduzidos pelo utilizador e as entradas incertas são excluídas apenas pela premissa escolhida.
- Não estabelece automaticamente a conta bancária de cada fatura. Deve ser escolhida no ajuste quando se pretende uma previsão por conta.
- O módulo não identifica por semelhança uma fatura local duplicada de uma fatura ERP. Evite registar o mesmo compromisso nas duas fontes; a substituição explícita é disponibilizada para ocorrências recorrentes.

## Verificação

`backend/tests/test_liquidity.py` cobre entradas/saídas no mesmo dia, reservas, saldos desatualizados, CSV, liquidação parcial, vencidos sem data, ajustes/conflitos, transferências, recorrências/substituições, cenários sem mutação de dados reais, moedas separadas, pendentes ERP, depósitos em trânsito/créditos parciais, perfis e isolamento entre esquemas de empresas. Os testes usam esquemas temporários de demonstração.

`scripts/test-liquidity-ui.py` usa apenas fixtures identificadas de demonstração: navegação, gráfico, detalhe diário, origem, compromisso manual, cenário, comparação, CSV, telemóvel, teclado e falha/repetição. As imagens de verificação ficam em `.local/visual-template/liquidez-*.png`.

## Liquidação com e sem ERP

Com ligação ERP ativa, documentos ERP usam o formulário e a confirmação existentes. Sem ligação ativa, o botão Liquidar regista um pagamento local associado ao documento, sem enviar para o ERP, e reduz apenas o saldo por liquidar. Documentos e compromissos locais podem ser liquidados localmente mesmo quando existe ERP ativo. Administradores e gestores podem confirmar liquidações.

A caixa exige uma folha aberta na data do pagamento e regista numerário com rubrica e entidade. No banco, o registo é uma transferência local em DocumentPayment, disponível para reconciliação: até à associação explícita ao extrato, o saldo da previsão é ajustado e identificado como estimado. Após reconciliar, deixa de ser descontado novamente. Valores, datas e referências repetidas são validados. Não suporta liquidação real em moedas diferentes de EUR.

As liquidações locais não são reenviadas automaticamente quando a ligação ERP volta a ficar ativa. Se existirem pagamentos locais associados, a liquidação desse documento no ERP fica bloqueada até à conferência, evitando duplicação. Mudanças do saldo na origem de um documento liquidado localmente são sinalizadas para revisão.

Migração 0031: liquidity_settlements, com ligações ao pagamento, movimento de caixa quando aplicável, documento de origem, utilizador e referência única. Testes adicionais: tests/test_liquidity_local_settlement.py.
