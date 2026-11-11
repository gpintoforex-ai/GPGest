import { useEffect, useState } from "react";
import { FileText, Link2, Paperclip, Search, Undo2 } from "lucide-react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";

/**
 * Documentos do Arquivo (comprovativos, declarações, contratos, apólices, documentos de viaturas) e registos reais
 * (fatura, ERP, banco, pagamento) associados a prazos, contratos e viaturas. Reutiliza o Arquivo: o original fica
 * onde está e vê-se na aplicação. Associar um registo real não conclui o prazo.
 */

type Row = Record<string, any>;
const fullDate = (iso?: string | null) => iso ? iso.slice(0, 10).split("-").reverse().join("/") : "—";

function Preview({ url, name, onClose }: { url: string; name: string; onClose: () => void }) {
  const [src, setSrc] = useState(""), [image, setImage] = useState(false), [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController(); let objectUrl = "";
    fetch(url, { credentials: "same-origin", signal: controller.signal }).then(async response => {
      if (!response.ok) { const result = await response.json().catch(() => ({})); throw new Error(result.detail || "Não foi possível abrir o documento."); }
      const blob = await response.blob();
      objectUrl = URL.createObjectURL(blob); setImage(blob.type.startsWith("image/")); setSrc(objectUrl);
    }).catch(e => { if (!controller.signal.aborted) setError((e as Error).message); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [url]);
  return <AppDialog className="obl-preview" aria-label={`Documento ${name}`} onRequestClose={onClose}>
    <h2>{name}</h2>
    {error ? <Notice tone="error">{error}</Notice> : !src ? <p role="status">A abrir o documento…</p> : image ? <img src={src} alt={`Original de ${name}`} /> : <iframe title={`Original de ${name}`} src={src} />}
    <div className="ui-dialog-actions"><ActionButton type="button" className="primary" onClick={onClose}>Fechar</ActionButton></div>
  </AppDialog>;
}

/** Documentos do Arquivo associados a um prazo, obrigação, contrato ou viatura. */
export function ArchiveDocs({ kind, id, title = "Documentos" }: { kind: "obligation_occurrence" | "obligation" | "contract" | "vehicle"; id: string; title?: string }) {
  const [docs, setDocs] = useState<Row[] | null>(null), [error, setError] = useState(""), [version, setVersion] = useState(0);
  const [searching, setSearching] = useState(false), [q, setQ] = useState(""), [results, setResults] = useState<Row[] | null>(null), [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Row | null>(null);
  useEffect(() => {
    let active = true;
    api(`/obligations/documents?${new URLSearchParams({ kind, id })}`).then(r => { if (active) setDocs(r); }).catch(e => { if (active) setError((e as Error).message); });
    return () => { active = false; };
  }, [kind, id, version]);
  async function search() {
    setBusy(true); setError("");
    try { setResults((await api(`/archive/documents?${new URLSearchParams({ q, limit: "20" })}`)).documents); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function attach(documentId: string) {
    setBusy(true); setError("");
    try { await api("/archive/links/manual", { document_id: documentId, target_kind: kind, target_id: id }); setSearching(false); setResults(null); setQ(""); setVersion(v => v + 1); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="obl-docs" aria-label={title}>
    <div className="obl-docs-head"><h3><Paperclip size={16} aria-hidden="true" />{title}</h3>
      <ActionButton type="button" onClick={() => setSearching(!searching)}><Link2 size={15} aria-hidden="true" />Associar do arquivo</ActionButton></div>
    {error && <Notice tone="error">{error}</Notice>}
    {searching && <div className="obl-docs-search" role="group" aria-label="Pesquisa no arquivo">
      <label>Procurar no arquivo<input type="search" value={q} placeholder="Nome do ficheiro, entidade, número" onChange={e => setQ(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void search(); } }} /></label>
      <ActionButton type="button" disabled={busy} onClick={() => void search()}><Search size={15} aria-hidden="true" />Procurar</ActionButton>
      {results && (results.length ? <ul className="obl-docs-list">{results.map(r => <li key={r.id}><span><b>{r.original_name || r.document_number || "Documento"}</b>
        <small>{[r.entity_name, fullDate(r.issue_date), r.total_cents != null ? formatEuro(r.total_cents) : ""].filter(Boolean).join(" · ")}</small></span>
        <ActionButton type="button" disabled={busy} onClick={() => void attach(r.id)} aria-label={`Associar ${r.original_name || "documento"}`}>Associar</ActionButton></li>)}</ul>
        : <p className="dash-muted">Sem documentos com esta pesquisa. Carregue-o primeiro no Arquivo.</p>)}
    </div>}
    {!docs ? !error && <p role="status">A carregar…</p> : !docs.length ? <p className="dash-muted">Sem documentos associados.</p>
      : <ul className="obl-docs-list">{docs.map(d => <li key={d.link_id}><span><FileText size={15} aria-hidden="true" /><b>{d.name}</b>
        <small>{[fullDate(d.issue_date), d.total_cents != null ? formatEuro(d.total_cents) : "", d.status === "proposta" ? "proposta do arquivo" : ""].filter(Boolean).join(" · ")}</small></span>
        <ActionButton type="button" onClick={() => setPreview(d)} aria-label={`Ver ${d.name}`}>Ver</ActionButton></li>)}</ul>}
    {preview && <Preview url={preview.file_url} name={preview.name} onClose={() => setPreview(null)} />}
  </section>;
}

/** Registos reais (fatura, ERP, banco, pagamento) propostos e associados a um prazo. */
export function RealRecords({ occurrenceId, onComplete }: { occurrenceId: string; onComplete?: (amountCents: number) => void }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [undoing, setUndoing] = useState<Row | null>(null), [reason, setReason] = useState("");
  useEffect(() => { api(`/obligations/occurrences/${encodeURIComponent(occurrenceId)}/matches`).then(setData).catch(e => setError((e as Error).message)); }, [occurrenceId]);
  async function run(action: () => Promise<Row>) {
    setBusy(true); setError("");
    try { setData(await action()); setUndoing(null); setReason(""); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const active = (data?.links || []).filter((l: Row) => !l.undone_at);
  return <section className="obl-docs" aria-label="Registos reais">
    <div className="obl-docs-head"><h3><Link2 size={16} aria-hidden="true" />Fatura ou pagamento real</h3></div>
    {error && <Notice tone="error">{error}</Notice>}
    {!data ? !error && <p role="status">A procurar…</p> : <>
      {active.length > 0 && <ul className="obl-docs-list">{active.map((l: Row) => <li key={l.id}><span><StatusBadge tone="success" compact>Associado</StatusBadge>
        <b>{l.entity} · {l.label}</b><small>{l.source_label} · {formatEuro(l.amount_cents)} · {l.created_by}</small></span>
        <span className="obl-docs-actions">{onComplete && data.status === "pendente" && <ActionButton type="button" onClick={() => onComplete(l.amount_cents)}>Concluir com {formatEuro(l.amount_cents)}</ActionButton>}
          <ActionButton type="button" onClick={() => setUndoing(l)} aria-label={`Anular a associação de ${l.label}`}><Undo2 size={15} aria-hidden="true" />Anular</ActionButton></span></li>)}</ul>}
      {data.proposals.length ? <ul className="obl-docs-list">{data.proposals.map((p: Row) => <li key={p.source_kind + p.source_id}><span>
        <StatusBadge tone={p.score >= 85 ? "success" : "info"} compact>{`Proposta · ${p.score} %`}</StatusBadge><b>{p.entity} · {p.label}</b>
        <small>{p.source_label} · {fullDate(p.date)} · {formatEuro(p.amount_cents)} · {p.reasons.join(" · ")}{p.used_elsewhere ? " · já associado a outro prazo" : ""}</small></span>
        <ActionButton type="button" disabled={busy} onClick={() => void run(() => api(`/obligations/occurrences/${encodeURIComponent(occurrenceId)}/links`, { source_kind: p.source_kind, source_id: p.source_id, reasons: p.reasons }))}
          aria-label={`Associar ${p.label}`}>Associar</ActionButton></li>)}</ul>
        : !active.length && <p className="dash-muted">Sem fatura ou pagamento compatível (entidade, valor e data até 45 dias).</p>}
      <ul className="obl-notes">{data.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
    </>}
    {undoing && <AppDialog className="obl-action-dialog" aria-label="Anular associação" busy={busy} onRequestClose={() => setUndoing(null)}>
      <h2>Anular associação</h2><p>{undoing.entity} · {undoing.label} · {formatEuro(undoing.amount_cents)}</p>
      <label>Motivo<textarea rows={3} value={reason} maxLength={2000} onChange={e => setReason(e.target.value)} /></label>
      <div className="ui-dialog-actions"><ActionButton type="button" onClick={() => setUndoing(null)}>Cancelar</ActionButton>
        <ActionButton type="button" className="primary" disabled={busy || reason.trim().length < 3}
          onClick={() => void run(() => api(`/obligation-links/${encodeURIComponent(undoing.id)}/undo`, { reason: reason.trim() }))}>Anular associação</ActionButton></div>
    </AppDialog>}
  </section>;
}

/** Janela de um prazo: comprovativos do arquivo e registos reais, com conclusão pelo valor do registo. */
export function OccurrenceDocuments({ item, onClose, onComplete }: { item: Row; onClose: () => void; onComplete: (amountCents: number) => void }) {
  return <AppDialog className="obl-docs-dialog" aria-label={`Documentos e associações de ${item.title}`} onRequestClose={onClose}>
    <h2>{item.title}</h2>
    <p className="obl-hint">Data-limite {fullDate(item.due_date)}{item.expected_cents != null ? ` · previsto ${formatEuro(item.expected_cents)}` : ""}</p>
    <ArchiveDocs kind="obligation_occurrence" id={item.id} title="Comprovativos e declarações" />
    <RealRecords occurrenceId={item.id} onComplete={item.status === "pendente" ? onComplete : undefined} />
    <div className="ui-dialog-actions"><ActionButton type="button" className="primary" onClick={onClose}>Fechar</ActionButton></div>
  </AppDialog>;
}


