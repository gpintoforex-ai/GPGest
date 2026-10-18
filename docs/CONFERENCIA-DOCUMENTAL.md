# Conferência documental e pagamentos

Abra **Contabilidade → Conferência documental**. As quatro áreas são Conferência documental, Documentos e anexos, Despesas por tratar e Entrega à contabilidade. Neste módulo encontra a importação e-Fatura, a conferência com o ERP, os anexos e o acompanhamento de documentos em falta. **Bancos → Reconciliação automática** mantém a conferência bancária separada, com movimentos lado a lado, sugestões justificadas e confirmação manual.

## Utilização

1. Escolha a loja e **Importar e-Fatura**. Selecione o CSV, use **Pré-visualizar**, confira os documentos e confirme a importação. Os filtros de datas passam para o período importado. O modelo CSV também pode ser preenchido manualmente; XLSX com estas colunas é suportado. Reimportar mantém anexos, estados e pagamentos; divergências de valores ou datas bloqueiam a confirmação. Quando os documentos antigos ainda não têm base tributável ou identificação da importação, a reconferência do ficheiro completa esses dados sem duplicar faturas.
2. Como administrador, use **Consultar compras no ERP**, escolha a loja e o período e confirme a consulta. A aplicação usa a ligação Wintouch já configurada para a empresa. Na demonstração, confirme expressamente que serão consultados dados reais.
3. Abra **Conferir** em cada fatura. Veja a correspondência proposta, os critérios e as divergências. Selecione o documento ERP, indique um motivo e confirme a associação. Pode desfazê-la com justificação; o histórico conserva a associação anterior. O número interno do ERP e a referência do fornecedor aparecem separadamente.
4. Indique se tem o papel ou ficheiro, a localização e as observações. Pode registar **Não localizado** ou **Solicitado ao fornecedor**. A verificação guarda responsável e data; a pendência conta desde a entrada do documento na aplicação. Anexe PDFs, JPEGs ou PNGs separadamente. Há até cinco anexos de 5 MB por documento, com visualização e descarga protegidas pelas permissões da loja. Um anexo não altera automaticamente a disponibilidade declarada.
5. Para despesas sem correspondência, escolha **Preparar despesa no ERP**, **Diretamente para a contabilidade** ou **Não aplicável**, neste último caso com justificação. A proposta permite rever fornecedor, número, tipo, datas, vencimento, total e várias linhas com categorias, bases, taxas e IVA. Confirme a revisão antes de exportar. Uma base calculada quando o ficheiro não a fornece é apenas uma proposta, a conferir com a fatura.
6. Na entrega à contabilidade, avance de **Por preparar** para **Pronto**, **Entregue** e, quando aplicável, **Confirmado**. Para preparar a entrega, confirme que o papel ou ficheiro está disponível. Filtre o período, selecione os documentos e use **Exportar documentos e controlo** para obter um ZIP com os anexos e um CSV de controlo. Exportar não altera o estado de entrega nem confirma um lançamento contabilístico.
7. Use **Identificar pagamento desta fatura** ou selecione várias faturas e identifique um pagamento comum. Pode registar um pagamento confirmado manualmente, usar um movimento interno/Wintouch real ou distribuir um pagamento anteriormente identificado. Distribua apenas o valor efetivamente pago: uma fatura pode ter várias prestações e um pagamento pode liquidar várias faturas. O saldo em aberto é controlado em cêntimos. Comissões, juros e movimentos sem fatura podem ser identificados pelo botão superior, sem liquidar faturas. Estes registos não enviam dinheiro.
8. Em **Bancos**, crie as contas necessárias e importe extratos XLSX BPI Net Empresas por conta. Em **Reconciliação**, relacione os movimentos bancários com os pagamentos identificados. Reveja as sugestões, montantes e justificações antes de confirmar. Lotes de cartão distinguem bruto, comissão e líquido. Transferências próprias relacionam saída e entrada sem criar receitas ou despesas. Desfazer a reconciliação mantém o pagamento identificado; para corrigir a distribuição, desfaça primeiro a associação bancária e justifique a reversão da distribuição na ficha documental.

