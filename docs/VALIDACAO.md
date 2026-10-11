# Validação da Fase 1 — 10/10/2026

## Cálculo automático da folha — 11/10/2026

- 34 testes do backend passaram, incluindo inclusão automática Wintouch em novas folhas e folhas antigas abertas, ausência de escrita em consultas, contagens guardadas e atribuição única quando há duas caixas da mesma loja/dia.
- O teste de navegador confirmou 100 € de fundo + 5 € de recebimento Wintouch apresentados automaticamente como 105 € de numerário esperado, sem ativar ou voltar a introduzir esse recebimento; cartão totalizou 20 € locais + 7 € Wintouch. Contagem, persistência, fecho e layout móvel passaram.
- TypeScript e compilação de produção passaram. Fechos anteriores preservados; as contagens continuam independentes dos valores esperados.

## Folha de caixa — 11/10/2026

- Migração `0006` aplicada a todos os esquemas de empresas registadas, após cópia de segurança; folhas antigas e valores existentes preservados.
- 33 testes do backend passaram: contagens persistentes, cêntimos exatos, cartões com reembolsos, notas/moedas, permissões, justificação de diferenças, fecho/reabertura e valores Wintouch por empresa, dia e loja. Pagamentos com data diferente da fatura e mudança de dia em Europe/Lisbon foram verificados.
- Unicidade dos recebimentos Wintouch por loja/dia, isolamento entre empresas e bloqueio de alterações a origens/contagem depois do fecho verificados.
- Os valores esperados e contados de uma folha fechada mantêm-se após alterações na origem importada; PDF validado.
- Teste de navegador em esquema temporário: fundo de maneio, ativação de Wintouch numa folha aberta, soma GPGest + Wintouch, contagem persistente após recarregar, notas/moedas, rejeição de fecho com diferença sem justificação, fecho e ligação ao PDF passaram. Layout a 390 px sem transbordamento da página e sem erros JavaScript.
- TypeScript, compilação de produção e Ruff passaram.
- As quatro novas colunas e a unicidade por loja/dia foram verificadas nos dois esquemas locais. Backup após migração restaurado numa base temporária, com todas as tabelas e contagens comparadas.

Ambiente local Windows, Python 3.13, React/TypeScript, FastAPI e PostgreSQL 16.15. Valores monetários em cêntimos inteiros. Base de demonstração própria, sem dados do site Desafio Salutar.

## Verificações concluídas

- `npm run build`: TypeScript e compilação de produção passaram.
- **20 testes passaram** (suite geral e teste adicional de permissões), usando esquemas PostgreSQL temporários isolados.
- Teste de navegador: login, abertura, venda em cartão sem alterar numerário, contagem, fecho, ligação a PDF, menu móvel e ausência de erros JavaScript passaram.
- Imagens de computador e telemóvel verificadas; não existe deslocamento horizontal da página no teste a 390 px.
- Relatório de fecho validado como PDF no teste do backend.
- Anexos: acesso autenticado, validação de tipo, rejeição de conteúdo sem assinatura e bloqueio após fecho.
- Backup PostgreSQL criado e restaurado numa base temporária; lista e contagem de todas as tabelas da aplicação comparadas.

## Regras financeiras e de acesso testadas

1. Numerário e cartão separados; 0,10 € + 0,20 € calculados exatamente.
2. Montantes com mais de duas casas decimais e valores não finitos rejeitados.
3. Diferença no fecho exige justificação; caixa fechada bloqueia movimentos e anexos.
4. Reabertura autorizada e estorno preservam os registos originais; estorno repetido rejeitado.
5. Duas aberturas concorrentes: uma aceite, outra rejeitada.
6. Saídas concorrentes superiores ao fundo disponível: apenas uma aceite.
7. Chave duplicada de movimento e transferência rejeitada sem duplicar saldo.
8. Transferência entre caixas conserva o total; não cria resultado de gestão.
9. Consulta não pode abrir caixa; operador não acede a lojas sem autorização.
10. Operador só movimenta uma sessão sob a sua responsabilidade; passagem de caixa altera essa responsabilidade com histórico.
11. Operador não reabre caixa fechada.
12. Alteração de palavra-passe exige a atual e termina outras sessões.
13. Pedidos de origem externa não autorizada são rejeitados.

