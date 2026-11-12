import { useMemo, useState } from "react";
import {
  AlertTriangle, BarChart3, ChevronDown, ChevronRight, Coins, Download, Info, Lock, Printer, RotateCcw, Search, Settings2, ShoppingCart,
  Table2, TrendingDown, TrendingUp, Wallet,
} from "lucide-react";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { rubricIcons } from "./components/RubricBadge";
import { BarList, ChartFrame, ColumnsChart, Legend } from "./DashboardCharts";
import ExpenseDrilldown from "./ExpenseDrilldown";
import RubricMovements, { type MovementsScope } from "./RubricMovements";
import type { DrilldownStep } from "./Drilldown";
import { api } from "./lib/api";
import { downloadCsv } from "./lib/csv";
import { handOffPeriod } from "./utils/periodHandoff";
import { formatEuro } from "./utils/format";
import "./ManagementCosts.css";

/**
 * Proveitos, custos e margens por rubrica (Mapas de gestão → Custos e proveitos): indicadores, tabela mensal com
 * subtotais, comparação com o ano anterior e com o orçamento, alertas, gráficos e consulta/correção dos movimentos
 * de cada valor. Os valores vêm todos do servidor (o mesmo cálculo de sempre); aqui só se filtram e apresentam.
 */

type Row = Record<string, any>;
type Open = { view: string; viewLabel: string; category?: string | null; name?: string; start: string; end: string; only?: MovementsScope };
const MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const MONTH_NAMES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const GROUPS: [string, string][] = [["purchases", "Compras de mercadorias e investimentos"], ["operating", "Despesas operacionais"]];
const TYPES: [string, string][] = [["", "Todas as rubricas"], ["purchases", "Compras e investimentos"], ["operating", "Despesas operacionais"],
  ["fixed", "Custos fixos"], ["variable", "Custos variáveis"], ["unclassified", "Por classificar"]];
const ALERT_KINDS: Record<string, string> = { unclassified: "Sem classificação", duplicate: "Possível duplicado", increase: "Aumento anual",
  above_average: "Acima da média", month_change: "Subida mensal" };
const day = (iso: string) => iso.split("-").reverse().join("/");
const lastDay = (year: number, month: number) => `${year}-${String(month).padStart(2, "0")}-${String(new Date(Date.UTC(year, month, 0)).getUTCDate()).padStart(2, "0")}`;
const shortLabel = (key: string) => key.includes("-T") ? `T${key.split("-T")[1]} ${key.slice(0, 4)}` : `${MONTHS[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`;
const euros = (cents: number | null | undefined) => cents == null ? "" : (cents / 100).toFixed(2).replace(".", ",");
const percent = (value: number | null | undefined) => value == null ? "—" : `${value.toLocaleString("pt-PT", { maximumFractionDigits: 1 })}%`;
const change = (current: number, previous: number | null | undefined) => previous ? Math.round((current - previous) * 1000 / Math.abs(previous)) / 10 : null;

/** Cor identificativa pela natureza do nome (regras fixas); as restantes usam a cor da rubrica. */
const TONES: [RegExp, string][] = [[/segurança social|seg\. social|\btsu\b/, "purple"], [/mercador/, "blue"], [/venciment|salári|ordenad|remunera|pessoal/, "violet"],
  [/impost|\biva\b|\birc\b|\birs\b|taxa/, "amber"], [/renda|aluguer|arrendament/, "navy"], [/seguro/, "orange"], [/comunica|telefon|internet|telecom/, "sky"],
  [/eletric|elétric|\bluz\b|energia/, "yellow"], [/banc|comiss/, "slate"], [/outr|diversa|por classificar/, "gray"]];
function toneOf(name: string) {
  const text = name.toLocaleLowerCase("pt-PT");
  return TONES.find(([pattern]) => pattern.test(text))?.[1] || "";
}

function RubricMark({ row }: { row: Row }) {
  const tone = toneOf(`${row.category_name} ${row.parent_name || ""}`) || (row.category_id ? "" : "gray");
  const Icon = (rubricIcons[row.icon as keyof typeof rubricIcons] || rubricIcons.tag)[1];
  return <span className={`mc-mark${tone ? ` tone-${tone}` : ""}`} style={tone ? undefined : { color: row.color || undefined }} aria-hidden="true"><Icon size={14} /></span>;
}

function Tip({ text, label }: { text: string; label: string }) {
  return <span className="mc-tip"><button type="button" aria-label={`Como se calcula: ${label}`} title={text}><Info size={14} aria-hidden="true" /></button>
    <span role="tooltip">{text}</span></span>;
}

function Delta({ current, previous, higherIsBetter }: { current: number; previous?: number | null; higherIsBetter: boolean }) {
  const value = change(current, previous);
  if (value === null) return <small className="mc-kpi-sub">Sem dados comparáveis do ano anterior</small>;
  const good = value === 0 ? null : (value > 0) === higherIsBetter;
  const Icon = value >= 0 ? TrendingUp : TrendingDown;
  return <small className={`mc-delta${good === null ? "" : good ? " is-good" : " is-bad"}`}><Icon size={14} aria-hidden="true" />
    {value > 0 ? "+" : ""}{percent(value)} vs. ano anterior <span className="sr-only">({good === null ? "sem variação" : good ? "favorável" : "desfavorável"})</span></small>;
}

