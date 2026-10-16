# Conferência da Folha de Caixa em modo local autónomo

Em **Configurações > Integrações**, uma empresa em modo local pode conferir a folha sem ligação ERP. As folhas já fechadas preservam o funcionamento e os valores históricos do respetivo fecho.

A folha apresenta a identificação da caixa, o estado e um quadro de **Apuramento de numerário**: fundo a manter, faturado em numerário mais outros movimentos, total esperado na gaveta, diferença e valor a retirar ou a repor.

Abra **Conferência e fecho** para declarar o realizado, contar o numerário, justificar diferenças e fechar. Numa folha fechada, **Ver detalhes do fecho** permite consultar os valores, obter o PDF e reabrir com permissão e motivo. Estes detalhes ficam recolhidos ao abrir a página.

Nos ícones **i** da folha de caixa, da conferência do faturado e da calculadora, pode abrir a explicação e carregar em **Ouvir informação**. **Parar leitura** interrompe o áudio; ocultar a explicação, fechar a secção ou sair da página também interrompe a leitura. É usada a síntese de voz do navegador, com preferência por uma voz de português de Portugal. A disponibilidade depende do navegador e das vozes do dispositivo; o texto continua acessível quando a leitura em voz alta não está disponível.

Em **Voz e velocidade**, pode escolher uma voz portuguesa e uma leitura **Mais pausada**, **Normal** ou **Mais rápida**. A seleção automática dá preferência a português de Portugal e a vozes identificadas como naturais ou neurais, quando disponíveis. As preferências ficam guardadas neste navegador e são partilhadas entre os painéis de informação. A leitura respeita frases e parágrafos, faz pausas entre estes e pronuncia as fórmulas e siglas por extenso. Alterar uma preferência interrompe a leitura atual; volte a carregar em **Ouvir informação** para ouvir com a nova escolha.

Na roda dentada ao lado do ícone de informação, configure **Folhas por página por defeito** e **Modo de contagem por defeito** (**Valor total** ou **Notas e moedas**). As preferências são guardadas automaticamente neste navegador, por utilizador e empresa, e aplicam-se também às folhas integradas. O seletor de paginação fora do painel altera apenas a vista atual. Alterar o modo preferido não modifica contagens existentes: é necessário aplicar uma contagem para alterar o rascunho. Se existir um total sem discriminação, a opção de notas e moedas exige preencher a contagem antes de aplicar. O painel inclui ainda os atalhos de configuração de caixas e meios de pagamento para administradores e gestores.

## Realizado do dia

Na coluna **Faturado**, use **Registar valor** no respetivo meio de pagamento (numerário, Multibanco/TPA, MB WAY, transferência e outros meios). Este valor representa o faturado do dia e é a base da coluna **Esperado**. No numerário, o esperado inclui ainda o fundo de maneio e outros movimentos da gaveta. A janela permite introduzir o valor e, opcionalmente, as observações do dia e a referência POS, comuns a todos os meios. **Aplicar valor** atualiza o rascunho; use **Guardar rascunho** na folha para o guardar. Para alterar um valor, carregue no montante; **Deixar por preencher** remove a declaração desse meio sem a substituir por zero.

Administradores e gestores podem adicionar meios locais. Estes meios também ficam disponíveis no registo de movimentos reais da empresa.

Em **Tabelas → Meios de pagamento** ou pelo atalho **Configurar meios de pagamento** nos parâmetros da folha, administradores e gestores podem **Inativar** ou **Ativar** meios de base e personalizados. Os inativos continuam na tabela de configuração, identificados como **Inativo**, e ficam indisponíveis em novos movimentos. Nas folhas abertas, deixam de aparecer e não entram nos totais, nas diferenças nem nos requisitos de conferência ou fecho, mesmo que tenham movimentos ou valores guardados, incluindo zeros. Os movimentos e valores anteriores são preservados; reativar o meio numa folha aberta volta a disponibilizar esses valores. Os fechos anteriores e os respetivos relatórios não são alterados pela inativação ou reativação. O numerário é obrigatório para conferir a gaveta. As alterações ficam registadas na auditoria e limitadas à empresa atual. No modo integrado, mantém-se a configuração do ERP.