Os testes não deixam sessões ou movimentos na caixa da aplicação. O ensaio de recuperação remove apenas a base temporária criada para o teste.

## Limites da entrega

O painel de sincronização foi movido para **Integrações → Wintouch** da empresa. Foram validados acesso sem sessão da plataforma, seleção exclusiva da própria empresa e bloqueio de operação/histórico para operadores. O navegador confirmou formulário, histórico e largura móvel, com resposta de sincronização simulada. As credenciais da API continuam na administração central.

Foi acrescentada a [importação manual Wintouch](SINCRONIZACAO-WINTOUCH.md) de artigos, entidades e caixas. Os testes verificam confirmação na demonstração, paginação, atualização sem duplicados, anulação por IDs repetidos, preservação do catálogo anterior e classificação explícita cliente/fornecedor. Três leituras reais de um registo confirmaram HTTP 200 e o formato de lista dos endpoints; não guardaram esses dados. A interface de importação foi exercitada com resposta simulada, sem importar dados reais durante o teste do navegador.

Em 11/10/2026 foi aplicada a migração `0003` com 11 tabelas Wintouch por empresa. Os testes adicionais verificam preço Decimal com quatro casas, clientes/fornecedores na mesma entidade, prevenção de duplicados, separação dos movimentos financeiros locais, exclusão de execuções de sincronização concorrentes e migração/reversão em dois esquemas existentes. Não há importação automática nesta etapa; veja [TABELAS-WINTOUCH.md](TABELAS-WINTOUCH.md).

A administração central em `/plataforma` foi validada no navegador: autenticação separada, formulários de criação/configuração, saída de sessão e largura móvel. Os testes verificam isolamento dos esquemas, rejeição de uma sessão de outra empresa, impossibilidade de administradores das lojas acederem à plataforma, desativação imediata de empresas, cifragem das chaves e teste Wintouch exclusivamente de leitura com transporte simulado. Para importar são necessárias as credenciais de cada cliente.

O backup inclui agora a plataforma e os esquemas das empresas; a recuperação compara tabelas e contagens. A empresa inicial e os acessos anteriores foram preservados. Consulte [ADMINISTRACAO-PLATAFORMA.md](ADMINISTRACAO-PLATAFORMA.md) para configuração e atualização dos esquemas.

Esta entrega corresponde à **Fase 1 funcional**, com PDF de fecho, anexos e exportação CSV de movimentos, mais gestão central de empresas e configuração Wintouch. Os módulos das fases 2–5 estão identificados como pendentes. A análise Wintouch usa o Swagger guardado no projeto existente; a configuração e o teste de leitura estão disponíveis, mas ainda não há sincronização de dados. A integração bancária continua sem prestador confirmado.

A importação CSV/Excel, depósitos com talões e trânsito, receitas/custos reconhecidos, lotes e reconciliação ainda não estão implementados. O ambiente foi validado localmente; ainda não foi publicado nem auditado para utilização em produção.

O runner de testes apresenta um aviso de descontinuação do transporte httpx no Starlette instalado. Os testes passam; o aviso não afeta os pedidos da aplicação. As versões usadas estão guardadas em `backend/requirements.lock.txt`.
# Movimentos reais e previstos — 11/10/2026

## Resumo por meio de pagamento

- Resumo dos movimentos reais por meio, antes da paginação: quantidade, créditos, débitos, saldo e total geral. Os documentos de venda não mostram este resumo de recebimentos.
- Navegador: cartão local e cartões Wintouch agrupados, 13 movimentos com 130 € de créditos, 10 € de débitos e 120 € de saldo. Numerário permanece separado. Passar de página preserva o resumo; restringir as datas reduz o total para os dois movimentos locais. Layout móvel e compilação TypeScript/Vite aprovados.
- Apenas apresentação; sem alteração de dados ou saldos locais.

## Mês atual por defeito

- Filtros de movimentos reais/previstos e datas de importação assumem o primeiro e último dia do mês corrente em Europe/Lisbon.
- Navegador com relógio simulado: fevereiro de 2024 termina a 29; 31/07/2026 às 23:30 UTC já seleciona agosto em Lisboa, mesmo com navegador em Nova Iorque. Limpar datas continua a permitir consulta sem período.
- Compilação TypeScript/Vite e validação dos três ecrãs aprovadas, sem importações ou alterações nos dados da empresa.

