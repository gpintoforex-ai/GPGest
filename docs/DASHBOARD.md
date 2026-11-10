# Dashboard de gestão (Visão geral)

O painel inicial reúne, para a empresa ativa e o estabelecimento escolhido no topo, quatro áreas que podem ser ocultadas ou reordenadas em **Personalizar** (preferência guardada no navegador, por utilizador): **Resumo do negócio**, **Evolução**, **O que precisa da sua atenção** e **Ações rápidas**. Mantêm-se a configuração inicial, a operação de caixa e os últimos movimentos.

Filtros: período (atalhos de dia, semana, mês, trimestre e ano) e comparação com o **período anterior** ou o **mesmo período do ano anterior**. Num mês em curso compara com os mesmos dias do mês anterior; um mês completo compara com o mês anterior completo; outros intervalos, com o período imediatamente anterior com o mesmo número de dias. A metodologia é sempre indicada. Cada indicador tem um ícone «i» com o significado, a fórmula, a origem, a última atualização e as limitações. «Sem dados» distingue-se de zero.

Todos os cálculos são feitos no servidor (`/api/dashboard/overview`, `backend/app/dashboard.py`), por blocos independentes: um erro num bloco não impede os restantes. Os cálculos reutilizam os módulos existentes.

## Versão nova (lado a lado com a atual)

**Experimentar a nova versão** (nos filtros do painel) mostra o Dashboard modernizado; **Voltar à versão atual** regressa. A escolha fica no navegador, por empresa e utilizador (`iazela-dashboard-versao:<empresa>:<utilizador>`); a versão atual continua a ser a predefinida até a nova ser validada. As duas usam a mesma consulta (`/api/dashboard/overview`), os mesmos filtros (atalhos, intervalo, comparação anterior/homóloga, estabelecimento do topo), os mesmos blocos e as mesmas permissões (`frontend/src/DashboardHome.tsx`, `ManagementDashboard.tsx`).

- **Cartões principais:** Vendas (com/sem IVA indicado, variação, documentos), Compras e despesas (total das despesas por rubrica, sem IVA dedutível no regime normal, variação), **Margem bruta apurada** (margem %, lucro bruto, vendas consideradas, barra de cobertura de custos, selo «Margem parcial» e **Ver artigos sem custo**; cor pelo nível de cobertura) e Tesouraria (saldo disponível conhecido, avisos, TPA por liquidar não somado). Cada um abre a página de detalhe e tem o «i».
- **Separadores:** as mesmas áreas, mais **Margens e custos** (ver abaixo). **Evolução** organiza em cartões os gráficos atuais (com **Ver tabela**) e acrescenta **Previsão de IVA** em detalhe, **Evolução de vendas e compras** e **Alertas e tarefas** (os cinco primeiros alertas; **Ver todos** abre a área completa). **Encomendas e abastecimento** junta os sinais de encomendas e a visão de trabalho (pedidos, evolução, trabalho por área), para todos os perfis.
- **Personalizar:** áreas (mostrar/ocultar, ordem) e cartões da Evolução (mostrar/ocultar, ordem); **Repor**.
- **Previsão de IVA (detalhe):** saldo estimado a pagar ou a recuperar, período filtrado e periodicidade configurada, IVA liquidado, IVA dedutível estimado (por rubrica), IVA suportado não dedutível, documentos considerados e origem. Marca **provisória** quando há compras sem rubrica (o IVA dessas compras conta como dedutível até serem classificadas) e indica que é uma estimativa de gestão, não o apuramento da declaração periódica, sem regularizações nem créditos de períodos anteriores. A fórmula não mudou.
- **Evolução de vendas e compras:** vendas = faturação (série do gráfico de faturação); compras = documentos de compra e custos sem documento das despesas por rubrica, pela data do documento, com a mesma fórmula do total (`expenses.points`, mesmos períodos da faturação). Não são recebimentos nem pagamentos; se as vendas estiverem com IVA (bases em falta), o gráfico avisa.

Validação: `scripts/dashboard-baseline.py` (valores atuais vs nova versão do servidor, ver [DASHBOARD-AUDITORIA.md](DASHBOARD-AUDITORIA.md)) e `scripts/test-dashboard-modern-ui.py` (interface, temas, larguras e contraste).

## Indicadores e gráficos

