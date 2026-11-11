import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle, ArrowDownUp, Banknote, CalendarClock, ExternalLink, FileText, Hourglass, Package, Receipt, RotateCcw, Search, Ticket, Users, Wallet, X,
} from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, Notice } from "./components/InteractionUI";
import { BarList, ChartFrame, ColumnsChart, Legend } from "./DashboardCharts";
import { DocumentPreview, DrilldownPanel, type DrilldownStep } from "./Drilldown";
import { useLocationSearch } from "./components/WorkspaceUI";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";
import "./SalesExplorer.css";

/**
 * Análise comercial: quem comprou → o que comprou → em que documento → por quanto → quanto pagou → quanto falta
 * receber. Só consulta (nada aqui altera documentos). O detalhe abre num único painel com histórico (voltar e
 * percurso), e os filtros ficam guardados na sessão para regressar da ficha do artigo ou do cliente sem os perder.
 */

type Row = Record<string, any>;
type Tab = "documents" | "customers" | "articles" | "receipts";
type Filters = { tab: Tab; q: string; state: string; customer: string; customerName: string; article: string; month: string; sort: string; direction: "asc" | "desc" };
const STORAGE = "iazela-analise-comercial";
const DEFAULTS: Filters = { tab: "documents", q: "", state: "", customer: "", customerName: "", article: "", month: "", sort: "", direction: "desc" };
const STATE_TONES: Record<string, BusinessTone> = { pago: "success", parcial: "info", pendente: "warning", vencido: "danger", anulado: "neutral",
  nota_credito: "accent", excluido: "neutral" };
const STATE_OPTIONS: [string, string][] = [["", "Todos os estados"], ["em_divida", "Em dívida (pendente, parcial ou vencido)"], ["pago", "Pago"],
  ["parcial", "Parcialmente pago"], ["pendente", "Pendente"], ["vencido", "Vencido"], ["nota_credito", "Notas de crédito"], ["anulado", "Anulado"]];
const RECONCILIATION: Record<string, [string, BusinessTone]> = { reconciliado: ["Reconciliado", "success"], por_reconciliar: ["Por reconciliar", "warning"],
  caixa: ["Caixa", "info"] };
const MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
const day = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
const month = (key: string) => `${MONTHS[Number(key.slice(5, 7)) - 1]} ${key.slice(2, 4)}`;
const quantity = (value?: string | null) => value == null ? "—" : Number(value).toLocaleString("pt-PT", { maximumFractionDigits: 3 });
const price = (value?: string | null) => value == null ? "—" : Number(value).toLocaleString("pt-PT", { style: "currency", currency: "EUR", minimumFractionDigits: 2, maximumFractionDigits: 4 });
const euro = (cents?: number | null) => cents == null ? "—" : formatEuro(cents);

function load(): Filters {
  try { return { ...DEFAULTS, ...JSON.parse(sessionStorage.getItem(STORAGE) || "{}").filters }; } catch { return DEFAULTS; }
}
/** Percurso a repor depois de abrir a ficha do artigo ou do cliente (só uma vez, até 30 minutos depois). */
function resumeSteps(): DrilldownStep[] | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(STORAGE) || "{}");
    return value.resume && value.steps && Date.now() - value.at < 30 * 60_000 ? value.steps : null;
  } catch { return null; }
}
function persist(filters: Filters, steps: DrilldownStep[] | null, resume = false) {
  try { sessionStorage.setItem(STORAGE, JSON.stringify({ filters, steps, resume, at: Date.now() })); } catch { /* sem armazenamento: só não repõe */ }
}

export function StateBadge({ state, label }: { state: string; label: string }) {
  return <StatusBadge tone={STATE_TONES[state] || "neutral"} compact>{label}</StatusBadge>;
}

function Sortable({ label, field, filters, onSort, numeric }: { label: string; field: string; filters: Filters; onSort: (field: string) => void; numeric?: boolean }) {
  const active = filters.sort === field;
  return <th scope="col" className={numeric ? "numeric" : undefined} aria-sort={active ? (filters.direction === "asc" ? "ascending" : "descending") : "none"}>
    <button type="button" className="se-sort" onClick={() => onSort(field)}>{label}<ArrowDownUp size={12} aria-hidden="true" /></button></th>;
}

