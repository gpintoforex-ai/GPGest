import { currentMonthRange } from "./components/DatePresets";
import Info from "./components/InfoIcon";
import { PageHeader, TaskWorkspace, updateLocationParam } from "./components/WorkspaceUI";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, OctagonAlert, RefreshCw, Settings2, CircleDot, ChevronRight, Layers, Percent, Receipt, ShoppingCart, Wallet } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice } from "./components/InteractionUI";
import DatePresets from "./components/DatePresets";
import { BarList, ChartFrame, ColumnsChart, ForecastChart, Legend } from "./DashboardCharts";
import ExpenseDrilldown from "./ExpenseDrilldown";
import RubricMovements from "./RubricMovements";
import { DrilldownTrigger, type DrilldownStep } from "./Drilldown";
import { formatEuro } from "./utils/format";
import { handOffPeriod } from "./utils/periodHandoff";
import "./ManagementDashboard.css";
import { OrderCycleSignals } from "./OrderAnalytics";
import { MarginArticles, MarginPanel, MarginSummary, marginTone } from "./MarginInsights";

type Row = Record<string, any>;
type SectionKey = "summary" | "evolution" | "margins" | "articles" | "attention" | "actions" | "operation" | "orders";
type ChartKey = "sales" | "flows" | "forecast" | "due" | "expenses" | "families";
/** Cartões da Evolução na versão nova: os gráficos atuais mais Vendas vs compras, IVA e alertas. */
type CardKey = ChartKey | "versus" | "vat" | "alerts";
const CARDS: [CardKey, string][] = [["sales", "Faturação"], ["flows", "Recebimentos e pagamentos"], ["forecast", "Saldo previsto (30 dias)"],
  ["due", "A receber e a pagar por vencimento"], ["expenses", "Custos, compras e obrigações fiscais"], ["vat", "Previsão de IVA"], ["versus", "Evolução de vendas e custos operacionais"],
  ["families", "Vendas por família"], ["alerts", "Alertas e tarefas"]];
export type DashboardVersion = "atual" | "nova";

/** Versão do dashboard escolhida pelo utilizador neste navegador (a nova fica lado a lado até ser validada). */
export function useDashboardVersion(scope: string) {
  const key = `iazela-dashboard-versao:${scope}`;
  const read = (): DashboardVersion => { try { return localStorage.getItem(key) === "nova" ? "nova" : "atual"; } catch { return "atual"; } };
  const [version, setVersion] = useState<DashboardVersion>(read);
  const save = (next: DashboardVersion) => { setVersion(next); try { localStorage.setItem(key, next); } catch { /* só nesta sessão */ } };
  return [version, save] as const;
}
const SECTIONS: [SectionKey, string][] = [["summary", "Resumo do negócio"], ["evolution", "Evolução"], ["margins", "Margens e custos"], ["articles", "Artigos mais vendidos"], ["attention", "O que precisa da sua atenção"], ["actions", "Ações rápidas"], ["operation", "Operação de caixa"], ["orders", "Encomendas e abastecimento"]];
const DUE: [string, string][] = [["vencido", "Vencido"], ["7_dias", "Próx. 7 dias"], ["30_dias", "8–30 dias"], ["mais_30", "> 30 dias"], ["sem_data", "Sem data"]];
const SEVERITY: Record<string, [string, typeof AlertTriangle]> = { critical: ["Crítico", OctagonAlert], warning: ["Atenção", AlertTriangle], info: ["Informação", CircleDot] };
const lisbonToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const day = (value: string) => value?.slice(0, 10).split("-").reverse().join("/") || "—";
const when = (value: string) => value ? new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Lisbon" }).format(new Date(value)) : "—";

type Prefs = { order: SectionKey[]; hidden: SectionKey[]; cards?: CardKey[]; hiddenCards?: CardKey[] };

function usePreferences(scope: string) {
  const key = `iazela-dashboard:${scope}`;
  const read = () => { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } };
  const [prefs, setPrefs] = useState<Prefs>(() => read() || { order: SECTIONS.map(([k]) => k), hidden: [] });
  const save = (next: Prefs) => { setPrefs(next); try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* preferência só nesta sessão */ } };
  return [prefs, save] as const;
}

function MetaInfo({ meta }: { meta?: Row }) {
  if (!meta) return null;
  return <details className="dash-meta" onClick={e => e.stopPropagation()}><summary aria-label="Como é calculado"><Info text={`${meta.definition}. ${meta.formula}`} size={15} aria-hidden="true" /></summary>
    <div className="dash-meta-body" role="note">
      <p><b>O que significa:</b> {meta.definition}</p><p><b>Fórmula:</b> {meta.formula}</p><p><b>Origem:</b> {meta.source}</p>
      <p><b>Última atualização:</b> {meta.updated_at ? when(meta.updated_at) : "não indicada pela origem"}</p>{meta.limitations && <p><b>Limitações:</b> {meta.limitations}</p>}
    </div></details>;
}

function Delta({ value, inverse }: { value: number | null | undefined; inverse?: boolean }) {
  if (value === null || value === undefined) return <small className="dash-delta">Sem comparação</small>;
  const good = inverse ? value < 0 : value > 0;
  const Icon = value > 0 ? ArrowUp : ArrowDown;
  return <small className={`dash-delta ${value === 0 ? "" : good ? "is-good" : "is-bad"}`}><Icon size={13} aria-hidden="true" />{value > 0 ? "+" : ""}{value.toFixed(1).replace(".", ",")}% vs comparação</small>;
}

function Kpi({ label, children, meta, onOpen, state }: { label: string; children: ReactNode; meta?: Row; onOpen?: () => void; state?: string }) {
  return <article className={`dash-kpi${state ? " is-" + state : ""}`}>
    <div className="dash-kpi-head"><span>{label}</span><MetaInfo meta={meta} /></div>
    {onOpen ? <ActionButton type="button" className="dash-kpi-body" onClick={onOpen}>{children}</ActionButton> : <div className="dash-kpi-body">{children}</div>}
  </article>;
}

function BlockState({ block, children }: { block?: Row; children: ReactNode }) {
  if (!block) return <p className="dash-loading" role="status">A calcular…</p>;
  if (block.status === "error") return <Notice tone="warning">{block.message}</Notice>;
  if (block.status === "restricted") return <p className="dash-muted">{block.message}</p>;
  if (block.status === "empty") return <p className="dash-muted">{block.message || "Sem dados neste período."}</p>;
  return <>{children}</>;
}