Os filtros de disponibilidade, anexos, ERP, tratamento, entrega, pagamento e banco são independentes. Os indicadores destacam documentos não localizados, papel por digitalizar, ausência de correspondência, divergências, despesas por tratar, documentos por entregar e movimentos bancários pendentes. As ações em lote mostram fornecedores e documentos afetados, exigem revisão do resumo e confirmação, e são aplicadas numa única transação.

## Integrações e formatos verificados

- **e-Fatura por ficheiro:** o importador foi validado com a amostra CSV real disponível localmente, contendo 121 documentos e as colunas `Setor`, `Emitente`, `Nº Fatura / ATCUD`, `Tipo`, `Data Emissão`, `Total`, `IVA` e `Base Tributável`. Separa NIF/nome no emitente e a referência ATCUD quando explicitamente separada. CSV e XLSX usam os campos efetivamente presentes; não existe extração de PDFs do e-Fatura neste fluxo.
- **Wintouch:** o adaptador de compras faz consultas autenticadas à API REST da empresa, usando `product_documents`, `document_types`, `document_series` e `currencies`, com os dados de fornecedor incorporados. Campos e classes foram conferidos no Swagger local e em respostas reais. A validação de leitura encontrou dez documentos de compra, todos com NIF; apenas quatro tinham a referência externa do fornecedor. Nos restantes, é necessária conferência manual. Não se inventa essa referência a partir do número interno nem se associa apenas pelo montante.
- **BPI:** importação do XLSX real de BPI Net Empresas em EUR. Guarda conta, data do movimento, data-valor, descrição, montante, saldo, lote e origem. Não inventa uma referência independente quando não existe no extrato.
- O acesso SOAP e-Fatura anteriormente existente foi preservado como opção de configuração em **Acessos e-Fatura**; não é uma dependência da importação por ficheiro. Exige credenciais, certificados e autorização próprios, descritos em [COMPRAS-EFATURA.md](COMPRAS-EFATURA.md).

## Limites concretos

O **adaptador de compras Wintouch é de leitura**. O envio de despesas ainda depende da validação de um contrato de escrita para documentos de compra, séries, fornecedores, categorias e IVA. Embora a API tenha operações de escrita para outros fluxos do projeto, não são reutilizadas sem validar o lançamento de despesas. Neste módulo há preparação, revisão e exportação CSV; não há um botão que declare uma despesa lançada sem uma resposta real do ERP.

Ao rever ou exportar uma proposta, a aplicação volta a consultar os documentos Wintouch quando a ligação está ativa. Verifica tanto a identidade importada como a identidade proposta, incluindo alterações de fornecedor ou número. Uma falha impede a revisão/exportação e mantém a proposta anterior. Sem ligação ativa, indica expressamente que a conferência usa apenas a cópia local. Depois de lançar externamente, volte a consultar o ERP e confirme a associação: só esta associação identifica o documento real devolvido pelo ERP.

A consulta usa a loja escolhida para os documentos da empresa Wintouch; o mesmo documento ERP não é importado novamente noutra loja. São suportados documentos em EUR. Documentos anulados não podem receber uma nova associação; uma associação existente passa a apresentar divergência quando a consulta deteta a anulação.

A deduplicação BPI usa datas, descrição, montante, saldo e a ocorrência de linhas totalmente idênticas no ficheiro. Conserva movimentos legítimos com a mesma data e montante e evita repetir linhas em extratos sobrepostos. O formato analisado não contém um identificador bancário estável: se dois movimentos forem idênticos em **todos** estes campos e aparecerem isoladamente em ficheiros diferentes, não é possível distingui-los com certeza. Este caso exige conferência do extrato; não é anunciado como identificação bancária infalível.

Pedidos ao fornecedor e entregas são registos de controlo, sem envio automático de mensagens. As categorias da proposta são indicações para revisão/importação manual; não constituem códigos de contas Wintouch validados. Os movimentos bancários nunca geram automaticamente receitas ou despesas.

## Migração e verificação

A migração `0010` cria o histórico documental, cópias de documentos ERP, propostas, pagamentos, distribuições e lotes de importação em todos os esquemas de empresas. Acrescenta base tributável e identificação da importação aos documentos existentes, sem transformar dados do e-Fatura em anexos nem alterar os seus pagamentos. Novas empresas usam o modelo atualizado. As migrações de extratos e compras também permitem a instalação desde a versão anterior.

