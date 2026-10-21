// Testa a deteção de margens (frontend/src/quickscan/edges.ts) com cenas sintéticas, sem navegador.
// Uso: node scripts/test-edge-detection.mjs
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { makeScene, cornerError } from "./document-scenes.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "frontend", "package.json"));
const esbuild = require("esbuild");
const bundle = await esbuild.build({ entryPoints: [path.join(root, "frontend/src/quickscan/edges.ts")], bundle: true, write: false, format: "esm", platform: "neutral", logLevel: "silent" });
const { detectDocument, detectCorners, validCorners } = await import("data:text/javascript;base64," + Buffer.from(bundle.outputFiles[0].text).toString("base64"));

let seed = 7;
const random = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
const inside = ([x, y], polygon) => {
  let result = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i], [xj, yj] = polygon[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) result = !result;
  }
  return result;
};
const segmentDistance = ([px, py], [ax, ay], [bx, by]) => {
  const dx = bx - ax, dy = by - ay, t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - ax - t * dx, py - ay - t * dy);
};
/**
 * Cena: fundo, papel (quadrilátero em píxeis), texto (com margem, como num documento real), ruído, luz
 * desigual, sombra suave à volta do papel (o caso típico de branco sobre branco) e objetos escuros.
 */
function scene({ width = 320, height = 240, background = [62, 44, 30], paper = [244, 241, 234], quad, text = false, noise = 0, gradient = false,
  light = null, shadow = 0, shadowWidth = 6, objects = [], textMargin = 10 }) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const point = [x + 0.5, y + 0.5];
    const onPaper = quad && inside(point, quad);
    let color = onPaper ? paper : background;
    if (onPaper && text && y % 9 < 2 && (x * 7 + y * 3) % 23 > 5
      && Math.min(...quad.map((a, i) => segmentDistance(point, a, quad[(i + 1) % quad.length]))) > textMargin) color = [40, 40, 45];
    for (const object of objects) if (inside(point, object.quad)) color = object.color;
    let shade = gradient ? 0.75 + 0.25 * (x / width) : 1;
    if (light) shade *= light[0] + (light[1] - light[0]) * (x / width + y / height) / 2;
    if (quad && shadow && !onPaper) {
      const distance = Math.min(...quad.map((a, i) => segmentDistance(point, a, quad[(i + 1) % quad.length])));
      if (distance < shadowWidth) shade *= 1 - shadow * (1 - distance / shadowWidth);
    }
    for (let c = 0; c < 3; c++) data[(y * width + x) * 4 + c] = color[c] * shade + (noise ? (random() - 0.5) * 2 * noise : 0);
    data[(y * width + x) * 4 + 3] = 255;
  }
  return { width, height, data };
}
const rotated = (cx, cy, w, h, degrees) => {
  const a = degrees * Math.PI / 180, cos = Math.cos(a), sin = Math.sin(a);
  return [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]].map(([x, y]) => [cx + x * cos - y * sin, cy + x * sin + y * cos]);
};
/** Erro máximo entre cantos, aceitando qualquer canto inicial (mantendo o sentido dos ponteiros). */
function error(found, truth, width, height) {
  const normal = truth.map(([x, y]) => [x / (width - 1), y / (height - 1)]);
  let best = Infinity;
  for (let shift = 0; shift < 4; shift++) best = Math.min(best, Math.max(...found.map((p, i) => Math.hypot(p[0] - normal[(i + shift) % 4][0], p[1] - normal[(i + shift) % 4][1]))));
  return best;
}

const failures = [];
function check(name, condition, detail = "") {
  console.log(`${condition ? "OK  " : "FALHA"} ${name}${detail ? " — " + detail : ""}`);
  if (!condition) failures.push(name);
}
function expectDocument(name, options, tolerance = 0.025) {
  const image = scene(options);
  const result = detectDocument(image);
  const deviation = result.corners ? error(result.corners, options.quad, image.width, image.height) : Infinity;
  check(name, result.reason === "ok" && validCorners(result.corners) && deviation <= tolerance, `motivo ${result.reason}, erro ${deviation === Infinity ? "—" : (deviation * 100).toFixed(1) + "%"}`);
}

expectDocument("Fatura rodada 12° sobre madeira", { background: [126, 84, 46], quad: rotated(160, 120, 150, 190, 12) });
expectDocument("Papel rodado 45°", { quad: rotated(160, 120, 140, 140, 45) });
expectDocument("Perspetiva (trapézio)", { quad: [[95, 40], [235, 52], [262, 214], [70, 205]] });
expectDocument("Documento com linhas de texto", { quad: rotated(160, 118, 170, 200, -6), text: true });
expectDocument("Ruído de sensor (±18)", { quad: rotated(160, 120, 160, 190, 3), noise: 18 });
expectDocument("Iluminação desigual", { quad: rotated(160, 120, 170, 190, 8), gradient: true });
expectDocument("Imagem vertical (retrato)", { width: 240, height: 320, quad: rotated(120, 160, 170, 230, 5) });

