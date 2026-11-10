import { useEffect, useMemo, useState } from "react";
import { CalendarRange, Link2, CheckCircle2, Download, FileWarning, MoreHorizontal, PiggyBank, Plus, Receipt, Scale, Target, Trash2, Wallet } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { ActionMenu, DetailDrawer, ModuleTabs, PageHeader, useWorkspaceTab } from "./components/WorkspaceUI";
import { BarList, ChartFrame, ColumnsChart, Legend } from "./DashboardCharts";
import { api } from "./lib/api";
import { centsInput, euroToCents, formatEuro } from "./utils/format";
import { LineLinks, Matching } from "./BudgetMatching";
import "./Budget.css";

/**
 * Orçamento e previsão de despesas: versões (rascunho, aprovado, revisto), despesas previstas com
 * recorrência, aumentos e exceções, e comparação com o realizado dos mapas de gestão. Só planeamento:
 * nada aqui cria pagamentos ou lançamentos.
 */

type Row = Record<string, any>;
const TABS = [
  { id: "visao", label: "Visão geral" }, { id: "rubricas", label: "Previsto vs realizado" },
  { id: "despesas", label: "Despesas previstas" }, { id: "associacoes", label: "Associações" }, { id: "centros", label: "Centros de custo" },
] as const;
const MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
const STATUS_TONE: Record<string, BusinessTone> = { rascunho: "warning", aprovado: "success", revisto: "neutral" };
const VAT_RATES = ["0", "6", "13", "23"];
const OCCURRENCE: Record<string, [string, BusinessTone]> = {
  previsto: ["Previsto", "neutral"], realizado: ["Realizado", "success"], parcial: ["Parcial", "info"], acima: ["Acima do previsto", "danger"], sem_documento: ["Sem documento", "warning"],
};
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
export const percentText = (value: number | null | undefined) =>
  value == null ? "n.a." : `${value > 0 ? "+" : ""}${value.toLocaleString("pt-PT", { maximumFractionDigits: 1 })} %`;

/** Estado da comparação: acima, abaixo (poupança só com o período fechado), em linha ou não prevista. */
function deviationState(state: string, closed: boolean): [string, BusinessTone] {
  if (state === "acima") return ["Acima do orçamento", "danger"];
  if (state === "abaixo") return closed ? ["Poupança (período fechado)", "success"] : ["Abaixo do orçamento", "info"];
  if (state === "nao_orcamentado") return ["Não prevista", "warning"];
  return ["Em linha", "success"];
}

export function linesCsv(lines: Row[]) {
  const euros = (cents: number) => centsInput(cents);
  const quote = (text: string) => `"${String(text ?? "").replace(/"/g, '""')}"`;
  const head = ["Rubrica", "Descrição", "Fornecedor", "Centro de custo", "Periodicidade", "Início", "Fim", "Valor sem IVA", "IVA %",
    ...MONTHS.map(m => m.slice(0, 3)), "Previsto anual (custo)", "Com IVA anual", "IVA dedutível anual", "Estado"];
  const rows = lines.map(l => [quote(l.category_name), quote(l.description), quote(l.entity_name), quote(l.cost_center), quote(l.periodicity_label),
    l.start_date, l.end_date || "", euros(l.amount_cents), l.vat_rate, ...l.months.map(euros), euros(l.annual_cents), euros(l.annual_gross_cents),
    euros(l.annual_deductible_cents), l.status === "ativa" ? "Ativa" : "Suspensa"]);
  return "﻿" + [head.map(quote), ...rows].map(r => r.join(";")).join("\r\n");
}

