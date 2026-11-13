import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw, Search, Sparkles, Trash2, X } from "lucide-react";
import { StatusBadge } from "./components/BusinessUI";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import "./Drilldown.css"; // cabeçalho das janelas de detalhe (sem o estilo global da barra superior)
import "./ArticleWebContent.css";

/**
 * Descritivo dos artigos para o site com IA. A IA escreve uma proposta a partir da ficha e das imagens do artigo;
 * nada é publicado sem revisão: na ficha, a proposta passa para o formulário (grava-se com a ficha); no lote, cada
 * proposta é revista, editada e aprovada. As frases a confirmar (alegações de saúde, advertências) vêm assinaladas.
 */

type Row = Record<string, any>;
export type WebFields = { description: string; web_summary: string; web_highlights: string[]; seo_title: string; seo_description: string };
const STATUS: Record<string, [string, "warning" | "success" | "neutral" | "info"]> = {
  pendente: ["Por rever", "warning"], aprovada: ["Aprovada", "success"], aplicada: ["Aplicada na ficha", "success"], descartada: ["Descartada", "neutral"],
};
const COST = "Cada proposta gasta créditos da conta Anthropic configurada no servidor.";

function Review({ proposal }: { proposal: Row }) {
  return <>
    {proposal.needs_review?.length > 0 && <div className="awc-warning" role="note">
      <strong><AlertTriangle size={15} aria-hidden="true" />Confirme antes de publicar</strong>
      <ul>{proposal.needs_review.map((item: string) => <li key={item}>{item}</li>)}</ul>
      <small>Alegações de saúde e advertências só podem aparecer como estão no rótulo.</small>
    </div>}
    {proposal.missing_information?.length > 0 && <p className="awc-hint">Em falta nos dados: {proposal.missing_information.join("; ")}.</p>}
    <p className="awc-hint">Fontes: {(proposal.sources_used || []).map((s: string) => s === "imagem" ? "imagens do artigo" : "ficha do artigo").join(" e ") || "ficha do artigo"}
      {proposal.images_used === false && " (as imagens não puderam ser lidas)"} · {proposal.model}</p>
  </>;
}

