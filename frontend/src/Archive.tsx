import { useEffect, useRef, useState } from "react";
import { ModuleTabs, useWorkspaceTab } from "./components/WorkspaceUI";
import type { ReactNode } from "react";
import { Archive as ArchiveIcon, CheckCircle2, Download, FileSearch, Link2, RefreshCw, Search, ShieldCheck, Upload, XCircle } from "lucide-react";
import { api } from "./lib/api";
import { ManagedForm, ActionButton, AppDialog, Notice, confirmAction } from "./components/InteractionUI";
import { Field } from "./components/Form";
import { MetricCard, StatusBadge } from "./components/BusinessUI";
import AssistantIdentity from "./components/AssistantIdentity";
import { formatEuro } from "./utils/format";
import "./DocumentControl.css";
import "./Archive.css";

type Row = Record<string, any>;
type Store = Row;
export const ARCHIVE_PAGES = ["Arquivo — Visão geral", "Caixa de entrada", "Todos os documentos", "Associações", "Pendências", "Dossiês contabilísticos", "Arquivo — Auditoria", "Arquivo — Configurações"] as const;

const TYPE_LABELS: Row = { fatura: "Fatura", fatura_recibo: "Fatura-recibo", fatura_simplificada: "Fatura simplificada", nota_credito: "Nota de crédito", nota_debito: "Nota de débito", recibo: "Recibo", comprovativo_pagamento: "Comprovativo de pagamento", extrato_bancario: "Extrato bancário", despesa: "Despesa", documento_caixa: "Documento de caixa", contrato: "Contrato", outro: "Outro", por_classificar: "Por classificar" };
const STATUS: Row = { pendente: ["A processar", "neutral"], processado: ["Processado", "success"], erro: ["Erro", "danger"], duplicado: ["Duplicado", "warning"] };
const RELATION: Row = { associado: ["Associado", "success"], proposta: ["Proposta por validar", "warning"], sem_relacao: ["Sem relação", "neutral"] };
const LINK_STATUS: Row = { proposta: ["Proposta", "warning"], confirmada: ["Confirmada", "success"], rejeitada: ["Rejeitada", "neutral"], anulada: ["Anulada", "neutral"] };
const STRENGTH: Row = { forte: "Forte", possivel: "Possível", ambigua: "Ambígua", manual: "Manual", existente: "Já registada" };
const SOURCES: Row = { qr: "QR fiscal", text: "Texto do PDF", texto: "Texto", ocr: "OCR", correcao: "Correção manual", registo: "Registo", ficha_entidade: "Ficha da entidade", compras: "Compras" };
const ACTIONS: Row = { indexado: "Indexado", carregado: "Carregado", processado: "Processado", corrigido: "Corrigido", revisto: "Revisto", visualizado: "Visualizado", descarregado: "Descarregado", erro_processamento: "Erro de processamento", duplicado_detetado: "Duplicado detetado", associacao_proposta: "Associação proposta", associacao_confirmada: "Associação confirmada", associacao_rejeitada: "Associação rejeitada", associacao_anulada: "Associação anulada", integridade_falhou: "Integridade falhou", integridade_verificada: "Integridade verificada", reprocessamento_pedido: "Reprocessamento pedido" };
const FIELDS: [string, string, "text" | "date" | "money"][] = [["issue_date", "Data de emissão", "date"], ["entity_nif", "NIF da entidade", "text"], ["entity_name", "Entidade", "text"], ["document_code", "Tipo fiscal (FT, FR, NC…)", "text"], ["document_number", "Número do documento", "text"], ["base_cents", "Base tributável", "money"], ["tax_cents", "IVA", "money"], ["total_cents", "Total", "money"], ["currency", "Moeda", "text"], ["payment_reference", "Referência de pagamento", "text"]];
const when = (value?: string | null) => value ? new Date(value).toLocaleString("pt-PT", { timeZone: "Europe/Lisbon", dateStyle: "short", timeStyle: "short" }) : "—";
const day = (value?: string | null) => value ? value.slice(0, 10).split("-").reverse().join("/") : "—";
const size = (bytes: number) => bytes ? `${(bytes / 1048576).toLocaleString("pt-PT", { maximumFractionDigits: 2 })} MB` : "—";

/** Euros escritos pelo utilizador → cêntimos inteiros, sem vírgula flutuante. */
export function toCents(value: string): number | null | undefined {
  let text = value.trim().replace(/\s|€/g, "");
  if (!text) return null;
  if (text.includes(",")) text = text.replace(/\./g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return undefined;
  const [whole, decimals = ""] = text.split(".");
  return Number(whole) * 100 + Number(decimals.padEnd(2, "0"));
}
export const documentLabel = (code: string, number: string) => !code || number.toUpperCase().startsWith(code.toUpperCase() + " ") ? number : `${code} ${number}`;
const centsText = (cents: number | null | undefined) => cents == null ? "" : (cents / 100).toFixed(2).replace(".", ",");

function Badge({ map, value }: { map: Row; value: string }) {
  const [label, tone] = map[value] || [value, "neutral"];
  return <StatusBadge tone={tone}>{label}</StatusBadge>;
}

function Confidence({ value, source }: { value?: number; source?: string }) {
  if (value == null) return null;
  const tone = value >= 0.9 ? "success" : value >= 0.7 ? "warning" : "danger";
  const label = value >= 0.9 ? "Alta" : value >= 0.7 ? "Média" : "Baixa";
  return <span className={`arc-confidence arc-${tone}`} title={`Confiança ${Math.round(value * 100)}% · ${SOURCES[source || ""] || source || ""}`}>{label} · {SOURCES[source || ""] || source}</span>;
}

function DocumentTable({ rows, onOpen, selected, onSelect, empty = "Sem documentos." }: { rows: Row[]; onOpen: (id: string) => void; selected?: Set<string>; onSelect?: (ids: Set<string>) => void; empty?: string }) {
  if (!rows.length) return <p className="arc-empty">{empty}</p>;
  const all = !!selected && rows.every(r => selected.has(r.id));
  return <div className="table-wrap"><table className="arc-table">
    <thead><tr>
      {onSelect && <th><input type="checkbox" aria-label="Selecionar todos" checked={all} onChange={() => onSelect(all ? new Set() : new Set(rows.map(r => r.id)))} /></th>}
      <th>Documento</th><th>Tipo</th><th>Entidade</th><th>Emissão</th><th className="numeric">Total</th><th>Estado</th><th>Relação</th>
    </tr></thead>
    <tbody>{rows.map(row => <tr key={row.id}>
      {onSelect && <td><input type="checkbox" aria-label={`Selecionar ${row.original_name}`} checked={selected!.has(row.id)} onChange={() => { const next = new Set(selected); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); onSelect(next); }} /></td>}
      <td><ActionButton type="button" className="arc-link" onClick={() => onOpen(row.id)}>{row.original_name || "Documento"}</ActionButton><small>{row.origin_label}{row.store ? ` · ${row.store}` : ""} · {when(row.received_at)}</small></td>
      <td>{row.type_label}</td>
      <td>{row.entity_name || row.entity_nif || "—"}{row.document_number && <small>{documentLabel(row.document_code, row.document_number)}</small>}</td>
      <td>{day(row.issue_date)}</td>
      <td className="numeric">{formatEuro(row.total_cents)}</td>
      <td>{row.duplicate_of ? <Badge map={STATUS} value="duplicado" /> : <Badge map={STATUS} value={row.processing_status} />}{row.review_status === "revisto" ? <small>Revisto</small> : <small>Por rever</small>}</td>
      <td><Badge map={RELATION} value={row.relation} /></td>
    </tr>)}</tbody>
  </table></div>;
}

