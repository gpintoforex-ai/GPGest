import { ActionLink } from "./components/InteractionUI";
import { confirmAction } from "./components/InteractionUI";
import { ActionButton, AppDialog, ManagedForm, Notice } from "./components/InteractionUI";
import { useEffect, useRef, useState } from "react";
import { Camera, RotateCw, Scissors, Trash2, ArrowUp, ArrowDown, FileText, ScanLine, CheckCircle2 } from "lucide-react";
import { CameraView, captureFrame, flash, liveCameraAvailable, useAutoCapture, useCamera } from "./quickscan/camera";
import { findDocument, type Corners } from "./quickscan/edges";
import type { LiveDetection } from "./quickscan/LiveEdges";
import { api } from "./lib/api";
import { Field } from "./components/Form";
import { calendarDay } from "./RecordList";
import { StatusBadge } from "./components/BusinessUI";
import "./DocumentScanner.css";
type Row = Record<string, any>;
type Page = { id: string; file: File; source: string; preview: string; edit: Row; detected?: boolean };
const defaults = () => ({ rotation: 0, left: 0, top: 0, width: 1, height: 1, colour: "colour", quality: 85, corners: null });
const states: Row = { preparation: "Em preparação", pdf_generated: "PDF gerado", uploading: "A enviar", cloud_confirmed: "Envio confirmado · Associação pendente", cloud_saved: "Guardado na cloud", upload_error: "Erro no envio" };
const bytes = (value: number) => `${(value / 1024 / 1024).toFixed(2)} MB`;
async function responseData(response: Response) {
  if (!response.ok) { const info = await response.json().catch(() => ({})); throw new Error(typeof info.detail === "string" ? info.detail : "Não foi possível processar o documento."); }
  return response;
}

