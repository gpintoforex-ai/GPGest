# Tipos de documento de compra

**Tabelas → Tipos de Documentos** define os tipos de documento de compra usados nas **Receções de mercadoria** e o efeito financeiro que geram ao confirmar a receção.

## Tabela

Segue o comportamento comum das Tabelas ([TABELAS-COMPORTAMENTO-COMUM.md](TABELAS-COMPORTAMENTO-COMUM.md)): pesquisa, filtros por **natureza**, **condição de pagamento** e estado, criar, editar, ativar/desativar e remover. Um tipo usado num documento (receção ou compra) **não pode ser eliminado**, só desativado; tipos inativos não aparecem em novas receções.

Campos (tabela `purchase_document_types`, migração **0068**): código (único), descrição, natureza (`compra` | `nota_credito_compra`), sinal (+1 fatura, −1 nota de crédito, derivado da natureza), condição de pagamento (`dinheiro` | `credito`), meio de pagamento por omissão, dias de vencimento (só a crédito), série, «gera movimento de caixa», «gera dívida», ativo, criado/atualizado em. Os dois indicadores derivam da condição (dinheiro → caixa; crédito → dívida) quando não são indicados, mas ficam guardados para permitir exceções. A origem (IAZELA/ERP), o identificador e o código na Wintouch e a última sincronização ficam na ligação ao ERP comum às tabelas (`erp_links`).

Tipos pré-carregados na primeira utilização (também em empresas novas):

| Código | Descrição | Natureza | Sinal | Pagamento | Caixa | Dívida |
|---|---|---|---|---|---|---|
| FC | Fatura de Compra | Compra | +1 | Crédito (30 dias) | não | sim |
| FCD | Fatura de Compra a Dinheiro | Compra | +1 | Dinheiro (numerário) | sim | não |
| NCC | Nota de Crédito de Compra | Nota de crédito | −1 | Crédito (30 dias) | não | sim |
| NCCD | Nota de Crédito Compra Dinheiro | Nota de crédito | −1 | Dinheiro (numerário) | sim | não |

## Importação da Wintouch

O botão **Sincronizar** (modo integrado) lê os tipos de documento da Wintouch e mostra a pré-visualização antes de gravar: novos, já existentes (ligados pelo identificador da Wintouch ou, em alternativa, pelo código) e conflitos (mesmo código com dados diferentes, ou mesma descrição sem código comum — a decidir: «Usar o ERP» liga e atualiza; «Manter por resolver» não altera nada). Só leitura: nada é enviado à Wintouch.

Só entram os documentos de compra financeiros, pela classe da Wintouch (verificada na API): 32 fatura de compra a dinheiro → compra/dinheiro; 33 devolução de compra a dinheiro → nota de crédito/dinheiro; 34 fatura de compra → compra/crédito; 35 nota de crédito de fornecedor → nota de crédito/crédito. Orçamentos, encomendas e guias (30, 31, 36, 37) ficam de fora. A natureza e a condição podem ser corrigidas depois na ficha (não são reescritas pela sincronização seguinte). Repetir não duplica. Cada execução fica no histórico de sincronizações (`tabela:document_types`: lidos, criados, atualizados, erros) e na auditoria. Em modo local, a importação fica indisponível com o motivo; os tipos locais funcionam sempre.

## Receções de mercadoria

A receção (manual ou a partir de fatura) pede o **tipo de documento** (obrigatório, só ativos), o número e a data do documento do fornecedor e, conforme o tipo, o **meio de pagamento** (a dinheiro; pré-preenchido com o do tipo, editável) e o **vencimento** (a crédito; proposto pelo prazo da ficha do fornecedor ou pelos dias do tipo, editável). Uma nota de crédito a crédito pode **abater faturas em aberto** do mesmo fornecedor. O efeito financeiro só acontece ao **confirmar** a receção, nunca em rascunho.

Ao confirmar (valor = total do documento × sinal), em `app/purchase_financials.py`:

- **Compra** (documento em dívida) com o tipo (`purchases.document_type_id`), ligada à receção; se já existir a mesma compra (e-Fatura ou outra origem) com o mesmo total, é reutilizada; um documento da Wintouch já importado com a mesma identidade é associado.
- **Crédito (FC/NCC):** movimento **previsto** na data de vencimento (FC saída, NCC entrada). Uma NC associada a faturas regista uma **compensação** (pagamento de documentos sem dinheiro, origem `compensacao`): a dívida da fatura e o previsto descem pelo valor da NC; o resto da NC, se houver, fica previsto.
- **Dinheiro (FCD/NCCD):** **movimento real** na data do documento — numerário na folha de caixa aberta da loja (exige caixa aberta; numerário insuficiente bloqueia, como na folha de caixa), outros meios na conta bancária associada ao meio — e a compra fica paga (sem dívida). FCD sai; NCCD (reembolso do fornecedor) entra.
- **Stock:** faturas dão entrada; **notas de crédito dão saída** dos artigos; artigos de serviço não movimentam stock.
- **Pagar a dívida:** liquidar o previsto (Movimentos previstos → liquidar) cria o movimento real e o pagamento da compra; a dívida e o previsto ficam a zero.
- **Rastreabilidade:** movimentos com origem `rececao`, referência à receção e documento «código + número»; a receção guarda o resumo (`financial`: compra, previsto, movimento, pagamentos/compensações).
- **Editar** uma receção confirmada desfaz e volta a gerar o efeito financeiro (bloqueado se a compra já tiver pagamentos de terceiros ou o previsto estiver liquidado). **Anular** estorna o stock e os movimentos reais (movimentos inversos, com motivo, sem apagar) e retira a compra e o previsto gerados pela receção.

### Receções confirmadas sem tipo

Receções confirmadas antes de existirem tipos de documento têm o stock mas nenhum efeito financeiro. Em **Compras → Receções**, a ação **Definir tipo** (administrador ou gestor) pede o tipo, o meio de pagamento ou o vencimento e gera a compra e o previsto ou o movimento real, como na confirmação. **O stock não muda.** Só aceita tipos de fatura (FC, FCD…): o stock dessas receções entrou como compra, e uma nota de crédito poria o stock e o dinheiro em sentidos opostos. Fica bloqueada se a receção já tiver tipo, compra ou fatura associada (o efeito financeiro vem dessa fatura). Fica registada na auditoria (`rececao_tipo_documento_definido`).

## Modos e sincronização

O efeito financeiro é **o mesmo nos dois modos**: no **modo integrado** a receção também cria o previsto (a crédito) ou o movimento real (a dinheiro), além da compra (associada ao documento da Wintouch quando já importado).

Para não contar duas vezes, o **previsto local de uma receção deixa de contar** quando o ERP já tem o documento: há associação ativa ao documento ERP, o documento de compra importado tem a mesma identidade (NIF, tipo, número) e total, ou a Wintouch lista um valor em aberto do mesmo fornecedor com o mesmo número (com ou sem o código do tipo à frente: «FT 2026A/12» = «2026A/12»). Em **Movimentos previstos** fica com o estado **No ERP** e 0 € por liquidar, e a liquidez passa a contar o valor em aberto da Wintouch. O pagamento faz-se a partir desse valor em aberto (liquidar o previsto local é recusado). Se a associação ao ERP for desfeita, o previsto local volta a contar.

O **movimento real** de um documento a dinheiro não se duplica: a sincronização da Wintouch não importa os pagamentos de documentos de compra como movimentos de caixa (só vendas e entradas/saídas de caixa). Se o pagamento ao fornecedor também for registado na Wintouch como saída de caixa, fica nas duas origens e tem de ser estornado num dos lados.

A aplicação funciona no servidor (sem modo offline no navegador) e não existe envio de movimentos financeiros para a Wintouch: o conector não grava caixa nem tesouraria. Todas as operações são idempotentes (chaves por receção/edição), por isso repetir um pedido não duplica movimentos.

## Testes

`backend/tests/test_purchase_document_types.py` (critérios de aceitação: tipos pré-carregados, regras da tabela e filtros; FCD 100 € → saída real; FC 100 € a 30 dias → dívida e previsto; NCC 20 € associada → dívida 80 €; NCCD 20 € → entrada real; pagar a FC → dívida a zero; caixa fechada/numerário insuficiente; anular; editar; previsto que deixa de contar quando o ERP tem o documento; tipo definido numa receção antiga sem mexer no stock; importação sem duplicados; importação indisponível em modo local) e `scripts/test-document-types-ui.py`, `scripts/test-invoice-receiving-ui.py`, `scripts/test-procurement-correction-ui.py`.
