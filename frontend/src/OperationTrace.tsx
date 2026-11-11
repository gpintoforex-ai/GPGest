import { useEffect, useMemo, useState } from "react";
import { Banknote, CheckCircle2, FileCheck2, FileText, Landmark, Link2, Receipt, Tags } from "lucide-react";
import { StatusBadge, type BusinessTone } from "./components/BusinessUI";
import { ActionButton, ActionLink, Notice } from "./components/InteractionUI";
import { DetailDrawer, PageHeader } from "./components/WorkspaceUI";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";
import { ClassifyDialog } from "./Classification";
import "./OperationTrace.css";

/**
 * Rastreabilidade das operações: cada compra (e cada documento só do ERP) com o estado das ligações
 * e-Fatura → PDF → ERP → rubrica → pagamento → banco, e a ficha com o PDF ao lado dos dados.
 * Só leitura: as ligações confirmam-se nos módulos de origem.
 */

type Row = Record<string, any>;
const DIMS = [
  { key: "efatura", label: "e-Fatura", icon: FileCheck2 }, { key: "pdf", label: "PDF", icon: FileText }, { key: "erp", label: "ERP", icon: Link2 },
  { key: "rubrica", label: "Rubrica", icon: Tags }, { key: "pagamento", label: "Pagamento", icon: Banknote }, { key: "banco", label: "Banco", icon: Landmark },
] as const;
export const STATE_TONE: Record<string, BusinessTone> = {
  confirmada: "success", provavel: "info", varias: "warning", em_falta: "warning", divergencia: "danger", nao_aplicavel: "neutral",
};
const PROBLEMS = ["divergencia", "varias", "em_falta", "provavel"];
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";
/** «FT A/1» em vez de «FT FT A/1» quando o número já traz o tipo. */
export const docLabel = (type: string, number: string) => number?.toUpperCase().startsWith(`${type} `.toUpperCase()) ? number : `${type} ${number}`;
const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function OperationTrace({ stores, navigate }: { stores: Row[]; navigate: (page: string) => void }) {
  const today = new Date();
  const [filters, setFilters] = useState({ start: isoDay(new Date(today.getFullYear(), today.getMonth() - 2, 1)), end: isoDay(today), store: "", attention: true, q: "", dim: "", state: "" });
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState("");
  const [open, setOpen] = useState<Row | null>(null);
  useEffect(() => {
    let active = true;
    setError(""); setData(null);
    api(`/operations?${new URLSearchParams({ start: filters.start, end: filters.end, store_id: filters.store })}`)
      .then(r => { if (active) setData(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [filters.start, filters.end, filters.store]);
  const rows = useMemo(() => (data?.rows || []).filter((r: Row) => (!filters.attention || r.attention.length)
    && (!filters.dim || (filters.state ? r.dimensions[filters.dim].state === filters.state : PROBLEMS.includes(r.dimensions[filters.dim].state)))
    && (!filters.q || `${r.supplier_name} ${r.supplier_nif} ${r.document}`.toLowerCase().includes(filters.q.toLowerCase()))), [data, filters]);
  const set = (changes: Row) => setFilters(current => ({ ...current, ...changes }));

  return <div className="trace">
    <PageHeader title="Rastreabilidade das operações" description="Cada compra do documento original à liquidação: e-Fatura, PDF, ERP, rubrica, pagamento e banco. Mostra só o que precisa de intervenção; as ligações confirmam-se nos módulos de origem." />
    {error && <Notice tone="error">{error}</Notice>}
    <div className="trace-filters filter-bar" role="group" aria-label="Filtrar operações">
      <label>De<input type="date" value={filters.start} onChange={e => set({ start: e.target.value })} /></label>
      <label>Até<input type="date" value={filters.end} onChange={e => set({ end: e.target.value })} /></label>
      <label>Estabelecimento<select value={filters.store} onChange={e => set({ store: e.target.value })}><option value="">Todos</option>
        {stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label>Pesquisar<input type="search" value={filters.q} placeholder="Fornecedor, NIF ou número" onChange={e => set({ q: e.target.value })} /></label>
      <label className="checkbox"><input type="checkbox" checked={filters.attention} onChange={e => set({ attention: e.target.checked })} />Só o que precisa de intervenção</label>
    </div>
    {data && <div className="trace-summary" role="group" aria-label="Resumo por ligação">
      {DIMS.map(({ key, label, icon: Icon }) => {
        const counts = data.summary[key];
        const issues = counts.divergencia + counts.varias + counts.em_falta;
        const active = filters.dim === key;
        return <ActionButton key={key} type="button" className="trace-card ui-tone" data-tone={counts.divergencia ? "danger" : issues ? "warning" : "success"} inferAction={false} aria-pressed={active}
          aria-label={`${label}: ${counts.confirmada} confirmada(s), ${issues} por resolver. ${active ? "Remover filtro" : "Filtrar"}`}
          onClick={() => set(active ? { dim: "", state: "" } : { dim: key, state: "", attention: false })}>
          <span className="trace-card-icon" aria-hidden="true"><Icon size={20} /></span>
          <span className="trace-card-text"><span>{label}</span><strong>{issues}</strong>
            <small>{counts.confirmada} confirmada(s){counts.divergencia ? ` · ${counts.divergencia} divergência(s)` : ""}{counts.nao_aplicavel ? ` · ${counts.nao_aplicavel} n.a.` : ""}</small></span>
        </ActionButton>;
      })}
    </div>}
    {filters.dim && data && <div className="trace-chips" role="group" aria-label="Estado da ligação filtrada">
      <span>{DIMS.find(d => d.key === filters.dim)?.label}:</span>
      {["", ...Object.keys(data.states)].map(state => <ActionButton key={state || "all"} type="button" aria-pressed={filters.state === state} onClick={() => set({ state })}>
        {state ? data.states[state] : "Por resolver"}</ActionButton>)}
    </div>}
    <section className="trace-card-panel" aria-label="Operações">
      {!data ? !error && <p role="status">A carregar operações…</p>
        : <>
          <p className="trace-count">{data.attention} de {data.total} operação(ões) precisam de intervenção · {fullDate(data.start)} a {fullDate(data.end)} · modo {data.mode === "integrated" ? "integrado" : "local"}</p>
          {!rows.length ? <p className="dash-muted">{data.total ? "Nada por resolver com estes filtros." : "Sem documentos de compra no período."}</p>
            : <div className="table-wrap"><table className="trace-grid" aria-label="Operações de compra">
              <thead><tr><th>Data</th><th>Documento</th><th className="numeric">Total</th>{DIMS.map(d => <th key={d.key}>{d.label}</th>)}<th>Ações</th></tr></thead>
              <tbody>{rows.map((r: Row) => <tr key={r.kind + r.id}>
                <td>{fullDate(r.document_date)}</td>
                <td><strong>{r.supplier_name}</strong><small>{docLabel(r.document_type, r.document)} · {r.origin_label}{r.store ? ` · ${r.store}` : ""}</small>
                  {r.attention.length > 0 && <small className="trace-attention">{r.attention.slice(0, 2).join(" · ")}{r.attention.length > 2 ? ` · +${r.attention.length - 2}` : ""}</small>}</td>
                <td className="numeric">{formatEuro(r.amount_cents)}</td>
                {DIMS.map(d => <td key={d.key}><StatusBadge tone={STATE_TONE[r.dimensions[d.key].state]} compact title={r.dimensions[d.key].label}>{r.dimensions[d.key].state_label}</StatusBadge></td>)}
                <td><ActionButton type="button" onClick={() => setOpen(r)} aria-label={`Abrir a ficha de ${docLabel(r.document_type, r.document)}`}>Ficha</ActionButton></td>
              </tr>)}</tbody></table></div>}
        </>}
    </section>
    <ul className="trace-notes">{(data?.notes || []).map((n: string) => <li key={n}>{n}</li>)}</ul>
    {open && <TraceDrawer row={open} onClose={() => setOpen(null)} navigate={page => { setOpen(null); navigate(page); }} />}
  </div>;
}

function FilePreview({ files }: { files: Row[] }) {
  const [selected, setSelected] = useState(0);
  const [preview, setPreview] = useState(""), [image, setImage] = useState(false), [error, setError] = useState("");
  const file = files[selected];
  useEffect(() => {
    const controller = new AbortController(); let objectUrl = "";
    setPreview(""); setError("");
    if (file) fetch(file.view_url, { credentials: "same-origin", signal: controller.signal }).then(async response => {
      if (!response.ok) { const result = await response.json().catch(() => ({})); throw new Error(result.detail || "Não foi possível carregar o ficheiro."); }
      const blob = await response.blob();
      if (!["application/pdf", "image/png", "image/jpeg", "image/webp"].includes(blob.type.split(";")[0])) throw new Error("O servidor não devolveu um PDF ou imagem.");
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setImage(blob.type.startsWith("image/")); setPreview(objectUrl);
    }).catch(e => { if (!controller.signal.aborted) setError((e as Error).message); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [file?.view_url]);
  if (!files.length) return <div className="trace-preview trace-preview-empty"><FileText size={28} aria-hidden="true" /><p>Sem PDF associado. Associe-o na Conferência documental ou no Arquivo.</p></div>;
  return <div className="trace-preview">
    {files.length > 1 && <label>Ficheiro<select value={selected} onChange={e => setSelected(Number(e.target.value))}>
      {files.map((f, i) => <option key={f.id} value={i}>{f.name}{f.source === "arquivo" ? ` (arquivo · ${f.status === "confirmada" ? "confirmado" : "proposta"})` : ""}</option>)}</select></label>}
    {file.source === "arquivo" && file.status === "proposta" && <Notice tone="info">Proposta do arquivo ({file.score} pontos): confirme ou rejeite no Arquivo.</Notice>}
    {error ? <Notice tone="error">{error}</Notice> : !preview ? <p role="status">A carregar o ficheiro…</p>
      : image ? <img src={preview} alt={`Original de ${file.name}`} /> : <iframe title={`Original de ${file.name}`} src={preview} />}
    <ActionLink href={file.view_url} target="_blank" rel="noreferrer">Abrir noutra janela</ActionLink>
  </div>;
}

function value(key: string, v: any) {
  if (v === null || v === undefined || v === "") return "—";
  if (key.endsWith("_cents")) return formatEuro(v);
  if (key === "document_date") return fullDate(v);
  return String(v);
}

function TraceDrawer({ row, onClose, navigate }: { row: Row; onClose: () => void; navigate: (page: string) => void }) {
  const [detail, setDetail] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [classifying, setClassifying] = useState(false), [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    api(`/operations/${row.kind === "erp" ? "erp" : "purchase"}/${encodeURIComponent(row.id)}`).then(r => { if (active) setDetail(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [row.id, row.kind, version]);
  const d = detail || row;
  return <DetailDrawer className="trace-drawer" aria-label={`Ficha da operação ${docLabel(row.document_type, row.document)}`} onRequestClose={onClose}>
    <h2>{d.supplier_name} · {docLabel(d.document_type, d.document)}</h2>
    <p className="trace-sub">{fullDate(d.document_date)} · {formatEuro(d.amount_cents)} · {d.origin_label}{d.store ? ` · ${d.store}` : ""} · NIF {d.supplier_nif}</p>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    <ol className="trace-chain" aria-label="Percurso da operação">
      {DIMS.map(({ key, label, icon: Icon }) => {
        const item = d.dimensions[key];
        return <li key={key} className="ui-tone" data-tone={STATE_TONE[item.state]}>
          <span className="trace-chain-icon" aria-hidden="true">{item.state === "confirmada" ? <CheckCircle2 size={16} /> : <Icon size={16} />}</span>
          <span><b>{label}</b><small>{item.state_label}</small><small>{item.label}</small></span>
        </li>;
      })}
    </ol>
    {d.attention?.length > 0 && <Notice tone="warning">Precisa de intervenção: {d.attention.join(" · ")}</Notice>}
    {!detail ? !error && <p role="status">A carregar a ficha…</p> : <>
      <div className="trace-side">
        <section aria-label="Documento original"><h3>Documento original</h3><FilePreview files={detail.files} /></section>
        <section aria-label="Dados lado a lado"><h3>Dados lado a lado</h3>
          <div className="table-wrap"><table className="trace-compare" aria-label="Comparação dos dados">
            <thead><tr><th>Campo</th><th>{d.kind === "erp" ? "—" : "Documento"}</th><th>ERP</th><th>PDF</th></tr></thead>
            <tbody>{detail.comparison.map((c: Row) => <tr key={c.key} className={c.different ? "is-different" : ""}>
              <th scope="row">{c.label}{c.different && <small>Diferente</small>}</th>
              {["documento", "erp", "pdf"].map(source => <td key={source} className={c.key.endsWith("_cents") ? "numeric" : ""}>{value(c.key, c.values[source])}</td>)}
            </tr>)}</tbody></table></div>
          {detail.erp_candidates?.length > 1 && <p className="dash-muted">{detail.erp_candidates.length} documentos do ERP possíveis: escolha na Conferência documental.</p>}
        </section>
      </div>
      <div className="trace-blocks">
        <section aria-label="Rubricas"><h3><Tags size={16} aria-hidden="true" />Rubricas</h3>
          {detail.rubrics.length ? <ul className="trace-rubrics">{detail.rubrics.map((r: Row, i: number) => <li key={i}><span>{r.category_name}</span><b>{formatEuro(r.base_cents)}</b><small>IVA {formatEuro(r.tax_cents)}</small></li>)}</ul>
            : <p className="dash-muted">Sem repartição.</p>}
          <small>{detail.rubric_source === "linhas" ? "Pelas linhas da compra." : detail.rubric_source === "documento" ? "Definida no documento." : detail.rubric_source === "fornecedor" ? "Rubrica do fornecedor (sugestão)." : "Por classificar."}</small>
          {detail.rubric_source !== "linhas" && <ActionButton type="button" className="trace-classify" onClick={() => setClassifying(true)}>{detail.rubric_source === "documento" ? "Alterar a classificação" : "Classificar…"}</ActionButton>}</section>
        <section aria-label="Vencimento e pagamentos"><h3><Receipt size={16} aria-hidden="true" />Vencimento e pagamentos</h3>
          {detail.planned.map((p: Row) => <p key={p.id}>Vencimento {fullDate(p.due_date)} · {formatEuro(Math.abs(p.amount_cents))} · {p.settled ? "liquidado" : "por liquidar"}</p>)}
          {detail.payments.map((p: Row) => <p key={p.allocation_id}>{fullDate(p.payment_date)} · {formatEuro(p.document_amount_cents)} · {p.method || p.kind} · banco: {p.bank_status}</p>)}
          {!detail.planned.length && !detail.payments.length && <p className="dash-muted">{d.kind === "erp" ? "Valores em aberto no ERP." : "Sem vencimento nem pagamentos registados."}</p>}
          {d.paid_cents != null && <small>Pago {formatEuro(d.paid_cents)} · em falta {formatEuro(d.remaining_cents)}</small>}</section>
        {detail.history?.length > 0 && <section aria-label="Histórico"><h3>Histórico</h3><ul className="trace-history">
          {detail.history.slice(0, 10).map((h: Row) => <li key={h.id}>{fullDate(h.created_at)} · {h.actor} · {h.action.replace(/_/g, " ")}</li>)}</ul></section>}
      </div>
    </>}
    {classifying && <ClassifyDialog kind={row.kind} id={row.id} onClose={() => setClassifying(false)}
      onSaved={message => { setClassifying(false); setNotice(message); setVersion(v => v + 1); }} />}
    <div className="ui-dialog-actions">
      <ActionButton type="button" onClick={() => navigate("Conferência documental")}>Abrir a Conferência documental</ActionButton>
      <ActionButton type="button" onClick={() => navigate("Arquivo — Visão geral")}>Abrir o Arquivo</ActionButton>
      <ActionButton type="button" className="primary" onClick={onClose}>Fechar</ActionButton>
    </div>
  </DetailDrawer>;
}
