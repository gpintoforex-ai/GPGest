# Importação manual Wintouch — 11/10/2026

Entre na sua empresa e abra **Integrações → Wintouch → Dados a importar → Sincronizar agora**. Pode escolher artigos, entidades (clientes/fornecedores) ou caixas e consultar o histórico da sua empresa. Apenas o administrador da empresa pode gerir sincronizações; não precisa de sessão da plataforma. A ligação e as credenciais API continuam a ser definidas pela administração central. Na demonstração é obrigatória confirmação explícita de que pretende guardar dados da origem nesse ambiente. A implementação não inicia importações automaticamente.

Foram feitas três pequenas leituras reais, uma por endpoint com `limit=1`, na ligação configurada: `products`, `entities` e `cash_accounts` responderam HTTP 200 com listas. Foi verificado apenas o formato/campos, sem guardar estes dados ou publicar valores pessoais. Os campos `IsClient` e `IsSupplier` foram confirmados na resposta de entidades e usados como classificação explícita.

## Comportamento

- Todas as chamadas remotas são GET, com chave cifrada recuperada no servidor, identificador da empresa e redirecionamentos desativados.
- A importação consulta páginas de até 200 registos usando `limit` e `skip`, até uma página vazia. Avança pelo número efetivamente recebido, permitindo limites de página menores da API.
- O catálogo é atualizado por empresa e ID externo. Repetir a operação atualiza os registos existentes; não duplica IDs nem altera ligações locais de caixas.
- Uma transação por recurso: erro de comunicação, JSON inválido, IDs repetidos, formato incompatível ou total informado incompleto cancela todas as alterações dessa execução. Os dados anteriores permanecem e a última sincronização bem-sucedida não avança.
- A execução tem histórico de início/fim, estado e contagens, com erros sanitizados. Só uma execução do mesmo recurso/empresa pode estar em curso. Execuções interrompidas há mais de dez minutos são encerradas como falha quando tentar de novo.
- Limites de proteção: 20 segundos por chamada, verificação de duração de três minutos entre páginas e no máximo 50 000 registos. A duração total pode incluir a última chamada e gravação. Não se declara sucesso quando estes limites interrompem a operação.
- Não elimina registos ausentes: ausência numa listagem não prova eliminação. Usa `Enabled` quando explicitamente recebido.

## Campos e limites atuais

Artigos: ID, código, nome, estado, custo `LastCostGlobal`, controlo de lotes e metadados de origem conhecidos. O custo exige precisão compatível com `NUMERIC(18,4)`. Preços de venda, moeda e stock requerem os adaptadores dos endpoints respetivos; não são inferidos a partir de outros preços.

Entidades: ID, código, nome, NIF e os campos de email/telefone se a listagem os fornecer; `IsClient` e `IsSupplier` podem coexistir. Campos desconhecidos mantêm-se desconhecidos e dados ausentes não apagam os campos locais previamente recebidos. Não importa moradas, dados bancários nem respostas completas da API.

Caixas: ID, código e nome. Mantém mapeamentos existentes para lojas/caixas GPGest. Não interpreta automaticamente o estado do turno, o fundo ou o saldo como caixa local.

Datas `LastUpdate` sem offset não recebem um fuso presumido. O identificador externo é preservado; respostas sem ID/nome válido são rejeitadas. A API pode mudar durante a paginação: repita a sincronização se isso provocar repetição de IDs ou alteração do total indicado.

Esta entrega faz atualização completa por recurso, a pedido. O cursor é reservado para uma futura sincronização incremental confirmada pela API. A periodicidade automática e a execução por fila em produção continuam por configurar conforme os limites do fornecedor.

Documentos, pagamentos, movimentos e turnos financeiros continuam separados e sem importação nesta entrega; exigem validação dos seus campos, anulações e mapeamentos antes de utilização nos saldos. Consulte [TABELAS-WINTOUCH.md](TABELAS-WINTOUCH.md).
# Vendas e recebimentos

Existe agora uma importação própria em **Integrações → Importar vendas Wintouch**, com escolha de loja e período. Consulte [VENDAS-WINTOUCH.md](VENDAS-WINTOUCH.md). Os dados de venda, pagamentos e crédito podem ser consultados nos menus de movimentos, separados dos lançamentos locais. As limitações financeiras abaixo referem-se à integração no livro de caixa e às tarefas automáticas.
