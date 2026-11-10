import { useEffect, useState } from "react";
import { ArrowDown, ArrowLeftRight, ArrowUp, Boxes, Eye, FileText, Filter, MoreHorizontal, Package, RefreshCw, SlidersHorizontal, User, Warehouse } from "lucide-react";
import { api } from "./lib/api";
import { formatQuantity } from "./lib/quantity";
import DatePresets, { currentMonthRange } from "./components/DatePresets";
import { Field } from "./components/Form";
import ArticleSearch from "./components/ArticleSearch";
import { ActionButton, Notice } from "./components/InteractionUI";
import { KpiCard } from "./components/OverviewUI";
import { StatusBadge, SyncStatus, type BusinessTone } from "./components/BusinessUI";
import { ActionMenu, DetailDrawer, PageHeader } from "./components/WorkspaceUI";
import StockSalesSync from "./StockSalesSync";
import "./StockMovements.css";

type Row = Record<string, any>;
const day = (s?: string) => s ? s.slice(0, 10).split("-").reverse().join("/") : "—";
const lisbonDay = (at: string) => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(at));
const time = (at: string) => new Date(at).toLocaleTimeString("pt-PT", { timeZone: "Europe/Lisbon", hour: "2-digit", minute: "2-digit" });
const money = (value: unknown) => value === null || value === undefined || value === "" ? "—"
  : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(value));

const kindLabels: Row = { purchase: "Receção / compra", sale: "Venda", supplier_return: "Devolução ao fornecedor", customer_return: "Devolução de cliente", transfer_in: "Transferência — entrada", transfer_out: "Transferência — saída", loss: "Perda", expired: "Validade", adjustment: "Ajuste", opening: "Saldo inicial", valuation: "Ajuste de valorização" };
const origins: Row = { procurement: "Compras", Wintouch: "Wintouch", customer_order: "Encomenda de cliente", "SAF-T": "Vendas SAF-T", inventory: "Inventário", import: "Importação de stock", article_lots: "Ficha do artigo (lotes)", manual: "Movimento manual" };
const bases: Row = { both: "Data do documento ou de registo", document: "Data do documento", record: "Data de registo" };

