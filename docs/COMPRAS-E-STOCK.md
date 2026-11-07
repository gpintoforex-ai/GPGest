# Compras e Stock

O menu reúne Visão geral, Encomendas a fornecedores, Receções de mercadoria, Compras, Devoluções a fornecedores e Movimentos de stock. As fichas de artigos, armazéns e fornecedores continuam nos menus existentes.

Na tabela de Armazéns, a ação **Remover** exige confirmação e a permissão de ajuste de stock no estabelecimento. Só permite remover armazéns sem movimentos, inventários ou documentos de compra associados, incluindo rascunhos. Um saldo de stock a zero não permite apagar o histórico. A remoção fica registada na auditoria.

## Circuito

1. Crie uma encomenda, selecione fornecedor, armazém e artigos e guarde o rascunho. Confirme a encomenda quando estiver revista. A confirmação não cria stock, dívida ou envio de email.
   Uma encomenda sem número do documento recebe o numerador sequencial **EC-0001, EC-0002, …**, o mesmo das encomendas preparadas nas Encomendas de clientes.
2. Na encomenda confirmada, escolha **Receber mercadoria**. Ajuste a quantidade da entrega, indique rejeições, lotes e validades e confirme. As quantidades aceites entram no livro de stock existente. Uma receção pode reunir linhas de várias encomendas do mesmo fornecedor/estabelecimento; também pode ser criada sem encomenda.
   **Encomendas por receber:** no topo de **Receções de mercadoria** estão as encomendas confirmadas com mercadoria pendente, as atrasadas assinaladas a vermelho. Cada uma mostra a previsão de entrega, o que falta receber e os **clientes à espera** (pedidos das Encomendas de clientes, os urgentes a vermelho). **Receber** abre a receção já com as quantidades pendentes.
   - Ao **confirmar**, as quantidades aceites que pertencem a encomendas feitas para pedidos de clientes ficam **reservadas automaticamente** a esses clientes, pela ordem das atribuições. O excedente fica em stock geral. A receção mostra o que foi reservado. Uma reserva impossível (por exemplo, stock já consumido) fica como aviso e não anula a receção.
   - Repetir a confirmação não volta a reservar.
   - Uma receção que já reservou para clientes não se anula nem se edita nas Compras: liberte as reservas no pedido e corrija com uma devolução ao fornecedor.
   - **Anexar PDF** guarda a guia ou a fatura no Arquivo digital e liga-a ao documento. Um ficheiro igual já arquivado é reutilizado.
   - Com o tipo de documento, a confirmação cria a compra e o movimento **real** (a dinheiro) ou **previsto** (a crédito, com vencimento). O previsto entra na **Previsão de liquidez**, e a receção tem a ligação para a abrir.
3. Na receção, escolha **Associar fatura**. Pode juntar várias receções e selecionar uma compra já existente, incluindo documentos identificados no e-Fatura, ou um documento do catálogo ERP. As validações exigem a mesma identidade e montante. Uma compra nova usa a tabela `purchases`, aparece na conferência documental e tem proposta de despesa no mecanismo existente. A associação a uma receção não cria outra entrada.
4. Na compra direta de mercadorias, a confirmação cria uma receção relacionada e aplica os mesmos controlos de stock, lote e validade. Serviços e despesas não movimentam stock.
5. Use **Devolver mercadoria** na receção original. Indique quantidade e motivo. A confirmação cria a saída física; a posterior nota de crédito cria o documento financeiro sem repetir a saída. As devoluções sem nota de crédito aparecem na visão geral.
5a. **Editar uma receção confirmada** (botão «Editar» na lista, enquanto não tiver fatura nem devolução associadas): reabre **a mesma janela do lançamento** — a «Receção a partir de fatura» (com o PDF e as linhas como foram gravadas: quantidade, bónus, preço, descontos, IVA, lote e validade) ou o formulário da receção manual («Receção — edição»). Ao gravar, a mesma receção é atualizada (`PUT /api/procurement/{id}/confirmed` ou `PUT /api/purchase-receiving/receipts/{id}`) e os movimentos de stock são gerados de novo; as ligações à encomenda são refeitas. As entradas anteriores são **apagadas** quando nada as usou depois (as receções de compra não têm obrigação fiscal); se o artigo já teve movimentos posteriores (vendas, lotes consumidos), apagar desacertaria o custo médio e os lotes, e faz-se um movimento inverso ao custo original. As entradas novas são geradas antes de retirar as antigas, para o stock nunca ficar negativo pelo caminho. A auditoria guarda as linhas anteriores. Com fatura ou devolução associadas, corrige-se por **Devolver mercadoria**. Os rascunhos editam-se diretamente («Editar»).

   **Anexo da compra:** quando a receção tem tipo de documento, a compra que gera recebe automaticamente, como anexo, o PDF de origem (a fatura lida do Arquivo digital e os PDF anexados à receção), com a associação confirmada no arquivo — na Conferência documental deixa de aparecer «sem anexo». Isto acontece ao confirmar e ao **editar**: a edição recria a compra e os anexos dela (incluindo os acrescentados à mão) e as associações do arquivo passam para a nova, sem duplicar. Ao **anular** a receção (ou editá-la retirando o tipo de documento), saem só as cópias de documentos do arquivo — o original fica no Arquivo digital, com a associação anulada; se a compra tiver anexos próprios, a operação é recusada com o nome desses anexos, para não se perderem. Associações rejeitadas ou anuladas à mão no arquivo não são refeitas.
