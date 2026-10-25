import { currentMonthRange } from "./components/DatePresets";
import { useEffect, useMemo, useRef, useState } from "react";
import { Ban, CheckCircle, FileText, Mail, Paperclip, RefreshCw, Search } from "lucide-react";
import { api } from "./lib/api";
import { ActionButton, ActionLink, Notice, confirmAction, useDialogWorking } from "./components/InteractionUI";
import { Field } from "./components/Form";
import { StatusBadge } from "./components/BusinessUI";
import DatePresets from "./components/DatePresets";
import DriveImportFiles, { DriveSearchNotice, useDriveFiles, type DriveFile } from "./DriveImportFiles";
import InvoiceWorkflow from "./InvoiceWorkflow";
import { uploadPurchaseFile } from "./utils/purchaseImport";
import { formatEuro } from "./utils/format";
import "./ConferenceAssistant.css";

type Row = Record<string, any>;
type Tone = "neutral" | "success" | "warning" | "danger";

const STEPS: [string, string][] = [
  ["files", "Pesquisar ficheiros"], ["import", "Importar e-Fatura"], ["attachments", "Procurar anexos"],
  ["purchases", "Conferir compras"], ["review", "Rever propostas"], ["finish", "Concluir"],
];
const ATTACHMENT: Record<string, [string, Tone]> = {
  validated: ["Anexo validado", "success"], review: ["Anexo por confirmar", "warning"], missing: ["Anexo em falta", "danger"], unknown: ["Anexo por procurar", "neutral"],
};
const PURCHASE: Record<string, [string, Tone]> = {
  consistent: ["Compra consistente", "success"], divergent: ["Compra com divergências", "danger"], draft: ["Rascunho por confirmar", "warning"],
  not_found: ["Compra não encontrada", "warning"], unavailable: ["ERP indisponível", "neutral"], unknown: ["Compra por conferir", "neutral"],
};
const RESULT: Record<string, [string, Tone]> = {
  complete: ["Conferido", "success"], pending: ["Pendente", "warning"], divergent: ["Divergência", "danger"], review: ["Para rever", "warning"],
};
const STRENGTH: Record<string, [string, Tone]> = { forte: ["Correspondência forte", "success"], possivel: ["Correspondência possível", "warning"], ambigua: ["Ambígua", "danger"] };
const ACTIONS: [string, string][] = [
  ["", "Todas as ações"], ["none", "Concluídos"], ["review_attachment", "Rever anexo"], ["find_attachment", "Procurar anexos"],
  ["request_document", "Pedir documento"], ["prepare_purchase", "Preparar lançamento"], ["confirm_draft", "Confirmar rascunho"],
  ["review_divergence", "Rever divergências"], ["review_efatura", "Confirmar no e-Fatura"], ["retry_erp", "Repetir consulta ERP"], ["check_purchases", "Conferir compras"],
];
const STEP_ACTIONS: Record<string, string[]> = {
  attachments: ["review_attachment", "find_attachment", "request_document"],
  purchases: ["prepare_purchase", "confirm_draft", "review_divergence", "review_efatura", "retry_erp", "check_purchases"],
};
// Na pré-visualização, primeiro o que exige atenção.
const ORDER = ["conflict", "skipped", "new", "duplicate"];
const day = (value: string) => value?.slice(0, 10).split("-").reverse().join("/") || "—";
const lisbonToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Lisbon", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

