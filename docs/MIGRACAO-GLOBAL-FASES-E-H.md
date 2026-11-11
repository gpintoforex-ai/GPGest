# IAZELA — migração dos módulos e assistência contextual (E–H)

Mantém-se o nome e o logótipo IAZELA, o menu turquesa com efeito de luz e os campos brancos no tema claro. Os componentes comuns evoluem os módulos existentes; os serviços de negócio, as permissões, os documentos, as reservas e os movimentos continuam nos fluxos originais.

## Implementação por área

| Área | Visão geral e continuidade | Revisão contextual |
|---|---|---|
| Encomendas | O detalhe usa o percurso Pedido → Necessidades → Simulação → Fornecedor → Receção → Aviso → Entrega → Fecho. A etapa atual usa os estados e quantidades existentes; não significa que todas as anteriores tenham sido concluídas. O centro conserva o painel de cinco etapas aprovado. | A necessidade líquida calculada pelo servidor abre a cobertura e os clientes de origem. Reservas, mínimos/portes, propostas sem duplicação e aprovações mantêm-se. |
| Artigos e stock | A ficha e o relatório usam cartões comuns, preservando preços, custos autorizados, margens, lotes, fornecedores e histórico. O relatório resume artigos abaixo do mínimo, com validade em risco, sem vendas e com excesso/validade próxima. | A reposição abre o artigo em consulta. Margem negativa abre o separador de preços; a evidência identifica os valores da ficha, incluindo alterações ainda por guardar. Não são criadas reservas ou compras. |
| Compras e receções | As etapas de compra apresentam encomendas pendentes, atrasos, receções por faturar, documentos por conferir e devoluções. O resumo de compras distingue documentos, ausência de anexo e pagamentos bancários por associar. | Abre a encomenda por receber ou a primeira compra sem anexo. Confirmar uma receção conserva as validações de stock e finanças. Falha/incompatibilidade da consulta não é tratada como uma lista válida vazia. |
| Vendas e SAF-T | Faturação, recebimentos, saldos e montantes em conferência conservam as medidas distintas e usam cartões comuns. O histórico SAF-T distingue ficheiros concluídos, por confirmar e com falha. | As correspondências abrem a revisão existente. Ficheiros prontos abrem a pré-visualização; continuam a exigir confirmação. A importação não cria recebimentos nem movimentos de stock automaticamente. |
| Tesouraria e caixa | A lista resume folhas carregadas abertas/fechadas e permite filtrar esses estados antes da paginação. Listagens de movimentos, previstos e depósitos partilham o resumo dos resultados filtrados e das linhas apenas de consulta, sem alterar os totais. | Uma diferença de numerário permite abrir diretamente a contagem. Cancelar preserva os dados; guardar, justificar, fechar, reabrir e estornar mantêm os mecanismos existentes, incluindo movimentos reais das diferenças locais. |
| Bancos | Os cartões comuns mantêm saldo contabilístico, saldo do extrato, comparação na mesma data/moeda, TPA por liquidar e remanescentes. Mudar conta, loja ou datas retira os valores anteriores enquanto se aguarda a nova consulta. | Pendências abrem a reconciliação da conta. As propostas respeitam todos os filtros atuais e abrem a seleção para revisão. Associação e criação de comissão continuam distintas e confirmadas. |
| Liquidez | Mantêm-se as projeções, cenários, reserva, moedas, contas, origens e períodos existentes. | Uma projeção abaixo da reserva abre os movimentos do dia de menor saldo. Não é um pagamento nem uma afirmação de saldo bancário disponível. |
| Documentos e arquivo | Conservam-se os estados independentes de anexo, correspondência ERP, pagamento, banco e entrega. Na conferência, uma falha mostra indicadores indisponíveis e retira as sugestões. | O documento sem anexo é escolhido nos filtros atuais e abre o detalhe existente. O arquivo indica caixa de entrada/propostas com evidência e navegação para revisão. Nenhum envio ou lançamento ERP é feito por consultar a sugestão. |
| Dossiês contabilísticos | Resumo dos períodos carregados, por encerrar, encerrados e com anexos em falta. A unidade é o período: não se somam documentos entre períodos potencialmente sobrepostos. | Fecho, exportação, preparação, envio SMTP e confirmação de receção continuam a ter estados e autorizações próprios. |
| Clientes, fornecedores e catálogos | A página comum apresenta total filtrado de todas as páginas e quantidade da página atual, preservando pesquisa, ordenação, seleção, exportação e sincronização suportadas. | Conflito/erro visível na página abre a ficha em consulta. Não há chamadas externas por linha nem uma sincronização automática. |

## Contrato dos novos resumos

`OperationsOverview` recebe valores e ações já autorizados do módulo. Não faz pedidos de rede nem conhece regras de escrita. Usa `ModuleOverview`, `KpiCard` e `ContextSuggestion`.

Carregamento e erro são estados distintos de zero. A indicação «Consulta recebida em» só aparece quando o módulo fornece a referência de uma resposta efetiva: identifica a receção da consulta no navegador, não a atualização do ERP ou do banco. Filtrar os mesmos dados não muda essa hora. A evidência de cada sugestão identifica a origem, o âmbito, o período e as limitações aplicáveis.

Os indicadores documentam a população: relatório filtrado, documentos carregados antes da pesquisa, lista de compras filtrada, movimentos bancários com os filtros atuais, ou página do catálogo. Não são apresentados como totais globais intercambiáveis. Não se somam valores de moedas ou unidades diferentes.

## Segurança dos percursos

As novas ações de assistência limitam-se a selecionar uma área, navegar, consultar um detalhe ou abrir a contagem/pré-visualização. As operações de escrita continuam nos serviços e diálogos existentes, com revisão, motivo, idempotência e confirmação aplicáveis. Não foram adicionados serviços externos de IA, emissão fiscal, conetores bancários, stock ERP não suportado ou envio automático de encomendas.

A contagem foi retirada de dentro do conteúdo que só era montado ao abrir a conferência. É o mesmo diálogo e o mesmo estado; a revisão contextual pode assim abri-lo diretamente, sem gravar a folha.

## Validação e reversão

Testes de interface com dados sintéticos e APIs integralmente intercetadas: artigos, stock, lotes, compras/receções, faturação, SAF-T, pedidos, centro de encomendas, bancos e conferência de caixa. O teste transversal `test-operations-overviews-ui.py` verifica filtros de caixa, revisão/cancelamento de contagem, indicadores de dossiês, evidência e navegação do arquivo, detalhe documental nos filtros atuais, falha de consulta sem falsos zeros e artigo prioritário em consulta.

Os testes de componentes comuns verificam estados, contraste, campos brancos, seleção de colunas, foco, teclado e revisão com evidência nos dois temas. A navegação conserva os 64 destinos. Compilação TypeScript/Vite e verificação estática da interface completam a validação do lote. O relatório no workspace identifica os resultados finais e as capturas.

Não foram alteradas regras do servidor nem executadas operações reais nas integrações. A validação usa cenários representativos; não constitui certificação integral de todos os históricos e serviços externos. Não se introduziu migração de base de dados.

O checkpoint anterior à implementação está em `work/phase-e-h-baseline-20261101.zip`, no workspace de trabalho, sem segredos ou dados. Existem alterações anteriores no projeto: reverter apenas os ficheiros e diferenças deste lote.