6. Use o **Plano de pagamentos** ou a **Conferência documental** existentes para pagamentos totais/parciais, distribuição e reconciliação. Registar/conferir a compra não a marca como paga.

**Percurso da compra:** a ficha de qualquer documento de compras (encomenda — separador «Percurso» —, receção, fatura, devolução ou nota de crédito) mostra o processo completo: as etapas **Encomenda → Receção → Documento de compra → Conferência → Anexo → Pagamento → ERP** e, quando existem, **Devolução e nota de crédito**, cada uma com o estado (feito, parcial, por fazer ou sem documento). Abaixo ficam os documentos ligados linha a linha — incluindo as outras receções da mesma encomenda — com as quantidades (encomendada, recebida, faturada, devolvida, creditada e pendente) e, para cada compra, conferência, pagamento (pago e em falta), previsto e associação ERP; «Abrir» passa para o documento escolhido. É só consulta (`GET /api/procurement/{id}/process`): respeita as lojas e a permissão de ver custos do utilizador e não altera nada.

**Encomenda na receção a partir de fatura:** o quadro «Encomenda ao fornecedor» mostra, por linha, **Encomendado**, **Já recebido** (noutras receções), **Por receber** e **Nesta fatura**. Ao reabrir uma receção confirmada, o que ela própria recebeu não conta como já recebido — como na gravação —, para não aparecer como entrega a mais. **Consultar encomenda** abre ali o percurso da encomenda, sem sair da receção.

**Tipo de documento na receção:** cada receção indica o tipo de documento de compra (FC, FCD, NCC, NCCD…), que gera ao confirmar a compra, a dívida e o previsto ou o movimento real de caixa, e nas notas de crédito a saída de stock. Ver [TIPOS-DOCUMENTO-COMPRA.md](TIPOS-DOCUMENTO-COMPRA.md).

## Validades na receção

Ao **lançar** uma receção (manual, a partir de fatura ou a partir das Encomendas de clientes):
- **Artigos com lotes exigem validade.** Sem ela, a receção não é lançada e a mensagem diz que artigos e lotes faltam. Se o lote já existir com validade, essa conta. O rascunho pode ser gravado sem validade.
- **Validade a menos de 2 anos** da data do documento: aviso a âmbar na linha e no documento.
- **Validade a menos de 1 ano**: aviso a vermelho, e a receção fica com uma **reclamação ao fornecedor por fazer**. A lista **Reclamações de validade por fazer** aparece no topo de Receções de mercadoria. Em cada receção, **Registar reclamação** (como e quando) ou **Não reclamar** (porquê) guardam o registo. Fica no documento e na auditoria (`compra_circuito_reclamacao_validade`). A lista mostra também a etiqueta «Reclamar validade».

As linhas das janelas de receção mostram estes avisos junto ao campo Validade enquanto se preenche.