function LinkCard({ link, canManage, onDecide, navigate, showDocument }: { link: Row; canManage: boolean; onDecide: (link: Row, decision: string) => void; navigate: (page: string) => void; showDocument?: (id: string) => void }) {
  const target = link.target || {};
  return <article className={`arc-link-card arc-${link.strength}`}>
    <header>
      <strong>{link.target_label}: {target.label || "registo indisponível"}</strong>
      <Badge map={LINK_STATUS} value={link.status} />
    </header>
    {link.document && <p><ActionButton type="button" className="arc-link" onClick={() => showDocument?.(link.document.id)}>{link.document.original_name}</ActionButton> · {link.document.entity_name || link.document.entity_nif || "Entidade por identificar"} · {formatEuro(link.document.total_cents)}</p>}
    <p className="arc-muted">{target.entity}{target.date ? ` · ${day(target.date)}` : ""}{target.amount_cents != null ? ` · ${formatEuro(target.amount_cents)}` : ""}{target.store ? ` · ${target.store}` : ""}{target.detail ? ` · ${target.detail}` : ""}</p>
    <p><b>Pontuação {link.score}</b> · {STRENGTH[link.strength] || link.strength}{link.amount_cents != null ? ` · valor imputado ${formatEuro(link.amount_cents)}` : ""}</p>
    <ul className="arc-reasons">{(link.reasons || []).map((reason: string) => <li key={reason}>{reason}</li>)}</ul>
    <footer>
      {link.page && <ActionButton type="button" className="arc-link" onClick={() => navigate(link.page)}>Abrir {link.page}</ActionButton>}
      {canManage && link.status === "proposta" && <>
        <ActionButton type="button" className="primary" onClick={() => onDecide(link, "confirmar")}><CheckCircle2 size={16} aria-hidden="true" />Confirmar</ActionButton>
        <ActionButton type="button" onClick={() => onDecide(link, "rejeitar")}><XCircle size={16} aria-hidden="true" />Rejeitar</ActionButton>
      </>}
      {canManage && link.status === "confirmada" && link.method !== "associacao_existente" && <ActionButton type="button" onClick={() => onDecide(link, "anular")}>Anular</ActionButton>}
    </footer>
  </article>;
}

async function decideLinks(links: Row[], decision: string) {
  const ids = links.map(l => l.id);
  let reason = "";
  if (decision === "anular") {
    reason = window.prompt("Motivo da anulação (mínimo 3 caracteres):")?.trim() || "";
    if (reason.length < 3) return null;
  }
  const preview = await api("/archive/links/decide", { ids, decision, reason });
  const label = decision === "confirmar" ? "Confirmar" : decision === "rejeitar" ? "Rejeitar" : "Anular";
  const ambiguous = preview.ambiguous?.length ? ` ${preview.ambiguous.length} associação(ões) é(são) ambígua(s): confirme que escolheu o registo certo.` : "";
  const effect = decision === "confirmar" ? " Nas compras, o documento fica anexado à ficha (sem duplicar ficheiros Drive)." : "";
  if (!(await confirmAction({ title: `${label} ${ids.length} associação(ões)?`, message: `${preview.items.map((i: Row) => `${i.target?.label || i.target_label} (${i.score})`).slice(0, 6).join("; ")}${ids.length > 6 ? "…" : ""}.${effect}${ambiguous}`, confirmLabel: label, tone: decision === "confirmar" ? "primary" : "warning" }))) return null;
  return api("/archive/links/decide", { ids, decision, reason, confirm: true });
}