## Totais do período selecionado

- Créditos, débitos e saldo calculados em cêntimos sobre todos os registos filtrados, antes da paginação. Os previstos usam os saldos por liquidar; a consulta de documentos não mostra totais de recebimentos.
- Navegador com dados simulados de ambas as origens: 150 € de créditos, 10 € de débitos e 140 € de saldo; mudar a página preserva os totais. Ao restringir o período aos movimentos locais: 40 €, 0 € e 40 €. Documentos não apresentam este resumo. Layout móvel aprovado.
- Compilação TypeScript/Vite aprovada. Não foram alterados dados, saldos de caixas ou relatórios.

## Tabela única de movimentos reais

- Movimentos locais e recebimentos Wintouch reunidos numa tabela, ordenados por data, com coluna Origem e filtros/paginação comuns. Documentos alternam a mesma tabela, sem outro painel.
- Navegador com 2 movimentos locais e 12 recebimentos simulados: uma tabela, 14 registos, duas páginas, filtro inclusivo, reposição de página, identificação de origem, anexos/estornos apenas locais e layout móvel aprovados.
- Nenhuma alteração à base de dados, saldos ou relatórios. Compilação TypeScript/Vite aprovada.

## Filtros e paginação

- Movimentos locais, previstos, pagamentos e documentos Wintouch: componente comum com datas inclusivas e 10/25/50/100 registos por página.
- Navegador com 31 registos simulados: navegação, mudança de tamanho e reposição da primeira página, intervalo inclusivo, intervalo inválido, limpeza, vencimentos e layout móvel aprovados. Sem alterações nos dados da empresa.
- Datas de origem sem offset preservam o dia indicado; timestamps com offset usam o dia em Europe/Lisbon, tanto no filtro como na apresentação.
- Compilação TypeScript/Vite aprovada. Não foram alteradas rotas nem estruturas da base de dados; a paginação é feita sobre os registos já carregados.

## Importação de vendas Wintouch

- Suite completa: **28 testes aprovados**. TypeScript/Vite e Ruff aprovados; cópia posterior à importação restaurada numa base temporária, com contagens de todas as tabelas verificadas.
- Migração 0005: snapshot financeiro por empresa e loja, com IDs únicos de origem.
- Verificação com a API configurada: 70 documentos consultados para 01–11/10/2026, resultando em 5 documentos de venda e 4 pagamentos. Primeira execução gravou 9 registos; a repetição atualizou os mesmos 9, sem duplicados.
- Testes adicionais: pagamentos separados de faturas, crédito, pagamentos posteriores, rejeição de meios/estados desconhecidos, moeda não EUR, montantes inválidos, rollback, saldo liquidado, perfis de acesso e empresa atual sem aceitar IDs de outras empresas.
- Navegador: período, loja e confirmação demo, importação simulada, consultas de recebimentos/documentos/pendentes e layout móvel. Nenhum envio ou alteração na Wintouch.
- Os movimentos Wintouch são snapshots de consulta e não alteram saldos, dashboard ou relatórios financeiros locais. Consulte [VENDAS-WINTOUCH.md](VENDAS-WINTOUCH.md).

- Suite completa: 22 testes aprovados; compilação TypeScript/Vite e Ruff aprovados.
- Previsto não cria lançamento financeiro; liquidações parciais e totais criam movimentos reais com documento, entidade, data e meio de pagamento, numa transação atómica.
- Verificados: repetição da mesma referência sem duplicação, rejeição de valores superiores ao saldo, caixa de outra loja, perfil de consulta e reposição do saldo após estorno.
- Migração 0004 aplicada às empresas existentes. Cópia de segurança posterior restaurada numa base temporária, com verificação das contagens de todas as tabelas.
- Navegador: dois menus, documento previsto, formulário de criação, painel de liquidação e largura móvel, usando dados simulados sem criar transações nos dados da empresa.
- Importação automática de documentos financeiros Wintouch ainda não implementada. As liquidações requerem caixa aberta, seguindo o fluxo financeiro existente.
