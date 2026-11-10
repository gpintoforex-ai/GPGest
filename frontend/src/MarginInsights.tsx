import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowDown, ArrowUp, CircleAlert, Layers, Lightbulb, ListChecks, PackageSearch, RefreshCw, TrendingUp, Wallet } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { BarList, ChartFrame, ColumnsChart, Legend } from "./DashboardCharts";
import { api } from "./lib/api";
import { formatQuantity } from "./lib/quantity";
import { formatEuro } from "./utils/format";
import "./MarginInsights.css";

/**
 * Margem bruta histórica e cobertura de custos (dashboard).
 * O custo de cada venda é o custo das mercadorias vendidas do stock (custo médio à data da venda);
 * a margem é sempre identificada como parcial quando há vendas de mercadorias sem custo conhecido.
 */

type Row = Record<string, any>;
const pct = (value: number | null | undefined) => value == null ? "—" : `${Number(value).toLocaleString("pt-PT", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} %`;
const LEVELS: Record<string, [BusinessTone, string]> = {
  complete: ["success", "Margem completa"], partial: ["warning", "Margem parcial"], critical: ["danger", "Margem parcial · cobertura baixa"],
};
export const marginTone = (level?: string | null): BusinessTone => (level && LEVELS[level]?.[0]) || "info";
const KINDS: Record<string, string> = { purchase: "Receção / compra", sale: "Venda", supplier_return: "Devolução ao fornecedor", customer_return: "Devolução de cliente",
  transfer_in: "Transferência (entrada)", transfer_out: "Transferência (saída)", loss: "Perda", expired: "Validade", adjustment: "Ajuste", opening: "Saldo inicial", valuation: "Ajuste de valorização" };
const SOURCE_LABELS: Record<string, string> = { stock: "Saída de stock", estimated: "Custo médio à data (estimado)", mixed: "Saída de stock e estimado" };
/** Ação recomendada por motivo: corrigir na origem, nunca alterar custos já gravados. */
const SUGGESTIONS: Record<string, [string, string, string]> = {
  no_cost: ["Registe a compra por uma receção na aplicação ou valorize o inventário inicial do artigo.", "Receções de mercadoria", "Abrir receções"],
  no_history: ["A primeira entrada com custo é posterior à venda: confirme a data da compra ou registe o inventário inicial à data certa.", "Artigos e validades", "Abrir artigos"],
  no_match: ["Associe o artigo Wintouch a uma ficha local (Artigos e validades) para ligar vendas e custos.", "Artigos e validades", "Abrir artigos"],
  unidentified: ["O artigo da venda não existe nos artigos sincronizados: sincronize os artigos da Wintouch.", "Artigos e validades", "Abrir artigos"],
  inactive: ["A ficha está inativa e sem custo: reative-a ou registe a compra.", "Artigos e validades", "Abrir artigos"],
  incomplete: ["Há entradas sem custo no stock deste artigo: complete o custo na receção ou no documento de origem.", "Movimentos de stock", "Abrir movimentos"],
  zero_cost: ["Custo zero pode ser uma oferta. Confirme na compra; não é necessariamente um erro.", "Movimentos de stock", "Abrir movimentos"],
};

export function CoverageBar({ percent, level, label = "Cobertura de custos" }: { percent: number | null | undefined; level?: string | null; label?: string }) {
  const value = Math.max(0, Math.min(100, Number(percent ?? 0)));
  return <div className="margin-coverage ui-tone" data-tone={marginTone(level)} role="progressbar" aria-label={label}
    aria-valuemin={0} aria-valuemax={100} aria-valuenow={Number(value.toFixed(1))} aria-valuetext={pct(percent)}>
    <span style={{ width: `${value}%` }} />
  </div>;
}