**Lote ou validade num artigo sem controlo de lotes:** antes eram descartados sem aviso; agora o lançamento é recusado com o motivo.
- Na receção a partir de fatura, a linha avisa e permite **ativar «Controlar lotes e validades» na ficha do artigo** ao lançar, se o artigo ainda não tiver stock. Fica na auditoria.
- Num artigo novo criado na receção, há um botão que marca o controlo de lotes.
- Se o artigo já tiver stock sem lote, a mensagem pede para ativar os lotes na ficha do artigo, indicando o lote do stock existente.
- Na receção manual, a mesma situação é recusada com a indicação para ativar os lotes na ficha.

## Devoluções por validade

Na ficha do fornecedor, separador **Validades e devoluções**, indica-se se o fornecedor **aceita a devolução de produtos a acabar a validade** e **quantos meses antes** avisar (1 a 36, 3 por omissão).

Em **Devoluções a fornecedores**, **Produtos a devolver por validade** lista os lotes em stock desses fornecedores cuja validade termina dentro desse aviso:
- artigo, lote, validade e dias que faltam (vermelho a 30 dias ou menos);
- quantidade, armazém e fornecedor.

O fornecedor de cada lote é o da compra que lhe deu entrada; se não estiver registado, é o fornecedor principal do artigo. Lotes já vencidos não aparecem. **Preparar devolução** abre a devolução já preenchida com o lote, a quantidade, o motivo «validade» e o último custo. Só confirmar a devolução movimenta o stock. A Visão geral das Compras mostra quantos lotes estão nesta lista.

## Receção a partir de fatura (PDF do Arquivo digital)

Em **Receções de mercadoria → Receção a partir de fatura**, escolha a fatura do fornecedor guardada no Arquivo digital (as já recebidas aparecem marcadas e não podem dar entrada outra vez). O original (PDF ou imagem) aparece ao lado da conferência, fixo enquanto se percorrem as linhas, e começa a ser mostrado enquanto a leitura/OCR decorre; **Ocultar/Mostrar fatura** alterna o painel e **Abrir noutra janela** abre-o à parte (útil com dois ecrãs). Em ecrãs estreitos começa oculto. Cada visualização fica no histórico do documento no arquivo.

**Confirmar receção:** o botão está sempre disponível (exceto durante o registo ou numa receção que já não pode ser editada). Ao carregar, o que falta é verificado pela ordem do ecrã — fornecedor e ficha completa, número, data, armazém, tipo de documento, linhas incluídas, artigo por escolher, descrição, quantidade, código e designação de artigos novos, lote em artigos com lotes, lote num artigo sem lotes (ativar ou limpar) e entrega superior por autorizar — e a janela leva ao campo a corrigir, assinalado e com a mensagem no topo e na própria linha. Os erros devolvidos pelo servidor («Linha N: …», fornecedor, tipo de documento, vencimento, encomenda) levam ao mesmo sítio. Nada é enviado enquanto houver informação em falta.

**A partir da Conferência documental:** as compras sem receção nem compra no circuito têm **Receber mercadoria**, que abre esta janela com a fatura do arquivo já lida ou, sem fatura no arquivo, uma receção manual preenchida com o cabeçalho da compra ([detalhe](CONFERENCIA-DOCUMENTAL.md)).

**Encomenda ao fornecedor:** depois de ler a fatura, o quadro **Encomenda ao fornecedor** propõe automaticamente a encomenda confirmada desse fornecedor (no mesmo estabelecimento) com mais artigos em comum com a fatura; pode escolher outra ou **Sem encomenda (receção direta)**. A tabela compara cada linha da encomenda com a fatura: **Igual à encomenda**, **A mais**, **A menos** (o resto fica por receber) ou **Não vem nesta fatura** (fica por receber); as linhas da fatura com artigos fora da encomenda (ou artigos novos) entram em stock sem ligação. Cada linha liga-se à linha paga do mesmo artigo; o bónus liga-se à linha de oferta (preço 0) quando a encomenda a tem, ficando a linha paga e a oferta separadas com o mesmo custo total. Quantidades acima do encomendado exigem **Aceitar as quantidades acima do encomendado (entrega superior)**. Ao confirmar, a encomenda fica recebida nessas quantidades e o que vem para pedidos de clientes fica-lhes reservado, como em **Receber**; reaberta para edição, a receção mantém a encomenda e as ligações. O lote e a validade lidos da fatura entram como habitualmente (artigos com lotes).