```powershell
cd 'C:\Projetos\Site Loja\GPGest\backend'
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m pytest tests/test_documents.py tests/test_document_migration.py tests/test_document_payments_sync.py tests/test_reconciliation.py tests/test_purchases.py tests/test_purchase_import.py tests/test_bpi_import.py tests/test_sales.py tests/test_cash_sheet.py -q
cd ..
.\.venv\Scripts\python.exe scripts/test-document-control-ui.py
.\.venv\Scripts\python.exe scripts/test-reconciliation-ui.py
cd frontend
npm run build
```

Os testes usam esquemas PostgreSQL temporários e o navegador usa um backend de teste isolado. Nunca anexam PDFs fictícios nem criam pagamentos nas empresas reais. O ambiente atual continua em demonstração.

Foram cobertos os doze cenários pedidos: mercadorias com ERP/PDF; eletricidade sem ERP com PDF; papel sem digitalização; ausência de papel/PDF; ERP com pendência documental; entrega direta à contabilidade; pagamento de três faturas; duas prestações; transferência própria; reimportação de faturas/extratos; anexo sem pagamento; e falha de integração mantendo uma proposta pendente. Para o último cenário, foi testada a falha da nova consulta ao ERP: a gravação de despesas no ERP é uma limitação explícita deste adaptador e não foi simulada como funcionalidade disponível.

Existem ainda verificações de divergências, preservação de zeros e separadores no número, associação/desassociação auditada, múltiplas taxas de IVA, exportação sem falso lançamento/entrega, ações em lote atómicas, permissões dos anexos, idempotência de pagamentos e partilha de saldos com os movimentos de origem. A interface foi exercitada em desktop e telemóvel.


## Carregamento das listas e recuperação de falhas

As listas documental e do assistente e-Fatura usam consultas agrupadas por pedido.
Os anexos são carregados como metadados; o conteúdo binário não é transferido para
construir as tabelas. A validação de conteúdo continua a comparar os resumos SHA-256
dos anexos, incluindo a invalidação de confirmações quando o ficheiro muda.

Uma falha na consulta de pagamentos, reconciliação ou catálogo ERP não elimina as
faturas que já foram carregadas. O assistente distingue o carregamento, o erro e a
ausência de faturas no período, e permite repetir a consulta. Depois de uma operação
confirmada, a atualização secundária da página decorre em segundo plano; uma falha
nessa atualização não é apresentada como uma falha da gravação.

Esta otimização mantém as pastas, credenciais, agendamento e associações existentes.
Os testes de regressão incluem os resultados de pagamento e reconciliação, a
equivalência entre ficha e listagem e o número de consultas com 60 faturas.

## Associação automática de PDFs no assistente

No segundo passo, «Procurar correspondências automaticamente» compara os PDFs da pasta Drive com os documentos do período, sem seleção obrigatória de uma fatura. Usa o texto extraível do PDF e o nome do ficheiro para comparar número, NIF, montante e data. Propõe apenas resultados com confiança de pelo menos 70/100, número encontrado e vantagem de pelo menos 20 pontos sobre outro candidato. Empates ficam para conferência individual. PDFs digitalizados sem texto continuam a necessitar de revisão, pois não existe OCR.

O utilizador confirma as propostas em conjunto (até 20 por confirmação). Os ficheiros permanecem no Drive; a base de dados guarda apenas referências e metadados, com auditoria. A análise pagina 50 PDFs e considera até 200 documentos por período; reduza o período para conferir mais documentos. A conferência individual permanece opcional.


O assistente principal inclui agora o terceiro passo ?Correspond?ncia com o ERP?. Permite pesquisar candidatos para todos os documentos do per?odo no cat?logo local, atualizar a consulta ? Wintouch por loja (administrador, com confirma??o), confirmar cada associa??o e abrir documentos sem correspond?ncia para revis?o da proposta de lan?amento. Uma pesquisa no cat?logo local n?o prova aus?ncia de lan?amento no ERP.