/** Conteúdo do cartão principal «Margem bruta»: margem, lucro, cobertura e acesso aos artigos sem custo. */
export function MarginSummary({ margin, onArticles, onReport }: { margin: Row; onArticles?: () => void; onReport?: () => void }) {
  const report = onReport && <ActionButton type="button" className="dash-link" onClick={onReport}>Ver custos e proveitos</ActionButton>;
  if (margin.cost_restricted) return <div className="margin-summary"><small>O seu perfil não permite ver custos de stock.</small>{report}</div>;
  const [tone, label] = LEVELS[margin.level] || ["info", "Sem vendas de mercadorias"];
  return <div className="margin-summary">
    <StatusBadge tone={tone} compact>{label}</StatusBadge>
    {margin.gross_margin_cents != null && <small>Lucro bruto <b>{formatEuro(margin.gross_margin_cents)}</b> sobre {formatEuro(margin.covered_cents)} de vendas com custo</small>}
    <CoverageBar percent={margin.cost_coverage_percent} level={margin.level} />
    <small>Cobertura de custos <b>{pct(margin.cost_coverage_percent)}</b>{margin.uncovered_cents ? <> · sem custo {formatEuro(margin.uncovered_cents)}</> : null}</small>
    <div className="margin-summary-links">
      {onArticles && <ActionButton type="button" className="dash-link" onClick={onArticles}>
        {margin.review_articles ? `Ver artigos sem custo (${margin.review_articles})` : "Ver artigos e custos"}</ActionButton>}
      {report}
    </div>
  </div>;
}

function Tile({ label, tone, icon: Icon, value, children }: { label: string; tone: BusinessTone; icon: typeof Layers; value: string; children?: ReactNode }) {
  return <article className="margin-tile ui-tone" data-tone={tone}>
    <span className="margin-tile-icon" aria-hidden="true"><Icon size={18} /></span>
    <div><span className="margin-tile-label">{label}</span><strong>{value}</strong>{children}</div>
  </article>;
}