1. **Leitura das linhas** (`app/invoice_lines.py`): primeiro o texto do PDF com a disposição em colunas; nas digitalizações, OCR (Tesseract, `GPGEST_OCR_*`; 3×, a cores, até 3500 px, que foi a resolução que melhor leu as faturas reais). Não depende do formato de cada fornecedor: em cada linha procura a combinação em que **quantidade × preço × (1 − desconto) = valor da linha**. Isso separa a quantidade de números da descrição («BIO 25») e de códigos (EAN, lotes) e funciona sem colunas alinhadas. Deteta ofertas (desconto de 100 %), bónus («5 + 1» entram 6 unidades pelo valor da linha), um desconto global comum a todas as linhas e lote/validade na própria linha ou na seguinte. A soma das linhas é comparada com a base tributável do documento; uma diferença é assinalada. Linhas sem conta certa não são propostas (preenchem-se à mão) e portes/envios ficam desmarcados.
2. **Correspondência com os artigos** (`app/purchase_receiving.py`), por ordem: correspondência aprendida, código de barras, referência do fornecedor, código do artigo e nome semelhante. O nome semelhante penaliza tamanhos diferentes («500 ml» ≠ «250 ml») e só é escolhido automaticamente com semelhança alta e margem clara; caso contrário ficam sugestões e é proposto um artigo novo, com aviso quando há semelhantes.
   O **fornecedor** escolhe-se a escrever o nome ou o NIF (lista filtrada, como na folha de caixa); «＋ Criar novo» abre a ficha com o nome escrito. A lista é a mesma de **Tabelas → Fornecedores**, incluindo os fornecedores do ERP. Um fornecedor do ERP ainda sem ficha local passa a ter uma (ligada à entidade do ERP) quando se confirma a receção; nada é escrito no ERP. O mesmo vale para os fornecedores escolhidos em Receções, Compras e Encomendas a fornecedores. Junto ao prazo de pagamento aparecem a **rubrica** e o **meio de pagamento do fornecedor**, tal como na tabela de Fornecedores (com os do ERP quando a ficha local não os tem). **Editar fornecedor** (o lápis junto à pesquisa) abre a ficha da tabela para atualizar os dados sem sair da receção. Depois de gravar, a receção passa a usar os dados novos. O documento não é lançado com a ficha incompleta: sem NIF, rubrica, prazo ou meio de pagamento aparece um aviso com o que falta, a confirmação fica bloqueada e o servidor recusa o lançamento, também ao editar uma receção. **Regra comum a todos os movimentos:** um fornecedor só pode ser usado num movimento com **rubrica**, **meio de pagamento** e **prazo de pagamento** preenchidos na ficha. Os rascunhos gravam-se com a ficha incompleta. A verificação é feita **ao lançar (confirmar)** o documento. Os da Wintouch contam como preenchidos. O servidor recusa e indica o que falta em:
   - encomendas, receções, compras, devoluções e notas de crédito (ao confirmar, incluindo a edição de receções confirmadas);
   - encomendas reais preparadas a partir das encomendas de clientes e receções dessas encomendas;
   - lançamentos da conferência e-Fatura (um fornecedor criado na hora pela conferência fica por lançar até a ficha estar completa);
   - movimentos manuais de stock com fornecedor;
   - débitos atribuídos a fornecedores no fecho de caixa.

   Anular ou estornar nunca é bloqueado. Nas Encomendas de clientes, a ficha incompleta aparece a vermelho junto ao fornecedor e no quadro de necessidades.
   O cabeçalho mostra o **prazo de pagamento** da ficha do fornecedor e o **vencimento** calculado a partir da data do documento (mesma regra de `payment_terms.due_date`). As linhas que ainda precisam de artigo — **por criar** ou **por associar** — ficam a vermelho, com etiqueta e uma contagem por cima das linhas.
   O **tipo de documento** é proposto a partir do tipo da fatura lido do QR ou do arquivo: **FT** e **ND** → tipo de compra a crédito (**FC**, ou outro tipo ativo a crédito); **FR** e **FS** → compra a dinheiro (**FCD**). Fica editável, e o meio de pagamento e o vencimento seguem o tipo, como na escolha manual.
   Se o NIF da fatura não corresponder a nenhum fornecedor, **Criar fornecedor** abre a ficha de fornecedor das Tabelas já com o NIF e o nome do documento e consulta o VIES (morada, código postal, localidade); depois de criado, fica escolhido na receção.
   **Descontos:** cada linha mostra e permite corrigir quantidade, bónus, preço unitário e desconto (%); o cabeçalho tem o **desconto global (%)** do documento, proposto quando todas as linhas de mercadoria partilham o mesmo desconto comum (senão, esse desconto fica nas linhas afetadas). O valor líquido e o custo por unidade recebida (faturadas + bónus) são recalculados ao editar. A receção guarda o preço da fatura e um desconto equivalente que junta desconto da linha, desconto global e bónus, para que o custo em stock seja valor líquido ÷ unidades.
   **Resumo do IVA e total do documento** (no fim da janela): incidência, IVA e total por taxa e o total calculado, comparados com a base e o total da fatura no arquivo. Cada coluna (incidência, IVA, total) é destacada: **verde** quando igual, **âmbar** para diferenças de arredondamento até 2 cêntimos (o IVA é arredondado por taxa e pode diferir do fornecedor) e **vermelho** para diferenças reais a conferir; a linha **Diferença** mostra o valor com sinal e a classificação por extenso. Inclui todas as linhas lidas, também as desmarcadas (portes fazem parte da fatura, mas não entram em stock); o desconto global não se aplica a portes/serviços. Cada linha tem a taxa de **IVA (%)** lida da fatura, editável quando o OCR não a consegue ler.

   **Acerto de arredondamento:** quando a diferença para a fatura é de arredondamento (até 10 cêntimos na incidência e até 10 no IVA), **Acertar ao total da fatura** iguala a base e o IVA da receção aos da fatura. O servidor calcula o acerto sobre os seus próprios totais, por isso o total gravado fica exatamente igual ao da fatura, e recusa diferenças maiores. O acerto entra no documento financeiro: compra, dívida e previsto, ou movimento de caixa. Evita assim o conflito com uma compra já importada do e-Fatura ou do ERP com o total do fornecedor. O custo dos artigos em stock não muda. O acerto fica gravado na receção (`rounding_adjustment`) e na auditoria, mantém-se ao editar e **Retirar acerto** desfaz a escolha antes de confirmar.
   **Adicionar linha:** quando a leitura não encontra linhas (digitalização ilegível, formato sem tabela) ou falta alguma, acrescentam-se linhas à mão a partir do original ao lado — descrição, quantidade, bónus, preço, desconto, IVA, lote e validade — com artigo novo por omissão (a designação acompanha a descrição) ou um existente. As linhas acrescentadas podem ser removidas; antes de enviar, indica-se a linha a que falta descrição, quantidade ou código/designação do artigo novo.
   **Editar fornecedor:** abre a ficha da entidade sem sair da receção, tanto ao criar como ao editar um lançamento. Ao guardar, o nome e o prazo de pagamento são atualizados no cabeçalho; as linhas preenchidas mantêm-se. O vencimento acompanha o novo prazo quando ainda corresponde ao sugerido; uma data ajustada manualmente é preservada. A edição segue as permissões e regras da ficha de fornecedores, incluindo as do ERP.
   **Mesmo artigo em várias linhas:** em «Procurar outro artigo…», pode escolher o mesmo artigo em linhas diferentes. Cada linha conserva a quantidade, preço, desconto, lote e validade próprios; por exemplo, uma linha oferecida com desconto de 100 % e outra paga sem desconto. Ambas entram em stock pelas respetivas quantidades e custos, sem juntar os descontos.
   **Serviços:** um artigo marcado como **Serviço (não movimenta stock)** — na ficha do artigo ou ao criá-lo na receção — conta no valor e no IVA da fatura, mas nunca entra em stock (o stock recusa movimentos de serviços; ficam fora dos inventários e relatórios de stock e não podem controlar lotes). Portes e envios são propostos como serviço. Um artigo com movimentos não pode passar a serviço. Uma fatura só com serviços não dá receção: regista-se em Compras.