/** Na ficha do artigo: gerar e usar a proposta no formulário. */
export function WebContentAssistant({ articleId, readOnly, onUse }: { articleId?: string; readOnly?: boolean; onUse: (fields: WebFields) => void }) {
  const [state, setState] = useState<Row | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState("");
  useEffect(() => {
    if (!articleId) return;
    api(`/stock/articles/${articleId}/web-content`).then(setState).catch(e => setError((e as Error).message));
  }, [articleId]);
  if (!articleId) return <p className="awc-hint">Guarde o artigo para gerar o descritivo com IA.</p>;
  const run = async (action: () => Promise<Row>) => {
    setBusy(true); setError("");
    try { setState({ ...state, ...(await action()) }); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const proposal = state?.proposal;
  const pending = proposal?.status === "pendente";
  return <section className="awc" aria-label="Descritivo com IA">
    <div className="awc-head">
      <ActionButton type="button" className="primary" disabled={busy || readOnly || state?.configured === false}
        onClick={() => void run(() => api(`/stock/articles/${articleId}/web-content`, {}))}>
        {busy ? <RefreshCw size={15} aria-hidden="true" className="awc-spin" /> : <Sparkles size={15} aria-hidden="true" />}
        {busy ? "A gerar a proposta…" : pending ? "Gerar outra proposta" : "Gerar descritivo com IA"}</ActionButton>
      {proposal && <StatusBadge tone={STATUS[proposal.status]?.[1] || "neutral"} compact>{STATUS[proposal.status]?.[0] || proposal.status}</StatusBadge>}
    </div>
    {state?.configured === false && <Notice tone="info">A IA não está configurada: indique ANTHROPIC_API_KEY no ficheiro .env do servidor.</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    <p className="awc-hint">{COST} A proposta não altera a ficha: reveja-a, use-a no formulário e grave o artigo.</p>
    {pending && <div className="awc-proposal">
      <h4>Proposta</h4>
      <p className="awc-text">{proposal.fields.description}</p>
      <dl className="awc-facts">
        <div><dt>Resumo</dt><dd>{proposal.fields.web_summary}</dd></div>
        <div><dt>Pontos-chave</dt><dd><ul>{proposal.fields.web_highlights.map((h: string) => <li key={h}>{h}</li>)}</ul></dd></div>
        <div><dt>Título SEO</dt><dd>{proposal.fields.seo_title}</dd></div>
        <div><dt>Meta-descrição</dt><dd>{proposal.fields.seo_description}</dd></div>
      </dl>
      <Review proposal={proposal} />
      <div className="awc-actions">
        <ActionButton type="button" className="primary" disabled={readOnly} onClick={() => onUse(proposal.fields)}><CheckCircle2 size={15} aria-hidden="true" />Usar no formulário</ActionButton>
        <ActionButton type="button" disabled={busy || readOnly} onClick={() => void run(() => api(`/stock/articles/${articleId}/web-content/discard`, {}))}><Trash2 size={15} aria-hidden="true" />Descartar</ActionButton>
      </div>
    </div>}
  </section>;
}

function ReviewDialog({ article, onClose, onDone }: { article: Row; onClose: () => void; onDone: (message: string) => void }) {
  const [state, setState] = useState<Row | null>(null), [fields, setFields] = useState<WebFields | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const load = (value: Row) => { setState(value); setFields(value.proposal?.status === "pendente" ? value.proposal.fields : null); };
  useEffect(() => { api(`/stock/articles/${article.id}/web-content`).then(load).catch(e => setError((e as Error).message)); }, [article.id]);
  const run = async (action: () => Promise<Row>, message?: string) => {
    setBusy(true); setError("");
    try { const value = await action(); if (message) onDone(message); else load(value); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const set = (key: keyof WebFields, value: any) => setFields(current => current ? { ...current, [key]: value } : current);
  const counter = (value: string, max: number) => <small className={value.length > max ? "awc-over" : "awc-count"}>{value.length}/{max}</small>;
  return <AppDialog className="awc-dialog" aria-label={`Descritivo do site: ${article.name}`} guardChanges={false} onRequestClose={onClose}>
    <header className="drilldown-header"><h2>{article.name}</h2><p>{article.code}{article.brand && ` · ${article.brand}`} · descritivo para o site</p></header>
    <ActionButton type="button" className="awc-close" aria-label="Fechar" onClick={onClose}><X size={16} /></ActionButton>
    {error && <Notice tone="error">{error}</Notice>}
    {!state ? <p role="status">A carregar…</p> : !fields ? <>
      <Notice tone="info">Sem proposta por rever para este artigo.</Notice>
      <ActionButton type="button" className="primary" disabled={busy} onClick={() => void run(() => api(`/stock/articles/${article.id}/web-content`, {}))}><Sparkles size={15} aria-hidden="true" />{busy ? "A gerar…" : "Gerar proposta"}</ActionButton>
    </> : <form className="awc-form" onSubmit={e => { e.preventDefault(); void run(() => api(`/stock/articles/${article.id}/web-content/approve`, { ...fields, revision: state.revision }), `Descritivo de «${article.name}» aprovado e publicado na ficha.`); }}>
      <label>Descrição<textarea rows={7} value={fields.description} maxLength={4000} onChange={e => set("description", e.target.value)} />{counter(fields.description, 4000)}</label>
      <label>Resumo<textarea rows={2} value={fields.web_summary} maxLength={300} onChange={e => set("web_summary", e.target.value)} />{counter(fields.web_summary, 300)}</label>
      <label>Pontos-chave (um por linha)<textarea rows={4} value={fields.web_highlights.join("\n")} onChange={e => set("web_highlights", e.target.value.split("\n").slice(0, 8))} /></label>
      <label>Título SEO<input value={fields.seo_title} maxLength={70} onChange={e => set("seo_title", e.target.value)} />{counter(fields.seo_title, 60)}</label>
      <label>Meta-descrição<textarea rows={2} value={fields.seo_description} maxLength={170} onChange={e => set("seo_description", e.target.value)} />{counter(fields.seo_description, 155)}</label>
      <Review proposal={state.proposal} />
      <div className="awc-actions">
        <ActionButton type="submit" className="primary" disabled={busy}><CheckCircle2 size={15} aria-hidden="true" />Aprovar e gravar na ficha</ActionButton>
        <ActionButton type="button" disabled={busy} onClick={() => void run(() => api(`/stock/articles/${article.id}/web-content`, {}))}><RefreshCw size={15} aria-hidden="true" />Gerar outra</ActionButton>
        <ActionButton type="button" disabled={busy} onClick={() => void run(() => api(`/stock/articles/${article.id}/web-content/discard`, {}), `Proposta de «${article.name}» descartada.`)}><Trash2 size={15} aria-hidden="true" />Descartar</ActionButton>
      </div>
      <p className="awc-hint">Aprovar grava a descrição, o resumo, os pontos-chave e o SEO na ficha do artigo; a loja online recebe-os pelo catálogo.</p>
    </form>}
  </AppDialog>;
}

/** Lote: artigos sem descritivo e propostas por rever. A geração é feita um artigo de cada vez, com progresso. */
export function WebContentQueue() {
  const [state, setState] = useState("sem_descritivo"), [search, setSearch] = useState(""), [offset, setOffset] = useState(0);
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<string[]>([]), [progress, setProgress] = useState<{ done: number; total: number; failed: string[] } | null>(null);
  const [reviewing, setReviewing] = useState<Row | null>(null), [version, setVersion] = useState(0);
  const stop = useRef(false);
  useEffect(() => {
    let active = true;
    setData(null);
    const timer = window.setTimeout(() => api(`/stock/web-content?${new URLSearchParams({ state, q: search, offset: String(offset) })}`)
      .then(value => { if (active) setData(value); }).catch(e => { if (active) setError((e as Error).message); }), search ? 300 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [state, search, offset, version]);
  async function generate() {
    stop.current = false;
    const ids = [...selected];
    const failed: string[] = [];
    setProgress({ done: 0, total: ids.length, failed });
    for (const [index, id] of ids.entries()) {
      if (stop.current) break;
      try { await api(`/stock/articles/${id}/web-content`, {}, { timeoutMs: 180_000 }); }
      catch (e) { failed.push(`${data?.rows.find((r: Row) => r.id === id)?.name || id}: ${(e as Error).message}`); }
      setProgress({ done: index + 1, total: ids.length, failed: [...failed] });
    }
    setSelected([]); setVersion(v => v + 1);
    setNotice(`${ids.length - failed.length} proposta(s) geradas e por rever.${failed.length ? ` ${failed.length} com erro.` : ""}`);
  }
  const rows: Row[] = data?.rows || [];
  const running = !!progress && progress.done < progress.total && !stop.current;
  return <section className="panel awc-queue" aria-label="Descritivos do site">
    <div className="awc-queue-head"><h2><Sparkles size={18} aria-hidden="true" />Descritivos para o site</h2>
      <p className="awc-hint">A IA propõe a descrição, o resumo, os pontos-chave e o SEO de cada artigo a partir da ficha e das imagens. Nada é publicado sem revisão. {COST}</p></div>
    {data?.configured === false && <Notice tone="info">A IA não está configurada: indique ANTHROPIC_API_KEY no ficheiro .env do servidor.</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success" onDismiss={() => setNotice("")}>{notice}</Notice>}
    <div className="awc-toolbar">
      <div className="awc-tabs" role="group" aria-label="Artigos a mostrar">
        {([["sem_descritivo", "Sem descritivo"], ["por_rever", "Propostas por rever"]] as const).map(([key, label]) =>
          <button key={key} type="button" aria-pressed={state === key} onClick={() => { setState(key); setOffset(0); setSelected([]); }}>{label}</button>)}
      </div>
      <label className="awc-search"><Search size={15} aria-hidden="true" /><input aria-label="Pesquisar artigo" placeholder="Código, nome ou marca…" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label>
      {state === "sem_descritivo" && <ActionButton type="button" className="primary" disabled={!selected.length || running || data?.configured === false} onClick={() => void generate()}>
        <Sparkles size={15} aria-hidden="true" />Gerar propostas ({selected.length})</ActionButton>}
      {running && <ActionButton type="button" onClick={() => { stop.current = true; }}>Parar</ActionButton>}
    </div>
    {progress && <div className="awc-progress" role="status">
      <progress max={progress.total} value={progress.done} />{progress.done} de {progress.total} artigo(s){running ? " — a gerar…" : ""}
      {progress.failed.length > 0 && <ul>{progress.failed.map(f => <li key={f}>{f}</li>)}</ul>}
    </div>}
    {!data ? <p role="status">A carregar…</p> : !rows.length ? <p className="awc-hint">{state === "sem_descritivo" ? "Todos os artigos ativos têm descritivo." : "Sem propostas por rever."}</p> :
      <div className="table-wrap"><table className="awc-table" aria-label="Artigos para o descritivo do site">
        <thead><tr>{state === "sem_descritivo" && <th><input type="checkbox" aria-label="Selecionar os artigos visíveis (até 25)" checked={rows.slice(0, 25).every(r => selected.includes(r.id))}
          onChange={e => setSelected(e.target.checked ? rows.slice(0, 25).map(r => r.id) : [])} /></th>}<th>Código</th><th>Artigo</th><th>Marca</th><th>Imagens</th><th>Estado</th><th><span className="awc-sr">Ações</span></th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.id}>
          {state === "sem_descritivo" && <td><input type="checkbox" aria-label={`Selecionar ${r.name}`} checked={selected.includes(r.id)} disabled={!selected.includes(r.id) && selected.length >= 25}
            onChange={e => setSelected(e.target.checked ? [...selected, r.id] : selected.filter(id => id !== r.id))} /></td>}
          <td>{r.code}</td><td>{r.name}{r.family && <small>{r.family}</small>}</td><td>{r.brand || "—"}</td>
          <td>{r.has_images ? "Sim" : <small className="awc-muted">Sem imagem: texto mais genérico</small>}</td>
          <td>{r.proposal_status ? <StatusBadge tone={STATUS[r.proposal_status]?.[1] || "neutral"} compact>{STATUS[r.proposal_status]?.[0]}</StatusBadge> : "Sem descritivo"}</td>
          <td><ActionButton type="button" onClick={() => setReviewing(r)}>{r.proposal_status === "pendente" ? "Rever" : "Abrir"}</ActionButton></td>
        </tr>)}</tbody>
      </table></div>}
    {data && data.total > 50 && <div className="awc-pages"><span>{offset + 1}–{Math.min(offset + 50, data.total)} de {data.total}</span>
      <ActionButton type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>Anterior</ActionButton>
      <ActionButton type="button" disabled={offset + 50 >= data.total} onClick={() => setOffset(offset + 50)}>Seguinte</ActionButton></div>}
    {reviewing && <ReviewDialog article={reviewing} onClose={() => { setReviewing(null); setVersion(v => v + 1); }}
      onDone={message => { setNotice(message); setReviewing(null); setVersion(v => v + 1); }} />}
  </section>;
}
