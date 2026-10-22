# Ciclo de encomendas — evolução de 22/10/2026

## Diagnóstico e arquitetura antes da implementação

O módulo `customer_orders` já regista pedidos e referências provisórias, protege reservas, agrega compras, recebe parcialmente e acompanha contactos e entregas. `procurement` é a fonte dos documentos e movimentos de compras. O arquivo conserva originais cifrados e mantém filas recuperáveis de OCR e associações. O OCR atual extrai identidade e totais, mas não interpreta linhas de mercadorias. O Google Drive possui upload com identificador remoto persistido antes da escrita. O conector Wintouch não implementa criação de artigos ou faturas de compra.

Não se substituem estas estruturas. A evolução acrescenta três tabelas por empresa: `order_replenishment_policies` (artigo/loja, parâmetros de procura e risco), `order_document_reviews` (documento original, simulação/encomenda, conferência, proposta e envio cloud) e `order_cycle_settings` (automatização autorizada por loja). Migração aditiva 0058.

Fluxo: pedido → verificação de stock/reservas/compras pendentes → recomendação justificada → simulação aprovada → encomenda → receção física e reserva → notificação/levantamento. Em paralelo: original no arquivo → OCR → correspondência de artigos e conferência de encomenda/receção/fatura → validação → proposta ERP ou lançamento local explicitamente confirmado. A pendência administrativa não desfaz a receção física.

Fases: (1) necessidades e recomendações sem escrita; (2) ligação ao arquivo, OCR de linhas e conferência persistida; (3) cloud recuperável e propostas/validações; (4) separadores e indicadores; (5) automatização SMTP consentida e testes de regressão direcionados.

As operações mutáveis reutilizam bloqueio por empresa, chaves idempotentes, revisões, funções/lojas autorizadas e histórico. Aprovações ficam ligadas à impressão digital dos dados conferidos; alterações ao original, receção ou encomenda invalidam a aprovação. Escrita ERP indisponível nunca é apresentada como concluída. Referências do fornecedor não são códigos internos sem correspondência validada.

## Evolução após identificar o Wintouch Cloud

