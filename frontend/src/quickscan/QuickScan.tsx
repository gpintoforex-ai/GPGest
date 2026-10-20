import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import { ArrowDown, ArrowUp, Camera, CheckCircle2, CloudUpload, Crop, Download, FileText, Images, LogOut, Plus, RefreshCw, RotateCw, ScanLine, Trash2 } from "lucide-react";
import Brand from "../components/Brand";
import { FULL_FRAME, findDocument, validCorners, type Corners } from "./edges";
import { deviceToken, quick, QuickError, sendWithProgress } from "./quickApi";
import "./QuickScan.css";

type Mode = "colour" | "enhanced" | "grey" | "bw";
type Edit = { corners: Corners; rotation: 0 | 90 | 180 | 270; colour: Mode };
type Page = { id: string; full: Blob; small: Blob; source: string; preview: string; edit: Edit; detected: boolean };
type Step = "loading" | "pair" | "pin" | "capture" | "edit" | "review" | "pdf" | "done";
type Row = Record<string, any>;

const MODES: [Mode, string][] = [["colour", "Original"], ["enhanced", "Cor"], ["grey", "Cinzentos"], ["bw", "Preto e branco"]];
const MAX_SIDE = 3000, SMALL_SIDE = 1200;
const megabytes = (value: number) => new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 2 }).format(value / 1048576) + " MB";
const slug = (value: string) => value.normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");

function toBlob(canvas: HTMLCanvasElement, quality: number) {
  return new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error("Não foi possível preparar a imagem.")), "image/jpeg", quality));
}

function scaled(source: CanvasImageSource, width: number, height: number, side: number) {
  const scale = Math.min(1, side / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("O navegador não permite preparar a imagem.");
  context.drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas;
}

/** Fotografia → JPEG sem metadados (orientação já aplicada pelo navegador), só em memória. */
async function normalise(source: CanvasImageSource, width: number, height: number) {
  const full = scaled(source, width, height, MAX_SIDE), small = scaled(source, width, height, SMALL_SIDE);
  const [fullBlob, smallBlob, corners] = await Promise.all([toBlob(full, 0.9), toBlob(small, 0.85), findDocument(small)]);
  full.width = full.height = 0;
  return { full: fullBlob, small: smallBlob, corners };
}

async function fromFile(file: File) {
  if (!file.type.startsWith("image/") && !/\.(jpe?g|png|webp|heic|heif)$/i.test(file.name)) throw new Error("Escolha uma fotografia (JPG, PNG, WebP ou HEIC).");
  if (file.size > 40 * 1024 * 1024) throw new Error("A fotografia ultrapassa 40 MB.");
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode().catch(() => { throw new Error("Não foi possível abrir esta imagem. Em iPhone, escolha o formato «Mais compatível» (JPEG) nas definições da câmara."); });
    return await normalise(image, image.naturalWidth, image.naturalHeight);
  } finally { URL.revokeObjectURL(url); }
}

function cameraHelp(error: unknown) {
  const name = (error as DOMException)?.name;
  const apple = /iPhone|iPad|iPod/.test(navigator.userAgent);
  if (name === "NotAllowedError" || name === "SecurityError")
    return apple
      ? "A câmara foi recusada. No iPhone: toque em «aA» na barra de endereço › Definições do site › Câmara › Permitir (ou Definições › Safari › Câmara). Depois toque em «Tentar novamente». Pode também escolher fotografias da galeria."
      : "A câmara foi recusada. No Android (Chrome): toque no cadeado junto ao endereço › Permissões › Câmara › Permitir e recarregue a página. Pode também escolher fotografias da galeria.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "Não foi encontrada nenhuma câmara. Use a galeria para escolher fotografias.";
  if (name === "NotReadableError") return "A câmara está a ser usada por outra aplicação. Feche-a e tente novamente.";
  return "Não foi possível abrir a câmara. Use «Fotografar» com a aplicação da câmara ou escolha da galeria.";
}