/** Cor e ícone por tipo: verde entradas, vermelho saídas, azul transferências, âmbar ajustes. */
function movementStyle(kind: string): { tone: BusinessTone; icon: typeof ArrowDown } {
  if (["transfer_in", "transfer_out"].includes(kind)) return { tone: "info", icon: ArrowLeftRight };
  if (["adjustment", "valuation"].includes(kind)) return { tone: "warning", icon: SlidersHorizontal };
  if (["sale", "supplier_return", "loss", "expired"].includes(kind)) return { tone: "danger", icon: ArrowUp };
  return { tone: "success", icon: ArrowDown };
}
function MovementBadge({ kind }: { kind: string }) {
  const { tone, icon: Icon } = movementStyle(kind);
  return <StatusBadge tone={tone} icon={<Icon size={14} />}>{kindLabels[kind] || kind}</StatusBadge>;
}
const signed = (quantity: unknown) => {
  const value = Number(quantity);
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${formatQuantity(Math.abs(value))}`;
};
/** Quantidades por unidade de medida: nunca se somam unidades diferentes. */
function byUnit(values: Row | undefined, sign: string) {
  const entries = Object.entries(values || {});
  if (!entries.length) return "0";
  if (entries.length > 2) return `${entries.length} unidades de medida`;
  return entries.map(([unit, quantity]) => `${sign}${formatQuantity(quantity)} ${unit}`).join(" · ");
}
/** Registo noutro dia que o documento: dia (e ano, se diferente) e hora; no mesmo dia, só a hora. */
const registered = (r: Row) => {
  if (!r.created_at) return "";
  const at = lisbonDay(r.created_at);
  if (at === r.occurred_on) return time(r.created_at);
  // Dia e hora sem quebra entre si; a linha pode quebrar depois de «Registo».
  return `Registo ${at.slice(0, 4) === String(r.occurred_on).slice(0, 4) ? day(at).slice(0, 5) : day(at)} ${time(r.created_at)}`;
};
const syncInfo = (r: Row) => r.origin === "Wintouch"
  ? { state: "sincronizado" as const, label: "Importado da Wintouch", text: "Movimento criado pela sincronização de vendas Wintouch; repetir a sincronização não o duplica." }
  : { state: "apenas_local" as const, label: "Registado na aplicação", text: r.origin === "procurement" ? "As compras entram no stock só pela receção na aplicação; não são enviadas ao ERP." : "Movimento local da aplicação; não é enviado ao ERP." };

const emptyFilters = () => ({ start: currentMonthRange().from, end: currentMonthRange().to, article_id: "", kind: "", page: 1, view: "documents", group: "", groupLabel: "", date_basis: "both", warehouse_id: "", origin: "", q: "" });

/**
 * Movimentos de stock: indicadores, filtros, grelha por documento ou por linha, detalhe lateral e a
 * sincronização das vendas Wintouch. Só consulta: nenhum movimento é criado ou alterado aqui.
 */
export default function StockMovements({ store, stores, articles, warehouses, documents, onOpenDocument }: {
  store: string;
  stores: Row[];
  articles: Row[];
  warehouses: Row[];
  /** Documentos de compras carregados (para «Abrir documento»). */
  documents: Row[];
  onOpenDocument: (document: Row) => void;
}) {
  const [filters, setFilters] = useState(emptyFilters);
  const [ledger, setLedger] = useState<Row>({ rows: [], more: false, summary: null, key: "" });
  const [error, setError] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<Row | null>(null);
  const [salesStatus, setSalesStatus] = useState<Row | null>(null);
  const [salesSync, setSalesSync] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const update = (changes: Row) => setFilters(f => ({ ...f, page: 1, ...changes }));

  const { groupLabel: _label, ...query } = filters;
  const key = new URLSearchParams(Object.entries({ store_id: store, ...query }).filter(([, v]) => v !== "" && v != null).map(([k, v]) => [k, String(v)])).toString();
  useEffect(() => {
    let active = true;
    setError("");
    // A resposta guarda o pedido que a originou: a grelha só desenha dados da vista e dos filtros atuais.
    api("/procurement-ledger?" + key).then(r => { if (active) setLedger({ ...r, key }); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [key, refresh]);
  useEffect(() => { api("/integrations/wintouch/sales/stock-status").then(setSalesStatus).catch(() => setSalesStatus(null)); }, [refresh]);
  // A pesquisa por documento só é aplicada depois de uma pausa na escrita.
  useEffect(() => { const timer = window.setTimeout(() => setFilters(f => f.q === search.trim() ? f : { ...f, q: search.trim(), page: 1 }), 400); return () => window.clearTimeout(timer); }, [search]);

  const ready = ledger.key === key;
  const rows: Row[] = ready ? ledger.rows : [];
  const summary: Row | null = ready ? ledger.summary : null;
  const documentsView = filters.view === "documents" && !filters.group;
  const total = summary ? (documentsView ? summary.documents : summary.count) : 0;
  const first = (filters.page - 1) * 50 + 1;
  const advancedCount = ["warehouse_id", "origin", "q"].filter(k => (filters as Row)[k]).length + (filters.date_basis !== "both" ? 1 : 0);
  const storeWarehouses = warehouses.filter(w => !store || w.store_id === store);
  const procurementDoc = (r: Row) => r.procurement_id ? documents.find(d => d.id === r.procurement_id) : undefined;
  const openLines = (r: Row) => { setDetail(null); update({ view: "lines", group: r.group, groupLabel: `${r.document_type} ${r.document}`.trim() }); };
  const filterArticle = (r: Row) => { setDetail(null); update({ article_id: r.article_id, group: "", groupLabel: "", view: "lines" }); };
  const openDocument = (r: Row) => { const doc = procurementDoc(r); if (doc) { setDetail(null); onOpenDocument(doc); } };
  const clear = () => { setSearch(""); setFilters(f => ({ ...emptyFilters(), view: f.view })); };

  const lineActions = (r: Row) => <ActionMenu label={`Ações do movimento ${r.article_code} ${r.document || ""}`.trim()} icon={<MoreHorizontal size={18} aria-hidden="true" />}>
    <ActionButton onClick={() => setDetail(r)}>Ver detalhe</ActionButton>
    {procurementDoc(r) && <ActionButton onClick={() => openDocument(r)}>Abrir documento</ActionButton>}
    {!filters.group && <ActionButton onClick={() => openLines(r)}>Ver linhas do documento</ActionButton>}
    <ActionButton onClick={() => filterArticle(r)}>Filtrar por este artigo</ActionButton>
  </ActionMenu>;
  const documentActions = (r: Row) => <ActionMenu label={`Ações do documento ${r.document_type} ${r.document}`} icon={<MoreHorizontal size={18} aria-hidden="true" />}>
    <ActionButton onClick={() => openLines(r)}>Ver linhas</ActionButton>
    {procurementDoc(r) && <ActionButton onClick={() => openDocument(r)}>Abrir documento</ActionButton>}
  </ActionMenu>;
  const documentCell = (r: Row, entity = true) => <div className="stock-ledger-document">
    <FileText size={18} aria-hidden="true" />
    <span><b>{r.document_type} {r.document}</b>{entity && <small>{r.entity_name || r.origin_label}</small>}
      {procurementDoc(r) && <ActionButton className="stock-ledger-open" aria-label={`Abrir documento ${r.document_type} ${r.document}`} onClick={e => { e.stopPropagation(); openDocument(r); }}><Eye size={14} aria-hidden="true" />Abrir</ActionButton>}</span>
  </div>;
  const article = (r: Row) => <div className="stock-ledger-article">
    {r.photo ? <img src={r.photo} alt="" loading="lazy" /> : <span className="stock-ledger-thumb" aria-hidden="true"><Package size={18} /></span>}
    <span><b>{r.article_code}</b><small>{r.article_name}</small></span>
  </div>;
  const quantity = (r: Row) => <span className={`stock-ledger-qty ${Number(r.quantity) < 0 ? "is-out" : Number(r.quantity) > 0 ? "is-in" : ""}`}>{signed(r.quantity)} {r.unit}</span>;
  /** Stock acumulado do artigo no armazém da linha: antes → depois do movimento (pela ordem de registo). */
  const balance = (r: Row) => r.stock_after === undefined ? null
    : <small className="stock-ledger-balance" title={`Stock do artigo em «${r.warehouse_name}» antes e depois deste movimento`}>Stock {formatQuantity(r.stock_before)} → <b>{formatQuantity(r.stock_after)}</b></small>;
  const cost = (r: Row) => <>{r.unit_cost === null ? <span>Custo reservado</span> : <b>{money(r.unit_cost)}</b>}
    <small className={Number(r.value) < 0 ? "is-out" : ""}>{r.value_delta ? `Ajuste ${money(r.value_delta)}` : r.value === null ? "" : money(r.value)}</small></>;

  return <section className="stock-ledger">
    <PageHeader title="Movimentos de stock" description="Entradas, saídas, transferências e ajustes de artigos, com o documento que os originou." actions={<>
      {salesStatus?.available && salesStatus.can_sync && <ActionButton className="primary" onClick={() => setSalesSync(true)}><RefreshCw size={16} aria-hidden="true" />{salesStatus.running ? "Acompanhar sincronização" : "Sincronizar vendas"}</ActionButton>}
      <ActionButton onClick={() => setRefresh(v => v + 1)}>Atualizar</ActionButton>
    </>} />
    {salesStatus?.available && <div className="stock-ledger-sync">
      <SyncStatus state={salesStatus.last?.status === "falhou" || salesStatus.last?.stock_status === "erro" ? "erro" : Number(salesStatus.last?.pending_count) + Number(salesStatus.last?.conflict_count) > 0 ? "conflito" : salesStatus.last ? "sincronizado" : "pendente"}
        label={salesStatus.running ? "Vendas Wintouch: a sincronizar" : salesStatus.last ? "Vendas Wintouch" : "Vendas Wintouch por sincronizar"} lastSuccessfulAt={salesStatus.last?.finished_at} />
      <span>{salesStatus.last?.status === "falhou" ? `A última sincronização falhou: ${salesStatus.last.error || "repita o período."}`
        : salesStatus.last?.period ? `Sincronizadas de ${day(salesStatus.last.period.start)} a ${day(salesStatus.last.period.end)} · ${new Date(salesStatus.last.finished_at).toLocaleString("pt-PT", { timeZone: "Europe/Lisbon", dateStyle: "short", timeStyle: "short" })}`
        : salesStatus.last ? `Última sincronização a ${new Date(salesStatus.last.finished_at).toLocaleString("pt-PT", { timeZone: "Europe/Lisbon", dateStyle: "short", timeStyle: "short" })}` : "Ainda não sincronizadas para o stock."}</span>
      {salesStatus.last?.stock_status === "erro" && <span className="stock-ledger-warning">{salesStatus.last.stock_message || "As saídas de stock não foram aplicadas."}</span>}
      {Number(salesStatus.last?.pending_count) + Number(salesStatus.last?.conflict_count) > 0 && <span className="stock-ledger-warning">{salesStatus.last.pending_count} linha(s) por rever e {salesStatus.last.conflict_count} em conflito.</span>}
      <small>As vendas chegam ao stock por esta sincronização; as compras entram só pela receção na aplicação.{!salesStatus.can_sync && " Só o administrador sincroniza as vendas."}</small>
    </div>}

    <div className="stock-ledger-kpis" aria-label="Indicadores dos movimentos filtrados">
      <KpiCard label="Valor líquido dos movimentos" tone="info" icon={<Boxes size={22} />} state={summary ? "ready" : "loading"}
        value={summary?.net_value === null ? "Sem acesso a custos" : money(summary?.net_value)}
        description={summary ? `${summary.count} movimento(s) · entradas − saídas ao custo` : undefined} />
      <KpiCard label="Entradas" tone="success" icon={<ArrowDown size={22} />} state={summary ? "ready" : "loading"} value={summary && byUnit(summary.inbound, "+")}
        description={summary && (summary.inbound_value === null ? "Valor sem acesso a custos" : money(summary.inbound_value))} />
      <KpiCard label="Saídas" tone="danger" icon={<ArrowUp size={22} />} state={summary ? "ready" : "loading"} value={summary && byUnit(summary.outbound, "−")}
        description={summary && (summary.outbound_value === null ? "Valor sem acesso a custos" : `− ${money(summary.outbound_value)}`)} />
      <KpiCard label="Artigos movimentados" tone="suggestion" icon={<Package size={22} />} state={summary ? "ready" : "loading"} value={summary?.articles}
        description={summary && `${summary.documents} documento(s)${summary.transfers ? ` · ${summary.transfers} transferência(s) fora das entradas/saídas` : ""}`} />
      <KpiCard label="Armazéns" tone="warning" icon={<Warehouse size={22} />} state={summary ? "ready" : "loading"} value={summary?.warehouses?.length}
        description={summary && (summary.warehouses.join(", ") || "Sem movimentos")} />
    </div>

    <div className="panel stock-ledger-panel">
      <div className="stock-ledger-toolbar">
        <div className="module-tabs" role="group" aria-label="Apresentação dos movimentos">{([["documents", "Por documento"], ["lines", "Por linha"]] as const).map(([value, label]) => <ActionButton key={value} type="button" aria-pressed={filters.view === value && !filters.group} className={filters.view === value && !filters.group ? "active" : ""} onClick={() => update({ view: value, group: "", groupLabel: "" })}>{label}</ActionButton>)}</div>
      </div>
      {filters.group ? <Notice tone="info">Linhas do documento <strong>{filters.groupLabel}</strong> (todas as datas). <ActionButton onClick={() => update({ view: "documents", group: "", groupLabel: "" })}>Voltar aos documentos</ActionButton></Notice> : <>
        <div className="stock-ledger-filters filter-bar">
          <div className="stock-ledger-period"><DatePresets from={filters.start} to={filters.end} onSelect={(start, end) => update({ start, end })} /></div>
          <Field label="Desde"><input type="date" value={filters.start} onChange={e => update({ start: e.target.value })} /></Field>
          <Field label="Até"><input type="date" value={filters.end} onChange={e => update({ end: e.target.value })} /></Field>
          <Field label="Artigo"><ArticleSearch articles={articles as { id: string }[]} value={filters.article_id} onPick={a => setFilters(f => (a?.id || "") === f.article_id ? f : { ...f, article_id: a?.id || "", page: 1 })} /></Field>
          <Field label="Tipo de movimento"><select value={filters.kind} onChange={e => update({ kind: e.target.value })}><option value="">Todos</option>{Object.entries(kindLabels).map(([k, v]) => <option value={k} key={k}>{String(v)}</option>)}</select></Field>
          <ActionButton className={`stock-ledger-more${advanced || advancedCount ? " active" : ""}`} aria-expanded={advanced} aria-controls="stock-ledger-advanced" onClick={() => setAdvanced(v => !v)}><Filter size={16} aria-hidden="true" />Filtros{advancedCount ? ` (${advancedCount})` : ""}</ActionButton>
        </div>
        {advanced && <div id="stock-ledger-advanced" className="stock-ledger-advanced" role="group" aria-label="Filtros avançados">
          <Field label="Critério de data"><select value={filters.date_basis} onChange={e => update({ date_basis: e.target.value })}>{Object.entries(bases).map(([k, v]) => <option key={k} value={k}>{String(v)}</option>)}</select></Field>
          <Field label="Armazém"><select value={filters.warehouse_id} onChange={e => update({ warehouse_id: e.target.value })}><option value="">Todos</option>{storeWarehouses.map(w => <option key={w.id} value={w.id}>{w.name}{stores.length > 1 && !store ? ` · ${stores.find(s => s.id === w.store_id)?.name || ""}` : ""}</option>)}</select></Field>
          <Field label="Origem"><select value={filters.origin} onChange={e => update({ origin: e.target.value })}><option value="">Todas</option>{Object.entries(origins).map(([k, v]) => <option key={k} value={k}>{String(v)}</option>)}</select></Field>
          <Field label="Documento"><input type="search" value={search} placeholder="Número do documento" onChange={e => setSearch(e.target.value)} /></Field>
          <ActionButton onClick={clear}>Limpar filtros</ActionButton>
        </div>}
        <p className="footnote">{filters.date_basis === "document" ? "O período usa a data do documento (a data efetiva do movimento)." : filters.date_basis === "record" ? "O período usa a data em que o movimento foi registado na aplicação." : "O período inclui a data do documento e a data de registo: uma receção lançada hoje com fatura de outro mês aparece nos dois."}</p>
      </>}
      {error && <Notice tone="error">{error}</Notice>}

      {documentsView ? <>
        <div className="table-wrap stock-ledger-table"><table><thead><tr>{["Data / registo", "Documento", "Entidade", "Linhas / artigos", "Entradas / saídas", "Armazém", "Valor", "Ações"].map(h => <th key={h} className={["Valor", "Linhas / artigos"].includes(h) ? "numeric" : undefined}>{h}</th>)}</tr></thead>
          <tbody>{rows.map(r => <tr key={r.group} className="stock-ledger-row" onClick={e => { if (!(e.target as HTMLElement).closest("button, summary, a, details")) openLines(r); }}>
            <td><b className="stock-ledger-nowrap">{day(r.occurred_on)}</b><small>{registered(r)}</small><small>{r.user_name}</small></td>
            <td>{documentCell(r, false)}<div className="stock-ledger-kinds">{(r.kinds || []).map((k: string) => <MovementBadge key={k} kind={k} />)}</div></td>
            <td>{r.entity_name || <span className="muted">{r.origin_label}</span>}</td>
            <td className="numeric">{r.lines} {r.lines === 1 ? "linha" : "linhas"}<small>{r.articles} {r.articles === 1 ? "artigo" : "artigos"}</small></td>
            <td>{Number(r.inbound) > 0 && <span className="stock-ledger-qty is-in">+{formatQuantity(r.inbound)}</span>}{Number(r.outbound) > 0 && <span className="stock-ledger-qty is-out">−{formatQuantity(r.outbound)}</span>}{!Number(r.inbound) && !Number(r.outbound) && <small>Sem quantidade (valorização)</small>}</td>
            <td><span className="stock-ledger-warehouse"><Warehouse size={16} aria-hidden="true" />{r.warehouse_name}</span></td>
            <td className="numeric"><b className={Number(r.value) < 0 ? "is-out" : ""}>{r.value === null ? "—" : money(r.value)}</b></td>
            <td>{documentActions(r)}</td>
          </tr>)}</tbody></table></div>
        <ul className="stock-ledger-cards" aria-label="Documentos que movimentaram stock">{rows.map(r => <li key={r.group}>
          <button type="button" className="stock-ledger-card" onClick={() => openLines(r)}>
            <span className="stock-ledger-card-head"><b>{r.document_type} {r.document}</b><span>{day(r.occurred_on)}</span></span>
            <small>{r.entity_name || r.origin_label} · {r.lines} {r.lines === 1 ? "linha" : "linhas"} · {r.warehouse_name}</small>
            <span className="stock-ledger-card-flow">{Number(r.inbound) > 0 && <span className="stock-ledger-qty is-in">+{formatQuantity(r.inbound)}</span>}{Number(r.outbound) > 0 && <span className="stock-ledger-qty is-out">−{formatQuantity(r.outbound)}</span>}<b>{r.value === null ? "" : money(r.value)}</b></span>
            <small className="stock-ledger-card-hint">Ver linhas</small>
          </button></li>)}</ul>
      </> : <>
        <div className="table-wrap stock-ledger-table"><table><thead><tr>{["Data / utilizador", "Documento", "Artigo", "Movimento", "Quantidade / stock", "Armazém", "Lote / validade", "Custo / valor", "Ações"].map(h => <th key={h} className={["Quantidade / stock", "Custo / valor"].includes(h) ? "numeric" : undefined}>{h}</th>)}</tr></thead>
          <tbody>{rows.map(r => <tr key={r.id} className="stock-ledger-row" onClick={e => { if (!(e.target as HTMLElement).closest("button, summary, a, details")) setDetail(r); }}>
            <td><b className="stock-ledger-nowrap">{day(r.occurred_on)}</b><small>{registered(r)}</small><small className="stock-ledger-user"><User size={13} aria-hidden="true" />{r.user_name}</small></td>
            <td>{documentCell(r)}</td>
            <td>{article(r)}</td>
            <td><MovementBadge kind={r.kind} /><small>{r.reason}</small></td>
            <td className="numeric">{quantity(r)}{balance(r)}</td>
            <td><span className="stock-ledger-warehouse"><Warehouse size={16} aria-hidden="true" />{r.warehouse_name}</span></td>
            <td>{r.lot_number || "—"}<small className="stock-ledger-nowrap">{r.expiry ? day(r.expiry) : ""}</small></td>
            <td className="numeric">{cost(r)}</td>
            <td>{lineActions(r)}</td>
          </tr>)}</tbody></table></div>
        <ul className="stock-ledger-cards" aria-label="Movimentos de stock">{rows.map(r => <li key={r.id}>
          <button type="button" className="stock-ledger-card" onClick={() => setDetail(r)}>
            <span className="stock-ledger-card-head"><MovementBadge kind={r.kind} /><span>{day(r.occurred_on)}</span></span>
            {article(r)}
            <span className="stock-ledger-card-flow">{quantity(r)}<b>{r.value === null ? "" : money(r.value)}</b></span>
            {balance(r)}
            <small>{r.document_type} {r.document} · {r.warehouse_name}{r.lot_number ? ` · Lote ${r.lot_number}` : ""}</small>
            <small className="stock-ledger-card-hint">Ver detalhe</small>
          </button></li>)}</ul>
      </>}
      {!ready ? <p role="status">A carregar movimentos…</p> : !rows.length && <p className="stock-ledger-empty">Sem movimentos de stock para estes filtros. Alargue o período ou limpe os filtros.</p>}
      <div className="stock-ledger-footer">
        <span>{ready && rows.length ? `A mostrar ${first}–${first + rows.length - 1} de ${total} ${documentsView ? "documento(s)" : "movimento(s)"}` : ""}</span>
        <div className="actions"><ActionButton disabled={filters.page <= 1} onClick={() => setFilters(f => ({ ...f, page: f.page - 1 }))}>Anterior</ActionButton><span>Página {filters.page}</span><ActionButton disabled={!ledger.more || !ready} onClick={() => setFilters(f => ({ ...f, page: f.page + 1 }))}>Seguinte</ActionButton></div>
      </div>
      <p className="footnote">Movimentos confirmados são preservados. Use devolução para corrigir uma receção ou os ajustes/transferências justificados no módulo de artigos.</p>
    </div>

    {detail && <DetailDrawer aria-labelledby="stock-ledger-detail-title" onRequestClose={() => setDetail(null)} className="stock-ledger-detail">
      <h2 id="stock-ledger-detail-title">Movimento de stock</h2>
      <div className="stock-ledger-detail-head">{article(detail)}<MovementBadge kind={detail.kind} /></div>
      <dl className="stock-ledger-detail-grid">
        <div><dt>Quantidade</dt><dd>{quantity(detail)}</dd></div>
        <div><dt>Stock no armazém</dt><dd>{formatQuantity(detail.stock_before)} → {formatQuantity(detail.stock_after)} {detail.unit}<small>Antes e depois deste movimento, pela ordem de registo</small></dd></div>
        <div><dt>Estado</dt><dd>{detail.original_id ? "Confirmado · ligado ao movimento original" : "Confirmado"}</dd></div>
        <div><dt>Motivo</dt><dd>{detail.reason || "—"}{detail.override_reason && <small>{detail.override_reason}</small>}</dd></div>
        <div><dt>Documento</dt><dd>{detail.document_type} {detail.document}<small>{detail.entity_name || ""}</small></dd></div>
        <div><dt>Origem</dt><dd>{detail.origin_label}</dd></div>
        <div><dt>Data do documento</dt><dd>{day(detail.occurred_on)}</dd></div>
        <div><dt>Registo</dt><dd>{day(lisbonDay(detail.created_at))} {time(detail.created_at)}<small>{detail.user_name}</small></dd></div>
        <div><dt>Armazém</dt><dd>{detail.warehouse_name}</dd></div>
        <div><dt>Lote</dt><dd>{detail.lot_number || "Sem lote"}</dd></div>
        <div><dt>Validade</dt><dd>{detail.expiry ? day(detail.expiry) : "—"}</dd></div>
        <div><dt>Custo unitário</dt><dd>{detail.unit_cost === null ? "Sem acesso ou custo reservado" : money(detail.unit_cost)}</dd></div>
        <div><dt>Valor</dt><dd>{detail.value === null ? "—" : money(detail.value)}{detail.value_delta && <small>Ajuste de valor {money(detail.value_delta)}</small>}</dd></div>
        <div className="is-wide"><dt>Sincronização</dt><dd><SyncStatus state={syncInfo(detail).state} label={syncInfo(detail).label} /><small>{syncInfo(detail).text}</small></dd></div>
        <div className="is-wide"><dt>Referência da operação</dt><dd><code>{detail.operation_id}</code></dd></div>
      </dl>
      <div className="actions">
        {procurementDoc(detail) && <ActionButton className="primary" onClick={() => openDocument(detail)}>Abrir documento</ActionButton>}
        {!filters.group && <ActionButton onClick={() => openLines(detail)}>Ver linhas do documento</ActionButton>}
        <ActionButton onClick={() => filterArticle(detail)}>Filtrar por este artigo</ActionButton>
      </div>
    </DetailDrawer>}
    {salesSync && <StockSalesSync stores={stores} warehouses={warehouses} initialFrom={salesStatus?.next_start} initialTo={salesStatus?.next_end} onClose={() => { setSalesSync(false); setRefresh(v => v + 1); }} onDone={() => setRefresh(v => v + 1)} />}
  </section>;
}
