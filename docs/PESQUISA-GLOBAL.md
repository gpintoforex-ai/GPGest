# Pesquisa global — fase C

A barra do cabeçalho pesquisa menus, artigos, clientes, fornecedores, encomendas de clientes e documentos do arquivo. Mantém a marca IAZELA e o desenho existente. Ctrl/Cmd+K coloca o foco na pesquisa; não interfere com uma janela de detalhe aberta. Setas, Enter e Escape permitem usar os resultados pelo teclado.

## Âmbito e resultados

- Os menus são filtrados pelo catálogo e pelo perfil atuais. Continuam disponíveis se a API de pesquisa falhar. A correspondência inclui o nome, o identificador legado e a área.
- A consulta de registos começa com dois caracteres, após 250 ms sem digitação. O servidor aceita até 100 caracteres, ignora maiúsculas/acentos portugueses e trata `%` e `_` como texto literal. Todos os termos têm de corresponder ao mesmo registo.
- São apresentados até cinco registos por categoria e oito menus. «Primeiros resultados» indica que existem mais correspondências; a pesquisa não representa um total de registos.
- A empresa é a da sessão autenticada. Encomendas e documentos respeitam as lojas autorizadas e a loja selecionada. Com uma loja explícita, documentos sem loja não são incluídos.
- Artigos, clientes e fornecedores são identidades do catálogo da empresa, partilhadas pelas lojas. A pesquisa não expõe existências nem valores por loja. Clientes e fornecedores exigem a permissão de consulta da tabela; a projeção local/ERP respeita a autoridade configurada, as marcas locais, as remoções e a empresa do ERP.
- Documentos são encontrados pelos metadados: nome do ficheiro, número, entidade e NIF. Não se pesquisa o conteúdo integral dos anexos. Não são devolvidos caminhos de armazenamento, custos nem notas privadas.

## Abrir um resultado

Os resultados encaminham para os módulos existentes. Artigos, clientes e fornecedores abrem a ficha em consulta. Encomendas e documentos abrem o detalhe existente, com as permissões e confirmações normais desse módulo. Consultar a pesquisa não cria encomendas, reservas, movimentos, associações nem envios.

O encaminhamento usa `consulta.tipo`, `consulta.id` e uma chave por seleção, juntamente com a página e o contexto da área. O módulo volta a validar o registo e o servidor volta a verificar o acesso. A navegação conserva a proteção dos formulários em edição. Os parâmetros de consulta são removidos ao navegar para outro destino.

O filtro «Filtrar este painel…» do Centro de Encomendas continua a filtrar apenas o painel; a pesquisa global está sempre no cabeçalho.

## Carregamento, falhas e privacidade

As respostas antigas são ignoradas quando a consulta, loja ou sessão muda. A pesquisa não guarda o texto no armazenamento persistente do browser. O estado de erro é distinto de uma consulta válida sem resultados e permite repetir a consulta. O limite de espera é de 15 segundos.

`GET /api/workspace/search?q=…&store_id=…` devolve `query`, `read_only: true` e grupos com `id`, `label`, `more` e `items`. Cada item contém só `id`, `label`, `detail` e o destino. A consulta usa limites SQL; não carrega o catálogo completo nem o histórico de stock. Não introduz tabelas, migrações ou serviços externos. A correspondência textual por substring pode exigir índices próprios para históricos muito grandes; esta fase não mede desempenho em bases reais volumosas.

Implementação: `backend/app/workspace_search.py`, `components/GlobalSearch.tsx`, `components/useRecordHandoff.ts` e adaptadores nos módulos existentes. Validação: `backend/tests/test_workspace_search.py` e `scripts/test-global-search-ui.py`, com dados sintéticos e APIs de interface intercetadas.
