# Dashboard — auditoria antes da modernização

Levantamento de 08/11/2026 da página **Dashboard** (Visão geral), antes de qualquer alteração. Serve para
comparar a nova versão com a atual: tudo o que está aqui tem de continuar disponível. Fórmulas detalhadas e
permissões: [DASHBOARD.md](DASHBOARD.md).

## 1. Estrutura atual e ficheiros

| Camada | Ficheiro | Papel |
|---|---|---|
| Página | `frontend/src/App.tsx` (bloco `page === "Dashboard"`) | Monta a configuração inicial, a visão de trabalho e o painel financeiro; injeta «Operação de caixa» e «Últimos movimentos» |
| Configuração inicial | `frontend/src/CompanySetup.tsx` (`CompanySetupPanel`) | Assistente de configuração da empresa (quando incompleta) |
| Visão de trabalho | `frontend/src/WorkOverview.tsx` | Saudação, Centro de Encomendas, KPIs de pedidos (6 semanas), atenção dos pedidos, evolução dos pedidos, trabalho por área |
| Painel financeiro | `frontend/src/ManagementDashboard.tsx` | Filtros, personalização e as 7 áreas (abaixo) |
| Gráficos | `frontend/src/DashboardCharts.tsx` | `ChartFrame` (com **Ver tabela / Ver gráfico**), `ColumnsChart`, `ForecastChart`, `BarList`, `Legend`, dicas |
| Encomendas | `frontend/src/OrderAnalytics.tsx` (`OrderCycleSignals`) | Área «Encomendas e abastecimento» |
| Estilos | `ManagementDashboard.css`, `WorkOverview` (priority-*) | — |
| API | `backend/app/dashboard.py` → `GET /api/dashboard/overview` | Blocos independentes: `sales`, `treasury`, `expenses`, `margin`, `vat`, `top_articles`, `operations`, `attention` |
| API | `GET /api/order-cycle/analytics` (`order_cycle.py`) | Pedidos, entregas, compras previstas |
| API (legado) | `GET /api/dashboard` (`main.py`) | Movimentos e sessões usados por «Operação de caixa» e «Últimos movimentos» |

## 2. Inventário funcional

### Filtros e controlos (painel financeiro)
- Atalhos **Hoje, Esta semana, Mês, Trimestre, Ano** (`DatePresets`), **Desde/Até** (intervalo personalizado, até 400 dias).
- **Comparar com**: período anterior (`previous`) ou mesmo período do ano anterior (`yoy`).
- Estabelecimento: seletor global do topo (`store_id`); empresa: a da sessão.
- **Atualizar** (nova consulta) e **Personalizar** (mostrar/ocultar e reordenar as 7 áreas; guardado no navegador por utilizador: `iazela-dashboard:<empresa>:<utilizador>`; **Repor**).
- Linha de contexto: empresa, âmbito, método de comparação, período em curso, hora de atualização.
- Ícone «i» em cada indicador: significado, fórmula, origem, última atualização, limitações (`meta`).

### Áreas (separadores do `TaskWorkspace`)
| Área | Conteúdo | Perfis |
|---|---|---|
| Resumo do negócio | KPI Faturação (com/sem IVA, variação, n.º documentos → abre faturação); Previsão de IVA (saldo, liquidado, dedutível, **Ver mapa de IVA**); Saldo disponível conhecido (contas/caixas, incompleto, desatualizado, TPA por liquidar não somado, outras moedas); Recebimentos e pagamentos (+/−, saldo do período, com IVA); A pagar: vencido e 30 dias (e a receber 30 dias); Margem bruta (ou indisponível com cobertura de custo) | finanças: adm./gestor/consulta; IVA e margem: adm./gestor |
| Evolução | Faturação por dia/semana/mês com série de comparação; Recebimentos e pagamentos (com IVA); Saldo previsto 30 dias (reserva, mínimo); A receber e a pagar por vencimento; Despesas por rubrica (sem IVA dedutível, variação, **Ver documentos por rubrica**); Vendas por família (margem e cobertura de custo). Todos com **Ver tabela** | IDEM |
| Artigos mais vendidos | Top 10 por quantidade líquida (código, artigo, quantidade, vendas sem IVA), aviso de top parcial | adm./gestor/consulta |
| O que precisa da sua atenção | Notas com severidade (crítico/atenção/informação), facto, valores, impacto, ação recomendada e **Abrir** (página com o período) | todos (conteúdo conforme perfil) |
| Ações rápidas | Abrir caixa; Fechar ou conferir caixa; Registar recebimento ou pagamento; Importar SAF-T (local, adm.); Importar extrato; Conferir documentos; Plano de pagamentos; Previsão de liquidez | conforme perfil/modo |
| Operação de caixa | Caixas abertas (caixa, operador, abertura, saldo) e **Gerir caixas**; Últimos movimentos (5) e **Ver todos** | todos |
| Encomendas e abastecimento | `OrderCycleSignals` (do pedido à entrega) | adm./gestor |

