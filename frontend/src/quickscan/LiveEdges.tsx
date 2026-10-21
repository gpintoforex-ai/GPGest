import { useEffect, useRef, useState } from "react";
import type { MutableRefObject, RefObject } from "react";
import { analyse, detectDocument, type Corners, type DetectionReason } from "./edges";

/**
 * Deteção das margens em direto sobre a imagem da câmara (tipo CamScanner), toda no dispositivo.
 *
 * Analisa ≈8 imagens por segundo numa versão reduzida do vídeo (256 px), desenha o contorno
 * detetado, orienta o utilizador e, com a captura automática ligada, fotografa quando o documento
 * fica imóvel e inteiro na imagem durante ~1 segundo. Nada é guardado nem enviado nesta análise.
 */
export type LiveDetection = { corners: Corners; reason: DetectionReason; at: number } | null;

const INTERVAL = 120;   // ms entre análises (ajustado automaticamente em dispositivos lentos)
const HOLD = 1000;      // ms de estabilidade antes da captura automática
const STEADY = 0.02;    // deslocação máxima de um canto entre análises (fração da imagem)
const SNAP = 0.08;      // acima disto o contorno salta para a nova posição em vez de suavizar
const HINT_DELAY = 500; // ms que um novo estado tem de durar antes de mudar a indicação

const HINTS: Record<DetectionReason | "steady" | "manual" | "captured", string> = {
  none: "A procurar o documento… Use um fundo escuro e liso e boa luz.",
  small: "Aproxime o telemóvel do documento.",
  touching: "Afaste um pouco: o documento tem de caber inteiro na imagem.",
  irregular: "Alinhe o documento e evite sombras ou reflexos.",
  contrast: "Pouco contraste: afaste o telemóvel até ver as margens do papel e use um fundo escuro.",
  ok: "Documento detetado. Mantenha o telemóvel imóvel…",
  manual: "Documento detetado. Toque no botão para fotografar.",
  steady: "A fotografar…",
  captured: "Página fotografada. Vire a página ou coloque o documento seguinte.",
};

// Melhorias da deteção aparecem logo; pioras só se durarem (evita a indicação a piscar).
const RANK: Record<keyof typeof HINTS, number> = { none: 0, contrast: 1, irregular: 1, small: 1, touching: 1, ok: 2, manual: 2, captured: 2, steady: 3 };
const REARM = 0.12; // mudança mínima (fração da imagem) para voltar a fotografar automaticamente
const distance = (a: Corners, b: Corners) => Math.max(...a.map((p, i) => Math.hypot(p[0] - b[i][0], p[1] - b[i][1])));
const blend = (a: Corners, b: Corners, weight: number) =>
  a.map((p, i) => [p[0] + (b[i][0] - p[0]) * weight, p[1] + (b[i][1] - p[1]) * weight]) as Corners;