/** Detalhe: pré-visualização do original, dados extraídos com confiança, correções, relações e histórico. */
export function DocumentDetail({ id, stores, canManage, onClose, onChanged, navigate }: { id: string; stores: Store[]; canManage: boolean; onClose: () => void; onChanged: () => void; navigate: (page: string) => void }) {
  const [doc, setDoc] = useState<Row | null>(null), [form, setForm] = useState<Row>({}), [preview, setPreview] = useState(""), [previewError, setPreviewError] = useState("");
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  const [kind, setKind] = useState("purchase"), [query, setQuery] = useState(""), [targets, setTargets] = useState<Row[]>([]);
  const url = useRef("");
  async function load() {
    const result = await api(`/archive/documents/${id}`);
    setDoc(result);
    setForm({ doc_type: result.doc_type, description: result.description, store_id: result.store_id || "", ...Object.fromEntries(FIELDS.map(([key, , type]) => [key, type === "money" ? centsText(result[key]) : result[key] ?? ""])) });
  }
  useEffect(() => { load().catch(e => setError((e as Error).message)); }, [id]);
  useEffect(() => {
    if (!doc) return;
    let active = true;
    fetch(doc.file_url, { credentials: "same-origin" }).then(async response => {
      if (!response.ok) { const info = await response.json().catch(() => ({})); throw new Error(info.detail || "Original indisponível."); }
      const blob = await response.blob();
      if (!active) return;
      if (url.current) URL.revokeObjectURL(url.current);
      url.current = URL.createObjectURL(blob); setPreview(url.current);
    }).catch(e => { if (active) setPreviewError((e as Error).message); });
    return () => { active = false; };
  }, [doc?.id]);
  useEffect(() => () => { if (url.current) URL.revokeObjectURL(url.current); }, []);
  async function run(task: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function save(reviewed: boolean) {
    const body: Row = { reviewed, clear: [] as string[] };
    for (const [key, , type] of FIELDS) {
      const raw = String(form[key] ?? "").trim();
      const original = type === "money" ? centsText(doc![key]) : doc![key] ?? "";
      if (raw === String(original)) continue;
      if (!raw) { body.clear.push(key); continue; }
      if (type === "money") {
        const cents = toCents(raw);
        if (cents === undefined) throw new Error(`Valor inválido em «${FIELDS.find(f => f[0] === key)![1]}». Use, por exemplo, 123,45.`);
        body[key] = cents;
      } else body[key] = raw;
    }
    if (form.doc_type !== doc!.doc_type) body.doc_type = form.doc_type;
    if ((form.description || "") !== (doc!.description || "")) body.description = form.description;
    if (form.store_id && form.store_id !== doc!.store_id) body.store_id = form.store_id;
    const result = await api(`/archive/documents/${id}`, body, { method: "PUT" });
    setDoc(result); setNotice(reviewed ? "Documento revisto." : "Correções guardadas no histórico."); onChanged();
  }
  if (!doc) return <AppDialog className="doc-dialog arc-dialog" aria-label="Documento do arquivo" onRequestClose={onClose}>{error ? <Notice tone="error">{error}</Notice> : <p role="status">A carregar…</p>}</AppDialog>;
  const fields = doc.extraction?.fields || {};
  const candidates = doc.extraction?.candidates || {};
  return <AppDialog className="doc-dialog arc-dialog" busy={busy} aria-label={`Documento ${doc.original_name}`} onRequestClose={onClose}>
    <header className="arc-dialog-head">
      <div><h2><FileSearch size={20} aria-hidden="true" /> {doc.original_name || "Documento"}</h2>
        <p className="arc-muted">{doc.origin_label} · {doc.type_label} · {size(doc.size)} · recebido {when(doc.received_at)}{doc.store ? ` · ${doc.store}` : ""}</p></div>
      <ActionButton type="button" onClick={onClose}>Fechar</ActionButton>
    </header>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {doc.duplicate_of && <Notice tone="warning">Duplicado de outro documento do arquivo (mesmo SHA-256). Não entra nas associações.</Notice>}
    {doc.warnings?.map((w: string) => <Notice key={w} tone="warning">{w}</Notice>)}
    {doc.jobs?.filter((j: Row) => j.error).map((j: Row) => <Notice key={j.kind} tone={j.status === "erro" ? "error" : "warning"}>{j.kind === "extract" ? "Extração" : "Relações"}: {j.error}{j.status === "pendente" ? ` Nova tentativa automática às ${when(j.next_attempt_at)}.` : ""}</Notice>)}
    <div className="arc-detail">
      <section className="arc-preview" aria-label="Pré-visualização do original">
        {preview ? (doc.mime === "application/pdf" ? <iframe title="Pré-visualização do PDF" src={preview} /> : <img src={preview} alt={`Original de ${doc.original_name}`} />)
          : <p className="arc-muted">{previewError || "A carregar o original…"}</p>}
        <div className="arc-actions">
          {preview && <a className="ui-action" href={preview} download={doc.original_name || "documento"}><Download size={16} aria-hidden="true" />Descarregar</a>}
          {canManage && <ActionButton type="button" disabled={busy} onClick={() => run(async () => { await api(`/archive/documents/${id}/retry`, {}); await load(); setNotice("Processamento repetido."); onChanged(); })}><RefreshCw size={16} aria-hidden="true" />Repetir processamento</ActionButton>}
        </div>
        <dl className="arc-facts">
          <dt>Integridade</dt><dd>{doc.integrity_ok === false ? "Falhou: o original mudou" : doc.integrity_ok ? `Verificada em ${when(doc.integrity_checked_at)}` : "Por verificar"}</dd>
          <dt>SHA-256</dt><dd className="arc-hash">{doc.sha256 || "—"}</dd>
          <dt>Conservação</dt><dd>Até {day(doc.retention_until)}{doc.legal_hold ? " · retenção obrigatória ativa" : ""}</dd>
          <dt>Dossiê</dt><dd>{doc.dossier ? `${doc.dossier.label}${doc.dossier.closed ? " (fechado)" : ""} — ${doc.dossier.reason}` : "Sem data de emissão"}</dd>
        </dl>
      </section>
      <section className="arc-fields" aria-label="Dados do documento">
        <h3>Dados extraídos</h3>
        <ManagedForm onSubmit={event => { event.preventDefault(); void run(() => save(false)); }}>
          <fieldset disabled={!canManage || busy}>
            <Field label="Tipo de documento"><select value={form.doc_type} onChange={e => setForm({ ...form, doc_type: e.target.value })}>{Object.entries(TYPE_LABELS).map(([key, label]) => <option key={key} value={key}>{String(label)}</option>)}</select></Field>
            {FIELDS.map(([key, label, type]) => <div key={key} className="arc-field">
              <Field label={<>{label} <Confidence value={fields[key]?.confidence} source={fields[key]?.source} /></>}><input type={type === "date" ? "date" : "text"} inputMode={type === "money" ? "decimal" : undefined} value={form[key] ?? ""} onChange={e => setForm({ ...form, [key]: e.target.value })} /></Field>
            </div>)}
            <Field label="Descrição"><input value={form.description || ""} maxLength={500} onChange={e => setForm({ ...form, description: e.target.value })} /></Field>
            <Field label="Loja"><select value={form.store_id} onChange={e => setForm({ ...form, store_id: e.target.value })}><option value="">Sem loja (empresa)</option>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          </fieldset>
          {Object.keys(candidates).filter(k => k !== "due_dates").length > 0 && <Notice tone="info">Valores ambíguos não preenchidos automaticamente: {Object.entries(candidates).filter(([k]) => k !== "due_dates").map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("; ")}</Notice>}
          {candidates.due_dates?.length > 0 && <p className="arc-muted">Vencimento indicado no documento: {candidates.due_dates.map(day).join(", ")}</p>}
          {canManage && <div className="arc-actions">
            <ActionButton className="primary" disabled={busy}>Guardar correções</ActionButton>
            {doc.review_status !== "revisto" && <ActionButton type="button" disabled={busy} onClick={() => run(() => save(true))}><CheckCircle2 size={16} aria-hidden="true" />Guardar e marcar revisto</ActionButton>}
          </div>}
        </ManagedForm>
      </section>
    </div>
    <section aria-label="Relações">
      <h3><Link2 size={18} aria-hidden="true" /> Relações</h3>
      {!doc.links.length && <p className="arc-muted">Sem relações. As propostas aparecem depois do processamento; também pode associar manualmente.</p>}
      <div className="arc-link-grid">{doc.links.map((link: Row) => <LinkCard key={link.id} link={link} canManage={canManage} navigate={navigate} onDecide={(l, d) => void run(async () => { const r = await decideLinks([l], d); if (r) { await load(); onChanged(); setNotice(r.notes?.join(" ") || "Decisão registada."); } })} />)}</div>
      {canManage && !doc.duplicate_of && <details className="arc-manual">
        <summary>Associar manualmente</summary>
        <div className="arc-manual-row">
          <Field label="Registo"><select value={kind} onChange={e => { setKind(e.target.value); setTargets([]); }}><option value="purchase">Compra / e-Fatura</option><option value="erp_document">Compra do ERP</option><option value="bank_transaction">Movimento bancário</option><option value="document_payment">Pagamento</option></select></Field>
          <Field label="Procurar (nome, número ou valor)"><input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void run(async () => setTargets(await api(`/archive/targets?kind=${kind}&q=${encodeURIComponent(query)}`))); } }} /></Field>
          <ActionButton type="button" onClick={() => void run(async () => setTargets(await api(`/archive/targets?kind=${kind}&q=${encodeURIComponent(query)}`)))}><Search size={16} aria-hidden="true" />Procurar</ActionButton>
        </div>
        {targets.length > 0 && <ul className="arc-targets">{targets.map(t => <li key={t.id}><span>{t.label} · {t.entity} · {day(t.date)} · {formatEuro(t.amount_cents)}</span>
          <ActionButton type="button" onClick={() => void run(async () => {
            if (!(await confirmAction({ title: "Associar manualmente?", message: `${doc.original_name} ↔ ${t.label} (${formatEuro(t.amount_cents)}). Fica registada como associação manual confirmada.`, confirmLabel: "Associar" }))) return;
            const result = await api("/archive/links/manual", { document_id: doc.id, target_kind: kind, target_id: t.id });
            setTargets([]); await load(); onChanged(); setNotice(result.note || "Associação registada.");
          })}>Associar</ActionButton></li>)}</ul>}
      </details>}
    </section>
    {doc.duplicates?.length > 0 && <section><h3>Cópias detetadas</h3><ul>{doc.duplicates.map((d: Row) => <li key={d.id}>{d.original_name} · {d.origin_label} · {when(d.received_at)}</li>)}</ul></section>}
    <details className="arc-history"><summary>Histórico de alterações e acessos ({doc.events.length + doc.corrections.length})</summary>
      {doc.corrections.length > 0 && <><h4>Correções</h4><ul>{doc.corrections.map((c: Row, i: number) => <li key={i}>{when(c.data)} · {c.utilizador}: {c.campo} «{String(c.antes ?? "—")}» → «{String(c.depois ?? "—")}»</li>)}</ul></>}
      <h4>Eventos</h4><ul>{doc.events.map((e: Row, i: number) => <li key={i}>{when(e.created_at)} · {e.user} · {ACTIONS[e.action] || e.action}{e.detail?.erro ? ` — ${e.detail.erro}` : ""}</li>)}</ul>
    </details>
  </AppDialog>;
}