### Fora do painel financeiro (mesma página)
- Visão de trabalho: KPIs Pedidos criados, Pedidos por concluir, Entregas no prazo, Compras previstas; «O que precisa da sua atenção» dos pedidos (portes grátis, mercadoria pronta…); Evolução dos pedidos (com **Ver tabela**); Trabalho por área; **Centro de Encomendas** / **Pedidos de clientes**; **Atualizar prioridades**; **Ver sistema visual**.
- Configuração inicial da empresa (quando incompleta).

## 3. Fontes de dados e dependências

| Bloco | Fonte | Dependências |
|---|---|---|
| Faturação (`sales`) | SAF-T importado (`billing_conference.documents`) ou, sem SAF-T, vendas Wintouch (`wt_sale_records`, documentos) | SAF-T; ERP Wintouch (sincronização de vendas) |
| IVA (`vat`) | IVA liquidado: SAF-T ou ERP (`tax_cents`); IVA dedutível: documentos de compra do Mapa de IVA (`management_maps.purchase_documents`) × percentagem de dedução da rubrica | SAF-T/ERP; e-Fatura, Compras, ERP (compras); Configurações fiscais (`vat_settings`: regime, periodicidade) |
| Tesouraria (`treasury`) | Saldos, compromissos e previsão: previsão de liquidez (`app.state.liquidity`); fluxos: `billing_conference.receipts` / `payment_list`; TPA: `pending_settlement` | Bancos (extratos), Tesouraria (caixas, movimentos previstos), ERP (documentos em aberto) |
| Despesas (`expenses`) | `management_maps.cost_documents` por rubrica | e-Fatura, Compras, ERP, registos bancários com rubrica |
| Margem (`margin`) | Linhas por artigo (SAF-T ou vendas Wintouch) × custo das mercadorias vendidas (saída de stock ou custo médio à data) | SAF-T ou ERP; Stock |
| Top artigos (`top_articles`) | Linhas SAF-T ou linhas Wintouch importadas | SAF-T; ERP |
| Atenção (`attention` + `operations`) | Regras sobre os blocos + caixas, reconciliação bancária, anexos, e-Fatura por lançar, stock (mínimos, validades) | Caixa, Bancos, e-Fatura, Stock |
| Encomendas | `order-cycle/analytics` | Encomendas de clientes, Compras |

Fórmulas: ver a tabela «Indicadores e gráficos» de [DASHBOARD.md](DASHBOARD.md); coincidem com o código
(`dashboard.py`) à data deste levantamento.

## 4. Base de comparação (regressão)

`scripts/dashboard-baseline.py` chama as mesmas rotas da página, para períodos fixos (mês em curso e anterior,
3.º trimestre com homólogo, ano), com o utilizador indicado, e grava o JSON em `.local/dashboard-baseline/<etiqueta>/`
(dados reais: pasta privada). `--compare <a> <b>` lista as diferenças, ignorando campos que dependem do momento
(hora de geração, previsão a 30 dias, vencimentos relativos a hoje). Recolha de referência: `antes-2026-11-08`;
duas recolhas seguidas sem alterações dão «Sem diferenças». A nova versão só substitui a atual depois de
`--compare` sem diferenças nos cálculos (ou com diferenças explicadas e aprovadas).

## 5. Observações (sem alteração feita)

