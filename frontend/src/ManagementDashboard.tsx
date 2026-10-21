import { TaskWorkspace } from "./components/WorkspaceUI";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, ArrowDown, ArrowUp, Info, OctagonAlert, RefreshCw, Settings2, CircleDot } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, Notice } from "./components/InteractionUI";
import DatePresets from "./components/DatePresets";
import { BarList, ChartFrame, ColumnsChart, ForecastChart, Legend } from "./DashboardCharts";
import { formatEuro } from "./utils/format";
import { handOffPeriod } from "./utils/periodHandoff";
import "./ManagementDashboard.css";

type Row = Record<string, any>;
type SectionKey = "summary" | "evolution" | "attention" | "actions" | "operation";
const SECTIONS: [SectionKey, string][] = [["summary", "Resumo do negócio"], ["evolution", "Evolução"], ["attention", "O que precisa da sua atenção"], ["actions", "Ações rápidas"], ["operation", "Operação de caixa"]];
const DUE: [string, string][] = [["vencido", "Vencido"], ["7_dias", "Próx. 7 dias"], ["30_dias", "8–30 dias"], ["mais_30", "> 30 dias"], ["sem_data", "Sem data"]];
const SEVERITY: Record<string, [string, typeof AlertTriangle]> = { critical: ["Crítico", OctagonAlert], warning: ["Atenção", AlertTriangle], info: ["Informação", CircleDot] };
const lisbonToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
const day = (value: string) => value?.slice(0, 10).split("-").reverse().join("/") || "—";
const when = (value: string) => value ? new Intl.DateTimeFormat("pt-PT", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Lisbon" }).format(new Date(value)) : "—";

function usePreferences(scope: string) {
  const key = `iazela-dashboard:${scope}`;
  const read = () => { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; } };
  const [prefs, setPrefs] = useState<{ order: SectionKey[]; hidden: SectionKey[] }>(() => read() || { order: SECTIONS.map(([k]) => k), hidden: [] });
  const save = (next: { order: SectionKey[]; hidden: SectionKey[] }) => { setPrefs(next); try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* preferência só nesta sessão */ } };
  return [prefs, save] as const;
}