3. **Artigos novos** propostos com código (referência ou EAN), designação, IVA, código de barras e controlo de lotes/validades conforme a fatura; editáveis antes de confirmar.
4. **Confirmar** cria os artigos novos, aprende as correspondências (tabela `purchase_article_aliases`, migração **0067**) e lança a receção pelo circuito acima (mesmas validações, lotes, custo e movimentos de stock). O custo unitário inclui descontos e ofertas. A receção guarda a ligação ao documento do arquivo; a fatura financeira associa-se depois com **Associar fatura**.

Os estados de conferência, existência de anexo, pagamento e associação ao ERP são apresentados separadamente. Na coluna **Conferência / anexo**, a receção mostra «Fatura por associar» ou «Faturada», a compra «Por conferir» (com atalho **Conferir** para a Conferência documental) ou «Conferida», e o rascunho «Por confirmar»; o anexo (fatura original do arquivo, ligações e anexos da compra) abre-se na própria coluna com **Consultar anexo**. A rubrica é definida **por linha**: cada linha de uma compra ou nota de crédito pode ter rubrica própria (por exemplo, mercadorias e serviços na mesma fatura); sem ela, usa a rubrica por omissão do documento e, depois, a do fornecedor. No modo autónomo, todas as linhas têm de ter rubrica para alimentar o plano local. O pagamento continua a ser um só por fatura: o movimento previsto fica com a rubrica de maior valor e guarda a repartição por rubrica (tabela `planned_allocations`, migração **0051**), apresentada nos movimentos previstos. A rubrica de um previsto com várias rubricas só se altera nas linhas da compra. A proposta de despesa usa a rubrica de cada linha. Ao liquidar, o movimento real fica com a rubrica principal do previsto. Uma compra confirmada não pode ser alterada através da edição livre do montante do previsto.