1. **Margem bruta histórica (revista a 09/11/2026).** Antes, a margem usava a média de todas as entradas de compra, inventário e saldo inicial do artigo, sem data. Por isso incluía compras posteriores à venda e divergia do stock. Só aparecia com cobertura igual ou superior a 80 %, e só com SAF-T. Agora usa o custo das mercadorias vendidas do stock: o custo da saída de stock da venda ou, sem ela, o custo médio à data. Funciona com SAF-T e com as vendas Wintouch. Os serviços ficam fora. A margem aparece sempre, identificada como parcial quando a cobertura é inferior a 100 %. Os motivos da falta de custo estão na grelha «Ver artigos sem custo». Detalhe em [DASHBOARD.md](DASHBOARD.md#margens-e-custos).
2. **Previsão de IVA** já usa a percentagem de dedução por rubrica (não assume IVA das compras todo dedutível), só no regime normal, e indica que não inclui regularizações nem créditos transitados. Não há data de obrigação fiscal nem separação por período fiscal (usa o período filtrado).
3. A página mistura três camadas (visão de trabalho, painel financeiro com separadores, operação de caixa dentro de um separador). O «O que precisa da sua atenção» existe duas vezes (pedidos e financeiro).
4. KPIs com variação só na faturação e nas despesas; recebimentos/pagamentos têm valores do período anterior na API (`previous_received_cents`, `previous_paid_cents`) que não são mostrados.

## 6. Matriz de correspondência (atual → proposta)

Decisões de 08/11/2026: manter a marca IAZELA; nova versão **lado a lado** com a atual; das novidades, só o gráfico
**Vendas vs compras** (sem margem a partir do stock no modo ERP, sem datas da obrigação de IVA, sem mini-gráficos).
Implementado como descrito em [DASHBOARD.md](DASHBOARD.md) («Versão nova»). Regressão: `--compare antes-2026-11-08
depois-serie-compras` sem diferenças nos valores existentes (só acrescentados `expenses.points` e `expenses.unit`).

| Atual | Proposta (referência aprovada) |
|---|---|
| Filtros (atalhos, Desde/Até, Comparar, Atualizar, Personalizar) | Barra do cabeçalho: seletor de período (atalhos + intervalo), comparação, Atualizar, Personalizar — mesmos valores enviados à API |
| Separadores das 7 áreas | Separadores no topo (Resumo do negócio, Evolução, Artigos mais vendidos, O que precisa da sua atenção com contador, Ações rápidas, Operação de caixa, Encomendas e abastecimento) |
| KPI Faturação | Cartão **Vendas** (valor, variação, base com/sem IVA indicada, mini-gráfico da série já calculada) |
| KPI Despesas (total do bloco despesas) | Cartão **Compras e despesas** (total por rubrica, sem IVA dedutível, variação) — distinto de pagamentos |
| KPI Margem bruta | Cartão **Margem bruta** (ou «Indisponível» com cobertura de custo) |
| KPI Saldo disponível conhecido | Cartão **Tesouraria** (saldo conhecido, avisos de incompleto/desatualizado, TPA à parte) |
| KPI Recebimentos e pagamentos | Mantido no Resumo (e variação com os valores do período anterior já calculados) |
| KPI A pagar vencido/30 dias | Mantido no Resumo |
| KPI Previsão de IVA | Cartão **Previsão de IVA** com detalhe (ver §7) e **Ver mapa de IVA** |
| Faturação por período + comparação | Cartão «Faturação por mês (sem IVA)» com **Ver tabela** |
| Recebimentos e pagamentos (gráfico) | «Recebimentos e pagamentos (com IVA)» com **Ver tabela** e nota sobre transferências e TPA |
| Saldo previsto 30 dias | «Saldo previsto · próximos 30 dias» com **Ver tabela** e mínimo |
| A receber/a pagar por vencimento | Mesmo gráfico com **Ver tabela** |
| Despesas por rubrica + Ver documentos por rubrica | Lista com barras, **Ver tabela**, **Ver documentos por rubrica**, total e comparação |
| Vendas por família (margem) | «Vendas por família» (anel ou barras) com **Ver tabela**; só quando há linhas de venda por artigo (SAF-T ou Wintouch) |
| Top 10 artigos | Separador «Artigos mais vendidos» (tabela igual) |
| O que precisa da sua atenção (financeiro) + atenção dos pedidos | Cartão **Alertas e tarefas** (resumo com contadores e ligação) + separador completo com facto, impacto e ação |
| Ações rápidas | Separador e atalhos (mesmas ações e permissões) |
| Operação de caixa + Últimos movimentos | Separador «Operação de caixa» (igual) |
| Encomendas e abastecimento + visão de trabalho dos pedidos | Separador «Encomendas e abastecimento» (KPIs de pedidos, sinais, evolução) |
| Personalizar (ordem e visibilidade das áreas) | Personalizar áreas **e** cartões do resumo (ordem/visibilidade), mesma chave de preferência + migração |

**Novo na referência, sem equivalente hoje:** gráfico «Evolução vendas vs compras» (implementado: série de compras por
período no servidor, a partir do bloco de despesas) e mini-gráficos nos cartões (não aprovados).

## 7. Previsão de IVA — apresentação proposta

Com os dados existentes: período (o filtrado; **periodicidade configurada** mensal/trimestral indicada), IVA
liquidado, IVA dedutível estimado (por rubrica), IVA suportado não dedutível (diferença), saldo estimado a pagar ou
a recuperar, documentos considerados (vendas/compras), origem dos valores, e as limitações já declaradas.
A acrescentar, sem alterar a fórmula: **período fiscal** derivado da periodicidade (mês ou trimestre que contém o
fim do filtro), **data-limite da declaração e do pagamento** (regra do CIVA a confirmar antes de implementar),
**provisória** quando há documentos sem rubrica ou sem IVA discriminado, e a nota «estimativa de gestão — não é o
apuramento da declaração periódica». Regularizações e créditos de períodos anteriores: não existem nos dados;
aparecem como «não incluídos» (campo manual, se aprovado).