Foi localizado `C:\Projetos\Wintouch\backend\_swagger_v1.json`, documentação Wintouch Web API v1 já referenciada pelo projeto. Documenta `POST products`, `POST product_documents` e consultas por identificador. A [referência pública do Wintouch Cloud](https://gateway.wintouchcloud.com/swagger/index.html) identifica a mesma família de API. A implementação nova usa apenas os caminhos documentados, sem tentar caminhos alternativos nem repetir escritas após falha.

O adaptador `wintouch_order_write.py` prepara artigos com categoria/unidade/IVA/preço explicitamente mapeados e compras de classe 32 (FT, EUR), de artigos sem lotes ou dimensões e na unidade base. Exige administrador, ligação e capacidade de escrita ativas, recurso autorizado e confirmação específica quando a empresa local está em demonstração. Regista o identificador e o estado `sending` antes do POST; apenas um GET que confirme identidade, campos, linhas e totais permite `confirmed`. Um resultado `uncertain` permite apenas consultar o mesmo identificador. A referência local ao artigo e a ligação ao documento ERP são criadas após essa confirmação.

Não foram criados artigos nem compras no ERP real durante o desenvolvimento. O contrato foi verificado no Swagger e em testes com transporte substituído. A validação ponta a ponta na instalação Wintouch exige selecionar os identificadores reais e confirmar uma operação de teste na aplicação.

## Estrutura de dados

| Tabela | Conteúdo |
|---|---|
| `order_replenishment_policies` | Artigo/loja, revisão, segurança, cobertura, janela de histórico, validade prevista, fator sazonal e teto de capital |
| `order_document_reviews` | Original do arquivo, simulação/encomenda, receção opcional, linhas revistas, divergências, aprovação, proposta, resultado ERP, compra local e identificador cloud recuperável |
| `order_cycle_settings` | Loja, administrador responsável, revisão, autorização de SMTP e de compras ERP automáticas, modelo de mensagem |

As intenções de escrita ERP ficam em `order_document_reviews.data.erp_operations`, com payload congelado, impressão digital, origem, identificador, utilizador e resultado. Os originais continuam em `ArchiveDocument`/`ArchiveBlob`; os trabalhos em `ArchiveJob`; compras e stock continuam nos seus documentos/ledgers atuais. O histórico utiliza os eventos e a auditoria existentes. Não se guardam chaves ERP ou credenciais SMTP nestas tabelas.

## Utilização

1. Em **Compras e Stock → Encomendas de clientes**, os nove separadores mostram o ciclo. Novos pedidos recebem um número derivado do identificador completo; números anteriores são preservados.
2. Ao registar produtos, pesquise os artigos locais ou abra o catálogo ERP sincronizado. A associação de uma referência existente reutiliza o circuito atual de artigos e as suas permissões. Artigos provisórios continuam sem criação automática no ERP.
3. No detalhe do pedido consulte stock livre depois das reservas e compras pendentes. Reserve quantidades locais disponíveis ou mantenha o saldo por comprar. O indicador de progresso distingue disponibilidade física, contacto e levantamento. O valor apresentado é uma estimativa sem IVA, não um saldo fiscal ou um pagamento.
4. Em **Necessidades de Compra**, escolha a loja e consulte as razões de reposição. São consideradas saídas de venda reais, mínimo/reposição do artigo, segurança, cobertura, prazo do fornecedor, reservas, procura por satisfazer, compras em trânsito, validade, margem estimada e capital. Configure o fator sazonal explicitamente; sem histórico não se inventam vendas.
5. No **Simulador de Fornecedores**, as recomendações só entram ao escolher **Selecionar para simulação**. O artigo não é acrescentado apenas para atingir um mínimo. Uma linha já selecionada usa o maior valor entre a proposta e a quantidade atual, preservando as atribuições aos clientes. As condições comerciais continuam no mesmo módulo.
6. Prepare a encomenda ao fornecedor, confirme o envio SMTP com PDF e registe receções físicas parciais pelo circuito existente. Os artigos recebidos são reservados aos pedidos independentemente da pendência administrativa da fatura.
7. Em **Receção e OCR → Originais, OCR e cloud**, associe uma encomenda preparada a um original já no arquivo ou importe PDF/fotografia. Também pode utilizar a digitalização existente e selecionar o original indexado no arquivo. O ficheiro cifrado é conservado; a fila extrai os dados e envia a cópia para a conta/pasta Drive configuradas. Falhas de rede retomam com o identificador remoto já persistido.
8. Em **Conferência ERP**, consulte o original e as colunas **Encomendado / Recebido / Faturado**. Confirme NIF, número, datas, totais e linhas. As correspondências exatas de código/EAN/referência do mesmo fornecedor e as semelhanças são propostas até confirmação. Pode transcrever linhas quando o OCR não as consegue interpretar.
9. Para artigo desconhecido, complete código interno proposto, categoria, unidade, IVA, fornecedor, EAN e preço. Pode guardar proposta ERP ou confirmar criação local quando a empresa está em modo local. Uma criação ERP exige selecionar os IDs reais e confirmar a escrita. A referência do fornecedor não é transformada automaticamente num código interno.
10. Guarde os dados revistos e indique o motivo da decisão. Divergências relevantes impedem processamento automático. A aprovação humana fica registada com a fotografia dos dados; uma nova receção ou alteração ao original exige nova aprovação. NIF, moeda, duplicação fiscal, totais e quantidades acima do recebido não podem ser contornados no lançamento apenas por aprovar uma divergência.
11. Pode preparar/exportar uma proposta, lançar compra local ligada às receções sem repetir stock, ou preparar uma escrita Wintouch com tipo/série/armazém/setor/moeda explicitamente escolhidos. Consulte os dados preparados e confirme. Em caso de resultado incerto, use **Verificar resultado no Wintouch**.
12. Um administrador pode autorizar, por loja, email automático a clientes consentidos com produtos disponíveis e/ou envio de compras ERP já preparadas, aprovadas e sem divergências. Ambas as opções começam desligadas. Falhas/resultado incerto exigem revisão manual; WhatsApp permanece manual e abrir a ligação não significa envio.

## Instalação e verificação

Requer migração **0058**. Antes de reiniciar uma API com este código, faça cópia de segurança e execute `alembic upgrade head` na pasta `backend`, com o ambiente da respetiva instalação. A atualização local não altera a API pública que executa noutra máquina.

Os testes de `test_order_cycle.py` cobrem recomendações, stock reservado, trânsito, validade, capital, revisões, conferência tripla, receções parciais, aprovação invalidada, lançamento local idempotente, arquivo/OCR, cloud incerta sem duplicação, propostas de artigos, criação Wintouch com GET de confirmação, isolamento de catálogo ERP, automatizações e migração aditiva. `test_wintouch_order_write.py` valida payloads, identidade/campos/totais, serialização Decimal e recuperação sem segundo POST. As regressões de encomendas, compras, inventário, arquivo e modos de funcionamento são executadas em conjunto ou por módulo. A interface é testada com respostas sintéticas a 1440 e 390 píxeis, sem operações externas reais.

## Limitações e melhorias futuras

Validação concluída nesta entrega: **78 testes** de encomendas/compras/inventário e ciclo; **28 testes** do ciclo/contratos Wintouch/arquivo; **9 testes** de modos de funcionamento. Depois dos últimos controlos fiscais e de permissões, repetiram-se os testes específicos: **22 aprovados**. Estes conjuntos sobrepõem-se e não representam uma soma de testes únicos. A compilação de produção, `check:ui`, Ruff e a interface a 1440/390 píxeis passaram. A API local respondeu HTTP 200 após a migração 0058 e o reinício. Transportes ERP/SMTP/cloud externos foram substituídos nos testes; não se efetuaram escritas externas reais.

- **Stock ERP:** continua indisponível no conector atual. Reservas, FEFO, receções e entregas físicas usam o stock local e mantêm o bloqueio dos artigos sob controlo ERP. Criar uma compra não inventa uma confirmação de disponibilidade física. Ligar quantidades/armazéns ERP e respetivo circuito logístico exige validar o contrato dessas consultas e movimentos.
- **Compras ERP:** esta implementação suporta FT de classe 32, EUR, artigos na unidade base, sem lotes ou dimensões. Outros tipos, conversões, retenções ou condições especiais permanecem como propostas para tratamento no ERP. O transporte ainda precisa de validação real de ponta a ponta na instalação de teste.
- **OCR:** interpreta conservadoramente tabelas com cabeçalhos reconhecíveis e colunas separadas por tabulações, ponto e vírgula, barras ou espaços múltiplos. Não é um parser universal de faturas. Layouts ambíguos exigem transcrição e revisão. O OCR de imagens depende do Tesseract já configurado; o QR fiscal não contém as linhas dos artigos.
- **Reposição:** é uma regra explicável, não um modelo preditivo treinado. Sazonalidade e prazo de validade são parâmetros explícitos. Sem custo conhecido, o capital é apresentado como não apurado e o custo precisa de confirmação na simulação. O prazo de validade real recebido continua a ser registado no lote.
- **Cloud:** usa a integração Google Drive existente. Sem conta/pasta configuradas, a pendência fica visível e o original permanece cifrado no arquivo. Não são inventados outros fornecedores cloud. A revisão está ligada a uma encomenda; repartição de um único original por várias encomendas é uma evolução futura.
- **Faturação ao cliente:** não cria um documento fiscal nem regista pagamento pelo simples levantamento. Usa-se o circuito de faturação existente. A estimativa sem IVA não deve ser apresentada como saldo em dívida.
- **Escala:** a conferência atual carrega as revisões autorizadas e calcula correspondências. Paginação no servidor e pré-carregamento adicional são melhorias para volumes elevados.