| Indicador / gráfico | Fórmula e origem |
|---|---|
| Previsão de IVA | No Resumo do negócio: IVA liquidado nas vendas menos IVA dedutível estimado das compras, com as percentagens por rubrica do Mapa de IVA. Respeita período e loja; mostra saldo a pagar ou crédito estimado, sem extrapolar vendas futuras. Usa SAF-T ou, na sua ausência, vendas ERP com IVA discriminado. Só administrador e gestor, no regime normal. Não inclui saldos transitados nem regularizações. |
| Top 10 de artigos vendidos | Área Artigos mais vendidos: quantidade líquida (vendas menos devoluções), com desempate pelo valor sem IVA. Mostra código, descrição, quantidade e vendas. Usa as linhas do SAF-T ou, quando o ERP é a fonte de faturação, as linhas Wintouch já importadas para o stock. Não soma ambas as fontes. Exclui documentos anulados/inativos; não exige custos, ficha local de stock ou saída de stock aplicada. Disponível para administrador, gestor e consulta. |
| Faturação | SAF-T: documentos FT, FS, FR, ND, NC normais pela data do documento, **sem IVA**, notas de crédito subtraem, anulados excluídos (`billing_conference.documents`). Sem SAF-T, vendas do ERP **com IVA** (o ERP não fornece a base). Gráfico por dia, semana ou mês, com a série de comparação. |
| Saldo disponível conhecido | Um saldo por conta/caixa, o mesmo da previsão de liquidez (último extrato ou saldo de referência; caixas: fundo + movimentos). Nunca extrato + livro da mesma conta. Assinala saldos incompletos, estimados ou desatualizados. Outras moedas não são somadas. TPA por liquidar mostrado à parte. |
| Recebimentos e pagamentos | Recebimentos registados menos reembolsos; pagamentos a fornecedores, despesas, saídas de caixa e encargos bancários (`billing_conference.receipts` / `payment_list`). Excluem transferências entre contas próprias e estornos; a liquidação do TPA não é um novo recebimento. Com IVA. |
| A pagar / a receber por vencimento | Saldos por liquidar da previsão de liquidez (movimentos previstos, documentos do ERP em aberto, previsões manuais), em vencido, 7 dias, 8–30 dias, mais de 30 dias e sem data. Uma fatura registada não conta como paga. |
| Saldo previsto (30 dias) | Cenário base da previsão de liquidez: ponto de partida no saldo atual conhecido e projeção tracejada; reserva mínima como referência. |
| Despesas por rubrica | Documentos de compra pela data do documento, mais os custos sem documento registados no banco com rubrica, pela data do movimento (mapas de gestão): regime normal = base + IVA não dedutível estimado; outros regimes = total. Compras de mercadoria não são custo das vendas. Só administrador e gestor. |
| Vendas e margem por família | Linhas por artigo do SAF-T de vendas (artigo pelo código ou pela correspondência SAF-T dos parâmetros de stock) ou, no modo integrado, das vendas Wintouch importadas (artigo pela ficha local ligada ao artigo ERP), agrupadas pela família/categoria da ficha. Margem e cobertura como em **Margens e custos**. Só administrador e gestor; custos exigem também a permissão de ver custos de stock. |

## Margens e custos

Margem bruta **histórica** das vendas de mercadorias (`backend/app/margin_analysis.py`, `frontend/src/MarginInsights.tsx`):

- **Custo das mercadorias vendidas**, pelo método de valorização do stock (custo médio ponderado móvel da empresa). É o custo gravado na saída de stock dessa venda (venda Wintouch ou SAF-T integrada no stock, chaves `wt-stock:`/`saft-stock:`). Sem essa saída, usa-se o custo médio reconstituído com os movimentos até à data da venda, assinalado como «estimado». **Nunca o último custo de compra.** As compras posteriores não mudam a margem de vendas passadas. Os bónus e descontos já estão no custo de entrada, por isso a margem histórica reflete o custo efetivo. A margem comercial de referência e a margem efetiva de receção estão na revisão de preços, não aqui.
- **Devoluções e notas de crédito** subtraem a venda e o custo. As devoluções usam o custo da sua entrada de stock ou o custo médio à data.
- **Serviços** (fichas «Serviço») ficam fora da margem e da cobertura e são indicados à parte. **Custo zero** é um custo conhecido (por exemplo, uma oferta), assinalado para revisão mas não tratado como erro.
- **Cobertura de custos** = vendas de mercadorias com custo ÷ vendas de mercadorias. A margem é sempre apresentada sobre as vendas com custo, com o nível de cobertura: **completa** (100 %, verde), **parcial** (80–99,9 %, âmbar) ou **parcial com cobertura baixa** (abaixo de 80 %, vermelho). Com cobertura parcial, o painel diz explicitamente que não é a margem global do período.
- **Motivos da falta de custo:**
  - sem custo registado;
  - custo zero;
  - custo histórico indisponível à data da venda, quando a primeira entrada é posterior;
  - sem correspondência com a ficha local, quando o artigo ERP ou o código SAF-T não está ligado;
  - artigo não identificado;
  - artigo inativo sem custo;
  - dados incompletos, quando há uma entrada sem custo no stock.