## Envio e documentos

As encomendas podem ser duplicadas como rascunhos, impressas/consultadas em PDF e preparadas para envio pelo SMTP configurado. A pré-visualização congela o PDF e a mensagem no mecanismo `EmailDispatch` existente. O envio exige confirmação; o estado de envio e a data são independentes do estado da encomenda. Uma aceitação SMTP não é confirmação de leitura. Resultados incertos bloqueiam nova preparação automática para evitar duplicar envios.

Os anexos indicados neste circuito são ligações HTTPS: os ficheiros permanecem no Drive ou no serviço de origem. Para validar o ficheiro, associar um PDF Drive ao documento ou incluí-lo no dossiê contabilístico, utilize a conferência documental e o assistente existentes. Uma ligação HTTPS arbitrária não é descarregada automaticamente para o dossiê.

## Stock e valorização

As entradas, saídas, transferências, perdas, ajustes e saldos iniciais reutilizam `StockEntry`, `StockLot`, `StockWarehouse` e as permissões existentes. O novo histórico permite filtrar período, artigo, tipo e estabelecimento e abrir o documento de compras de origem. Mostra utilizador, data/hora, unidade, lote, validade e custo/valor quando autorizado.

Os movimentos confirmados não são apagados nem editados livremente. Uma correção física exige devolução ou movimento inverso/ajuste justificado no módulo existente. Transferências mantêm os dois movimentos relacionados nos armazéns.

Diferenças entre custo provisório e faturado geram entradas `valuation` com quantidade zero e ligação ao movimento original. A parte ainda incorporada no stock atualiza o custo médio; a parte consumida fica identificada separadamente. Os custos gravados em vendas anteriores e as suas margens não são reescritos. O IVA não recuperável integra o custo de acordo com a configuração existente.

## Isolamento e repetição