function MetaInfo({ meta }: { meta?: Row }) {
  if (!meta) return null;
  return <details className="dash-meta" onClick={e => e.stopPropagation()}><summary aria-label="Como é calculado"><Info size={15} aria-hidden="true" /></summary>
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
export default function ManagementDashboard({ store, scopeKey, companyName, mode, role, navigate, onAction, operation }: {
  store: string; scopeKey: string; companyName: string; mode: string; role: string; navigate: (page: string) => void; onAction: (kind: string) => void; operation?: ReactNode;
}) {
  const today = lisbonToday();
  const [start, setStart] = useState(today.slice(0, 8) + "01"), [end, setEnd] = useState(today);
  const [compare, setCompare] = useState("previous");
  const [data, setData] = useState<Row | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [customizing, setCustomizing] = useState(false), [version, setVersion] = useState(0);
  const [prefs, savePrefs] = usePreferences(scopeKey);
  const valid = !!start && !!end && start <= end;
  const request = useRef(0);
  useEffect(() => {
    if (!valid) return;
    const id = ++request.current;
    setLoading(true); setError("");
    api(`/dashboard/overview?${new URLSearchParams({ start, end, compare, store_id: store })}`, undefined, { timeoutMs: 90_000 })
      .then(result => { if (id === request.current) setData(result); })
      .catch(e => { if (id === request.current) setError((e as Error).message); })
      .finally(() => { if (id === request.current) setLoading(false); });
  }, [start, end, compare, store, version]);
  const open = (page: string, withPeriod = true) => { if (withPeriod) handOffPeriod(page, start, end); navigate(page); };
  const sales = data?.sales, treasury = data?.treasury, expenses = data?.expenses, margin = data?.margin;
  const salesPage = data?.mode === "local" ? "Faturação e recebimentos" : "Movimentos reais";

  const sections: Record<SectionKey, ReactNode> = {
    operation,
    summary: <div className="dash-kpis">
      {data?.permissions?.finance !== false && <Kpi label={`Faturação${sales?.vat_basis ? ` (${sales.vat_basis})` : ""}`} meta={sales?.meta} onOpen={() => open(salesPage)}>
        <BlockState block={sales}><strong>{formatEuro(sales?.total_cents)}</strong><Delta value={sales?.change_percent} /><small>{sales?.documents} documento(s)</small></BlockState></Kpi>}
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
      {data?.permissions?.costs && <Kpi label="Margem bruta" meta={margin?.meta} onOpen={() => open("Mapas de gestão")}>
        <BlockState block={margin}>{margin?.gross_margin_cents != null ? <><strong>{formatEuro(margin.gross_margin_cents)}</strong><small>{margin.gross_margin_percent}% das vendas com custo conhecido ({margin.cost_coverage_percent}%)</small></>
          : <><strong>Indisponível</strong><small>{margin?.cost_restricted ? "O seu perfil não permite ver custos de stock." : `Custo conhecido em ${String(margin?.cost_coverage_percent ?? 0).replace(".", ",")}% das vendas (mínimo 80%).`}</small></>}</BlockState></Kpi>}
    </div>,
    evolution: <div className="dash-charts">
      {data?.permissions?.finance !== false && <BlockState block={sales}>{sales?.points && <ChartFrame title={`Faturação por ${sales.unit === "day" ? "dia" : sales.unit === "week" ? "semana" : "mês"} (${sales.vat_basis})`}
        legend={<Legend series={[{ key: "c", label: data?.period?.label, form: "bar" }, { key: "p", label: `Comparação: ${data?.comparison?.method}`, form: "line", dashed: true }]} />}
        table={{ columns: ["Período", "Atual", "Comparação"], rows: sales.points.map((p: Row) => [p.label, formatEuro(p.current_cents), p.previous_cents === null ? "—" : formatEuro(p.previous_cents)]) }}
        footnote={`Comparação: ${day(data?.comparison?.start)} a ${day(data?.comparison?.end)} (${data?.comparison?.method}).`}>
        <ColumnsChart ariaLabel="Faturação do período e do período de comparação" labels={sales.points.map((p: Row) => p.label)}
          series={[{ key: "c", label: "Atual", form: "bar" }, { key: "p", label: "Comparação", form: "line", dashed: true }]}
          values={[sales.points.map((p: Row) => p.current_cents), sales.points.map((p: Row) => p.previous_cents)]} />
      </ChartFrame>}</BlockState>}
      {data?.permissions?.finance !== false && <BlockState block={treasury}>{treasury?.flows && <ChartFrame title="Recebimentos e pagamentos (com IVA)"
        legend={<Legend series={[{ key: "in", label: "Recebimentos", form: "bar" }, { key: "out", label: "Pagamentos", form: "bar" }]} />}
        table={{ columns: ["Período", "Recebimentos", "Pagamentos"], rows: treasury.flows.map((f: Row) => [f.label, formatEuro(f.in_cents), formatEuro(f.out_cents)]) }}
        footnote="Sem transferências entre contas próprias nem liquidações de TPA (já contadas quando o cliente pagou).">
        <ColumnsChart ariaLabel="Recebimentos e pagamentos por período" labels={treasury.flows.map((f: Row) => f.label)}
          series={[{ key: "in", label: "Recebimentos", form: "bar" }, { key: "out", label: "Pagamentos", form: "bar" }]}
          values={[treasury.flows.map((f: Row) => f.in_cents), treasury.flows.map((f: Row) => f.out_cents)]} />
      </ChartFrame>}</BlockState>}
      {data?.permissions?.finance !== false && <BlockState block={treasury}>{treasury?.forecast && (treasury.forecast.available ? <ChartFrame title="Saldo previsto · próximos 30 dias (EUR)"
        legend={<Legend series={[{ key: "f", label: "Previsto (a partir do saldo atual conhecido)", form: "line", dashed: true }]} />}
        table={{ columns: ["Data", "Entradas", "Saídas", "Saldo"], rows: treasury.forecast.points.map((p: Row) => [day(p.date), formatEuro(p.in_cents), formatEuro(p.out_cents), formatEuro(p.closing_cents)]) }}
        footnote={treasury.forecast.incomplete ? "Saldo inicial incompleto: há contas sem saldo conhecido." : `Mínimo: ${formatEuro(treasury.forecast.minimum_cents)} em ${day(treasury.forecast.minimum_date)}.`}>
        <ForecastChart ariaLabel="Saldo previsto nos próximos 30 dias" start={treasury.forecast.initial_cents} reserve={treasury.forecast.reserve_cents}
          points={treasury.forecast.points.map((p: Row) => ({ label: day(p.date).slice(0, 5), value: p.closing_cents }))} />
      </ChartFrame> : <p className="dash-muted">Previsão indisponível: sem saldos ou movimentos previstos.</p>)}</BlockState>}
      {data?.permissions?.finance !== false && <BlockState block={treasury}>{treasury?.due && <ChartFrame title="A receber e a pagar por vencimento (EUR)"
        legend={<Legend series={[{ key: "r", label: "A receber", form: "bar" }, { key: "p", label: "A pagar", form: "bar" }]} />}
        table={{ columns: ["Vencimento", "A receber", "A pagar"], rows: DUE.map(([k, label]) => [label, `${formatEuro(treasury.due.receivable[k].amount_cents)} (${treasury.due.receivable[k].count})`, `${formatEuro(treasury.due.payable[k].amount_cents)} (${treasury.due.payable[k].count})`]) }}
        footnote="Saldos por liquidar: uma fatura registada não conta como paga.">
        <ColumnsChart ariaLabel="Valores a receber e a pagar por vencimento" labels={DUE.map(([, label]) => label)}
          series={[{ key: "r", label: "A receber", form: "bar" }, { key: "p", label: "A pagar", form: "bar" }]}
          values={[DUE.map(([k]) => treasury.due.receivable[k].amount_cents), DUE.map(([k]) => treasury.due.payable[k].amount_cents)]} />
      </ChartFrame>}</BlockState>}
      {data?.permissions?.costs && <BlockState block={expenses}>{expenses?.rows && <ChartFrame title={`Despesas por rubrica (${expenses.vat_basis})`}
        table={{ columns: ["Rubrica", "Período", "Comparação"], rows: expenses.rows.map((r: Row) => [r.name, formatEuro(r.amount_cents), formatEuro(r.previous_cents)]) }}
        footnote={<>Total {formatEuro(expenses.total_cents)} · <Delta value={expenses.change_percent} inverse /> <MetaInfo meta={expenses.meta} /></>}>
        <BarList ariaLabel="Despesas por rubrica" rows={expenses.rows.map((r: Row) => ({ label: r.name, value: r.amount_cents, note: r.previous_cents ? `comparação ${formatEuro(r.previous_cents)}` : undefined }))} />
        <ActionButton type="button" className="dash-link" onClick={() => open("Despesas por rubrica")}>Ver documentos por rubrica</ActionButton>
      </ChartFrame>}</BlockState>}
      {data?.permissions?.costs && <BlockState block={margin}>{margin?.rows && <ChartFrame title="Vendas por família de artigo (sem IVA)"
        table={{ columns: ["Família", "Vendas", "Artigos", "Custo conhecido", "Margem"], rows: margin.rows.map((r: Row) => [r.name, formatEuro(r.sales_cents), r.articles, r.cost_coverage_percent == null ? "—" : `${r.cost_coverage_percent}%`, r.margin_cents == null ? "Indisponível" : formatEuro(r.margin_cents)]) }}
        footnote={<>Custo conhecido em {String(margin.cost_coverage_percent ?? 0).replace(".", ",")}% das vendas{margin.cost_restricted ? " (sem permissão para ver custos)" : ""}. <MetaInfo meta={margin.meta} /></>}>
        <BarList ariaLabel="Vendas por família de artigo" rows={margin.rows.map((r: Row) => ({ label: r.name, value: r.sales_cents, note: r.margin_cents != null ? `margem ${formatEuro(r.margin_cents)}` : r.cost_coverage_percent ? `custo conhecido ${r.cost_coverage_percent}%` : "custo em falta" }))} />
      </ChartFrame>}</BlockState>}
    </div>,
    attention: <div className="dash-attention">
      {!data && <p className="dash-loading" role="status">A analisar…</p>}
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
  const order = prefs.order.filter(key => SECTIONS.some(([k]) => k === key)).concat(SECTIONS.map(([k]) => k).filter(k => !prefs.order.includes(k)));
  const move = (key: SectionKey, delta: number) => {
    const next = [...order]; const index = next.indexOf(key); const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    savePrefs({ ...prefs, order: next });
  };

  return <div className="dash" aria-busy={loading}>
    <div className="dash-filters filter-bar" role="group" aria-label="Filtros do painel">
      <DatePresets from={start} to={end} onSelect={(from, to) => { setStart(from); setEnd(to); }} />
      <label>Desde<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
      <label>Até<input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)} /></label>
      <label>Comparar com<select value={compare} onChange={e => setCompare(e.target.value)}><option value="previous">Período anterior</option><option value="yoy">Mesmo período do ano anterior</option></select></label>
      <ActionButton type="button" disabled={loading} onClick={() => setVersion(v => v + 1)}><RefreshCw size={16} />Atualizar</ActionButton>
      <ActionButton type="button" aria-expanded={customizing} onClick={() => setCustomizing(!customizing)}><Settings2 size={16} />Personalizar</ActionButton>
    </div>
    <p className="dash-context">{companyName} · {store ? "estabelecimento selecionado no topo" : "todos os estabelecimentos autorizados"} · {data ? <>Comparação: {data.comparison.method}{data.period.in_progress && " · período em curso"} · atualizado {when(data.generated_at)}</> : "a calcular…"}</p>
    {!valid && <Notice tone="warning">Indique um período válido.</Notice>}
    {error && <Notice tone="error">{error} <ActionButton type="button" onClick={() => setVersion(v => v + 1)}>Tentar novamente</ActionButton></Notice>}
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
  </div>;
}
