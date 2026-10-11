# Importação de vendas Wintouch

Em **Integrações → Importar vendas Wintouch**, escolha loja e datas e execute **Importar vendas**. O administrador da empresa pode importar até um ano por execução. Em demonstração, o formulário exige confirmação explícita. A ligação e empresa API vêm exclusivamente da configuração central da empresa autenticada.

## Dados disponíveis

- **Movimentos reais → Recebimentos Wintouch**: pagamentos individualizados com documento, data de origem, entidade, meio e montante. **Ver documentos de venda** apresenta as faturas separadamente dos pagamentos.
- **Movimentos previstos → Saldos a crédito Wintouch**: valores em aberto, vencimento e entidade, obtidos da conta corrente Wintouch. Efetive o recebimento na Wintouch e volte a importar o período; o saldo atualizado substitui o anterior. Estes registos não usam o botão de liquidação dos previstos manuais.
- Faturas, faturas-recibo, faturas simplificadas e notas de crédito de venda são selecionadas pelas classes 5, 3, 40 e 6, confirmadas nos tipos de documentos da instalação. Orçamentos, encomendas, guias e compras ficam excluídos. Os recibos de recebimento são consultados separadamente.

## Consistência

Cada ID de documento, pagamento ou pendente fica único por empresa API, no esquema da empresa GPGest. Repetir o período atualiza os registos. Pagamentos não são calculados a partir do total da fatura. Os totais documentais usam bases tributáveis e IVA, sem somar descontos novamente. Apenas EUR é suportado e os montantes são lidos com Decimal e convertidos em cêntimos.

O estado final da conta corrente distingue liquidação de crédito. Meios sem estado explícito só são aceites pelos pares de tipo e mecanismo observados na instalação: numerário, transferência, cartões, cheque e cartão refeição. Conta corrente, consumo interno e o código de desconto observado não são recebimentos. Meios ou estados desconhecidos interrompem a execução, em vez de serem classificados por aproximação. Outros clientes devem validar os meios configurados antes de usar a importação.

O adaptador lê `document_types`, `document_series`, `currencies`, `payment_methods`, `credit_account_states`, `product_documents`, `payablesreceivables` e `settlement_documents`, com páginas de 100 até uma página vazia. Os detalhes documentais permitem obter `Payments` e `SettlementDetails`. A ausência de pagamentos/estados obrigatórios, IDs repetidos, totais inconsistentes ou qualquer falha cancela a gravação completa. Histórico em `wt_sync_runs`, recurso `sales`.

O snapshot financeiro fica em `wt_sale_records`, criado pela migração `0005` em todas as empresas. A escolha da loja associa os documentos da ligação a essa loja; não constitui um mapeamento automático de estabelecimentos Wintouch. Uma associação existente a outra loja é rejeitada. Os dados não são eliminados: registos substituídos ou ausentes no snapshot completo do período ficam inativos. Não se alteram períodos fora do âmbito importado.

As datas sem offset mantêm a data de calendário indicada pela origem, sem inventar um fuso. As datas dos pagamentos constam dos movimentos; o período de importação seleciona os documentos de venda e os recibos desse intervalo. Pagamentos de uma fatura selecionada podem ter data fora do intervalo. Pendentes representam o saldo atual, incluindo liquidações posteriores à emissão.

## Limites atuais

É um snapshot de consulta dos movimentos Wintouch. Não abre nem fecha caixas, não escreve na Wintouch e não altera saldos, relatórios ou dashboard dos movimentos locais. Não concilia automaticamente um pagamento Wintouch com um lançamento manual. As duas origens são apresentadas em tabelas separadas para que não sejam somadas duas vezes.

Notas de crédito não são consideradas reembolsos pagos sem um pagamento identificado. Anulações com `DeletedDate` são preservadas como documento anulado e os seus pagamentos não são publicados como reais. Recibos com estado de processamento não validado são rejeitados. Descontos, retenções e valores pendentes não são convertidos automaticamente em dinheiro recebido.

Limites: timeout de 20 segundos por chamada, verificação de três minutos entre chamadas e 50 000 registos por coleção. Não existe tarefa automática; a importação é manual e usa apenas GET na origem.

## Validação em 11/10/2026

Primeira importação autorizada, de 01/10/2026 a 11/10/2026, na única empresa com API ativa e única loja: 70 documentos consultados, 5 documentos de venda e 4 pagamentos guardados, sem pendentes nesse período. Segunda execução atualizou os mesmos 9 registos, sem criar duplicados. Credenciais e dados pessoais não foram incluídos nos logs de validação.