export default function SalesExplorer({ store, start, end, navigate }: {
  store: string; start: string; end: string; navigate?: (page: string, params?: Record<string, string>) => void;
}) {
  const [filters, setFilters] = useState<Filters>(load);
  const [overview, setOverview] = useState<Row | null>(null), [list, setList] = useState<Row | null>(null);
  const [error, setError] = useState(""), [offset, setOffset] = useState(0);
  const [panel, setPanel] = useState<DrilldownStep[] | null>(null);
  const [steps, setSteps] = useState<DrilldownStep[] | null>(null);
  const period = `${start}|${end}|${store}`;
  const location = useLocationSearch();
  useEffect(() => { if (panel || steps) persist(filters, steps); else persist(filters, null); }, [filters, steps]);
  // Regresso a esta página (a aplicação mantém as páginas visitadas): repõe o painel no mesmo ponto.
  useEffect(() => {
    if (new URLSearchParams(location).get("pagina") !== "Faturação e recebimentos") return;
    const resume = resumeSteps();
    if (resume) { setPanel(resume); setSteps(resume); persist(filters, resume); }
  }, [location]);
  useEffect(() => {
    let active = true;
    setOverview(null); setError("");
    api(`/sales/overview?${new URLSearchParams({ start, end, store_id: store })}`, undefined, { timeoutMs: 120_000 })
      .then(result => { if (active) setOverview(result); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [period]);
  const path = filters.tab === "documents" ? "documents" : filters.tab;
  useEffect(() => {
    let active = true;
    const params: Record<string, string> = { start, end, store_id: store, q: filters.q, offset: String(offset), limit: "50", direction: filters.direction, sort: filters.sort };
    if (filters.tab === "documents") Object.assign(params, { state: filters.state, customer: filters.customer, article: filters.article, month: filters.month });
    if (filters.tab === "articles") params.customer = filters.customer;
    setList(null);
    const timer = window.setTimeout(() => api(`/sales/${path}?${new URLSearchParams(params)}`, undefined, { timeoutMs: 120_000 })
      .then(result => { if (active) setList({ ...result, tab: filters.tab }); }).catch(e => { if (active) setError((e as Error).message); }), filters.q ? 250 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [period, filters, offset]);

  const update = (changes: Partial<Filters>) => { setOffset(0); setFilters(current => ({ ...current, ...changes })); };
  const sort = (field: string) => update({ sort: field, direction: filters.sort === field && filters.direction === "desc" ? "asc" : "desc" });
  const open = (step: DrilldownStep) => setPanel([step]);
  const openDocument = (row: Row) => open({ label: `${row.type_label || "Documento"} ${row.document}`, params: { view: "document", id: row.id } });
  const openCustomer = (key: string, name: string) => open({ label: name, params: { view: "customer", key } });
  const openArticle = (code: string, name: string) => open({ label: `${code} · ${name}`, params: { view: "article", code } });
  // A aplicação não muda de página com um diálogo aberto: guarda o percurso, fecha o painel e navega. Ao regressar,
  // o painel reabre no mesmo ponto (percurso guardado na sessão) e os filtros mantêm-se.
  const leave = (page: string, params: Record<string, string>) => {
    persist(filters, steps, true);
    setPanel(null);
    window.setTimeout(() => navigate?.(page, params), 0);
  };
  const k: Row = overview?.kpis || {};
  const filtering = filters.q || filters.state || filters.customer || filters.article || filters.month;
  const cards: { key: string; label: string; value: string; note: string; icon: ReactNode; tone: string; action: () => void; hint: string }[] = [
    { key: "net", label: "Faturação sem IVA", value: euro(k.net_cents), note: `${k.documents ?? 0} documento(s); notas de crédito subtraem`, icon: <Banknote size={18} />, tone: "green",
      action: () => update({ tab: "documents", state: "", month: "" }), hint: "Ver os documentos" },
    { key: "gross", label: "Faturação com IVA", value: euro(k.gross_cents), note: k.cancelled ? `${k.cancelled} anulado(s) fora dos totais` : "Documentos normais", icon: <FileText size={18} />,
      tone: "blue", action: () => update({ tab: "documents", state: "", month: "" }), hint: "Ver os documentos" },
    { key: "received", label: "Recebido", value: euro(k.received_cents), note: "Dos documentos do período", icon: <Wallet size={18} />, tone: "teal",
      action: () => update({ tab: "receipts" }), hint: "Ver os recebimentos" },
    { key: "pending", label: "Por receber", value: euro(k.pending_cents), note: "Pendente, parcial ou vencido", icon: <Hourglass size={18} />, tone: "amber",
      action: () => update({ tab: "documents", state: "em_divida" }), hint: "Ver os documentos em dívida" },
    { key: "overdue", label: "Vencido", value: euro(k.overdue_cents), note: "Só com data de vencimento", icon: <AlertTriangle size={18} />, tone: "red",
      action: () => update({ tab: "documents", state: "vencido" }), hint: "Ver os documentos vencidos" },
    { key: "documents", label: "Documentos emitidos", value: String(k.documents ?? 0), note: `${k.invoices ?? 0} fatura(s) · NC ${euro(k.credit_notes_cents)}`, icon: <Receipt size={18} />,
      tone: "slate", action: () => update({ tab: "documents", state: "" }), hint: "Ver os documentos" },
    { key: "customers", label: "Clientes com faturação", value: String(k.customers ?? 0), note: "No período", icon: <Users size={18} />, tone: "violet",
      action: () => update({ tab: "customers" }), hint: "Ver os clientes" },
    { key: "ticket", label: "Ticket médio", value: euro(k.average_ticket_cents), note: "Sem IVA, por fatura", icon: <Ticket size={18} />, tone: "sky",
      action: () => update({ tab: "documents", state: "", sort: "net_cents", direction: "desc" }), hint: "Ver os documentos por valor" },
    { key: "days", label: "Prazo médio de recebimento", value: k.average_receipt_days == null ? "—" : `${k.average_receipt_days.toLocaleString("pt-PT")} dias`,
      note: "Ponderado pelo valor recebido", icon: <CalendarClock size={18} />, tone: "slate", action: () => update({ tab: "receipts" }), hint: "Ver os recebimentos" },
  ];

  const months: Row[] = overview?.months || [];
  return <div className="se">
    {error && <Notice tone="error">{error}</Notice>}
    {!overview && !error && <p role="status">A carregar a análise comercial…</p>}
    {overview && <>
      {overview.mode === "none" && <Notice tone="info">Sem vendas: importe o SAF-T de vendas (modo local) ou sincronize as vendas do ERP (modo integrado).</Notice>}
      <section className="se-kpis" aria-label="Indicadores de faturação e recebimentos">{cards.map(card =>
        <button key={card.key} type="button" className={`se-kpi tone-${card.tone}`} onClick={card.action} aria-label={`${card.label}: ${card.value}. ${card.hint}`}>
          <span className="se-kpi-icon" aria-hidden="true">{card.icon}</span>
          <span className="se-kpi-body"><span className="se-kpi-label">{card.label}</span><strong>{card.value}</strong><small>{card.note}</small></span>
        </button>)}</section>
      {!!k.net_missing && <Notice tone="warning">{k.net_missing} documento(s) do ERP sem base tributável: a faturação sem IVA não os inclui. Volte a sincronizar as vendas.</Notice>}
      <section className="se-charts" aria-label="Gráficos da análise comercial">
        <ChartFrame title="Faturação e recebimentos por mês (com IVA)" legend={<Legend series={[{ key: "f", label: "Faturação", form: "bar" }, { key: "r", label: "Recebimentos", form: "bar" }]} />}
          table={{ columns: ["Mês", "Faturação com IVA", "Recebimentos", "Por receber"], rows: months.map(m => [month(m.key), euro(m.gross_cents), euro(m.received_cents), euro(m.pending_cents)]) }}
          footnote="Recebimentos pela data do recebimento, dos documentos do período. Clique num mês para ver os documentos.">
          <ColumnsChart ariaLabel="Faturação e recebimentos por mês" labels={months.map(m => month(m.key))} series={[{ key: "f", label: "Faturação", form: "bar" }, { key: "r", label: "Recebimentos", form: "bar" }]}
            values={[months.map(m => m.gross_cents), months.map(m => m.received_cents)]} onSelect={i => update({ tab: "documents", month: months[i].key, state: "" })} />
        </ChartFrame>
        <ChartFrame title="Clientes com mais faturação (sem IVA)" table={{ columns: ["Cliente", "Faturação", "Por receber"], rows: (overview.top_customers || []).map((c: Row) => [c.name, euro(c.net_cents), euro(c.pending_cents)]) }}
          footnote="Clique num cliente para abrir a ficha resumida.">
          <BarList ariaLabel="Clientes com mais faturação" rows={(overview.top_customers || []).map((c: Row) => ({ label: c.name, value: c.net_cents,
            note: c.pending_cents ? `por receber ${euro(c.pending_cents)}` : undefined, onOpen: () => openCustomer(c.key, c.name) }))} />
        </ChartFrame>
        {overview.has_lines ? <ChartFrame title="Artigos mais vendidos (sem IVA)" table={{ columns: ["Artigo", "Faturação", "Quantidade"], rows: (overview.top_articles || []).map((a: Row) => [`${a.code} · ${a.description}`, euro(a.net_cents), quantity(a.quantity)]) }}
          footnote="Clique num artigo para ver os documentos e os clientes que o compraram.">
          <BarList ariaLabel="Artigos mais vendidos" rows={(overview.top_articles || []).map((a: Row) => ({ label: a.description || a.code, value: a.net_cents,
            note: `${quantity(a.quantity)} un. · ${a.customers} cliente(s)`, onOpen: () => openArticle(a.code, a.description) }))} />
        </ChartFrame> : <p className="se-hint">Sem linhas de artigos nos documentos deste período{overview.mode === "erp" ? " (a sincronização das vendas não incluiu os artigos)" : ""}.</p>}
        <ChartFrame title="Valores por estado" table={{ columns: ["Estado", "Documentos", "Total com IVA", "Por receber"], rows: (overview.states || []).map((s: Row) => [s.label, s.documents, euro(s.gross_cents), euro(s.pending_cents)]) }}
          footnote="Clique num estado para ver os documentos.">
          <BarList ariaLabel="Documentos por estado" rows={(overview.states || []).filter((s: Row) => s.key !== "anulado" && s.key !== "excluido").map((s: Row) => ({ label: s.label,
            value: s.key === "pago" || s.key === "nota_credito" ? s.gross_cents : s.pending_cents, note: `${s.documents} documento(s)${s.key === "pago" || s.key === "nota_credito" ? "" : " · em dívida"}`,
            onOpen: () => update({ tab: "documents", state: s.key, month: "" }) }))} />
        </ChartFrame>
      </section>
    </>}

    <section className="panel se-browser">
      <div className="se-toolbar" role="search">
        <label className="se-search"><Search size={15} aria-hidden="true" /><input aria-label="Pesquisar cliente, código, NIF, documento, artigo ou valor" placeholder="Cliente, código, NIF, documento, artigo ou valor…"
          value={filters.q} onChange={e => update({ q: e.target.value })} /></label>
        {filters.tab === "documents" && <label>Estado<select aria-label="Estado do documento" value={filters.state} onChange={e => update({ state: e.target.value })}>
          {STATE_OPTIONS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>}
        <ActionButton type="button" disabled={!filtering} onClick={() => update({ q: "", state: "", customer: "", customerName: "", article: "", month: "" })}><RotateCcw size={15} aria-hidden="true" />Limpar filtros</ActionButton>
      </div>
      {(filters.customer || filters.article || filters.month) && <div className="se-chips" aria-label="Filtros ativos">
        {filters.customer && <button type="button" onClick={() => update({ customer: "", customerName: "" })}>Cliente: {filters.customerName}<X size={13} aria-label="Retirar" /></button>}
        {filters.article && <button type="button" onClick={() => update({ article: "" })}>Artigo: {filters.article}<X size={13} aria-label="Retirar" /></button>}
        {filters.month && <button type="button" onClick={() => update({ month: "" })}>Mês: {month(filters.month)}<X size={13} aria-label="Retirar" /></button>}
      </div>}
      <div className="se-tabs" role="group" aria-label="Vista">
        {([["documents", "Documentos", FileText], ["customers", "Clientes", Users], ["articles", "Artigos", Package], ["receipts", "Recebimentos", Wallet]] as const).map(([key, text, Icon]) =>
          <button key={key} type="button" aria-pressed={filters.tab === key} onClick={() => update({ tab: key, sort: "", direction: "desc" })}><Icon size={15} aria-hidden="true" />{text}</button>)}
      </div>
      {/* Só a resposta do separador atual (ao mudar de separador, a anterior tem outra estrutura). */}
      {!list || list.tab !== filters.tab ? <p role="status">A carregar…</p> : <>
        {filters.tab === "documents" && <DocumentsTable list={list} filters={filters} onSort={sort} onOpen={openDocument} onCustomer={openCustomer} />}
        {filters.tab === "customers" && <div className="se-scroll"><table className="se-table" aria-label="Clientes">
          <thead><tr><Sortable label="Código" field="code" filters={filters} onSort={sort} /><Sortable label="Cliente" field="name" filters={filters} onSort={sort} /><th scope="col">NIF</th>
            <Sortable label="Faturação s/ IVA" field="net_cents" filters={filters} onSort={sort} numeric /><Sortable label="Recebido" field="received_cents" filters={filters} onSort={sort} numeric />
            <Sortable label="Por receber" field="pending_cents" filters={filters} onSort={sort} numeric /><Sortable label="Vencido" field="overdue_cents" filters={filters} onSort={sort} numeric />
            <Sortable label="Docs." field="documents" filters={filters} onSort={sort} numeric /><Sortable label="Última compra" field="last_date" filters={filters} onSort={sort} /></tr></thead>
          <tbody>{list.rows.map((c: Row) => <tr key={c.key}><td>{c.code || "—"}</td>
            <td><button type="button" className="se-link" onClick={() => openCustomer(c.key, c.name)}>{c.name}</button></td><td>{c.nif || "—"}</td>
            <td className="numeric">{euro(c.net_cents)}</td><td className="numeric">{euro(c.received_cents)}</td><td className="numeric">{euro(c.pending_cents)}</td>
            <td className={`numeric${c.overdue_cents ? " se-danger" : ""}`}>{euro(c.overdue_cents)}</td><td className="numeric">{c.documents}</td><td>{day(c.last_date)}</td></tr>)}</tbody>
        </table></div>}
        {filters.tab === "articles" && <>{!list.has_lines && <p className="se-hint">Sem linhas de artigos nos documentos deste período.</p>}
          <div className="se-scroll"><table className="se-table" aria-label="Artigos">
            <thead><tr><Sortable label="Código" field="code" filters={filters} onSort={sort} /><Sortable label="Descrição" field="description" filters={filters} onSort={sort} />
              <Sortable label="Quantidade" field="quantity" filters={filters} onSort={sort} numeric /><Sortable label="Docs." field="documents" filters={filters} onSort={sort} numeric />
              <Sortable label="Clientes" field="customers" filters={filters} onSort={sort} numeric /><Sortable label="Faturado s/ IVA" field="net_cents" filters={filters} onSort={sort} numeric />
              <Sortable label="Preço médio" field="average_price_cents" filters={filters} onSort={sort} numeric /><Sortable label="Última venda" field="last_date" filters={filters} onSort={sort} /></tr></thead>
            <tbody>{list.rows.map((a: Row) => <tr key={a.code || a.description}><td>{a.code || "—"}</td>
              <td><button type="button" className="se-link" onClick={() => openArticle(a.code, a.description)}>{a.description || a.code}</button>{a.family && <small>{a.family}</small>}</td>
              <td className="numeric">{quantity(a.quantity)}</td><td className="numeric">{a.documents}</td><td className="numeric">{a.customers}</td><td className="numeric">{euro(a.net_cents)}</td>
              <td className="numeric">{euro(a.average_price_cents)}</td><td>{day(a.last_date)}</td></tr>)}</tbody>
          </table></div></>}
        {filters.tab === "receipts" && <><p className="se-hint">{list.note}</p><div className="se-scroll"><table className="se-table" aria-label="Recebimentos">
          <thead><tr><th scope="col">Data</th><th scope="col">Origem</th><th scope="col">Documento / referência</th><th scope="col">Meio</th><th scope="col">Conta ou caixa</th>
            <th scope="col" className="numeric">Valor</th><th scope="col">Documentos liquidados</th><th scope="col">Reconciliação</th></tr></thead>
          <tbody>{list.rows.map((r: Row) => { const [label, tone] = RECONCILIATION[r.reconciliation] || ["—", "neutral"]; return <tr key={r.id}>
            <td>{day(r.date)}</td><td>{r.source_label}</td>
            <td><button type="button" className="se-link" onClick={() => open({ label: `Recebimento ${r.document || r.reference || ""}`.trim(), params: { view: "receipt", id: r.id } })}>{r.document || r.reference || "Recebimento"}</button></td>
            <td>{r.method || "—"}</td><td>{r.account || "—"}</td><td className="numeric">{euro(r.amount_cents)}</td>
            <td>{r.documents.map((d: Row) => <button key={d.id} type="button" className="se-link se-block" onClick={() => openDocument(d)}>{d.document}<small>{d.customer_name}{d.allocated_cents != null && ` · ${euro(d.allocated_cents)}`}</small></button>)}</td>
            <td>{r.reconciliation ? <StatusBadge tone={tone} compact>{label}</StatusBadge> : "—"}</td></tr>; })}</tbody>
        </table></div></>}
        {!list.rows.length && <p className="se-hint">Sem registos para estes filtros.</p>}
        <div className="se-pages">
          <span>{list.total ? `${offset + 1}–${Math.min(offset + list.rows.length, list.total)} de ${list.total}` : "0 registos"}</span>
          <ActionButton type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>Anterior</ActionButton>
          <ActionButton type="button" disabled={offset + list.rows.length >= list.total} onClick={() => setOffset(offset + 50)}>Seguinte</ActionButton>
        </div>
      </>}
      {overview?.notes?.map((note: string) => <p key={note} className="footnote">{note}</p>)}
    </section>

    {panel && <DrilldownPanel<Row> key={JSON.stringify(panel[0])} label="Consulta comercial" context={{ company: "Faturação e recebimentos", store, start, end }} endpoint="/sales/detail"
      initial={panel} onSteps={setSteps} onClose={() => { setPanel(null); setSteps(null); }}>
      {(value, { step, open: push }) => <Detail key={JSON.stringify(step.params)} value={value}
        onDocument={row => push({ label: `${row.type_label || "Documento"} ${row.document}`, params: { view: "document", id: row.id } })}
        onCustomer={(key, name) => push({ label: name, params: { view: "customer", key } })}
        onArticle={(code, name) => push({ label: `${code} · ${name}`, params: { view: "article", code } })}
        onReceipt={(id, name) => push({ label: name, params: { view: "receipt", id } })}
        onLeave={leave} onFilter={changes => { setPanel(null); setSteps(null); update(changes); }} />}
    </DrilldownPanel>}
  </div>;
}

function DocumentsTable({ list, filters, onSort, onOpen, onCustomer }: { list: Row; filters: Filters; onSort: (field: string) => void; onOpen: (row: Row) => void; onCustomer: (key: string, name: string) => void }) {
  const t: Row = list.totals || {};
  return <>
    <p className="se-totals" role="status">{list.total} documento(s) · sem IVA <b>{euro(t.net_cents)}</b> · com IVA <b>{euro(t.gross_cents)}</b> · recebido <b>{euro(t.received_cents)}</b> · por receber <b>{euro(t.pending_cents)}</b></p>
    <div className="se-scroll"><table className="se-table" aria-label="Documentos de faturação">
      <thead><tr><Sortable label="Data" field="date" filters={filters} onSort={onSort} /><th scope="col">Tipo</th><Sortable label="Número" field="document" filters={filters} onSort={onSort} />
        <Sortable label="Cliente" field="customer_name" filters={filters} onSort={onSort} /><Sortable label="Sem IVA" field="net_cents" filters={filters} onSort={onSort} numeric />
        <th scope="col" className="numeric">IVA</th><Sortable label="Total" field="gross_cents" filters={filters} onSort={onSort} numeric />
        <Sortable label="Recebido" field="received_cents" filters={filters} onSort={onSort} numeric /><Sortable label="Pendente" field="pending_cents" filters={filters} onSort={onSort} numeric />
        <Sortable label="Vencimento" field="due_date" filters={filters} onSort={onSort} /><Sortable label="Estado" field="state" filters={filters} onSort={onSort} /></tr></thead>
      <tbody>{list.rows.map((d: Row) => <tr key={d.id} className={`is-${d.state}`}>
        <td>{day(d.date)}</td><td>{d.type}</td>
        <td><button type="button" className="se-link" onClick={() => onOpen(d)}>{d.document}</button></td>
        <td><button type="button" className="se-link" onClick={() => onCustomer(d.customer_key, d.customer_name)}>{d.customer_name}</button>{d.customer_code && <small>{d.customer_code}{d.customer_nif && ` · ${d.customer_nif}`}</small>}</td>
        <td className="numeric">{d.direction < 0 ? "−" : ""}{euro(d.net_cents)}</td><td className="numeric">{euro(d.tax_cents)}</td>
        <td className="numeric"><b>{d.direction < 0 ? "−" : ""}{euro(d.gross_cents)}</b></td><td className="numeric">{euro(d.received_cents)}</td>
        <td className="numeric">{d.pending_cents ? euro(d.pending_cents) : "—"}</td><td>{day(d.due_date)}</td><td><StateBadge state={d.state} label={d.state_label} /></td>
      </tr>)}</tbody>
    </table></div>
  </>;
}

type DetailProps = { value: Row; onDocument: (row: Row) => void; onCustomer: (key: string, name: string) => void; onArticle: (code: string, name: string) => void;
  onReceipt: (id: string, name: string) => void; onLeave: (page: string, params: Record<string, string>) => void; onFilter: (changes: Partial<Filters>) => void };

function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (key: string) => void }) {
  return <div className="se-tabs se-detail-tabs" role="tablist" aria-label="Separadores do detalhe">{tabs.map(([key, label]) =>
    <button key={key} type="button" role="tab" aria-selected={value === key} onClick={() => onChange(key)}>{label}</button>)}</div>;
}

function Facts({ items }: { items: [string, ReactNode][] }) {
  return <dl className="se-facts">{items.filter(([, value]) => value !== null && value !== undefined && value !== "").map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function Detail(props: DetailProps) {
  const { value } = props;
  if (value.view === "document") return <DocumentDetail {...props} />;
  if (value.view === "customer") return <CustomerDetail {...props} />;
  if (value.view === "article") return <ArticleDetail {...props} />;
  return <ReceiptDetail {...props} />;
}

function DocumentDetail({ value: d, onCustomer, onArticle, onReceipt, onLeave }: DetailProps) {
  const [tab, setTab] = useState("summary"), [search, setSearch] = useState("");
  const lines: Row[] = useMemo(() => (d.lines || []).filter((line: Row) => !search.trim() || `${line.code} ${line.description}`.toLocaleLowerCase("pt-PT").includes(search.trim().toLocaleLowerCase("pt-PT"))), [d, search]);
  const receipts: Row[] = [...(d.receipts || []), ...(d.conference || [])];
  const receiptId = (r: Row) => `${r.source}:${r.movement_id || r.id}`;
  return <div className="se-detail">
    <div className="se-detail-head"><StateBadge state={d.state} label={d.state_label} /><strong>{euro(d.gross_cents)}</strong>
      <button type="button" className="se-link" onClick={() => onCustomer(d.customer_key, d.customer_name)}>{d.customer_name}</button></div>
    <Tabs value={tab} onChange={setTab} tabs={[["summary", "Resumo"], ["lines", `Artigos (${d.lines.length})`], ["receipts", `Recebimentos (${receipts.length})`], ["original", "Documento original"]]} />
    {tab === "summary" && <>
      <Facts items={[["Documento", `${d.type_label} ${d.document}`], ["Data de emissão", day(d.date)], ["Cliente", <button key="c" type="button" className="se-link" onClick={() => onCustomer(d.customer_key, d.customer_name)}>{d.customer_name}</button>],
        ["Código do cliente", d.customer_code], ["NIF", d.customer_nif || d.customer?.nif], ["Morada", d.customer?.address], ["Condições de pagamento", d.payment_terms],
        ["Vencimento", d.due_date ? day(d.due_date) : "Sem data de vencimento"], ["Valor sem IVA", euro(d.net_cents)], ["Descontos", d.discount_cents ? euro(d.discount_cents) : d.discount_cents === 0 ? "—" : null],
        ["IVA", euro(d.tax_cents)], ["Total", <b key="t">{euro(d.gross_cents)}</b>], ["Recebido", euro(d.received_cents)], ["Em dívida", euro(d.pending_cents)],
        ["Estado", <StateBadge key="s" state={d.state} label={d.state_label} />], ["Origem", d.origin === "erp" ? "ERP (vendas sincronizadas)" : "SAF-T de vendas"],
        ["Identificação", d.key]]} />
      {d.received_source === "conferencia" && <p className="se-hint">Recebido pela conferência com os recebimentos registados no GPGest (maior do que o indicado no SAF-T; as fontes não se somam).</p>}
      {d.entity_id && <ActionButton type="button" onClick={() => onLeave("Clientes", { "consulta.tipo": "customers", "consulta.id": d.entity_id })}><ExternalLink size={15} aria-hidden="true" />Ver ficha completa do cliente</ActionButton>}
    </>}
    {tab === "lines" && <>
      {!d.lines.length ? <p className="se-hint">{d.origin === "erp" ? "A sincronização das vendas não incluiu os artigos deste documento." : "O documento não tem linhas no SAF-T."}</p> : <>
        <label className="se-search"><Search size={15} aria-hidden="true" /><input aria-label="Pesquisar artigos no documento" placeholder="Código ou descrição…" value={search} onChange={e => setSearch(e.target.value)} /></label>
        <div className="se-scroll"><table className="se-table" aria-label="Artigos faturados">
          <thead><tr><th scope="col">Código</th><th scope="col">Descrição</th><th scope="col" className="numeric">Qtd.</th><th scope="col">Un.</th><th scope="col" className="numeric">Preço unit.</th>
            <th scope="col" className="numeric">Desconto</th><th scope="col" className="numeric">IVA</th><th scope="col" className="numeric">Sem IVA</th><th scope="col" className="numeric">Com IVA</th></tr></thead>
          <tbody>{lines.map((line: Row) => <tr key={line.index}>
            <td>{line.code ? <button type="button" className="se-link" onClick={() => onArticle(line.code, line.description)}>{line.code}</button> : "—"}</td>
            <td>{line.description}{line.article_id && <button type="button" className="se-mini" onClick={() => onLeave("Artigos e validades", { "consulta.tipo": "article", "consulta.id": line.article_id })}>Ficha do artigo<ExternalLink size={12} aria-hidden="true" /></button>}</td>
            <td className="numeric">{quantity(line.quantity)}</td><td>{line.unit || "—"}</td><td className="numeric">{price(line.unit_price)}</td>
            <td className="numeric">{line.discount_cents ? euro(line.discount_cents) : "—"}</td><td className="numeric">{line.vat_rate != null ? `${Number(line.vat_rate).toLocaleString("pt-PT")}%` : "—"}</td>
            <td className="numeric">{euro(line.net_cents)}</td><td className="numeric"><b>{euro(line.gross_cents)}</b></td></tr>)}</tbody>
          <tfoot><tr><th scope="row" colSpan={7}>Total das linhas</th><td className="numeric"><b>{euro(d.lines_totals.net_cents)}</b></td>
            <td className="numeric"><b>{d.lines.some((l: Row) => l.gross_cents != null) ? euro(d.lines_totals.net_cents + d.lines_totals.vat_cents) : "—"}</b></td></tr></tfoot>
        </table></div>
        {!d.lines_match && <Notice tone="warning">A soma das linhas sem IVA ({euro(d.lines_totals.net_cents)}) difere do total do documento ({euro(d.net_cents)}): descontos globais ou arredondamentos da origem.</Notice>}
        {d.origin === "erp" && <p className="se-hint">Linhas do ERP: o preço unitário é o valor sem IVA a dividir pela quantidade; a taxa de IVA por linha não vem na sincronização.</p>}
      </>}
    </>}
    {tab === "receipts" && <>
      {!receipts.length ? <p className="se-hint">Sem recebimentos associados a este documento.</p> : <div className="se-scroll"><table className="se-table" aria-label="Recebimentos do documento">
        <thead><tr><th scope="col">Data</th><th scope="col">Origem</th><th scope="col">Meio</th><th scope="col">Conta ou caixa</th><th scope="col">Referência</th>
          <th scope="col" className="numeric">Valor</th><th scope="col" className="numeric">Neste documento</th><th scope="col">Reconciliação</th></tr></thead>
        <tbody>{receipts.map((r: Row) => { const [label, tone] = RECONCILIATION[r.reconciliation] || ["—", "neutral"]; return <tr key={r.id}>
          <td>{day(r.date)}</td><td>{r.source_label}</td><td>{r.method || "—"}</td><td>{r.account || "—"}</td>
          <td><button type="button" className="se-link" onClick={() => onReceipt(receiptId(r), `Recebimento ${r.document || r.reference || ""}`.trim())}>{r.document || r.reference || "Ver recebimento"}</button></td>
          <td className="numeric">{euro(r.amount_cents)}</td><td className="numeric">{r.allocated_cents == null ? "Vários documentos" : euro(r.allocated_cents)}</td>
          <td>{r.reconciliation ? <StatusBadge tone={tone} compact>{label}</StatusBadge> : "—"}</td></tr>; })}</tbody>
      </table></div>}
      <p className="se-hint">Recebido {euro(d.received_cents)} · em dívida {euro(d.pending_cents)}. Um recibo e um recebimento registado no GPGest para o mesmo documento não se somam.</p>
    </>}
    {tab === "original" && <>
      {d.originals.length ? <DocumentPreview identifiers={d.originals} /> : <Notice tone="info">{d.original_note}</Notice>}
    </>}
  </div>;
}

function CustomerDetail({ value: c, onDocument, onArticle, onLeave, onFilter }: DetailProps) {
  const [tab, setTab] = useState("documents");
  const t: Row = c.totals || {};
  return <div className="se-detail">
    <Facts items={[["Código", c.code], ["NIF", c.nif], ["Morada", c.address], ["Telefone", c.phone], ["Email", c.email], ["Última compra", day(c.last_date)]]} />
    <div className="se-mini-kpis">
      {([["Faturação s/ IVA", t.net_cents], ["Recebido", t.received_cents], ["Por receber", t.pending_cents], ["Vencido", t.overdue_cents]] as const).map(([label, value]) =>
        <div key={label}><span>{label}</span><strong className={label === "Vencido" && value ? "se-danger" : undefined}>{euro(value)}</strong></div>)}
      <div><span>Documentos</span><strong>{t.documents ?? 0}</strong></div>
    </div>
    <div className="se-actions">
      {c.entity_id && <ActionButton type="button" onClick={() => onLeave("Clientes", { "consulta.tipo": "customers", "consulta.id": c.entity_id })}><ExternalLink size={15} aria-hidden="true" />Ver ficha completa</ActionButton>}
      <ActionButton type="button" onClick={() => onFilter({ tab: "documents", customer: c.key, customerName: c.name, state: "", month: "" })}>Consultar documentos</ActionButton>
      <ActionButton type="button" onClick={() => onFilter({ tab: "articles", customer: c.key, customerName: c.name })}>Consultar artigos comprados</ActionButton>
      <ActionButton type="button" onClick={() => onFilter({ tab: "receipts", q: c.name })}>Consultar recebimentos</ActionButton>
    </div>
    <Tabs value={tab} onChange={setTab} tabs={[["documents", `Documentos (${c.documents.length})`], ["articles", `Artigos comprados (${c.articles.length})`], ["history", "Evolução das compras"], ["stopped", `Deixou de comprar (${c.stopped.length})`]]} />
    {tab === "documents" && <div className="se-scroll"><table className="se-table" aria-label="Documentos do cliente">
      <thead><tr><th scope="col">Data</th><th scope="col">Documento</th><th scope="col" className="numeric">Total</th><th scope="col" className="numeric">Pendente</th><th scope="col">Estado</th></tr></thead>
      <tbody>{c.documents.map((d: Row) => <tr key={d.id}><td>{day(d.date)}</td><td><button type="button" className="se-link" onClick={() => onDocument(d)}>{d.document}</button></td>
        <td className="numeric">{d.direction < 0 ? "−" : ""}{euro(d.gross_cents)}</td><td className="numeric">{d.pending_cents ? euro(d.pending_cents) : "—"}</td><td><StateBadge state={d.state} label={d.state_label} /></td></tr>)}</tbody>
    </table></div>}
    {tab === "articles" && <ArticlesBought rows={c.articles} onArticle={onArticle} empty="Sem artigos nos documentos do período." />}
    {tab === "history" && <ChartFrame title="Faturação sem IVA por mês (últimos 12 meses)" table={{ columns: ["Mês", "Faturação"], rows: c.history.map((m: Row) => [month(m.key), euro(m.net_cents)]) }}>
      <ColumnsChart ariaLabel="Evolução das compras do cliente" labels={c.history.map((m: Row) => month(m.key))} series={[{ key: "f", label: "Faturação", form: "bar" }]} values={[c.history.map((m: Row) => m.net_cents)]} />
    </ChartFrame>}
    {tab === "stopped" && <><p className="se-hint">Artigos comprados nos 12 meses anteriores ao período e sem compras no período.</p>
      <ArticlesBought rows={c.stopped} onArticle={onArticle} empty="Nenhum artigo deixou de ser comprado." /></>}
  </div>;
}

function ArticlesBought({ rows, onArticle, empty }: { rows: Row[]; onArticle: (code: string, name: string) => void; empty: string }) {
  if (!rows.length) return <p className="se-hint">{empty}</p>;
  return <div className="se-scroll"><table className="se-table" aria-label="Artigos comprados">
    <thead><tr><th scope="col">Artigo</th><th scope="col" className="numeric">Qtd.</th><th scope="col" className="numeric">Docs.</th><th scope="col" className="numeric">Faturado s/ IVA</th>
      <th scope="col" className="numeric">Preço médio</th><th scope="col" className="numeric">Último preço</th><th scope="col">Última compra</th></tr></thead>
    <tbody>{rows.map(a => <tr key={a.code || a.description}><td><button type="button" className="se-link" onClick={() => onArticle(a.code, a.description)}>{a.code}</button><small>{a.description}</small></td>
      <td className="numeric">{quantity(a.quantity)}</td><td className="numeric">{a.documents}</td><td className="numeric">{euro(a.net_cents)}</td>
      <td className="numeric">{euro(a.average_price_cents)}</td><td className="numeric">{price(a.last_unit_price)}</td><td>{day(a.last_date)}</td></tr>)}</tbody>
  </table></div>;
}

function ArticleDetail({ value: a, onDocument, onCustomer, onLeave }: DetailProps) {
  const [tab, setTab] = useState("documents");
  return <div className="se-detail">
    <Facts items={[["Código", a.code], ["Família", a.family || "—"], ["Marca", a.brand || "—"], ["Stock atual", a.stock != null ? `${quantity(a.stock)} ${a.unit || ""}`.trim() : "Sem ficha de stock associada"],
      ["Último preço de venda (s/ IVA)", price(a.last_unit_price)], ["Preço médio de venda (s/ IVA)", euro(a.average_price_cents)],
      ["Custo de aquisição", a.cost != null ? price(a.cost) : "Desconhecido"], ["Margem estimada", a.margin_percent != null ? `${a.margin_percent.toLocaleString("pt-PT")}%` : "Não calculada"],
      ["Quantidade vendida", quantity(a.quantity)], ["Faturado sem IVA", euro(a.net_cents)], ["Documentos", a.documents], ["Clientes", a.customers]]} />
    {a.cost_note && <p className="se-hint">{a.cost_note}</p>}
    {a.article_id && <ActionButton type="button" onClick={() => onLeave("Artigos e validades", { "consulta.tipo": "article", "consulta.id": a.article_id })}><ExternalLink size={15} aria-hidden="true" />Abrir ficha do artigo</ActionButton>}
    <Tabs value={tab} onChange={setTab} tabs={[["documents", `Documentos (${a.lines.length})`], ["customers", `Clientes compradores (${a.buyers.length})`]]} />
    {tab === "documents" && <div className="se-scroll"><table className="se-table" aria-label="Documentos com o artigo">
      <thead><tr><th scope="col">Data</th><th scope="col">Documento</th><th scope="col">Cliente</th><th scope="col" className="numeric">Qtd.</th><th scope="col" className="numeric">Preço unit.</th><th scope="col" className="numeric">Sem IVA</th></tr></thead>
      <tbody>{a.lines.map((line: Row, index: number) => <tr key={`${line.document_id}-${index}`}><td>{day(line.date)}</td>
        <td><button type="button" className="se-link" onClick={() => onDocument({ id: line.document_id, document: line.document, type_label: "Documento" })}>{line.document}</button></td>
        <td><button type="button" className="se-link" onClick={() => onCustomer(line.customer_key, line.customer_name)}>{line.customer_name}</button></td>
        <td className="numeric">{quantity(line.quantity)}</td><td className="numeric">{price(line.unit_price)}</td><td className="numeric">{euro(line.net_cents)}</td></tr>)}</tbody>
    </table></div>}
    {tab === "customers" && <div className="se-scroll"><table className="se-table" aria-label="Clientes que compraram o artigo">
      <thead><tr><th scope="col">Cliente</th><th scope="col">NIF</th><th scope="col" className="numeric">Faturação s/ IVA</th><th scope="col" className="numeric">Docs.</th><th scope="col">Última compra</th></tr></thead>
      <tbody>{a.buyers.map((c: Row) => <tr key={c.key}><td><button type="button" className="se-link" onClick={() => onCustomer(c.key, c.name)}>{c.name}</button></td><td>{c.nif || "—"}</td>
        <td className="numeric">{euro(c.net_cents)}</td><td className="numeric">{c.documents}</td><td>{day(c.last_date)}</td></tr>)}</tbody>
    </table></div>}
  </div>;
}

function ReceiptDetail({ value: r, onDocument, onCustomer }: DetailProps) {
  const [label, tone] = RECONCILIATION[r.reconciliation] || ["—", "neutral"];
  return <div className="se-detail">
    <Facts items={[["Origem", r.source_label], ["Data", day(r.date)], ["Valor", euro(r.amount_cents)], ["Meio de pagamento", r.method || "—"], ["Conta ou caixa", r.account || "—"],
      ["Referência", r.reference || r.document || "—"], ["Reconciliação", r.reconciliation ? <StatusBadge key="r" tone={tone} compact>{label}</StatusBadge> : "—"]]} />
    <h3 className="se-subtitle">Documentos liquidados</h3>
    <div className="se-scroll"><table className="se-table" aria-label="Documentos liquidados pelo recebimento">
      <thead><tr><th scope="col">Documento</th><th scope="col">Cliente</th><th scope="col" className="numeric">Total</th><th scope="col" className="numeric">Neste recebimento</th><th scope="col">Estado</th></tr></thead>
      <tbody>{r.documents.map((d: Row) => <tr key={d.id}><td><button type="button" className="se-link" onClick={() => onDocument(d)}>{d.document}</button></td>
        <td><button type="button" className="se-link" onClick={() => onCustomer(d.customer_key, d.customer_name)}>{d.customer_name}</button></td>
        <td className="numeric">{euro(d.gross_cents)}</td><td className="numeric">{d.allocated_cents == null ? "Repartido pelo ERP" : euro(d.allocated_cents)}</td><td><StateBadge state={d.state} label={d.state_label} /></td></tr>)}</tbody>
    </table></div>
  </div>;
}