const full = detectDocument(scene({ quad: [[-20, -20], [340, -20], [340, 260], [-20, 260]] }));
check("Papel a ocupar a imagem toda → pouco contraste/afastar", full.reason === "contrast", `motivo ${full.reason}`);
const cut = detectDocument(scene({ quad: [[-30, 40], [180, 30], [190, 210], [-30, 200]] }));
check("Papel cortado na margem → afastar (com contorno)", cut.reason === "touching" && !!cut.corners, `motivo ${cut.reason}`);
const far = detectDocument(scene({ quad: rotated(160, 120, 60, 75, 4) }));
check("Documento longe → aproximar (com contorno)", far.reason === "small" && !!far.corners, `motivo ${far.reason}, área ${(far.area * 100).toFixed(1)}%`);
const empty = detectDocument(scene({}));
check("Sem documento", empty.reason === "none" && !empty.corners, `motivo ${empty.reason}`);
const flat = detectDocument(scene({ background: [228, 226, 222], quad: rotated(160, 120, 150, 190, 10) }));
const flatError = flat.corners ? error(flat.corners, rotated(160, 120, 150, 190, 10), 320, 240) : 0;
check("Papel branco em fundo branco: nunca um contorno errado", flat.reason !== "ok" || flatError <= 0.03, `motivo ${flat.reason}`);
// Branco sobre branco (o caso do CamScanner): mesa clara, papel um pouco mais claro e uma sombra suave.
const whiteTable = [229, 228, 224], whitePaper = [247, 246, 243];
expectDocument("Branco sobre branco com sombra suave", { background: whiteTable, paper: whitePaper, quad: rotated(160, 120, 160, 200, 7), shadow: 0.07, noise: 5, text: true });
expectDocument("Branco sobre branco quase igual (6 níveis, sem sombra)", { background: [238, 237, 234], paper: [244, 243, 240], quad: rotated(160, 118, 165, 205, -5), noise: 3 }, 0.03);
expectDocument("Luz desigual: mesa mais clara do que o papel num dos lados", { background: [236, 235, 232], paper: [242, 241, 238], quad: [[90, 38], [238, 50], [252, 212], [72, 200]], light: [0.82, 1.08], shadow: 0.05, noise: 4 }, 0.03);
expectDocument("Branco sobre branco com teclado escuro ao lado", { background: whiteTable, paper: whitePaper, quad: rotated(120, 120, 150, 190, 4), shadow: 0.06, noise: 4, text: true,
  objects: [{ quad: [[215, 30], [310, 34], [306, 210], [212, 206]], color: [35, 35, 38] }] });
expectDocument("Talão estreito em mesa branca", { background: whiteTable, paper: whitePaper, quad: rotated(160, 120, 70, 210, 3), shadow: 0.07, noise: 4, text: true }, 0.03);
const emptyWhite = detectDocument(scene({ background: whiteTable, light: [0.9, 1.05], noise: 5 }));
check("Mesa branca sem documento: nada é detetado", emptyWhite.reason !== "ok", `motivo ${emptyWhite.reason}`);

const shapeL = scene({ quad: [[60, 40], [260, 40], [260, 90], [120, 90], [120, 210], [60, 210]] });
const irregular = detectDocument(shapeL);
check("Forma irregular (em L) não é aceite como documento", irregular.reason !== "ok", `motivo ${irregular.reason}`);
check("Fotografia: documento longe ainda propõe cantos", !!detectCorners(scene({ quad: rotated(160, 120, 90, 110, 4) })));

// Banco de cenas realistas (folha branca em mesa branca, talões, perspetiva, sombras finas, objetos, folha
// cortada pela moldura, mesa vazia). O essencial: nunca um contorno «pronto» errado — a captura automática
// usaria esse recorte. Uma folha não detetada apenas obriga a tirar a fotografia à mão.
let good = 0, documents = 0;
const wrong = [];
for (let seed = 1; seed <= 64; seed++) {
  const overrides = seed % 8 === 0 ? { expect: "none" } : seed % 10 === 0 ? { expect: "touching" } : {};
  const s = makeScene(seed, overrides);
  if (!s) continue;
  const result = detectDocument(s);
  if (s.expect !== "ok") { if (result.reason === "ok") wrong.push(`${seed} (${s.expect})`); continue; }
  documents++;
  const deviation = result.corners ? cornerError(result.corners, s.quad, s.width, s.height) : Infinity;
  if (result.reason === "ok" && deviation <= 0.025) good++;
  else if (result.reason === "ok") wrong.push(`${seed} (erro ${(deviation * 100).toFixed(1)}%)`);
}
check("Cenas realistas: nenhum contorno errado", !wrong.length, wrong.join(", "));
check("Cenas realistas: deteta pelo menos 80% das folhas", good >= 0.8 * documents, `${good}/${documents}`);

// Desempenho na resolução usada em direto (256×192). Num computador sem carga são ≈12–16 ms; o limite é
// largo para não falhar com a máquina ocupada, mas apanha regressões grosseiras (análise 8 vezes por segundo).
const live = scene({ width: 256, height: 192, quad: rotated(128, 96, 120, 150, 9), text: true, noise: 10 });
detectDocument(live);
const started = performance.now();
for (let i = 0; i < 60; i++) detectDocument(live);
const average = (performance.now() - started) / 60;
check("Tempo médio por análise (256×192)", average < 40, `${average.toFixed(1)} ms`);

if (failures.length) { console.error(`\n${failures.length} falha(s): ${failures.join("; ")}`); process.exit(1); }
console.log("\nDeteção de margens: todos os cenários passaram.");