- **Cartões:** margem bruta, lucro bruto, cobertura de custos (barra), vendas sem custo conhecido e artigos que necessitam de revisão. Também mostra os motivos com valor e número de artigos.
- **Gráficos:**
  - evolução da margem (%) e da cobertura (%) por dia, semana ou mês;
  - margem por artigo, para as maiores vendas com custo;
  - artigos com margem negativa;
  - margem por família, na Evolução.
- **Ver artigos sem custo** (`GET /api/dashboard/margin-articles`, só leitura, exige a permissão de ver custos). A grelha mostra código, descrição, família, quantidade vendida, vendas sem IVA, custo utilizado, origem do custo, margem, motivo e estado ERP. Tem pesquisa, filtros (a rever, sem custo, custo zero, margem negativa, custo estimado, motivo) e ordenação. **Corrigir** abre a correção assistida:
  - a ação recomendada para o motivo, como registar a receção, valorizar o inventário inicial ou associar o artigo ERP;
  - o custo médio atual e o último custo, só para consulta;
  - as últimas compras, com o custo de referência e o efetivo;
  - os movimentos de stock recentes.

  Nenhum custo histórico nem documento é alterado. Corrige-se na origem e usa-se **Recalcular indicadores**.

## O que precisa da sua atenção

Regras verificáveis, cada uma com facto, valores, impacto, ação recomendada e ligação: pagamentos vencidos e a vencer em 7 dias; recebimentos vencidos; saldo previsto abaixo da reserva; contas bancárias sem extrato recente; TPA por liquidar; quebra de faturação (≤ −15% face à comparação); aumento de despesas (≥ +20% e ≥ 100 €); compras sem rubrica; caixas de dias anteriores por fechar; fechos com diferenças; movimentos bancários com mais de 7 dias por reconciliar; documentos de compra sem anexo; faturas do e-Fatura por lançar; lotes com validade nos próximos 30 dias; artigos abaixo do mínimo. Nenhuma recomendação altera preços, cria pagamentos ou envia emails. Não é usada IA.

## Permissões

Administrador e gestor veem tudo; consulta vê faturação e tesouraria, sem custos nem resultados; operador vê apenas a operação (caixas, reconciliação, stock). As restrições são aplicadas na API.

## Limitações atuais e como as resolver

- **Top de artigos pelo ERP:** usa o detalhe guardado na importação de vendas, sem nova chamada ao ERP nem novos movimentos de stock. As quantidades são unidades de stock da origem. Documentos sem linhas completas tornam o top parcial, com aviso para repetir a importação do período com o detalhe de stock. O histórico depende das linhas já importadas a partir do início do controlo de stock. Artigos ainda sem catálogo são identificados pelo ID do ERP.

- **Margem bruta:** depende de custos no stock. Vêm do **inventário inicial valorizado** (Compras e Stock → Importações), das receções de mercadoria e das entradas valorizadas. Sem custos, a cobertura baixa e a margem fica identificada como parcial. Os descontos globais do documento SAF-T não são repartidos pelas linhas. Não há revisão manual de custos históricos: corrige-se na origem.
- **Faturação líquida no modo integrado:** a sincronização de vendas Wintouch guarda a base tributável e o IVA de cada documento (`BaseValue1–4`, `VATValue1–4`), quando batem com o total (tolerância de 2 cêntimos). O painel mostra a faturação sem IVA só quando todos os documentos do período e da comparação têm base; caso contrário mostra com IVA e indica quantos documentos faltam — volte a sincronizar as vendas desse período.
- **Saldo realizado histórico:** o gráfico de previsão parte do saldo atual; não há série diária de saldos passados de caixa.
- **Consolidação entre empresas:** cada empresa tem dados separados; o painel mostra a empresa ativa.
