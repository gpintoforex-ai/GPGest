import { useEffect, useRef, useState } from "react";
import type { MutableRefObject, RefObject } from "react";
import { findDocument, type Corners } from "./edges";
import LiveEdges, { type LiveDetection } from "./LiveEdges";
import "./LiveCamera.css";

/**
 * Câmara com deteção das margens em direto, partilhada pela digitalização rápida (PIN) e pela
 * digitalização da área autenticada. As imagens ficam só em memória; nada é guardado no dispositivo.
 */
const MAX_SIDE = 3000, SMALL_SIDE = 1200;
const BURST = 6, BURST_GAP = 70; // fotografia: a mais nítida de 6 imagens em ≈0,4 s (evita a imagem tremida)
const AUTO_KEY = "iazela_digitalizacao_auto";

/** A câmara em direto exige contexto seguro (HTTPS ou localhost) e suporte do navegador. */
export const liveCameraAvailable = () => typeof window !== "undefined" && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;

export function cameraHelp(error: unknown) {
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

/** Abre a câmara traseira enquanto `active`; fecha-a (e liberta o sensor) ao desativar ou desmontar. */
export function useCamera(active: boolean) {
  const video = useRef<HTMLVideoElement>(null);
  const [ready, setReady] = useState(false), [error, setError] = useState(""), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setReady(false);
    if (!active) return;
    if (!liveCameraAvailable()) {
      setError("A câmara em direto exige ligação segura (HTTPS). Use «Fotografar» (aplicação da câmara) ou a galeria.");
      return;
    }
    let cancelled = false, media: MediaStream | null = null;
    setError("");
    navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } } })
      .then(stream => {
        if (cancelled) { stream.getTracks().forEach(track => track.stop()); return; }
        media = stream;
        // Foco automático contínuo, quando o telemóvel o permite (Chrome em Android); ignorado nos restantes.
        const track = stream.getVideoTracks()[0];
        const modes = (track?.getCapabilities?.() as { focusMode?: unknown } | undefined)?.focusMode;
        if (Array.isArray(modes) && modes.includes("continuous")) void track.applyConstraints({ advanced: [{ focusMode: "continuous" } as MediaTrackConstraintSet] }).catch(() => undefined);
        if (video.current) { video.current.srcObject = stream; void video.current.play().catch(() => undefined); }
      })
      .catch(problem => { if (!cancelled) setError(cameraHelp(problem)); });
    return () => {
      cancelled = true;
      media?.getTracks().forEach(track => track.stop());
      if (video.current) video.current.srcObject = null;
      setReady(false);
    };
  }, [active, attempt]);
  return { video, ready, setReady, error, retry: () => setAttempt(value => value + 1) };
}
export type Camera = ReturnType<typeof useCamera>;

/** Preferência «captura automática», guardada só neste dispositivo (ligada por omissão). */
export function useAutoCapture() {
  const [enabled, setEnabled] = useState(() => { try { return localStorage.getItem(AUTO_KEY) !== "0"; } catch { return true; } });
  const toggle = () => setEnabled(value => {
    try { localStorage.setItem(AUTO_KEY, value ? "0" : "1"); } catch { /* preferência só nesta sessão */ }
    return !value;
  });
  return [enabled, toggle] as const;
}

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

/** Imagem → JPEG sem metadados (orientação já aplicada pelo navegador) + cantos detetados. Só em memória. */
export async function normalise(source: CanvasImageSource, width: number, height: number) {
  const full = scaled(source, width, height, MAX_SIDE), small = scaled(source, width, height, SMALL_SIDE);
  const [fullBlob, smallBlob, corners] = await Promise.all([toBlob(full, 0.9), toBlob(small, 0.85), findDocument(small)]);
  full.width = full.height = 0;
  return { full: fullBlob, small: smallBlob, corners };
}

/** Fotografia da galeria ou da aplicação da câmara → JPEG normalizado + cantos detetados. */
export async function fromFile(file: File) {
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

/** Cantos detetados numa fotografia já existente (sem a converter), ou `null` para ajuste manual. */
export async function detectInFile(file: Blob) {
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    return await findDocument(image);
  } catch {
    return null;
  } finally { URL.revokeObjectURL(url); }
}

/**
 * Nitidez de uma imagem reduzida: variância do laplaciano do brilho, só na zona do documento (quando
 * conhecida). Uma imagem tremida ou desfocada perde os contornos finos das letras e o valor desce.
 */