/** Valor da tabela: botão que abre os movimentos, alerta discreto e, opcionalmente, orçamento e desvio. */
function Cell({ value, onOpen, name, alert, planned, planActual }: { value: number; onOpen?: () => void; name: string; alert?: Row; planned?: number | null; planActual?: number }) {
  const deviation = planned != null ? change(planActual ?? value, planned) : null;
  const content = <><span className="mc-value">{alert && <AlertTriangle size={12} className={`mc-alert-icon is-${alert.tone}`} aria-label={alert.title} />}{value ? formatEuro(value) : <span className="mc-zero">—</span>}</span>
    {planned != null && <small className={`mc-plan${deviation != null && deviation > 0 ? " is-over" : ""}`} title={planActual != null ? "Desvio calculado só com as rubricas orçamentadas" : undefined}>orç. {formatEuro(planned)}{deviation != null && ` · ${deviation > 0 ? "+" : ""}${percent(deviation)}`}</small>}</>;
  return <td className="numeric">{onOpen && value ? <button type="button" className="mc-cell" title={alert?.message} aria-label={`${name}: ${formatEuro(value)}${alert ? ` (${alert.title})` : ""}. Ver movimentos`} onClick={onOpen}>{content}</button> : content}</td>;
}

export default function ManagementCosts({ data, start, end, store, onPeriod, navigate, onChanged }: {
  data: Row; start: string; end: string; store: string; mode: string;
  onPeriod: (start: string, end: string) => void; navigate?: (page: string, params?: Record<string, string>) => void; onChanged: () => void;
}) {
  const [view, setView] = useState<"table" | "charts">("table");
  const [search, setSearch] = useState(""), [type, setType] = useState(""), [showEmpty, setShowEmpty] = useState(false);
  const [compare, setCompare] = useState(false), [budget, setBudget] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({}), [selected, setSelected] = useState<string | null>(null);
  const [open, setOpen] = useState<Open | null>(null), [detail, setDetail] = useState<{ steps: DrilldownStep[]; start: string; end: string } | null>(null);
  const [settings, setSettings] = useState<Row | null>(null), [settingsError, setSettingsError] = useState(""), [saving, setSaving] = useState(false);
  const periods: Row[] = data.periods || [];
  const keys = periods.map(p => p.key);
  const previous: Row | null = data.previous?.has_data ? data.previous : null;
  const plan: Row | null = data.budget?.available ? data.budget : null;
  const year = Number(start.slice(0, 4)), fromMonth = Number(start.slice(5, 7)), toMonth = end.slice(0, 4) === start.slice(0, 4) ? Number(end.slice(5, 7)) : 12;
  const thisYear = new Date().getFullYear();
  // A análise comercial existe nos dois modos (SAF-T no local; vendas do ERP no integrado).
  const salesPage = "Faturação e recebimentos";

  const allRows: Row[] = useMemo(() => [...(data.costs || []), ...(showEmpty ? (data.empty_rubrics || []).map((r: Row) => ({ ...r, values: {}, documents: {}, total_cents: 0, records: 0, empty: true })) : [])], [data, showEmpty]);
  const filtering = !!search.trim() || !!type;
  const rows = allRows.filter(row => {
    const text = search.trim().toLocaleLowerCase("pt-PT");
    if (text && !`${row.category_name} ${row.parent_name || ""}`.toLocaleLowerCase("pt-PT").includes(text)) return false;
    if (type === "purchases" || type === "operating") return row.group === type;
    if (type === "fixed") return row.fixed;
    if (type === "variable") return !row.fixed && !!row.category_id;
    if (type === "unclassified") return !row.category_id;
    return true;
  });
  const sum = (list: Row[], key?: string) => list.reduce((n, r) => n + (key ? r.values[key] || 0 : r.total_cents), 0);
  const filteredCosts = Object.fromEntries(keys.map(k => [k, sum(rows, k)]));
  const filteredTotal = sum(rows);
  const alerts: Row[] = data.alerts || [];
  const cellAlert = (category: string | null, key: string | null) => alerts.find(a => (a.category_id || null) === (category || null) && a.period === key && a.kind !== "duplicate");
  const range = (key: string) => {
    const y = Number(key.slice(0, 4));
    const [first, last] = key.includes("-T") ? [(Number(key.split("-T")[1]) - 1) * 3 + 1, (Number(key.split("-T")[1]) - 1) * 3 + 3] : [Number(key.slice(5, 7)), Number(key.slice(5, 7))];
    const from = `${y}-${String(first).padStart(2, "0")}-01`, to = lastDay(y, last);
    return { start: from < start ? start : from, end: to > end ? end : to };
  };
  const label = (key: string) => periods.find(p => p.key === key)?.label || key;

  const openRow = (row: Row, key: string | null, view = "costs", viewLabel = "Custos do mapa de custos e proveitos") =>
    setOpen({ view, viewLabel, category: row.category_id ?? null, name: row.category_name, ...(key ? range(key) : { start, end }) });
  const openAll = (key: string | null, only?: MovementsScope) => setOpen({ view: "costs", viewLabel: "Custos do mapa de custos e proveitos", ...(key ? range(key) : { start, end }), only });
  const openSales = (key: string | null) => {
    if (!navigate) return;
    const period = key ? range(key) : { start, end };
    handOffPeriod(salesPage, period.start, period.end);
    navigate(salesPage);
  };
  const openAlert = (alert: Row) => {
    if (alert.kind === "duplicate") return openAll(null, { ids: alert.ids, label: "Possível duplicado" });
    if (alert.kind === "increase") return openRow({ category_id: alert.category_id, category_name: alert.category_name }, null);
    openRow({ category_id: alert.category_id, category_name: alert.category_name }, alert.period);
  };
  const setYearMonths = (y: number, first: number, last: number) => onPeriod(`${y}-${String(first).padStart(2, "0")}-01`, lastDay(y, Math.max(first, last)));
  const clear = () => { setSearch(""); setType(""); setShowEmpty(false); setCompare(false); setBudget(false); setSelected(null); setCollapsed({}); };

  async function loadSettings() {
    setSettingsError("");
    try { setSettings(await api("/management/alert-settings")); } catch (e) { setSettingsError((e as Error).message); }
  }
  async function saveSettings() {
    if (!settings) return;
    setSaving(true); setSettingsError("");
    try {
      const minimum = Math.round(Number(String(settings.minimum_euros ?? euros(settings.minimum_cents)).replace(/\./g, "").replace(",", ".")) * 100);
      if (!Number.isFinite(minimum) || minimum < 0) throw new Error("Indique um valor mínimo válido em euros.");
      await api("/management/alert-settings", { above_average_percent: Number(settings.above_average_percent), month_change_percent: Number(settings.month_change_percent),
        increase_percent: Number(settings.increase_percent), minimum_cents: minimum, duplicate_days: Number(settings.duplicate_days), revision: settings.revision }, { method: "PUT" });
      setSettings(null); onChanged();
    } catch (e) { setSettingsError((e as Error).message); } finally { setSaving(false); }
  }

  const kpis: Row = data.kpis || {};
  const prevKpis: Row | null = previous?.kpis || null;
  const groupRows = (group: string) => rows.filter(r => r.group === group);
  const visibleCategories = rows.map(r => r.category_id || "");
  const cards = [
    { key: "revenue", label: "Proveitos", icon: <Coins size={20} />, tone: "green", value: kpis.revenue_cents, prev: prevKpis?.revenue_cents, good: true,
      formula: "Vendas sem IVA do período (SAF-T ou vendas do ERP), pela data do documento; notas de crédito e devoluções subtraem.",
      open: navigate ? () => openSales(null) : undefined, openLabel: "Ver vendas do período" },
    { key: "costs", label: filtering ? "Custos (filtro ativo)" : "Custos", icon: <Wallet size={20} />, tone: "violet", value: filtering ? filteredTotal : kpis.costs_cents,
      prev: filtering ? null : prevKpis?.costs_cents, good: false, sub: filtering ? `de ${formatEuro(kpis.costs_cents)} no total` : undefined,
      formula: "Soma das rubricas de custos operacionais e de compras deste mapa (critério atual). Regime normal: base + IVA não dedutível; restantes regimes: com IVA.",
      open: () => openAll(null, filtering ? { categories: visibleCategories, label: "Rubricas filtradas" } : undefined), openLabel: "Ver os movimentos dos custos" },
    { key: "margin", label: "Margem de gestão estimada", icon: <BarChart3 size={20} />, tone: kpis.margin_cents < 0 ? "red" : "teal", value: kpis.margin_cents,
      prev: prevKpis?.margin_cents, good: true, sub: kpis.margin_percent == null ? "Sem proveitos: margem % indisponível" : `${percent(kpis.margin_percent)} dos proveitos`,
      formula: "Proveitos − custos deste mapa. Margem % = margem ÷ proveitos (só com proveitos positivos). Indicativa: não é o resultado contabilístico nem uma margem de tesouraria." },
    { key: "merchandise", label: "Compras de mercadorias", icon: <ShoppingCart size={20} />, tone: "blue", value: kpis.merchandise_cents, prev: prevKpis?.merchandise_cents, good: false,
      sub: kpis.costs_cents ? `${percent(Math.round(kpis.merchandise_cents * 1000 / kpis.costs_cents) / 10)} dos custos` : undefined,
      formula: "Rubricas de compras (natureza «Mercadorias» ou sem natureza), pela data da fatura. Não é o custo das mercadorias vendidas; o investimento fica à parte.",
      open: () => openAll(null, { categories: (data.costs || []).filter((r: Row) => r.group === "purchases" && r.nature !== "investimento").map((r: Row) => r.category_id || ""), label: "Compras de mercadorias" }),
      openLabel: "Ver os movimentos das compras de mercadorias" },
    { key: "fixed", label: "Custos fixos", icon: <Lock size={20} />, tone: "navy", value: kpis.fixed_cents, prev: prevKpis?.fixed_cents, good: false,
      sub: (data.costs || []).some((r: Row) => r.fixed) ? (kpis.costs_cents ? `${percent(Math.round(kpis.fixed_cents * 1000 / kpis.costs_cents) / 10)} dos custos` : undefined) : "Marque as rubricas fixas em Tabelas → Rubricas",
      formula: "Soma das rubricas marcadas como «Custo fixo» em Tabelas → Rubricas (rendas, seguros, vencimentos…).",
      open: () => openAll(null, { categories: (data.costs || []).filter((r: Row) => r.fixed).map((r: Row) => r.category_id || ""), label: "Custos fixos" }),
      openLabel: "Ver os movimentos dos custos fixos" },
  ];

  const showPlan = budget && !!plan;
  const budgetOf = (row: Row, key?: string) => !plan || !plan.values[row.category_id || ""] ? null
    : key ? plan.values[row.category_id || ""][key] || 0 : keys.reduce((n, k) => n + (plan.values[row.category_id || ""][k] || 0), 0);
  // Realizado das rubricas com orçamento (o desvio do total não compara o orçamento de algumas rubricas com o realizado de todas).
  const budgeted = (data.costs || []).filter((r: Row) => plan?.values[r.category_id || ""]);
  const planActual = (key?: string) => sum(budgeted, key);
  const compareCells = (current: number, before: number | null | undefined) => compare ? <>
    <td className="numeric mc-prev">{previous ? formatEuro(before || 0) : "—"}</td>
    <td className={`numeric mc-prev${(change(current, before) || 0) > 0 ? " is-up" : ""}`}>{previous ? percent(change(current, before)) : "—"}</td></> : null;

  function rubricRow(row: Row) {
    const id = row.category_id || "";
    const rowAlert = alerts.find(a => (a.category_id || "") === id && !a.period && a.kind !== "duplicate");
    const name = row.parent_name ? `${row.parent_name} › ${row.category_name}` : row.category_name;
    return <tr key={id || "none"} className={selected === id ? "is-selected" : undefined}>
      <th scope="row"><button type="button" className="mc-rubric" aria-pressed={selected === id} onClick={() => setSelected(selected === id ? null : id)}>
        <RubricMark row={row} /><span>{name}</span>{row.fixed && <span className="mc-tag">fixo</span>}
        {rowAlert && <AlertTriangle size={12} className={`mc-alert-icon is-${rowAlert.tone}`} aria-label={rowAlert.title} />}</button></th>
      {keys.map(key => <Cell key={key} value={row.values[key] || 0} name={`${name}, ${label(key)}`} alert={cellAlert(row.category_id, key)}
        planned={showPlan && plan ? budgetOf(row, key) : null} onOpen={row.empty ? undefined : () => openRow(row, key)} />)}
      <Cell value={row.total_cents} name={`${name}, total`} alert={rowAlert} planned={showPlan && plan ? budgetOf(row) : null} onOpen={row.empty ? undefined : () => openRow(row, null)} />
      {compareCells(row.total_cents, previous?.costs[id]?.total_cents)}
    </tr>;
  }

  const exportCsv = () => {
    const head = ["Rubrica", ...periods.map(p => p.label), "Total", ...(compare ? ["Ano anterior", "Variação %"] : [])];
    const line = (name: string, values: Row, total: number, before?: number | null) => [name, ...keys.map(k => euros(values[k] || 0)), euros(total),
      ...(compare ? [previous ? euros(before || 0) : "", previous ? String(change(total, before) ?? "").replace(".", ",") : ""] : [])];
    const table: unknown[][] = [[`Proveitos, custos e margens por rubrica · ${day(start)} a ${day(end)}${filtering ? " · filtro ativo" : ""}`], head,
      line("Proveitos (vendas sem IVA)", data.revenue, kpis.revenue_cents, previous?.kpis.revenue_cents)];
    for (const [group, title] of GROUPS) {
      const list = groupRows(group);
      if (!list.length) continue;
      table.push(line(title, Object.fromEntries(keys.map(k => [k, sum(list, k)])), sum(list), filtering ? null : previous ? Object.values(previous.subtotals[group] || {}).reduce((a: number, b) => a + Number(b), 0) : null));
      list.forEach(r => table.push(line(`  ${r.parent_name ? `${r.parent_name} › ` : ""}${r.category_name}`, r.values, r.total_cents, previous?.costs[r.category_id || ""]?.total_cents)));
    }
    if (filtering) table.push(line("Total das rubricas filtradas", filteredCosts, filteredTotal));
    table.push(line("Total de custos", data.total_costs, kpis.costs_cents, previous?.kpis.costs_cents));
    table.push(line("Margem de gestão estimada", data.margin, kpis.margin_cents, previous?.kpis.margin_cents));
    table.push(["Margem %", ...keys.map(k => data.revenue[k] > 0 ? String(Math.round(data.margin[k] * 1000 / data.revenue[k]) / 10).replace(".", ",") : ""),
      kpis.margin_percent == null ? "" : String(kpis.margin_percent).replace(".", ",")]);
    table.push([], ["Fora dos custos (informativo)"], line("Pagamentos ao Estado (valor pago)", data.state_payments || {}, kpis.state_payments_cents));
    (data.outside || []).forEach((r: Row) => table.push(line(`  ${r.category_name}`, r.values, r.total_cents)));
    if (data.sales_vat) table.push(line("IVA liquidado nas vendas", data.sales_vat, keys.reduce((n, k) => n + (data.sales_vat[k] || 0), 0)));
    if (data.purchase_vat) {
      table.push(line("IVA suportado nas compras", data.purchase_vat.input, data.purchase_vat.totals.input_cents));
      table.push(line("IVA dedutível estimado", data.purchase_vat.deductible, data.purchase_vat.totals.deductible_cents));
      table.push(line("IVA não dedutível (incluído nos custos)", data.purchase_vat.non_deductible, data.purchase_vat.totals.non_deductible_cents));
    }
    downloadCsv(`custos-proveitos-${start}-${end}.csv`, table);
  };
  const print = () => {
    document.body.classList.add("mc-printing");
    const done = () => { document.body.classList.remove("mc-printing"); window.removeEventListener("afterprint", done); };
    window.addEventListener("afterprint", done);
    window.print();
  };

  const toolbar = <section className="panel mc-toolbar mc-no-print" aria-label="Filtros do mapa de custos e proveitos">
    <label className="mc-search"><Search size={15} aria-hidden="true" /><input aria-label="Pesquisar rubrica" placeholder="Pesquisar rubrica…" value={search} onChange={e => setSearch(e.target.value)} /></label>
    <label>Ano<select aria-label="Ano" value={year} onChange={e => setYearMonths(Number(e.target.value), fromMonth, toMonth)}>
      {Array.from({ length: 6 }, (_, i) => thisYear - i).concat(year > thisYear || year < thisYear - 5 ? [year] : []).map(y => <option key={y} value={y}>{y}</option>)}</select></label>
    <label>De<select aria-label="Mês inicial" value={fromMonth} onChange={e => setYearMonths(year, Number(e.target.value), Math.max(Number(e.target.value), toMonth))}>
      {MONTH_NAMES.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}</select></label>
    <label>Até<select aria-label="Mês final" value={toMonth} onChange={e => setYearMonths(year, Math.min(fromMonth, Number(e.target.value)), Number(e.target.value))}>
      {MONTH_NAMES.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}</select></label>
    <label>Tipo de rubrica<select value={type} onChange={e => setType(e.target.value)}>{TYPES.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>
    <label className="mc-check"><input type="checkbox" checked={compare} onChange={e => setCompare(e.target.checked)} />Comparar com o ano anterior</label>
    {plan && <label className="mc-check"><input type="checkbox" checked={budget} onChange={e => setBudget(e.target.checked)} />Comparar com o orçamento</label>}
    <label className="mc-check"><input type="checkbox" checked={showEmpty} onChange={e => setShowEmpty(e.target.checked)} />Rubricas sem movimentos</label>
    <ActionButton type="button" onClick={clear} disabled={!filtering && !showEmpty && !compare && !budget && !selected}><RotateCcw size={15} aria-hidden="true" />Limpar filtros</ActionButton>
  </section>;

  return <div className="mc">
    <div className="mc-print-only"><h2>Proveitos, custos e margens por rubrica</h2><p>{day(start)} a {day(end)}{filtering ? " · filtro ativo" : ""}</p></div>
    <section className="mc-kpis" aria-label="Indicadores do período">{cards.map(card => <article key={card.key} className={`mc-kpi tone-${card.tone}`}>
      <span className="mc-kpi-icon" aria-hidden="true">{card.icon}</span>
      <div className="mc-kpi-body">
        <span className="mc-kpi-label">{card.label}<Tip text={card.formula} label={card.label} /></span>
        <strong>{formatEuro(card.value || 0)}</strong>
        {card.sub && <small className="mc-kpi-sub">{card.sub}</small>}
        {!(card.key === "costs" && filtering) && <Delta current={card.value || 0} previous={previous ? card.prev : null} higherIsBetter={card.good} />}
      </div>
      {card.open && <button type="button" className="mc-kpi-hit" aria-label={card.openLabel} onClick={card.open} />}
    </article>)}</section>
    {toolbar}
    {!periods.length ? <section className="panel"><p>Sem vendas nem compras neste período.</p></section> : <>
      <div className="mc-bar mc-no-print">
        <div className="mc-switch" role="group" aria-label="Apresentação">
          <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}><Table2 size={16} aria-hidden="true" />Tabela</button>
          <button type="button" aria-pressed={view === "charts"} onClick={() => setView("charts")}><BarChart3 size={16} aria-hidden="true" />Gráficos</button>
        </div>
        <div className="mc-actions">
          <ActionButton type="button" onClick={exportCsv}><Download size={15} aria-hidden="true" />Exportar Excel</ActionButton>
          <ActionButton type="button" onClick={print}><Printer size={15} aria-hidden="true" />PDF / Imprimir</ActionButton>
        </div>
      </div>
      {compare && !previous && <Notice tone="info">Sem dados do mesmo período do ano anterior para comparar.</Notice>}
      {budget && plan && <p className="mc-hint">Orçamento: {plan.name} (versão {plan.version}, {plan.status_label.toLowerCase()}). Por baixo de cada valor das rubricas orçamentadas: orçamentado e desvio; no total de custos, o desvio só considera as rubricas orçamentadas.{plan.partial ? " Atenção: o período não começa ou não acaba num mês completo; o orçamento é do mês inteiro." : ""}</p>}
      {!plan && data.budget?.message && <p className="mc-hint mc-no-print">{data.budget.message}</p>}
      {alerts.length > 0 && <details className="mc-alerts mc-no-print">
        <summary><AlertTriangle size={15} aria-hidden="true" />{alerts.length} alerta(s) para rever <small>sinais para confirmar, não erros</small></summary>
        <ul>{alerts.map(alert => <li key={alert.id}><button type="button" onClick={() => openAlert(alert)}>
          <span className={`mc-alert-kind is-${alert.tone}`}>{ALERT_KINDS[alert.kind]}</span><span>{alert.message}</span></button></li>)}</ul>
        <ActionButton type="button" onClick={() => void loadSettings()}><Settings2 size={15} aria-hidden="true" />Limites dos alertas</ActionButton>
      </details>}
      {!alerts.length && <p className="mc-hint mc-no-print">Sem alertas com os limites atuais. <button type="button" className="mc-link" onClick={() => void loadSettings()}>Limites dos alertas</button></p>}

      {view === "table" ? <section className="panel mc-table-panel">
        <div className="mc-scroll" tabIndex={0} role="region" aria-label="Proveitos, custos e margens por rubrica (tabela com deslocamento)">
          <table className={`mc-table${compare ? " has-compare" : ""}`}>
            <thead><tr><th scope="col">Rubrica</th>{periods.map(p => <th key={p.key} scope="col" className="numeric" title={p.label}>{shortLabel(p.key)}</th>)}
              <th scope="col" className="numeric mc-total">Total</th>{compare && <><th scope="col" className="numeric mc-prev">Ano anterior</th><th scope="col" className="numeric mc-prev">Var.</th></>}</tr></thead>
            <tbody>
              <tr className="mc-revenue"><th scope="row"><span className="mc-rubric is-static"><span className="mc-mark tone-green" aria-hidden="true"><Coins size={14} /></span><span>Proveitos (vendas sem IVA)</span></span></th>
                {keys.map(key => <Cell key={key} value={data.revenue[key] || 0} name={`Proveitos, ${label(key)}`} onOpen={navigate ? () => openSales(key) : undefined} />)}
                <Cell value={kpis.revenue_cents} name="Proveitos, total" onOpen={navigate ? () => openSales(null) : undefined} />{compareCells(kpis.revenue_cents, previous?.kpis.revenue_cents)}</tr>
              {GROUPS.map(([group, title]) => {
                const list = groupRows(group);
                if (!list.length) return null;
                const closed = collapsed[group];
                return [<tr key={group} className="mc-group"><th scope="row"><button type="button" className="mc-rubric" title={title} aria-expanded={!closed} onClick={() => setCollapsed({ ...collapsed, [group]: !closed })}>
                  {closed ? <ChevronRight size={15} aria-hidden="true" /> : <ChevronDown size={15} aria-hidden="true" />}<span>{title}</span><small>{list.length} rubrica(s)</small></button></th>
                  {keys.map(key => <td key={key} className="numeric">{formatEuro(sum(list, key))}</td>)}<td className="numeric mc-total">{formatEuro(sum(list))}</td>
                  {compareCells(sum(list), filtering || !previous ? null : Object.values(previous.subtotals[group] || {}).reduce((a: number, b) => a + Number(b), 0))}</tr>,
                ...(closed ? [] : list.map(rubricRow))];
              })}
              {filtering && <tr className="mc-subtotal"><th scope="row">Total das rubricas filtradas</th>{keys.map(key => <td key={key} className="numeric">{formatEuro(filteredCosts[key])}</td>)}
                <td className="numeric mc-total">{formatEuro(filteredTotal)}</td>{compare && <><td className="mc-prev" /><td className="mc-prev" /></>}</tr>}
              <tr className="mc-sum"><th scope="row">Total de custos</th>{keys.map(key => <Cell key={key} value={data.total_costs[key] || 0} name={`Total de custos, ${label(key)}`} planned={showPlan && plan ? plan.totals[key] || 0 : null} planActual={planActual(key)} onOpen={() => openAll(key)} />)}
                <Cell value={kpis.costs_cents} name="Total de custos" planned={showPlan && plan ? Object.values(plan.totals).reduce((a: number, b) => a + Number(b), 0) : null} planActual={planActual()} onOpen={() => openAll(null)} />{compareCells(kpis.costs_cents, previous?.kpis.costs_cents)}</tr>
              <tr className="mc-sum mc-margin"><th scope="row">Margem de gestão estimada</th>{keys.map(key => <td key={key} className={`numeric${data.margin[key] < 0 ? " negative" : ""}`}>{formatEuro(data.margin[key])}</td>)}
                <td className={`numeric mc-total${kpis.margin_cents < 0 ? " negative" : ""}`}>{formatEuro(kpis.margin_cents)}</td>{compareCells(kpis.margin_cents, previous?.kpis.margin_cents)}</tr>
              <tr className="mc-percent"><th scope="row">Margem % (sobre proveitos)</th>{keys.map(key => <td key={key} className="numeric">{data.revenue[key] > 0 ? percent(Math.round(data.margin[key] * 1000 / data.revenue[key]) / 10) : "—"}</td>)}
                <td className="numeric mc-total">{percent(kpis.margin_percent)}</td>{compare && <><td className="numeric mc-prev">{percent(previous?.kpis.margin_percent)}</td><td className="mc-prev" /></>}</tr>
            </tbody>
            <tbody className="mc-info">
              <tr><th scope="rowgroup" colSpan={keys.length + 2 + (compare ? 2 : 0)}>Fora dos custos (informativo: não soma aos custos nem à margem)</th></tr>
              <tr><th scope="row"><span className="mc-rubric is-static"><span className="mc-mark tone-amber" aria-hidden="true"><Coins size={14} /></span><span>Pagamentos ao Estado (valor pago)</span></span></th>
                {keys.map(key => <Cell key={key} value={data.state_payments?.[key] || 0} name={`Pagamentos ao Estado, ${label(key)}`} onOpen={() => setOpen({ view: "fiscal", viewLabel: "Obrigações fiscais (pagamentos ao Estado)", ...range(key) })} />)}
                <Cell value={kpis.state_payments_cents || 0} name="Pagamentos ao Estado, total" onOpen={() => setOpen({ view: "fiscal", viewLabel: "Obrigações fiscais (pagamentos ao Estado)", start, end })} />{compare && <><td className="mc-prev" /><td className="mc-prev" /></>}</tr>
              {(data.outside || []).map((row: Row) => <tr key={row.category_id || "none"}><th scope="row"><span className="mc-rubric is-static"><RubricMark row={row} /><span>{row.category_name}</span>
                <span className="mc-tag">{row.kind === "fiscal" ? "obrigação fiscal" : row.kind === "financeiro" ? "movimento financeiro" : "fora dos custos"}</span></span></th>
                {keys.map(key => <Cell key={key} value={row.values[key] || 0} name={`${row.category_name}, ${label(key)}`} onOpen={() => openRow(row, key, "all", "Documentos e custos bancários da rubrica (fora dos custos)")} />)}
                <Cell value={row.total_cents} name={`${row.category_name}, total`} onOpen={() => openRow(row, null, "all", "Documentos e custos bancários da rubrica (fora dos custos)")} />{compare && <><td className="mc-prev" /><td className="mc-prev" /></>}</tr>)}
              {data.sales_vat && <tr><th scope="row">IVA liquidado nas vendas</th>{keys.map(key => <td key={key} className="numeric">{formatEuro(data.sales_vat[key] || 0)}</td>)}
                <td className="numeric mc-total">{formatEuro(keys.reduce((n, k) => n + (data.sales_vat[k] || 0), 0))}</td>{compare && <><td className="mc-prev" /><td className="mc-prev" /></>}</tr>}
              {data.purchase_vat && ([["input", "IVA suportado nas compras", "input_cents"], ["deductible", "IVA dedutível estimado", "deductible_cents"],
                ["non_deductible", data.regime === "normal" ? "IVA não dedutível (incluído nos custos)" : "IVA não dedutível (incluído nos custos: regime sem dedução)", "non_deductible_cents"]] as const).map(([key, text, total]) =>
                <tr key={key}><th scope="row">{text}</th>{keys.map(k => <td key={k} className="numeric">{formatEuro(data.purchase_vat[key][k])}</td>)}
                  <td className="numeric mc-total">{formatEuro(data.purchase_vat.totals[total])}</td>{compare && <><td className="mc-prev" /><td className="mc-prev" /></>}</tr>)}
            </tbody>
          </table>
        </div>
        <p className="mc-hint mc-no-print">Clique num valor para ver os movimentos que o compõem (consultar, corrigir a rubrica, observações e histórico). Os subtotais não voltam a somar no total.</p>
      </section> : charts()}
      {(data.notes || []).filter(Boolean).map((note: string) => <p key={note} className="footnote">{note}</p>)}
    </>}

    {open && <RubricMovements context={{ company: "Mapas de gestão", store, start: open.start, end: open.end, view: open.view, viewLabel: open.viewLabel }}
      category={open.category} categoryName={open.name} only={open.only} origin="mapas_de_gestao" onClose={() => setOpen(null)} onChanged={onChanged}
      onConsult={row => setDetail({ steps: [{ label: open.viewLabel, params: {} }, { label: row.label, params: { document_id: row.id }, expected: row.amount_cents }], start: open.start, end: open.end })}
      onReconcile={row => { setOpen(null); if (navigate) window.setTimeout(() => navigate("Reconciliação automática", { pesquisa: row.entity || row.label }), 0); }} />}
    {detail && <ExpenseDrilldown context={{ company: "Mapas de gestão", store, start: detail.start, end: detail.end }} initial={detail.steps} onClose={() => setDetail(null)} />}
    {(settings || settingsError) && <AppDialog className="mc-settings" aria-label="Limites dos alertas" guardChanges={false} onRequestClose={() => { setSettings(null); setSettingsError(""); }}>
      <header className="drilldown-header"><h2>Limites dos alertas</h2><p>Sinais para rever os movimentos; uma variação invulgar não é um erro.</p></header>
      {settingsError && <Notice tone="error">{settingsError}</Notice>}
      {settings && <form className="mc-settings-form" onSubmit={e => { e.preventDefault(); void saveSettings(); }}>
        {([["above_average_percent", "Acima da média dos restantes períodos (%)"], ["month_change_percent", "Subida face ao período anterior (%)"],
          ["increase_percent", "Aumento face ao mesmo período do ano anterior (%)"], ["duplicate_days", "Possíveis duplicados: mesmo valor e entidade até N dias (0 desliga)"]] as const).map(([key, text]) =>
          <label key={key}>{text}<input type="number" min={0} max={1000} value={settings[key]} disabled={!settings.can_edit} onChange={e => setSettings({ ...settings, [key]: e.target.value })} /></label>)}
        <label>Valor mínimo da diferença (€)<input inputMode="decimal" value={settings.minimum_euros ?? euros(settings.minimum_cents)} disabled={!settings.can_edit} onChange={e => setSettings({ ...settings, minimum_euros: e.target.value })} /></label>
        {settings.can_edit ? <div className="mc-actions"><ActionButton type="submit" className="primary" disabled={saving}>Guardar limites</ActionButton>
          <ActionButton type="button" onClick={() => setSettings(null)}>Cancelar</ActionButton></div> : <p className="mc-hint">Só o administrador altera os limites.</p>}
      </form>}
    </AppDialog>}
  </div>;

  function charts() {
    const labels = keys.map(shortLabel);
    const costsSeries = keys.map(k => filtering ? filteredCosts[k] : data.total_costs[k] || 0);
    const costsName = filtering ? "Custos (rubricas filtradas)" : "Custos";
    const ranked = [...rows].filter(r => r.total_cents).sort((a, b) => b.total_cents - a.total_cents);
    const positive = ranked.reduce((n, r) => n + Math.max(0, r.total_cents), 0);
    const nameOf = (r: Row) => r.parent_name ? `${r.parent_name} › ${r.category_name}` : r.category_name;
    return <section className="mc-charts" aria-label="Gráficos">
      <ChartFrame title="Proveitos e custos por período" legend={<Legend series={[{ key: "r", label: "Proveitos (sem IVA)", form: "bar" }, { key: "c", label: costsName, form: "bar" }]} />}
        table={{ columns: ["Período", "Proveitos", costsName], rows: keys.map((k, i) => [label(k), formatEuro(data.revenue[k] || 0), formatEuro(costsSeries[i])]) }}
        footnote="Clique numa coluna para ver os movimentos dos custos desse período.">
        <ColumnsChart ariaLabel="Proveitos e custos por período" labels={labels} series={[{ key: "r", label: "Proveitos", form: "bar" }, { key: "c", label: costsName, form: "bar" }]}
          values={[keys.map(k => data.revenue[k] || 0), costsSeries]} onSelect={i => openAll(keys[i], filtering ? { categories: visibleCategories, label: "Rubricas filtradas" } : undefined)} />
      </ChartFrame>
      <ChartFrame title="Margem de gestão estimada" legend={previous ? <Legend series={[{ key: "m", label: "Período atual", form: "bar" }, { key: "p", label: "Ano anterior", form: "line", dashed: true }]} /> : undefined}
        table={{ columns: ["Período", "Margem", ...(previous ? ["Ano anterior"] : [])], rows: keys.map(k => [label(k), formatEuro(data.margin[k]), ...(previous ? [formatEuro(previous.margin[k] || 0)] : [])]) }}
        footnote="Proveitos − custos do mapa (indicativa; não é o resultado contabilístico).">
        <ColumnsChart ariaLabel="Margem de gestão estimada por período" labels={labels} series={[{ key: "m", label: "Margem", form: "bar" }, ...(previous ? [{ key: "p", label: "Ano anterior", form: "line" as const, dashed: true }] : [])]}
          values={[keys.map(k => data.margin[k] || 0), ...(previous ? [keys.map(k => previous.margin[k] ?? null)] : [])]} onSelect={i => openAll(keys[i])} />
      </ChartFrame>
      {previous && <ChartFrame title="Custos: período atual e ano anterior" legend={<Legend series={[{ key: "c", label: "Período atual", form: "bar" }, { key: "p", label: "Ano anterior", form: "line", dashed: true }]} />}
        table={{ columns: ["Período", "Atual", "Ano anterior", "Variação"], rows: keys.map(k => [label(k), formatEuro(data.total_costs[k] || 0), formatEuro(previous.total_costs[k] || 0), percent(change(data.total_costs[k] || 0, previous.total_costs[k]))]) }}>
        <ColumnsChart ariaLabel="Custos do período atual e do mesmo período do ano anterior" labels={labels} series={[{ key: "c", label: "Atual", form: "bar" }, { key: "p", label: "Ano anterior", form: "line", dashed: true }]}
          values={[keys.map(k => data.total_costs[k] || 0), keys.map(k => previous.total_costs[k] ?? null)]} onSelect={i => openAll(keys[i])} />
      </ChartFrame>}
      <ChartFrame title="Despesas por rubrica" table={{ columns: ["Rubrica", "Total", ...(previous ? ["Ano anterior"] : [])], rows: ranked.map(r => [nameOf(r), formatEuro(r.total_cents), ...(previous ? [formatEuro(previous.costs[r.category_id || ""]?.total_cents || 0)] : [])]) }}
        footnote="Clique numa rubrica para ver os movimentos.">
        <BarList ariaLabel="Despesas por rubrica no período" rows={ranked.map(r => ({ label: nameOf(r), value: r.total_cents, onOpen: () => openRow(r, null),
          note: previous ? `ano anterior ${formatEuro(previous.costs[r.category_id || ""]?.total_cents || 0)}` : undefined }))} />
      </ChartFrame>
      <ChartFrame title="Peso de cada rubrica nos custos" table={{ columns: ["Rubrica", "Peso"], rows: ranked.map(r => [nameOf(r), percent(positive ? Math.round(Math.max(0, r.total_cents) * 1000 / positive) / 10 : 0)]) }}>
        <BarList ariaLabel="Distribuição percentual dos custos por rubrica" format={v => percent(v / 10)}
          rows={ranked.filter(r => r.total_cents > 0).map(r => ({ label: nameOf(r), value: positive ? Math.round(r.total_cents * 1000 / positive) : 0, note: formatEuro(r.total_cents), onOpen: () => openRow(r, null) }))} />
      </ChartFrame>
    </section>;
  }
}
