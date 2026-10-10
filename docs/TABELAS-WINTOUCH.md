# Tabelas de integração Wintouch — 11/10/2026

As 11 tabelas são criadas no esquema PostgreSQL de cada empresa. A migração `0003` atualiza a empresa inicial e todas as empresas registadas, numa transação; novas empresas recebem o mesmo modelo automaticamente. Foi criada uma cópia de segurança antes da atualização.

| Tabela | Conteúdo |
|---|---|
| `wt_products` | Artigos: ID Wintouch, código, nome, categoria, unidade, preço, custo, moeda e controlo de lotes. |
| `wt_categories` | Categorias Wintouch, separadas por nível 1–7 e com referência ao pai. |
| `wt_entities` | Clientes e fornecedores: uma entidade pode desempenhar ambos os papéis. Nome, código, NIF, email e telefone. Classificação desconhecida fica nula. |
| `wt_cash_accounts` | Caixas/contas Wintouch e mapeamento opcional para loja e caixa GPGest. |
| `wt_workshifts` | Turnos Wintouch: caixa, operador externo, abertura, fecho e contagens conhecidas. Ligação opcional à sessão GPGest. |
| `wt_payment_methods` | Meios de pagamento externos e mapeamento explícito para numerário, cartão, transferência ou outro. |
| `wt_documents` | Documentos de origem, entidade, datas, moeda, valores e estado/anulação. |
| `wt_document_lines` | Linhas de documentos, artigo, quantidade, preço unitário e total. |
| `wt_movements` | Movimentos/pagamentos externos, com documento, entidade, caixa, turno, meio, montante e direção. Ligação opcional a movimento local. |
| `wt_sync_runs` | Histórico por recurso: estado, início/fim, contagens e erro sanitizado. Apenas uma execução em curso por recurso e empresa Wintouch. |
| `wt_sync_state` | Posição/cursor e última execução bem-sucedida de cada recurso. |

## Identidade e precisão

Cada registo externo tem ID local, identificador da empresa na API (`enterprise_id`), ID Wintouch (`external_id`), data/versão da origem, data de sincronização e referência à execução. A empresa GPGest é determinada pelo esquema, sem copiar dados entre clientes.

Restrições únicas impedem duplicação da mesma identidade externa. Documentos e movimentos incluem o recurso de origem na identidade, porque dois endpoints podem devolver o mesmo ID. Categorias incluem o nível; linhas incluem o documento. Códigos e NIFs têm índices de pesquisa, mas não são usados como identidade única.

Valores financeiros consolidados usam cêntimos inteiros; preços e custos unitários usam `NUMERIC(18,4)` e quantidades `NUMERIC(18,6)`, tratados como Decimal em Python. Campos ainda desconhecidos ficam nulos, incluindo moeda, montante, direção ou anulação; não se assumem valores nem estados a partir da ausência de informação.

## Separação financeira

`wt_movements` guarda a origem Wintouch. Não acrescenta valores automaticamente às caixas, saldos ou relatórios GPGest. Uma fatura não é presumida como recebimento. Os mapeamentos para sessões e movimentos locais exigem validação explícita e têm restrições únicas para evitar reutilização do mesmo registo local.

O futuro adaptador deve validar que entidades, documentos, caixas e turnos ligados pertencem ao mesmo `enterprise_id`, confirmar o significado dos campos, paginar completamente, atualizar por identidade externa e só avançar o cursor após uma execução concluída. Mudanças do identificador Wintouch da configuração mantêm identidades distintas, exigindo seleção explícita da origem nas consultas.

Esta etapa entrega o modelo e a migração, sem importar dados reais para a demonstração. A sincronização automática, o adaptador dos campos das respostas e a consulta do catálogo pela interface continuam como etapa seguinte. Não são guardadas respostas completas da API, cabeçalhos nem chaves nestas tabelas; os erros de sincronização devem ser sanitizados antes de persistir.

## Atualização e recuperação

Execute `alembic upgrade head` em `backend` com o servidor parado para impedir criação concorrente de empresas durante a migração. O backup inclui todos os esquemas de clientes. Guarde também a chave de encriptação privada conforme [ADMINISTRACAO-PLATAFORMA.md](ADMINISTRACAO-PLATAFORMA.md).

A migração foi testada em dois esquemas PostgreSQL existentes, incluindo criação, inserção com o mesmo ID externo em empresas diferentes e reversão em esquemas temporários. O downgrade remove os dados Wintouch; não é uma operação habitual de manutenção e não altera as tabelas financeiras originais.