function sharpness(context: CanvasRenderingContext2D, width: number, height: number, corners: Corners | null) {
  const { data } = context.getImageData(0, 0, width, height);
  let x0 = 1, y0 = 1, x1 = width - 2, y1 = height - 2;
  if (corners) {
    const xs = corners.map(p => p[0] * width), ys = corners.map(p => p[1] * height);
    const insetX = (Math.max(...xs) - Math.min(...xs)) * 0.1, insetY = (Math.max(...ys) - Math.min(...ys)) * 0.1;
    x0 = Math.max(1, Math.round(Math.min(...xs) + insetX)); x1 = Math.min(width - 2, Math.round(Math.max(...xs) - insetX));
    y0 = Math.max(1, Math.round(Math.min(...ys) + insetY)); y1 = Math.min(height - 2, Math.round(Math.max(...ys) - insetY));
  }
  const lum = (x: number, y: number) => { const q = (y * width + x) * 4; return 0.299 * data[q] + 0.587 * data[q + 1] + 0.114 * data[q + 2]; };
  let sum = 0, squares = 0, count = 0;
  for (let y = y0; y <= y1; y += 2) {
    for (let x = x0; x <= x1; x += 2) {
      const value = 4 * lum(x, y) - lum(x - 1, y) - lum(x + 1, y) - lum(x, y - 1) - lum(x, y + 1);
      sum += value; squares += value * value; count++;
    }
  }
  return count ? squares / count - (sum / count) ** 2 : 0;
}

const nextFrame = (element: HTMLVideoElement) => new Promise<void>(resolve => {
  const video = element as HTMLVideoElement & { requestVideoFrameCallback?: (callback: () => void) => number };
  const timer = setTimeout(resolve, BURST_GAP * 2);
  const done = () => { clearTimeout(timer); setTimeout(resolve, BURST_GAP / 2); };
  if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(done); else setTimeout(done, BURST_GAP / 2);
});

/** A imagem mais nítida de uma rajada curta do vídeo, já copiada em resolução total. */
async function sharpestFrame(element: HTMLVideoElement, corners: Corners | null) {
  const width = element.videoWidth, height = element.videoHeight;
  const probe = scaled(element, width, height, 480), probeContext = probe.getContext("2d", { willReadFrequently: true });
  const best = document.createElement("canvas");
  best.width = width; best.height = height;
  const bestContext = best.getContext("2d");
  if (!probeContext || !bestContext) throw new Error("O navegador não permite preparar a imagem.");
  let top = -1;
  for (let shot = 0; shot < BURST; shot++) {
    if (shot) await nextFrame(element);
    probeContext.drawImage(element, 0, 0, probe.width, probe.height);
    const value = sharpness(probeContext, probe.width, probe.height, corners);
    // Copia logo a mesma imagem (sem esperar), para a versão completa corresponder à medida.
    if (value > top) { top = value; bestContext.drawImage(element, 0, 0, width, height); }
  }
  probe.width = probe.height = 0;
  return best;
}

/**
 * Fotografa a partir do vídeo: escolhe a imagem mais nítida de uma rajada curta (o toque no ecrã e a
 * mão fazem tremer a primeira) e usa os cantos vistos em direto (o que o utilizador viu).
 */
export async function captureFrame(camera: Camera, live: MutableRefObject<LiveDetection>, seen?: Corners) {
  const element = camera.video.current;
  if (!element || !element.videoWidth) throw new Error("A câmara ainda não está pronta.");
  const recent = live.current && Date.now() - live.current.at < 700 && live.current.reason !== "irregular" ? live.current.corners : null;
  const frame = await sharpestFrame(element, seen || recent);
  try {
    const result = await normalise(frame, frame.width, frame.height);
    return { ...result, corners: seen || recent || result.corners };
  } finally { frame.width = frame.height = 0; }
}

/** Efeito visual (e vibração curta, quando existe) no momento da fotografia. */
export function flash(box: HTMLElement | null) {
  box?.classList.remove("qs-flash");
  void box?.offsetWidth; // reinicia a animação
  box?.classList.add("qs-flash");
  try { navigator.vibrate?.(30); } catch { /* sem vibração neste dispositivo */ }
}

/** Vídeo da câmara com o contorno do documento desenhado em direto. */
export function CameraView({ camera, live, active, autoCapture, captured, onCapture, box }: {
  camera: Camera;
  live: MutableRefObject<LiveDetection>;
  active: boolean;
  autoCapture: boolean;
  captured?: number;
  onCapture: (corners: Corners) => void;
  box?: RefObject<HTMLDivElement | null>;
}) {
  return <div className="qs-camera" ref={box}>
    <video ref={camera.video} playsInline muted autoPlay onLoadedMetadata={() => camera.setReady(true)} aria-label="Imagem da câmara" />
    <LiveEdges video={camera.video} latest={live} autoCapture={autoCapture} captured={captured} active={active && camera.ready} onCapture={onCapture} />
  </div>;
}
