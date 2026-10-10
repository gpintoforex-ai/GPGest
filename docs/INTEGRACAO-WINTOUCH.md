# Análise do projeto Wintouch — 10/10/2026

Origem analisada: `C:\Projetos\Wintouch`. Análise de código, Swagger guardado no projeto e metadados das bases SQLite locais abertas em modo de leitura. Não foram feitas chamadas à API remota, alterados dados do Wintouch ou copiados segredos para o GPGest.

## Evidência disponível

- `backend/app/main.py`: cliente HTTP, autenticação `Authorization: ApiKey`, configuração por empresa e sincronização local de artigos, categorias e entidades. Inclui consulta de preços, stock, pendentes e documentos de encomenda.
- `backend/_swagger_v1.json`: documentação **Wintouch Web API v1**. A versão v1 da API não identifica a versão instalada do produto Wintouch.
- Uma base local contém 6 859 artigos, 678 entidades e 128 categorias. Há outra base de empresa sem artigos/entidades. Estes números provam a existência de dados previamente guardados, não uma ligação remota atualmente validada.
- Os artigos guardados têm identificador, código, nome, categorias, `LastCostGlobal`, `LastUpdate`, `IsBatchesEnabled` e parâmetros de controlo de lotes.
- `backend/app/services/wintouch_client.py` contém apenas `{}`; o cliente efetivo encontra-se em `main.py`.

## Funcionalidades que podem ser aproveitadas

| Área GPGest | Evidência | Implementação proposta |
| --- | --- | --- |
| Artigos e categorias | `GET products`, `product_1st_categories` a `product_7th_categories`, sincronização já existente | Catálogo local PostgreSQL, IDs externos, pesquisa e atualização sem duplicados. |
| Preços, custos e stock | `GET products/{id}/prices`, `products/{id}/stocks`; campo local `LastCostGlobal` | Stock importado separado dos lotes locais; preços/custos com precisão decimal e origem identificada. Validar unidade, IVA e significado do custo antes de calcular margens. |
| Clientes e fornecedores | `GET entities`, endereços e pendentes já consultados pelo projeto | Seleção de entidades nos movimentos, contas a receber/pagar. Validar classificação de cliente/fornecedor. |
| Caixa e turnos | Swagger: `GET cash_accounts`, `opened_workshifts`, `closed_workshifts`, `closing_summary` | Consultar sessões e comparar o resumo Wintouch com a contagem GPGest. Não criar duas sessões para o mesmo turno. |
| Meios de pagamento | `GET payment_methods`, `cash_accounts/{id}/payment_methods_closing_summary` | Mapeamento explícito para numerário, cartão, transferência e outros; conciliação por meio. |
| Vendas, devoluções e recebimentos | Swagger: `GET product_documents`, `pos/receipts`, `settlement_documents` | Importar documentos e pagamentos separadamente. Não tratar uma encomenda como venda paga nem uma fatura como recebimento. |
| Lotes e validades | Swagger: `GET products/{id}/product_batches`, `products/{id}/batches` | Modelo `ProductBatch` documenta `ID`, `ProductID`, `Batch1/2/3`, `ExpirationDate`, `EntryDate`, `Version`. Confirmar resposta real e quantidades por lote antes de FEFO e alertas. |

Os caminhos da tabela são relativos ao prefixo `/api/{apiVersion}/` documentado. Só serão ativados endpoints de leitura especificamente permitidos no adaptador; um método GET por si só não garante ausência de efeitos secundários em todos os endpoints do Swagger.

## Limites e validações ainda necessárias

1. **Empresa de destino:** existem duas bases de empresa. Confirmar qual corresponde à empresa/loja do GPGest antes de importar dados reais. Não misturar empresas.
2. **Autenticação:** o Swagger marca `x-current-enterprise` como obrigatório; o cliente comum existente envia ApiKey e Accept. Confirmar o comportamento da instalação/empresa e os cabeçalhos efetivamente necessários.
3. **Formato das respostas:** vários endpoints documentam o retorno como `System.Object`. A existência do caminho não valida os campos ou a estrutura do resultado. Consultar amostras reais sem publicar dados pessoais.
4. **Paginação:** o Swagger documenta `limit`, `skip` e `sort`; algumas sincronizações existentes não os utilizam. Implementar paginação completa antes de considerar uma importação concluída.
5. **Anulações, correções e datas:** confirmar IDs estáveis, estado dos documentos, data do pagamento, moeda, estabelecimento, caixa e turno. Resumos agregados não substituem movimentos identificáveis.
6. **Precisão monetária:** o projeto anterior usa `float` em diversos cálculos. O GPGest deve manter Decimal na leitura e cêntimos no registo, sem copiar esses cálculos financeiros.
7. **Lotes:** a validade está documentada, mas a quantidade disponível por lote não foi confirmada. Não distribuir o stock total por lotes presumidos.
8. **Bancos:** `banks` é uma área documentada do Wintouch; não constitui confirmação de acesso a extratos bancários nem substitui Open Banking.
9. **Atualidade:** confirmar que o Swagger guardado corresponde à API utilizada e que a chave autoriza cada endpoint. A análise não testou a ligação remota.

## Ordem recomendada

1. Adaptador Wintouch próprio no GPGest, só de leitura, com segredos no servidor, empresa selecionada e teste de ligação.
2. Sincronização de artigos, categorias e entidades com paginação, IDs externos únicos, histórico de execução e reprocessamento seguro. Não importar dados reais para a base de demonstração atual.
3. Mapeamento de lojas/caixas e meios de pagamento; pré-visualização dos turnos, documentos e recebimentos antes de gerar movimentos locais.
4. Comparação de fecho Wintouch/GPGest e importação de recebimentos individuais sem duplicação de registos manuais.
5. Lotes e alertas após validação de quantidade, unidade e validade; manter gestão local quando a origem não fornecer os dados necessários.

Não reutilizar as rotas de criação de encomendas, alteração de entidades, abertura/fecho POS ou emissão de documentos do projeto anterior: o GPGest é uma ferramenta de gestão e não deve escrever no Wintouch nesta integração.

Esta análise serviu de base à gestão central de empresas. A configuração por cliente, teste de leitura e [importação manual de artigos, entidades e caixas](SINCRONIZACAO-WINTOUCH.md) estão disponíveis em `/plataforma`. Consulte [ADMINISTRACAO-PLATAFORMA.md](ADMINISTRACAO-PLATAFORMA.md). A sincronização automática e a importação financeira continuam pendentes.