/** Dashboard de gestão: resumo, evolução, atenção e ações rápidas, com os mesmos filtros. */
export default function ManagementDashboard({ store, scopeKey, companyName, mode, role, navigate, onAction, operation, variant = "atual", onVersion, work }: {
  store: string; scopeKey: string; companyName: string; mode: string; role: string; navigate: (page: string, params?: Record<string,string>) => void; onAction: (kind: string) => void; operation?: ReactNode;
  /** «nova»: layout modernizado com os mesmos dados; «atual»: o painel de sempre. */
  variant?: DashboardVersion; onVersion?: (variant: DashboardVersion) => void;
  /** Visão de trabalho (pedidos e trabalho por área), mostrada na versão nova em «Encomendas e abastecimento». */
  work?: ReactNode;
}) {
  const today = lisbonToday();
  const [start, setStart] = useState(today.slice(0, 8) + "01"), [end, setEnd] = useState(currentMonthRange().to);
  const [compare, setCompare] = useState("previous");
  const dataKey = JSON.stringify([scopeKey, role, mode, store, start, end, compare]);
  const [snapshot, setSnapshot] = useState<{ key: string; data: Row } | null>(null);
  const data = snapshot?.key === dataKey ? snapshot.data : null;
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [customizing, setCustomizing] = useState(false), [version, setVersion] = useState(0);
  const [expenseDrill, setExpenseDrill] = useState<{ key: string; steps: DrilldownStep[] } | null>(null);
  // Movimentos da rubrica (consulta e correção): administrador e gestor.
  const [movements, setMovements] = useState<{ key: string; category: string | null; name: string; view: Row } | null>(null);
  const [byParent, setByParent] = useState(false);
  // Vista das saídas: custos operacionais, compras e investimentos, obrigações fiscais ou saídas de tesouraria.
  const [expenseView, setExpenseView] = useState("operating");
  const [marginArticles, setMarginArticles] = useState(false);
  const [prefs, savePrefs] = usePreferences(scopeKey);
  const valid = !!start && !!end && start <= end;
  const request = useRef(0);
  useEffect(() => {
    const id = ++request.current;
    let active = true;
    setError("");
    if (!valid) { setLoading(false); return; }
    setLoading(true); setError("");
    api(`/dashboard/overview?${new URLSearchParams({ start, end, compare, store_id: store })}`, undefined, { timeoutMs: 90_000 })
      .then(result => {
        if (!result.permissions || !result.period || !result.comparison || !Array.isArray(result.attention))
          throw new Error("O resumo financeiro está indisponível nesta versão do servidor.");
        if (active && id === request.current) setSnapshot({ key: dataKey, data: result });
      })
      .catch(e => { if (active && id === request.current) setError((e as Error).message); })
      .finally(() => { if (active && id === request.current) setLoading(false); });
    return () => { active = false; };
  }, [dataKey, valid, version]);
  const open = (page: string, withPeriod = true) => { if (withPeriod) handOffPeriod(page, start, end); navigate(page); };
  // Relatório de custos e proveitos (Mapas de gestão) com o período do painel.
  const openCostsMap = () => { handOffPeriod("Mapas de gestão", start, end); navigate("Mapas de gestão", { "area.mapas": "costs" }); };
  const unavailable = error ? { status: "error", message: "Consulta indisponível. Tente atualizar o painel." } : !valid ? { status: "error", message: "Indique um período válido." } : undefined;
  const sales = data?.sales || unavailable, treasury = data?.treasury || unavailable, expenses = data?.expenses || unavailable, margin = data?.margin || unavailable;
  const vat = data?.vat || unavailable || (data ? { status: "empty", message: "Atualize o servidor para disponibilizar a previsão de IVA." } : undefined);
  const topArticles = data?.top_articles || unavailable || (data ? { status: "empty", message: "Atualize o servidor para disponibilizar o top de artigos." } : undefined);
  const expenseViews: Row[] = expenses?.views || [];
  const shownView: Row | undefined = expenseViews.find(v => v.key === expenseView) || (expenses?.rows ? { key: "operating", label: "Custos e despesas operacionais", basis: expenses.vat_basis, ...expenses } : undefined);
  const canCorrect = role === "administrador" || role === "gestor";
  const explainExpenses = (row?: Row, view: Row | undefined = expenseViews.find(v => v.key === "operating") || shownView) => {
    if (row && canCorrect) {
      setMovements({ key: dataKey, category: row.category_id === "outras" ? "" : row.category_id ?? null, name: row.name, view: view || {} });
      return;
    }
    const base: Record<string, string> = view && view.key !== "operating" ? { view: view.key } : {};
    const steps: DrilldownStep[] = [{ label: view?.label || "Custos e despesas operacionais", params: base, expected: view?.total_cents ?? expenses?.total_cents }];
    if (row) steps.push({ label: row.name, params: { ...base, category: row.category_id ?? "" }, expected: row.amount_cents });
    setExpenseDrill({ key: dataKey, steps });
  };
  const fiscalVat = shownView?.key === "fiscal" ? shownView.vat : null;
  const shownRows: Row[] = (byParent && shownView?.parent_rows) || shownView?.rows || [];
  const expenseDialog = <>
    {movements?.key === dataKey && <RubricMovements context={{ company: companyName, store, start, end, view: movements.view.key || "operating", viewLabel: movements.view.label }}
      category={movements.category} categoryName={movements.name} onClose={() => setMovements(null)}
      onChanged={() => { setVersion(v => v + 1); window.dispatchEvent(new Event("iazela-expenses-changed")); }}
      onConsult={row => {
        const base: Record<string, string> = movements.view.key && movements.view.key !== "operating" ? { view: movements.view.key } : {};
        setExpenseDrill({ key: dataKey, steps: [{ label: movements.view.label || "Custos e despesas operacionais", params: base },
          { label: row.label, params: { ...base, document_id: row.id }, expected: row.amount_cents }] });
      }}
      onReconcile={row => {
        // A navegação só é permitida sem janelas abertas: fecha o painel e navega a seguir.
        setMovements(null);
        window.setTimeout(() => navigate("Reconciliação automática", { pesquisa: row.entity || row.label }), 0);
      }} />}
    {expenseDrill?.key === dataKey && <ExpenseDrilldown context={{ company: companyName, store, start, end }}
      initial={expenseDrill.steps} onClose={() => setExpenseDrill(null)} />}
  </>;
  const salesPage = data?.mode === "local" ? "Faturação e recebimentos" : "Movimentos reais";

  // Gráficos da Evolução: os mesmos blocos nas duas versões (a nova acrescenta cartões, sem alterar estes).
  const charts: Record<ChartKey, ReactNode> = {
    sales: data?.permissions?.finance !== false && <BlockState block={sales}>{sales?.points && <ChartFrame title={`Faturação por ${sales.unit === "day" ? "dia" : sales.unit === "week" ? "semana" : "mês"} (${sales.vat_basis})`}
        legend={<Legend series={[{ key: "c", label: data?.period?.label, form: "bar" }, { key: "p", label: `Comparação: ${data?.comparison?.method}`, form: "line", dashed: true }]} />}
        table={{ columns: ["Período", "Atual", "Comparação"], rows: sales.points.map((p: Row) => [p.label, formatEuro(p.current_cents), p.previous_cents === null ? "—" : formatEuro(p.previous_cents)]) }}
        footnote={`Comparação: ${day(data?.comparison?.start)} a ${day(data?.comparison?.end)} (${data?.comparison?.method}).`}>
        <ColumnsChart ariaLabel="Faturação do período e do período de comparação" labels={sales.points.map((p: Row) => p.label)}
          series={[{ key: "c", label: "Atual", form: "bar" }, { key: "p", label: "Comparação", form: "line", dashed: true }]}
          values={[sales.points.map((p: Row) => p.current_cents), sales.points.map((p: Row) => p.previous_cents)]} />
      </ChartFrame>}</BlockState>,
    flows: data?.permissions?.finance !== false && <BlockState block={treasury}>{treasury?.flows && <ChartFrame title="Recebimentos e pagamentos (com IVA)"
        legend={<Legend series={[{ key: "in", label: "Recebimentos", form: "bar" }, { key: "out", label: "Pagamentos", form: "bar" }]} />}
        table={{ columns: ["Período", "Recebimentos", "Pagamentos"], rows: treasury.flows.map((f: Row) => [f.label, formatEuro(f.in_cents), formatEuro(f.out_cents)]) }}
        footnote="Sem transferências entre contas próprias nem liquidações de TPA (já contadas quando o cliente pagou).">
        <ColumnsChart ariaLabel="Recebimentos e pagamentos por período" labels={treasury.flows.map((f: Row) => f.label)}
          series={[{ key: "in", label: "Recebimentos", form: "bar" }, { key: "out", label: "Pagamentos", form: "bar" }]}
          values={[treasury.flows.map((f: Row) => f.in_cents), treasury.flows.map((f: Row) => f.out_cents)]} />
      </ChartFrame>}</BlockState>,
    forecast: data?.permissions?.finance !== false && <BlockState block={treasury}>{treasury?.forecast && (treasury.forecast.available ? <ChartFrame title="Saldo previsto · próximos 30 dias (EUR)"
        legend={<Legend series={[{ key: "f", label: "Previsto (a partir do saldo atual conhecido)", form: "line", dashed: true }]} />}
        table={{ columns: ["Data", "Entradas", "Saídas", "Saldo"], rows: treasury.forecast.points.map((p: Row) => [day(p.date), formatEuro(p.in_cents), formatEuro(p.out_cents), formatEuro(p.closing_cents)]) }}
        footnote={treasury.forecast.incomplete ? "Saldo inicial incompleto: há contas sem saldo conhecido." : `Mínimo: ${formatEuro(treasury.forecast.minimum_cents)} em ${day(treasury.forecast.minimum_date)}.`}>
        <ForecastChart ariaLabel="Saldo previsto nos próximos 30 dias" start={treasury.forecast.initial_cents} reserve={treasury.forecast.reserve_cents}
          points={treasury.forecast.points.map((p: Row) => ({ label: day(p.date).slice(0, 5), value: p.closing_cents }))} />
      </ChartFrame> : <p className="dash-muted">Previsão indisponível: sem saldos ou movimentos previstos.</p>)}</BlockState>,
    due: data?.permissions?.finance !== false && <BlockState block={treasury}>{treasury?.due && <ChartFrame title="A receber e a pagar por vencimento (EUR)"
        legend={<Legend series={[{ key: "r", label: "A receber", form: "bar" }, { key: "p", label: "A pagar", form: "bar" }]} />}
        table={{ columns: ["Vencimento", "A receber", "A pagar"], rows: DUE.map(([k, label]) => [label, `${formatEuro(treasury.due.receivable[k].amount_cents)} (${treasury.due.receivable[k].count})`, `${formatEuro(treasury.due.payable[k].amount_cents)} (${treasury.due.payable[k].count})`]) }}
        footnote="Saldos por liquidar: uma fatura registada não conta como paga.">
        <ColumnsChart ariaLabel="Valores a receber e a pagar por vencimento" labels={DUE.map(([, label]) => label)}
          series={[{ key: "r", label: "A receber", form: "bar" }, { key: "p", label: "A pagar", form: "bar" }]}
          values={[DUE.map(([k]) => treasury.due.receivable[k].amount_cents), DUE.map(([k]) => treasury.due.payable[k].amount_cents)]} />
      </ChartFrame>}</BlockState>,
    expenses: data?.permissions?.costs && <BlockState block={expenseViews.length ? { status: "ok" } : expenses}>{shownView && <ChartFrame title={`${shownView.label} (${shownView.basis})`}
        table={{ columns: ["Rubrica", "Período", "Comparação"], rows: shownRows.map((r: Row) => [<DrilldownTrigger onOpen={() => explainExpenses(r, shownView)}>{r.name}</DrilldownTrigger>, formatEuro(r.amount_cents), formatEuro(r.previous_cents)]) }}
        footnote={<><DrilldownTrigger onOpen={() => explainExpenses(undefined, shownView)}>Total {formatEuro(shownView.total_cents)}</DrilldownTrigger> · <Delta value={shownView.change_percent} inverse /> <MetaInfo meta={expenses.meta} /></>}>
        {expenseViews.length > 0 && <div className="dash-views" role="group" aria-label="Vista das saídas">
          {expenseViews.map(v => <button key={v.key} type="button" aria-pressed={v.key === shownView.key} onClick={() => setExpenseView(v.key)}>
            {v.label}<span>{formatEuro(v.total_cents)}</span></button>)}
        </div>}
        {shownView.origin && <p className="dash-muted dash-origin"><strong>Origem:</strong> {shownView.origin}</p>}
        {!!expenses?.config_notes?.length && <div className="dash-config-notes" role="note" aria-label="Mudanças recentes na classificação">
          <strong>Mudanças recentes na classificação</strong>
          <ul>{expenses.config_notes.map((n: Row, i: number) => <li key={i}>{n.text}</li>)}</ul>
          {data?.permissions?.costs && <ActionButton type="button" className="dash-link" onClick={() => navigate("Rubricas", { "area.rubricas": "natureza" })}>Ver histórico</ActionButton>}
        </div>}
        {expenses?.has_parents && <ActionButton type="button" className="dash-link" aria-pressed={byParent} onClick={() => setByParent(v => !v)}>
          {byParent ? "Mostrar sub-rubricas" : "Agrupar por rubrica-mãe"}</ActionButton>}
        {shownRows.length ? <BarList ariaLabel={shownView.label} rows={shownRows.map((r: Row) => ({ label: r.name, value: r.amount_cents, onOpen: () => explainExpenses(r, shownView), note: r.previous_cents ? `comparação ${formatEuro(r.previous_cents)}` : undefined }))} />
          : <p className="dash-muted">Sem valores nesta vista no período.{shownView.key !== "operating" && expenses?.rubrics_without_nature ? " As rubricas por definir entram nos custos operacionais." : ""}</p>}
        {fiscalVat && <div className="dash-vat-block"><dl className="dash-vat-detail" aria-label="IVA do período">
          {([["IVA liquidado nas vendas", fiscalVat.output_cents == null ? "Indisponível" : formatEuro(fiscalVat.output_cents)],
            ["IVA suportado nas compras", formatEuro(fiscalVat.input_cents)], ["IVA dedutível estimado", formatEuro(fiscalVat.deductible_cents)],
            ["IVA não dedutível (custo)", formatEuro(fiscalVat.non_deductible_cents)], ["Pagamentos ao Estado", formatEuro(fiscalVat.payments_cents)]] as [string, string][])
            .map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl><p className="dash-muted">{fiscalVat.note}</p></div>}
        <div className="dash-view-links">
          <ActionButton type="button" className="dash-link" onClick={() => open("Despesas por rubrica")}>Ver documentos por rubrica</ActionButton>
          {!!expenses?.rubrics_without_nature && data?.permissions?.costs && <ActionButton type="button" className="dash-link" onClick={() => navigate("Rubricas", { "area.rubricas": "natureza" })}>
            Natureza das rubricas ({expenses.rubrics_without_nature} por definir)</ActionButton>}
        </div>
      </ChartFrame>}</BlockState>,
    families: data?.permissions?.costs && <BlockState block={margin}>{margin?.rows && <ChartFrame title="Vendas por família de artigo (sem IVA)"
        table={{ columns: ["Família", "Vendas", "Artigos", "Custo conhecido", "Margem"], rows: margin.rows.map((r: Row) => [r.name, formatEuro(r.sales_cents), r.articles, r.cost_coverage_percent == null ? "—" : `${r.cost_coverage_percent}%`, r.margin_cents == null ? "Indisponível" : formatEuro(r.margin_cents)]) }}
        footnote={<>Custo conhecido em {String(margin.cost_coverage_percent ?? 0).replace(".", ",")}% das vendas{margin.cost_restricted ? " (sem permissão para ver custos)" : ""}. <MetaInfo meta={margin.meta} /></>}>
        <BarList ariaLabel="Vendas por família de artigo" rows={margin.rows.map((r: Row) => ({ label: r.name, value: r.sales_cents, note: r.margin_cents != null ? `margem ${formatEuro(r.margin_cents)}` : r.cost_coverage_percent ? `custo conhecido ${r.cost_coverage_percent}%` : "custo em falta" }))} />
      </ChartFrame>}</BlockState>,
  };
  const sections: Record<SectionKey, ReactNode> = {
    orders: ["administrador", "gestor"].includes(role) ? <OrderCycleSignals store={store} start={start} end={end} revision={version} navigate={navigate} /> : <p>Os indicadores de compras requerem autorização para consultar custos.</p>,
    operation,
    articles: <section className="panel"><h2>Top 10 de artigos vendidos</h2>
      <p className="dash-muted">Ordenado por quantidade vendida, descontando devoluções. Respeita o período e a loja selecionados.</p>
      {!!topArticles?.incomplete_documents && <Notice tone="warning">Top parcial: {topArticles.incomplete_documents} documento(s) sem detalhe completo de artigos. Repita a importação de vendas com o detalhe de stock para o período selecionado.</Notice>}
      <BlockState block={topArticles}>{topArticles?.rows && <div className="table-wrap"><table>
        <thead><tr><th>Posição</th><th>Código</th><th>Artigo</th><th className="numeric">Quantidade líquida</th><th className="numeric">Vendas sem IVA</th></tr></thead>
        <tbody>{topArticles.rows.map((r: Row, index: number) => <tr key={r.id || r.code || r.name}><td>{index + 1}</td><td>{r.code || "—"}</td><td>{r.name}</td><td className="numeric">{Number(r.quantity).toLocaleString("pt-PT", { maximumFractionDigits: 4 })}</td><td className="numeric">{formatEuro(r.sales_cents)}</td></tr>)}</tbody>
      </table></div>}</BlockState>
      <MetaInfo meta={topArticles?.meta} />
    </section>,
    summary: <div className="dash-kpis">
      {data?.permissions?.finance === false && <Notice>Os indicadores financeiros não estão disponíveis para o seu perfil.</Notice>}
      {data?.permissions?.finance !== false && <Kpi label={`Faturação${sales?.vat_basis ? ` (${sales.vat_basis})` : ""}`} meta={sales?.meta} onOpen={() => open(salesPage)}>
        <BlockState block={sales}><strong>{formatEuro(sales?.total_cents)}</strong><Delta value={sales?.change_percent} /><small>{sales?.documents} documento(s)</small></BlockState></Kpi>}
      {data?.permissions?.costs && expenses?.status === "ok" && <Kpi label={`Custos operacionais (${expenses.vat_basis})`} meta={expenses.meta} onOpen={() => explainExpenses()}>
        <strong>{formatEuro(expenses.total_cents)}</strong><small>Explicar valor por rubrica</small></Kpi>}
      {data?.permissions?.costs && <Kpi label="Previsão de IVA" meta={vat?.meta}>
        <BlockState block={vat}><strong>{formatEuro(Math.abs(vat?.balance_cents || 0))}</strong>
          <small>{vat?.balance_cents > 0 ? "A pagar estimado" : vat?.balance_cents < 0 ? "Crédito estimado" : "Saldo estimado nulo"} · período selecionado</small>
          <small>Liquidado: {formatEuro(vat?.output_cents)} · Dedutível: {formatEuro(vat?.deductible_cents)}</small>
          {vat?.purchase_documents !== undefined && <small>IVA suportado: {formatEuro(vat?.input_cents)} em {vat.purchase_documents} documento(s) de compra (pela data do documento)</small>}
          <ActionButton type="button" className="dash-link" onClick={() => { handOffPeriod("Mapas de gestão", start, end); navigate("Mapas de gestão", { "area.mapas": "vat" }); }}>Ver mapa de IVA</ActionButton>
        </BlockState></Kpi>}
      {data?.permissions?.finance !== false && <Kpi label="Saldo disponível conhecido" meta={treasury?.meta?.balance} onOpen={() => open("Previsão de liquidez", false)} state={treasury?.balance_complete === false || treasury?.accounts?.some((a: Row) => a.stale) ? "warn" : ""}>
        <BlockState block={treasury}>{treasury?.balance_cents === null ? <strong>Sem dados</strong> : <strong>{formatEuro(treasury?.balance_cents)}</strong>}
          <small>{treasury?.accounts?.length} conta(s)/caixa(s){treasury?.balance_complete === false ? " · saldo incompleto" : ""}{treasury?.accounts?.some((a: Row) => a.stale) ? " · há saldos desatualizados" : ""}</small>
          {!!treasury?.card_pending_count && <small>+ {formatEuro(treasury.card_pending_cents)} em TPA por liquidar (não somado)</small>}
          {!!treasury?.other_currencies?.length && <small>Outras moedas não somadas: {treasury.other_currencies.join(", ")}</small>}
          {treasury?.liquidity_error && <small>{treasury.liquidity_error}</small>}</BlockState></Kpi>}
      {data?.permissions?.finance !== false && <Kpi label="Recebimentos e pagamentos" meta={treasury?.meta?.flows} onOpen={() => open("Movimentos reais")}>
        <BlockState block={treasury}><strong className="dash-pair"><span>+{formatEuro(treasury?.received_cents)}</span><span>−{formatEuro(treasury?.paid_cents)}</span></strong>
          <small>Saldo do período: {formatEuro((treasury?.received_cents || 0) - (treasury?.paid_cents || 0))} (com IVA)</small></BlockState></Kpi>}
      {data?.permissions?.finance !== false && <Kpi label="A pagar: vencido e 30 dias" meta={treasury?.meta?.due} onOpen={() => open("Movimentos previstos", false)} state={treasury?.due?.payable?.vencido?.count ? "bad" : ""}>
        <BlockState block={treasury}><strong>{formatEuro(["vencido", "7_dias", "30_dias"].reduce((s, k) => s + (treasury?.due?.payable?.[k]?.amount_cents || 0), 0))}</strong>
          <small>Vencido: {formatEuro(treasury?.due?.payable?.vencido?.amount_cents)} · A receber (30 dias): {formatEuro(["vencido", "7_dias", "30_dias"].reduce((s, k) => s + (treasury?.due?.receivable?.[k]?.amount_cents || 0), 0))}</small></BlockState></Kpi>}
      {data?.permissions?.costs && <Kpi label="Margem bruta" meta={margin?.meta} state={margin?.level === "critical" ? "bad" : margin?.level === "partial" ? "warn" : ""}>
        <BlockState block={margin}><strong>{margin?.gross_margin_percent != null ? `${String(margin.gross_margin_percent).replace(".", ",")} %` : "Sem custo conhecido"}</strong>
          <MarginSummary margin={margin || {}} onArticles={() => setMarginArticles(true)} onReport={openCostsMap} /></BlockState></Kpi>}
    </div>,
    margins: data?.permissions?.costs ? <MarginPanel margin={margin} onArticles={() => setMarginArticles(true)} onReport={openCostsMap} />
      : <p className="dash-muted">As margens e os custos requerem autorização para consultar custos.</p>,
    evolution: <div className="dash-charts">
      {charts.sales}
      {charts.flows}
      {charts.forecast}
      {charts.due}
      {charts.expenses}
      {charts.families}
    </div>,
    attention: <div className="dash-attention">
      {!data && <p className="dash-loading" role="status">{error ? "Análise indisponível." : !valid ? "Indique um período válido." : "A analisar…"}</p>}
      {data && !data.attention.length && <p className="dash-muted">Nada a assinalar com os dados disponíveis neste período.</p>}
      {data?.attention.map((note: Row) => { const [label, Icon] = SEVERITY[note.severity]; return <article key={note.key} className={`dash-note is-${note.severity}`}>
        <span className="dash-note-severity"><Icon size={16} aria-hidden="true" />{label}</span>
        <div><strong>{note.fact}</strong>{note.values && <p className="dash-note-values">{note.values}</p>}<p><b>Impacto:</b> {note.impact} <b>Ação recomendada:</b> {note.action}</p></div>
        <ActionButton type="button" onClick={() => open(note.page)}>Abrir</ActionButton>
      </article>; })}
    </div>,
    actions: <div className="dash-actions">
      {["administrador", "gestor", "operador"].includes(role) && <ActionButton type="button" onClick={() => onAction("open")}>Abrir caixa</ActionButton>}
      {["administrador", "gestor", "operador"].includes(role) && <ActionButton type="button" onClick={() => navigate("Folha de caixa")}>Fechar ou conferir caixa</ActionButton>}
      {["administrador", "gestor", "operador"].includes(role) && <ActionButton type="button" onClick={() => onAction("movement")}>Registar recebimento ou pagamento</ActionButton>}
      {mode === "local" && role === "administrador" && <ActionButton type="button" onClick={() => navigate("Importar SAF-T de vendas")}>Importar SAF-T</ActionButton>}
      {["administrador", "gestor"].includes(role) && <ActionButton type="button" onClick={() => navigate("Extratos")}>Importar extrato</ActionButton>}
      {["administrador", "gestor"].includes(role) && <ActionButton type="button" onClick={() => navigate("Conferência documental")}>Conferir documentos</ActionButton>}
      <ActionButton type="button" onClick={() => navigate("Movimentos previstos")}>Plano de pagamentos</ActionButton>
      <ActionButton type="button" onClick={() => navigate("Previsão de liquidez")}>Previsão de liquidez</ActionButton>
    </div>,
  };
  const order = prefs.order.filter(key => SECTIONS.some(([k]) => k === key)).concat(SECTIONS.map(([k]) => k).filter(k => !prefs.order.includes(k))).filter(key => (!["orders", "margins"].includes(key) || ["administrador", "gestor"].includes(role)) && (key !== "articles" || ["administrador", "gestor", "consulta"].includes(role)));
  const move = (key: SectionKey, delta: number) => {
    const next = [...order]; const index = next.indexOf(key); const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    savePrefs({ ...prefs, order: next });
  };

  // Grelha de artigos vendidos e custos (mesmo período e estabelecimento do painel); recalcular atualiza o painel.
  const marginDialog = marginArticles && <MarginArticles start={start} end={end} store={store} navigate={page => open(page, false)}
    onClose={() => setMarginArticles(false)} onRecalculate={() => setVersion(v => v + 1)} />;

  // ---------------------------------------------------------------- versão nova (mesmos dados e blocos)
  const modern = variant === "nova";
  const finance = data?.permissions?.finance !== false, costs = !!data?.permissions?.costs;
  const showAttention = () => updateLocationParam("area.dashboard", "attention");
  const vatBalance = vat?.balance_cents || 0;
  const extra: Record<"versus" | "vat" | "alerts", ReactNode> = {
    versus: costs && finance && <BlockState block={sales?.status === "ok" ? expenses : sales}>{sales?.points && expenses?.points && expenses.points.length === sales.points.length && <ChartFrame title="Evolução de vendas e custos operacionais"
      legend={<Legend series={[{ key: "v", label: `Vendas (${sales.vat_basis})`, form: "line" }, { key: "c", label: `Custos operacionais (${expenses.vat_basis})`, form: "line" }]} />}
      table={{ columns: ["Período", "Vendas", "Custos operacionais"], rows: sales.points.map((p: Row, i: number) => [p.label, formatEuro(p.current_cents), formatEuro(expenses.points[i].amount_cents)]) }}
      footnote={`Vendas: faturação pela data do documento; custos: os documentos e custos sem documento das rubricas de custos operacionais. Não são recebimentos nem pagamentos.${sales.vat_basis === "com IVA" ? " Atenção: vendas com IVA (bases em falta) e compras sem IVA dedutível." : ""}`}>
      <ColumnsChart ariaLabel="Vendas e custos operacionais por período" labels={sales.points.map((p: Row) => p.label)}
        series={[{ key: "v", label: "Vendas", form: "line" }, { key: "c", label: "Custos operacionais", form: "line" }]}
        values={[sales.points.map((p: Row) => p.current_cents), expenses.points.map((p: Row) => p.amount_cents)]} />
    </ChartFrame>}</BlockState>,
    vat: costs && <article className="dash-card dash-vat" aria-label="Previsão de IVA">
      <div className="dash-card-head"><h3><Percent size={18} aria-hidden="true" />Previsão de IVA</h3><MetaInfo meta={vat?.meta} /></div>
      <BlockState block={vat}>
        <div className={`dash-vat-balance ${vatBalance > 0 ? "is-pay" : vatBalance < 0 ? "is-credit" : ""}`}>
          <span>{vatBalance > 0 ? "Saldo estimado a pagar" : vatBalance < 0 ? "Saldo estimado a recuperar" : "Saldo estimado nulo"}</span>
          <strong>{formatEuro(Math.abs(vatBalance))}</strong>
        </div>
        <dl className="dash-vat-grid">
          <div><dt>Período</dt><dd>{day(start)} a {day(end)}{vat?.period && <small>Periodicidade configurada: {vat.period}</small>}</dd></div>
          <div><dt>IVA liquidado (vendas)</dt><dd>{formatEuro(vat?.output_cents)}</dd></div>
          <div><dt>IVA dedutível estimado</dt><dd>{formatEuro(vat?.deductible_cents)}<small>Por rubrica, segundo a percentagem de dedução</small></dd></div>
          {vat?.input_cents != null && <div><dt>IVA suportado não dedutível</dt><dd>{formatEuro((vat.input_cents || 0) - (vat.deductible_cents || 0))}</dd></div>}
          <div><dt>Documentos</dt><dd>{vat?.sales_documents ?? "—"} de venda · {vat?.purchase_documents ?? "—"} de compra</dd></div>
          {vat?.meta?.source && <div><dt>Origem</dt><dd>{vat.meta.source}</dd></div>}
        </dl>
        {!!expenses?.unclassified_cents && <Notice tone="warning">Previsão provisória: há {formatEuro(expenses.unclassified_cents)} de compras sem rubrica, cujo IVA conta como dedutível até serem classificadas.</Notice>}
        <p className="dash-muted">Estimativa de gestão com os documentos conhecidos, não o apuramento da declaração periódica. Não inclui regularizações nem créditos de períodos anteriores.</p>
        <ActionButton type="button" className="dash-link" onClick={() => { handOffPeriod("Mapas de gestão", start, end); navigate("Mapas de gestão", { "area.mapas": "vat" }); }}>Ver mapa de IVA</ActionButton>
      </BlockState>
    </article>,
    alerts: <article className="dash-card dash-alerts" aria-label="Alertas e tarefas">
      <div className="dash-card-head"><h3>Alertas e tarefas</h3>{!!data?.attention.length && <span className="dash-count">{data.attention.length}</span>}</div>
      {!data ? <p className="dash-loading" role="status">{error ? "Análise indisponível." : "A analisar…"}</p>
        : !data.attention.length ? <p className="dash-muted">Nada a assinalar com os dados disponíveis neste período.</p>
        : <ul>{data.attention.slice(0, 5).map((note: Row) => { const [label, Icon] = SEVERITY[note.severity]; return <li key={note.key}>
            <ActionButton type="button" className={`dash-alert is-${note.severity}`} aria-label={`${label}: ${note.fact}. Abrir`} onClick={() => open(note.page)}>
              <Icon size={16} aria-hidden="true" /><span>{note.fact}</span><ChevronRight size={16} aria-hidden="true" /></ActionButton></li>; })}</ul>}
      {!!data?.attention.length && <ActionButton type="button" className="dash-link" onClick={showAttention}>Ver todos com impacto e ação recomendada</ActionButton>}
    </article>,
  };
  const card = (key: CardKey) => key === "versus" || key === "vat" || key === "alerts" ? extra[key] : charts[key];
  const cardOrder = (prefs.cards || []).filter(key => CARDS.some(([k]) => k === key)).concat(CARDS.map(([k]) => k).filter(k => !(prefs.cards || []).includes(k)));
  const hiddenCards = prefs.hiddenCards || [];
  const moveCard = (key: CardKey, delta: number) => {
    const next = [...cardOrder]; const index = next.indexOf(key); const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    savePrefs({ ...prefs, cards: next });
  };
  /** Cartão principal: valor, variação e nota, com os estados do bloco (a calcular, sem acesso, sem dados). */
  const hero = (label: string, tone: string, Icon: typeof Wallet, block: Row | undefined, value: ReactNode, note: ReactNode, onOpen?: () => void, meta?: Row) =>
    <article className="dash-hero-card ui-tone" data-tone={tone}>
      <span className="dash-hero-icon" aria-hidden="true"><Icon size={22} /></span>
      <div className="dash-hero-body">
        <div className="dash-kpi-head"><span>{label}</span><MetaInfo meta={meta} /></div>
        <BlockState block={block}>{onOpen ? <ActionButton type="button" variant="quiet" inferAction={false} className="dash-kpi-body" onClick={onOpen}><strong>{value}</strong>{note}</ActionButton> : <div className="dash-kpi-body"><strong>{value}</strong>{note}</div>}</BlockState>
      </div>
    </article>;
  // Na versão nova, «Encomendas e abastecimento» junta os sinais de encomendas e a visão de trabalho (para todos os perfis).
  const modernSections: Record<SectionKey, ReactNode> = {
    ...sections,
    evolution: <div className="dash-cards">{cardOrder.filter(key => !hiddenCards.includes(key)).map(key => { const node = card(key); return node ? <div key={key} className={`dash-card-slot is-${key}`}><h3 className="dash-slot-title">{CARDS.find(([k]) => k === key)?.[1]}</h3>{node}</div> : null; })}</div>,
    orders: <>{["administrador", "gestor"].includes(role) && sections.orders}{work}</>,
  };
  if (modern) {
    const modernOrder = order.concat(work && !order.includes("orders") && !prefs.hidden.includes("orders") ? ["orders" as SectionKey] : []);
    return <div className="dash is-modern" aria-busy={loading}>
      <PageHeader title="Dashboard" description={`Visão global do negócio · ${companyName}`} actions={<>
        <ActionButton type="button" disabled={loading} onClick={() => setVersion(v => v + 1)}><RefreshCw size={16} />Atualizar</ActionButton>
        <ActionButton type="button" aria-expanded={customizing} onClick={() => setCustomizing(!customizing)}><Settings2 size={16} />Personalizar</ActionButton>
        {onVersion && <ActionButton type="button" onClick={() => onVersion("atual")}>Voltar à versão atual</ActionButton>}
      </>} />
      <div className="dash-filters filter-bar" role="group" aria-label="Filtros do painel">
        <DatePresets from={start} to={end} onSelect={(from, to) => { setStart(from); setEnd(to); }} />
        <label>Desde<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
        <label>Até<input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)} /></label>
        <label>Comparar com<select value={compare} onChange={e => setCompare(e.target.value)}><option value="previous">Período anterior</option><option value="yoy">Mesmo período do ano anterior</option></select></label>
      </div>
      <p className="dash-context">{companyName} · {store ? "estabelecimento selecionado no topo" : "todos os estabelecimentos autorizados"} · {data ? <>Comparação: {data.comparison.method}{data.period.in_progress && " · período em curso"} · atualizado {when(data.generated_at)}</> : error ? "consulta indisponível" : !valid ? "período inválido" : "a calcular…"}</p>
      {!valid && <Notice tone="warning">Indique um período válido.</Notice>}
      {error && <Notice tone="error">{error}{data && " São apresentados dados da última consulta deste âmbito; atualize para confirmar os valores."} <ActionButton type="button" onClick={() => setVersion(v => v + 1)}>Tentar novamente</ActionButton></Notice>}
      {customizing && <section className="panel dash-customize" aria-label="Personalizar o painel">
        <p>Mostre, oculte ou reordene as áreas e os cartões da Evolução. A preferência fica guardada neste navegador para o seu utilizador.</p>
        <h3>Áreas</h3>
        {modernOrder.map((key, index) => <div key={key} className="dash-customize-row">
          <label className="checkbox"><input type="checkbox" checked={!prefs.hidden.includes(key)} onChange={e => savePrefs({ ...prefs, order, hidden: e.target.checked ? prefs.hidden.filter(k => k !== key) : [...prefs.hidden, key] })} />{SECTIONS.find(([k]) => k === key)?.[1]}</label>
          <ActionButton type="button" disabled={index === 0} aria-label={`Subir ${SECTIONS.find(([k]) => k === key)?.[1]}`} onClick={() => move(key, -1)}><ArrowUp size={14} /></ActionButton>
          <ActionButton type="button" disabled={index === modernOrder.length - 1} aria-label={`Descer ${SECTIONS.find(([k]) => k === key)?.[1]}`} onClick={() => move(key, 1)}><ArrowDown size={14} /></ActionButton>
        </div>)}
        <h3>Cartões da Evolução</h3>
        {cardOrder.map((key, index) => <div key={key} className="dash-customize-row">
          <label className="checkbox"><input type="checkbox" checked={!hiddenCards.includes(key)} onChange={e => savePrefs({ ...prefs, cards: cardOrder, hiddenCards: e.target.checked ? hiddenCards.filter(k => k !== key) : [...hiddenCards, key] })} />{CARDS.find(([k]) => k === key)?.[1]}</label>
          <ActionButton type="button" disabled={index === 0} aria-label={`Subir ${CARDS.find(([k]) => k === key)?.[1]}`} onClick={() => moveCard(key, -1)}><ArrowUp size={14} /></ActionButton>
          <ActionButton type="button" disabled={index === cardOrder.length - 1} aria-label={`Descer ${CARDS.find(([k]) => k === key)?.[1]}`} onClick={() => moveCard(key, 1)}><ArrowDown size={14} /></ActionButton>
        </div>)}
        <ActionButton type="button" onClick={() => savePrefs({ order: SECTIONS.map(([k]) => k), hidden: [] })}>Repor</ActionButton>
      </section>}
      {data?.permissions?.finance === false && <Notice>Os indicadores financeiros não estão disponíveis para o seu perfil.</Notice>}
      <div className={`dash-hero${loading && data ? " dash-refreshing" : ""}`} aria-label="Indicadores principais" role="group">
        {finance && hero(`Vendas${sales?.vat_basis ? ` (${sales.vat_basis})` : ""}`, "success", ShoppingCart, sales, formatEuro(sales?.total_cents),
          <><Delta value={sales?.change_percent} /><small>{sales?.documents} documento(s)</small></>, () => open(salesPage), sales?.meta)}
        {costs && (expenses?.status === "empty" && expenses?.previous_cents
          // Sem compras no período, mas com compras na comparação: mostra-se o valor comparável em vez de um cartão vazio.
          ? hero(`Custos operacionais (${expenses.vat_basis})`, "info", Receipt, { status: "ok" }, "Sem custos",
            <small>Neste período, pela data do documento. {data?.comparison?.method}: {formatEuro(expenses.previous_cents)}</small>, () => open("Despesas por rubrica"), expenses.meta)
          : hero(`Custos operacionais${expenses?.vat_basis ? ` (${expenses.vat_basis})` : ""}`, "info", Receipt, expenses, formatEuro(expenses?.total_cents),
            <><Delta value={expenses?.change_percent} inverse /><small>Por rubrica, pela data do documento</small></>, () => explainExpenses(), expenses?.meta))}
        {costs && (margin?.status === "ok" && !margin.cost_restricted
          // Margem bruta apurada (sobre as vendas com custo) e cobertura de custos, com a cor do nível de cobertura.
          ? hero(margin.partial ? "Margem bruta apurada (parcial)" : "Margem bruta apurada", marginTone(margin.level), Layers, margin,
            margin.gross_margin_percent != null ? `${String(margin.gross_margin_percent).replace(".", ",")} %` : "Sem custo conhecido",
            <MarginSummary margin={margin} onArticles={() => setMarginArticles(true)} onReport={openCostsMap} />, undefined, margin.meta)
          : hero("Margem bruta", "info", Layers, margin?.status === "empty" ? { status: "ok" } : margin, margin?.cost_restricted ? "Sem acesso" : "Sem vendas",
            <small>{margin?.cost_restricted ? "O seu perfil não permite ver custos de stock." : margin?.message || "Sem vendas por artigo neste período."}</small>, openCostsMap, margin?.meta))}
        {finance && hero("Tesouraria · saldo conhecido", "warning", Wallet, treasury, treasury?.balance_cents === null ? "Sem dados" : formatEuro(treasury?.balance_cents),
          <small>{treasury?.accounts?.length} conta(s)/caixa(s){treasury?.balance_complete === false ? " · saldo incompleto" : ""}{treasury?.accounts?.some((a: Row) => a.stale) ? " · há saldos desatualizados" : ""}{treasury?.card_pending_count ? ` · + ${formatEuro(treasury.card_pending_cents)} em TPA por liquidar (não somado)` : ""}</small>,
          () => open("Previsão de liquidez", false), treasury?.meta?.balance)}
      </div>
      <div className={loading && data ? "dash-refreshing" : ""}>
        <TaskWorkspace name="dashboard" label="Áreas do dashboard" items={modernOrder.filter(key => !prefs.hidden.includes(key)).map(key => ({
          id: key, label: SECTIONS.find(([k]) => k === key)?.[1] || key,
          count: key === "attention" && data?.attention.length ? data.attention.length : undefined,
          content: <section className="dash-section" aria-label={SECTIONS.find(([k]) => k === key)?.[1]}>{modernSections[key]}</section>,
        }))} />
        {modernOrder.every(key => prefs.hidden.includes(key)) && <p>As áreas estão ocultas nas suas preferências. Utilize Personalizar para as mostrar.</p>}
      </div>
      {marginDialog}
    {expenseDialog}
    </div>;
  }

  return <div className="dash" aria-busy={loading}>
    <div className="dash-filters filter-bar" role="group" aria-label="Filtros do painel">
      <DatePresets from={start} to={end} onSelect={(from, to) => { setStart(from); setEnd(to); }} />
      <label>Desde<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
      <label>Até<input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)} /></label>
      <label>Comparar com<select value={compare} onChange={e => setCompare(e.target.value)}><option value="previous">Período anterior</option><option value="yoy">Mesmo período do ano anterior</option></select></label>
      <ActionButton type="button" disabled={loading} onClick={() => setVersion(v => v + 1)}><RefreshCw size={16} />Atualizar</ActionButton>
      <ActionButton type="button" aria-expanded={customizing} onClick={() => setCustomizing(!customizing)}><Settings2 size={16} />Personalizar</ActionButton>
      {onVersion && <ActionButton type="button" onClick={() => onVersion("nova")}>Experimentar a nova versão</ActionButton>}
    </div>
    <p className="dash-context">{companyName} · {store ? "estabelecimento selecionado no topo" : "todos os estabelecimentos autorizados"} · {data ? <>Comparação: {data.comparison.method}{data.period.in_progress && " · período em curso"} · atualizado {when(data.generated_at)}</> : error ? "consulta indisponível" : !valid ? "período inválido" : "a calcular…"}</p>
    {!valid && <Notice tone="warning">Indique um período válido.</Notice>}
    {error && <Notice tone="error">{error}{data && " São apresentados dados da última consulta deste âmbito; atualize para confirmar os valores."} <ActionButton type="button" onClick={() => setVersion(v => v + 1)}>Tentar novamente</ActionButton></Notice>}
    {customizing && <section className="panel dash-customize" aria-label="Personalizar o painel">
      <p>Mostre, oculte ou reordene as áreas. A preferência fica guardada neste navegador para o seu utilizador.</p>
      {order.map((key, index) => <div key={key} className="dash-customize-row">
        <label className="checkbox"><input type="checkbox" checked={!prefs.hidden.includes(key)} onChange={e => savePrefs({ ...prefs, order, hidden: e.target.checked ? prefs.hidden.filter(k => k !== key) : [...prefs.hidden, key] })} />{SECTIONS.find(([k]) => k === key)?.[1]}</label>
        <ActionButton type="button" disabled={index === 0} aria-label="Subir" onClick={() => move(key, -1)}><ArrowUp size={14} /></ActionButton>
        <ActionButton type="button" disabled={index === order.length - 1} aria-label="Descer" onClick={() => move(key, 1)}><ArrowDown size={14} /></ActionButton>
      </div>)}
      <ActionButton type="button" onClick={() => savePrefs({ order: SECTIONS.map(([k]) => k), hidden: [] })}>Repor</ActionButton>
    </section>}
    <div className={loading && data ? "dash-refreshing" : ""}>
      <TaskWorkspace name="dashboard" label="Áreas do dashboard" items={order.filter(key => !prefs.hidden.includes(key)).map(key => ({
        id: key, label: SECTIONS.find(([k]) => k === key)?.[1] || key,
        count: key === "attention" && data?.attention.length ? data.attention.length : undefined,
        content: <section className="dash-section" aria-label={SECTIONS.find(([k]) => k === key)?.[1]}>{sections[key]}</section>,
      }))} />
      {order.every(key => prefs.hidden.includes(key)) && <p>As áreas estão ocultas nas suas preferências. Utilize Personalizar para as mostrar.</p>}
    </div>
    {marginDialog}
    {expenseDialog}
  </div>;
}
