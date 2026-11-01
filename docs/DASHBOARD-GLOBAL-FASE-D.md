# Dashboard global — fase D

O resumo financeiro está visível na página inicial, entre o cabeçalho das prioridades e os indicadores das encomendas. Usa o componente de gestão já existente, com os mesmos serviços, fórmulas, metadados e destinos. Deixa de estar dentro de uma secção expansível fechada.

As áreas Resumo do negócio, Evolução, Atenção, Ações rápidas, Operação de caixa e Encomendas/abastecimento conservam a navegação e a personalização existentes. Se o utilizador tiver ocultado ou reordenado áreas nas suas preferências, essa escolha permanece válida. «Personalizar → Repor» recupera a disposição inicial.

## Períodos e origem

O resumo financeiro mantém as datas e a comparação dos seus filtros; por defeito, o mês atual. Os indicadores operacionais continuam a usar as últimas seis semanas, identificadas junto das prioridades. Os valores dos dois períodos não são somados nem apresentados como se fossem da mesma população. As previsões e disponibilidades atuais mantêm as limitações explicadas nos módulos existentes.

A origem é `GET /api/dashboard/overview`, e as prioridades operacionais usam `GET /api/order-cycle/analytics`. A apresentação não cria outro cálculo de saldos ou margens, não mistura moedas e não altera regras de IVA, movimentos, fechos ou reservas. Continuam disponíveis os metadados «Como é calculado» e as ligações para os registos de origem.

## Atualização e acesso

O resultado financeiro é identificado pela empresa/utilizador, perfil, modo, loja, datas e comparação. Ao mudar esses parâmetros, os valores anteriores deixam de ser apresentados enquanto a nova consulta está pendente. Uma resposta antiga ou recebida depois de sair do componente é ignorada.

Uma falha inicial mostra indisponibilidade, sem transformar a falha em zero. Uma atualização falhada dentro do mesmo âmbito conserva a última leitura com um aviso explícito. Datas inválidas não iniciam uma consulta e mostram a indicação correspondente. Um perfil sem permissão financeira recebe uma explicação, sem cartões com valores. A autorização efetiva continua no servidor.

O regresso do detalhe de encomendas ao dashboard não inicializa a loja do Centro de Encomendas durante a transição: essa inicialização só acontece se o endereço ainda apontar para a página de encomendas.

## Validação e reversão

- `scripts/test-dashboard-scope-ui.py`: resumo inicial visível, valores de origem preservados, mudança de loja/período sem dados antigos, respostas fora de ordem, falha inicial versus atualização e perfil restrito. Só APIs sintéticas intercetadas; nenhuma escrita.
- `scripts/test-approved-dashboard-ui.py`: apresentação em 1440/1011/768/390 px, claro/escuro, indicadores operacionais e gráfico preservados, permissões financeiras explícitas, atalhos e regresso ao dashboard mantendo «Todas as lojas».
- TypeScript/Vite, navegação e verificação estática da interface.

Checkpoint anterior a este lote: `work/phase-d-baseline-20261101.zip`, no workspace de trabalho. Contém apenas os ficheiros afetados, sem dados nem credenciais. Não repor globalmente o repositório: existem alterações anteriores de outros lotes.