/** Área «Margens e custos»: indicadores, motivos, evolução, margem por artigo e margem negativa. */
export function MarginPanel({ margin, onArticles, onReport }: { margin?: Row; onArticles: () => void; onReport?: () => void }) {
  if (!margin) return <p className="dash-loading" role="status">A calcular…</p>;
  if (margin.status !== "ok") return <p className="dash-muted">{margin.message || "Sem vendas por artigo neste período."}</p>;
  if (margin.cost_restricted) return <p className="dash-muted">O seu perfil não permite ver custos de stock.</p>;
  const tone = marginTone(margin.level);
  const uncoveredShare = margin.sales_cents > 0 ? 100 - Number(margin.cost_coverage_percent ?? 0) : null;
  const positive = (margin.article_margins || []).filter((a: Row) => a.margin_cents > 0);
  return <div className="margin-panel">
    <div className="margin-tiles" role="group" aria-label="Indicadores de margem">
      <Tile label="Margem bruta" tone={tone} icon={TrendingUp} value={pct(margin.gross_margin_percent)}>
        <small>{margin.partial ? "Parcial: só vendas com custo conhecido" : "Todas as vendas de mercadorias"}</small></Tile>
      <Tile label="Lucro bruto" tone="info" icon={Wallet} value={margin.gross_margin_cents == null ? "—" : formatEuro(margin.gross_margin_cents)}>
        <small>Sobre {formatEuro(margin.covered_cents)} de vendas</small></Tile>
      <Tile label="Cobertura de custos" tone={tone} icon={Layers} value={pct(margin.cost_coverage_percent)}>
        <CoverageBar percent={margin.cost_coverage_percent} level={margin.level} /></Tile>
      <Tile label="Vendas sem custo conhecido" tone={margin.uncovered_cents ? margin.level === "critical" ? "danger" : "warning" : "success"} icon={CircleAlert}
        value={formatEuro(margin.uncovered_cents)}><small>{pct(uncoveredShare)} das vendas de mercadorias</small></Tile>
      <Tile label="Artigos que necessitam de revisão" tone="suggestion" icon={ListChecks} value={String(margin.review_articles ?? 0)}>
        <ActionButton type="button" className="dash-link" onClick={onArticles}>Ver artigos sem custo</ActionButton></Tile>
    </div>
    {margin.partial && <Notice tone={margin.level === "critical" ? "error" : "warning"}>Margem parcial: calculada só sobre {formatEuro(margin.covered_cents)} de {formatEuro(margin.sales_cents)} de vendas de mercadorias. Não representa a margem global do período.</Notice>}
    {!!margin.incomplete_documents && <Notice tone="warning">{margin.incomplete_documents} documento(s) de venda sem linhas de artigo ficam fora desta análise.</Notice>}
    <p className="dash-muted">
      Custo das mercadorias vendidas: o custo gravado na saída de stock da venda (custo médio à data) ou, sem essa saída, o custo médio reconstituído à data da venda
      {margin.estimated_cents ? ` (${formatEuro(margin.estimated_cents)} de vendas com custo estimado)` : ""}. Nunca o último custo de compra.
      {margin.services_cents ? ` Serviços fora da margem: ${formatEuro(margin.services_cents)}.` : ""}
      {margin.zero_cost_cents ? ` ${formatEuro(margin.zero_cost_cents)} de vendas com custo zero (por exemplo, ofertas), assinaladas para revisão.` : ""}
    </p>
    {onReport && <ActionButton type="button" className="dash-link" onClick={onReport}>Ver mapa de custos e proveitos</ActionButton>}
    {!!margin.reasons?.length && <section className="margin-reasons" aria-label="Motivos da falta de custo">
      <h3>O que falta rever</h3>
      <ul>{margin.reasons.map((r: Row) => <li key={r.reason}><StatusBadge tone={r.reason === "zero_cost" ? "info" : "warning"} compact>{r.label}</StatusBadge>
        <span>{r.articles} artigo(s) · {formatEuro(r.sales_cents)}</span></li>)}</ul>
    </section>}
    <div className="margin-charts">
      {!!margin.points?.length && <ChartFrame title="Evolução da margem e da cobertura de custos"
        legend={<Legend series={[{ key: "m", label: "Margem bruta (%)", form: "bar" }, { key: "c", label: "Cobertura de custos (%)", form: "line" }]} />}
        table={{ columns: ["Período", "Vendas", "Com custo", "Margem", "Margem (%)", "Cobertura"], rows: margin.points.map((p: Row) => [p.label, formatEuro(p.sales_cents), formatEuro(p.covered_cents),
          p.margin_cents == null ? "—" : formatEuro(p.margin_cents), pct(p.margin_percent), pct(p.coverage_percent)]) }}
        footnote="Margem sobre as vendas com custo conhecido de cada período; a cobertura indica a parte das vendas de mercadorias com custo.">
        <ColumnsChart ariaLabel="Margem bruta e cobertura de custos por período" labels={margin.points.map((p: Row) => p.label)} formatValue={v => pct(v)}
          series={[{ key: "m", label: "Margem bruta (%)", form: "bar" }, { key: "c", label: "Cobertura de custos (%)", form: "line" }]}
          values={[margin.points.map((p: Row) => p.margin_percent), margin.points.map((p: Row) => p.coverage_percent)]} />
      </ChartFrame>}
      {!!positive.length && <ChartFrame title="Margem por artigo (maiores vendas com custo)"
        table={{ columns: ["Código", "Artigo", "Vendas com custo", "Margem", "Margem (%)"], rows: margin.article_margins.map((a: Row) => [a.code, a.name, formatEuro(a.covered_cents), formatEuro(a.margin_cents), pct(a.margin_percent)]) }}
        footnote="Lucro bruto de cada artigo no período, ao custo das mercadorias vendidas.">
        <BarList ariaLabel="Margem por artigo" rows={positive.map((a: Row) => ({ label: `${a.code} · ${a.name}`, value: a.margin_cents, note: `${pct(a.margin_percent)} de ${formatEuro(a.covered_cents)}` }))} />
      </ChartFrame>}
      <section className="margin-negative" aria-label="Artigos com margem negativa">
        <h3>Artigos com margem negativa</h3>
        {!margin.negative?.length ? <p className="dash-muted">Nenhum artigo vendido abaixo do custo neste período.</p>
          : <ul>{margin.negative.map((a: Row) => <li key={a.key}><span><span><b>{a.code}</b> · {a.name}</span><small>Vendas {formatEuro(a.sales_cents)}</small></span>
            <StatusBadge tone="danger" compact>{formatEuro(a.margin_cents)} ({pct(a.margin_percent)})</StatusBadge></li>)}</ul>}
      </section>
    </div>
  </div>;
}