As rotas usam a sessão da empresa e o âmbito dos estabelecimentos autorizados. Gestores/administradores com acesso a custos podem escrever; leitura e custos respeitam as permissões existentes. Nos artigos do ERP, código, nome, estado e preço vêm do ERP e só se alteram na origem; os restantes dados (categoria, mínimos, localização, lotes, fornecedor, códigos de barras) e o **stock** são geridos na IAZELA. O conector Wintouch não lê quantidades e a Wintouch pode nem controlar stock (`GET /products/control_stocks` devolve `false`, sem registos em `/products/{armazém}/warehouse_product_stocks`), por isso os artigos do ERP aceitam entradas, saídas, inventários, lotes, vendas SAF-T e reservas locais. Só se a empresa declarar o stock como responsabilidade do ERP (`authority.stock = "erp"`) é que esses movimentos locais são recusados.

**Sincronizar** (cabeçalho de Artigos e validades, em modo integrado, para administradores): como nas tabelas, recebe primeiro o catálogo da Wintouch (o mesmo que Integrações → Artigos) e mostra o resumo «A associar / A criar / Já associados / Ignorados»; as fichas só mudam ao confirmar. A criação das fichas em falta é opcional.

**Associar por código** (Artigos e validades → Importações, ou dentro de Sincronizar): liga de uma vez as fichas locais aos artigos sincronizados do ERP com o mesmo código (ignora maiúsculas/minúsculas). A pré-visualização mostra quantas fichas serão ligadas, quantos artigos ativos do ERP não têm ficha (podem ser criadas) e os códigos repetidos, que são ignorados. Ligar mantém a ficha e o histórico de stock; nada muda sem confirmação e repetir não duplica. Exige o catálogo de artigos sincronizado em Configurações → Integrações.

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

### Leitura de faturas digitalizadas (OCR)

Uma fatura digitalizada não tem texto: as linhas são lidas por OCR. Nos PDF da Digitalização (uma fotografia por página), o OCR lê a **fotografia original** em várias tentativas, por esta ordem: ampliada a 4000 px em cinzento, depois a cores a 3200 px, e por fim a página redesenhada. Fica a primeira leitura cuja soma das linhas fecha com a base do documento; se nenhuma fechar, a que leu mais linhas. Quando a primeira tentativa falha, a leitura pode demorar até cerca de 15 s. Cada linha só é aceite quando **quantidade × preço = total** (ou com o desconto comum do documento). No fim, a soma das linhas é comparada com a base do documento. Para compensar os erros típicos do OCR:
- **Vírgula perdida no preço unitário** («2780» lido em vez de 27,80): a vírgula é reposta só se a conta fechar.
- **Um algarismo mal lido** («2780» em vez de 27,60): aceita-se total ÷ quantidade quando difere do lido num único algarismo e fecha a conta exatamente.
- Números com zeros à esquerda (NIB, telefone, códigos) nunca entram nestas correções.
- Quando há correções, a receção avisa para confirmar os preços com o original.
- **Linhas de oferta:** uma linha logo a seguir, do mesmo produto, só com quantidade e IVA (sem preço nem total), é a oferta da linha anterior. Fica como **bónus**: entra em stock e reparte o custo (ex.: 3 + 1 a 27,60 € dá 20,70 €/un.).
- **Lote:** com coluna «Lote» depois da designação, o número longo no fim da descrição vai para o lote.

## Despesas por rubrica e compras do ERP

Em **Contabilidade → Despesas por rubrica** (gestores e administradores), as despesas são somadas por rubrica pela **data das faturas de compra**:

- **Compras do ERP** (cópia só de leitura obtida em «Atualizar compras do ERP», que usa a consulta Wintouch existente). A rubrica é, por esta ordem, a definida localmente no documento ou a do fornecedor (procurado pelo NIF em Tabelas → Fornecedores; um NIF com rubricas diferentes fica por classificar). Um documento pode ser repartido por várias rubricas, com a soma igual ao total. Definir a rubrica não altera o ERP e fica na auditoria (migração **0052**, tabela `erp_document_rubrics`).
- **Compras confirmadas na IAZELA**, pela repartição das suas linhas.

Notas de crédito subtraem e documentos anulados no ERP não entram. Cada documento conta uma vez: uma compra local ligada a um documento ERP é contada pelo ERP. Documentos e-Fatura ainda não lançados não entram. Os documentos sem rubrica aparecem em **Por classificar**, com a ação **Definir rubrica**.

