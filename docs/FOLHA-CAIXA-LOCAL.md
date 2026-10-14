# Conferência da Folha de Caixa em modo local autónomo

Em **Configurações > Integrações**, uma empresa em modo local pode conferir a folha sem ligação ERP. As folhas já fechadas preservam o funcionamento e os valores históricos do respetivo fecho.

## Realizado do dia

Na Folha de Caixa, indique as vendas/recebimentos do dia por numerário, Multibanco/TPA, MB WAY, transferência e outros meios. Administradores e gestores podem adicionar meios locais. Estes meios também ficam disponíveis no registo de movimentos reais da empresa.

O realizado é uma referência manual: guardar ou alterar valores não cria entradas, saídas ou ajustes. Campos vazios ficam **Por conferir**; um zero só é declarado quando introduzido pelo utilizador. A referência ao fecho do POS e as observações são opcionais.

A comparação usa as vendas e recebimentos registados na mesma caixa e data. Os reembolsos de vendas reduzem o total. Fundos iniciais, reforços, entradas extraordinárias, despesas, depósitos e transferências entre caixas ficam fora desta comparação. Movimentos importados anteriormente continuam identificados como **ERP**; a declaração é identificada como **Manual** e não modifica os registos importados.

**Diferença da conferência = valor registado − valor realizado declarado.**

## Numerário físico

**Saldo esperado = fundo inicial + entradas em numerário − saídas em numerário.**

As saídas incluem retiradas para depósito, depósitos confirmados e transferências que retiram dinheiro da gaveta. Um depósito em rascunho não retira dinheiro. O realizado manual nunca é acrescentado novamente ao saldo. Movimentos de outras caixas não são agregados ao saldo físico desta caixa.

Introduza o numerário contado ou use a contagem por notas e moedas. Aplicar uma contagem de zero confirma explicitamente uma gaveta vazia.

**Diferença de numerário = contado − esperado.**

## Guardar, conferir e fechar

- **Guardar rascunho** preserva valores, campos ainda vazios, contagem, observações e referência POS.
- **Conferir** exige todos os valores realizados, incluindo zeros explícitos, e a contagem física. Guarda o utilizador e a hora. Alterações posteriores aos valores ou aos movimentos tornam a conferência desatualizada.
- **Fechar caixa** valida novamente os valores atuais. Uma diferença em qualquer meio ou no numerário exige justificação, mesmo que diferenças de meios distintos se anulem no total. O fecho local não cria ajustes automáticos.
- O fecho preserva os valores e permite gerar o relatório PDF. A reabertura é reservada a administradores/gestores, exige motivo e conserva o realizado, a contagem e o histórico de auditoria. É necessária uma nova conferência.

Para corrigir a data depois de guardar o realizado, limpe os valores e guarde o rascunho antes de alterar o dia, ou crie uma nova folha. Uma declaração já preenchida não é deslocada silenciosamente para outra data.

No modo integrado, continuam disponíveis a importação ERP, a contagem existente, a identificação de fornecedores dos débitos e os fechos anteriores. A API rejeita a substituição do realizado ERP por uma declaração manual nesse modo.

## Instalação e validação

A migração **0035** acrescenta apenas a tabela de meios locais em cada esquema de empresa. As declarações, conferências, utilizadores e timestamps utilizam a persistência existente da folha. Não são recalculados ou modificados movimentos históricos.

Exemplos verificados:

| Realizado / gaveta | Resultado |
| --- | --- |
| Declarado 1.000,00 €; registado 980,00 € | Diferença −20,00 € |
| Fundo 100,00 €; entradas 300,00 €; saídas 50,00 € | Esperado 350,00 € |
| Contado 340,00 €; esperado 350,00 € | Falta de 10,00 € |
| Guardar, alterar e fechar o realizado local | Nenhum movimento adicional |

Os testes `backend/tests/test_cash_local.py` e `test_cash_local_tenants.py` verificam cálculos, persistência, permissões, isolamento, migração, origem ERP e ausência de duplicação. O teste `scripts/test-cash-local-ui.py` verifica o fluxo no navegador e a vista móvel, exigindo uma base local descartável `gpgest_test_cash_*` no ficheiro privado `.local/test-cash.env`. Nunca utilize a base Neon da aplicação para estes testes.
