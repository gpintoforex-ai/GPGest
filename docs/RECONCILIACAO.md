# Reconciliação

Bancos → Reconciliação automática reúne movimentos e sugestões, faturas e documentos (incluindo importação e-Fatura), e histórico. A importação dos extratos está em Bancos → Extratos.

O banco e os movimentos internos aparecem lado a lado, com valores associados e disponíveis. É possível selecionar vários movimentos em qualquer lado e ajustar os montantes para pagamentos parciais. Os estados são pendente, parcial, reconciliado e ignorado. Ignorar exige uma justificação e pode ser revertido.

As sugestões pontuam montante disponível, distância entre datas, referência, entidade e termos da descrição. Mostram os motivos e confiança baixa, média ou alta; a pontuação é uma heurística, não uma probabilidade estatística. Selecionar uma sugestão preenche a seleção, mas apenas **Confirmar reconciliação** grava a associação.

Recebimentos de cartão podem ser agrupados: bruto interno − comissão + diferença justificada = líquido bancário. A sugestão de lote reúne recebimentos de cartão do mesmo dia, com crédito nos dez dias seguintes e diferença até 10% do bruto. A comissão sugerida deve ser conferida no comprovativo bancário. Lotes com outras datas ou combinações podem ser selecionados manualmente.

Comissões, diferenças, depósitos e transferências exigem justificação. Saídas de caixa para depósito correspondem a entradas no banco; reforços de caixa correspondem a saídas no banco. Transferências entre contas próprias podem associar débitos e créditos de igual valor em contas diferentes da mesma loja. Transferências entre caixas e vendas em numerário não são candidatas à reconciliação bancária.

Não são criadas receitas, despesas, movimentos de caixa ou documentos fiscais a partir de créditos, débitos, comissões ou diferenças. Estes valores ficam no registo de reconciliação para conferência.

As associações antigas das compras e as novas reconciliações partilham o cálculo de valores disponíveis. Bloqueios das linhas no servidor impedem que confirmações concorrentes excedam o valor. Documentos e-Fatura de teste, movimentos estornados e movimentos ignorados não podem ser associados. Uma reconciliação tem de ser desfeita antes de estornar um movimento associado ou importar uma redução incompatível de um recebimento Wintouch.

Confirmar, desfazer, ignorar e reativar deixam registos de auditoria. Desfazer conserva o grupo e as linhas históricas, devolvendo os montantes disponíveis; exige uma justificação. As associações antigas de compras continuam consultáveis e reversíveis em Faturas e documentos.

Migração necessária: `0009`, aplicada a todos os esquemas de empresas existentes. Novas empresas recebem as tabelas automaticamente.
