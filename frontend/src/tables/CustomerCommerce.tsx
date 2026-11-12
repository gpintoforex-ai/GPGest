import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { ArrowDownUp, FileText, Search } from "lucide-react";
import { StatusBadge, type BusinessTone } from "../components/BusinessUI";
import { ActionButton, Notice } from "../components/InteractionUI";
import type { DrilldownStep } from "../Drilldown";
import { api } from "../lib/api";
import Processing from "./Processing";
import "./CustomerCommerce.css";

/**
 * Ficha de cliente — consulta comercial (separadores Vendas e Documentos): artigos comprados e documentos, a partir
 * das vendas do SAF-T ou do ERP ligadas à ficha pelos identificadores (nunca pelo nome). Independente da consulta
 * financeira (conta corrente). O detalhe abre num único painel com histórico.
 */

type Row = Record<string, any>;
export type Period = { preset: string; start: string; end: string };
const SalesPanel = lazy(() => import("../SalesExplorer").then(module => ({ default: module.SalesPanel })));
const TONES: Record<string, BusinessTone> = { pago: "success", parcial: "info", pendente: "warning", vencido: "danger", anulado: "neutral",
  nota_credito: "accent", excluido: "neutral" };
const day = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const euro = (cents?: number | null) => cents == null ? "—" : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(cents / 100);
const quantity = (value?: string | null) => value == null ? "—" : Number(value).toLocaleString("pt-PT", { maximumFractionDigits: 3 });
const iso = (value: Date) => value.toISOString().slice(0, 10);
const fold = (text: string) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLocaleLowerCase("pt-PT");

export function periodOf(preset: string, start = "", end = ""): Period {
  const today = new Date(), year = today.getFullYear();
  if (preset === "ano") return { preset, start: `${year}-01-01`, end: iso(today) };
  if (preset === "ano_anterior") return { preset, start: `${year - 1}-01-01`, end: `${year - 1}-12-31` };
  if (preset === "tudo") return { preset, start: iso(new Date(today.getTime() - 3650 * 86_400_000)), end: iso(today) };
  if (preset === "personalizado") return { preset, start, end };
  return { preset: "12meses", start: iso(new Date(today.getTime() - 364 * 86_400_000)), end: iso(today) };
}

/** Dados comerciais da ficha (partilhados pelos separadores e pelo indicador de faturação). */
export function useCustomerCommerce(recordId: string | null, period: Period, enabled: boolean) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState("");
  useEffect(() => {
    if (!recordId || !enabled || !period.start || !period.end) return;
    let current = true;
    setData(null); setError("");
    api(`/sales/customer-sheet?${new URLSearchParams({ record_id: recordId, start: period.start, end: period.end })}`, undefined, { timeoutMs: 120_000 })
      .then(result => { if (current) setData(result); }).catch(e => { if (current) setError((e as Error).message); });
    return () => { current = false; };
  }, [recordId, period.start, period.end, enabled]);
  return { data, error };
}

export function PeriodPicker({ period, onChange }: { period: Period; onChange: (period: Period) => void }) {
  return <div className="cc-period" role="group" aria-label="Período das vendas">
    <label>Período<select aria-label="Período das vendas" value={period.preset} onChange={e => onChange(periodOf(e.target.value, period.start, period.end))}>
      <option value="12meses">Últimos 12 meses</option><option value="ano">Este ano</option><option value="ano_anterior">Ano anterior</option>
      <option value="tudo">Todo o histórico (10 anos)</option><option value="personalizado">Personalizado</option></select></label>
    {period.preset === "personalizado" && <>
      <label>Desde<input type="date" value={period.start} max={period.end} onChange={e => onChange({ ...period, start: e.target.value })} /></label>
      <label>Até<input type="date" value={period.end} min={period.start} onChange={e => onChange({ ...period, end: e.target.value })} /></label>
    </>}
  </div>;
}