type SortKey = "code" | "name" | "family" | "quantity" | "sales_cents" | "unit_cost" | "margin_percent" | "uncovered_cents";
const FILTERS: [string, string][] = [["review", "A rever"], ["missing", "Sem custo"], ["zero", "Custo zero"], ["negative", "Margem negativa"], ["estimated", "Custo estimado"], ["all", "Todos"]];

/** Grelha dos artigos vendidos com custo, origem, motivo e estado ERP; correção assistida por artigo. */
export function MarginArticles({ start, end, store, navigate, onClose, onRecalculate }: {
  start: string; end: string; store: string; navigate: (page: string) => void; onClose: () => void; onRecalculate: () => void;
}) {
  const [data, setData] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState(""), [filter, setFilter] = useState("review"), [reason, setReason] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "uncovered_cents", dir: -1 });
  const [open, setOpen] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    api(`/dashboard/margin-articles?${new URLSearchParams({ start, end, store_id: store })}`, undefined, { timeoutMs: 90_000 })
      .then(result => { if (active) setData(result); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [start, end, store, reload]);
  const rows = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("pt-PT");
    const list = (data?.rows || []).filter((r: Row) => {
      if (term && !`${r.code} ${r.name} ${r.family}`.toLocaleLowerCase("pt-PT").includes(term)) return false;
      if (reason && !r.reasons.includes(reason)) return false;
      if (filter === "review") return r.needs_review;
      if (filter === "missing") return r.uncovered_cents !== 0;
      if (filter === "zero") return r.reasons.includes("zero_cost");
      if (filter === "negative") return r.margin_cents != null && r.margin_cents < 0;
      if (filter === "estimated") return r.cost_source === "estimated" || r.cost_source === "mixed";
      return true;
    });
    const text = ["code", "name", "family"].includes(sort.key);
    const value = (r: Row) => text ? String(r[sort.key] ?? "") : r[sort.key] == null ? -Infinity : Number(r[sort.key]);
    return [...list].sort((a, b) => {
      const x = value(a), y = value(b);
      if (typeof x === "string" || typeof y === "string") return String(x).localeCompare(String(y), "pt-PT") * sort.dir;
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [data, search, filter, reason, sort]);
  const header = (key: SortKey, label: string, numeric = false) => <th className={numeric ? "numeric" : undefined}
    aria-sort={sort.key === key ? sort.dir === 1 ? "ascending" : "descending" : "none"}>
    <button type="button" className="margin-sort" onClick={() => setSort(s => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : numeric ? -1 : 1 }))}>
      {label}{sort.key === key && (sort.dir === 1 ? <ArrowUp size={13} aria-hidden="true" /> : <ArrowDown size={13} aria-hidden="true" />)}</button></th>;
  const reasons: Row = data?.reasons || {};
  const summary = data?.summary;
  return <AppDialog className="margin-articles-dialog" aria-label="Artigos vendidos e custos" guardChanges={false} onRequestClose={onClose}>
    <div className="margin-articles-head">
      <PackageSearch size={28} aria-hidden="true" />
      <div><h2>Artigos vendidos e custos</h2><p>Custo das mercadorias vendidas, origem do custo e motivo quando falta. Só consulta: nenhum custo nem documento é alterado aqui.</p></div>
    </div>
    {summary && <div className="margin-articles-summary">
      <StatusBadge tone={marginTone(summary.level)}>{LEVELS[summary.level]?.[1] || "Sem vendas de mercadorias"}</StatusBadge>
      <span>Margem {pct(summary.gross_margin_percent)} · lucro bruto {summary.gross_margin_cents == null ? "—" : formatEuro(summary.gross_margin_cents)}</span>
      <span>Cobertura {pct(summary.cost_coverage_percent)} · sem custo {formatEuro(summary.uncovered_cents)}</span>
      <CoverageBar percent={summary.cost_coverage_percent} level={summary.level} />
    </div>}
    <div className="margin-articles-filters filter-bar" role="group" aria-label="Filtrar artigos">
      <label>Pesquisar<input type="search" value={search} placeholder="Código, artigo ou família" onChange={e => setSearch(e.target.value)} /></label>
      <label>Mostrar<select value={filter} onChange={e => setFilter(e.target.value)}>{FILTERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      <label>Motivo<select value={reason} onChange={e => setReason(e.target.value)}><option value="">Todos</option>
        {Object.entries(reasons).map(([k, l]) => <option key={k} value={k}>{String(l)}</option>)}</select></label>
      <ActionButton type="button" onClick={() => { setReload(v => v + 1); onRecalculate(); }}><RefreshCw size={16} aria-hidden="true" />Recalcular indicadores</ActionButton>
    </div>
    {error ? <Notice tone="error">{error}</Notice> : !data ? <p role="status">A analisar os artigos vendidos…</p>
      : <div className="table-wrap"><table className="margin-articles-grid" aria-label="Artigos vendidos com custo e margem">
        <thead><tr>{header("code", "Código")}{header("name", "Descrição")}{header("family", "Família")}{header("quantity", "Qtd. vendida", true)}
          {header("sales_cents", "Vendas sem IVA", true)}{header("unit_cost", "Custo utilizado", true)}<th>Origem do custo</th>{header("margin_percent", "Margem", true)}
          <th>Motivo</th><th>Estado ERP</th><th>Ações</th></tr></thead>
        <tbody>{!rows.length && <tr><td colSpan={11}>Nenhum artigo com estes filtros.</td></tr>}
          {rows.map((r: Row) => <ArticleRow key={r.key} row={r} reasons={reasons} sources={data.sources} expanded={open === r.key}
            onToggle={() => setOpen(open === r.key ? null : r.key)} navigate={page => { onClose(); navigate(page); }} />)}</tbody>
      </table></div>}
    <div className="ui-dialog-actions"><ActionButton type="button" onClick={onClose}>Fechar</ActionButton></div>
  </AppDialog>;
}

