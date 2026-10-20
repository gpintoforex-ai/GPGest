# Compras e Stock

O menu reúne Visão geral, Encomendas a fornecedores, Receções de mercadoria, Compras, Devoluções a fornecedores e Movimentos de stock. As fichas de artigos, armazéns e fornecedores continuam nos menus existentes.

Na tabela de Armazéns, a ação **Remover** exige confirmação e a permissão de ajuste de stock no estabelecimento. Só permite remover armazéns sem movimentos, inventários ou documentos de compra associados, incluindo rascunhos. Um saldo de stock a zero não permite apagar o histórico. A remoção fica registada na auditoria.

## Circuito

1. Crie uma encomenda, selecione fornecedor, armazém e artigos e guarde o rascunho. Confirme a encomenda quando estiver revista. A confirmação não cria stock, dívida ou envio de email.
2. Na encomenda confirmada, escolha **Receber mercadoria**. Ajuste a quantidade da entrega, indique rejeições, lotes e validades e confirme. As quantidades aceites entram no livro de stock existente. Uma receção pode reunir linhas de várias encomendas do mesmo fornecedor/estabelecimento; também pode ser criada sem encomenda.
3. Na receção, escolha **Associar fatura**. Pode juntar várias receções e selecionar uma compra já existente, incluindo documentos identificados no e-Fatura, ou um documento do catálogo ERP. As validações exigem a mesma identidade e montante. Uma compra nova usa a tabela `purchases`, aparece na conferência documental e tem proposta de despesa no mecanismo existente. A associação a uma receção não cria outra entrada.
4. Na compra direta de mercadorias, a confirmação cria uma receção relacionada e aplica os mesmos controlos de stock, lote e validade. Serviços e despesas não movimentam stock.
5. Use **Devolver mercadoria** na receção original. Indique quantidade e motivo. A confirmação cria a saída física; a posterior nota de crédito cria o documento financeiro sem repetir a saída. As devoluções sem nota de crédito aparecem na visão geral.
6. Use o **Plano de pagamentos** ou a **Conferência documental** existentes para pagamentos totais/parciais, distribuição e reconciliação. Registar/conferir a compra não a marca como paga.

Os estados de conferência, existência de anexo, pagamento e associação ao ERP são apresentados separadamente. A rubrica é definida **por linha**: cada linha de uma compra ou nota de crédito pode ter rubrica própria (por exemplo, mercadorias e serviços na mesma fatura); sem ela, usa a rubrica por omissão do documento e, depois, a do fornecedor. No modo autónomo, todas as linhas têm de ter rubrica para alimentar o plano local. O pagamento continua a ser um só por fatura: o movimento previsto fica com a rubrica de maior valor e guarda a repartição por rubrica (tabela `planned_allocations`, migração **0051**), apresentada nos movimentos previstos. A rubrica de um previsto com várias rubricas só se altera nas linhas da compra. A proposta de despesa usa a rubrica de cada linha. Ao liquidar, o movimento real fica com a rubrica principal do previsto. Uma compra confirmada não pode ser alterada através da edição livre do montante do previsto.

## Envio e documentos

As encomendas podem ser duplicadas como rascunhos, impressas/consultadas em PDF e preparadas para envio pelo SMTP configurado. A pré-visualização congela o PDF e a mensagem no mecanismo `EmailDispatch` existente. O envio exige confirmação; o estado de envio e a data são independentes do estado da encomenda. Uma aceitação SMTP não é confirmação de leitura. Resultados incertos bloqueiam nova preparação automática para evitar duplicar envios.

Os anexos indicados neste circuito são ligações HTTPS: os ficheiros permanecem no Drive ou no serviço de origem. Para validar o ficheiro, associar um PDF Drive ao documento ou incluí-lo no dossiê contabilístico, utilize a conferência documental e o assistente existentes. Uma ligação HTTPS arbitrária não é descarregada automaticamente para o dossiê.

## Stock e valorização

As entradas, saídas, transferências, perdas, ajustes e saldos iniciais reutilizam `StockEntry`, `StockLot`, `StockWarehouse` e as permissões existentes. O novo histórico permite filtrar período, artigo, tipo e estabelecimento e abrir o documento de compras de origem. Mostra utilizador, data/hora, unidade, lote, validade e custo/valor quando autorizado.

Os movimentos confirmados não são apagados nem editados livremente. Uma correção física exige devolução ou movimento inverso/ajuste justificado no módulo existente. Transferências mantêm os dois movimentos relacionados nos armazéns.

Diferenças entre custo provisório e faturado geram entradas `valuation` com quantidade zero e ligação ao movimento original. A parte ainda incorporada no stock atualiza o custo médio; a parte consumida fica identificada separadamente. Os custos gravados em vendas anteriores e as suas margens não são reescritos. O IVA não recuperável integra o custo de acordo com a configuração existente.

## Isolamento e repetição