function useOverview() {
  const [meta, setMeta] = useState<Row | null>(null);
  const reload = () => api("/archive/overview").then(setMeta);
  useEffect(() => { reload().catch(() => undefined); }, []);
  return [meta, reload] as const;
}

function Assistant({ open, navigate, canManage }: { open: (id: string) => void; navigate: (page: string) => void; canManage: boolean }) {
  const [question, setQuestion] = useState(""), [result, setResult] = useState<Row | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const examples = ["Encontra as faturas da EDP de setembro.", "Quais são os documentos em falta para a contabilidade?", "Mostra pagamentos sem comprovativo.", "Relaciona os anexos disponíveis com as compras do ERP.", "Prepara o dossiê contabilístico de setembro."];
  async function ask(text: string) {
    setBusy(true); setError(""); setResult(null);
    try { setResult(await api("/archive/assistant", { question: text })); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel arc-assistant">
    <AssistantIdentity assistant="clara" />
    <ManagedForm className="arc-ask" onSubmit={event => { event.preventDefault(); if (question.trim().length >= 3) void ask(question); }}>
      <Field label="Pergunte ao arquivo"><input value={question} maxLength={500} placeholder="Ex.: Encontra as faturas da EDP de setembro." onChange={e => setQuestion(e.target.value)} /></Field>
      <ActionButton className="primary" disabled={busy || question.trim().length < 3}><Search size={16} aria-hidden="true" />Perguntar</ActionButton>
    </ManagedForm>
    <div className="arc-chips">{examples.map(example => <ActionButton key={example} type="button" className="arc-chip" onClick={() => { setQuestion(example); void ask(example); }}>{example}</ActionButton>)}</div>
    <p className="arc-muted">Interpretação local, sem envio de dados a serviços externos. As consultas só leem; ações de escrita mostram um plano e pedem confirmação.</p>
    {busy && <p role="status">A consultar…</p>}
    {error && <Notice tone="error">{error}</Notice>}
    {result && <div className="arc-answer" aria-live="polite">
      <p className="arc-answer-text"><StatusBadge tone={result.mode === "escrita" ? "warning" : "neutral"}>{result.mode === "escrita" ? "Escrita (requer confirmação)" : "Leitura"}</StatusBadge> {result.answer}</p>
      {(result.interpretation?.periodo || result.interpretation?.tipo || result.interpretation?.entidade) && <p className="arc-muted">Interpretado: {[result.interpretation.periodo && `período ${result.interpretation.periodo}`, result.interpretation.tipo && `tipo ${result.interpretation.tipo}`, result.interpretation.entidade && `entidade «${result.interpretation.entidade}»`].filter(Boolean).join(" · ")}</p>}
      <details open><summary>Fontes consultadas</summary><ul>{result.sources?.map((s: string) => <li key={s}>{s}</li>)}</ul></details>
      {result.documents?.length > 0 && <DocumentTable rows={result.documents} onOpen={open} />}
      {result.items?.length > 0 && <div className="table-wrap"><table><thead><tr><th>Registo</th><th>Entidade</th><th>Data</th><th className="numeric">Valor</th></tr></thead><tbody>{result.items.slice(0, 50).map((item: Row) => <tr key={item.id}><td>{item.label}</td><td>{item.entity || ""}</td><td>{day(item.date)}</td><td className="numeric">{formatEuro(item.amount_cents)}</td></tr>)}</tbody></table></div>}
      {result.relations?.length > 0 && <details><summary>Relações usadas ({result.relations.length})</summary><ul>{result.relations.map((r: Row) => <li key={r.id}>{r.target_label}: {r.target?.label} · {(r.reasons || []).join("; ")}</li>)}</ul></details>}
      <div className="arc-actions">
        {result.plan && canManage && <ActionButton type="button" className="primary" onClick={async () => {
          if (!(await confirmAction({ title: "Executar o plano?", message: `${result.plan.summary}. Só cria propostas para validação; não confirma associações nem faz lançamentos.`, confirmLabel: "Executar" }))) return;
          try { const done = await api("/archive/assistant/confirm", { token: result.plan.token }); setResult({ ...result, plan: null, answer: done.answer, navigate: done.navigate }); } catch (e) { setError((e as Error).message); }
        }}>Confirmar e executar</ActionButton>}
        {result.navigate && <ActionButton type="button" onClick={() => navigate(result.navigate)}>Abrir {result.navigate}</ActionButton>}
      </div>
    </div>}
  </section>;
}

function Uploader({ stores, store, onDone }: { stores: Store[]; store: string; onDone: (message: string) => void }) {
  const [target, setTarget] = useState(store || stores[0]?.id || ""), [type, setType] = useState(""), [description, setDescription] = useState("");
  const [results, setResults] = useState<Row[]>([]), [busy, setBusy] = useState(""), [error, setError] = useState(""), [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  async function send(files: FileList | File[]) {
    const list = Array.from(files);
    if (!list.length) return;
    setError(""); setResults([]);
    if (list.length > 20) { setError("Carregue no máximo 20 ficheiros de cada vez."); return; }
    if (list.reduce((total, file) => total + file.size, 0) > 48 * 1024 * 1024) { setError("Os ficheiros ultrapassam 50 MB por envio. Carregue menos ficheiros de cada vez."); return; }
    const form = new FormData();
    form.append("store_id", target); form.append("doc_type", type); form.append("description", description);
    list.forEach(file => form.append("files", file, file.name));
    setBusy("A carregar…");
    try {
      const response = await fetch("/api/archive/upload", { method: "POST", body: form, credentials: "same-origin" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Não foi possível carregar os ficheiros.");
      setResults(data.results);
      setBusy("A processar (extração e relações)…");
      await api("/archive/process", {}, { timeoutMs: 120_000 }).catch(() => undefined);
      onDone(`${data.results.filter((r: Row) => r.status === "carregado").length} ficheiro(s) carregado(s).`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(""); if (input.current) input.current.value = ""; }
  }
  return <section className="panel">
    <h3><Upload size={18} aria-hidden="true" /> Carregar documentos</h3>
    <div className="form-grid">
      <Field label="Loja"><select value={target} onChange={e => setTarget(e.target.value)}>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
      <Field label="Tipo (opcional)"><select value={type} onChange={e => setType(e.target.value)}><option value="">Identificar automaticamente</option><option value="fatura">Fatura</option><option value="recibo">Recibo</option><option value="comprovativo_pagamento">Comprovativo de pagamento</option><option value="despesa">Despesa</option><option value="extrato_bancario">Extrato bancário</option><option value="contrato">Contrato</option><option value="outro">Outro</option></select></Field>
      <Field label="Descrição (opcional)"><input value={description} maxLength={500} onChange={e => setDescription(e.target.value)} /></Field>
    </div>
    <div className={`arc-drop ${dragging ? "active" : ""}`} onDragOver={e => { e.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (!busy) void send(e.dataTransfer.files); }}>
      <p>Arraste PDF, JPG, PNG ou WebP para aqui (até 10 MB cada, 20 de cada vez) ou</p>
      <input ref={input} type="file" multiple accept="application/pdf,image/jpeg,image/png,image/webp" hidden onChange={e => e.target.files && void send(e.target.files)} />
      <ActionButton type="button" className="primary" disabled={!!busy || !target} onClick={() => input.current?.click()}><Upload size={16} aria-hidden="true" />Escolher ficheiros</ActionButton>
      <small>O conteúdo é verificado (não só a extensão); PDFs com JavaScript, ficheiros embutidos ou cifrados são rejeitados. O original fica cifrado e nunca é alterado.</small>
    </div>
    {busy && <p role="status">{busy}</p>}
    {error && <Notice tone="error">{error}</Notice>}
    {results.length > 0 && <ul className="arc-results">{results.map((r, i) => <li key={i} className={`arc-result-${r.status}`}>{r.name}: {r.status === "carregado" ? "carregado" : r.status === "duplicado" ? r.message : `rejeitado — ${r.message}`}</li>)}</ul>}
  </section>;
}

export default function Archive({ page, stores, store, navigate }: { page: string; stores: Store[]; store: string; navigate: (page: string) => void }) {
  const [meta, reloadMeta] = useOverview();
  const [open, setOpen] = useState<string | null>(null), [version, setVersion] = useState(0), [notice, setNotice] = useState(""), [error, setError] = useState("");
  const canManage = !!meta?.can_manage;
  const changed = () => { setVersion(v => v + 1); void reloadMeta(); };
  async function run(task: () => Promise<void>) {
    setError(""); setNotice("");
    try { await task(); } catch (e) { setError((e as Error).message); }
  }
  const header = (title: string, text: ReactNode, actions?: ReactNode) => <div className="page-intro"><div><h1>{title}</h1><p>{text}</p></div>{actions && <div className="arc-actions">{actions}</div>}</div>;
  const syncButton = canManage && <ActionButton type="button" onClick={() => void run(async () => {
    const result = await api("/archive/sync", { drive: true, process: true }, { timeoutMs: 180_000 });
    setNotice(`${result.created} documento(s) novo(s) indexado(s)${result.processing ? `; ${result.processing.processed} processamento(s), ${result.processing.pending} na fila` : ""}.${result.warnings?.length ? " " + result.warnings.join(" ") : ""}`);
    changed();
  })}><RefreshCw size={16} aria-hidden="true" />Atualizar arquivo</ActionButton>;
  const messages = <>{error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success">{notice}</Notice>}</>;
  const detail = open && <DocumentDetail id={open} stores={stores} canManage={canManage} navigate={p => { setOpen(null); navigate(p); }} onClose={() => setOpen(null)} onChanged={changed} />;
  let body: ReactNode = null;
  if (page === "Arquivo — Visão geral") body = <>
    {header("Arquivo digital", "Todos os documentos da empresa num só lugar: digitalizações, carregamentos, anexos, Drive e Gmail, com extração, relações e histórico.", syncButton)}
    {messages}
    {meta && <div className="metric-grid arc-metrics">
      <MetricCard label="Documentos" value={meta.total} description="Ficheiros indexados no arquivo (originais preservados)." />
      <MetricCard label="Caixa de entrada" value={meta.inbox} tone={meta.inbox ? "warning" : "success"} description="Por rever ou com erro de processamento." />
      <MetricCard label="Propostas por validar" value={meta.proposals} tone={meta.proposals ? "warning" : "neutral"} description="Associações sugeridas pelo motor de relações." />
      <MetricCard label="Em processamento" value={meta.queue} description="Trabalhos na fila (extração e relações)." />
      <MetricCard label="Erros" value={meta.errors} tone={meta.errors ? "danger" : "success"} description="Documentos que não foi possível processar." />
      <MetricCard label="Duplicados" value={meta.duplicates} description="Cópias detetadas pelo SHA-256." />
    </div>}
    {meta && !meta.ocr_available && <Notice tone="info">OCR local indisponível no servidor: documentos digitalizados sem texto nem QR ficam para preenchimento manual.</Notice>}
    {(meta?.integrity_failed || 0) > 0 && <Notice tone="error">{meta?.integrity_failed} documento(s) com falha de integridade. Veja Arquivo — Configurações.</Notice>}
    <Assistant open={setOpen} navigate={navigate} canManage={canManage} />
    {meta && <section className="panel"><h3>Recebidos recentemente</h3><DocumentTable rows={meta.latest} onOpen={setOpen} empty="Ainda não há documentos. Use «Atualizar arquivo» para indexar os existentes ou carregue ficheiros na Caixa de entrada." />
      <p className="arc-muted">Origens: {meta.origins.map((o: Row) => `${o.label} (${o.count})`).join(" · ") || "—"} · Última atualização: {when(meta.last_sync_at)}</p></section>}
  </>;
  else if (page === "Caixa de entrada") body = <>
    {header("Caixa de entrada", "Documentos novos a rever: confirme o tipo e os dados extraídos e valide as associações propostas.", <>{syncButton}<ActionButton type="button" onClick={() => void run(async () => { const r = await api("/archive/process", {}, { timeoutMs: 120_000 }); setNotice(`${r.processed} processado(s), ${r.failed} com erro, ${r.pending} na fila.`); changed(); })}>Processar fila</ActionButton></>)}
    {messages}
    {meta?.can_upload && <Uploader stores={stores} store={store} onDone={message => { setNotice(message); changed(); }} />}
    <DocumentList key={`inbox-${version}`} inbox stores={stores} store={store} canManage={canManage} open={setOpen} onChanged={changed} />
  </>;
  else if (page === "Todos os documentos") body = <>
    {header("Todos os documentos", "Pesquisa global por nome, entidade, NIF, número, referência ou descrição, com filtros e ações em lote.", syncButton)}
    {messages}
    <DocumentList key={`all-${version}`} stores={stores} store={store} canManage={canManage} open={setOpen} onChanged={changed} meta={meta} />
  </>;
  else if (page === "Associações") body = <>{header("Associações", "Propostas do motor de relações com a justificação de cada uma. As ambíguas ficam sempre para validação humana.")}{messages}<Links key={version} canManage={canManage} open={setOpen} navigate={navigate} onChanged={changed} /></>;
  else if (page === "Pendências") body = <>{header("Pendências", "O que falta para ter o arquivo completo e pronto para a contabilidade.")}{messages}<Pending key={version} open={setOpen} navigate={navigate} /></>;
  else if (page === "Dossiês contabilísticos") body = <>{header("Dossiês contabilísticos", "Documentos por mês de emissão. O fecho do período e a exportação ou envio continuam em «Entrega à contabilidade».", <ActionButton type="button" onClick={() => navigate("Entrega à contabilidade")}>Abrir Entrega à contabilidade</ActionButton>)}{messages}<Periods navigate={navigate} /></>;
  else if (page === "Arquivo — Auditoria") body = <>{header("Auditoria do arquivo", "Histórico de alterações, decisões e acessos a documentos.")}{messages}<AuditLog open={setOpen} /></>;
  else if (page === "Arquivo — Configurações") body = <>{header("Configurações do arquivo", "Conservação, processamento automático, OCR e verificação de integridade.")}{messages}<Settings admin={!!meta?.is_admin} /></>;
  return <div className="arc">{body}{detail}</div>;
}

function DocumentList({ inbox, stores, store, canManage, open, onChanged, meta }: { inbox?: boolean; stores: Store[]; store: string; canManage: boolean; open: (id: string) => void; onChanged: () => void; meta?: Row | null }) {
  const [filters, setFilters] = useState<Row>({ q: "", doc_type: "", origin: "", status: "", relation: "", start: "", end: "" });
  const [data, setData] = useState<Row | null>(null), [offset, setOffset] = useState(0), [selected, setSelected] = useState<Set<string>>(new Set()), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const load = () => {
    const params = new URLSearchParams({ ...filters, store_id: store, offset: String(offset), limit: "50", ...(inbox ? { inbox: "true" } : {}) });
    return api("/archive/documents?" + params).then(setData).catch(e => setError((e as Error).message));
  };
  useEffect(() => { void load(); }, [offset, store, inbox]);
  async function batch(action: string, label: string) {
    setError(""); setNotice("");
    try {
      const ids = [...selected];
      const preview = await api("/archive/documents/batch", { ids, action });
      if (!(await confirmAction({ title: `${label}: ${ids.length} documento(s)?`, message: preview.documents.slice(0, 8).map((d: Row) => d.name).join(", ") + (ids.length > 8 ? "…" : ""), confirmLabel: label }))) return;
      await api("/archive/documents/batch", { ids, action, confirm: true });
      if (action === "reprocessar") await api("/archive/process", {}, { timeoutMs: 120_000 });
      setSelected(new Set()); setNotice(`${label}: concluído.`); onChanged(); await load();
    } catch (e) { setError((e as Error).message); }
  }
  return <section className="panel">
    {!inbox && <ManagedForm className="arc-filters filter-bar" data-ignore-dirty onSubmit={event => { event.preventDefault(); setOffset(0); void load(); }}>
      <Field label="Pesquisar"><input value={filters.q} placeholder="Nome, entidade, NIF, número…" onChange={e => setFilters({ ...filters, q: e.target.value })} /></Field>
      <Field label="Tipo"><select value={filters.doc_type} onChange={e => setFilters({ ...filters, doc_type: e.target.value })}><option value="">Todos</option>{Object.entries(meta?.types || {}).map(([k, v]) => <option key={k} value={k}>{String(v)}</option>)}</select></Field>
      <Field label="Origem"><select value={filters.origin} onChange={e => setFilters({ ...filters, origin: e.target.value })}><option value="">Todas</option>{Object.entries(meta?.origin_labels || {}).map(([k, v]) => <option key={k} value={k}>{String(v)}</option>)}</select></Field>
      <Field label="Estado"><select value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })}><option value="">Todos</option><option value="por_rever">Por rever</option><option value="revisto">Revisto</option><option value="pendente">A processar</option><option value="erro">Erro</option><option value="duplicado">Duplicado</option></select></Field>
      <Field label="Relação"><select value={filters.relation} onChange={e => setFilters({ ...filters, relation: e.target.value })}><option value="">Todas</option><option value="associado">Associado</option><option value="proposta">Proposta por validar</option><option value="sem_relacao">Sem relação</option></select></Field>
      <Field label="Emitido desde"><input type="date" value={filters.start} onChange={e => setFilters({ ...filters, start: e.target.value })} /></Field>
      <Field label="Até"><input type="date" value={filters.end} onChange={e => setFilters({ ...filters, end: e.target.value })} /></Field>
      <ActionButton className="primary"><Search size={16} aria-hidden="true" />Filtrar</ActionButton>
    </ManagedForm>}
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {canManage && selected.size > 0 && <div className="arc-batch" role="group" aria-label="Ações em lote">
      <span>{selected.size} selecionado(s)</span>
      <ActionButton type="button" onClick={() => void batch("revisto", "Marcar como revistos")}>Marcar revistos</ActionButton>
      <ActionButton type="button" onClick={() => void batch("relacionar", "Calcular associações")}>Calcular associações</ActionButton>
      <ActionButton type="button" onClick={() => void batch("reprocessar", "Repetir processamento")}>Repetir processamento</ActionButton>
    </div>}
    {!data ? <p role="status">A carregar…</p> : <>
      <p className="arc-muted">{data.total} documento(s){inbox ? " por rever ou com erro" : ""}.</p>
      <DocumentTable rows={data.documents} onOpen={open} selected={canManage ? selected : undefined} onSelect={canManage ? setSelected : undefined} empty={inbox ? "Caixa de entrada vazia: tudo revisto." : "Nenhum documento corresponde aos filtros."} />
      {data.total > 50 && <div className="arc-pages"><ActionButton type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>Anteriores</ActionButton><span>{offset + 1}–{Math.min(offset + 50, data.total)} de {data.total}</span><ActionButton type="button" disabled={offset + 50 >= data.total} onClick={() => setOffset(offset + 50)}>Seguintes</ActionButton></div>}
    </>}
  </section>;
}

function Links({ canManage, open, navigate, onChanged }: { canManage: boolean; open: (id: string) => void; navigate: (page: string) => void; onChanged: () => void }) {
  const [status, setStatus] = useWorkspaceTab("arquivo-associacoes", "proposta", Object.keys(LINK_STATUS));
  const [strength, setStrength] = useState(""), [rows, setRows] = useState<Row[] | null>(null), [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const load = () => api(`/archive/links?status=${status}&strength=${strength}`).then(setRows).catch(e => setError((e as Error).message));
  useEffect(() => { setSelected(new Set()); void load(); }, [status, strength]);
  async function decide(list: Row[], decision: string) {
    setError(""); setNotice("");
    try { const result = await decideLinks(list, decision); if (result) { setNotice((result.notes || []).filter(Boolean).slice(0, 3).join(" ") || "Decisão registada."); setSelected(new Set()); onChanged(); await load(); } }
    catch (e) { setError((e as Error).message); }
  }
  return <section className="panel">
    <div className="arc-tabs"><ModuleTabs label="Estado das associações" value={status} tabs={Object.entries(LINK_STATUS).map(([key, [label]]) => ({ id: key, label: `${label}s` }))} onChange={setStatus} />
      <select aria-label="Força" value={strength} onChange={e => setStrength(e.target.value)}><option value="">Todas as forças</option><option value="forte">Fortes</option><option value="possivel">Possíveis</option><option value="ambigua">Ambíguas</option></select></div>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {canManage && status === "proposta" && rows && rows.length > 0 && <div className="arc-batch">
      <label><input type="checkbox" checked={rows.every(r => selected.has(r.id))} onChange={() => setSelected(rows.every(r => selected.has(r.id)) ? new Set() : new Set(rows.map(r => r.id)))} /> Selecionar todas ({rows.length})</label>
      <ActionButton type="button" disabled={!selected.size} className="primary" onClick={() => void decide(rows.filter(r => selected.has(r.id)), "confirmar")}>Confirmar selecionadas</ActionButton>
      <ActionButton type="button" disabled={!selected.size} onClick={() => void decide(rows.filter(r => selected.has(r.id)), "rejeitar")}>Rejeitar selecionadas</ActionButton>
      <ActionButton type="button" onClick={() => setSelected(new Set(rows.filter(r => r.strength === "forte").map(r => r.id)))}>Selecionar fortes</ActionButton>
    </div>}
    {!rows ? <p role="status">A carregar…</p> : !rows.length ? <p className="arc-empty">Sem associações neste estado.</p> :
      <div className="arc-link-grid">{rows.map(link => <div key={link.id} className="arc-selectable">
        {canManage && status === "proposta" && <input type="checkbox" aria-label="Selecionar associação" checked={selected.has(link.id)} onChange={() => { const next = new Set(selected); if (next.has(link.id)) next.delete(link.id); else next.add(link.id); setSelected(next); }} />}
        <LinkCard link={link} canManage={canManage} navigate={navigate} showDocument={open} onDecide={(l, d) => void decide([l], d)} />
      </div>)}</div>}
  </section>;
}

function Pending({ open, navigate }: { open: (id: string) => void; navigate: (page: string) => void }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState("");
  const [section, setSection] = useWorkspaceTab("pendencias", "");
  useEffect(() => { api("/archive/pending").then(setData).catch(e => setError((e as Error).message)); }, []);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <p role="status">A carregar…</p>;
  const records = (rows: Row[], page: string) => rows.length ? <div className="table-wrap"><table><thead><tr><th>Registo</th><th>Entidade</th><th>Data</th><th className="numeric">Valor</th></tr></thead>
    <tbody>{rows.slice(0, 50).map(r => <tr key={r.id}><td>{r.label}</td><td>{r.entity || ""}{r.store ? <small>{r.store}</small> : null}</td><td>{day(r.date)}</td><td className="numeric">{formatEuro(r.amount_cents)}</td></tr>)}</tbody></table>
    <ActionButton type="button" onClick={() => navigate(page)}>Abrir {page}</ActionButton></div> : <p className="arc-empty">Nada pendente.</p>;
  // Um motivo de cada vez, com contagem em cada separador (antes: seis listas empilhadas).
  const sections = [
    { id: "associacoes", label: "Associações", count: data.proposals },
    { id: "rever", label: "Por rever", count: data.review.length },
    { id: "compras", label: "Compras sem documento", count: data.purchases_without_document.length },
    { id: "pagamentos", label: "Pagamentos sem comprovativo", count: data.payments_without_proof.length },
    { id: "faturas", label: "Faturas sem compra", count: data.documents_without_purchase.length },
    { id: "conservacao", label: "Conservação", count: data.retention_due.length },
  ];
  const current = sections.some(item => item.id === section) ? section : (sections.find(item => item.count)?.id || "associacoes");
  return <div className="arc-pending">
    <ModuleTabs label="Motivos de pendência" value={current} onChange={setSection} tabs={sections} />
    {current === "associacoes" && <section className="panel"><h3>Associações por validar <StatusBadge tone={data.proposals ? "warning" : "success"}>{data.proposals}</StatusBadge></h3>{data.proposals ? <ActionButton type="button" onClick={() => navigate("Associações")}>Validar associações</ActionButton> : <p className="arc-empty">Nada pendente.</p>}</section>}
    {current === "rever" && <section className="panel"><h3>Documentos por rever <StatusBadge tone={data.review.length ? "warning" : "success"}>{data.review.length}</StatusBadge></h3><DocumentTable rows={data.review} onOpen={open} empty="Nada pendente." /></section>}
    {current === "compras" && <section className="panel"><h3>Compras sem documento (12 meses) <StatusBadge tone={data.purchases_without_document.length ? "warning" : "success"}>{data.purchases_without_document.length}</StatusBadge></h3>{records(data.purchases_without_document, "Compras")}</section>}
    {current === "pagamentos" && <section className="panel"><h3>Pagamentos sem comprovativo (6 meses) <StatusBadge tone={data.payments_without_proof.length ? "warning" : "success"}>{data.payments_without_proof.length}</StatusBadge></h3><p className="arc-muted">Débitos bancários sem documento associado nem compra alocada.</p>{records(data.payments_without_proof, "Extratos")}</section>}
    {current === "faturas" && <section className="panel"><h3>Faturas sem compra associada <StatusBadge tone={data.documents_without_purchase.length ? "warning" : "success"}>{data.documents_without_purchase.length}</StatusBadge></h3><DocumentTable rows={data.documents_without_purchase} onOpen={open} empty="Nada pendente." /></section>}
    {current === "conservacao" && <section className="panel"><h3>Prazo de conservação terminado <StatusBadge tone="neutral">{data.retention_due.length}</StatusBadge></h3><p className="arc-muted">Nada é eliminado automaticamente. A decisão de destruir originais é da empresa, com o contabilista, e só depois do prazo legal.</p><DocumentTable rows={data.retention_due} onOpen={open} empty="Nenhum documento com prazo terminado." /></section>}
  </div>;
}

function Periods({ navigate }: { navigate: (page: string) => void }) {
  const [rows, setRows] = useState<Row[] | null>(null), [error, setError] = useState("");
  useEffect(() => { api("/archive/periods").then(setRows).catch(e => setError((e as Error).message)); }, []);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!rows) return <p role="status">A carregar…</p>;
  return <section className="panel">{!rows.length ? <p className="arc-empty">Ainda não há documentos com data de emissão nos últimos 12 meses.</p> :
    <div className="table-wrap"><table><thead><tr><th>Mês</th><th className="numeric">Documentos</th><th className="numeric">Fiscais</th><th className="numeric">Associados</th><th className="numeric">Por rever</th><th className="numeric">Compras</th><th className="numeric">Compras sem documento</th><th>Período</th></tr></thead>
      <tbody>{rows.map(r => <tr key={r.month}><td>{r.month.split("-").reverse().join("/")}</td><td className="numeric">{r.documents}</td><td className="numeric">{r.fiscal}</td><td className="numeric">{r.linked}</td><td className="numeric">{r.review}</td><td className="numeric">{r.purchases}</td><td className="numeric">{r.purchases_missing ? <StatusBadge tone="warning">{r.purchases_missing}</StatusBadge> : 0}</td><td>{r.closed ? <StatusBadge tone="success">Fechado</StatusBadge> : <StatusBadge>Aberto</StatusBadge>}</td></tr>)}</tbody></table></div>}
    <ActionButton type="button" onClick={() => navigate("Entrega à contabilidade")}>Fechar, exportar ou enviar em Entrega à contabilidade</ActionButton>
  </section>;
}

function AuditLog({ open }: { open: (id: string) => void }) {
  const [rows, setRows] = useState<Row[] | null>(null), [action, setAction] = useState(""), [error, setError] = useState("");
  useEffect(() => { api(`/archive/audit?action=${action}`).then(setRows).catch(e => setError((e as Error).message)); }, [action]);
  return <section className="panel">
    <Field label="Ação"><select value={action} onChange={e => setAction(e.target.value)}><option value="">Todas</option>{Object.entries(ACTIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
    {error && <Notice tone="error">{error}</Notice>}
    {!rows ? <p role="status">A carregar…</p> : !rows.length ? <p className="arc-empty">Sem registos.</p> :
      <div className="table-wrap"><table><thead><tr><th>Data</th><th>Utilizador</th><th>Ação</th><th>Documento</th><th>Detalhe</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.id}><td>{when(r.created_at)}</td><td>{r.user}</td><td>{ACTIONS[r.action] || r.action}</td><td>{r.document_id ? <ActionButton type="button" className="arc-link" onClick={() => open(r.document_id)}>{r.document || "Documento"}</ActionButton> : "—"}</td><td className="arc-detail-cell">{Object.entries(r.detail || {}).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ")}</td></tr>)}</tbody></table></div>}
  </section>;
}

function Settings({ admin }: { admin: boolean }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState(""), [check, setCheck] = useState<Row | null>(null), [busy, setBusy] = useState(false);
  useEffect(() => { api("/archive/settings").then(setData).catch(e => setError((e as Error).message)); }, []);
  if (!data) return error ? <Notice tone="error">{error}</Notice> : <p role="status">A carregar…</p>;
  return <>
    <section className="panel">
      <h3><ArchiveIcon size={18} aria-hidden="true" /> Processamento e conservação</h3>
      {error && <Notice tone="error">{error}</Notice>}
      {notice && <Notice tone="success">{notice}</Notice>}
      <ManagedForm onSubmit={async event => { event.preventDefault(); setError(""); try { setData(await api("/archive/settings", { retention_years: data.retention_years, ocr_enabled: data.ocr_enabled, auto_process: data.auto_process, include_drive: data.include_drive, auto_confirm_strong: data.auto_confirm_strong }, { method: "PUT" })); setNotice("Configuração guardada."); } catch (e) { setError((e as Error).message); } }}>
        <fieldset disabled={!admin}>
          <Field label="Conservação (anos após o fim do ano de emissão; mínimo 10)"><input type="number" min={10} max={30} value={data.retention_years} onChange={e => setData({ ...data, retention_years: Number(e.target.value) })} /></Field>
          <label className="arc-check"><input type="checkbox" checked={data.auto_process} onChange={e => setData({ ...data, auto_process: e.target.checked })} /> Processar automaticamente em segundo plano (extração e relações)</label>
          <label className="arc-check"><input type="checkbox" checked={data.ocr_enabled} onChange={e => setData({ ...data, ocr_enabled: e.target.checked })} /> Usar OCR local quando o documento não tem texto nem QR {data.ocr_available ? "(disponível)" : "(não instalado no servidor)"}</label>
          <label className="arc-check"><input type="checkbox" checked={data.include_drive} onChange={e => setData({ ...data, include_drive: e.target.checked })} /> Incluir na caixa de entrada os ficheiros das pastas de anexos do Google Drive</label>
          <label className="arc-check"><input type="checkbox" checked={data.auto_confirm_strong} onChange={e => setData({ ...data, auto_confirm_strong: e.target.checked })} /> Confirmar automaticamente associações a compras com identidade fiscal por QR (NIF, número, data e total iguais e sem alternativas)</label>
        </fieldset>
        {admin ? <ActionButton className="primary">Guardar configuração</ActionButton> : <p className="arc-muted">Só o administrador altera estas opções.</p>}
      </ManagedForm>
      <p className="arc-muted">Os originais nunca são apagados pelo arquivo. A digitalização não autoriza, por si só, a destruição de documentos em papel: confirme com o contabilista os requisitos legais aplicáveis (por exemplo, Decreto-Lei n.º 28/2019 sobre conservação de faturas e documentos fiscalmente relevantes).</p>
    </section>
    <section className="panel">
      <h3><ShieldCheck size={18} aria-hidden="true" /> Integridade</h3>
      <p>Recalcula o SHA-256 dos originais (começando pelos verificados há mais tempo) e compara com o valor registado no arquivo.</p>
      {admin && <ActionButton type="button" disabled={busy} onClick={async () => { setBusy(true); setError(""); try { setCheck(await api("/archive/integrity", { limit: 100 }, { timeoutMs: 300_000 })); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>Verificar 100 documentos</ActionButton>}
      {busy && <p role="status">A verificar…</p>}
      {check && <Notice tone={check.failed.length ? "error" : "success"}>{check.checked} verificado(s): {check.ok} íntegro(s), {check.failed.length} com falha, {check.unavailable.length} indisponível(is) (cloud).{check.failed.map((f: Row) => ` ${f.name}: ${f.reason}`).join("")}</Notice>}
    </section>
  </>;
}
