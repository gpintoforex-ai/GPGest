# Mapas de gestão: custos, proveitos e IVA

Em **Contabilidade → Mapas de gestão** (gestores e administradores) há dois mapas indicativos, por período e loja. São mapas de gestão: não substituem a contabilidade nem a declaração periódica de IVA.

## Custos e proveitos

- **Proveitos:** faturação do SAF-T de vendas, líquida de IVA, pela data do documento (documentos normais FT, FS, FR, ND; notas de crédito subtraem). No modo integrado, sem SAF-T importado, são as vendas Wintouch sincronizadas, líquidas de IVA (anulados excluídos; devoluções subtraem). É a mesma faturação do cartão de vendas do dashboard. Documentos do ERP sem base tributável ficam fora e são assinalados numa nota.
- **Custos:** todos os documentos de compra pela data do documento (e-Fatura, compras confirmadas em Compras e registos manuais), uma vez por documento, mais as compras do ERP sem documento correspondente. Inclui faturas do e-Fatura ainda não lançadas. A rubrica vem, por esta ordem, das linhas da compra lançada, da rubrica do documento ERP ligado, da repartição confirmada na [classificação de rubricas](MOTOR-CENTRAL-RELACOES.md#classificação-inteligente-de-rubricas-passo-2) ou da rubrica do fornecedor; base e IVA são repartidos na mesma proporção.
- **Custos sem documento:** os pagamentos e comissões do banco registados na reconciliação («Resolver» ou regras por descrição) com rubrica e marcados como «conta como custo», pela data do movimento e sem IVA. Registos corrigidos ficam de fora. Ver [RECONCILIACAO-BANCARIA.md](RECONCILIACAO-BANCARIA.md).
- **Rubrica de cada custo:** a das linhas da compra lançada; senão, a definida no documento ERP; senão, a do fornecedor; sem nenhuma, *Por classificar*.
- **Valor do custo:** no regime normal, base tributável mais o IVA não dedutível estimado; nos restantes regimes, o total (o IVA também é custo).
- **Natureza da rubrica:** só entram as rubricas de custos operacionais ou de compras e investimentos ([natureza das rubricas](NATUREZA-DAS-RUBRICAS.md)). Rubricas de obrigações fiscais (por exemplo, pagamentos de IVA ao Estado) ou movimentos financeiros fora destas análises ficam de fora e são listadas numa nota. O IVA suportado e dedutível continua a contar todos os documentos.
- Agrupamento por mês ou trimestre, com total de custos e margem (proveitos − custos). Não inclui amortizações, acréscimos e diferimentos, nem custos pagos sem registo com rubrica (por exemplo, vencimentos pagos sem passar pela reconciliação).

### Proveitos, custos e margens por rubrica (apresentação)

O separador **Proveitos, custos e margens** mostra os mesmos valores com uma apresentação de gestão. A modernização não
mudou nenhum cálculo: os totais por rubrica, os custos, os proveitos e a margem são os de antes (verificado em
`backend/tests/test_management_costs.py`).

- **Indicadores:** proveitos, custos, margem de gestão estimada (com a margem % sobre os proveitos, quando positivos),
  compras de mercadorias (rubricas de compras sem natureza «Investimento») e custos fixos (rubricas marcadas como
  **Custo fixo** em Tabelas → Rubricas). Cada cartão tem a fórmula (ícone ⓘ) e a variação face ao mesmo período do ano
  anterior, quando há dados comparáveis. Clicar num cartão abre os movimentos que compõem o valor; os proveitos abrem
  as vendas do período. A margem é indicativa: não é o resultado contabilístico nem uma margem de tesouraria.
- **Tabela mensal:** todos os meses (ou trimestres) do período, mesmo sem movimentos. Proveitos; grupos **Compras de
  mercadorias e investimentos** e **Despesas operacionais** com subtotal (recolher/expandir); total de custos; margem
  e margem %. A primeira coluna, o cabeçalho e o total ficam fixos durante o deslocamento. Os subtotais não voltam a
  somar no total.
- **Fora dos custos (informativo):** pagamentos ao Estado pelo valor pago (a mesma vista «Obrigações fiscais» do
  Dashboard), as rubricas de obrigações fiscais e de movimentos financeiros, IVA liquidado nas vendas, IVA suportado,
  dedutível estimado e não dedutível. Nada disto soma aos custos nem à margem.
- **Filtros:** pesquisa por rubrica, ano e intervalo de meses (ou as datas do topo), tipo de rubrica (compras,
  despesas operacionais, fixos, variáveis, por classificar), rubricas sem movimentos, comparação com o ano anterior e
  com o orçamento, e **Limpar filtros**. A empresa e o estabelecimento são os do topo da aplicação. Com um filtro
  ativo, a tabela mostra o total das rubricas filtradas e o cartão de custos indica o total geral.
- **Movimentos de cada valor:** clicar num valor (rubrica e mês, ou o total anual) abre os
  [movimentos da rubrica](MOVIMENTOS-DA-RUBRICA.md): data, documento, entidade, origem, base, IVA, valor, rubrica,
  estado de reconciliação ou pagamento, consulta do documento e dos originais, correção da rubrica (individual ou em
  lote, com pré-visualização e motivo), observações, histórico e associação a documentos. A correção só muda a
  classificação de gestão; os totais, o orçamento e os gráficos recalculam. A auditoria regista a origem
  `mapas_de_gestao`.
- **Orçamento:** com um orçamento do ano (último aprovado; senão o rascunho), cada rubrica orçamentada mostra o valor
  orçamentado e o desvio por mês e no total. No total de custos, o desvio só considera as rubricas orçamentadas.
- **Alertas (sinais para rever, nunca erros):** valor acima da média dos restantes períodos, subida face ao período
  anterior, aumento face ao mesmo período do ano anterior, possíveis duplicados (a mesma entidade e o mesmo valor em
  poucos dias) e despesas sem rubrica. Os limites (percentagens, valor mínimo da diferença e janela dos duplicados)
  configuram-se em **Limites dos alertas** (só o administrador altera; fica na auditoria como
  `limites_alertas_mapas`). Clicar num alerta abre os movimentos que o justificam.
- **Gráficos** (botão **Tabela | Gráficos**): proveitos e custos por período, margem de gestão estimada (com o ano
  anterior), custos atuais e do ano anterior, despesas por rubrica e peso de cada rubrica. Colunas e barras abrem os
  movimentos; cada gráfico tem a vista em tabela.
- **Exportação:** **Exportar Excel** (CSV com «;», a tabela filtrada com subtotais, totais, margem e a parte
  informativa), **PDF / Imprimir** (só o relatório, com cabeçalhos e totais) e **Exportar movimentos** no painel de
  cada rubrica. Os valores exportados são os apresentados.
- **Segurança Social e impostos:** um encargo pago ao banco com uma rubrica de custo conta uma vez, pela data do
  movimento; se a rubrica tiver a natureza «Obrigação fiscal», fica nos pagamentos ao Estado e fora dos custos. A
  aplicação não separa encargos da entidade patronal e retenções dos trabalhadores dentro do mesmo pagamento.

API: `GET /api/management/costs-revenues?start&end&group&store_id&compare=true` (acrescenta `subtotals`, `kpis`,
`outside`, `state_payments`, `sales_vat`, `previous`, `budget`, `alerts`, `empty_rubrics`, mantendo os campos
anteriores), `GET/PUT /api/management/alert-settings`. Migração 0092 (`categories.fixed_cost`, `map_alert_settings`).
Testes: `backend/tests/test_management_costs.py`, `scripts/test-management-costs-ui.py`.

## Mapa de IVA

- Segue a periodicidade do **regime de IVA** configurado (mensal ou trimestral). Na isenção do artigo 53.º não há mapa.
- **IVA liquidado:** IVA do SAF-T de vendas (notas de crédito subtraem).
- **IVA suportado:** IVA dos documentos de compra, incluindo os ainda não lançados.
- **IVA dedutível (estimado):** IVA suportado × percentagem de dedução da rubrica (Tabelas → Rubricas → «IVA dedutível estimado nas compras»; vazio = 100%). Exemplos: 50% no gasóleo de viaturas ligeiras, 0% em despesas não dedutíveis. A dedutibilidade real é confirmada pelo contabilista.
- **Saldo estimado:** liquidado − dedutível, como *a entregar* ou *a recuperar*. No regime dos pequenos retalhistas é apenas uma referência (regras próprias do art. 60.º do CIVA).
- O detalhe por rubrica mostra o IVA suportado e o dedutível estimado de cada rubrica.

## Relação com os outros ecrãs

- **Despesas por rubrica** conta apenas compras lançadas (ERP e Compras) e indica quantas faturas do e-Fatura estão por lançar.
- As faturas do e-Fatura lançam-se na **Conferência documental → Rever propostas** (propostas automáticas, ver [assistente e-Fatura](ASSISTENTE-EFATURA.md)).