A coluna **Esperado** está destacada com uma faixa amarela. Use o ícone vermelho de sentido proibido **Sem movimento** junto ao nome de um meio sem registos para confirmar de uma só vez faturado e conferido a zero. A descrição aparece ao passar o rato; o botão fica com fundo e contorno vermelhos quando confirmado e pode voltar a carregar para deixar esses valores por preencher. Esta opção não substitui movimentos ou valores diferentes de zero. No numerário, confirma apenas faturado zero: a contagem física continua obrigatória, incluindo o fundo de maneio. Os zeros são guardados com **Guardar rascunho**, **Conferir** ou no fecho, com a auditoria existente, sem criar movimentos.

Guardar ou alterar o faturado no rascunho não cria entradas, saídas ou ajustes, nem altera o saldo contabilizado nos movimentos. Atualiza apenas o esperado para a conferência. **Fechar caixa** regista os montantes ainda em falta, conforme descrito abaixo. Campos vazios ficam **Por conferir** e não são tratados como zero. A referência ao fecho do POS e as observações são opcionais.

**Consultar movimentos registados** mantém uma comparação adicional com as vendas e recebimentos registados na mesma caixa e data. Os reembolsos reduzem o total. Fundos iniciais, reforços, entradas extraordinárias, despesas, depósitos e transferências ficam fora desta consulta de recebimentos. Os dados continuam identificados como **Manual** ou **ERP**; não são modificados. Não é necessário criar movimentos de venda para conferir o faturado declarado.

**Diferença da conferência = contado / conferido − esperado.** A diferença entre movimentos registados e faturado fica apenas na consulta dos registos.

## Conta bancária por defeito

Em **Tabelas → Meios de pagamento**, selecione o estabelecimento e escolha a **Conta bancária por defeito** de cada meio, nomeadamente **Multibanco / TPA**, **MB WAY** e **Transferência**. As contas disponíveis são as criadas em **Contas bancárias** para esse estabelecimento. A mesma conta pode receber vários meios; cada estabelecimento guarda as suas próprias associações. **Sem conta associada** remove a associação. O numerário continua a ser conferido na gaveta.

As alterações são guardadas automaticamente na base de dados e mantêm-se ao atualizar a página. Administradores e gestores podem configurar as contas dentro dos seus acessos; os restantes perfis podem consultar. A associação não cria movimentos, não altera saldos ou fechos e não substitui dados importados do ERP. Uma conta guardada mantém-se associada se o meio for inativado e posteriormente reativado.

Esta configuração utiliza a migração aditiva **0038**, aplicada a todos os esquemas de empresa. Não são atribuídas contas automaticamente: a escolha inicial é feita pelo utilizador.

## Débitos e créditos por meio de pagamento

A coluna **Débitos / créditos** apresenta o saldo **créditos − débitos**, com sinal positivo para entradas líquidas e negativo para saídas líquidas. Carregue no valor para abrir diretamente a **Calculadora**, escolha **Débito (saída)** ou **Crédito (entrada)** e introduza o total do dia, incluindo os valores já registados. Pode somar, subtrair, multiplicar ou dividir; **Aplicar valor** utiliza o resultado com duas casas decimais. Os totais de cada tipo são iguais ou superiores a zero; os débitos reduzem o esperado e os créditos aumentam-no.

Sem declaração manual, são utilizados os valores dos movimentos. Zero explícito declara um total de zero. O total declarado inclui os valores registados, que não são contabilizados novamente. Os recebimentos de vendas são tratados no faturado e os reembolsos já incluídos no faturado líquido ficam fora destes totais. **Usar débitos dos movimentos** ou **Usar créditos dos movimentos** repõe o valor registado do tipo selecionado. **Cancelar** ou Escape mantêm os valores anteriores.

Use **Guardar rascunho** para gravar. Os débitos e créditos são guardados separadamente, com utilizador e hora, preservados no fecho e apresentados no PDF. Declarar ou calcular estes valores não cria movimentos nem altera o saldo contabilizado. A alteração invalida uma conferência anterior; diferenças continuam a exigir justificação para fechar. Exemplo: fundo 40,00 €, faturado 300,00 €, créditos 20,00 € e débitos 10,00 € resultam num esperado de 350,00 €.

## Numerário físico

**Esperado na gaveta = fundo inicial + faturado em numerário + outras entradas − outras saídas.**

