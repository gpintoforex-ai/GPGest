# Conferência da Folha de Caixa em modo local autónomo

Em **Configurações > Integrações**, uma empresa em modo local pode conferir a folha sem ligação ERP. As folhas já fechadas preservam o funcionamento e os valores históricos do respetivo fecho.

A folha apresenta a identificação da caixa, o estado e um quadro de **Apuramento de numerário**: fundo a manter, faturado em numerário mais outros movimentos, total esperado na gaveta, diferença e valor a retirar ou a repor.

Abra **Conferência e fecho** para declarar o realizado, contar o numerário, justificar diferenças e fechar. Numa folha fechada, **Ver detalhes do fecho** permite consultar os valores, obter o PDF e reabrir com permissão e motivo. Estes detalhes ficam recolhidos ao abrir a página.

## Realizado do dia

Na coluna **Faturado**, use **Registar valor** no respetivo meio de pagamento (numerário, Multibanco/TPA, MB WAY, transferência e outros meios). Este valor representa o faturado do dia e é a base da coluna **Esperado**. No numerário, o esperado inclui ainda o fundo de maneio e outros movimentos da gaveta. A janela permite introduzir o valor e, opcionalmente, as observações do dia e a referência POS, comuns a todos os meios. **Aplicar valor** atualiza o rascunho; use **Guardar rascunho** na folha para o guardar. Para alterar um valor, carregue no montante; **Deixar por preencher** remove a declaração desse meio sem a substituir por zero.

Administradores e gestores podem adicionar meios locais. Estes meios também ficam disponíveis no registo de movimentos reais da empresa.

Guardar ou alterar o faturado não cria entradas, saídas ou ajustes, nem altera o saldo contabilizado nos movimentos. Atualiza apenas o esperado para a conferência. Campos vazios ficam **Por conferir** e não são tratados como zero. A referência ao fecho do POS e as observações são opcionais.

**Consultar movimentos registados** mantém uma comparação adicional com as vendas e recebimentos registados na mesma caixa e data. Os reembolsos reduzem o total. Fundos iniciais, reforços, entradas extraordinárias, despesas, depósitos e transferências ficam fora desta consulta de recebimentos. Os dados continuam identificados como **Manual** ou **ERP**; não são modificados. Não é necessário criar movimentos de venda para conferir o faturado declarado.

**Diferença da conferência = contado / conferido − esperado.** A diferença entre movimentos registados e faturado fica apenas na consulta dos registos.

## Numerário físico

**Esperado na gaveta = fundo inicial + faturado em numerário + outras entradas − outras saídas.**

As outras entradas e saídas incluem reforços, despesas, retiradas para depósito, depósitos confirmados e transferências que afetam a gaveta. Um depósito em rascunho não retira dinheiro. Os recebimentos já registados são substituídos pelo faturado declarado na projeção, para nunca somar ambos. Em termos de cálculo: **esperado = saldo dos movimentos − recebimentos registados + faturado declarado**. Nos outros meios aplica-se a mesma regra, sem fundo inicial. Movimentos de outras caixas não são agregados.

Introduza o numerário contado ou use a contagem por notas e moedas. Aplicar uma contagem de zero confirma explicitamente uma gaveta vazia.

Na tabela, **Contado / conferido** abre a contagem de notas e moedas na linha do numerário. Nos restantes meios pode registar o valor líquido conferido no terminal, extrato ou comprovativos. A coluna **Diferença** compara com o esperado indicado na mesma linha. Os valores são guardados no rascunho e no fecho. Qualquer diferença num valor contado/conferido exige justificação para fechar; a ausência de movimentos de venda não exige justificação quando o contado corresponde ao faturado esperado. Não são criados ajustes automáticos.

**Diferença de numerário = contado − esperado.**

## Guardar, conferir e fechar

- **Guardar rascunho** preserva valores, campos ainda vazios, contagem, observações e referência POS.
- **Conferir** exige todos os valores realizados, incluindo zeros explícitos, e a contagem física. Guarda o utilizador e a hora. Alterações posteriores aos valores ou aos movimentos tornam a conferência desatualizada.
- **Fechar caixa** valida novamente os valores atuais. Uma diferença em qualquer meio ou no numerário exige justificação, mesmo que diferenças de meios distintos se anulem no total. O fecho local não cria ajustes automáticos.
- O fecho preserva os valores e permite gerar o relatório PDF. A reabertura é reservada a administradores/gestores, exige motivo e conserva o realizado, a contagem e o histórico de auditoria. É necessária uma nova conferência.

Para corrigir a data depois de guardar o realizado, limpe os valores e guarde o rascunho antes de alterar o dia, ou crie uma nova folha. Uma declaração já preenchida não é deslocada silenciosamente para outra data.

No modo integrado, continuam disponíveis a importação ERP, a contagem existente, a identificação de fornecedores dos débitos e os fechos anteriores. A API rejeita a substituição do realizado ERP por uma declaração manual nesse modo.

## Instalação e validação

A migração **0035** acrescenta apenas a tabela de meios locais em cada esquema de empresa. As declarações, conferências, utilizadores e timestamps utilizam a persistência existente. A regra baseada no faturado não requer nova migração. O fecho guarda a base `declared_sales`, o esperado por meio e as diferenças, incluindo no PDF. Folhas já fechadas conservam o cálculo e os valores históricos; a interface identifica os fechos anteriores baseados nos movimentos.

Exemplos verificados:

| Realizado / gaveta | Resultado |
| --- | --- |
| Faturado no cartão 1.000,00 €; conferido 980,00 € | Diferença −20,00 € |
| Fundo 100,00 €; faturado em numerário 300,00 €; saídas 50,00 € | Esperado 350,00 € |
| Contado 340,00 €; esperado 350,00 € | Falta de 10,00 € |
| Fundo 40,00 €; faturado em numerário 200,00 €; contado 50,00 € | Esperado 240,00 €; falta de 190,00 € |
| Faturado no cartão 50,00 €; conferido 50,00 €; sem movimentos de venda | Esperado 50,00 €; diferença 0,00 € |
| Guardar, alterar e fechar o realizado local | Nenhum movimento adicional |

Os testes `backend/tests/test_cash_local.py` e `test_cash_local_tenants.py` verificam cálculos, persistência, permissões, isolamento, migração, origem ERP e ausência de duplicação. O teste `scripts/test-cash-local-ui.py` verifica o fluxo no navegador e a vista móvel, exigindo uma base local descartável `gpgest_test_cash_*` no ficheiro privado `.local/test-cash.env`. Nunca utilize a base Neon da aplicação para estes testes.