function ArticleRow({ row, reasons, expanded, onToggle, navigate }: { row: Row; reasons: Row; sources: Row; expanded: boolean; onToggle: () => void; navigate: (page: string) => void }) {
  const reasonText = row.reasons.map((r: string) => reasons[r] || r).join("; ");
  return <>
    <tr className={row.needs_review ? "needs-review" : undefined}>
      <td>{row.code}</td><td>{row.name}</td><td>{row.family}</td>
      <td className="numeric">{formatQuantity(row.quantity)}</td><td className="numeric">{formatEuro(row.sales_cents)}
        {!!row.uncovered_cents && row.uncovered_cents !== row.sales_cents && <small>sem custo {formatEuro(row.uncovered_cents)}</small>}</td>
      <td className="numeric">{row.unit_cost == null ? "—" : `${Number(row.unit_cost).toLocaleString("pt-PT", { style: "currency", currency: "EUR", maximumFractionDigits: 4 })}`}</td>
      <td>{row.cost_source ? SOURCE_LABELS[row.cost_source] : "Sem custo"}</td>
      <td className="numeric">{row.margin_cents == null ? "—" : <StatusBadge compact tone={row.margin_cents < 0 ? "danger" : "success"}>{pct(row.margin_percent)}</StatusBadge>}</td>
      <td>{reasonText ? <StatusBadge compact tone={row.reasons.includes("zero_cost") && row.reasons.length === 1 ? "info" : "warning"}>{reasonText}</StatusBadge> : <StatusBadge compact tone="success">Custo conhecido</StatusBadge>}</td>
      <td>{row.erp_status}</td>
      <td><ActionButton type="button" aria-expanded={expanded} onClick={onToggle} disabled={!row.article_id && !row.reasons.length}>
        {expanded ? "Ocultar" : row.needs_review ? "Corrigir" : "Consultar"}</ActionButton></td>
    </tr>
    {expanded && <tr className="margin-assist-row"><td colSpan={11}><Assist row={row} navigate={navigate} /></td></tr>}
  </>;
}