function Unavailable({ data, error }: { data: Row | null; error: string }) {
  if (error) return <Notice tone="warning">{error}</Notice>;
  if (!data) return <Processing label="A consultar as vendas do cliente" />;
  return <Notice tone="info">{data.reason}</Notice>;
}

function Sortable({ label, field, sort, onSort, numeric }: { label: string; field: string; sort: { field: string; dir: number }; onSort: (field: string) => void; numeric?: boolean }) {
  return <th scope="col" className={numeric ? "numeric" : undefined} aria-sort={sort.field === field ? (sort.dir > 0 ? "ascending" : "descending") : "none"}>
    <button type="button" className="cc-sort" onClick={() => onSort(field)}>{label}<ArrowDownUp size={12} aria-hidden="true" /></button></th>;
}

function sortRows(rows: Row[], sort: { field: string; dir: number }) {
  const value = (row: Row) => { const v = row[sort.field]; return typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v; };
  return [...rows].sort((a, b) => {
    const x = value(a), y = value(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "pt-PT")) * sort.dir;
  });
}

type CommonProps = { recordId: string; data: Row | null; error: string; period: Period; onPeriod: (period: Period) => void;
  onLeave?: (page: string, params: Record<string, string>) => void };

export function CustomerSales({ recordId, data, error, period, onPeriod, onLeave }: CommonProps) {
  const [search, setSearch] = useState(""), [family, setFamily] = useState(""), [sort, setSort] = useState({ field: "net_cents", dir: -1 });
  const [panel, setPanel] = useState<DrilldownStep[] | null>(null);
  const rows = useMemo(() => sortRows((data?.articles || []).filter((a: Row) => (!family || a.family === family)
    && (!search.trim() || fold(`${a.code} ${a.description}`).includes(fold(search.trim())))), sort), [data, family, search, sort]);
  const onSort = (field: string) => setSort(current => ({ field, dir: current.field === field ? -current.dir : -1 }));
  if (!data?.available) return <><PeriodPicker period={period} onChange={onPeriod} /><Unavailable data={data} error={error} /></>;
  const k: Row = data.kpis;
  return <div className="cc">
    <div className="cc-kpis" role="group" aria-label="Indicadores de vendas do cliente">
      <div><span>Artigos distintos</span><strong>{k.articles}</strong></div>
      <div><span>Unidades vendidas</span><strong>{quantity(k.units)}</strong><small>faturadas − devolvidas</small></div>
      <div><span>Valor de vendas</span><strong>{euro(k.net_cents)}</strong><small>sem IVA · com IVA {euro(k.gross_cents)}</small></div>
      <div><span>Artigo mais comprado</span><strong className="cc-ellipsis" title={k.top_article?.description}>{k.top_article ? k.top_article.description || k.top_article.code : "—"}</strong>
        {k.top_article && <small>{quantity(k.top_article.quantity)} unidade(s)</small>}</div>
      <div><span>Última compra</span><strong>{day(k.last_date)}</strong></div>
    </div>
    <div className="cc-toolbar">
      <label className="cc-search"><Search size={15} aria-hidden="true" /><input aria-label="Pesquisar artigo por código ou descrição" placeholder="Código ou descrição do artigo…" value={search} onChange={e => setSearch(e.target.value)} /></label>
      {data.families.length > 0 && <label>Família<select aria-label="Família de artigos" value={family} onChange={e => setFamily(e.target.value)}><option value="">Todas</option>
        {data.families.map((f: string) => <option key={f} value={f}>{f}</option>)}</select></label>}
      <PeriodPicker period={period} onChange={onPeriod} />
    </div>
    {!rows.length ? <p className="record-sheet-empty">{data.articles.length ? "Nenhum artigo corresponde aos filtros." : "Sem vendas a este cliente no período."}</p>
      : <div className="cc-scroll"><table className="cc-table" aria-label="Artigos comprados pelo cliente">
        <thead><tr><Sortable label="Código" field="code" sort={sort} onSort={onSort} /><Sortable label="Descrição" field="description" sort={sort} onSort={onSort} />
          <th scope="col">Família</th><Sortable label="Quantidade" field="quantity" sort={sort} onSort={onSort} numeric />
          <Sortable label="Preço médio" field="average_price_cents" sort={sort} onSort={onSort} numeric /><Sortable label="Sem IVA" field="net_cents" sort={sort} onSort={onSort} numeric />
          <Sortable label="Com IVA" field="gross_cents" sort={sort} onSort={onSort} numeric /><Sortable label="1.ª venda" field="first_date" sort={sort} onSort={onSort} />
          <Sortable label="Última venda" field="last_date" sort={sort} onSort={onSort} /><Sortable label="Docs." field="documents" sort={sort} onSort={onSort} numeric /></tr></thead>
        <tbody>{rows.map(a => <tr key={a.key}>
          <td>{a.code || "—"}</td>
          <td><button type="button" className="cc-link" onClick={() => setPanel([{ label: `${a.code || "Sem código"} · ${a.description}`, params: { view: "customer_article", record_id: recordId, key: a.key } }])}>{a.description}</button>
            {a.service && <StatusBadge tone="neutral" compact>Serviço</StatusBadge>}{a.no_code && <StatusBadge tone="neutral" compact>Sem código</StatusBadge>}</td>
          <td>{a.family || "—"}</td>
          <td className="numeric">{quantity(a.quantity)}{a.returned !== "0" && <small>{quantity(a.sold)} faturadas · {quantity(a.returned)} devolvidas</small>}</td>
          <td className="numeric">{euro(a.average_price_cents)}</td><td className="numeric">{euro(a.net_cents)}</td><td className="numeric">{euro(a.gross_cents)}</td>
          <td>{day(a.first_date)}</td><td>{day(a.last_date)}</td><td className="numeric">{a.documents}</td></tr>)}</tbody>
      </table></div>}
    <p className="footnote">{data.notes[0]} {data.notes[1]}</p>
    {panel && <Suspense fallback={<Processing label="A abrir" />}><SalesPanel initial={panel} start={period.start} end={period.end} navigate={onLeave ? (page, params) => onLeave(page, params || {}) : undefined} onClose={() => setPanel(null)} /></Suspense>}
  </div>;
}