export function ScanButton({ initialDocument, movement, deposit, store, onSaved }: { initialDocument?: Row; movement?: Row; deposit?: Row; store?: string; onSaved?: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  return <><ActionButton type="button" onClick={() => setOpen(true)}><Camera size={18} aria-hidden="true" />Digitalizar documento</ActionButton>{open && <DocumentScanner initialDocument={initialDocument} movement={movement} deposit={deposit} store={store} onClose={() => setOpen(false)} onSaved={onSaved} />}</>;
}

export default function DocumentScanner({ initialDocument, movement, deposit, store, onClose, onSaved }: { initialDocument?: Row; movement?: Row; deposit?: Row; store?: string; onClose: () => void; onSaved?: () => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null), nativeCamera = useRef<HTMLInputElement>(null), gallery = useRef<HTMLInputElement>(null), pdfInput = useRef<HTMLInputElement>(null);
  const urls = useRef(new Set<string>()), replacing = useRef<string | null>(null), cropStart = useRef<number[] | null>(null);
  const [setup, setSetup] = useState<Row | null>(null), [entities, setEntities] = useState<Row[]>([]);
  const [pages, setPages] = useState<Page[]>([]), [existingPDF, setExistingPDF] = useState<File | null>(null);
  const [editing, setEditing] = useState<string | null>(null), [edit, setEdit] = useState<Row>(defaults());
  const [job, setJob] = useState<Row | null>(null), [pdfURL, setPdfURL] = useState(""), [key, setKey] = useState(crypto.randomUUID());
  const [meta, setMeta] = useState<Row>({ store_id: store || "", purchase_id: initialDocument?.id || null, movement_id: movement?.id || null, deposit_id: deposit?.id || null, document_type: initialDocument?.document_type || (deposit ? "DEP" : "FT"), document_date: initialDocument?.document_date || deposit?.preparation_date || (movement?.occurred_at ? calendarDay(movement.occurred_at) : ""), entity: initialDocument?.supplier_name || movement?.entity || "", nif: initialDocument?.supplier_nif || "", document: initialDocument?.document || movement?.document || deposit?.number || "", amount: initialDocument?.amount_cents !== undefined ? String(initialDocument.amount_cents / 100) : movement?.amount_cents !== undefined ? String(movement.amount_cents / 100) : "", category_id: movement?.category_id || "", notes: "" });
  const [busy, setBusy] = useState(false), [phase, setPhase] = useState("preparation"), [progress, setProgress] = useState<number | null>(null), [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false), [warnings, setWarnings] = useState<string[]>([]), [recoverId, setRecoverId] = useState("");
  const [qrNotice, setQrNotice] = useState(""), [qrCandidates, setQrCandidates] = useState<Row[]>([]);
  // Câmara com deteção das margens em direto (modo contínuo: várias páginas sem sair da câmara).
  const [cameraOpen, setCameraOpen] = useState(false), [captured, setCaptured] = useState(0);
  const camera = useCamera(cameraOpen), [autoCapture, toggleAuto] = useAutoCapture();
  const live = useRef<LiveDetection>(null), cameraBox = useRef<HTMLDivElement>(null), capturing = useRef(false), cameraSection = useRef<HTMLElement>(null);
  // Ao abrir, a câmara fica à vista (no telemóvel aparece abaixo dos botões da janela).
  useEffect(() => { if (cameraOpen) cameraSection.current?.scrollIntoView({ block: "start", behavior: "smooth" }); }, [cameraOpen]);
  const dirty = !!(pages.length || existingPDF || job) && phase !== "cloud_saved";
  const locked = !!(initialDocument || movement || deposit || meta.purchase_id);
  const source = pages.find(p => p.id === editing);
  function objectURL(blob: Blob) { const url = URL.createObjectURL(blob); urls.current.add(url); return url; }
  function revoke(url: string) { URL.revokeObjectURL(url); urls.current.delete(url); }
  function close() { if (!busy) onClose(); }
  useEffect(() => {
    dialog.current?.showModal();
    Promise.all([api("/scans/setup"), api("/entities")]).then(([info, items]) => { setSetup(info); setEntities(items); setMeta(current => ({ ...current, store_id: initialDocument?.store_id || movement?.store_id || deposit?.store_id || current.store_id || info.stores[0]?.id || "" })); }).catch(e => setError(e.message));
    return () => { urls.current.forEach(url => URL.revokeObjectURL(url)); };
  }, []);
  useEffect(() => { const listener = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } }; window.addEventListener("beforeunload", listener); return () => window.removeEventListener("beforeunload", listener); }, [dirty]);
  function invalidate() { setJob(null); setPhase("preparation"); setConfirmed(false); setWarnings([]); setKey(crypto.randomUUID()); if (pdfURL) { revoke(pdfURL); setPdfURL(""); } }
  function updateMeta(field: string, value: any) { invalidate(); setMeta(current => ({ ...current, [field]: value })); }
  /** Pré-visualização já recortada (servidor) para as páginas com cantos detetados; a original fica para edição. */
  async function refreshPreview(page: Page) {
    try {
      const form = new FormData(); form.append("file", page.file); form.append("edit", JSON.stringify(page.edit));
      const response = await responseData(await fetch("/api/scans/image-preview", { method: "POST", credentials: "same-origin", body: form }));
      const url = objectURL(await response.blob());
      setPages(current => current.map(p => p.id === page.id && p.preview === page.preview ? { ...p, preview: url } : p));
    } catch { /* a miniatura fica com a fotografia original; o recorte pode ser revisto no editor */ }
  }
  async function addPhotos(files: File[], known?: (Corners | null)[]) {
    if (!files.length) { replacing.current = null; return; }
    setBusy(true);
    setError("");
    try {
      const nextCount = pages.length + files.length - (replacing.current ? 1 : 0);
      if (nextCount > (setup?.settings.max_pages || 20)) throw new Error("Número de páginas acima do limite configurado.");
      if (pages.filter(p => p.id !== replacing.current).reduce((sum, p) => sum + p.file.size, 0) + files.reduce((sum, f) => sum + f.size, 0) > 50*1024*1024) throw new Error("As fotografias ultrapassam 50 MB.");
      const added: Page[] = [];
      for (const file of files) {
        if (file.size > 15*1024*1024 || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new Error("Use fotografias JPG, PNG ou WebP até 15 MB. Para HEIC exporte a fotografia como JPEG.");
        const url = objectURL(file);
        const image = new Image();
        await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error("Não foi possível abrir esta fotografia.")); image.src = url; });
        // Cantos vistos na câmara em direto ou, nas fotografias, detetados localmente; o utilizador revê sempre.
        const corners = known ? known[added.length] : await findDocument(image);
        added.push({ id: crypto.randomUUID(), file, source: url, preview: url, edit: corners ? { ...defaults(), corners } : defaults(), detected: !!corners });
      }
      invalidate(); setExistingPDF(null); setQrNotice(""); setQrCandidates([]);
      if (replacing.current && added[0]) {
        const old = pages.find(p => p.id === replacing.current);
        if (old) { revoke(old.source); if (old.preview !== old.source) revoke(old.preview); }
        const replaceId = replacing.current;
        setPages(current => current.map(p => p.id === replaceId ? added[0] : p));
      } else setPages(current => [...current, ...added]);
      replacing.current = null;
      added.filter(page => page.detected).forEach(page => void refreshPreview(page));
    } catch (e) { setError((e as Error).message); replacing.current = null; } finally { setBusy(false); }
  }
  function openCamera(replace?: string) {
    replacing.current = replace || null;
    setError(""); setCameraOpen(true);
  }
  /** Fotografa a imagem da câmara com os cantos vistos em direto e acrescenta (ou substitui) a página. */
  async function captureLive(seen?: Corners) {
    if (capturing.current || !camera.ready) return;
    const replace = replacing.current;
    if (!replace && pages.length >= (setup?.settings.max_pages || 20)) { setError("Número de páginas acima do limite configurado."); return; }
    capturing.current = true;
    flash(cameraBox.current);
    try {
      const frame = await captureFrame(camera, live, seen);
      const file = new File([frame.full], `pagina-${pages.length + 1}.jpg`, { type: "image/jpeg" });
      await addPhotos([file], [frame.corners]);
      setCaptured(value => value + 1);
      if (replace) setCameraOpen(false); // «Repetir fotografia» substitui só essa página
    } catch (e) { setError((e as Error).message); } finally { capturing.current = false; }
  }
  async function previewEdit(page = source, options = edit) {
    if (!page) return;
    setBusy(true); setError("");
    try {
      const form = new FormData(); form.append("file", page.file); form.append("edit", JSON.stringify(options));
      const response = await responseData(await fetch("/api/scans/image-preview", { method: "POST", credentials: "same-origin", body: form }));
      const url = objectURL(await response.blob());
      if (page.preview !== page.source) revoke(page.preview);
      invalidate(); setPages(current => current.map(p => p.id === page.id ? { ...p, edit: options, preview: url } : p)); setEditing(null);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function remove(page: Page) { invalidate(); revoke(page.source); if (page.preview !== page.source) revoke(page.preview); setPages(current => current.filter(p => p.id !== page.id)); }
  function reorder(index: number, offset: number) { invalidate(); setPages(current => { const copy = [...current]; [copy[index], copy[index+offset]] = [copy[index+offset], copy[index]]; return copy; }); }
  function selectDocument(identifier: string) {
    const found = setup?.documents.find((d: Row) => d.id === identifier);
    invalidate(); setMeta(current => found ? { ...current, purchase_id: found.id, store_id: found.store_id, document_type: found.document_type, document_date: found.document_date, entity: found.supplier_name, nif: found.supplier_nif, document: found.document, amount: String(found.amount_cents / 100) } : { ...current, purchase_id: null });
  }
  function metadata() { const { amount, ...fields } = meta; return { ...fields, category_id: meta.category_id || null, amount_cents: amount === "" ? null : Math.round(Number(String(amount).replace(",", ".")) * 100) }; }
  function applyQR(data: Row) {
    setQrCandidates([]);
    if (locked) {
      const matches = meta.nif === data.nif && meta.document === data.document && meta.document_type === data.document_type && meta.document_date === data.document_date && Math.round(Number(meta.amount)*100) === data.amount_cents;
      setQrNotice(matches ? "QR code corresponde aos dados da ficha. Reveja o PDF antes de guardar." : "O QR code difere dos dados da ficha aberta. Confirme que escolheu o PDF correto; os dados da ficha foram preservados.");
      return;
    }
    const candidates = setup?.documents.filter((doc: Row) => doc.store_id === meta.store_id && doc.supplier_nif === data.nif && doc.document === data.document && doc.document_type === data.document_type && doc.document_date === data.document_date && doc.amount_cents === data.amount_cents) || [];
    const existing = candidates.length === 1 ? candidates[0] : null;
    const suppliers = entities.filter(entity => entity.tax_number === data.nif);
    const entity = suppliers.length === 1 ? suppliers[0] : null;
    invalidate();
    setMeta(current => ({ ...current, purchase_id: existing?.id || null, nif: data.nif, document_type: data.document_type, document: data.document, document_date: data.document_date, amount: String(data.amount_cents/100), entity: existing?.supplier_name || entity?.name || "", category_id: entity?.category_id || "" }));
    setQrNotice(`Dados preenchidos pelo QR code da página ${data.page}. ${existing ? "Associado à ficha existente." : entity ? "Entidade identificada pelo NIF." : "Indique a entidade: o QR fiscal contém o NIF, mas não o nome."} Reveja antes de guardar.`);
  }
  async function loadPDF(file: File) {
    if (file.size > (setup?.settings.max_bytes || 5*1024*1024)) { setError("O PDF ultrapassa o limite configurado."); return; }
    if (pages.length && !(await confirmAction({ title: "Substituir as fotografias?", message: `O PDF «${file.name}» vai substituir as ${pages.length} fotografias desta digitalização. As páginas ainda não guardadas serão descartadas.`, confirmLabel: "Substituir fotografias", tone: "warning" }))) return;
    invalidate(); pages.forEach(page => { revoke(page.source); if (page.preview !== page.source) revoke(page.preview); });
    setPages([]); setExistingPDF(file); setQrCandidates([]); setQrNotice("A procurar QR code nas páginas do PDF…"); setBusy(true); setError("");
    try {
      const form = new FormData(); form.append("file",file);
      const response = await responseData(await fetch("/api/scans/read-qr",{method:"POST",credentials:"same-origin",body:form}));
      const result = await response.json();
      if (result.status === "found") applyQR(result.documents[0]);
      else if (result.status === "multiple") { setQrCandidates(result.documents); setQrNotice("Foram encontrados documentos diferentes. Escolha o QR code correspondente ao documento que pretende registar."); }
      else setQrNotice("Não foi encontrado um QR fiscal legível. Preencha os dados manualmente; os dados atuais foram preservados.");
    } catch (e) { setQrNotice("Não foi possível ler o QR code. Pode preencher os dados manualmente."); setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function generate() {
    setBusy(true); setError(""); setPhase("preparation"); setProgress(0);
    try {
      const form = new FormData(); (existingPDF ? [existingPDF] : pages.map(p => p.file)).forEach(file => form.append("files", file));
      const requestKey = job && !job.cloud_id ? crypto.randomUUID() : key;
      if (requestKey !== key) setKey(requestKey);
      form.append("payload", JSON.stringify({ request_key: requestKey, metadata: metadata(), edits: existingPDF ? [] : pages.map(p => p.edit) }));
      const result = await new Promise<Row>((resolve, reject) => { const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/scans/prepare"); xhr.withCredentials = true; xhr.upload.onprogress = e => { if (e.lengthComputable) setProgress(Math.round(e.loaded / e.total * 100)); }; xhr.onerror = () => reject(new Error("Falha de rede ao gerar o PDF. As páginas continuam nesta janela; tente novamente.")); xhr.onload = () => { try { const response = JSON.parse(xhr.responseText); if (xhr.status < 200 || xhr.status >= 300) reject(new Error(typeof response.detail === "string" ? response.detail : "Não foi possível gerar o PDF.")); else resolve(response); } catch { reject(new Error("Resposta inválida do servidor.")); } }; xhr.send(form); });
      setJob(result); setWarnings(result.warnings || []);
      const pdf = await responseData(await fetch(result.view_url, { credentials: "same-origin" }));
      if (pdfURL) revoke(pdfURL); setPdfURL(objectURL(await pdf.blob())); setPhase("pdf_generated"); setConfirmed(false);
    } catch (e) { setError((e as Error).message); setPhase("preparation"); } finally { setProgress(null); setBusy(false); }
  }
  async function upload() {
    if (!job || !confirmed) return;
    setBusy(true); setError(""); setPhase("uploading");
    try { const result = await api(`/scans/${job.id}/upload`, { confirmed: true }); setJob(result); setPhase(result.status); if (onSaved) { try { await onSaved(); } catch { setError("Guardado na cloud, mas a lista n\u00e3o foi atualizada. Atualize a ficha."); } } }
    catch (e) { setError((e as Error).message); setPhase("upload_error"); try { setJob(await api(`/scans/${job.id}`)); } catch { /* Original ID remains available for retry. */ } }
    finally { setBusy(false); }
  }
  async function recover(saved: Row) {
    if (dirty && !(await confirmAction({ title: "Recuperar PDF guardado?", message: "Os dados e a pré-visualização atuais serão substituídos pelo PDF guardado no servidor. As alterações ainda não guardadas aos dados serão descartadas.", confirmLabel: "Recuperar PDF", tone: "warning" }))) return;
    setBusy(true); setError("");
    try { const result = await api(`/scans/${saved.id}`); const response = await responseData(await fetch(result.view_url, { credentials: "same-origin" })); setJob(result); setMeta({ ...result.metadata, amount: result.metadata.amount_cents === null ? "" : String(result.metadata.amount_cents/100), category_id: result.metadata.category_id || "" }); setPhase(result.status); setConfirmed(false); if (pdfURL) revoke(pdfURL); setPdfURL(objectURL(await response.blob())); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <AppDialog busy={busy} dirty={phase === "cloud_saved" ? false : dirty || undefined} ref={dialog} className="doc-dialog scanner-dialog" aria-label="Digitalizar documento" onRequestClose={() => {  close(); }}>
    <div className="panel-title"><h2>Digitalizar documento</h2></div>
    <p className="footnote">Selecione apenas as páginas e os dados necessários. Ao gerar o PDF, os ficheiros são enviados ao servidor para preparação; o PDF temporário tem expiração de 24 horas. Guardar no Google Drive exige confirmação e não é eliminado ao terminar essa expiração. <a href="/legal/privacidade">Tratamento de documentos e prestadores</a>.</p>
    <StatusBadge tone={phase === "cloud_saved" ? "success" : phase === "upload_error" ? "danger" : "neutral"}>{states[phase]}</StatusBadge>
    {error && <Notice className="error" role="alert">{error}</Notice>}{!setup && <p>A carregar a configuração…</p>}
    {setup && <>
      <p>Empresa: <b>{setup.company.name || "Empresa ativa"}</b> · Google Drive: {setup.account || "Conta por configurar"}. Limites: {setup.settings.max_pages} páginas · PDF até {bytes(setup.settings.max_bytes)}.</p>
      {(!setup.connected || !setup.folder_configured) && <Notice className="error">A pasta cloud ou a ligação Google não está configurada. Pode preparar e descarregar o PDF. Configure a conta e a pasta em Configurações &gt; Parâmetros &gt; Anexos antes de guardar.</Notice>}
      <ActionButton disabled={busy} onClick={() => api("/scans/setup").then(setSetup).catch(e => setError(e.message))}>Atualizar estado da ligação</ActionButton>
      <ActionButton disabled={busy} dismiss onClick={() => { onClose(); window.dispatchEvent(new Event("gpgest-attachment-settings")); }}>Configurar conta e pasta cloud</ActionButton>
      <p>As fotografias ficam nesta janela. Fechar ou recarregar pode perder as páginas capturadas. Depois de gerar, descarregue o PDF para conservar o trabalho.</p>
      <div className="scanner-actions">{liveCameraAvailable() && <ActionButton className="primary" disabled={busy || phase === "cloud_saved" || cameraOpen} onClick={() => openCamera()}><ScanLine size={20} aria-hidden="true" />Câmara com deteção de margens</ActionButton>}<ActionButton disabled={busy || phase === "cloud_saved"} onClick={() => { replacing.current = null; nativeCamera.current?.click(); }}><Camera size={22} />Fotografar / adicionar página</ActionButton><ActionButton disabled={busy || phase === "cloud_saved"} onClick={() => gallery.current?.click()}>Selecionar da galeria</ActionButton><ActionButton disabled={busy || phase === "cloud_saved"} onClick={() => pdfInput.current?.click()}><FileText size={20} />Carregar PDF existente</ActionButton></div>
      <input className="scanner-file" ref={nativeCamera} disabled={busy || phase === "cloud_saved"} type="file" accept="image/*" capture="environment" aria-label="Fotografar página" onChange={e => { const files = Array.from(e.target.files || []); e.target.value = ""; addPhotos(files); }} />
      <input className="scanner-file" ref={gallery} disabled={busy || phase === "cloud_saved"} type="file" accept="image/*" multiple aria-label="Selecionar fotografias da galeria" onChange={e => { const files = Array.from(e.target.files || []); e.target.value = ""; replacing.current = null; addPhotos(files); }} />
      <input className="scanner-file" ref={pdfInput} disabled={busy || phase === "cloud_saved"} type="file" accept="application/pdf,.pdf" aria-label="Carregar PDF existente" onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) loadPDF(file); }} />
      {cameraOpen && <section className="live-camera" ref={cameraSection} aria-label="Câmara com deteção de margens">
        {camera.error ? <Notice tone="warning">{camera.error}</Notice>
          : <CameraView camera={camera} live={live} box={cameraBox} autoCapture={autoCapture} captured={captured}
            active={!!replacing.current || pages.length < (setup.settings.max_pages || 20)} onCapture={corners => void captureLive(corners)} />}
        <div className="live-camera-controls">
          {!camera.error && <ActionButton type="button" className={`live-auto ${autoCapture ? "active" : ""}`} aria-pressed={autoCapture} onClick={toggleAuto}>
            {autoCapture ? <CheckCircle2 size={16} aria-hidden="true" /> : <ScanLine size={16} aria-hidden="true" />}Captura automática {autoCapture ? "ligada" : "desligada"}
          </ActionButton>}
          {!camera.error && <ActionButton type="button" className="qs-shutter" aria-label="Capturar página" disabled={busy || !camera.ready} onClick={() => void captureLive()}><span /></ActionButton>}
          {/recusada/.test(camera.error) && <ActionButton type="button" onClick={camera.retry}>Tentar novamente</ActionButton>}
          <p aria-live="polite">{replacing.current ? "A repetir uma página" : `${pages.length} página(s) nesta digitalização`}</p>
          <ActionButton type="button" onClick={() => { replacing.current = null; setCameraOpen(false); }}>Concluir câmara</ActionButton>
        </div>
      </section>}
      {qrNotice && <p role="status">{qrNotice}</p>}{qrCandidates.map((data,index) => <ActionButton key={index} disabled={busy} onClick={() => applyQR(data)}>{data.document} · {data.nif} · {(data.amount_cents/100).toFixed(2)} €</ActionButton>)}
      <p>{existingPDF ? `PDF existente: ${existingPDF.name} · ${bytes(existingPDF.size)}; páginas verificadas ao gerar.` : `${pages.length} página(s) · fotografias: ${bytes(pages.reduce((sum, p) => sum + p.file.size, 0))}. O tamanho final depende da compressão.`}</p>
      <div className="scanner-pages">{pages.map((page, index) => <article key={page.id}><img src={page.preview} alt={`Página ${index+1}`} /><strong>Página {index+1}</strong>{page.detected && <small className="scanner-detected">Margens detetadas · reveja o recorte</small>}<div className="scanner-actions"><ActionButton disabled={busy || phase === "cloud_saved"} onClick={() => { setEditing(page.id); setEdit({ ...page.edit }); }}><Scissors size={16} />Recortar / melhorar</ActionButton><ActionButton disabled={busy || phase === "cloud_saved"} onClick={() => previewEdit(page, { ...page.edit, rotation: (page.edit.rotation + 90) % 360 })}><RotateCw size={16} />Rodar</ActionButton><ActionButton disabled={busy || phase === "cloud_saved"} onClick={() => { if (liveCameraAvailable()) openCamera(page.id); else { replacing.current = page.id; nativeCamera.current?.click(); } }}>Repetir fotografia</ActionButton><ActionButton aria-label={`Eliminar página ${index+1}`} disabled={busy || phase === "cloud_saved"} confirmation={{ title: "Eliminar página?", message: `A página ${index + 1} será retirada desta digitalização. O ficheiro original não será eliminado.`, confirmLabel: "Eliminar página", tone: "danger" }} onClick={() => remove(page)}><Trash2 size={16} />Eliminar</ActionButton><ActionButton aria-label={`Subir página ${index+1}`} disabled={busy || index === 0 || phase === "cloud_saved"} onClick={() => reorder(index, -1)}><ArrowUp size={16} /></ActionButton><ActionButton aria-label={`Descer página ${index+1}`} disabled={busy || index === pages.length-1 || phase === "cloud_saved"} onClick={() => reorder(index, 1)}><ArrowDown size={16} /></ActionButton></div></article>)}</div>
      {source && <section className="panel scanner-editor" aria-label="Rever página"><h3>Recorte e orientação · revisão manual</h3><div className="scanner-crop" onPointerDown={e => { if (edit.corners || busy) return; const rect = e.currentTarget.getBoundingClientRect(); cropStart.current = [(e.clientX-rect.left)/rect.width, (e.clientY-rect.top)/rect.height]; e.currentTarget.setPointerCapture(e.pointerId); }} onPointerUp={e => { if (!cropStart.current) return; const rect = e.currentTarget.getBoundingClientRect(); const [x,y] = cropStart.current; const right = Math.max(0, Math.min(1, (e.clientX-rect.left)/rect.width)), bottom = Math.max(0, Math.min(1, (e.clientY-rect.top)/rect.height)); const width = Math.abs(right-x), height = Math.abs(bottom-y); if (width > .03 && height > .03) setEdit({ ...edit, left: Math.min(x,right), top: Math.min(y,bottom), width, height }); cropStart.current = null; }}><img draggable={false} src={source.source} alt="Imagem original para recortar" /><div style={{ left: `${edit.left*100}%`, top: `${edit.top*100}%`, width: `${edit.width*100}%`, height: `${edit.height*100}%` }} />{edit.corners?.map((point: number[], index: number) => <span className="scanner-corner" key={index} style={{ left: `${point[0]*100}%`, top: `${point[1]*100}%` }} onPointerDown={e => { e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={e => { if (!e.currentTarget.hasPointerCapture(e.pointerId) || busy) return; const rect = e.currentTarget.parentElement!.getBoundingClientRect(); const corners = edit.corners.map((p: number[]) => [...p]); corners[index] = [Math.max(0, Math.min(1, (e.clientX-rect.left)/rect.width)), Math.max(0, Math.min(1, (e.clientY-rect.top)/rect.height))]; setEdit({ ...edit, corners }); }}>{index+1}</span>)}</div><p>Arraste na imagem para marcar o recorte; em perspetiva, arraste os quatro cantos numerados.</p><div className="form-grid">{[["left", "Esquerda"], ["top", "Topo"], ["width", "Largura"], ["height", "Altura"]].map(([field, label]) => <Field key={field} label={`${label} do recorte (%)`}><input type="number" min="0" max="100" value={Math.round(edit[field]*100)} onChange={e => setEdit({ ...edit, [field]: Number(e.target.value)/100 })} /></Field>)}<Field label="Rotação"><select value={edit.rotation} onChange={e => setEdit({ ...edit, rotation: Number(e.target.value) })}>{[0, 90, 180, 270].map(value => <option key={value} value={value}>{value}°</option>)}</select></Field><Field label="Cor"><select value={edit.colour} onChange={e => setEdit({ ...edit, colour: e.target.value })}><option value="colour">Cor</option><option value="grey">Escala de cinzentos</option><option value="contrast">Contraste</option></select></Field><Field label="Compressão"><select value={edit.quality} onChange={e => setEdit({ ...edit, quality: Number(e.target.value) })}><option value="85">Legível</option><option value="70">Compacta</option></select></Field></div>
        <label className="doc-check"><input type="checkbox" checked={!!edit.corners} onChange={e => setEdit({ ...edit, corners: e.target.checked ? [[0,0],[1,0],[1,1],[0,1]] : null })} />Corrigir perspetiva por quatro cantos</label>{edit.corners && <><p>Os cantos são detetados automaticamente quando possível (câmara com deteção e fotografias); confirme-os ou arraste-os na imagem. A perspetiva aplica-se antes do recorte.</p><div className="form-grid">{["Superior esquerdo", "Superior direito", "Inferior direito", "Inferior esquerdo"].map((label, index) => <div key={label}>{["X", "Y"].map((axis, coordinate) => <Field key={axis} label={`${label} · ${axis} (%)`}><input type="number" min="0" max="100" value={Math.round(edit.corners[index][coordinate]*100)} onChange={e => { const corners = edit.corners.map((p: number[]) => [...p]); corners[index][coordinate] = Number(e.target.value)/100; setEdit({ ...edit, corners }); }} /></Field>)}</div>)}</div></>}
        <div className="scanner-actions"><ActionButton disabled={busy} onClick={() => previewEdit()}>Aplicar e rever página</ActionButton><ActionButton disabled={busy} onClick={() => setEdit(defaults())}>Repor original</ActionButton><ActionButton disabled={busy} onClick={() => setEditing(null)}>Cancelar edição</ActionButton></div></section>}
      <ManagedForm className="doc-form" onSubmit={e => { e.preventDefault(); generate(); }}><fieldset disabled={busy || phase === "cloud_saved"}>
        <Field label="Empresa"><input readOnly value={setup.company.name || "Empresa ativa"} /></Field><Field label="Loja"><select required disabled={locked} value={meta.store_id} onChange={e => updateMeta("store_id", e.target.value)}>{setup.stores.map((s: Row) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        {!initialDocument && !movement && !deposit && <Field label="Associar a documento existente"><select value={meta.purchase_id || ""} onChange={e => selectDocument(e.target.value)}><option value="">Criar documento de gestão após guardar</option>{setup.documents.map((d: Row) => <option key={d.id} value={d.id}>{d.supplier_name} · {d.document}</option>)}</select></Field>}
        {(initialDocument || movement || deposit) && <p>O PDF será associado à ficha aberta. Não cria outro documento.</p>}
        <div className="form-grid"><Field label="Tipo de documento"><input required readOnly={locked} maxLength={10} value={meta.document_type} onChange={e => updateMeta("document_type", e.target.value)} /></Field><Field label="Data do documento"><input type="date" required readOnly={locked} value={meta.document_date} onChange={e => updateMeta("document_date", e.target.value)} /></Field></div>{!meta.document_date && !locked && <ActionButton type="button" onClick={async () => { const date = calendarDay(new Date().toISOString()); if (await confirmAction({ title: "Usar a data de hoje?", message: `O documento e a pasta cloud vão usar a data ${date.split("-").reverse().join("/")}.`, confirmLabel: "Usar esta data" })) updateMeta("document_date", date); }}>Usar data de hoje, após confirmar</ActionButton>}
        <Field label="Fornecedor / entidade"><input list="scanner-entities" readOnly={locked} maxLength={300} value={meta.entity} onChange={e => { invalidate(); const found = entities.find(entity => entity.name === e.target.value); setMeta(current => ({ ...current, entity: e.target.value, nif: found?.tax_number || current.nif })); }} /></Field><datalist id="scanner-entities">{entities.map(entity => <option key={entity.id} value={entity.name} />)}</datalist>
        <div className="form-grid"><Field label="NIF, quando aplicável"><input readOnly={locked} maxLength={30} value={meta.nif} onChange={e => updateMeta("nif", e.target.value)} /></Field><Field label="Número do documento"><input required readOnly={locked} maxLength={100} value={meta.document} onChange={e => updateMeta("document", e.target.value)} /></Field><Field label="Valor (€), quando aplicável"><input readOnly={locked} inputMode="decimal" value={meta.amount} onChange={e => updateMeta("amount", e.target.value)} /></Field><Field label="Categoria"><select value={meta.category_id} onChange={e => updateMeta("category_id", e.target.value)}><option value="">Não indicada</option>{setup.categories.map((c: Row) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field></div><Field label="Observações"><textarea maxLength={2000} value={meta.notes} onChange={e => updateMeta("notes", e.target.value)} /></Field>
      </fieldset><ActionButton disabled={busy || phase === "cloud_saved" || !(pages.length || existingPDF)}><FileText size={18} />Gerar e pré-visualizar PDF</ActionButton></ManagedForm>
      {busy && <p role="status">{states[phase]}{progress !== null ? ` · ${progress}% de fotografias enviadas para gerar` : "…"}</p>}
      {warnings.map((warning, index) => <Notice key={index} className="scanner-warning">{warning}</Notice>)}
      {job && <section className="panel"><h3>PDF pronto para revisão</h3><p>{job.name} · {job.pages} página(s) · {bytes(job.size)}</p>{pdfURL && <><iframe src={pdfURL} title="Pré-visualização da digitalização PDF" className="scanner-pdf" /><div className="scanner-actions"><ActionLink className="button" href={pdfURL} download={job.name}>Descarregar PDF</ActionLink><ActionLink className="button" href={pdfURL} target="_blank" rel="noopener noreferrer">Abrir PDF noutra janela</ActionLink></div></>}
        {phase === "cloud_saved" ? <><StatusBadge tone="warning">{job.validation_status === "validado" ? "Anexo validado" : "Anexo por validar"}</StatusBadge><p>Guardado no Google Drive e associado à ficha. A digitalização não valida automaticamente o conteúdo.</p>{job.validation_status !== "validado" && <ActionButton disabled={busy} onClick={async () => { if (!(await confirmAction({ title: "Validar anexo?", message: `Confirma que abriu e reviu todas as páginas do documento «${meta.document || "Digitalização"}»? O anexo ficará marcado como validado.`, confirmLabel: "Validar anexo" }))) return; setBusy(true); try { const result = await api(`/scans/${job.id}/validate`, { confirmed: true }); setJob(result); if (onSaved) await onSaved(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>Validar anexo após revisão</ActionButton>}</> : <><p>Destino: pasta Google Drive configurada · organização {setup.settings.organisation === "company_period_type" ? "Empresa / Contabilidade / Ano / Mês / Tipo" : setup.settings.organisation === "period_type" ? "Ano / Mês / Tipo" : "Pasta configurada"}, pela data do documento.</p><label className="doc-check"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />Revi o PDF, os dados, a associação e o destino cloud. Confirmo o envio.</label><ActionButton className="primary" disabled={busy || !confirmed || !setup.connected || !setup.folder_configured} onClick={upload}>{phase === "upload_error" ? "Repetir envio / recuperar associação" : "Guardar na cloud"}</ActionButton>
          {phase === "upload_error" && !job.purchase_id && !job.movement_id && <div><Field label="Recuperar associação a ficha existente"><select value={recoverId} onChange={e => setRecoverId(e.target.value)}><option value="">Escolher documento</option>{setup.documents.filter((d: Row) => d.store_id === job.metadata.store_id).map((d: Row) => <option key={d.id} value={d.id}>{d.supplier_name} · {d.document}</option>)}</select></Field><ActionButton disabled={busy || !recoverId} onClick={async () => { setBusy(true); try { const result = await api(`/scans/${job.id}/associate`, { ...job.metadata, purchase_id: recoverId, movement_id: null, deposit_id: null }); setJob(result); setError(""); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>Confirmar associação, sem reenviar o ficheiro</ActionButton></div>}</>}
      </section>}
      {setup.recent.length > 0 && <details><summary>Digitalizações recentes e envios a recuperar</summary>{setup.recent.filter((scan: Row) => (!initialDocument || scan.metadata.purchase_id === initialDocument.id) && (!movement || scan.metadata.movement_id === movement.id) && (!deposit || scan.metadata.deposit_id === deposit.id)).map((scan: Row) => <div className="setting-row" key={scan.id}><span>{scan.name} · {states[scan.status]}</span><ActionButton disabled={busy} onClick={() => recover(scan)}>Abrir / recuperar PDF</ActionButton></div>)}<p>PDFs temporários cifrados expiram após 24 horas. Os identificadores cloud e o histórico ficam preservados.</p></details>}
    </>}
  </AppDialog>;
}

export function ScanSettings({ admin }: { admin: boolean }) {
  const [settings, setSettings] = useState<Row>({ max_pages: 20, max_bytes: 5*1024*1024, organisation: "company_period_type" }), [error, setError] = useState(""), [notice, setNotice] = useState("");
  useEffect(() => { api("/scans/settings").then(r => setSettings(current => ({ ...current, ...r }))).catch(e => setError(e.message)); }, []);
  return <section className="panel"><h3>Digitalização de documentos</h3><p>Usa a conta e a pasta Google Drive configuradas em Anexos. A pasta é verificada quanto à permissão de escrita antes de enviar.</p>{error && <Notice className="error">{error}</Notice>}{notice && <p role="status">{notice}</p>}<ManagedForm className="doc-form" onSubmit={async e => { e.preventDefault(); try { await api("/scans/settings", settings); setNotice("Limites e organização guardados."); } catch (err) { setError((err as Error).message); } }}><fieldset disabled={!admin}><Field label="Máximo de páginas"><input type="number" min="1" max="40" value={settings.max_pages} onChange={e => setSettings({ ...settings, max_pages: Number(e.target.value) })} /></Field><Field label="Tamanho máximo do PDF (MB)"><input type="number" min="1" max="5" value={settings.max_bytes/1024/1024} onChange={e => setSettings({ ...settings, max_bytes: Number(e.target.value)*1024*1024 })} /></Field><Field label="Organização dentro da pasta cloud"><select value={settings.organisation} onChange={e => setSettings({ ...settings, organisation: e.target.value })}><option value="company_period_type">Empresa / Contabilidade / Ano / Mês / Tipo</option><option value="period_type">Ano / Mês / Tipo</option><option value="root">Diretamente na pasta configurada</option></select></Field></fieldset>{admin && <ActionButton>Guardar definições de digitalização</ActionButton>}</ManagedForm></section>;
}
