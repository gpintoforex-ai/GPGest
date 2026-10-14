# Importação de vendas Wintouch

A importação inclui entradas e retiradas manuais de caixa através de
`GET item_documents`, com `Payments,CEntity`. As classes 21 (entrada) e 22
(retirada), respetivamente `CashFlowMode=1` e `-1`, foram confirmadas na API
configurada. Os pagamentos usam os meios e estados já validados e entram nos
Movimentos reais e na Folha de caixa com a origem Wintouch. Aberturas e fechos
de caixa são excluídos, tal como documentos anulados ou fora do período.
Reimportar atualiza os mesmos identificadores de pagamento, sem duplicação.

Ao abrir uma folha de caixa, uma entrada de numerário Wintouch já importada,
do dia e loja selecionados e ainda não usada noutra abertura, é sugerida como
fundo inicial. O valor pode ser alterado pelo utilizador. A associação fica
registada e essa entrada não volta a somar aos créditos da mesma folha nem ao
saldo disponível da sessão. Com várias entradas possíveis não há seleção
automática. O mapeamento existente das caixas Wintouch é respeitado; sem entrada
adequada mantém-se a sugestão da última abertura local.

Em **Integrações → Importar vendas Wintouch**, escolha loja e datas e execute **Importar vendas**. O administrador da empresa pode importar até um ano por execução. Em demonstração, o formulário exige confirmação explícita. A ligação e empresa API vêm exclusivamente da configuração central da empresa autenticada.

## Dados disponíveis

- **Movimentos reais**: tabela única com movimentos locais e pagamentos Wintouch, documento, data, entidade, meio, montante e coluna **Origem**. Os filtros e a paginação aplicam-se às duas origens em conjunto. **Ver documentos de venda Wintouch** alterna a mesma tabela para consulta de faturas; **Voltar aos movimentos reais** repõe os pagamentos e movimentos locais.
- **Movimentos previstos**: saldos de vendas e compras em aberto, com vencimento, entidade e origem. Administradores e gestores podem usar **Liquidar na Wintouch**, escolher documento, série, meio de pagamento e montante, e confirmar o envio. O GPGest consulta o saldo atual, cria o documento de liquidação pela API e confirma o identificador e montante na origem antes de atualizar o saldo. A confirmação do formulário escreve na Wintouch, mesmo quando a empresa GPGest está em demonstração. Os testes não enviam liquidações reais.

Cada envio tem um identificador único e auditoria persistida antes da chamada externa. Uma falha com resultado desconhecido bloqueia novas liquidações desse saldo para impedir duplicados; deve verificar o documento na Wintouch antes de resolver a operação. A liquidação suporta EUR e documentos sem retenções. A atualização dos movimentos reais segue a importação de vendas/recebimentos.
- Faturas, faturas-recibo, faturas simplificadas e notas de crédito de venda são selecionadas pelas classes 5, 3, 40 e 6, confirmadas nos tipos de documentos da instalação. Orçamentos, encomendas, guias e compras ficam excluídos. Os recibos de recebimento são consultados separadamente.

## Consistência

Cada ID de documento, pagamento ou pendente fica único por empresa API, no esquema da empresa GPGest. Repetir o período atualiza os registos. Pagamentos não são calculados a partir do total da fatura. Os totais documentais usam bases tributáveis e IVA, sem somar descontos novamente. Apenas EUR é suportado e os montantes são lidos com Decimal e convertidos em cêntimos.

O estado final da conta corrente distingue liquidação de crédito. Meios sem estado explícito só são aceites pelos pares de tipo e mecanismo observados na instalação: numerário, transferência, cartões, cheque e cartão refeição. Conta corrente, consumo interno e o código de desconto observado não são recebimentos. Meios ou estados desconhecidos interrompem a execução, em vez de serem classificados por aproximação. Outros clientes devem validar os meios configurados antes de usar a importação.

O adaptador lê `document_types`, `document_series`, `currencies`, `payment_methods`, `credit_account_states`, `product_documents`, `payablesreceivables` e `settlement_documents`, com páginas de 100 até uma página vazia. Os detalhes documentais permitem obter `Payments` e `SettlementDetails`. A ausência de pagamentos/estados obrigatórios, IDs repetidos, totais inconsistentes ou qualquer falha cancela a gravação completa. Histórico em `wt_sync_runs`, recurso `sales`.

O snapshot financeiro fica em `wt_sale_records`, criado pela migração `0005` em todas as empresas. A escolha da loja associa os documentos da ligação a essa loja; não constitui um mapeamento automático de estabelecimentos Wintouch. Uma associação existente a outra loja é rejeitada. Os dados não são eliminados: registos substituídos ou ausentes no snapshot completo do período ficam inativos. Não se alteram períodos fora do âmbito importado.

As datas sem offset mantêm a data de calendário indicada pela origem, sem inventar um fuso. As datas dos pagamentos constam dos movimentos; o período de importação seleciona os documentos de venda e os recibos desse intervalo. Pagamentos de uma fatura selecionada podem ter data fora do intervalo. Pendentes representam o saldo atual, incluindo liquidações posteriores à emissão.

## Limites atuais

É um snapshot de consulta dos movimentos Wintouch. A importação não abre nem fecha caixas, não escreve na Wintouch e não gera lançamentos locais. Na **Folha de caixa**, os créditos e débitos Wintouch do dia e da loja entram automaticamente nos totais, juntamente com todos os movimentos reais manuais da mesma loja/dia, de todas as sessões. Os valores ficam conservados no relatório ao fechar. A atribuição do numerário Wintouch a uma caixa para autorizar pagamentos continua única por loja/dia; o total diário apresentado na folha não disponibiliza dinheiro de outras caixas. Não concilia automaticamente um pagamento Wintouch com um lançamento manual. A tabela apresenta ambas as origens identificadas, sem gerar um segundo lançamento local. Anexos e estornos locais só estão disponíveis nos registos GPGest; os registos importados são geridos na Wintouch.

Notas de crédito não são consideradas reembolsos pagos sem um pagamento identificado. Anulações com `DeletedDate` são preservadas como documento anulado e os seus pagamentos não são publicados como reais. Recibos com estado de processamento não validado são rejeitados. Descontos, retenções e valores pendentes não são convertidos automaticamente em dinheiro recebido.

Limites: timeout de 20 segundos por chamada, verificação de três minutos entre chamadas e 50 000 registos por coleção. Não existe tarefa automática; a importação é manual e usa apenas GET na origem.

## Validação em 11/10/2026

Primeira importação autorizada, de 01/10/2026 a 11/10/2026, na única empresa com API ativa e única loja: 70 documentos consultados, 5 documentos de venda e 4 pagamentos guardados, sem pendentes nesse período. Segunda execução atualizou os mesmos 9 registos, sem criar duplicados. Credenciais e dados pessoais não foram incluídos nos logs de validação.
