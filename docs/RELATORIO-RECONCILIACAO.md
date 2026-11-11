# Relatório de reconciliação bancária - IAZELA

O PDF deixou de ser uma sequência de linhas de texto. A apresentação A4 usa o logótipo existente, tipografia incorporada, azul e branco, cartões de resumo, tabelas por estado, prioridades, cabeçalhos repetidos, subtotais e rodapés com página/total. Verde identifica conciliação integral; âmbar pendências e parciais; cinzento trânsito e informação secundária; vermelho diferenças calculáveis ou inconsistências identificadas. Estar pendente não é classificado como erro.

## Dados e integridade

`backend/app/reconciliation_report.py` projeta o snapshot existente para uma apresentação, sem escrever na base nem alterar movimentos, associações ou regras financeiras. Os montantes mantêm-se em cêntimos inteiros.

O resumo distingue movimentos conciliados na íntegra, remanescentes pendentes (incluindo parciais) e registos da aplicação em trânsito. O controlo global mostra também o total associado, incluindo pagamentos parciais. O trânsito não é somado às linhas do extrato. Cada tabela tem subtotais próprios.

Saldos ausentes aparecem como «Não disponível». O saldo contabilístico continua a ser o calculado pelos registos da aplicação e pela referência inicial, identificado como tal. A comparação requer dados comparáveis da mesma conta, moeda e data. Sem estes dados, o relatório nunca mostra a reconciliação como concluída. Um fecho com pendências fica identificado com ressalvas; alterações posteriores ou reabertura impedem apresentá-lo como um fecho atual válido.

Os PDF históricos preservam o snapshot do fecho. Os relatórios de consulta com dados alterados são identificados como dados atuais, não como o snapshot original.

As referências B0001, T0001 e R0001 identificam linhas bancárias, trânsito e grupos de associação. Mantêm-se estáveis quando apenas a ordenação do mesmo relatório muda. UUID, assinatura e linhas técnicas só surgem no anexo opcional. Descrições completas mantêm-se nas tabelas de movimentos; as prioridades usam excertos quando a descrição é extensa.

## Exportação

Em «Fecho, saldos e histórico da reconciliação», o utilizador pode escolher a ordenação por data ou valor absoluto, crescente ou decrescente, e incluir o anexo técnico no PDF. A ordenação aplica-se dentro de cada estado. A exportação rápida usa data crescente e exclui o anexo.

A rota existente `/api/reconciliation/accounts/{account_id}/report` aceita `format=pdf`, `sort_by=date|amount`, `descending=true|false`, `audit=true|false` e o `closing_id` existente. As permissões e o âmbito da conta mantêm-se. O CSV existente não foi reformulado.

## Validação

- Captura de um período com dados existentes antes da alteração, incluindo o PDF original e o snapshot; nenhuma inserção de movimentos de demonstração na instalação.
- Comparação da identidade de cada movimento, data, descrição, valor, associado e remanescente, antes/depois.
- Verificação das referências de todas as linhas no texto do PDF final e dos totais de entradas/saídas face ao original.
- Renderização com Poppler e inspeção das páginas A4, incluindo datas, cabeçalhos, continuação de tabelas e rodapés.
- Exemplo principal: 252 movimentos existentes; todos preservados. O relatório mantém «Em análise», pois falta informação para validar o saldo contabilístico.
- Testes adicionais de parciais, trânsito, divergências, saldos ausentes, fechos desatualizados, ordenação, descrições extensas, anexo opcional, permissões e exportação sem alteração de movimentos.

O exemplo e a comparação de controlo ficam em `output/pdf/IAZELA-reconciliacao-bancaria-demonstracao.pdf` e `output/pdf/IAZELA-reconciliacao-validacao.json`.