Os saldos por pagar das compras do ERP (sincronização Wintouch) já aparecem em Movimentos previstos e na previsão de liquidez; passam a mostrar a rubrica do documento ou do fornecedor. A sincronização guarda o NIF do fornecedor desses saldos a partir da próxima execução.

## Ficha e tabela de artigos

A ficha organiza-se em **Geral**, **Preços e margens**, **Stock e validades**, **Compras e fornecedores**, **Descrição e anexos** e **ERP e histórico**. Os separadores mantêm o preenchimento; Guardar/Cancelar ficam acessíveis, com proteção de alterações por guardar e atalho Ctrl/⌘+S. Os campos obrigatórios são código, designação e unidade, conforme as regras existentes. Família, categoria e marca aceitam novos valores na própria ficha, sem criar tabelas auxiliares.

- O preço sem IVA e com IVA atualiza-se nos dois sentidos. A margem bruta é `(venda sem IVA − custo médio) / venda sem IVA × 100`; falta de custo ou base de IVA desconhecida produz «Sem informação», e venda a zero não tem percentagem calculável. Os custos resultam dos movimentos e respeitam a permissão de consulta.
- Stock atual: soma dos movimentos dos armazéns autorizados. Reservado: reservas dos pedidos de clientes nesses armazéns. Disponível: saldo positivo livre de reservas, por armazém/lote, excluindo armazéns inativos e lotes expirados. Não compensa um saldo negativo de um armazém com unidades de outro. As correções continuam a exigir movimento ou inventário com motivo.
- A tabela pesquisa também a referência do fornecedor e filtra família, marca, fornecedor, estado e stock disponível. Os custos são uma coluna opcional. Ativar/desativar e eliminar mantêm confirmação e revisão concorrente; artigos com histórico não são eliminados.
- Descrição, observações internas, imagens adicionais e anexos são guardados separadamente no JSON da ficha existente, sem migração. As imagens e anexos são referências HTTPS (até 10 imagens e 20 anexos), sem upload ou cópia dos ficheiros. Não são enviadas observações internas ao ERP.
- Código, nome, estado e preço dos artigos ligados à Wintouch permanecem protegidos. A importação do catálogo usa o fluxo existente com pré-visualização e confirmação. O conector não permite escrever artigos no ERP nem identifica a base de IVA do seu preço.
- O detalhe consulta stock por armazém, última compra autorizada e até 30 eventos de auditoria, com data e autor; datas de criação/alteração aparecem quando registadas. Os detalhes de auditoria com custos não são expostos.
- O modelo atual não tem tipo serviço no catálogo de stock, numeração automática, várias tabelas de preços, stock máximo ou condições de compra por artigo. Estes dados não são simulados. Serviços em documentos de compra mantêm o funcionamento próprio sem stock.

Validação: `node scripts/test-article-values.cjs`, `backend/tests/test_inventory.py` e `scripts/test-inventory-ui.py` (APIs simuladas, incluindo criação/edição, retenção de dados após erro, duplicados, margens, filtros, teclado, ERP, telemóvel e temas).

## Inventário inicial valorizado

Em **Compras e Stock → Importações → Inventário inicial valorizado** (permissões de ajustar stock e ver custos), importe um CSV com **código do artigo, quantidade e custo unitário sem IVA** e, opcionalmente, armazém (nome), lote e validade. Indique a data de referência (por exemplo, 01/01/2026) e o motivo.

- A pré-visualização não grava nada e bloqueia: artigos sem ficha, inativos ou controlados pelo ERP; armazém fora do âmbito; quantidade inválida ou fora da precisão do artigo; custo em falta ou negativo; lote em falta (artigos com lotes); linhas repetidas; artigo que já tenha stock inicial nesse armazém. Custo zero é aceite com aviso.
- A confirmação é atómica (tudo ou nada) e idempotente: cria movimentos «stock inicial» com custo, com auditoria. Não altera vendas, compras nem pagamentos. Correções posteriores fazem-se por ajuste justificado.
- O custo médio destas entradas (e das compras confirmadas) é o custo usado na margem bruta do painel. Com IVA não recuperável configurado no stock, o custo inclui o IVA.
