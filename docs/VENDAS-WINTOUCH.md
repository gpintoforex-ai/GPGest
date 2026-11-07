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

## Movimentos de stock

Com o controlo de stock ativo (data de início em Artigos → Parâmetros), a importação lê também as
linhas de artigo de todos os documentos cujo tipo a Wintouch marca como movimentando stock
(`IsStockEnabled`), a partir dessa data, e aplica-os ao stock logo a seguir, com o utilizador que
importou. O sentido vem do tipo de documento (`StockMode`: entra, sai ou transfere):

| Documentos Wintouch (classe) | Movimento na IAZELA |
|---|---|
| Faturas, faturas-recibo, FS, guias a cliente, pedidos, amostras, contas de mesa (3, 5, 7, 19, 27, 39, 40) | Venda (saída, com a venda líquida) |
| Notas de crédito e devoluções de cliente (4, 6, 8, 28) | Devolução de cliente, ao custo da venda original |
| Faturas de compra e guias de entrada (32, 34, 36) | **Nenhum**: as compras entram no stock só pela receção na aplicação (ver abaixo) |
| Devoluções e notas de crédito de fornecedor (33, 35, 37) | Devolução a fornecedor, só quando a compra original foi integrada; senão, por rever |
| Entradas de stock (10) e outros tipos que entram | Acerto de entrada, com o custo da linha |
| Saídas de stock (11), consumo interno (20) e outros que saem | Acerto de saída |
| Transferências (12) | Transferência entre os armazéns associados ao ERP |

Os documentos só de stock (entradas, saídas, transferências, compras, guias) ficam guardados à parte
(tipo `stock`) e não aparecem nas vendas, nos pagamentos nem na folha de caixa. No mesmo dia, as
entradas são aplicadas antes das saídas.

- **Lotes:** o lote indicado na linha da Wintouch (`products/{id}/batches`, por artigo: número e validade; `products/product_batches` ignora a paginação) é
  usado na entrada e na saída de um artigo que controla lotes cá; numa saída, o lote tem de existir no
  stock local. Sem lote na Wintouch, a saída segue o FEFO e a entrada fica para registo manual. Num
  artigo sem lotes cá, o lote da Wintouch é ignorado.
- **Armazém:** o escolhido na sincronização; sem escolha, o armazém ligado ao armazém Wintouch da
  linha (Tabelas → Armazéns) ou o único armazém ativo da loja.
- **Devoluções:** a nota de crédito volta ao stock ao custo e no lote da venda original, quando a
  linha de origem (`Sources`) já foi integrada; caso contrário fica listada para registo manual. A
  devolução a fornecedor liga-se à compra original quando esta foi integrada; sem ela fica por rever
  (regista-se em «Devolver mercadoria» na receção da aplicação, para sair o lote recebido).
- **Compras:** a entrada de stock é a receção de mercadoria na aplicação, com lote, validade e custo
  conferidos; as vendas da Wintouch são as saídas. Uma compra da Wintouch já recebida na aplicação
  (associação ao documento ERP, ou o mesmo NIF, tipo e número) é ignorada e contada como «já recebida»;
  as outras ficam por rever, sem entrada automática, para o mesmo stock não entrar duas vezes. As
  compras da Wintouch integradas antes desta regra mantêm-se (e os seus estornos e devoluções).
- **Uma vez por linha:** a identidade é a linha da Wintouch; repetir o período não volta a
  movimentar o stock (as linhas só são relidas quando a versão do documento muda). Uma venda manual do mesmo documento e artigo impede a integração (e vice-versa).
- **Documento anulado depois de integrado:** movimento inverso ligado a cada movimento (estorno: a
  venda volta ao stock, a compra sai como devolução ao fornecedor, o acerto é invertido), sem apagar
  movimentos; devoluções e transferências anuladas ficam em conflito para revisão. **Documento alterado** (linha mudada ou removida): fica em conflito,
  sem novos movimentos; reveja e registe o acerto manualmente.
- **Ficam por aplicar, listadas no resultado:** artigos sem ficha associada, entradas de artigos com
  lotes sem lote na Wintouch, lote sem entrada no stock local, armazém por definir, transferências com
  armazéns por associar e linhas recusadas pelas regras de stock (por exemplo, stock insuficiente). Uma linha
  recusada não impede as restantes; repetir a importação volta a tentá-la.