export function CustomerDocuments({ data, error, period, onPeriod, onLeave }: CommonProps) {
  const [search, setSearch] = useState(""), [type, setType] = useState(""), [state, setState] = useState(""), [sort, setSort] = useState({ field: "date", dir: -1 });
  const [panel, setPanel] = useState<{ steps: DrilldownStep[]; date: string } | null>(null);
  const rows = useMemo(() => sortRows((data?.documents || []).filter((d: Row) => {
    if (type && d.type !== type || state && d.state !== state) return false;
    const text = search.trim();
    if (!text) return true;
    const amount = Math.round(Number(text.replace(/[€\s.]/g, "").replace(",", ".")) * 100);
    return fold(`${d.document} ${d.type} ${d.type_label} ${day(d.date)}`).includes(fold(text)) || (!Number.isNaN(amount) && [d.gross_cents, d.net_cents].includes(amount));
  }), sort), [data, type, state, search, sort]);
  const onSort = (field: string) => setSort(current => ({ field, dir: current.field === field ? -current.dir : -1 }));
  if (!data?.available) return <><PeriodPicker period={period} onChange={onPeriod} /><Unavailable data={data} error={error} /></>;
  const types = [...new Set<string>(data.documents.map((d: Row) => d.type))].sort();
  const open = (d: Row) => setPanel({ steps: [{ label: `${d.type_label} ${d.document}`, params: { view: "document", id: d.id } }], date: d.date });
  return <div className="cc">
    <div className="cc-toolbar">
      <label className="cc-search"><Search size={15} aria-hidden="true" /><input aria-label="Pesquisar documento por número, tipo, data ou valor" placeholder="Número, tipo, data (dd/mm/aaaa) ou valor…" value={search} onChange={e => setSearch(e.target.value)} /></label>
      <label>Tipo<select aria-label="Tipo de documento" value={type} onChange={e => setType(e.target.value)}><option value="">Todos</option>{types.map(t => <option key={t} value={t}>{t}</option>)}</select></label>
      <label>Estado<select aria-label="Estado do documento" value={state} onChange={e => setState(e.target.value)}><option value="">Todos</option>
        <option value="pago">Pago</option><option value="parcial">Parcialmente pago</option><option value="pendente">Pendente</option><option value="vencido">Vencido</option>
        <option value="nota_credito">Notas de crédito</option><option value="anulado">Anulados</option></select></label>
      <PeriodPicker period={period} onChange={onPeriod} />
    </div>
    <p className="cc-totals" role="status">{data.documents.length} documento(s) · sem IVA <b>{euro(data.kpis.net_cents)}</b> · com IVA <b>{euro(data.kpis.gross_cents)}</b>
      {data.kpis.cancelled ? ` · ${data.kpis.cancelled} anulado(s) fora dos totais` : ""}</p>
    {!rows.length ? <p className="record-sheet-empty">{data.documents.length ? "Nenhum documento corresponde aos filtros." : "Sem documentos deste cliente no período."}</p>
      : <div className="cc-scroll"><table className="cc-table" aria-label="Documentos do cliente">
        <thead><tr><Sortable label="Data" field="date" sort={sort} onSort={onSort} /><Sortable label="Tipo" field="type" sort={sort} onSort={onSort} />
          <Sortable label="Número" field="document" sort={sort} onSort={onSort} /><th scope="col">Série</th>
          <Sortable label="Sem IVA" field="net_cents" sort={sort} onSort={onSort} numeric /><th scope="col" className="numeric">IVA</th>
          <Sortable label="Total" field="gross_cents" sort={sort} onSort={onSort} numeric /><th scope="col">Documento</th><th scope="col">Pagamento</th>
          <th scope="col">Origem</th><th scope="col"><span className="cc-sr">Ações</span></th></tr></thead>
        <tbody>{rows.map(d => <tr key={d.id} className={d.status !== "normal" ? "is-cancelled" : undefined}>
          <td>{day(d.date)}</td><td title={d.type_label}>{d.type}</td><td><button type="button" className="cc-link" onClick={() => open(d)}>{d.document}</button></td>
          <td>{d.series || "—"}</td><td className="numeric">{d.direction < 0 ? "−" : ""}{euro(d.net_cents)}</td><td className="numeric">{euro(d.tax_cents)}</td>
          <td className="numeric"><b>{d.direction < 0 ? "−" : ""}{euro(d.gross_cents)}</b></td>
          <td>{d.status === "normal" ? (d.direction < 0 ? <StatusBadge tone="accent" compact>Nota de crédito</StatusBadge> : <StatusBadge tone="neutral" compact>Normal</StatusBadge>)
            : <StatusBadge tone="neutral" compact>{d.status_label}</StatusBadge>}</td>
          <td>{d.payment_known ? <StatusBadge tone={TONES[d.state] || "neutral"} compact>{d.state_label}</StatusBadge> : <small className="cc-muted">Sem ligação financeira</small>}</td>
          <td>{d.origin_label}</td>
          <td><ActionButton type="button" className="cc-open" aria-label={`Abrir documento ${d.document}`} onClick={() => open(d)}><FileText size={15} aria-hidden="true" />Abrir</ActionButton></td>
        </tr>)}</tbody>
      </table></div>}
    {panel && <Suspense fallback={<Processing label="A abrir" />}><SalesPanel initial={panel.steps} start={panel.date} end={panel.date} navigate={onLeave ? (page, params) => onLeave(page, params || {}) : undefined} onClose={() => setPanel(null)} /></Suspense>}
  </div>;
}

