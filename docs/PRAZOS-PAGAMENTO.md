# Prazos de pagamento

**Tabelas → Prazos de pagamento** reúne os prazos usados nas fichas de clientes e fornecedores e no cálculo de vencimentos. A tabela existe nos dois modos de funcionamento; a sincronização só aparece no modo integrado.

## Campos

| Campo | Regra |
|---|---|
| Código | Único, em maiúsculas, sem espaços (ex.: `30DIAS`). |
| Nome | Texto apresentado nas fichas (ex.: «30 Dias»). |
| Tipo de pagamento | **Após N dias**, **Fim do mês** ou **Fim do próximo mês**. Os prazos da Wintouch com um tipo ainda não confirmado aparecem como «Outro (Wintouch)» e não calculam vencimento. |
| Dias | Obrigatório em «Após N dias»; vazio nos restantes tipos. |
| Desconto (%) | Até duas casas decimais, guardado em centésimas de ponto percentual. É informativo: não altera valores de documentos. |
| Estado | Um prazo inativo deixa de ser proposto em novas escolhas; as entidades que já o usam mantêm-no. |

Criar, alterar, ativar/desativar e remover exige perfil de administrador ou gestor e fica na auditoria. Não é possível remover um prazo usado por clientes ou fornecedores: desative-o.

## Sincronizar com a Wintouch

A tabela segue o [comportamento comum das tabelas](TABELAS-COMPORTAMENTO-COMUM.md). Em **Integração com o ERP**:

- Um prazo criado na IAZELA é gravado logo na Wintouch (código, nome, tipo, dias e desconto), com a
  escrita autorizada em Integrações; só fica «Sincronizado» depois de a Wintouch confirmar.
- **Sincronizar** acrescenta os prazos da Wintouch em falta, liga ao da Wintouch um prazo da IAZELA com o
  **mesmo código**, atualiza os ligados e envia as alterações feitas na IAZELA. Um prazo com o mesmo
  código e dados diferentes, ou apenas com a mesma descrição, aparece como conflito para decidir.
- **Remover do ERP** elimina o prazo na Wintouch por pedido explícito. A Wintouch não tem estado
  ativo/inativo nos prazos, por isso desativar um prazo só afeta a IAZELA.

A sincronização de entidades atualiza o catálogo de prazos da Wintouch (`wt_payment_terms`), usado para
mostrar o prazo de cada entidade, mas **não** altera esta tabela: use **Sincronizar** na própria tabela.

Tipos Wintouch confirmados numa leitura real: `0` = Após, `3` = Fim do mês, `4` = Fim do próximo mês.

## Fichas de clientes e fornecedores

A ficha tem a lista **Prazo de pagamento** com os prazos ativos. Numa entidade da Wintouch sem prazo escolhido na IAZELA, a lista mostra «Igual ao ERP (…)» e a entidade segue o prazo da Wintouch; escolher outro prazo fica na IAZELA (estado «Alterações pendentes») e só chega à Wintouch com **Enviar para o ERP**. Num prazo «Após N dias», os dias ficam também na entidade (`payment_days`); alterar os dias do prazo atualiza as entidades que o usam.

Na conferência de faturas, sem vencimento indicado ou lido no anexo, o vencimento é calculado pelo prazo do fornecedor: data do documento + N dias, último dia do mês do documento ou último dia do mês seguinte.

## Migração

A migração **0061** cria `payment_terms` e `business_entities.payment_term_id`. Os dias já indicados nas fichas passaram a prazos «N Dias» (código `NDIAS`), que se ligam aos da Wintouch com o mesmo código na primeira sincronização. A migração **0062** passou as ligações à Wintouch para a tabela comum `erp_links`.