**Sincronizar a partir dos movimentos de stock:** em Artigos → Movimentos, o administrador usa
**Sincronizar movimentos**, escolhe o período (atalhos ou datas), a loja e, se houver vários, o armazém. A
sincronização corre em segundo plano (`POST /api/integrations/wintouch/sales/start`) e a janela mostra
uma barra de progresso real: etapa em curso e documentos tratados de um total (leitura dos documentos
e aplicação ao stock); nas etapas sem total conhecido a barra fica em movimento. Pode fechar a
janela: ao reabrir, o acompanhamento é retomado (`/sales/running`). No fim mostra os registos importados
e o resultado no stock, com as linhas por aplicar. Só uma sincronização de vendas corre de cada vez por
empresa; uma execução sem sinal de vida há dez minutos é dada como interrompida.

Cada aplicação fica na auditoria (`vendas_wintouch_stock`) com as quantidades de vendas, devoluções,
estornos, pendentes e conflitos. Sem data de início do controlo de stock, a importação não lê linhas
nem altera o stock.

## Consistência

Cada ID de documento, pagamento ou pendente fica único por empresa API, no esquema da empresa GPGest. Repetir o período atualiza os registos. Pagamentos não são calculados a partir do total da fatura. Os totais documentais usam bases tributáveis e IVA, sem somar descontos novamente. Apenas EUR é suportado e os montantes são lidos com Decimal e convertidos em cêntimos.

O estado final da conta corrente distingue liquidação de crédito. Meios sem estado explícito só são aceites pelos pares de tipo e mecanismo observados na instalação: numerário, transferência, cartões, cheque e cartão refeição. Conta corrente, consumo interno e o código de desconto observado não são recebimentos. Meios ou estados desconhecidos interrompem a execução, em vez de serem classificados por aproximação. Outros clientes devem validar os meios configurados antes de usar a importação.

O adaptador lê `document_types`, `document_series`, `currencies`, `payment_methods`, `credit_account_states`, `product_documents`, `payablesreceivables` e `settlement_documents`, com páginas de 100 até uma página vazia. Os detalhes documentais permitem obter `Payments` e `SettlementDetails`. A ausência de pagamentos/estados obrigatórios, IDs repetidos, totais inconsistentes ou qualquer falha cancela a gravação completa. Histórico em `wt_sync_runs`, recurso `sales`.

O snapshot financeiro fica em `wt_sale_records`, criado pela migração `0005` em todas as empresas. A escolha da loja associa os documentos da ligação a essa loja; não constitui um mapeamento automático de estabelecimentos Wintouch. Uma associação existente a outra loja é rejeitada. Os dados não são eliminados: registos substituídos ou ausentes no snapshot completo do período ficam inativos. Não se alteram períodos fora do âmbito importado.

As datas sem offset mantêm a data de calendário indicada pela origem, sem inventar um fuso. As datas dos pagamentos constam dos movimentos; o período de importação seleciona os documentos de venda e os recibos desse intervalo. Pagamentos de uma fatura selecionada podem ter data fora do intervalo. Pendentes representam o saldo atual, incluindo liquidações posteriores à emissão.

## Limites atuais

É um snapshot de consulta dos movimentos Wintouch. A importação não abre nem fecha caixas, não escreve na Wintouch e não gera lançamentos financeiros locais (as saídas de stock descritas acima são a exceção, só com o controlo de stock ativo). Na **Folha de caixa**, os créditos e débitos Wintouch do dia e da loja entram automaticamente nos totais, juntamente com todos os movimentos reais manuais da mesma loja/dia, de todas as sessões. Os valores ficam conservados no relatório ao fechar. A atribuição do numerário Wintouch a uma caixa para autorizar pagamentos continua única por loja/dia; o total diário apresentado na folha não disponibiliza dinheiro de outras caixas. Não concilia automaticamente um pagamento Wintouch com um lançamento manual. A tabela apresenta ambas as origens identificadas, sem gerar um segundo lançamento local. Anexos e estornos locais só estão disponíveis nos registos GPGest; os registos importados são geridos na Wintouch.

Notas de crédito não são consideradas reembolsos pagos sem um pagamento identificado. Anulações com `DeletedDate` são preservadas como documento anulado e os seus pagamentos não são publicados como reais. Recibos com estado de processamento não validado são rejeitados. Descontos, retenções e valores pendentes não são convertidos automaticamente em dinheiro recebido.

Limites: timeout de 20 segundos por chamada, verificação de três minutos entre chamadas e 50 000 registos por coleção. Não existe tarefa automática; a importação é manual e usa apenas GET na origem.

## Validação em 11/10/2026

Primeira importação autorizada, de 01/10/2026 a 11/10/2026, na única empresa com API ativa e única loja: 70 documentos consultados, 5 documentos de venda e 4 pagamentos guardados, sem pendentes nesse período. Segunda execução atualizou os mesmos 9 registos, sem criar duplicados. Credenciais e dados pessoais não foram incluídos nos logs de validação.