/** Assistente de conferência: e-Fatura ↔ anexos ↔ compras, em etapas que se podem retomar. */
export default function ConferenceAssistant({ stores, defaultStore, filterStore, admin, manager, onUpdated, onReview, onClose }: {
  stores: Row[]; defaultStore: string; filterStore: string; admin: boolean; manager: boolean;
  onUpdated: () => Promise<void>; onReview: (row: Row) => void | Promise<void>; onClose: () => void;
}) {
  const today = lisbonToday();
  const [step, setStep] = useState("files");
  const [start, setStart] = useState(today.slice(0, 8) + "01"), [end, setEnd] = useState(currentMonthRange().to);
  const [store, setStore] = useState(defaultStore || stores[0]?.id || "");
  const [restored, setRestored] = useState(false);
  const [preview, setPreview] = useState<Row | null>(null), [source, setSource] = useState<{ drive?: DriveFile; file?: File } | null>(null);
  const [data, setData] = useState<Row | null>(null), [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [supplierFilter, setSupplierFilter] = useState(""), [stateFilter, setStateFilter] = useState(""), [actionFilter, setActionFilter] = useState("");
  const [selected, setSelected] = useState<Row | null>(null), [batch, setBatch] = useState<string[]>([]);
  const [confirmDemo, setConfirmDemo] = useState(false);
  const running = useRef(false);
  useDialogWorking(busy);
  const validPeriod = !!start && !!end && start <= end;
  const efatura = useDriveFiles(manager && step === "files" ? "/invoice-workflow/efatura-files" : null);

  // Retomar a etapa, o período e a loja guardados para este utilizador.
  useEffect(() => {
    let active = true;
    api("/invoice-workflow/assistant-state").then(saved => {
      if (!active || !saved?.step) return;
      setStep(saved.step === "import" ? "files" : saved.step);
      if (saved.start) setStart(saved.start);
      if (saved.end) setEnd(saved.end);
      if (saved.store_id && stores.some(s => s.id === saved.store_id)) setStore(saved.store_id);
      setNotice(`Trabalho retomado na etapa «${STEPS.find(([id]) => id === saved.step)?.[1]}». Os ficheiros importados e as associações já confirmadas estão guardados.`);
    }).catch(() => {}).finally(() => { if (active) setRestored(true); });
    return () => { active = false; };
  }, []);
  const firstSave = useRef(true);
  useEffect(() => {
    if (!restored) return;
    // Só guarda depois de uma alteração: abrir o assistente não escreve no servidor.
    if (firstSave.current) { firstSave.current = false; return; }
    void api("/invoice-workflow/assistant-state", { step, start: validPeriod ? start : null, end: validPeriod ? end : null, store_id: store, drive_id: source?.drive?.id || "" }).catch(() => {});
  }, [restored, step, start, end, store]);

  async function load() {
    if (!validPeriod) return;
    setLoading(true);
    try { setData(await api(`/invoice-workflow?start=${start}&end=${end}&store_id=${encodeURIComponent(filterStore)}`, undefined, { timeoutMs: 45_000 })); }
    finally { setLoading(false); }
  }
  useEffect(() => {
    if (!["attachments", "purchases", "review", "finish"].includes(step) || !validPeriod) return;
    setError("");
    load().catch(e => setError(`Não foi possível carregar os documentos. ${(e as Error).message}`));
  }, [step, start, end, filterStore]);
  async function run(task: () => Promise<void>) {
    if (running.current) return;
    running.current = true; setBusy(true); setError(""); setNotice("");
    try { await task(); } catch (e) { setError((e as Error).message); } finally { running.current = false; setBusy(false); }
  }
  async function refresh() {
    try { await load(); } catch { setError("A operação ficou guardada, mas não foi possível atualizar a lista. Repita apenas a consulta."); }
    void onUpdated().catch(() => {});
  }

  const rows: Row[] = data?.rows || [];
  const visible = useMemo(() => rows.filter(row => {
    const text = supplierFilter.trim().toLowerCase();
    if (text && !`${row.supplier_name} ${row.supplier_nif} ${row.document}`.toLowerCase().includes(text)) return false;
    if (stateFilter && row.attachment_state !== stateFilter && row.purchase_state !== stateFilter && row.result !== stateFilter) return false;
    if (actionFilter) return row.action === actionFilter;
    const focus = STEP_ACTIONS[step];
    return !focus || focus.includes(row.action);
  }), [rows, supplierFilter, stateFilter, actionFilter, step]);

  async function previewDrive(file: DriveFile) {
    if (!store) throw new Error("Escolha a loja de importação.");
    const result = await api("/invoice-workflow/efatura-files/import", { store_id: store, drive_id: file.id, ...(validPeriod ? { start, end } : {}) }, { timeoutMs: 60_000 });
    setPreview(result); setSource({ drive: file }); setStep("import");
  }
  async function previewUpload(file: File) {
    if (!store) throw new Error("Escolha a loja de importação.");
    const result = await uploadPurchaseFile(store, file, false);
    setPreview({ ...result, filename: file.name, warnings: result.warnings || [] }); setSource({ file }); setStep("import");
  }
  async function confirmImport() {
    if (!preview || !source) return;
    if (!(await confirmAction({ title: "Importar documentos e-Fatura?", message: `Serão criados ${preview.new_count} documento(s) novos; ${preview.duplicate_count} já existem e não serão duplicados. Os dados do e-Fatura não substituem o documento original.`, confirmLabel: "Importar" }))) return;
    await run(async () => {
      const result = source.drive
        ? await api("/invoice-workflow/efatura-files/import", { store_id: store, drive_id: source.drive.id, confirm: true, ...(validPeriod ? { start, end } : {}) }, { timeoutMs: 60_000 })
        : await uploadPurchaseFile(store, source.file as File, true);
      setNotice(`${result.new_count} documento(s) importados; ${result.duplicate_count} duplicados ignorados.`);
      setPreview(null); setSource(null); setStep("attachments");
      void onUpdated().catch(() => {});
    });
  }
  async function verify(parts: string[]) {
    await run(async () => {
      const result = await api("/invoice-workflow/verify", { start, end, store_ids: filterStore ? [filterStore] : [], confirm_demo: confirmDemo, parts }, { timeoutMs: 300_000 });
      const drive = { checked: "pesquisa concluída", partial: "pesquisa parcial — repita para continuar", error: "falha no Drive (o progresso foi guardado)", unavailable: "pastas por configurar", skipped: "não pesquisado" } as Row;
      const erp = { checked: "consulta concluída", error: "falha no ERP — a ausência não prova que a compra não exista", unavailable: "consulta indisponível", skipped: "não consultado" } as Row;
      setNotice(`${result.checked ?? 0} documento(s) verificados. Anexos: ${drive[result.drive] || result.drive || "—"}. ERP: ${erp[result.erp] || result.erp || "—"}.`);
      await refresh();
    });
  }

  const stepIndex = STEPS.findIndex(([id]) => id === step);
  const stepsNav = useRef<HTMLElement>(null);
  useEffect(() => { stepsNav.current?.querySelector<HTMLElement>("[aria-current=step]")?.scrollIntoView({ block: "nearest", inline: "center" }); }, [step]);
  const counts = (key: string) => rows.reduce((acc: Row, row) => ({ ...acc, [row[key]]: (acc[row[key]] || 0) + 1 }), {});
  const strong = rows.filter(row => row.drive_candidates?.length === 1 && row.drive_candidates[0].strength === "forte");
  const proposals = rows.filter(row => row.drive_candidates?.length && row.attachment_state !== "validated");

  return <div className="efc-assistant">
    <p className="footnote">Assistente automatizado de conferência documental. As leituras de QR, texto PDF e OCR local e as correspondências propostas podem conter erros. Reveja os documentos antes de confirmar associações ou outras ações.</p>
    <nav className="efc-steps" aria-label="Etapas da conferência" ref={stepsNav}>
      {STEPS.map(([id, label], index) => <ActionButton type="button" key={id} className={step === id ? "active" : index < stepIndex ? "done" : ""} aria-current={step === id ? "step" : undefined}
        disabled={busy || (id === "import" && !preview)} onClick={() => setStep(id)}><span>{index + 1}</span>{label}</ActionButton>)}
    </nav>
    {error && <Notice className="error" role="alert">{error}</Notice>}
    {notice && <Notice tone="success" role="status" onDismiss={() => setNotice("")}>{notice}</Notice>}

    {step === "files" && <section className="efc-step">
      <h3>1. Pesquisar ficheiros do e-Fatura</h3>
      <p>Escolha o período a conferir e a loja de destino. São procurados os ficheiros exportados do portal e-Fatura (CSV ou XLSX) nas pastas Drive configuradas; SAF-T de vendas e anexos não entram nesta lista.</p>
      <div className="form-grid filter-bar">
        <DatePresets from={start} to={end} onSelect={(from, to) => { setStart(from); setEnd(to); }} />
        <Field label="Período desde"><input type="date" value={start} onChange={e => setStart(e.target.value)} /></Field>
        <Field label="Até"><input type="date" value={end} min={start} onChange={e => setEnd(e.target.value)} /></Field>
        <Field label="Loja de importação"><select value={store} onChange={e => setStore(e.target.value)}>{stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
      </div>
      {!validPeriod && <Notice tone="warning">Indique um período válido.</Notice>}
      {manager ? <>
        <DriveSearchNotice search={efatura} what="exportações e-Fatura" />
        {efatura.data?.source === "attachments" && <p className="footnote">Sem pasta e-Fatura própria: a pesquisa usa as pastas de Anexos{efatura.data.subfolders === false ? " (sem subpastas)" : " e as respetivas subpastas"}. Cada ficheiro CSV ou XLSX é identificado pelo conteúdo, com as colunas que o importador reconhece. Pode indicar uma pasta própria em Configurações → Verificação automática e-Fatura.</p>}
        <DriveImportFiles search={efatura} title="Ficheiros e-Fatura no Google Drive" actionLabel="Pré-visualizar" disabled={busy || !store}
          notConfigured="Indique a pasta dos ficheiros exportados do e-Fatura em Configurações → Verificação automática e-Fatura. Pode também carregar o ficheiro do computador."
          onPick={file => run(() => previewDrive(file))} />
        {!!efatura.data?.ignored?.length && <details className="efc-upload"><summary>{efatura.data.ignored.length} ficheiro(s) CSV/XLSX ignorados por não serem exportações e-Fatura</summary>
          <ul>{efatura.data.ignored.map(file => <li key={file.id}><b>{file.name}</b> — {file.reason}</li>)}</ul>
        </details>}
        <details className="efc-upload"><summary>Carregar ficheiro do computador</summary>
          <Field label="Ficheiro e-Fatura (CSV ou XLSX)"><input type="file" accept=".csv,.xlsx" disabled={busy} onChange={e => { const file = e.target.files?.[0]; if (file) void run(() => previewUpload(file)); }} /></Field>
        </details>
      </> : <p>A importação de ficheiros está reservada a gestores e administradores.</p>}
      <div className="efc-actions"><ActionButton type="button" disabled={busy || !validPeriod} onClick={() => setStep("attachments")}>Continuar com os documentos já importados</ActionButton></div>
    </section>}

    {step === "import" && preview && <section className="efc-step">
      <h3>2. Importar e-Fatura</h3>
      <p><b>{preview.filename}</b>{preview.format && <> · {preview.format}</>}{preview.period && <> · documentos de {day(preview.period[0])} a {day(preview.period[1])}</>}</p>
      {!!preview.columns?.length && <p className="footnote">Colunas reconhecidas: {preview.columns.join(", ")}.</p>}
      <div className="efc-summary">
        <span><StatusBadge tone="success">{preview.new_count} novos</StatusBadge></span>
        <span><StatusBadge>{preview.duplicate_count} já existentes</StatusBadge></span>
        <span><StatusBadge tone={preview.conflict_count ? "danger" : "neutral"}>{preview.conflict_count} conflitos</StatusBadge></span>
        {!!preview.skipped_count && <span><StatusBadge tone="warning">{preview.skipped_count} não importados</StatusBadge></span>}
      </div>
      {(preview.warnings || []).map((warning: string) => <Notice key={warning} tone="warning">{warning}</Notice>)}
      {!!preview.conflict_count && <Notice tone="error">Há documentos já existentes com valores diferentes. Corrija o ficheiro ou esclareça a diferença antes de importar; nada é substituído sem revisão.</Notice>}
      <div className="table-wrap"><table><thead><tr><th>Estado</th><th>Fornecedor</th><th>Documento</th><th>Data</th><th className="numeric">Total</th></tr></thead><tbody>
        {[...preview.rows].sort((a: Row, b: Row) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status)).slice(0, 100).map((row: Row) => <tr key={row.line}>
          <td><StatusBadge tone={row.status === "new" ? "success" : row.status === "conflict" ? "danger" : row.status === "skipped" ? "warning" : "neutral"}>{row.status === "new" ? "Novo" : row.status === "conflict" ? `Conflito: ${(row.differences || []).join(", ")}` : row.status === "skipped" ? `Não importado: ${row.skip_reason}${row.differences?.length ? ` — ${row.differences.join(", ")}` : ""}` : "Já existe"}</StatusBadge></td>
          <td>{row.supplier_name}<br /><small>{row.supplier_nif}</small></td><td>{row.document_type} {row.document}</td><td>{day(row.document_date)}</td><td className="numeric">{formatEuro(row.amount_cents)}</td>
        </tr>)}
      </tbody></table></div>
      {preview.rows.length > 100 && <p className="footnote">A mostrar 100 de {preview.rows.length} linhas; a importação inclui o ficheiro completo.</p>}
      <div className="efc-actions">
        <ActionButton type="button" className="primary" disabled={busy || !!preview.conflict_count || !preview.new_count} onClick={() => void confirmImport()}>Importar {preview.new_count} documento(s)</ActionButton>
        <ActionButton type="button" disabled={busy} onClick={() => { setPreview(null); setSource(null); setStep("files"); }}>Escolher outro ficheiro</ActionButton>
        <ActionButton type="button" disabled={busy} onClick={() => setStep("attachments")}>Continuar sem importar</ActionButton>
      </div>
    </section>}

    {["attachments", "purchases", "review", "finish"].includes(step) && <section className="efc-step">
      {step === "attachments" && <><h3>3. Procurar anexos</h3>
        <p>Procura o PDF ou a fotografia de cada documento nas pastas de anexos. O conteúdo do documento (QR fiscal, texto ou OCR local) tem prioridade; o nome do ficheiro é apenas um indício. Nada é associado automaticamente quando há divergências ou vários candidatos.</p>
        {manager && <ActionButton type="button" className="primary" disabled={busy || loading || !validPeriod} onClick={() => void verify(["attachments"])}><Search size={17} />Procurar anexos no Drive</ActionButton>}</>}
      {step === "purchases" && <><h3>4. Conferir compras</h3>
        <p>{data?.mode === "integrated" ? "Consulta o ERP para cada documento. Uma falha na ligação nunca é tratada como prova de que a compra não existe." : "Procura cada documento nas compras registadas em Compras e compara fornecedor, número, data, valores e estado."} As diferenças de arredondamento dentro da tolerância configurada continuam visíveis.</p>
        {admin && data?.mode === "integrated" && <label className="checkbox"><input type="checkbox" checked={confirmDemo} disabled={busy} onChange={e => setConfirmDemo(e.target.checked)} />Confirmo a consulta de dados reais se esta empresa estiver em demonstração.</label>}
        {manager && <ActionButton type="button" className="primary" disabled={busy || loading || !validPeriod} onClick={() => void verify(["purchases"])}><RefreshCw size={17} />Conferir compras</ActionButton>}</>}
      {step === "review" && <ReviewStep rows={rows} strong={strong} proposals={proposals} batch={batch} setBatch={setBatch} busy={busy} manager={manager} mode={data?.mode}
        run={run} refresh={refresh} setNotice={setNotice} onReview={onReview} start={start} end={end} filterStore={filterStore} admin={admin} onUpdated={onUpdated} />}
      {step === "finish" && <FinishStep rows={rows} />}

      {step !== "review" && <>
        <div className="efc-summary" aria-label="Resumo do período">
          {Object.entries(counts("result")).map(([key, count]) => <span key={key}><StatusBadge tone={RESULT[key]?.[1]}>{count as number} {RESULT[key]?.[0] || key}</StatusBadge></span>)}
          {!!data?.summary?.without_efatura && <span><StatusBadge tone="warning">{data.summary.without_efatura} sem e-Fatura</StatusBadge></span>}
        </div>
        <div className="efc-filters filter-bar" role="group" aria-label="Filtrar documentos">
          <Field label="Fornecedor ou documento"><input value={supplierFilter} onChange={e => setSupplierFilter(e.target.value)} placeholder="Nome, NIF ou número" /></Field>
          <Field label="Estado"><select value={stateFilter} onChange={e => setStateFilter(e.target.value)}>
            <option value="">Todos</option>
            <optgroup label="Anexo">{Object.entries(ATTACHMENT).map(([key, [label]]) => <option key={key} value={key}>{label}</option>)}</optgroup>
            <optgroup label="Compra">{Object.entries(PURCHASE).map(([key, [label]]) => <option key={key} value={key}>{label}</option>)}</optgroup>
            <optgroup label="Resultado">{Object.entries(RESULT).map(([key, [label]]) => <option key={"r" + key} value={key}>{label}</option>)}</optgroup>
          </select></Field>
          <Field label="Ação pendente"><select value={actionFilter} onChange={e => setActionFilter(e.target.value)}>{ACTIONS.map(([key, label]) => <option key={key} value={key}>{label}{key && data?.summary?.actions?.[key] ? ` (${data.summary.actions[key]})` : ""}</option>)}</select></Field>
        </div>
        {loading && <p role="status">A carregar documentos do período…</p>}
        <ConferenceTable rows={visible} onOpen={setSelected} />
        {!loading && data && !visible.length && <p>{rows.length ? "Não há documentos com estes filtros nesta etapa. Limpe os filtros ou avance." : `Não há documentos de ${day(start)} a ${day(end)}. Volte à primeira etapa para importar o ficheiro e-Fatura.`}</p>}
      </>}
      <div className="efc-actions">
        {stepIndex > 0 && <ActionButton type="button" disabled={busy} onClick={() => setStep(STEPS[stepIndex - 1][0] === "import" && !preview ? "files" : STEPS[stepIndex - 1][0])}>Etapa anterior</ActionButton>}
        {step !== "finish" ? <ActionButton type="button" className="primary" disabled={busy} onClick={() => setStep(STEPS[stepIndex + 1][0])}>Guardar e continuar</ActionButton>
          : <ActionButton type="button" className="primary" onClick={onClose}><CheckCircle size={17} />Concluir</ActionButton>}
        <ActionButton type="button" disabled={busy} onClick={onClose}>Guardar e sair</ActionButton>
      </div>
    </section>}

    {selected && <RowDetail row={selected} busy={busy} manager={manager} mode={data?.mode} run={run} refresh={async () => { await refresh(); setSelected(null); }}
      onReview={onReview} onClose={() => setSelected(null)} setNotice={setNotice} />}
  </div>;
}

function ConferenceTable({ rows, onOpen }: { rows: Row[]; onOpen: (row: Row) => void }) {
  const [page, setPage] = useState(1);
  const pages = Math.max(1, Math.ceil(rows.length / 25)), current = Math.min(page, pages);
  return <>
    <div className="table-wrap"><table className="efc-table"><thead><tr>
      <th>Fornecedor</th><th>Tipo e número</th><th>Data e total</th><th>e-Fatura</th><th>Anexo</th><th>Compra</th><th>Resultado</th><th>Próxima ação</th>
    </tr></thead><tbody>{rows.slice((current - 1) * 25, current * 25).map(row => <tr key={row.id}>
      <td><strong>{row.supplier_name}</strong><br /><small>{row.supplier_nif}</small></td>
      <td>{row.document_type} {row.document}</td>
      <td>{day(row.document_date)}<br /><span className="numeric">{formatEuro(row.amount_cents)}</span></td>
      <td><StatusBadge tone={row.efatura_state === "present" ? "success" : "warning"}>{row.efatura_state === "present" ? "Comunicado" : "Sem correspondência"}</StatusBadge></td>
      <td><StatusBadge tone={ATTACHMENT[row.attachment_state]?.[1]}>{ATTACHMENT[row.attachment_state]?.[0] || row.attachment_state}</StatusBadge></td>
      <td><StatusBadge tone={PURCHASE[row.purchase_state]?.[1]}>{PURCHASE[row.purchase_state]?.[0] || row.purchase_state}</StatusBadge>{row.purchase_source === "erp" && <small> · ERP</small>}</td>
      <td><StatusBadge tone={RESULT[row.result]?.[1]}>{RESULT[row.result]?.[0] || row.result}</StatusBadge></td>
      <td><ActionButton type="button" className="efc-link" onClick={() => onOpen(row)}>{row.action === "none" ? "Consultar" : row.action_label}</ActionButton></td>
    </tr>)}</tbody></table></div>
    {pages > 1 && <div className="efc-pagination"><ActionButton type="button" disabled={current === 1} onClick={() => setPage(current - 1)}>Anterior</ActionButton><span>Página {current} de {pages} · {rows.length} documentos</span><ActionButton type="button" disabled={current === pages} onClick={() => setPage(current + 1)}>Seguinte</ActionButton></div>}
  </>;
}

function CandidateFields({ candidate }: { candidate: Row }) {
  const fields = candidate.fields || {};
  return <ul className="efc-fields">
    {(fields.matching || []).map((field: string) => <li key={field} className="match">✓ {field} coincide</li>)}
    {(fields.divergent || []).map((field: string) => <li key={field} className="diverge">✗ {field} diverge</li>)}
    {(fields.missing || []).map((field: string) => <li key={field} className="missing">? {field} não identificado</li>)}
  </ul>;
}

function CandidateCard({ row, candidate, busy, manager, run, refresh, onReview, setNotice }: {
  row: Row; candidate: Row; busy: boolean; manager: boolean; run: (task: () => Promise<void>) => Promise<void>;
  refresh: () => Promise<void>; onReview: (row: Row) => void | Promise<void>; setNotice: (text: string) => void;
}) {
  const [show, setShow] = useState(false);
  const [label, tone] = STRENGTH[candidate.strength] || STRENGTH.ambigua;
  return <div className="efc-candidate">
    <div className="efc-candidate-title"><Paperclip size={16} /><b>{candidate.name}</b><StatusBadge tone={tone}>{label}</StatusBadge></div>
    <CandidateFields candidate={candidate} />
    <p className="footnote">Motivo: {(candidate.reasons || []).join("; ") || "—"}{candidate.source && <> · leitura por {candidate.source === "qr" ? "QR fiscal" : candidate.source === "ocr" ? "OCR local" : "texto do PDF"}</>}</p>
    {show && <p>Para rever este candidato, utilize “Abrir no Drive”. A abertura contacta o Google e está sujeita à sessão e às permissões nesse serviço. A IAZELA não carrega um visualizador externo nesta página.</p>}
    <div className="efc-actions">
      <ActionButton type="button" onClick={() => setShow(!show)}>{show ? "Ocultar informação" : "Como rever o documento"}</ActionButton>
      <ActionLink href={candidate.url} target="_blank" rel="noopener noreferrer">Abrir no Drive</ActionLink>
      {manager && <>
        <ActionButton type="button" className="primary" disabled={busy} onClick={() => void run(async () => {
          const warn = candidate.strength !== "forte" ? " Esta proposta não é forte: confirme que verificou os campos divergentes ou não identificados." : "";
          if (!(await confirmAction({ title: "Associar anexo?", message: `Confirma que abriu «${candidate.name}» e que corresponde a ${row.document_type} ${row.document} de ${row.supplier_name}?${warn}`, confirmLabel: "Associar" }))) return;
          await api(`/invoice-workflow/${row.id}/attach`, { attachment_key: candidate.drive_id, confirmed: true, reason: "Anexo revisto no assistente de conferência" + (candidate.strength !== "forte" ? " (proposta não forte, verificada manualmente)" : "") });
          setNotice("Anexo associado e validado."); await refresh();
        })}>Associar</ActionButton>
        <ActionButton type="button" disabled={busy} onClick={() => void run(async () => { await onReview(row); })}>Escolher outro</ActionButton>
        <ActionButton type="button" disabled={busy} onClick={() => void run(async () => {
          if (!(await confirmAction({ title: "Rejeitar esta proposta?", message: `«${candidate.name}» deixa de ser sugerido para este documento. O ficheiro não é alterado nem apagado.`, confirmLabel: "Rejeitar", tone: "warning" }))) return;
          await api(`/invoice-workflow/${row.id}/reject`, { drive_id: candidate.drive_id, reason: "Rejeitado no assistente de conferência" });
          setNotice("Proposta rejeitada."); await refresh();
        })}><Ban size={16} />Rejeitar</ActionButton>
      </>}
    </div>
  </div>;
}

function ReviewStep({ rows, strong, proposals, batch, setBatch, busy, manager, mode, run, refresh, setNotice, onReview, start, end, filterStore, admin, onUpdated }: {
  rows: Row[]; strong: Row[]; proposals: Row[]; batch: string[]; setBatch: (ids: string[]) => void; busy: boolean; manager: boolean; mode?: string;
  run: (task: () => Promise<void>) => Promise<void>; refresh: () => Promise<void>; setNotice: (text: string) => void; onReview: (row: Row) => void | Promise<void>;
  start: string; end: string; filterStore: string; admin: boolean; onUpdated: () => Promise<void>;
}) {
  const [preparing, setPreparing] = useState<Row | null>(null), [requesting, setRequesting] = useState<Row[] | null>(null);
  const missingPurchase = rows.filter(row => row.action === "prepare_purchase");
  const missingDocument = rows.filter(row => row.action === "request_document");
  const bySupplier = Object.values(missingDocument.reduce((acc: Row, row) => ({ ...acc, [row.supplier_nif]: [...(acc[row.supplier_nif] || []), row] }), {})) as Row[][];
  return <>
    <h3>5. Rever propostas</h3>
    <p>Confirme apenas o que verificou. As propostas fortes podem ser confirmadas em conjunto depois de pré-visualizadas; as possíveis e ambíguas são revistas uma a uma.</p>
    <h4>Anexos propostos ({proposals.length})</h4>
    {!!strong.length && manager && <div className="efc-batch">
      <label className="checkbox"><input type="checkbox" checked={batch.length === strong.length} onChange={e => setBatch(e.target.checked ? strong.map(row => row.id) : [])} />Selecionar as {strong.length} propostas fortes</label>
      <ActionButton type="button" className="primary" disabled={busy || !batch.length} onClick={() => void run(async () => {
        if (!(await confirmAction({ title: `Confirmar ${batch.length} associação(ões)?`, message: "Confirma que pré-visualizou estes anexos? Só são associadas propostas que continuem fortes e únicas; as restantes ficam para revisão.", confirmLabel: "Confirmar selecionadas" }))) return;
        const items = strong.filter(row => batch.includes(row.id)).map(row => ({ id: row.id, drive_id: row.drive_candidates[0].drive_id }));
        const result = await api("/invoice-workflow/attach-batch", { items, confirmed: true, reason: "Propostas fortes pré-visualizadas e confirmadas em conjunto" });
        setBatch([]); setNotice(`${result.attached.length} anexo(s) associados.${result.skipped.length ? ` ${result.skipped.length} ficaram para revisão individual.` : ""}`); await refresh();
      })}>Confirmar selecionadas ({batch.length})</ActionButton>
    </div>}
    {proposals.map(row => <details key={row.id} className="efc-proposal" open={proposals.length <= 3}>
      <summary><b>{row.supplier_name}</b> · {row.document_type} {row.document} · {day(row.document_date)} · {formatEuro(row.amount_cents)}{strong.includes(row) && manager && <label className="checkbox" onClick={e => e.stopPropagation()}><input type="checkbox" checked={batch.includes(row.id)} onChange={e => setBatch(e.target.checked ? [...batch, row.id] : batch.filter(id => id !== row.id))} />Incluir no lote</label>}</summary>
      {row.drive_candidates.length > 1 && <Notice tone="warning">Há {row.drive_candidates.length} anexos candidatos. Escolha o correto; nenhum é associado automaticamente.</Notice>}
      {row.drive_candidates.map((candidate: Row) => <CandidateCard key={candidate.drive_id} row={row} candidate={candidate} busy={busy} manager={manager} run={run} refresh={refresh} onReview={onReview} setNotice={setNotice} />)}
    </details>)}
    {!proposals.length && <p>Não há anexos por confirmar neste período.</p>}

    <h4>Lançamentos em falta{mode === "integrated" ? ` (${missingPurchase.length})` : ""}</h4>
    {mode === "integrated" ? <>
      <p>No modo integrado, o lançamento é feito no ERP. O conector atual não cria compras: prepare e exporte a proposta para lançar no ERP, e volte a conferir depois.</p>
      <InvoiceWorkflow start={start} end={end} store={filterStore} admin={admin} manager={manager} onUpdated={onUpdated} onReview={onReview} />
    </> : <>
      <LaunchProposals start={start} end={end} store={filterStore} busy={busy} manager={manager} run={run} setNotice={setNotice} refresh={refresh}
        onPrepare={(id: string) => void run(async () => { const row = rows.find(item => item.id === id) || { id }; setPreparing({ row, proposal: await api(`/invoice-workflow/${id}/prepare-purchase`) }); })} />
      {preparing && <PrepareForm key={preparing.row.id} row={preparing.row} proposal={preparing.proposal} busy={busy} run={run}
        onDone={async message => { setPreparing(null); setNotice(message); await refresh(); }} onCancel={() => setPreparing(null)} />}
    </>}

    <h4>Documentos em falta ({missingDocument.length})</h4>
    {bySupplier.map(group => <div key={group[0].supplier_nif} className="efc-pending"><span><b>{group[0].supplier_name}</b> · {group.length} documento(s): {group.map(row => row.document).join(", ")}</span>
      {manager && <ActionButton type="button" disabled={busy} onClick={() => setRequesting(group)}><Mail size={16} />Pedir ao fornecedor</ActionButton>}</div>)}
    {!missingDocument.length && <p>Não há documentos em falta.</p>}
    {requesting && <RequestForm rows={requesting} busy={busy} run={run} onDone={async message => { setRequesting(null); setNotice(message); await refresh(); }} onCancel={() => setRequesting(null)} />}
  </>;
}

/** Propostas automáticas de lançamento (modo autónomo): prontas confirmam-se em lote; as restantes revêem-se. */
function LaunchProposals({ start, end, store, busy, manager, run, setNotice, refresh, onPrepare }: {
  start: string; end: string; store: string; busy: boolean; manager: boolean; run: (task: () => Promise<void>) => Promise<void>;
  setNotice: (text: string) => void; refresh: () => Promise<void>; onPrepare: (id: string) => void;
}) {
  const [data, setData] = useState<Row | null>(null), [selected, setSelected] = useState<string[]>([]);
  const [overrides, setOverrides] = useState<Row>({}), [createSuppliers, setCreateSuppliers] = useState(false), [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    api(`/invoice-workflow/launch-proposals?start=${start}&end=${end}&store_id=${encodeURIComponent(store)}`).then(result => { if (active) { setData(result); setSelected([]); } }).catch(() => { if (active) setData({ proposals: [], categories: [] }); });
    return () => { active = false; };
  }, [start, end, store, version]);
  if (!data) return <p role="status">A preparar propostas de lançamento…</p>;
  const proposals: Row[] = data.proposals;
  // Uma proposta com fornecedor por criar fica pronta quando o utilizador autoriza a criação e indica a rubrica.
  const effective = (item: Row) => {
    const missing = item.missing.filter((m: string) => !(createSuppliers && m === "Fornecedor por criar") && !(overrides[item.id]?.category_id && m.startsWith("Rubrica")) && !(overrides[item.id]?.due_date && m.startsWith("Vencimento")));
    return { missing, ready: !missing.length };
  };
  const ready = proposals.filter(item => effective(item).ready);
  const setOverride = (id: string, key: string, value: string) => setOverrides({ ...overrides, [id]: { ...overrides[id], [key]: value || undefined } });
  return <div className="efc-launch">
    <p>Propostas preparadas a partir do e-Fatura e do anexo validado (QR fiscal, texto ou OCR). A rubrica vem do fornecedor; o vencimento vem do anexo ou do prazo habitual do fornecedor. Lançar cria a compra confirmada e o movimento previsto; não regista pagamentos nem movimenta stock.</p>
    {!proposals.length && <p>Não há documentos validados por lançar neste período.</p>}
    {!!proposals.length && <>
      <label className="checkbox"><input type="checkbox" checked={createSuppliers} onChange={e => setCreateSuppliers(e.target.checked)} />Criar os fornecedores em falta com o nome e o NIF do e-Fatura (fica no histórico).</label>
      {manager && <div className="efc-batch">
        <label className="checkbox"><input type="checkbox" checked={!!ready.length && selected.length === ready.length} onChange={e => setSelected(e.target.checked ? ready.map(item => item.id) : [])} />Selecionar as {ready.length} propostas prontas</label>
        <ActionButton type="button" className="primary" disabled={busy || !selected.length} onClick={() => void run(async () => {
          const items = proposals.filter(item => selected.includes(item.id));
          const total = items.reduce((sum, item) => sum + item.amount_cents, 0);
          if (!(await confirmAction({ title: `Lançar ${items.length} documento(s)?`, message: `Serão criadas ${items.length} compra(s) confirmadas (${formatEuro(total)}) e os respetivos movimentos previstos.${createSuppliers ? " Os fornecedores em falta serão criados." : ""} Uma compra confirmada não se apaga: corrige-se com uma nota de crédito em Compras.`, confirmLabel: "Lançar" }))) return;
          const result = await api("/invoice-workflow/launch-proposals/confirm", { items: items.map(item => ({ id: item.id, category_id: overrides[item.id]?.category_id || null, due_date: overrides[item.id]?.due_date || null })), create_missing_suppliers: createSuppliers, confirmed: true }, { timeoutMs: 120_000 });
          setNotice(`${result.launched.length} documento(s) lançados.${result.skipped.length ? ` ${result.skipped.length} ficaram por lançar: ${result.skipped.map((s: Row) => s.reason).join(" | ")}` : ""}`);
          setVersion(v => v + 1); await refresh();
        })}>Lançar selecionadas ({selected.length})</ActionButton>
      </div>}
      <div className="table-wrap"><table><thead><tr><th></th><th>Documento</th><th className="numeric">Total</th><th>Rubrica</th><th>Vencimento</th><th>Linhas</th><th>Estado</th></tr></thead><tbody>
        {proposals.map(item => { const state = effective(item); return <tr key={item.id}>
          <td>{manager && <input type="checkbox" aria-label={`Selecionar ${item.document}`} disabled={!state.ready} checked={selected.includes(item.id)} onChange={e => setSelected(e.target.checked ? [...selected, item.id] : selected.filter(id => id !== item.id))} />}</td>
          <td><b>{item.supplier_name}</b><br /><small>{item.document_type} {item.document} · {day(item.document_date)}</small></td>
          <td className="numeric">{formatEuro(item.amount_cents)}</td>
          <td><select aria-label={`Rubrica de ${item.document}`} value={overrides[item.id]?.category_id || item.category_id || ""} onChange={e => setOverride(item.id, "category_id", e.target.value)}><option value="">Por indicar</option>{data.categories.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></td>
          <td><input type="date" aria-label={`Vencimento de ${item.document}`} min={item.document_date} value={overrides[item.id]?.due_date || item.due_date || ""} onChange={e => setOverride(item.id, "due_date", e.target.value)} />{item.due_source && !overrides[item.id]?.due_date && <><br /><small>{item.due_source}</small></>}</td>
          <td><small>{item.lines.map((line: Row) => `${line.price} € a ${line.vat}%`).join(" · ") || "—"}{item.lines_source && <><br />{item.lines_source}</>}</small></td>
          <td>{state.ready ? <StatusBadge tone="success">Pronta</StatusBadge> : <><StatusBadge tone="warning">Requer revisão</StatusBadge><ul className="efc-missing">{state.missing.map((m: string) => <li key={m}>{m}</li>)}</ul>{manager && <ActionButton type="button" className="efc-link" disabled={busy} onClick={() => onPrepare(item.id)}><FileText size={14} />Rever e preparar</ActionButton>}</>}</td>
        </tr>; })}
      </tbody></table></div>
    </>}
  </div>;
}

function PrepareForm({ row, proposal, busy, run, onDone, onCancel }: {
  row: Row; proposal: Row; busy: boolean; run: (task: () => Promise<void>) => Promise<void>; onDone: (message: string) => Promise<void>; onCancel: () => void;
}) {
  const fields = proposal.fields;
  const candidates: Row[] = proposal.supplier.candidates || [];
  const [supplierId, setSupplierId] = useState(proposal.supplier.state === "exists" ? candidates.find(c => c.is_supplier && c.active)?.id || "" : "");
  const supplierCategory = candidates.find(c => c.id === supplierId)?.category_id || "";
  const [newSupplier, setNewSupplier] = useState({ name: fields.supplier_name || "", nif: fields.supplier_nif, email: "", address: "" });
  const [form, setForm] = useState({ document: fields.document, document_type: fields.document_type, document_date: fields.document_date, due_date: "", category_id: proposal.supplier.state === "exists" ? candidates.find(c => c.is_supplier && c.active)?.category_id || "" : "", notes: "" });
  const [lines, setLines] = useState<Row[]>(proposal.lines.length ? proposal.lines : []);
  const [confirmed, setConfirmed] = useState(false);
  const create = proposal.supplier.state === "not_found";
  const total = lines.reduce((sum, line) => sum + Math.round(Number(String(line.quantity || 0).replace(",", ".")) * Number(String(line.price || 0).replace(",", ".")) * (1 + Number(String(line.vat || 0).replace(",", ".")) / 100) * 100), 0);
  const target = Math.abs(fields.total_cents);
  const setLine = (index: number, key: string, value: string) => setLines(lines.map((line, i) => i === index ? { ...line, [key]: value } : line));
  return <div className="panel efc-prepare">
    <h4>Preparar lançamento · {row.supplier_name} · {row.document}</h4>
    <p>É criado um <b>rascunho</b> no módulo Compras, com o anexo de origem. Não fica pago e não movimenta caixa, banco nem stock; a confirmação faz-se em Compras, com as validações próprias.</p>
    {(proposal.review || []).map((item: string) => <p key={item} className="workflow-warning">Por rever: {item}</p>)}
    {create ? <fieldset><legend>Fornecedor novo (não existe nenhuma entidade com este NIF)</legend><div className="form-grid">
      <Field label="Nome (do e-Fatura; confirmar)"><input value={newSupplier.name} onChange={e => setNewSupplier({ ...newSupplier, name: e.target.value })} /></Field>
      <Field label="NIF"><input value={newSupplier.nif} readOnly /></Field>
      <Field label="Email, se conhecido"><input type="email" value={newSupplier.email} onChange={e => setNewSupplier({ ...newSupplier, email: e.target.value })} /></Field>
      <Field label="Morada, se conhecida"><input value={newSupplier.address} onChange={e => setNewSupplier({ ...newSupplier, address: e.target.value })} /></Field>
    </div></fieldset> : <Field label="Fornecedor"><select value={supplierId} onChange={e => setSupplierId(e.target.value)}><option value="">Escolher fornecedor</option>{candidates.map(c => <option key={c.id} value={c.id} disabled={!c.is_supplier || !c.active}>{c.name} · {c.nif}{!c.is_supplier ? " (não é fornecedor)" : !c.active ? " (inativo)" : ""}</option>)}</select></Field>}
    {proposal.supplier.state === "review" && <p className="workflow-warning">A entidade com este NIF está inativa ou não está classificada como fornecedor. Reveja-a em Tabelas → Entidades antes de continuar.</p>}
    <div className="form-grid">
      <Field label="Tipo (e-Fatura)"><input value={form.document_type} readOnly /></Field>
      <Field label="Número (e-Fatura)"><input value={form.document} onChange={e => setForm({ ...form, document: e.target.value })} /></Field>
      <Field label="Data de emissão (e-Fatura)"><input type="date" value={form.document_date} onChange={e => setForm({ ...form, document_date: e.target.value })} /></Field>
      <Field label="Vencimento (indicar)"><input type="date" required value={form.due_date} min={form.document_date} onChange={e => setForm({ ...form, due_date: e.target.value })} /></Field>
      <Field label="Rubrica por omissão das linhas"><select value={form.category_id} onChange={e => setForm({ ...form, category_id: e.target.value })}><option value="">{supplierCategory ? "Rubrica do fornecedor" : "Escolher depois em Compras"}</option>{proposal.categories.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
    </div>
    <h5>Linhas {lines[0]?.origin && <small>· origem: {lines[0].origin}</small>}</h5>
    {!lines.length && <p>Não foram identificadas linhas no documento. Introduza apenas o que consta da fatura.{proposal.line_suggestion && <> <ActionButton type="button" onClick={() => setLines([proposal.line_suggestion])}>Usar base e IVA do e-Fatura</ActionButton></>}</p>}
    {lines.map((line, index) => <div className="form-grid efc-line" key={index}>
      <Field label="Descrição"><input value={line.description} onChange={e => setLine(index, "description", e.target.value)} /></Field>
      <Field label="Natureza"><select value={line.nature} onChange={e => setLine(index, "nature", e.target.value)}><option value="expense">Despesa</option><option value="service">Serviço</option></select></Field>
      <Field label="Quantidade"><input inputMode="decimal" value={line.quantity} onChange={e => setLine(index, "quantity", e.target.value)} /></Field>
      <Field label="Preço unitário sem IVA (€)"><input inputMode="decimal" value={line.price} onChange={e => setLine(index, "price", e.target.value)} /></Field>
      <Field label="Taxa de IVA (%)"><input inputMode="decimal" value={line.vat} placeholder="Confirmar" onChange={e => setLine(index, "vat", e.target.value)} /></Field>
      <Field label="Rubrica da linha"><select value={line.category_id || ""} onChange={e => setLine(index, "category_id", e.target.value)}><option value="">Por omissão</option>{proposal.categories.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      <ActionButton type="button" onClick={() => setLines(lines.filter((_, i) => i !== index))}>Remover</ActionButton>
    </div>)}
    <ActionButton type="button" onClick={() => setLines([...lines, { description: "", nature: "expense", quantity: "1", price: "", vat: "" }])}>Adicionar linha</ActionButton>
    <p className={Math.abs(total - target) > 2 ? "workflow-warning" : ""}>Total das linhas: <b>{formatEuro(total)}</b> · total do documento: <b>{formatEuro(target)}</b>{Math.abs(total - target) > 2 && " — os totais têm de coincidir."}</p>
    <label className="checkbox"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />Revi fornecedor, documento e linhas. {create && "Confirmo a criação do fornecedor. "}O rascunho não é pago nem movimenta stock.</label>
    <div className="efc-actions">
      <ActionButton type="button" className="primary" disabled={busy || !confirmed || !form.due_date || !lines.length || (!create && !supplierId)} onClick={() => void run(async () => {
        const result = await api(`/invoice-workflow/${row.id}/prepare-purchase`, {
          ...(create ? { create_supplier: newSupplier } : { supplier_id: supplierId }), document: form.document, document_type: form.document_type,
          document_date: form.document_date, due_date: form.due_date, category_id: form.category_id || null, notes: form.notes,
          lines: lines.map(line => ({ description: line.description, nature: line.nature, quantity: String(line.quantity), price: String(line.price), vat: String(line.vat), category_id: line.category_id || null })), confirmed: true,
        });
        await onDone(result.message);
      })}>Criar rascunho em Compras</ActionButton>
      <ActionButton type="button" disabled={busy} onClick={onCancel}>Cancelar</ActionButton>
    </div>
  </div>;
}

function RequestForm({ rows, busy, run, onDone, onCancel }: {
  rows: Row[]; busy: boolean; run: (task: () => Promise<void>) => Promise<void>; onDone: (message: string) => Promise<void>; onCancel: () => void;
}) {
  const supplier = rows[0];
  const list = rows.map(row => `- ${row.document_type} ${row.document}, de ${day(row.document_date)}, ${formatEuro(row.amount_cents)}`).join("\n");
  const [to, setTo] = useState(""), [subject, setSubject] = useState(`Pedido de cópia de faturas — ${supplier.supplier_name}`);
  const [message, setMessage] = useState(`Bom dia,\n\nNão encontramos nos nossos registos o original das seguintes faturas comunicadas no e-Fatura:\n${list}\n\nPedimos o envio de uma cópia em PDF. Segue em anexo a lista dos documentos.\n\nObrigado.`);
  const [prepared, setPrepared] = useState<Row | null>(null);
  return <div className="panel efc-request">
    <h4>Pedir documentos a {supplier.supplier_name}</h4>
    <div className="form-grid"><Field label="Email do fornecedor"><input type="email" value={to} onChange={e => { setTo(e.target.value); setPrepared(null); }} /></Field>
      <Field label="Assunto"><input value={subject} onChange={e => { setSubject(e.target.value); setPrepared(null); }} /></Field></div>
    <Field label="Mensagem"><textarea rows={8} value={message} onChange={e => { setMessage(e.target.value); setPrepared(null); }} /></Field>
    {prepared && <Notice tone="info">Pré-visualização pronta: de {prepared.manifest?.sender?.email || "remetente configurado"} para {to}, com a lista dos {rows.length} documento(s) em anexo. Confirme para enviar.</Notice>}
    <div className="efc-actions">
      {!prepared ? <ActionButton type="button" className="primary" disabled={busy || !to.includes("@")} onClick={() => void run(async () => {
        setPrepared(await api("/invoice-workflow/request-documents/preview", { ids: rows.map(row => row.id), to, subject, message }));
      })}>Pré-visualizar envio</ActionButton> : <ActionButton type="button" className="primary" disabled={busy} onClick={() => void run(async () => {
        if (!(await confirmAction({ title: "Enviar pedido ao fornecedor?", message: `O email é enviado para ${to}. Os documentos ficam marcados como solicitados.`, confirmLabel: "Enviar" }))) return;
        const result = await api(`/invoice-workflow/request-documents/${prepared.id}/send`, { revision: prepared.revision, request_key: crypto.randomUUID(), confirmed: true }, { timeoutMs: 60_000 });
        await onDone(result.status === "accepted" || result.status === "received" ? "Pedido enviado ao fornecedor. Os documentos ficaram como solicitados." : `Envio com estado «${result.status}». Consulte o histórico de emails antes de repetir.`);
      })}>Enviar pedido</ActionButton>}
      <ActionButton type="button" disabled={busy} onClick={onCancel}>Cancelar</ActionButton>
    </div>
  </div>;
}

function RowDetail({ row, busy, manager, mode, run, refresh, onReview, onClose, setNotice }: {
  row: Row; busy: boolean; manager: boolean; mode?: string; run: (task: () => Promise<void>) => Promise<void>; refresh: () => Promise<void>;
  onReview: (row: Row) => void | Promise<void>; onClose: () => void; setNotice: (text: string) => void;
}) {
  const isDocument = !String(row.id).includes(":");
  return <section className="panel efc-detail" aria-label="Detalhe do documento">
    <div className="panel-title"><h4>{row.supplier_name} · {row.document_type} {row.document}</h4><ActionButton type="button" onClick={onClose}>Fechar</ActionButton></div>
    <p className="efc-explanation"><b>{row.action_label}.</b> {row.explanation}</p>
    <ul className="efc-dimensions">
      <li>e-Fatura: {row.efatura_state === "present" ? "documento comunicado e importado" : "sem correspondência nos documentos importados"}</li>
      <li>Anexo: {ATTACHMENT[row.attachment_state]?.[0]}{row.attachments?.map((a: Row) => <ActionLink key={a.key} className="efc-link" href={a.url} target="_blank" rel="noopener noreferrer"><Paperclip size={14} />{a.name}</ActionLink>)}</li>
      <li>Compra ({row.purchase_source === "erp" || mode === "integrated" ? "ERP" : "Compras"}): {PURCHASE[row.purchase_state]?.[0]}</li>
    </ul>
    {row.purchase_candidates?.map((candidate: Row) => <div key={candidate.id} className="efc-purchase"><b>{candidate.label}</b>{candidate.confirmed === false && " · rascunho"}
      {candidate.differences.length ? <ul>{candidate.differences.map((d: string) => <li key={d} className="diverge">{d}</li>)}</ul> : <span> · sem diferenças</span>}
      {candidate.notes?.map((n: string) => <p key={n} className="footnote">{n}</p>)}</div>)}
    {row.messages?.map((message: string, index: number) => <p key={index} className="workflow-warning">{message}</p>)}
    {row.drive_candidates?.map((candidate: Row) => <CandidateCard key={candidate.drive_id} row={row} candidate={candidate} busy={busy} manager={manager} run={run} refresh={refresh} onReview={onReview} setNotice={setNotice} />)}
    {!!row.rejected?.length && manager && <p className="footnote">Propostas rejeitadas: {row.rejected.length}. <ActionButton type="button" className="efc-link" disabled={busy} onClick={() => void run(async () => {
      for (const drive_id of row.rejected) await api(`/invoice-workflow/${row.id}/reject/undo`, { drive_id, reason: "Rejeição revista no assistente" });
      setNotice("As propostas rejeitadas voltam a ser consideradas na próxima procura."); await refresh();
    })}>Voltar a considerar</ActionButton></p>}
    {isDocument && <ActionButton type="button" disabled={busy} onClick={() => void run(async () => { await onReview(row); })}>Abrir ficha do documento</ActionButton>}
  </section>;
}

function FinishStep({ rows }: { rows: Row[] }) {
  const pending = rows.filter(row => row.result !== "complete");
  const groups = pending.reduce((acc: Row, row) => ({ ...acc, [row.action]: [...(acc[row.action] || []), row] }), {});
  return <>
    <h3>6. Concluir</h3>
    <p><CheckCircle size={17} /> {rows.length - pending.length} de {rows.length} documento(s) conferidos. O progresso fica guardado; pode voltar a esta conferência mais tarde.</p>
    {Object.entries(groups).map(([action, items]) => <div key={action} className="efc-pending-group">
      <b>{(items as Row[])[0].action_label}</b> · {(items as Row[]).length} documento(s)<br /><small>{(items as Row[])[0].explanation}</small>
    </div>)}
    {!pending.length && <Notice tone="success">Todos os documentos do período estão completos e conferidos.</Notice>}
  </>;
}
