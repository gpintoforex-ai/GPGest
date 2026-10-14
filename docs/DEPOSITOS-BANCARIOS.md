# Depósitos bancários

## Utilização

Abra **Financeira > Depósitos**, a área de tesouraria do menu atual. Escolha **Novo depósito**, a caixa e a conta bancária do mesmo estabelecimento. A empresa é sempre a empresa da sessão; não existe envio automático para o ERP ou uma operação bancária real.

Os novos depósitos são calculados a partir dos movimentos reais da caixa e da folha de origem. O numerário é sugerido pelo saldo acumulado e pode ser ajustado para depositar apenas parte do disponível. O valor não pode ultrapassar o saldo acumulado; o restante mantém-se em caixa. Os recebimentos por cheque são preenchidos automaticamente a partir da origem. A ficha e o talão mostram as referências dos movimentos e da folha de caixa. O mesmo recebimento por cheque não pode estar em dois depósitos ativos. Quando a origem não fornece o número ou o banco do cheque, apresenta-se a referência do recebimento com indicação explícita dessa falta.

Se os movimentos mudarem depois da preparação, atualize os movimentos de origem e prepare novamente antes da saída. Os depósitos anteriores e os seus históricos são preservados.

1. **Guardar rascunho** e **Preparar** não alteram saldos.
2. **Confirmar saída** utiliza o acumulado dos movimentos reais da caixa até à data indicada, incluindo dias já conferidos. Não exige abrir ou reabrir uma folha de caixa. Regista um único movimento de transferência para depósito e passa a **Em trânsito**.
3. **Registar entrega ao banco** passa a **Entregue ao banco**. A entrega não cria um crédito nem confirma fundos disponíveis.
4. **Talão interno / imprimir** abre o PDF para guardar ou imprimir através do visualizador do navegador. O documento identifica claramente que não é um comprovativo do banco.
5. Em **Anexos**, carregue PDF, PNG ou JPG até 5 MB. Administradores e gestores podem validar o comprovativo. É possível pré-visualizar os ficheiros.
6. **Reconciliar** apresenta movimentos do extrato da conta, com sugestões por valor, data e referência. Selecione e confirme o crédito e o montante. Repita para créditos separados ou parciais. A data-valor continua a ser a do extrato, sem antecipar a disponibilização dos fundos.

## Saldos, permissões e controlo

Os montantes do depósito, cheques e associações bancárias são `NUMERIC(16,2)` e são tratados como `Decimal` no servidor. A ligação ao livro de caixa e extratos existentes usa cêntimos inteiros, conforme o projeto.

A disponibilidade distingue numerário e cheques. Cartões, MB WAY e transferências não entram no numerário disponível. Consideram-se a abertura, movimentos reais e depósitos já saídos. Os valores Wintouch só são utilizados quando a conta de caixa está mapeada para a caixa escolhida. Sem registo de saldo suficiente, a saída fica bloqueada. Só um administrador pode autorizar uma exceção, com justificação registada.

Administradores, gestores e operadores podem preparar depósitos e confirmar a saída e a entrega dentro dos seus estabelecimentos. A consulta permite apenas leitura. A reconciliação, validação de comprovativos e reversão depois da saída exigem administrador ou gestor. As empresas utilizam os seus esquemas de base de dados; IDs de outra empresa não permitem consultar, alterar ou obter ficheiros.

As operações guardam pedido, movimento e histórico numa transação. A repetição da saída, cancelamento e confirmação com a mesma chave não duplica valores. Os créditos consomem o disponível no extrato através do controlo de reconciliação existente, incluindo alocações documentais; não se cria um segundo crédito bancário.

Comissões e cheques devolvidos são associados a débitos separados do extrato, com justificação e histórico. Não se apaga o cheque devolvido nem se transforma o depósito numa receita. A ficha mostra valor depositado, crédito associado, diferença, por reconciliar, comissões, devoluções e líquido no extrato.

## Cancelar e períodos encerrados

É possível cancelar rascunhos e preparados com justificação, sem movimentos. Depois da saída, é necessário confirmar o regresso físico de todos os valores. É criado um movimento de reversão ligado ao original, sem necessidade de abrir uma folha. Os movimentos são preservados. O estorno genérico do livro de caixa não substitui este fluxo.

As associações bancárias têm de ser desfeitas antes da reversão. Períodos contabilísticos encerrados bloqueiam alterações às operações e aos movimentos do extrato abrangidos. Reabra o período através do fluxo de entrega à contabilidade antes de corrigir esses dados.

## Dossiê contabilístico

O dossiê inclui `depositos/indice.json`, o PDF de cada talão e os mesmos anexos armazenados na ficha, sem uma nova importação. Inclui depósitos preparados, saídos, entregues ou associados ao extrato dentro do período. `depositos/pendencias.json` identifica comprovativos em falta ou por validar e créditos por reconciliar. Os indicadores da entrega à contabilidade incluem os depósitos e os comprovativos pendentes.

## Instalação e verificações

Execute `alembic upgrade head` no backend. Migrações:

- `0026_bank_deposits.py`: depósitos, cheques, associações ao extrato e comprovativos, em todos os esquemas das empresas.
- `0027_deposit_receipt_sources.py`: reserva dos recebimentos de cheques importados.

Testes: `tests/test_deposits.py` (numerário, cheques, misto, contagem, edição, saldo insuficiente, repetição, duplicação de cheques, parcial, cancelamentos, comprovativos, PDF, dossiê, comissões, devoluções, períodos e isolamento entre empresas). `scripts/test-deposits-ui.py` usa dados de demonstração identificados para verificar o fluxo e a responsividade. Os testes não executam operações bancárias reais.

O acumulado corresponde aos movimentos reais de numerário, excluindo saldos transportados de fundo de maneio na abertura, e desconta os depósitos confirmados. Não soma novamente fundos de abertura de folhas de conferência. A conta de destino pertence ao mesmo estabelecimento. Só são utilizados recebimentos de cheques atribuíveis à caixa selecionada. A integração de recebimentos atualmente disponível é Wintouch; o fluxo local de depósitos e reconciliação não exige ERP. A migração 0028 permite registar a transferência sem uma sessão de caixa aberta.

O fundo de maneio importado na abertura representa saldo transportado e permanece na caixa. Não integra os movimentos reais nem o acumulado disponível para depósito, mesmo quando o POS repete a entrada em cada abertura. Mantém-se na conferência como fundo a manter. Reforços efetivos de dinheiro devem ser identificados como entradas reais distintas do saldo de abertura.