As outras entradas e saídas incluem reforços, despesas, retiradas para depósito, depósitos confirmados e transferências que afetam a gaveta. Um depósito em rascunho não retira dinheiro. **Esperado = fundo + faturado + créditos − débitos**, utilizando o total declarado de cada tipo ou, se não existir declaração, o valor registado. Cada valor é considerado uma única vez. Nos outros meios aplica-se a mesma regra, sem fundo inicial. Movimentos de outras caixas não são agregados.

Introduza o numerário contado ou use a contagem por notas e moedas. Aplicar uma contagem de zero confirma explicitamente uma gaveta vazia.

Na tabela, **Contado / conferido** abre a contagem de notas e moedas na linha do numerário. Nos restantes meios pode registar o valor líquido conferido no terminal, extrato ou comprovativos. A coluna **Diferença** compara com o esperado indicado na mesma linha. Os valores são guardados no rascunho e no fecho. Qualquer diferença num valor contado/conferido exige justificação para fechar; a ausência de movimentos de venda não exige justificação quando o contado corresponde ao faturado esperado. Não são criados ajustes automáticos.

**Diferença de numerário = contado − esperado.**

## Guardar, conferir e fechar

Ao **fechar no modo local**, o faturado, os débitos e os créditos passam a movimentos efetivos em **Tesouraria → Movimentos reais**, com origem **Fecho de caixa**, utilizador, referência à folha e data do dia da caixa. Os valores já existentes nos movimentos locais e nos recebimentos ERP incluídos na folha são descontados: por exemplo, faturado 1.000,00 € com 980,00 € já registados cria apenas os 20,00 € em falta. Não são criados movimentos para fundos iniciais, zeros, montantes já integralmente registados ou diferenças entre contado e esperado. Uma declaração inferior ao valor já registado não anula movimentos existentes.

Os movimentos não monetários guardam a conta bancária por defeito configurada no respetivo estabelecimento. Alterar essa configuração não muda a conta dos movimentos já registados. Quando não existe associação, o movimento aparece em **Bancos · conta por associar**. Os débitos novos conservam o fornecedor indicado ou a referência à repartição de vários fornecedores guardada na folha.

Os novos pagamentos do fecho são lançados numa **linha por fornecedor e meio de pagamento**, com o montante que falta registar para esse fornecedor. Se existem débitos em vários meios, selecione o meio em cada linha da janela **Identificar fornecedor**. O mesmo fornecedor pode aparecer em meios diferentes; a soma atribuída não pode exceder o total de cada meio. Valores por atribuir aparecem numa linha separada **Por atribuir**. Pagamentos já registados com fornecedor identificado são descontados da respetiva atribuição, mantendo os movimentos originais. Quando os pagamentos anteriores não permitem identificar a atribuição com segurança, esta não é inventada.

Nos fechos antigos que possuem um único lançamento agregado e uma repartição completa conhecida, **Movimentos reais** apresenta as linhas por fornecedor, mantendo o lançamento contabilizado, o total e os anexos originais. Esta apresentação não cria novos movimentos. A reabertura e o novo fecho utilizam os lançamentos individuais, mantendo o histórico de estornos.

Se um meio tiver sido inativado depois do fecho, os seus movimentos já lançados são preservados na reabertura, pois esse meio não entra na nova conferência. A inativação não elimina dinheiro já registado.

Reabrir uma folha estorna apenas os movimentos gerados por esse fecho, mantendo o histórico. Um novo fecho volta a registar os valores em falta, sem os duplicar. Movimentos já reconciliados ou associados a documentos exigem desfazer essas ligações antes da reabertura. Os movimentos criados no fecho são alterados através da reabertura da folha. Fechos anteriores à alteração não são lançados automaticamente; para os registar, reabra e volte a fechar a respetiva folha com os valores conferidos.

Após a reabertura, os lançamentos anulados desse fecho e os respetivos estornos deixam de aparecer em **Movimentos reais** e nos totais de entradas e saídas apresentados nessa lista. O histórico continua guardado no registo de movimentos e na auditoria. Os movimentos manuais mantêm o seu histórico e os lançamentos válidos do novo fecho aparecem uma única vez.