As rotas usam a sessão da empresa e o âmbito dos estabelecimentos autorizados. Gestores/administradores com acesso a custos podem escrever; leitura e custos respeitam as permissões existentes. Artigos ERP permanecem apenas de leitura e o stock declarado como responsabilidade do ERP não aceita confirmações físicas locais.

Rascunhos têm revisão e chave de submissão. Confirmações partilham o bloqueio transacional do stock; movimentos usam chaves determinísticas e ligações por linha. Falhas revertem a operação completa. As quantidades de origem são novamente verificadas sob bloqueio. Entregas superiores à encomenda e divergências exigem aceitação explícita. Quantidades rejeitadas não encerram o saldo da encomenda.

As unidades são as unidades base dos artigos, com a precisão configurada. Conversões não configuradas são rejeitadas; o módulo não inventa fatores de conversão. A pesquisa de artigos aceita código e código de barras.

## Migração e integração

A migração **0046**, após **0045**, cria apenas `procurement_documents` e `procurement_links` e acrescenta a ligação opcional/única da previsão à compra em `planned_movements`. Fornecedores, artigos, lotes, compras, pagamentos, anexos e livros de stock não são duplicados em novas tabelas.

No modo integrado, este circuito identifica os documentos locais e as associações ao catálogo ERP existente. Não foi acrescentado um conector de escrita de encomendas/compras/stock no ERP. Compras importadas exigem uma receção local explícita para associar mercadorias, e artigos cujo stock pertence ao ERP não admitem entradas locais. A confirmação nunca é apresentada como sincronização ERP concluída.

## Validação

- `backend/tests/test_procurement.py`: circuito 20 → 12 → fatura de 12 → 8 → devolução de 2 → nota de crédito; pagamento parcial e saldo; compra direta com receção relacionada; serviços sem stock; lotes; duplicados; falhas transacionais; submissões concorrentes; valorização; permissões; associação a compra existente; PDF; SMTP simulado; ligação ao plano de pagamentos; migração reversível/idempotente.
- Regressão com os testes de stock, movimentos previstos e liquidações locais de liquidez.
- `scripts/test-procurement-ui.py`: menu, formulário, escolha de artigo, rascunho, janelas modais, telemóvel e contraste nos temas claro/escuro, apenas com APIs simuladas.
- Compilação TypeScript/Vite. Os testes de escrita usam uma base temporária, eliminada após execução. Nenhum email real foi enviado pelos testes.

## Despesas por rubrica e compras do ERP

Em **Contabilidade → Despesas por rubrica** (gestores e administradores), as despesas são somadas por rubrica pela **data das faturas de compra**:

- **Compras do ERP** (cópia só de leitura obtida em «Atualizar compras do ERP», que usa a consulta Wintouch existente). A rubrica é, por esta ordem, a definida localmente no documento ou a do fornecedor (procurado pelo NIF em Tabelas → Fornecedores; um NIF com rubricas diferentes fica por classificar). Um documento pode ser repartido por várias rubricas, com a soma igual ao total. Definir a rubrica não altera o ERP e fica na auditoria (migração **0052**, tabela `erp_document_rubrics`).
- **Compras confirmadas na IAZELA**, pela repartição das suas linhas.

Notas de crédito subtraem e documentos anulados no ERP não entram. Cada documento conta uma vez: uma compra local ligada a um documento ERP é contada pelo ERP. Documentos e-Fatura ainda não lançados não entram. Os documentos sem rubrica aparecem em **Por classificar**, com a ação **Definir rubrica**.

Os saldos por pagar das compras do ERP (sincronização Wintouch) já aparecem em Movimentos previstos e na previsão de liquidez; passam a mostrar a rubrica do documento ou do fornecedor. A sincronização guarda o NIF do fornecedor desses saldos a partir da próxima execução.

## Inventário inicial valorizado

Em **Compras e Stock → Importações → Inventário inicial valorizado** (permissões de ajustar stock e ver custos), importe um CSV com **código do artigo, quantidade e custo unitário sem IVA** e, opcionalmente, armazém (nome), lote e validade. Indique a data de referência (por exemplo, 01/01/2026) e o motivo.

- A pré-visualização não grava nada e bloqueia: artigos sem ficha, inativos ou controlados pelo ERP; armazém fora do âmbito; quantidade inválida ou fora da precisão do artigo; custo em falta ou negativo; lote em falta (artigos com lotes); linhas repetidas; artigo que já tenha stock inicial nesse armazém. Custo zero é aceite com aviso.
- A confirmação é atómica (tudo ou nada) e idempotente: cria movimentos «stock inicial» com custo, com auditoria. Não altera vendas, compras nem pagamentos. Correções posteriores fazem-se por ajuste justificado.
- O custo médio destas entradas (e das compras confirmadas) é o custo usado na margem bruta do painel. Com IVA não recuperável configurado no stock, o custo inclui o IVA.