export default function Budget({ stores }: { stores: Row[] }) {
  const [tab, setTab] = useWorkspaceTab("orcamento", "visao", TABS.map(t => t.id));
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear), [scopeId, setScopeId] = useState(""), [month, setMonth] = useState(0);
  const [budgetId, setBudgetId] = useState("");
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [version, setVersion] = useState(0);
  const [editing, setEditing] = useState<Row | null>(null);
  const [versionAction, setVersionAction] = useState<"approve" | "revise" | null>(null);
  const [rubricFilter, setRubricFilter] = useState("");
  const refresh = () => setVersion(v => v + 1);

  useEffect(() => {
    let active = true;
    setError("");
    const query = new URLSearchParams({ year: String(year), store_id: scopeId, ...(month ? { month: String(month) } : {}), ...(budgetId ? { budget_id: budgetId } : {}) });
    api(`/budgets/overview?${query}`).then(r => { if (active) setData(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [year, scopeId, month, budgetId, version]);

  const budget = data?.budget;
  const draft = budget?.status === "rascunho";
  const kpis = data?.kpis;
  const monthName = data ? MONTHS[data.month - 1] : "";
  const hasDraft = (data?.versions || []).some((v: Row) => v.status === "rascunho");
  const years = Array.from({ length: 6 }, (_, i) => thisYear + 1 - i);

  async function act(action: "approve" | "revise", reason: string) {
    try {
      const result = await api(`/budgets/${encodeURIComponent(budget.id)}/${action}`, { revision: budget.revision, reason });
      setVersionAction(null);
      setNotice(action === "approve" ? "Orçamento aprovado." : `Revisão criada (versão ${result.version}, em rascunho).`);
      setBudgetId(action === "revise" ? result.id : "");
      refresh();
    } catch (e) { setError((e as Error).message); }
  }
  async function removeLine(line: Row) {
    if (!window.confirm(`Remover a despesa prevista «${line.description}» deste rascunho?`)) return;
    try {
      await api(`/budget-lines/${encodeURIComponent(line.id)}?revision=${line.revision}`, {}, { method: "DELETE" });
      setNotice("Despesa prevista removida."); refresh();
    } catch (e) { setError((e as Error).message); }
  }
  function exportCsv() {
    const blob = new Blob([linesCsv(data?.lines || [])], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `orcamento-${year}${budget ? `-v${budget.version}` : ""}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  return <div className="bud">
    <PageHeader title="Orçamento e previsão de despesas" description="Planeie as despesas do ano, compare o previsto com o realizado e acompanhe os desvios. Não cria pagamentos nem lançamentos."
      actions={draft ? <ActionButton type="button" className="primary" onClick={() => setEditing({})}><Plus size={16} aria-hidden="true" />Nova despesa prevista</ActionButton> : undefined} />
    {error && <Notice tone="error" onDismiss={() => setError("")}>{error}</Notice>}
    {notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    <div className="bud-toolbar filter-bar" role="group" aria-label="Orçamento a apresentar">
      <label>Ano<select value={year} onChange={e => { setYear(Number(e.target.value)); setBudgetId(""); setMonth(0); }}>
        {years.map(y => <option key={y} value={y}>{y}</option>)}</select></label>
      <label>Âmbito<select value={scopeId} onChange={e => { setScopeId(e.target.value); setBudgetId(""); }}>
        <option value="">Empresa</option>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label>Mês de análise<select value={data?.month || ""} onChange={e => setMonth(Number(e.target.value))}>
        {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m[0].toUpperCase() + m.slice(1)}</option>)}</select></label>
      {!!data?.versions?.length && <label>Versão<select value={budget?.id || ""} onChange={e => setBudgetId(e.target.value)}>
        {data.versions.map((v: Row) => <option key={v.id} value={v.id}>Versão {v.version} — {v.status_label}</option>)}</select></label>}
    </div>
    {!data ? !error && <p role="status">A carregar orçamento…</p> : !budget ? <CreateBudget data={data} year={year} store={scopeId} onCreated={() => { setNotice("Orçamento criado em rascunho."); refresh(); }} onError={setError} />
      : <>
        <section className="bud-version" aria-label="Versão do orçamento">
          <div><span className="bud-version-title"><strong>{budget.name}</strong><StatusBadge tone={STATUS_TONE[budget.status]} compact>{`Versão ${budget.version} · ${budget.status_label}`}</StatusBadge></span>
            <small>{budget.status === "aprovado" && budget.approved_at ? `Aprovado por ${budget.approved_by || "—"} em ${fullDate(budget.approved_at)}` : draft ? "Rascunho: pode editar as despesas previstas antes de aprovar." : "Versão substituída por uma revisão aprovada (histórico)."}
              {budget.note ? ` · ${budget.note}` : ""}</small></div>
          <div className="bud-version-actions">
            {draft && <ActionButton type="button" className="primary" onClick={() => setVersionAction("approve")}><CheckCircle2 size={16} aria-hidden="true" />Aprovar</ActionButton>}
            {budget.status === "aprovado" && !hasDraft && <ActionButton type="button" onClick={() => setVersionAction("revise")}>Criar revisão</ActionButton>}
            <ActionButton type="button" onClick={exportCsv} disabled={!data.lines.length}><Download size={16} aria-hidden="true" />Exportar CSV</ActionButton>
          </div>
        </section>
        <div className="bud-kpis" role="group" aria-label="Indicadores do orçamento">
          <Kpi tone="suggestion" icon={Target} label="Orçamento anual" value={formatEuro(kpis.annual_budget_cents)} note={`${kpis.lines} despesa(s) prevista(s)`} onOpen={() => setTab("despesas")} />
          <Kpi tone="info" icon={CalendarRange} label={`Previsto até ${monthName}`} value={formatEuro(kpis.period_budget_cents)} note={`${formatEuro(kpis.month_budget_cents)} em ${monthName}`} onOpen={() => setTab("rubricas")} />
          <Kpi tone="accent" icon={Receipt} label={`Realizado até ${monthName}`} value={formatEuro(kpis.period_actual_cents)} note={`${formatEuro(kpis.month_actual_cents)} em ${monthName}`} onOpen={() => setTab("rubricas")} />
          <Kpi tone={kpis.deviation_cents > 0 ? "danger" : "success"} icon={Scale} label="Desvio acumulado" value={`${kpis.deviation_cents > 0 ? "+" : ""}${formatEuro(kpis.deviation_cents)}`}
            note={kpis.deviation_cents > 0 ? `${percentText(kpis.deviation_pct)} acima do orçamento` : kpis.deviation_cents < 0 ? `${percentText(kpis.deviation_pct)} · ${kpis.period_closed ? "poupança (período fechado)" : "abaixo, ainda sem fecho"}` : "em linha"}
            onOpen={() => { setRubricFilter(""); setTab("rubricas"); }} />
          <Kpi tone="warning" icon={FileWarning} label="Despesas não previstas" value={formatEuro(kpis.unbudgeted_cents)} note="Rubricas com realizado sem orçamento" onOpen={() => { setRubricFilter("nao_orcamentado"); setTab("rubricas"); }} />
          <Kpi tone={kpis.missing_documents ? "warning" : "success"} icon={Link2} label="Associado ao orçamento" value={formatEuro(kpis.linked_cents)}
            note={`pago ${formatEuro(kpis.paid_cents)} · por pagar ${formatEuro(kpis.unpaid_cents)}${kpis.missing_documents ? ` · ${kpis.missing_documents} previsto(s) sem documento` : ""}`}
            onOpen={() => setTab("associacoes")} />
          <Kpi tone="neutral" icon={Wallet} label="A pagar com IVA (ano)" value={formatEuro(kpis.annual_gross_cents)} note={`IVA dedutível previsto ${formatEuro(kpis.annual_deductible_cents)}`} onOpen={() => setTab("despesas")} />
        </div>
        <ModuleTabs label="Áreas do orçamento" tabs={TABS} value={tab} onChange={setTab} />
        {tab === "visao" ? <Overview data={data} onRubric={() => setTab("rubricas")} />
          : tab === "rubricas" ? <Rubrics data={data} filter={rubricFilter} onFilter={setRubricFilter} />
          : tab === "despesas" ? <Lines data={data} draft={draft} onOpen={setEditing} onRemove={line => void removeLine(line)} />
          : tab === "associacoes" ? <Matching budgetId={budget.id} onChanged={message => { setNotice(message); refresh(); }} />
          : <CostCenters centers={data.options.cost_centers} onChanged={message => { setNotice(message); refresh(); }} onError={setError} />}
      </>}
    <ul className="bud-notes">{(data?.notes || []).map((note: string) => <li key={note}>{note}</li>)}</ul>
    {editing && budget && <LineDrawer value={editing} budget={budget} data={data!} readOnly={!draft} onClose={() => setEditing(null)}
      onSaved={message => { setEditing(null); setNotice(message); refresh(); }} />}
    {versionAction && budget && <VersionDialog action={versionAction} budget={budget} onClose={() => setVersionAction(null)} onConfirm={reason => void act(versionAction, reason)} />}
  </div>;
}

function Kpi({ tone, icon: Icon, label, value, note, onOpen }: { tone: BusinessTone; icon: typeof Target; label: string; value: string; note?: string; onOpen: () => void }) {
  return <ActionButton type="button" className="bud-kpi ui-tone" data-tone={tone} inferAction={false} onClick={onOpen} aria-label={`${label}: ${value}. Ver detalhe`}>
    <span className="bud-kpi-icon" aria-hidden="true"><Icon size={22} /></span>
    <span className="bud-kpi-text"><span>{label}</span><strong>{value}</strong>{note && <small>{note}</small>}</span>
  </ActionButton>;
}

function CreateBudget({ data, year, store, onCreated, onError }: { data: Row; year: number; store: string; onCreated: () => void; onError: (m: string) => void }) {
  const [form, setForm] = useState({ name: "", copy_from_id: "", update_percent: "0", note: "" });
  const [busy, setBusy] = useState(false);
  const sources: Row[] = data.copy_sources || [];
  async function create() {
    setBusy(true);
    try {
      await api("/budgets", { year, store_id: store || null, name: form.name, note: form.note, copy_from_id: form.copy_from_id || null, update_percent: form.update_percent || "0" });
      onCreated();
    } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="bud-card bud-empty" aria-label="Criar orçamento">
    <h2><PiggyBank size={20} aria-hidden="true" />Sem orçamento de {year} {store ? "para este estabelecimento" : "da empresa"}</h2>
    <p>Crie o orçamento em rascunho, de raiz ou a partir de um orçamento anterior com uma percentagem de atualização. Depois de aprovado fica bloqueado; as alterações fazem-se por revisão, com histórico.</p>
    <div className="bud-form">
      <label>Nome<input value={form.name} maxLength={120} placeholder={`Orçamento ${year}`} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
      <label>Copiar de<select value={form.copy_from_id} onChange={e => setForm({ ...form, copy_from_id: e.target.value })}>
        <option value="">Começar vazio</option>{sources.map(s => <option key={s.id} value={s.id}>{s.year} — versão {s.version} ({s.status_label})</option>)}</select></label>
      {form.copy_from_id && <label>Atualização dos valores (%)<input type="number" step="0.01" min="-100" max="1000" value={form.update_percent} onChange={e => setForm({ ...form, update_percent: e.target.value })} /></label>}
      <label className="bud-wide">Observações<input value={form.note} maxLength={2000} onChange={e => setForm({ ...form, note: e.target.value })} /></label>
    </div>
    {data.rubrics.length > 0 && <p className="dash-muted">Já há {formatEuro(data.kpis.period_actual_cents)} de despesas realizadas em {year} até {MONTHS[data.month - 1]}; aparecem como «não previstas» até as orçamentar.</p>}
    <div className="ui-dialog-actions"><ActionButton type="button" className="primary" disabled={busy} onClick={() => void create()}>Criar orçamento de {year}</ActionButton></div>
  </section>;
}

function Overview({ data, onRubric }: { data: Row; onRubric: () => void }) {
  const labels = data.months.map((m: Row) => m.label);
  const months: Row[] = data.months;
  const top = data.rubrics.filter((r: Row) => r.annual_budget_cents || r.ytd_actual_cents).slice(0, 8);
  return <div className="bud-overview">
    <section className="bud-card bud-wide-card">
      <ChartFrame title="Previsto vs realizado por mês" legend={<Legend series={[{ key: "b", label: "Previsto", form: "bar" }, { key: "a", label: "Realizado", form: "bar" }]} />}
        table={{ columns: ["Mês", "Previsto", "Realizado", "Desvio", "Desvio %"], rows: months.map(m => [m.label, formatEuro(m.budget_cents), m.actual_cents == null ? "—" : formatEuro(m.actual_cents),
          m.deviation_cents == null ? "—" : formatEuro(m.deviation_cents), m.deviation_cents == null ? "—" : percentText(m.deviation_pct)]) }}
        footnote="Custo sem IVA dedutível, pela data do documento. Meses depois do mês de análise ficam sem realizado.">
        <ColumnsChart labels={labels} ariaLabel="Previsto e realizado por mês" series={[{ key: "b", label: "Previsto", form: "bar" }, { key: "a", label: "Realizado", form: "bar" }]}
          values={[months.map(m => m.budget_cents), months.map(m => m.actual_cents)]} />
      </ChartFrame>
    </section>
    <section className="bud-card">
      <ChartFrame title="Acumulado no ano" legend={<Legend series={[{ key: "b", label: "Previsto acumulado", form: "line", dashed: true }, { key: "a", label: "Realizado acumulado", form: "line" }]} />}
        table={{ columns: ["Mês", "Previsto acumulado", "Realizado acumulado"], rows: months.map(m => [m.label, formatEuro(m.cumulative_budget_cents), m.cumulative_actual_cents == null ? "—" : formatEuro(m.cumulative_actual_cents)]) }}>
        <ColumnsChart labels={labels} ariaLabel="Previsto e realizado acumulados" series={[{ key: "b", label: "Previsto acumulado", form: "line", dashed: true }, { key: "a", label: "Realizado acumulado", form: "line" }]}
          values={[months.map(m => m.cumulative_budget_cents), months.map(m => m.cumulative_actual_cents)]} />
      </ChartFrame>
    </section>
    <section className="bud-card">
      <ChartFrame title="Evolução dos desvios" legend={<Legend series={[{ key: "d", label: "Desvio (realizado − previsto)", form: "bar" }]} />}
        table={{ columns: ["Mês", "Desvio", "Desvio %"], rows: months.filter(m => m.deviation_cents != null).map(m => [m.label, formatEuro(m.deviation_cents), percentText(m.deviation_pct)]) }}
        footnote="Positivo: acima do orçamento. Negativo: abaixo; só é poupança depois do fecho do período.">
        <ColumnsChart labels={labels} ariaLabel="Desvio mensal" series={[{ key: "d", label: "Desvio", form: "bar" }]} values={[months.map(m => m.deviation_cents)]} />
      </ChartFrame>
    </section>
    <section className="bud-card">
      <h2>Despesas por rubrica <small>orçamento anual</small></h2>
      {top.length ? <><BarList ariaLabel="Orçamento anual por rubrica" rows={top.map((r: Row) => ({ label: r.category_name, value: r.annual_budget_cents,
        note: `realizado ${formatEuro(r.ytd_actual_cents)}` }))} />
      <ActionButton type="button" className="bud-more" onClick={onRubric}>Ver previsto vs realizado por rubrica</ActionButton></> : <p className="dash-muted">Sem despesas previstas.</p>}
    </section>
    <section className="bud-card">
      <h2>Despesas por fornecedor <small>orçamento anual</small></h2>
      {data.suppliers.length ? <BarList ariaLabel="Orçamento anual por fornecedor" rows={data.suppliers.map((s: Row) => ({ label: s.name, value: s.annual_cents }))} />
        : <p className="dash-muted">Sem fornecedores indicados nas despesas previstas.</p>}
    </section>
  </div>;
}

function Rubrics({ data, filter, onFilter }: { data: Row; filter: string; onFilter: (f: string) => void }) {
  const closed = data.kpis.period_closed;
  const rows: Row[] = data.rubrics.filter((r: Row) => !filter || r.state === filter);
  const month = MONTHS[data.month - 1];
  return <section className="bud-card" aria-label="Previsto vs realizado por rubrica">
    <div className="bud-filters filter-bar" role="group" aria-label="Filtrar rubricas">
      <label>Situação<select value={filter} onChange={e => onFilter(e.target.value)}><option value="">Todas</option><option value="acima">Acima do orçamento</option>
        <option value="abaixo">Abaixo do orçamento</option><option value="em_linha">Em linha</option><option value="nao_orcamentado">Não previstas</option></select></label>
    </div>
    {!rows.length ? <p className="dash-muted">Sem rubricas nesta situação.</p> : <div className="table-wrap"><table className="bud-grid" aria-label="Rubricas">
      <thead><tr><th>Rubrica</th><th className="numeric">Previsto {month}</th><th className="numeric">Realizado {month}</th><th className="numeric">Desvio {month}</th>
        <th className="numeric">Previsto acumulado</th><th className="numeric">Realizado acumulado</th><th className="numeric">Desvio acumulado</th><th className="numeric">Orçamento anual</th><th>Situação</th></tr></thead>
      <tbody>{rows.map(r => {
        const [label, tone] = deviationState(r.state, closed);
        return <tr key={r.category_id || "none"}><td><strong>{r.category_name}</strong></td>
          <td className="numeric">{formatEuro(r.month_budget_cents)}</td><td className="numeric">{formatEuro(r.month_actual_cents)}</td>
          <td className="numeric">{formatEuro(r.month_deviation_cents)}<small>{percentText(r.month_deviation_pct)}</small></td>
          <td className="numeric">{formatEuro(r.ytd_budget_cents)}</td><td className="numeric">{formatEuro(r.ytd_actual_cents)}</td>
          <td className="numeric">{formatEuro(r.ytd_deviation_cents)}<small>{percentText(r.ytd_deviation_pct)}</small></td>
          <td className="numeric">{formatEuro(r.annual_budget_cents)}</td>
          <td><StatusBadge tone={tone} compact>{label}</StatusBadge></td></tr>;
      })}</tbody></table></div>}
    <p className="dash-muted">Desvio = realizado − orçamentado. Com orçamento a zero, a percentagem é «n.a.». A ligação de cada documento a uma despesa prevista chega na fase seguinte.</p>
  </section>;
}

function Lines({ data, draft, onOpen, onRemove }: { data: Row; draft: boolean; onOpen: (line: Row) => void; onRemove: (line: Row) => void }) {
  const [q, setQ] = useState(""), [category, setCategory] = useState(""), [sort, setSort] = useState("annual");
  const lines = useMemo(() => {
    const list = (data.lines as Row[]).filter(l => (!category || l.category_id === category)
      && (!q || `${l.description} ${l.entity_name} ${l.category_name} ${l.cost_center}`.toLowerCase().includes(q.toLowerCase())));
    const by: Record<string, (a: Row, b: Row) => number> = {
      annual: (a, b) => b.annual_cents - a.annual_cents, month: (a, b) => b.month_cents - a.month_cents,
      category: (a, b) => a.category_name.localeCompare(b.category_name, "pt"), description: (a, b) => a.description.localeCompare(b.description, "pt"),
    };
    return [...list].sort(by[sort]);
  }, [data.lines, q, category, sort]);
  const month = MONTHS[data.month - 1];
  return <section className="bud-card" aria-label="Despesas previstas">
    <div className="bud-filters filter-bar" role="group" aria-label="Filtrar despesas previstas">
      <label>Pesquisar<input type="search" value={q} placeholder="Descrição, fornecedor, rubrica" onChange={e => setQ(e.target.value)} /></label>
      <label>Rubrica<select value={category} onChange={e => setCategory(e.target.value)}><option value="">Todas</option>
        {data.options.categories.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>Ordenar<select value={sort} onChange={e => setSort(e.target.value)}><option value="annual">Previsto anual</option><option value="month">Previsto do mês</option>
        <option value="category">Rubrica</option><option value="description">Descrição</option></select></label>
    </div>
    {!data.lines.length ? <p className="dash-muted">{draft ? "Ainda sem despesas previstas. Use «Nova despesa prevista» para registar a primeira (renda, energia, seguros, comunicações…)." : "Sem despesas previstas."}</p>
      : !lines.length ? <p className="dash-muted">Sem despesas com estes filtros.</p>
      : <div className="table-wrap"><table className="bud-grid" aria-label="Grelha do orçamento">
        <thead><tr><th>Rubrica</th><th>Descrição</th><th>Fornecedor</th><th>Periodicidade</th><th className="numeric">Previsto {month}</th>
          <th className="numeric">Previsto anual</th><th className="numeric">Realizado (ano)</th><th className="numeric">Ainda previsto</th><th className="numeric">Com IVA (ano)</th><th>Estado</th><th>Ações</th></tr></thead>
        <tbody>{lines.map(l => <tr key={l.id}>
          <td>{l.category_name}{l.cost_center && <small>{l.cost_center}</small>}</td>
          <td><button type="button" className="bud-link" onClick={() => onOpen(l)}>{l.description}</button>
            <small>{formatEuro(l.amount_cents)} sem IVA · IVA {l.vat_rate.replace(".00", "")} %{l.increases.length ? ` · ${l.increases.length} aumento(s)` : ""}{l.exceptions.length ? ` · ${l.exceptions.length} exceção(ões)` : ""}</small></td>
          <td>{l.entity_name || "—"}</td><td>{l.periodicity_label}<small>{fullDate(l.start_date)}{l.end_date ? ` a ${fullDate(l.end_date)}` : ""}</small></td>
          <td className="numeric">{formatEuro(l.month_cents)}</td><td className="numeric">{formatEuro(l.annual_cents)}</td>
          <td className="numeric">{formatEuro(l.realized_cents)}{l.links > 0 && <small>{l.links} documento(s)</small>}</td>
          <td className="numeric">{formatEuro(l.remaining_cents)}{l.missing_documents > 0 && <small className="bud-missing">{l.missing_documents} sem documento</small>}</td>
          <td className="numeric">{formatEuro(l.annual_gross_cents)}</td>
          <td><StatusBadge tone={l.status === "ativa" ? "success" : "neutral"} compact>{l.status === "ativa" ? "Ativa" : "Suspensa"}</StatusBadge></td>
          <td><ActionMenu label={`Ações de ${l.description}`} icon={<MoreHorizontal size={18} aria-hidden="true" />}>
            <ActionButton type="button" onClick={() => onOpen(l)}>{draft ? "Editar" : "Consultar"}</ActionButton>
            {draft && <ActionButton type="button" onClick={() => onRemove(l)}><Trash2 size={15} aria-hidden="true" />Remover</ActionButton>}
          </ActionMenu></td></tr>)}</tbody>
        <tfoot><tr><th colSpan={4}>Total ({lines.length})</th><td className="numeric">{formatEuro(lines.reduce((s, l) => s + l.month_cents, 0))}</td>
          <td className="numeric">{formatEuro(lines.reduce((s, l) => s + l.annual_cents, 0))}</td><td className="numeric">{formatEuro(lines.reduce((s, l) => s + l.realized_cents, 0))}</td>
          <td className="numeric">{formatEuro(lines.reduce((s, l) => s + l.remaining_cents, 0))}</td><td className="numeric">{formatEuro(lines.reduce((s, l) => s + l.annual_gross_cents, 0))}</td><td colSpan={2} /></tr></tfoot>
      </table></div>}
  </section>;
}

function VersionDialog({ action, budget, onClose, onConfirm }: { action: "approve" | "revise"; budget: Row; onClose: () => void; onConfirm: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  const approve = action === "approve";
  return <AppDialog className="bud-dialog" aria-label={approve ? "Aprovar orçamento" : "Criar revisão"} onRequestClose={onClose}>
    <h2>{approve ? "Aprovar orçamento" : "Criar revisão"}</h2>
    <p>{approve ? `A versão ${budget.version} fica bloqueada e passa a ser o orçamento em vigor${budget.version > 1 ? "; a versão aprovada anterior fica como «revista», no histórico" : ""}.`
      : "É criada uma nova versão em rascunho com as mesmas despesas previstas. A versão aprovada continua em vigor até aprovar a revisão."}</p>
    <label>{approve ? "Nota (opcional)" : "Motivo da revisão"}<textarea rows={3} value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} /></label>
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Cancelar</ActionButton>
      <ActionButton type="button" className="primary" disabled={!approve && reason.trim().length < 3} onClick={() => onConfirm(reason.trim())}>{approve ? "Aprovar" : "Criar revisão"}</ActionButton></div>
  </AppDialog>;
}

function LineDrawer({ value, budget, data, readOnly, onClose, onSaved }: { value: Row; budget: Row; data: Row; readOnly: boolean; onClose: () => void; onSaved: (message: string) => void }) {
  const editing = !!value.id;
  const today = new Date();
  const defaultStart = budget.year === today.getFullYear() ? `${budget.year}-${String(today.getMonth() + 1).padStart(2, "0")}-01` : `${budget.year}-01-01`;
  const [form, setForm] = useState<Row>(() => editing ? {
    ...value, amount: centsInput(value.amount_cents), includes_vat: false, vat_rate: String(Number(value.vat_rate)), every_months: value.every_months || "",
    end_date: value.end_date || "", cost_center_id: value.cost_center_id || "", payment_days: String(value.payment_days),
    increases: value.increases.map((i: Row) => ({ from: i.from, percent: String(Number(i.percent)) })),
    exceptions: value.exceptions.map((e: Row) => ({ date: e.date, mode: e.skip ? "skip" : "amount", amount: centsInput(e.amount_cents) })),
  } : { description: "", category_id: "", entity_name: "", amount: "", includes_vat: false, vat_rate: "23", periodicity: "mensal", every_months: "", start_date: defaultStart,
    end_date: "", payment_days: "0", payment_account: "", cost_center_id: "", increases: [], exceptions: [], note: "", status: "ativa", revision: 0 });
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const set = (changes: Row) => setForm(current => ({ ...current, ...changes }));
  const cents = euroToCents(form.amount || "");
  const category = data.options.categories.find((c: Row) => c.id === form.category_id);
  const exceptionCents = (form.exceptions as Row[]).map(e => e.mode === "amount" ? euroToCents(e.amount || "") : null);
  const invalid = form.description.trim().length < 2 || !form.category_id || cents == null || Number.isNaN(cents) || !form.start_date
    || (form.periodicity === "personalizada" && !Number(form.every_months)) || (form.increases as Row[]).some(i => !i.from || i.percent === "")
    || (form.exceptions as Row[]).some((e, i) => !e.date || (e.mode === "amount" && (exceptionCents[i] == null || Number.isNaN(exceptionCents[i]))));
  async function save() {
    setBusy(true); setError("");
    try {
      const body = {
        revision: form.revision || 0, category_id: form.category_id, description: form.description, entity_name: form.entity_name || "",
        cost_center_id: form.cost_center_id || null, amount_cents: cents, amount_includes_vat: !!form.includes_vat, vat_rate: form.vat_rate || "0",
        periodicity: form.periodicity, every_months: form.periodicity === "personalizada" ? Number(form.every_months) : null,
        start_date: form.start_date, end_date: form.periodicity === "unica" ? null : form.end_date || null, payment_days: Number(form.payment_days) || 0,
        payment_account: form.payment_account || "", note: form.note || "", status: form.status,
        increases: (form.increases as Row[]).map(i => ({ from: i.from, percent: i.percent })),
        exceptions: (form.exceptions as Row[]).map((e, i) => e.mode === "skip" ? { date: e.date, skip: true } : { date: e.date, amount_cents: exceptionCents[i] }),
      };
      await api(editing ? `/budget-lines/${encodeURIComponent(value.id)}` : `/budgets/${encodeURIComponent(budget.id)}/lines`, body, editing ? { method: "PUT" } : undefined);
      onSaved(editing ? "Despesa prevista atualizada." : "Despesa prevista criada.");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const occurrences: Row[] = value.occurrences || [];
  const execution: Record<string, Row> = Object.fromEntries((value.occurrence_execution || []).map((o: Row) => [o.date, o]));
  return <DetailDrawer className="bud-drawer" aria-label={editing ? "Despesa prevista" : "Nova despesa prevista"} busy={busy} onRequestClose={onClose}>
    <h2>{editing ? form.description : "Nova despesa prevista"}</h2>
    {readOnly && <Notice tone="info">Orçamento {budget.status_label.toLowerCase()}: só consulta. Para alterar, crie uma revisão.</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    <fieldset className="bud-form" disabled={readOnly}>
      <label className="bud-wide">Descrição<input value={form.description} maxLength={200} placeholder="Por exemplo, renda da loja, eletricidade, seguro multirriscos" onChange={e => set({ description: e.target.value })} /></label>
      <label>Rubrica<select value={form.category_id} onChange={e => set({ category_id: e.target.value })}><option value="">Escolha a rubrica</option>
        {data.options.categories.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>Fornecedor (se conhecido)<input value={form.entity_name} maxLength={200} onChange={e => set({ entity_name: e.target.value })} /></label>
      <label>Valor por ocorrência (€)<input inputMode="decimal" value={form.amount} onChange={e => set({ amount: e.target.value })} /></label>
      <label>O valor indicado é<select value={form.includes_vat ? "com" : "sem"} onChange={e => set({ includes_vat: e.target.value === "com" })}>
        <option value="sem">Sem IVA</option><option value="com">Com IVA (converte para sem IVA)</option></select></label>
      <label>Taxa de IVA (%)<input list="bud-vat-rates" type="number" min="0" max="100" step="0.01" value={form.vat_rate} onChange={e => set({ vat_rate: e.target.value })} />
        <datalist id="bud-vat-rates">{VAT_RATES.map(r => <option key={r} value={r} />)}</datalist></label>
      <p className="bud-hint">{category ? `IVA dedutível previsto: ${category.vat_deduction_percent ?? 100} % (definido na rubrica). ` : ""}O orçamento compara o custo sem IVA dedutível; o valor com IVA entra na previsão de pagamentos.</p>
      <label>Periodicidade<select value={form.periodicity} onChange={e => set({ periodicity: e.target.value })}>
        {Object.entries(data.options.periodicities).map(([key, label]) => <option key={key} value={key}>{String(label)}</option>)}</select></label>
      {form.periodicity === "personalizada" && <label>De quantos em quantos meses<input type="number" min="1" max="120" value={form.every_months} onChange={e => set({ every_months: e.target.value })} /></label>}
      <label>{form.periodicity === "unica" ? "Data prevista da despesa" : "Primeira data da despesa"}<input type="date" value={form.start_date} onChange={e => set({ start_date: e.target.value })} /></label>
      {form.periodicity !== "unica" && <label>Termina em (opcional)<input type="date" value={form.end_date} onChange={e => set({ end_date: e.target.value })} /></label>}
      <label>Pagamento (dias após a despesa)<input type="number" min="0" max="365" value={form.payment_days} onChange={e => set({ payment_days: e.target.value })} /></label>
      <label>Conta ou caixa prevista<select value={form.payment_account} onChange={e => set({ payment_account: e.target.value })}><option value="">Por definir</option>
        {data.options.accounts.map((a: Row) => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label>Centro de custo<select value={form.cost_center_id} onChange={e => set({ cost_center_id: e.target.value })}><option value="">Sem centro de custo</option>
        {data.options.cost_centers.filter((c: Row) => c.active || c.id === form.cost_center_id).map((c: Row) => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}</select></label>
      <label>Estado<select value={form.status} onChange={e => set({ status: e.target.value })}><option value="ativa">Ativa</option><option value="suspensa">Suspensa (não conta)</option></select></label>
      <fieldset className="bud-wide"><legend>Aumentos previstos</legend>
        {(form.increases as Row[]).map((item, index) => <div key={index} className="bud-row">
          <label>A partir de<input type="date" value={item.from} onChange={e => set({ increases: form.increases.map((x: Row, i: number) => i === index ? { ...x, from: e.target.value } : x) })} /></label>
          <label>Aumento (%)<input type="number" step="0.01" min="-100" max="1000" value={item.percent} onChange={e => set({ increases: form.increases.map((x: Row, i: number) => i === index ? { ...x, percent: e.target.value } : x) })} /></label>
          <ActionButton type="button" aria-label="Remover aumento" onClick={() => set({ increases: form.increases.filter((_: Row, i: number) => i !== index) })}><Trash2 size={15} aria-hidden="true" /></ActionButton>
        </div>)}
        <ActionButton type="button" onClick={() => set({ increases: [...form.increases, { from: "", percent: "" }] })}><Plus size={15} aria-hidden="true" />Acrescentar aumento</ActionButton>
      </fieldset>
      <fieldset className="bud-wide"><legend>Exceções (uma ocorrência que não se realiza ou tem outro valor)</legend>
        {(form.exceptions as Row[]).map((item, index) => <div key={index} className="bud-row">
          <label>Data da ocorrência<input type="date" value={item.date} onChange={e => set({ exceptions: form.exceptions.map((x: Row, i: number) => i === index ? { ...x, date: e.target.value } : x) })} /></label>
          <label>Exceção<select value={item.mode} onChange={e => set({ exceptions: form.exceptions.map((x: Row, i: number) => i === index ? { ...x, mode: e.target.value } : x) })}>
            <option value="skip">Não se realiza</option><option value="amount">Outro valor (sem IVA)</option></select></label>
          {item.mode === "amount" && <label>Valor (€)<input inputMode="decimal" value={item.amount} onChange={e => set({ exceptions: form.exceptions.map((x: Row, i: number) => i === index ? { ...x, amount: e.target.value } : x) })} /></label>}
          <ActionButton type="button" aria-label="Remover exceção" onClick={() => set({ exceptions: form.exceptions.filter((_: Row, i: number) => i !== index) })}><Trash2 size={15} aria-hidden="true" /></ActionButton>
        </div>)}
        <ActionButton type="button" onClick={() => set({ exceptions: [...form.exceptions, { date: "", mode: "skip", amount: "" }] })}><Plus size={15} aria-hidden="true" />Acrescentar exceção</ActionButton>
      </fieldset>
      <label className="bud-wide">Observações<textarea rows={2} value={form.note} maxLength={2000} onChange={e => set({ note: e.target.value })} /></label>
    </fieldset>
    {editing && <section className="bud-history" aria-label={`Ocorrências previstas em ${budget.year}`}>
      <h3>Ocorrências previstas em {budget.year}</h3>
      {!occurrences.length ? <p className="dash-muted">Sem ocorrências neste ano.</p> : <div className="table-wrap"><table>
        <thead><tr><th>Data</th><th>Pagamento</th><th className="numeric">Sem IVA</th><th className="numeric">IVA</th><th className="numeric">IVA dedutível</th><th className="numeric">Com IVA</th><th className="numeric">Custo</th><th className="numeric">Associado</th><th>Situação</th></tr></thead>
        <tbody>{occurrences.map(o => { const run = execution[o.date] || {}; return <tr key={o.date}><td>{fullDate(o.date)}{o.exception && <small>Exceção</small>}</td><td>{fullDate(o.payment_date)}</td>
          <td className="numeric">{formatEuro(o.base)}</td><td className="numeric">{formatEuro(o.vat)}</td><td className="numeric">{formatEuro(o.deductible)}</td>
          <td className="numeric">{formatEuro(o.gross)}</td><td className="numeric">{formatEuro(o.cost)}</td><td className="numeric">{formatEuro(run.linked_cents || 0)}</td>
          <td>{run.state ? <StatusBadge tone={OCCURRENCE[run.state][1]} compact>{OCCURRENCE[run.state][0]}</StatusBadge> : "—"}</td></tr>; })}</tbody></table></div>}
    </section>}
    {editing && <LineLinks lineId={value.id} editable={budget.status !== "revisto"} onChanged={() => onSaved("Associação anulada.")} />}
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Fechar</ActionButton>
      {!readOnly && <ActionButton type="button" className="primary" disabled={busy || invalid} onClick={() => void save()}>{editing ? "Guardar alterações" : "Criar despesa prevista"}</ActionButton>}</div>
  </DetailDrawer>;
}

function CostCenters({ centers, onChanged, onError }: { centers: Row[]; onChanged: (message: string) => void; onError: (message: string) => void }) {
  const [form, setForm] = useState({ id: "", code: "", name: "", active: true });
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const body = { code: form.code, name: form.name, active: form.active };
      await api(form.id ? `/cost-centers/${encodeURIComponent(form.id)}` : "/cost-centers", body, form.id ? { method: "PUT" } : undefined);
      onChanged(form.id ? "Centro de custo atualizado." : "Centro de custo criado.");
      setForm({ id: "", code: "", name: "", active: true });
    } catch (e) { onError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="bud-card" aria-label="Centros de custo">
    <p className="dash-muted">Opcionais em cada despesa prevista, para agrupar o orçamento (por exemplo, loja, armazém, viaturas, administrativo).</p>
    <div className="bud-form bud-center-form" role="group" aria-label={form.id ? "Editar centro de custo" : "Novo centro de custo"}>
      <label>Código<input value={form.code} maxLength={20} onChange={e => setForm({ ...form, code: e.target.value })} /></label>
      <label>Nome<input value={form.name} maxLength={100} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
      <label className="checkbox"><input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} />Ativo</label>
      <div className="bud-row-actions">
        {form.id && <ActionButton type="button" onClick={() => setForm({ id: "", code: "", name: "", active: true })}>Cancelar</ActionButton>}
        <ActionButton type="button" className="primary" disabled={busy || !form.code.trim() || form.name.trim().length < 2} onClick={() => void save()}>{form.id ? "Guardar" : "Acrescentar"}</ActionButton>
      </div>
    </div>
    {!centers.length ? <p className="dash-muted">Ainda sem centros de custo.</p> : <div className="table-wrap"><table className="bud-grid" aria-label="Lista de centros de custo">
      <thead><tr><th>Código</th><th>Nome</th><th>Estado</th><th>Ações</th></tr></thead>
      <tbody>{centers.map(c => <tr key={c.id}><td>{c.code}</td><td>{c.name}</td>
        <td><StatusBadge tone={c.active ? "success" : "neutral"} compact>{c.active ? "Ativo" : "Inativo"}</StatusBadge></td>
        <td><ActionButton type="button" onClick={() => setForm({ id: c.id, code: c.code, name: c.name, active: c.active })}>Editar</ActionButton></td></tr>)}</tbody></table></div>}
  </section>;
}