export default function LiveEdges({ video, active, autoCapture, onCapture, latest, captured = 0 }: {
  video: RefObject<HTMLVideoElement | null>;
  active: boolean;
  autoCapture: boolean;
  onCapture: (corners: Corners) => void;
  latest: MutableRefObject<LiveDetection>;
  /** Contador de fotografias (automáticas ou manuais): cada novo valor exige mudar de documento antes de nova captura automática. */
  captured?: number;
}) {
  const [state, setState] = useState<{ corners: Corners | null; reason: DetectionReason; progress: number; size: [number, number] }>(
    { corners: null, reason: "none", progress: 0, size: [0, 0] });
  const [hint, setHint] = useState<keyof typeof HINTS>("none");
  const capture = useRef(onCapture), automatic = useRef(autoCapture), shots = useRef(captured);
  capture.current = onCapture;
  automatic.current = autoCapture;
  shots.current = captured;

  useEffect(() => {
    latest.current = null;
    if (!active) { setState(s => ({ ...s, corners: null, progress: 0 })); return; }
    // fired: já fotografado este documento; só volta a disparar quando o documento muda ou sai da imagem.
    let frame = 0, last = 0, interval = INTERVAL, steadySince = 0, misses = 0, fired = false, firedAt: Corners | null = null, lost = 0, seenShots = shots.current;
    let shown: Corners | null = null, previous: Corners | null = null;
    let candidate: keyof typeof HINTS = "none", candidateSince = 0, current: keyof typeof HINTS = "none";
    const canvas = document.createElement("canvas");
    const tick = (time: number) => {
      frame = requestAnimationFrame(tick);
      if (time - last < interval) return;
      last = time;
      const element = video.current;
      if (!element || !element.videoWidth || element.readyState < 2 || document.hidden) return;
      const started = performance.now();
      const pixels = analyse(element, element.videoWidth, element.videoHeight, canvas, 256);
      if (!pixels) return;
      const result = detectDocument(pixels);
      // Dispositivos lentos: menos análises por segundo em vez de prender a interface.
      interval = Math.min(400, Math.max(INTERVAL, (performance.now() - started) * 3));
      if (shots.current !== seenShots) { seenShots = shots.current; fired = true; firedAt = shown || result.corners; lost = 0; steadySince = 0; }
      if (fired) {
        if (!result.corners || result.reason === "none") { if (++lost >= 3) { fired = false; firedAt = null; } }
        else { lost = 0; if (!firedAt || distance(firedAt, result.corners) > REARM) { fired = false; firedAt = null; } }
      }
      if (result.corners) {
        misses = 0;
        const movement = previous ? distance(previous, result.corners) : 1;
        previous = result.corners;
        shown = shown && distance(shown, result.corners) < SNAP ? blend(shown, result.corners, 0.5) : result.corners;
        steadySince = result.reason === "ok" && movement < STEADY ? steadySince || time : 0;
      } else {
        previous = null; steadySince = 0;
        if (++misses > 3) shown = null; // mantém o último contorno por instantes, sem piscar
      }
      const progress = steadySince && automatic.current && !fired ? Math.min(1, (time - steadySince) / HOLD) : 0;
      latest.current = shown ? { corners: shown, reason: result.reason, at: Date.now() } : null;
      const next: keyof typeof HINTS = progress > 0.3 ? "steady" : fired && result.corners ? "captured" : result.reason === "ok" && !automatic.current ? "manual" : result.reason;
      if (next !== candidate) { candidate = next; candidateSince = time; }
      if (next !== current && (RANK[next] >= RANK[current] || time - candidateSince >= HINT_DELAY)) { current = next; setHint(next); }
      setState({ corners: shown, reason: result.corners ? result.reason : "none", progress, size: [element.videoWidth, element.videoHeight] });
      if (progress >= 1 && shown && !fired) {
        fired = true; firedAt = shown; steadySince = 0;
        capture.current(shown);
      }
    };
    frame = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(frame); latest.current = null; };
  }, [active, video, latest]);

  const [width, height] = state.size;
  const radius = Math.max(width, height) * 0.012;
  return <>
    {active && width > 0 && <svg className="qs-live" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      {state.corners && <polygon className={`qs-live-quad qs-live-${state.reason}${state.progress > 0 ? " steady" : ""}`}
        points={state.corners.map(([x, y]) => `${x * width},${y * height}`).join(" ")} />}
      {state.corners && state.corners.map(([x, y], index) => <circle key={index} className={`qs-live-corner qs-live-${state.reason}`} cx={x * width} cy={y * height} r={radius} />)}
    </svg>}
    {active && <p className={`qs-live-hint qs-live-hint-${hint}`} role="status" aria-live="polite">{HINTS[hint]}</p>}
    {active && autoCapture && state.progress > 0 && <div className="qs-live-progress" aria-hidden="true"><span style={{ width: `${Math.round(state.progress * 100)}%` }} /></div>}
  </>;
}