export default function QuickScan({ onExit }: { onExit: () => void }) {
  const [step, setStep] = useState<Step>("loading");
  const [device, setDevice] = useState<Row | null>(null), [context, setContext] = useState<Row | null>(null);
  const [session, setSession] = useState(""), [locked, setLocked] = useState("");
  const [pin, setPin] = useState(""), [code, setCode] = useState("");
  const [pages, setPages] = useState<Page[]>([]), [current, setCurrent] = useState<string | null>(null), [draft, setDraft] = useState<Edit | null>(null);
  const [type, setType] = useState("fatura"), [description, setDescription] = useState(""), [name, setName] = useState(""), [nameEdited, setNameEdited] = useState(false);
  const [scan, setScan] = useState<Row | null>(null), [pdfURL, setPdfURL] = useState(""), [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(""), [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [cameraError, setCameraError] = useState(""), [cameraReady, setCameraReady] = useState(false), [cameraAttempt, setCameraAttempt] = useState(0);
  const video = useRef<HTMLVideoElement>(null), stream = useRef<MediaStream | null>(null), urls = useRef(new Set<string>());
  const gallery = useRef<HTMLInputElement>(null), nativeCamera = useRef<HTMLInputElement>(null), replacing = useRef<string | null>(null);
  const activity = useRef(Date.now()), editor = useRef<HTMLDivElement>(null), dragging = useRef<number | null>(null);
  const page = pages.find(p => p.id === current) || null;
  const maxPages = context?.max_pages || 20;

  function remember(blob: Blob) { const url = URL.createObjectURL(blob); urls.current.add(url); return url; }
  function forget(url: string) { if (url && urls.current.delete(url)) URL.revokeObjectURL(url); }
  function stopCamera() { stream.current?.getTracks().forEach(track => track.stop()); stream.current = null; setCameraReady(false); }
  function clearDocument() {
    pages.forEach(p => { forget(p.source); forget(p.preview); });
    forget(pdfURL);
    setPages([]); setCurrent(null); setDraft(null); setScan(null); setPdfURL(""); setRequestKey(crypto.randomUUID());
    setType("fatura"); setDescription(""); setName(""); setNameEdited(false); setError(""); setProgress(null);
  }

  /** Sessão terminada (inatividade, revogação): pede o PIN de novo, mantendo as páginas em memória. */
  function sessionEnded(message: string) {
    setSession(""); setLocked(message); setPin(""); stopCamera();
  }

  async function run<T>(label: string, task: () => Promise<T>) {
    setBusy(label); setError("");
    try { return await task(); }
    catch (problem) {
      if (problem instanceof QuickError && problem.status === 401 && session) sessionEnded(problem.message);
      else setError((problem as Error).message);
      return undefined;
    } finally { setBusy(""); setProgress(null); }
  }

  // Emparelhamento por link (?digitalizar=código) ou dispositivo já emparelhado.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const pairing = params.get("digitalizar") || "";
    if (pairing) {
      params.delete("digitalizar");
      window.history.replaceState(null, "", window.location.pathname + (params.toString() ? "?" + params : ""));
    }
    void (async () => {
      if (pairing.startsWith("p1.")) {
        try {
          const result = await quick("/pair", { pairing });
          deviceToken.set(result.device_token);
          setNotice("Dispositivo emparelhado. A partir de agora basta o PIN.");
        } catch (problem) { setError((problem as Error).message); }
      }
      const token = deviceToken.get();
      if (!token) { setStep("pair"); return; }
      try { setDevice(await quick("/device", { device: token })); setStep("pin"); }
      catch (problem) {
        if (problem instanceof QuickError && problem.status === 401) deviceToken.clear();
        setError((problem as Error).message); setStep("pair");
      }
    })();
    return () => { stopCamera(); urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current.clear(); };
  }, []);

  // Inatividade: o servidor termina a sessão; aqui pede-se o PIN logo que o prazo passa.
  useEffect(() => {
    if (!session || !context) return;
    const touch = () => { activity.current = Date.now(); };
    const events = ["pointerdown", "keydown", "touchstart"] as const;
    events.forEach(e => window.addEventListener(e, touch, { passive: true }));
    const timer = window.setInterval(() => {
      if (Date.now() - activity.current > context.idle_seconds * 1000 && !busy) sessionEnded("A sessão terminou por inatividade. Introduza o PIN para continuar.");
    }, 10_000);
    return () => { events.forEach(e => window.removeEventListener(e, touch)); clearInterval(timer); };
  }, [session, context, busy]);

  useEffect(() => {
    const unsaved = (pages.length > 0 || scan) && step !== "done";
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    if (unsaved) window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pages.length, scan, step]);

  // Câmara traseira preferida, apenas no passo de captura e com sessão ativa.
  useEffect(() => {
    if (step !== "capture" || !session) { stopCamera(); return; }
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setCameraError("A câmara em direto exige ligação segura (HTTPS). Use «Fotografar» (aplicação da câmara) ou a galeria.");
      return;
    }
    let cancelled = false;
    setCameraError("");
    navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } } })
      .then(media => {
        if (cancelled) { media.getTracks().forEach(t => t.stop()); return; }
        stream.current = media;
        if (video.current) { video.current.srcObject = media; void video.current.play().catch(() => undefined); }
      })
      .catch(problem => { if (!cancelled) setCameraError(cameraHelp(problem)); });
    return () => { cancelled = true; stopCamera(); };
  }, [step, session, cameraAttempt]);

  useEffect(() => {
    if (nameEdited || !scan) return;
    setName(`${context?.today || ""}_${type}_${String(scan.id).slice(0, 8)}`);
  }, [type, scan, nameEdited, context]);

  async function enter() {
    const token = deviceToken.get();
    await run("A validar o PIN…", async () => {
      try {
        const result = await quick("/session", { device: token, body: { pin } });
        const info = await quick("/context", { session: result.token });
        activity.current = Date.now();
        setSession(result.token); setContext(info); setLocked(""); setPin(""); setNotice("");
        if (step === "pin") setStep("capture");
      } catch (problem) {
        setPin("");
        if (problem instanceof QuickError && problem.status === 401 && /autorizado|revogado|desativado/.test(problem.message)) { deviceToken.clear(); setDevice(null); setStep("pair"); }
        throw problem;
      }
    });
  }

  function addPage(result: { full: Blob; small: Blob; corners: Corners | null }) {
    const edit: Edit = { corners: result.corners || FULL_FRAME, rotation: 0, colour: "enhanced" };
    const source = remember(result.small);
    const target = replacing.current;
    const id = target || crypto.randomUUID();
    setPages(list => {
      if (target) return list.map(p => { if (p.id !== target) return p; forget(p.source); forget(p.preview); return { ...p, full: result.full, small: result.small, source, preview: "", edit, detected: !!result.corners }; });
      return [...list, { id, full: result.full, small: result.small, source, preview: "", edit, detected: !!result.corners }];
    });
    replacing.current = null;
    invalidate();
    setCurrent(id); setDraft(edit); setStep("edit");
  }

  function invalidate() {
    // Alterar páginas depois de gerar exige um novo PDF (e uma nova referência de envio).
    if (scan && !scan.cloud_saved) { forget(pdfURL); setScan(null); setPdfURL(""); setRequestKey(crypto.randomUUID()); }
  }

  async function capture() {
    const element = video.current;
    if (!element || !element.videoWidth) return;
    await run("A preparar a fotografia…", async () => addPage(await normalise(element, element.videoWidth, element.videoHeight)));
  }

  async function chooseFiles(files: FileList | null) {
    if (!files?.length) return;
    const list = Array.from(files).slice(0, replacing.current ? 1 : Math.max(0, maxPages - pages.length));
    if (!list.length) { setError(`Máximo de ${maxPages} páginas por documento.`); return; }
    await run("A preparar as fotografias…", async () => {
      for (const [index, file] of list.entries()) {
        const result = await fromFile(file);
        if (index === list.length - 1) addPage(result);
        else {
          const source = remember(result.small);
          setPages(items => [...items, { id: crypto.randomUUID(), full: result.full, small: result.small, source, preview: "", edit: { corners: result.corners || FULL_FRAME, rotation: 0, colour: "enhanced" }, detected: !!result.corners }]);
        }
      }
      if (list.length > 1) setNotice(`${list.length} fotografias adicionadas. Reveja o recorte de cada página.`);
    });
  }

  async function preview(item: Page, edit: Edit) {
    const form = new FormData();
    form.append("edit", JSON.stringify({ ...edit, quality: 85 }));
    form.append("file", item.small, "pagina.jpg");
    return remember(await quick("/preview", { session, form, blob: true }));
  }

  async function applyEdit(next: "capture" | "review") {
    if (!page || !draft) return;
    if (!validCorners(draft.corners)) { setError("Os cantos têm de formar um quadrilátero sem cruzamentos. Ajuste-os antes de continuar."); return; }
    await run("A aplicar o recorte…", async () => {
      const url = await preview(page, draft);
      setPages(list => list.map(p => { if (p.id !== page.id) return p; forget(p.preview); return { ...p, edit: draft, preview: url }; }));
      invalidate();
      setCurrent(null); setDraft(null); setStep(next === "capture" && pages.length < maxPages ? "capture" : "review");
    });
  }

  function removePage(id: string) {
    setPages(list => { const item = list.find(p => p.id === id); if (item) { forget(item.source); forget(item.preview); } return list.filter(p => p.id !== id); });
    invalidate();
  }

  function move(id: string, delta: number) {
    setPages(list => { const index = list.findIndex(p => p.id === id), target = index + delta; if (target < 0 || target >= list.length) return list; const copy = [...list]; [copy[index], copy[target]] = [copy[target], copy[index]]; return copy; });
    invalidate();
  }

  async function rotate(item: Page) {
    const edit = { ...item.edit, rotation: ((item.edit.rotation + 90) % 360) as Edit["rotation"] };
    await run("A rodar…", async () => {
      const url = await preview(item, edit);
      setPages(list => list.map(p => { if (p.id !== item.id) return p; forget(p.preview); return { ...p, edit, preview: url }; }));
      invalidate();
    });
  }

  async function generate() {
    await run("A gerar o PDF…", async () => {
      const form = new FormData();
      form.append("payload", JSON.stringify({ request_key: requestKey, edits: pages.map(p => ({ ...p.edit, quality: 85 })) }));
      pages.forEach((p, i) => form.append("files", p.full, `pagina-${i + 1}.jpg`));
      setProgress(0);
      const result = await sendWithProgress("/prepare", form, session, setProgress);
      setProgress(null); setBusy("A preparar a pré-visualização…");
      const blob = await quick(`/scans/${result.id}/pdf`, { session, blob: true });
      forget(pdfURL);
      setScan(result); setPdfURL(remember(blob)); setStep("pdf");
    });
  }

  async function save() {
    if (!scan || busy) return;
    await run("A guardar na cloud e a aguardar confirmação…", async () => {
      const result = await quick(`/scans/${scan.id}/upload`, { session, body: { document_type: type, name: slug(name), description } });
      setScan(result);
      if (result.cloud_saved) {
        // As fotografias deixam de ser necessárias: libertar a memória do dispositivo.
        pages.forEach(p => { forget(p.source); forget(p.preview); });
        setPages([]); setStep("done");
      }
    });
  }

  async function finish() {
    if ((pages.length || (scan && !scan.cloud_saved)) && !window.confirm("Terminar sem guardar? As páginas digitalizadas serão apagadas deste dispositivo.")) return;
    stopCamera();
    if (session) await quick("/session/end", { session, body: {} }).catch(() => undefined);
    clearDocument(); setSession(""); setContext(null);
    urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current.clear();
    onExit();
  }

  function another() { clearDocument(); setNotice(""); setStep("capture"); }

  // Editor de cantos: arrastar com o dedo/rato ou mover com as setas do teclado.
  function pointAt(event: ReactPointerEvent) {
    const box = editor.current?.getBoundingClientRect();
    if (!box) return null;
    return [Math.min(1, Math.max(0, (event.clientX - box.left) / box.width)), Math.min(1, Math.max(0, (event.clientY - box.top) / box.height))] as [number, number];
  }
  function moveCorner(index: number, point: [number, number]) {
    setDraft(edit => edit && ({ ...edit, corners: edit.corners.map((c, i) => i === index ? point : c) as Corners }));
  }
  function keyCorner(index: number, event: ReactKeyboardEvent) {
    const step = event.shiftKey ? 0.05 : 0.01, deltas: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const delta = deltas[event.key];
    if (!delta || !draft) return;
    event.preventDefault();
    const [x, y] = draft.corners[index];
    moveCorner(index, [Math.min(1, Math.max(0, x + delta[0])), Math.min(1, Math.max(0, y + delta[1]))]);
  }

  const header = <header className="qs-header">
    <div className="qs-brand"><Brand /><span>Digitalização rápida</span></div>
    {device && <p className="qs-where">{device.company} · {device.store}</p>}
    <button type="button" className="ui-action qs-exit" onClick={() => void finish()}><LogOut size={18} aria-hidden="true" />Terminar</button>
  </header>;
  const messages = <>
    {error && <div className="ui-notice ui-notice-error qs-message" role="alert">{error}</div>}
    {notice && !error && <div className="ui-notice ui-notice-info qs-message" role="status">{notice}</div>}
    {busy && <div className="qs-busy" role="status" aria-live="polite"><span className="qs-spinner" aria-hidden="true" />{busy}{progress !== null && <progress max={1} value={progress} aria-label="Progresso do envio" />}</div>}
  </>;

  if (step === "loading") return <div className="qs"><main className="qs-main qs-center" role="status">A preparar a digitalização…</main></div>;

  if (step === "pair") return <div className="qs">{header}<main className="qs-main qs-narrow">
    <h1><ScanLine aria-hidden="true" /> Emparelhar este dispositivo</h1>
    {messages}
    <p>A digitalização rápida só funciona em dispositivos autorizados pelo administrador da empresa.</p>
    <ol className="qs-steps">
      <li>Na IAZELA, o administrador abre <b>Configurações › Parâmetros › Digitalização › Acessos rápidos</b>.</li>
      <li>Cria um acesso (ou pede um novo emparelhamento) e mostra o QR code.</li>
      <li>Leia o QR code com a câmara deste dispositivo, ou cole aqui o código de emparelhamento.</li>
    </ol>
    <form className="qs-form" onSubmit={event => { event.preventDefault(); void run("A emparelhar…", async () => {
      const value = code.includes("digitalizar=") ? new URL(code.trim()).searchParams.get("digitalizar") || "" : code.trim();
      const result = await quick("/pair", { pairing: value });
      deviceToken.set(result.device_token); setDevice(result.device); setCode(""); setNotice("Dispositivo emparelhado."); setStep("pin");
    }); }}>
      <label htmlFor="qs-code">Código ou link de emparelhamento</label>
      <input id="qs-code" value={code} onChange={e => setCode(e.target.value)} autoComplete="off" autoCapitalize="off" spellCheck={false} required />
      <button className="ui-action primary" disabled={!!busy || code.trim().length < 20}>Emparelhar</button>
    </form>
  </main></div>;

  if (step === "pin" || !session) return <div className="qs">{header}<main className="qs-main qs-narrow">
    <h1>Introduza o PIN</h1>
    {device && <p><b>{device.name}</b> · guarda em «{device.folder}»</p>}
    {locked && <div className="ui-notice ui-notice-warning qs-message" role="status">{locked}{pages.length > 0 && <> As {pages.length} página(s) continuam neste ecrã.</>}</div>}
    {messages}
    <form className="qs-form" onSubmit={event => { event.preventDefault(); void enter(); }}>
      <label htmlFor="qs-pin">PIN (6 a 12 algarismos)</label>
      <input id="qs-pin" className="qs-pin" type="password" inputMode="numeric" pattern="[0-9]{6,12}" minLength={6} maxLength={12} autoComplete="off" value={pin} onChange={e => setPin(e.target.value.replace(/\D/g, ""))} required autoFocus />
      <button className="ui-action primary" disabled={!!busy || pin.length < 6}>Continuar</button>
    </form>
    <p className="qs-small">O acesso rápido só permite digitalizar e guardar documentos. Não mostra dados da empresa nem documentos anteriores.</p>
  </main></div>;

  if (step === "edit" && page && draft) {
    const points = draft.corners.map(([x, y]) => `${x * 1000},${y * 1000}`).join(" ");
    return <div className="qs">{header}<main className="qs-main">
      <h1><Crop aria-hidden="true" /> Rever o recorte · página {pages.findIndex(p => p.id === page.id) + 1}</h1>
      {messages}
      <p className="qs-small">{page.detected ? "Margens detetadas automaticamente. Confirme ou arraste os quatro cantos." : "Não foi possível detetar as margens. Arraste os quatro cantos até às pontas do documento."}</p>
      <div className="qs-editor" ref={editor}
        onPointerMove={event => { if (dragging.current !== null) { const point = pointAt(event); if (point) moveCorner(dragging.current, point); } }}
        onPointerUp={() => { dragging.current = null; }} onPointerCancel={() => { dragging.current = null; }}>
        <img src={page.source} alt="Fotografia original da página" draggable={false} />
        <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">
          <polygon points={points} className={validCorners(draft.corners) ? "" : "invalid"} />
        </svg>
        {draft.corners.map(([x, y], index) => <button key={index} type="button" className="qs-handle" style={{ left: `${x * 100}%`, top: `${y * 100}%` }} role="slider"
          aria-label={["Canto superior esquerdo", "Canto superior direito", "Canto inferior direito", "Canto inferior esquerdo"][index]}
          aria-valuetext={`${Math.round(x * 100)}% horizontal, ${Math.round(y * 100)}% vertical`}
          onKeyDown={event => keyCorner(index, event)}
          onPointerDown={event => { event.preventDefault(); editor.current?.setPointerCapture?.(event.pointerId); dragging.current = index; }} />)}
      </div>
      <div className="qs-toolbar" role="group" aria-label="Modo de cor">
        {MODES.map(([value, label]) => <button key={value} type="button" className={`qs-chip ${draft.colour === value ? "active" : ""}`} aria-pressed={draft.colour === value} onClick={() => setDraft({ ...draft, colour: value })}>{label}</button>)}
      </div>
      <div className="qs-toolbar">
        <button type="button" className="ui-action" onClick={() => setDraft({ ...draft, rotation: ((draft.rotation + 90) % 360) as Edit["rotation"] })}><RotateCw size={18} aria-hidden="true" />Rodar{draft.rotation ? ` (${draft.rotation}°)` : ""}</button>
        <button type="button" className="ui-action" onClick={() => setDraft({ ...draft, corners: FULL_FRAME })}>Página inteira</button>
        <button type="button" className="ui-action" onClick={() => { replacing.current = page.id; setStep("capture"); }}><Camera size={18} aria-hidden="true" />Repetir fotografia</button>
        <button type="button" className="ui-action ui-action-danger" onClick={() => { removePage(page.id); setCurrent(null); setDraft(null); setStep(pages.length > 1 ? "review" : "capture"); }}><Trash2 size={18} aria-hidden="true" />Eliminar</button>
      </div>
      <footer className="qs-actions">
        {pages.length < maxPages && <button type="button" className="ui-action" disabled={!!busy} onClick={() => void applyEdit("capture")}><Plus size={18} aria-hidden="true" />Confirmar e fotografar outra</button>}
        <button type="button" className="ui-action primary" disabled={!!busy} onClick={() => void applyEdit("review")}>Confirmar e rever</button>
      </footer>
    </main></div>;
  }

  if (step === "capture") return <div className="qs qs-dark">{header}<main className="qs-main">
    {messages}
    <input ref={gallery} type="file" accept="image/*" multiple hidden onChange={e => { void chooseFiles(e.target.files); e.target.value = ""; }} />
    <input ref={nativeCamera} type="file" accept="image/*" capture="environment" hidden onChange={e => { void chooseFiles(e.target.files); e.target.value = ""; }} />
    {cameraError
      ? <div className="ui-notice ui-notice-warning qs-message" role="alert">{cameraError}</div>
      : <div className="qs-camera">
        <video ref={video} playsInline muted autoPlay onLoadedMetadata={() => setCameraReady(true)} aria-label="Imagem da câmara" />
        <div className="qs-frame" aria-hidden="true"><span>Enquadre o documento dentro da moldura, com boa luz e sem sombras</span></div>
      </div>}
    <p className="qs-small">{pages.length} de {maxPages} página(s){replacing.current ? " · a repetir uma página" : ""}</p>
    <footer className="qs-actions qs-capture-actions">
      <button type="button" className="ui-action" onClick={() => gallery.current?.click()} disabled={!!busy}><Images size={18} aria-hidden="true" />Galeria</button>
      {cameraError
        ? <>
          <button type="button" className="ui-action primary" onClick={() => nativeCamera.current?.click()} disabled={!!busy || pages.length >= maxPages}><Camera size={18} aria-hidden="true" />Fotografar</button>
          {/recusada/.test(cameraError) && <button type="button" className="ui-action" onClick={() => setCameraAttempt(n => n + 1)}><RefreshCw size={18} aria-hidden="true" />Tentar novamente</button>}
        </>
        : <button type="button" className="qs-shutter" aria-label="Capturar página" onClick={() => void capture()} disabled={!!busy || !cameraReady || (pages.length >= maxPages && !replacing.current)}><span /></button>}
      <button type="button" className="ui-action" disabled={!pages.length || !!busy} onClick={() => { replacing.current = null; setStep("review"); }}>Rever ({pages.length})</button>
    </footer>
  </main></div>;

  if (step === "review" || step === "pdf") return <div className="qs">{header}<main className="qs-main">
    <h1><FileText aria-hidden="true" /> {step === "review" ? "Rever páginas" : "Pré-visualizar e guardar"}</h1>
    {messages}
    {step === "review" && <>
      <ol className="qs-pages">
        {pages.map((item, index) => <li key={item.id}>
          <img src={item.preview || item.source} alt={`Página ${index + 1}`} />
          <span className="qs-page-number">{index + 1}</span>
          <div className="qs-page-tools">
            <button type="button" className="qs-icon" aria-label={`Subir página ${index + 1}`} disabled={!index} onClick={() => move(item.id, -1)}><ArrowUp size={18} /></button>
            <button type="button" className="qs-icon" aria-label={`Descer página ${index + 1}`} disabled={index === pages.length - 1} onClick={() => move(item.id, 1)}><ArrowDown size={18} /></button>
            <button type="button" className="qs-icon" aria-label={`Rodar página ${index + 1}`} onClick={() => void rotate(item)}><RotateCw size={18} /></button>
            <button type="button" className="qs-icon" aria-label={`Editar recorte da página ${index + 1}`} onClick={() => { setCurrent(item.id); setDraft(item.edit); setStep("edit"); }}><Crop size={18} /></button>
            <button type="button" className="qs-icon danger" aria-label={`Eliminar página ${index + 1}`} onClick={() => removePage(item.id)}><Trash2 size={18} /></button>
          </div>
        </li>)}
      </ol>
      {pages.length < maxPages && <button type="button" className="ui-action" onClick={() => setStep("capture")}><Plus size={18} aria-hidden="true" />Adicionar página</button>}
    </>}
    {step === "pdf" && scan && <section className="qs-summary">
      <p><b>{scan.pages}</b> página(s) · <b>{megabytes(scan.size)}</b> · destino «{scan.folder || context?.folder}»</p>
      {scan.warnings?.length > 0 && <ul className="qs-warnings">{scan.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
      <div className="qs-toolbar">
        <a className="ui-action" href={pdfURL} target="_blank" rel="noopener"><FileText size={18} aria-hidden="true" />Ver PDF</a>
        <a className="ui-action" href={pdfURL} download={(slug(name) || "documento") + ".pdf"}><Download size={18} aria-hidden="true" />Descarregar</a>
        {!scan.name_locked && <button type="button" className="ui-action" onClick={() => setStep("review")}>Voltar às páginas</button>}
      </div>
    </section>}
    <fieldset className="qs-types" disabled={!!scan?.name_locked}>
      <legend>Tipo de documento</legend>
      {(context?.types || []).map((t: Row) => <label key={t.id} className={type === t.id ? "active" : ""}><input type="radio" name="qs-type" value={t.id} checked={type === t.id} onChange={() => setType(t.id)} />{t.name}</label>)}
    </fieldset>
    <div className="qs-form">
      <label htmlFor="qs-description">Descrição (opcional)</label>
      <input id="qs-description" value={description} maxLength={500} onChange={e => setDescription(e.target.value)} disabled={!!scan?.name_locked} placeholder="Ex.: fatura da eletricidade de outubro" />
      {step === "pdf" && <>
        <label htmlFor="qs-name">Nome do ficheiro</label>
        <div className="qs-name"><input id="qs-name" value={name} maxLength={120} disabled={!!scan?.name_locked} onChange={e => { setName(e.target.value); setNameEdited(true); }} /><span>.pdf</span></div>
        {scan?.name_locked && <small>O envio já foi iniciado: o nome e o tipo ficam fixos para evitar duplicados.</small>}
      </>}
    </div>
    <footer className="qs-actions">
      {step === "review"
        ? <button type="button" className="ui-action primary" disabled={!pages.length || !!busy} onClick={() => void generate()}>Gerar PDF</button>
        : <button type="button" className="ui-action primary" disabled={!!busy || !scan} onClick={() => void save()}><CloudUpload size={18} aria-hidden="true" />{scan?.status === "upload_error" || error ? "Tentar novamente" : "Guardar na cloud"}</button>}
    </footer>
  </main></div>;

  return <div className="qs">{header}<main className="qs-main qs-narrow qs-done">
    <CheckCircle2 size={56} aria-hidden="true" className="qs-ok" />
    <h1>Documento guardado</h1>
    {scan && <p>«{scan.name}» foi guardado na pasta «{scan.folder || context?.folder}» e fica disponível para a conferência documental.</p>}
    <footer className="qs-actions">
      <button type="button" className="ui-action primary" onClick={another}><ScanLine size={18} aria-hidden="true" />Digitalizar outro documento</button>
      <button type="button" className="ui-action" onClick={() => void finish()}><LogOut size={18} aria-hidden="true" />Terminar</button>
    </footer>
  </main></div>;
}