- **Guardar rascunho** preserva valores, campos ainda vazios, contagem, observações e referência POS.
- **Conferir** exige todos os valores realizados, incluindo zeros explícitos, e a contagem física. Guarda o utilizador e a hora. Alterações posteriores aos valores ou aos movimentos tornam a conferência desatualizada.
- **Fechar caixa** permanece clicável enquanto não há uma operação em curso. Se faltarem valores ou justificações, abre uma janela flutuante **Para fechar a caixa**, com a lista do que falta, sem enviar o pedido de fecho. Use **Voltar à conferência**, o botão de fechar ou a tecla Escape para regressar e corrigir os dados; o rascunho é preservado. O fecho valida novamente os valores atuais. Uma diferença em qualquer meio ou no numerário exige justificação com pelo menos cinco caracteres, mesmo que diferenças de meios distintos se anulem no total. O fecho local não cria ajustes automáticos.
- Depois de o fecho ser guardado com sucesso, aparece a confirmação flutuante **Caixa fechada**, com visto verde, identificação da caixa e do dia e botão **OK**, no mesmo formato usado nas liquidações. A confirmação é apresentada nos modos local e integrado e pode ser fechada com **OK** ou Escape. Um erro no fecho não apresenta esta confirmação.
- Quando existem débitos, o fecho exige atribuir valores a pelo menos um **fornecedor ativo** na janela **Identificar fornecedor**. Pode adicionar vários fornecedores e indicar o montante destinado a cada um; a soma não pode ultrapassar os débitos totais e qualquer restante fica assinalado como por atribuir. Administradores e gestores podem criar um fornecedor na própria janela. Cancelar preserva o rascunho e mantém a caixa aberta. A repartição, o utilizador e a hora ficam guardados no fecho e na auditoria e aparecem também no PDF. Os montantes são registados por fornecedor, sem associação artificial a movimentos individuais. Esta indicação não cria movimentos. A API valida a obrigação nos modos local e integrado; fechos anteriores mantêm os dados históricos.
- O fecho preserva os valores e permite gerar o relatório PDF. A reabertura é reservada a administradores/gestores, exige motivo e conserva o realizado, a contagem e o histórico de auditoria. É necessária uma nova conferência.

Para corrigir a data depois de guardar o realizado, limpe os valores e guarde o rascunho antes de alterar o dia, ou crie uma nova folha. Uma declaração já preenchida não é deslocada silenciosamente para outra data.

No modo integrado, continuam disponíveis a importação ERP, a contagem existente, a identificação de fornecedores dos débitos e os fechos anteriores. A API rejeita a substituição do realizado ERP por uma declaração manual nesse modo.

## Instalação e validação

A migração aditiva **0039** guarda a conta bancária de cada movimento. Não altera os valores existentes nem lança fechos históricos. Os novos fechos locais registam os montantes em falta numa única transação, juntamente com o fecho e a auditoria. Os testes `backend/tests/test_cash_posting.py` e `scripts/test-cash-posting-ui.py` verificam o lançamento, a ausência de duplicações, as contas por defeito, a reabertura e a apresentação em movimentos reais.

A migração **0035** acrescenta apenas a tabela de meios locais em cada esquema de empresa. As declarações, conferências, utilizadores e timestamps utilizam a persistência existente. A regra baseada no faturado não requer nova migração. O fecho guarda a base `declared_sales`, o esperado por meio e as diferenças, incluindo no PDF. Folhas já fechadas conservam o cálculo e os valores históricos; a interface identifica os fechos anteriores baseados nos movimentos.

Exemplos verificados:

| Realizado / gaveta | Resultado |
| --- | --- |
| Faturado no cartão 1.000,00 €; conferido 980,00 € | Diferença −20,00 € |
| Fundo 100,00 €; faturado em numerário 300,00 €; saídas 50,00 € | Esperado 350,00 € |
| Contado 340,00 €; esperado 350,00 € | Falta de 10,00 € |
| Fundo 40,00 €; faturado em numerário 200,00 €; contado 50,00 € | Esperado 240,00 €; falta de 190,00 € |
| Faturado no cartão 50,00 €; conferido 50,00 €; sem movimentos de venda | Esperado 50,00 €; diferença 0,00 € |
| Guardar ou alterar o rascunho local | Nenhum movimento adicional |
| Fechar a caixa local | Regista apenas os montantes ainda em falta |

Os testes `backend/tests/test_cash_local.py` e `test_cash_local_tenants.py` verificam cálculos, persistência, permissões, isolamento, migração, origem ERP e ausência de duplicação. O teste `scripts/test-cash-local-ui.py` verifica o fluxo no navegador e a vista móvel, exigindo uma base local descartável `gpgest_test_cash_*` no ficheiro privado `.local/test-cash.env`. Nunca utilize a base Neon da aplicação para estes testes.