/** Correção assistida: consulta das últimas compras, do custo médio e dos movimentos; ação recomendada na origem. */
function Assist({ row, navigate }: { row: Row; navigate: (page: string) => void }) {
  const [state, setState] = useState<{ details?: Row; history?: Row[]; moves?: Row[]; error?: string } | null>(null);
  useEffect(() => {
    if (!row.article_id) { setState({}); return; }
    let active = true;
    const id = encodeURIComponent(row.article_id);
    Promise.allSettled([api(`/stock/articles/${id}/details`), api(`/purchase-receiving/cost-history?article_ids=${id}`), api(`/stock/movements?article_id=${id}`)])
      .then(([details, history, moves]) => {
        if (!active) return;
        setState({
          details: details.status === "fulfilled" ? details.value.current : undefined,
          history: history.status === "fulfilled" ? history.value.articles?.[row.article_id] || [] : undefined,
          moves: moves.status === "fulfilled" ? moves.value.rows.slice(0, 8) : undefined,
          error: [details, history, moves].every(r => r.status === "rejected") ? "Consulta indisponível." : undefined,
        });
      });
    return () => { active = false; };
  }, [row.article_id]);
  const tips = row.reasons.map((r: string) => SUGGESTIONS[r]).filter(Boolean);
  const money4 = (v: string | null | undefined) => v == null ? "—" : Number(v).toLocaleString("pt-PT", { style: "currency", currency: "EUR", maximumFractionDigits: 4 });
  return <div className="margin-assist">
    {tips.map(([text, page, label]: string[]) => <div key={text} className="margin-tip ui-tone" data-tone="suggestion">
      <Lightbulb size={16} aria-hidden="true" /><span>{text}</span><ActionButton type="button" onClick={() => navigate(page)}>{label}</ActionButton></div>)}
    {!row.article_id ? <p className="dash-muted">Sem ficha local: não há histórico de compras nem movimentos a consultar.</p>
      : !state ? <p role="status">A consultar compras, custo médio e movimentos…</p>
      : state.error ? <Notice tone="warning">{state.error}</Notice>
      : <div className="margin-assist-grid">
        <section><h4>Custos de referência</h4><dl>
          <div><dt>Custo médio atual (stock)</dt><dd>{money4(state.details?.average_cost)}</dd></div>
          <div><dt>Último custo de compra</dt><dd>{money4(state.details?.last_cost)}</dd></div>
        </dl><small>Só consulta: o custo de vendas passadas não é substituído pelo último custo.</small></section>
        <section><h4>Últimas compras</h4>{!state.history ? <p className="dash-muted">Indisponível.</p> : !state.history.length ? <p className="dash-muted">Sem compras registadas.</p>
          : <table><thead><tr><th>Data</th><th>Documento</th><th className="numeric">Qtd.</th><th className="numeric">Referência</th><th className="numeric">Efetivo</th></tr></thead>
            <tbody>{state.history.map((h: Row, i: number) => <tr key={i}><td>{h.date?.split("-").reverse().join("/")}</td><td>{h.document || "—"}</td>
              <td className="numeric">{formatQuantity(h.quantity)}</td><td className="numeric">{money4(h.reference_cost)}</td><td className="numeric">{money4(h.unit_cost)}</td></tr>)}</tbody></table>}</section>
        <section><h4>Movimentos de stock recentes</h4>{!state.moves ? <p className="dash-muted">Indisponível.</p> : !state.moves.length ? <p className="dash-muted">Sem movimentos.</p>
          : <table><thead><tr><th>Data</th><th>Tipo</th><th className="numeric">Qtd.</th><th className="numeric">Custo</th></tr></thead>
            <tbody>{state.moves.map((m: Row) => <tr key={m.id}><td>{m.occurred_on?.split("-").reverse().join("/")}</td><td>{KINDS[m.kind] || m.kind}</td>
              <td className="numeric">{formatQuantity(m.quantity)}</td><td className="numeric">{money4(m.unit_cost)}</td></tr>)}</tbody></table>}
          <ActionButton type="button" className="dash-link" onClick={() => navigate("Movimentos de stock")}>Abrir movimentos de stock</ActionButton></section>
      </div>}
  </div>;
}
