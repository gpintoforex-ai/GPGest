# Dashboard de gestão (Visão geral)

O painel inicial reúne, para a empresa ativa e o estabelecimento escolhido no topo, quatro áreas que podem ser ocultadas ou reordenadas em **Personalizar** (preferência guardada no navegador, por utilizador): **Resumo do negócio**, **Evolução**, **O que precisa da sua atenção** e **Ações rápidas**. Mantêm-se a configuração inicial, a operação de caixa e os últimos movimentos.

Filtros: período (atalhos de dia, semana, mês, trimestre e ano) e comparação com o **período anterior** ou o **mesmo período do ano anterior**. Num mês em curso compara com os mesmos dias do mês anterior; um mês completo compara com o mês anterior completo; outros intervalos, com o período imediatamente anterior com o mesmo número de dias. A metodologia é sempre indicada. Cada indicador tem um ícone «i» com o significado, a fórmula, a origem, a última atualização e as limitações. «Sem dados» distingue-se de zero.

Todos os cálculos são feitos no servidor (`/api/dashboard/overview`, `backend/app/dashboard.py`), por blocos independentes: um erro num bloco não impede os restantes. Os cálculos reutilizam os módulos existentes.

## Indicadores e gráficos

| Indicador / gráfico | Fórmula e origem |
|---|---|
| Faturação | SAF-T: documentos FT, FS, FR, ND, NC normais pela data do documento, **sem IVA**, notas de crédito subtraem, anulados excluídos (`billing_conference.documents`). Sem SAF-T, vendas do ERP **com IVA** (o ERP não fornece a base). Gráfico por dia, semana ou mês, com a série de comparação. |
| Saldo disponível conhecido | Um saldo por conta/caixa, o mesmo da previsão de liquidez (último extrato ou saldo de referência; caixas: fundo + movimentos). Nunca extrato + livro da mesma conta. Assinala saldos incompletos, estimados ou desatualizados. Outras moedas não são somadas. TPA por liquidar mostrado à parte. |
| Recebimentos e pagamentos | Recebimentos registados menos reembolsos; pagamentos a fornecedores, despesas, saídas de caixa e encargos bancários (`billing_conference.receipts` / `payment_list`). Excluem transferências entre contas próprias e estornos; a liquidação do TPA não é um novo recebimento. Com IVA. |
| A pagar / a receber por vencimento | Saldos por liquidar da previsão de liquidez (movimentos previstos, documentos do ERP em aberto, previsões manuais), em vencido, 7 dias, 8–30 dias, mais de 30 dias e sem data. Uma fatura registada não conta como paga. |
| Saldo previsto (30 dias) | Cenário base da previsão de liquidez: ponto de partida no saldo atual conhecido e projeção tracejada; reserva mínima como referência. |
| Despesas por rubrica | Documentos de compra pela data do documento (mapas de gestão): regime normal = base + IVA não dedutível estimado; outros regimes = total. Compras de mercadoria não são custo das vendas. Só administrador e gestor. |
| Vendas e margem por família | Linhas do SAF-T por artigo, agrupadas pela família/categoria da ficha do artigo. Margem bruta = vendas com custo conhecido − quantidade × custo médio ponderado das entradas de stock; só é mostrada com custo conhecido em pelo menos 80% das vendas. Margem bruta não é lucro. Só administrador e gestor; custos exigem também a permissão de ver custos de stock. |

## O que precisa da sua atenção

Regras verificáveis, cada uma com facto, valores, impacto, ação recomendada e ligação: pagamentos vencidos e a vencer em 7 dias; recebimentos vencidos; saldo previsto abaixo da reserva; contas bancárias sem extrato recente; TPA por liquidar; quebra de faturação (≤ −15% face à comparação); aumento de despesas (≥ +20% e ≥ 100 €); compras sem rubrica; caixas de dias anteriores por fechar; fechos com diferenças; movimentos bancários com mais de 7 dias por reconciliar; documentos de compra sem anexo; faturas do e-Fatura por lançar; lotes com validade nos próximos 30 dias; artigos abaixo do mínimo. Nenhuma recomendação altera preços, cria pagamentos ou envia emails. Não é usada IA.

## Permissões

Administrador e gestor veem tudo; consulta vê faturação e tesouraria, sem custos nem resultados; operador vê apenas a operação (caixas, reconciliação, stock). As restrições são aplicadas na API.

## Limitações atuais e como as resolver

- **Margem bruta:** exige custo conhecido em pelo menos 80% das vendas. O custo vem das entradas de stock com custo: **inventário inicial valorizado** (Compras e Stock → Importações) e, depois, compras de mercadoria confirmadas em Compras. Sem custos, o painel mostra «Indisponível» e a percentagem de vendas com custo conhecido.
- **Faturação líquida no modo integrado:** a sincronização de vendas Wintouch guarda a base tributável e o IVA de cada documento (`BaseValue1–4`, `VATValue1–4`), quando batem com o total (tolerância de 2 cêntimos). O painel mostra a faturação sem IVA só quando todos os documentos do período e da comparação têm base; caso contrário mostra com IVA e indica quantos documentos faltam — volte a sincronizar as vendas desse período.
- **Saldo realizado histórico:** o gráfico de previsão parte do saldo atual; não há série diária de saldos passados de caixa.
- **Consolidação entre empresas:** cada empresa tem dados separados; o painel mostra a empresa ativa.
