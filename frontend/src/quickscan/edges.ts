/**
 * Deteção local das margens de um documento (fotografias e imagem da câmara em direto), sem
 * serviços externos nem bibliotecas. Dois métodos, fica o melhor candidato:
 *
 * - Região clara: «brancura» (luminância menos saturação), limiar de Otsu, maior região ligada,
 *   envolvente convexa reduzida a 4 vértices. Rápido e exato quando o papel contrasta com o fundo.
 * - Arestas retas (papel branco sobre mesa branca): mapa de arestas com limiar relativo ao ruído e
 *   sem a luz de fundo, retas de Hough afinadas por mínimos quadrados, quadriláteros formados por
 *   pares de retas. Cada lado tem de coincidir com arestas reais ao longo do comprimento e junto dos
 *   dois cantos, e não pode continuar para lá deles: assim as linhas de texto, os blocos de texto e
 *   as bordas de outros objetos não passam por lados da folha.
 *
 * Devolve sempre o motivo, para orientar o utilizador em direto («aproxime», «afaste»…). Na dúvida
 * não deteta nada (a captura automática nunca usa um contorno errado); o recorte pode sempre ser ajustado.
 */
export type Point = [number, number];
export type Corners = [Point, Point, Point, Point]; // sup. esq., sup. dir., inf. dir., inf. esq.
/**
 * ok: pronto · small: demasiado longe · touching: corta a margem da imagem · irregular: forma pouco
 * fiável · contrast: imagem clara sem contraste (papel demasiado perto ou fundo branco) · none: nada encontrado
 */
export type DetectionReason = "ok" | "small" | "touching" | "irregular" | "contrast" | "none";
/** `score`: confiança (0–1) do quadrilátero escolhido; `method`: região clara ou arestas retas. */
export type Detection = { corners: Corners | null; reason: DetectionReason; area: number; score?: number; method?: "region" | "lines" | "" };
type Pixels = { width: number; height: number; data: Uint8ClampedArray | number[] };

export const FULL_FRAME: Corners = [[0.03, 0.03], [0.97, 0.03], [0.97, 0.97], [0.03, 0.97]];
const MIN_AREA = 0.12;      // fração da imagem para considerar o documento «perto o suficiente»
const MIN_STILL_AREA = 0.06; // em fotografias aceita-se um documento mais pequeno, para ajuste manual
const BORDER = 0.012;       // distância mínima à margem para considerar o documento inteiro

/** Quadrilátero convexo, sem cruzamentos, pela ordem dos ponteiros do relógio (mesma regra do servidor). */
export function validCorners(corners: Corners) {
  for (let i = 0; i < 4; i++) {
    const [a, b, c] = [corners[i], corners[(i + 1) % 4], corners[(i + 2) % 4]];
    const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    if (cross <= 0.01) return false;
  }
  return corners.every(([x, y]) => x >= 0 && x <= 1 && y >= 0 && y <= 1);
}

function polygonArea(points: Point[]) {
  let total = 0;
  for (let i = 0; i < points.length; i++) {
    const [x1, y1] = points[i], [x2, y2] = points[(i + 1) % points.length];
    total += x1 * y2 - x2 * y1;
  }
  return Math.abs(total) / 2;
}

/** Limiar de Otsu e separação entre as médias das duas classes (contraste disponível). */
function otsu(values: Float32Array, total: number) {
  const histogram = new Float64Array(256);
  for (let i = 0; i < total; i++) { const v = values[i]; histogram[v <= 0 ? 0 : v >= 255 ? 255 : (v + 0.5) | 0]++; }
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * histogram[i];
  let low = 0, weight = 0, best = -1, threshold = 127, separation = 0;
  for (let i = 0; i < 256; i++) {
    weight += histogram[i];
    if (!weight || weight === total) continue;
    low += i * histogram[i];
    const meanLow = low / weight, meanHigh = (sum - low) / (total - weight);
    const between = weight * (total - weight) * (meanLow - meanHigh) ** 2;
    if (between > best) { best = between; threshold = i; separation = meanHigh - meanLow; }
  }
  return { threshold, separation };
}

/** Envolvente convexa (cadeia monótona), pela ordem anti-horária em coordenadas cartesianas. */
function convexHull(points: Point[]) {
  if (points.length < 3) return points.slice();
  const sorted = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o: Point, a: Point, b: Point) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Point[] = [], upper: Point[] = [];
  for (const p of sorted) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  for (let i = sorted.length - 1; i >= 0; i--) {
    const p = sorted[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

/** Reduz um polígono convexo a 4 vértices, retirando sempre o que menos área acrescenta. */
function reduceToQuad(hull: Point[]) {
  const points = hull.slice();
  const triangle = (a: Point, b: Point, c: Point) => Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2;
  while (points.length > 4) {
    let index = 0, smallest = Infinity;
    for (let i = 0; i < points.length; i++) {
      const value = triangle(points[(i - 1 + points.length) % points.length], points[i], points[(i + 1) % points.length]);
      if (value < smallest) { smallest = value; index = i; }
    }
    points.splice(index, 1);
  }
  return points;
}

/**
 * Afina os cantos: ajusta uma reta a cada lado (pontos do contorno perto do lado, longe dos cantos,
 * por mínimos quadrados totais) e interseta lados consecutivos. As pontas arredondadas pela
 * desfocagem deixam de puxar os cantos para dentro. Devolve `null` quando não há pontos suficientes.
 */
function refine(corners: Point[], boundary: Point[]) {
  const lines: { point: Point; direction: Point }[] = [];
  for (let i = 0; i < 4; i++) {
    const a = corners[i], b = corners[(i + 1) % 4];
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (length < 8) return null;
    const ux = (b[0] - a[0]) / length, uy = (b[1] - a[1]) / length;
    let count = 0, mx = 0, my = 0;
    const near: Point[] = [];
    for (const p of boundary) {
      const px = p[0] - a[0], py = p[1] - a[1];
      const t = (px * ux + py * uy) / length;
      if (t < 0.12 || t > 0.88 || Math.abs(px * uy - py * ux) > 4) continue;
      near.push(p); count++; mx += p[0]; my += p[1];
    }
    if (count < 6) return null;
    mx /= count; my /= count;
    let sxx = 0, syy = 0, sxy = 0;
    for (const [x, y] of near) { sxx += (x - mx) ** 2; syy += (y - my) ** 2; sxy += (x - mx) * (y - my); }
    const angle = Math.atan2(2 * sxy, sxx - syy) / 2;
    lines.push({ point: [mx, my], direction: [Math.cos(angle), Math.sin(angle)] });
  }
  const result: Point[] = [];
  for (let i = 0; i < 4; i++) {
    const first = lines[(i + 3) % 4], second = lines[i];
    const cross = first.direction[0] * second.direction[1] - first.direction[1] * second.direction[0];
    if (Math.abs(cross) < 1e-3) return null;
    const qx = second.point[0] - first.point[0], qy = second.point[1] - first.point[1];
    const t = (qx * second.direction[1] - qy * second.direction[0]) / cross;
    const corner: Point = [first.point[0] + t * first.direction[0], first.point[1] + t * first.direction[1]];
    // Um ajuste que afasta muito o canto da aproximação inicial não é fiável.
    if (Math.hypot(corner[0] - corners[i][0], corner[1] - corners[i][1]) > 12) return null;
    result.push(corner);
  }
  return result;
}

/** Ordena os cantos: superior esquerdo primeiro e depois no sentido dos ponteiros do relógio. */
function ordered(points: Point[]): Corners {
  const cx = points.reduce((s, p) => s + p[0], 0) / points.length, cy = points.reduce((s, p) => s + p[1], 0) / points.length;
  const byAngle = points.slice().sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
  const start = byAngle.reduce((best, p, i) => p[0] + p[1] < byAngle[best][0] + byAngle[best][1] ? i : best, 0);
  return [0, 1, 2, 3].map(i => byAngle[(start + i) % 4]) as Corners;
}

function plausibleAngles(corners: Corners) {
  for (let i = 0; i < 4; i++) {
    const a = corners[(i + 3) % 4], b = corners[i], c = corners[(i + 1) % 4];
    const u = [a[0] - b[0], a[1] - b[1]], v = [c[0] - b[0], c[1] - b[1]];
    const cosine = (u[0] * v[0] + u[1] * v[1]) / (Math.hypot(u[0], u[1]) * Math.hypot(v[0], v[1]) || 1);
    const angle = Math.acos(Math.max(-1, Math.min(1, cosine))) * 180 / Math.PI;
    if (angle < 40 || angle > 140) return false;
  }
  return true;
}


// --- Deteção por arestas (branco sobre branco) ----------------------------------------------------
//
// Mesmo sem contraste de brilho, os lados da folha deixam arestas retas e longas (sombra fina de
// contacto, degrau de brilho ou de tom). Afinado com cenas sintéticas realistas (texto com margens,
// perspetiva, sombras, luz desigual, desfocagem e ruído): ver scripts/test-edge-detection.mjs.

// angle: normal da aresta (0–179) ou -1, por píxel.
type Edges = { width: number; height: number; angle: Int16Array; count: number; data: Pixels["data"] };
// Memória de trabalho reutilizada entre análises (≈8 por segundo em direto), para não sobrecarregar o coletor.
type Work = { total: number; a: Float32Array; b: Float32Array; c: Float32Array; d: Float32Array; e: Float32Array; f: Float32Array; tmp: Float32Array; angle: Int16Array; label: Int32Array; stack: Int32Array; mask: Uint8Array };
let work: Work | null = null;
function buffers(total: number): Work {
  if (!work || work.total !== total) {
    const f = () => new Float32Array(total);
    work = { total, a: f(), b: f(), c: f(), d: f(), e: f(), f: f(), tmp: f(), angle: new Int16Array(total), label: new Int32Array(total), stack: new Int32Array(total), mask: new Uint8Array(total) };
  }
  return work;
}

/** Duas médias 3×3 seguidas, numa só passagem por eixo (núcleo 1 2 3 2 1 / 9). `target` pode ser `source`. */
function blur5(source: Float32Array, target: Float32Array, tmp: Float32Array, width: number, height: number) {
  const ninth = 1 / 9;
  for (let y = 0; y < height; y++) {
    const row = y * width;
    for (let x = 0; x < width; x++) {
      const l2 = source[row + (x > 1 ? x - 2 : 0)], l1 = source[row + (x > 0 ? x - 1 : 0)];
      const r1 = source[row + (x < width - 1 ? x + 1 : width - 1)], r2 = source[row + (x < width - 2 ? x + 2 : width - 1)];
      tmp[row + x] = (l2 + 2 * l1 + 3 * source[row + x] + 2 * r1 + r2) * ninth;
    }
  }
  for (let y = 0; y < height; y++) {
    const u2 = (y > 1 ? y - 2 : 0) * width, u1 = (y > 0 ? y - 1 : 0) * width, row = y * width;
    const d1 = (y < height - 1 ? y + 1 : height - 1) * width, d2 = (y < height - 2 ? y + 2 : height - 1) * width;
    for (let x = 0; x < width; x++) target[row + x] = (tmp[u2 + x] + 2 * tmp[u1 + x] + 3 * tmp[row + x] + 2 * tmp[d1 + x] + tmp[d2 + x]) * ninth;
  }
}

/** Média 3×3 separável (as margens repetem o píxel da borda). `target` pode ser `source`. */
function blur3(source: Float32Array, target: Float32Array, tmp: Float32Array, width: number, height: number) {
  const third = 1 / 3;
  for (let y = 0; y < height; y++) {
    const row = y * width, last = row + width - 1;
    tmp[row] = (2 * source[row] + source[row + 1]) * third;
    for (let p = row + 1; p < last; p++) tmp[p] = (source[p - 1] + source[p] + source[p + 1]) * third;
    tmp[last] = (source[last - 1] + 2 * source[last]) * third;
  }
  for (let y = 0; y < height; y++) {
    const row = y * width, up = (y > 0 ? y - 1 : 0) * width, down = (y < height - 1 ? y + 1 : y) * width;
    for (let x = 0; x < width; x++) target[row + x] = (tmp[up + x] + tmp[row + x] + tmp[down + x]) * third;
  }
}

const SMOOTH = 1;   // gradiente máximo de um píxel «liso» (sem aresta nem texto) para estimar a luz de fundo
const BLOCK = 16;   // lado dos blocos onde se mede a luz de fundo

/**
 * Arestas finas (supressão de não-máximos) com o ângulo da normal (0–179) ou -1.
 *
 * - Brilho desfocado duas vezes e cor quente/fria (r − b) uma vez; Sobel em ambos, fica o mais forte.
 * - Luz de fundo: o gradiente médio dos píxeis lisos, em blocos de 16 px interpolados, é subtraído. Um
 *   gradiente de iluminação suave desvia 10–20° o ângulo das arestas ténues (branco sobre branco).
 * - Orientação pelo tensor de estrutura 3×3 (estável com ruído).
 * - Limiar relativo ao ruído da própria imagem (3 × mediana do gradiente).
 */
function edgeMap(image: Pixels): Edges {
  const { width, height, data } = image, total = width * height;
  const { a: lum, b: chroma, c: gradientX, d: gradientY, e: magnitude, tmp, angle } = buffers(total);
  for (let i = 0, q = 0; i < total; i++, q += 4) {
    const r = data[q], g = data[q + 1], b = data[q + 2];
    lum[i] = 0.299 * r + 0.587 * g + 0.114 * b;
    chroma[i] = r - b; // quente/frio: madeira, cartão e mesas coloridas face ao papel branco
  }
  blur5(lum, lum, tmp, width, height);
  blur3(chroma, chroma, tmp, width, height);
  // Sobel normalizado no brilho e na cor; fica o mais forte.
  const columns = Math.ceil(width / BLOCK), rows = Math.ceil(height / BLOCK);
  const sumX = new Float32Array(columns * rows), sumY = new Float32Array(columns * rows), counts = new Float32Array(columns * rows);
  magnitude.fill(0); gradientX.fill(0); gradientY.fill(0);
  for (let y = 1; y < height - 1; y++) {
    const blockRow = ((y / BLOCK) | 0) * columns;
    for (let x = 1; x < width - 1; x++) {
      const p = y * width + x, n = p - width, s = p + width;
      let gx = (lum[n + 1] + 2 * lum[p + 1] + lum[s + 1] - lum[n - 1] - 2 * lum[p - 1] - lum[s - 1]) * 0.125;
      let gy = (lum[s - 1] + 2 * lum[s] + lum[s + 1] - lum[n - 1] - 2 * lum[n] - lum[n + 1]) * 0.125;
      let best = gx * gx + gy * gy;
      const cx = (chroma[n + 1] + 2 * chroma[p + 1] + chroma[s + 1] - chroma[n - 1] - 2 * chroma[p - 1] - chroma[s - 1]) * 0.075;
      const cy = (chroma[s - 1] + 2 * chroma[s] + chroma[s + 1] - chroma[n - 1] - 2 * chroma[n] - chroma[n + 1]) * 0.075;
      const value = cx * cx + cy * cy;
      if (value > best) { best = value; gx = cx; gy = cy; }
      gradientX[p] = gx;
      gradientY[p] = gy;
      if (best < SMOOTH * SMOOTH) { const k = blockRow + ((x / BLOCK) | 0); sumX[k] += gx; sumY[k] += gy; counts[k]++; }
    }
  }
  // Luz de fundo: média por bloco, interpolada (bilinear) entre os centros dos blocos.
  for (let k = 0; k < counts.length; k++) if (counts[k] > 20) { sumX[k] /= counts[k]; sumY[k] /= counts[k]; } else { sumX[k] = 0; sumY[k] = 0; }
  const histogram = new Uint32Array(512);
  let samples = 0;
  const left = new Int32Array(width), right = new Int32Array(width), share = new Float32Array(width);
  for (let x = 0; x < width; x++) {
    const u = Math.min(columns - 1, Math.max(0, x / BLOCK - 0.5));
    left[x] = u | 0; right[x] = Math.min(columns - 1, left[x] + 1); share[x] = u - left[x];
  }
  for (let y = 1; y < height - 1; y++) {
    const v = Math.min(rows - 1, Math.max(0, y / BLOCK - 0.5)), r0 = (v | 0) * columns, r1 = Math.min(rows - 1, (v | 0) + 1) * columns, fy = v - (v | 0);
    for (let x = 1; x < width - 1; x++) {
      const c0 = left[x], c1 = right[x], fx = share[x];
      const bx = (sumX[r0 + c0] * (1 - fx) + sumX[r0 + c1] * fx) * (1 - fy) + (sumX[r1 + c0] * (1 - fx) + sumX[r1 + c1] * fx) * fy;
      const by = (sumY[r0 + c0] * (1 - fx) + sumY[r0 + c1] * fx) * (1 - fy) + (sumY[r1 + c0] * (1 - fx) + sumY[r1 + c1] * fx) * fy;
      const p = y * width + x, gx = gradientX[p] - bx, gy = gradientY[p] - by, m = Math.sqrt(gx * gx + gy * gy);
      magnitude[p] = m;
      gradientX[p] = gx; gradientY[p] = gy;
      histogram[m >= 127.75 ? 511 : (m * 4) | 0]++;
      samples++;
    }
  }
  let median = 0;
  for (let i = 0, seen = 0; i < 512; i++) { seen += histogram[i]; if (seen >= samples / 2) { median = i / 4; break; } }
  let threshold = Math.max(0.8, 3 * median), count = 0;
  for (let attempt = 0; attempt < 4; attempt++) {
    angle.fill(-1);
    count = 0;
    for (let y = 1; y < height - 1; y++) {
      for (let x = 1; x < width - 1; x++) {
        const p = y * width + x, m = magnitude[p];
        if (m < threshold) continue;
        // Orientação pelo tensor de estrutura (soma de gx², gy², gx·gy na vizinhança 3×3).
        let xx = 0, yy = 0, xy = 0;
        for (let q = p - width - 1; q <= p + width - 1; q += width) {
          for (let k = q; k < q + 3; k++) { const gx = gradientX[k], gy = gradientY[k]; xx += gx * gx; yy += gy * gy; xy += gx * gy; }
        }
        let degrees = 0.5 * Math.atan2(2 * xy, xx - yy) * 57.29577951308232;
        if (degrees < 0) degrees += 180;
        // Vizinhos ao longo do gradiente (4 direções) para manter só o máximo local.
        let a: number, b: number;
        if (degrees < 22.5 || degrees >= 157.5) { a = p - 1; b = p + 1; }
        else if (degrees < 67.5) { a = p - width - 1; b = p + width + 1; }
        else if (degrees < 112.5) { a = p - width; b = p + width; }
        else { a = p - width + 1; b = p + width - 1; }
        if (m < magnitude[a] || m < magnitude[b]) continue;
        const t = Math.round(degrees);
        angle[p] = t >= 180 ? t - 180 : t;
        count++;
      }
    }
    // Só texturas extremas (alcatifa, tecido) sobem o limiar; não se apagam os lados ténues de uma folha.
    if (count <= total * 0.3) break;
    threshold *= 1.4;
  }
  return { width, height, angle, count, data };
}

/** Reta ρ = x·cosθ + y·sinθ e o seu perfil de apoio (arestas coincidentes, de 1 em 1 px, dentro da imagem). */
type Line = { theta: number; rho: number; votes: number; cos: number; sin: number; start: number; cumulative: Int32Array };
/** Evidência de um lado: apoio ao longo do lado, junto de cada canto e no prolongamento para lá dos cantos. */
type Side = { coverage: number; ends: number; overshoot: number; length: number };
type Candidate = { corners: Point[]; score: number; coverage: number; method: "region" | "lines" };

const SPREAD = 6;          // graus de tolerância entre a direção da aresta e a normal da reta (votos de Hough)
const ANGLE_TOLERANCE = 20; // graus entre a aresta de um píxel e o lado do quadrilátero
const MIN_SCORE = 0.55;    // pontuação mínima (apoio ponderado × forma × papel) para aceitar
const MIN_SIDE = 0.45;     // cada lado coincide com arestas reais em pelo menos 45 % do comprimento…
const MIN_END = 0.35;      // …e junto de cada canto: linhas e blocos de texto não chegam aos cantos da folha
const MAX_OVERSHOOT = 0.6; // um lado verdadeiro termina no canto; uma reta que continua é de outra coisa
const LINES = 28;          // retas a combinar (as mais votadas e as mais exteriores de cada orientação)
const COS: Float32Array = new Float32Array(180), SIN: Float32Array = new Float32Array(180);
for (let t = 0; t < 180; t++) { COS[t] = Math.cos(t * Math.PI / 180); SIN[t] = Math.sin(t * Math.PI / 180); }

const angleGap = (a: number, b: number) => { const d = Math.abs(a - b) % 180; return Math.min(d, 180 - d); };

let accumulator: Float32Array | null = null;

/** Retas mais votadas (transformada de Hough com a orientação de cada aresta), já com o perfil de apoio. */
function houghLines(edges: Edges, limit = LINES): Line[] {
  const { width, height, angle } = edges;
  const D = Math.ceil(Math.hypot(width, height)), R = 2 * D + 2, size = 180 * R;
  if (!accumulator || accumulator.length !== size) accumulator = new Float32Array(size); else accumulator.fill(0);
  const votes = accumulator;
  for (let p = 0; p < angle.length; p++) {
    const a = angle[p];
    if (a < 0) continue;
    const x = p % width, y = (p - x) / width;
    for (let k = -SPREAD; k <= SPREAD; k++) {
      let t = a + k;
      if (t < 0) t += 180; else if (t >= 180) t -= 180;
      // Voto repartido pelas duas células de ρ mais próximas: um lado inclinado não fica dividido ao meio.
      const r = x * COS[t] + y * SIN[t] + D, base = r | 0, fraction = r - base, cell = t * R + base;
      votes[cell] += 1 - fraction;
      votes[cell + 1] += fraction;
    }
  }
  const minimum = Math.max(12, 0.1 * Math.min(width, height));
  const peaks: { t: number; rho: number; votes: number }[] = [];
  for (let t = 0; t < 180; t++) {
    const row = t * R;
    for (let r = 0; r < R; r++) {
      const v = votes[row + r];
      if (v < minimum) continue;
      let peak = true;
      for (let dt = -3; dt <= 3 && peak; dt++) {
        for (let dr = -3; dr <= 3; dr++) {
          if (!dt && !dr) continue;
          let tt = t + dt, rr = r + dr;
          if (tt < 0) { tt += 180; rr = 2 * D - rr; } else if (tt >= 180) { tt -= 180; rr = 2 * D - rr; }
          if (rr < 0 || rr >= R) continue;
          const other = votes[tt * R + rr];
          if (other > v || (other === v && tt * R + rr < row + r)) { peak = false; break; }
        }
      }
      if (peak) peaks.push({ t, rho: r - D, votes: v });
    }
  }
  peaks.sort((a, b) => b.votes - a.votes);
  // Retas agrupadas por orientação (±4°). Num documento com muitas linhas de texto compridas, estas
  // ocupariam todos os lugares; em cada grupo ficam as mais votadas e as mais exteriores, porque os
  // lados da folha envolvem sempre o texto.
  const groups: { theta: number; members: { peak: typeof peaks[number]; key: number }[] }[] = [];
  for (const peak of peaks) {
    let group = groups.find(g => angleGap(g.theta, peak.t) <= 4);
    if (!group) groups.push(group = { theta: peak.t, members: [] });
    group.members.push({ peak, key: Math.abs(peak.t - group.theta) > 90 ? -peak.rho : peak.rho });
  }
  const chosen = new Set<typeof peaks[number]>();
  for (const group of groups) {
    for (const { peak } of group.members.slice(0, 4)) chosen.add(peak);
    const byPosition = group.members.slice().sort((a, b) => a.key - b.key);
    for (const { peak } of [...byPosition.slice(0, 2), ...byPosition.slice(-2)]) chosen.add(peak);
  }
  const lines: Line[] = [];
  for (const peak of peaks) {
    if (lines.length >= limit) break;
    if (!chosen.has(peak)) continue;
    const line = withSupport(edges, peak.t, peak.rho, peak.votes);
    if (line) lines.push(line);
  }
  return lines;
}

/** Extremos (parâmetro s) da reta dentro da imagem; pontos da reta: (ρ·cos − s·sin, ρ·sin + s·cos). */
function span(width: number, height: number, rho: number, cos: number, sin: number): [number, number] | null {
  const x0 = rho * cos, y0 = rho * sin;
  let from = -Infinity, to = Infinity;
  if (Math.abs(sin) > 1e-6) { const a = x0 / sin, b = (x0 - (width - 1)) / sin; from = Math.max(from, Math.min(a, b)); to = Math.min(to, Math.max(a, b)); }
  else if (x0 < 0 || x0 > width - 1) return null;
  if (Math.abs(cos) > 1e-6) { const a = -y0 / cos, b = (height - 1 - y0) / cos; from = Math.max(from, Math.min(a, b)); to = Math.min(to, Math.max(a, b)); }
  else if (y0 < 0 || y0 > height - 1) return null;
  return to - from >= 8 ? [from, to] : null;
}

/**
 * Afina a reta de Hough (ângulo de 1 em 1°, distância de 1 em 1 px) com as arestas próximas, por mínimos
 * quadrados totais, e calcula o perfil de apoio: soma acumulada de passos de 1 px com aresta da mesma
 * orientação na vizinhança 3×3. Sem o ajuste, um lado comprido afasta-se 1–2 px da aresta nas pontas.
 */
function withSupport(edges: Edges, t: number, rho: number, votes: number): Line | null {
  const { width, height, angle } = edges;
  let theta = t, cos = COS[t], sin = SIN[t];
  for (const band of [2.5, 1.5]) {
    const range = span(width, height, rho, cos, sin);
    if (!range) return null;
    let n = 0, mx = 0, my = 0;
    const xs: number[] = [], ys: number[] = [];
    for (let s = range[0]; s <= range[1]; s++) {
      for (let o = -3; o <= 3; o++) {
        const x = Math.round((rho + o) * cos - s * sin), y = Math.round((rho + o) * sin + s * cos);
        if (x < 0 || y < 0 || x >= width || y >= height) continue;
        const a = angle[y * width + x];
        if (a < 0 || angleGap(a, theta) > ANGLE_TOLERANCE || Math.abs(x * cos + y * sin - rho) > band) continue;
        xs.push(x); ys.push(y); mx += x; my += y; n++;
      }
    }
    if (n < 8) break;
    mx /= n; my /= n;
    let sxx = 0, syy = 0, sxy = 0;
    for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; sxx += dx * dx; syy += dy * dy; sxy += dx * dy; }
    // Direção principal dos pontos; a normal fica a 90°.
    let normal = (0.5 * Math.atan2(2 * sxy, sxx - syy)) * 180 / Math.PI + 90;
    normal = ((normal % 180) + 180) % 180;
    if (angleGap(normal, theta) > 4) break;
    theta = normal; cos = Math.cos(theta * Math.PI / 180); sin = Math.sin(theta * Math.PI / 180);
    rho = mx * cos + my * sin;
  }
  const range = span(width, height, rho, cos, sin);
  if (!range) return null;
  const [from, to] = range, x0 = rho * cos, y0 = rho * sin;
  const n = Math.floor(to - from) + 1, cumulative = new Int32Array(n + 1);
  for (let i = 0; i < n; i++) {
    const s = from + i, x = Math.round(x0 - s * sin), y = Math.round(y0 + s * cos);
    let hit = 0;
    for (let oy = -1; oy <= 1 && !hit; oy++) {
      const yy = y + oy;
      if (yy < 0 || yy >= height) continue;
      for (let ox = -1; ox <= 1; ox++) {
        const xx = x + ox;
        if (xx < 0 || xx >= width) continue;
        const a = angle[yy * width + xx];
        if (a >= 0 && angleGap(a, theta) <= ANGLE_TOLERANCE) { hit = 1; break; }
      }
    }
    cumulative[i + 1] = cumulative[i] + hit;
  }
  return { theta, rho, votes, cos, sin, start: from, cumulative };
}

/** Fração de apoio da reta entre as posições a e b; -1 se o intervalo fica fora da imagem. */
function supported(line: Line, a: number, b: number) {
  const n = line.cumulative.length - 1;
  const i = Math.max(0, Math.min(n, Math.round(a - line.start))), j = Math.max(0, Math.min(n, Math.round(b - line.start)));
  return j > i ? (line.cumulative[j] - line.cumulative[i]) / (j - i) : -1;
}

/** Evidência de um lado de comprimento `length`, dada a medida de apoio entre duas distâncias ao primeiro canto. */
function evidence(length: number, measure: (from: number, to: number) => number): Side {
  const margin = Math.max(2, 0.03 * length), end = Math.max(6, 0.15 * length), extension = Math.max(6, 0.1 * length);
  return {
    coverage: Math.max(0, measure(margin, length - margin)),
    ends: Math.max(0, Math.min(measure(1, end), measure(length - end, length - 1))),
    overshoot: Math.max(0, measure(-extension, -3), measure(length + 3, length + extension)),
    length,
  };
}

function sideOnLine(line: Line, P: Point, Q: Point): Side {
  const sp = -P[0] * line.sin + P[1] * line.cos, sq = -Q[0] * line.sin + Q[1] * line.cos;
  const a = Math.min(sp, sq), b = Math.max(sp, sq);
  return evidence(b - a, (from, to) => supported(line, a + from, a + to));
}

/** O mesmo para um segmento qualquer (cantos da região clara), amostrando diretamente o mapa de arestas. */
function sideOnSegment(edges: Edges, P: Point, Q: Point): Side {
  const { width, height, angle } = edges;
  const dx = Q[0] - P[0], dy = Q[1] - P[1], length = Math.hypot(dx, dy) || 1, ux = dx / length, uy = dy / length;
  let normal = Math.atan2(ux, -uy) * 180 / Math.PI;
  if (normal < 0) normal += 180;
  normal %= 180;
  return evidence(length, (from, to) => {
    let hits = 0, samples = 0;
    for (let s = from; s < to; s++) {
      const x = Math.round(P[0] + ux * s), y = Math.round(P[1] + uy * s);
      if (x < 0 || y < 0 || x >= width || y >= height) continue;
      samples++;
      found: for (let oy = -1; oy <= 1; oy++) {
        for (let ox = -1; ox <= 1; ox++) {
          const xx = x + ox, yy = y + oy;
          if (xx < 0 || yy < 0 || xx >= width || yy >= height) continue;
          const a = angle[yy * width + xx];
          if (a >= 0 && angleGap(a, normal) <= ANGLE_TOLERANCE) { hits++; break found; }
        }
      }
    }
    return samples ? hits / samples : -1;
  });
}

function intersection(a: Line, b: Line): Point | null {
  const determinant = a.cos * b.sin - a.sin * b.cos;
  if (Math.abs(determinant) < 1e-6) return null;
  return [(a.rho * b.sin - a.sin * b.rho) / determinant, (a.cos * b.rho - a.rho * b.cos) / determinant];
}

/** Brancura média do interior (papel claro e pouco saturado) numa grelha de pontos. */
function interiorWhiteness(edges: Edges, corners: Point[]) {
  const { width, height, data } = edges;
  let total = 0, count = 0;
  for (let i = 1; i < 8; i++) {
    for (let j = 1; j < 8; j++) {
      const u = i / 8, v = j / 8;
      const top: Point = [corners[0][0] + (corners[1][0] - corners[0][0]) * u, corners[0][1] + (corners[1][1] - corners[0][1]) * u];
      const bottom: Point = [corners[3][0] + (corners[2][0] - corners[3][0]) * u, corners[3][1] + (corners[2][1] - corners[3][1]) * u];
      const x = Math.round(top[0] + (bottom[0] - top[0]) * v), y = Math.round(top[1] + (bottom[1] - top[1]) * v);
      if (x < 0 || y < 0 || x >= width || y >= height) continue;
      const q = (y * width + x) * 4, r = data[q], g = data[q + 1], b = data[q + 2];
      total += 0.299 * r + 0.587 * g + 0.114 * b - 0.6 * (Math.max(r, g, b) - Math.min(r, g, b));
      count++;
    }
  }
  return count ? total / count : 0;
}

/**
 * Pontuação de um quadrilátero (cantos em píxeis, pela ordem do contorno) a partir da evidência dos lados.
 * Rejeita lados sem apoio, sem apoio junto a um canto ou que continuam para lá do canto.
 */
function rate(edges: Edges, corners: Point[], sides: Side[], method: Candidate["method"]): Candidate | null {
  if (sides.some(side => side.coverage < MIN_SIDE || side.ends < MIN_END || side.overshoot > MAX_OVERSHOOT)) return null;
  const perimeter = sides.reduce((sum, side) => sum + side.length, 0);
  const coverage = sides.reduce((sum, side) => sum + side.coverage * side.length, 0) / perimeter;
  const overshoot = Math.max(...sides.map(side => side.overshoot));
  const area = polygonArea(corners) / (edges.width * edges.height);
  const paper = Math.max(0.15, Math.min(1, (interiorWhiteness(edges, ordered(corners)) - 90) / 110));
  const score = coverage * (0.85 + 0.15 * Math.min(1, area / 0.3)) * paper * (1 - 0.5 * Math.max(0, overshoot - 0.2));
  return { corners, coverage, method, score };
}

/** Distância entre duas retas quase paralelas, medida junto ao centro da imagem. */
function separation(a: Line, b: Line, cx: number, cy: number) {
  const offset = cx * b.cos + cy * b.sin - b.rho;
  const px = cx - offset * b.cos, py = cy - offset * b.sin;
  return Math.abs(px * a.cos + py * a.sin - a.rho);
}

/** Quadriláteros formados por dois pares de retas (lados opostos ~paralelos, adjacentes ~perpendiculares). */
function lineCandidates(edges: Edges, lines: Line[]): Candidate[] {
  const { width, height } = edges;
  const gap = 0.08 * Math.min(width, height);
  const pairs: [number, number][] = [];
  for (let i = 0; i < lines.length; i++) {
    for (let j = i + 1; j < lines.length; j++) {
      if (angleGap(lines[i].theta, lines[j].theta) <= 30 && separation(lines[i], lines[j], width / 2, height / 2) >= gap) pairs.push([i, j]);
    }
  }
  const found: Candidate[] = [];
  for (let a = 0; a < pairs.length; a++) {
    for (let b = a + 1; b < pairs.length; b++) {
      const [i, j] = pairs[a], [k, l] = pairs[b];
      if (i === k || i === l || j === k || j === l) continue;
      if (Math.min(angleGap(lines[i].theta, lines[k].theta), angleGap(lines[i].theta, lines[l].theta), angleGap(lines[j].theta, lines[k].theta), angleGap(lines[j].theta, lines[l].theta)) < 45) continue;
      // Contorno i∩k → k∩j → j∩l → l∩i: os lados ficam sobre k, j, l e i.
      const points = [intersection(lines[i], lines[k]), intersection(lines[k], lines[j]), intersection(lines[j], lines[l]), intersection(lines[l], lines[i])];
      if (points.some(p => !p || p[0] < -0.02 * width || p[1] < -0.02 * height || p[0] > 1.02 * width || p[1] > 1.02 * height)) continue;
      const corners = points as Point[];
      const normal = ordered(corners).map(([x, y]) => [Math.min(1, Math.max(0, x / (width - 1))), Math.min(1, Math.max(0, y / (height - 1)))] as Point) as Corners;
      if (!validCorners(normal) || !plausibleAngles(normal) || polygonArea(normal) < 0.04) continue;
      const sides = [sideOnLine(lines[k], corners[0], corners[1]), sideOnLine(lines[j], corners[1], corners[2]), sideOnLine(lines[l], corners[2], corners[3]), sideOnLine(lines[i], corners[3], corners[0])];
      const result = rate(edges, corners, sides, "lines");
      if (result) found.push(result);
    }
  }
  return found;
}

/** Afina os lados com as arestas reais (mínimos quadrados) e interseta-os; `null` se não for fiável. */
function refineWithEdges(edges: Edges, corners: Point[]) {
  const { width, height, angle } = edges;
  const sides: Point[][] = [];
  for (let i = 0; i < 4; i++) {
    const P = corners[i], Q = corners[(i + 1) % 4];
    const dx = Q[0] - P[0], dy = Q[1] - P[1], length = Math.hypot(dx, dy);
    if (length < 8) return null;
    let normal = Math.atan2(dx, -dy) * 180 / Math.PI;
    if (normal < 0) normal += 180;
    normal %= 180;
    const nx = -dy / length, ny = dx / length;
    const points: Point[] = [];
    for (let t = 0.08; t <= 0.92; t += 1 / length) {
      for (let offset = -3; offset <= 3; offset++) {
        const x = Math.round(P[0] + dx * t + nx * offset), y = Math.round(P[1] + dy * t + ny * offset);
        if (x < 0 || y < 0 || x >= width || y >= height) continue;
        const a = angle[y * width + x];
        if (a >= 0 && angleGap(a, normal) <= ANGLE_TOLERANCE) points.push([x, y]);
      }
    }
    if (points.length < 8) return null;
    sides.push(points);
  }
  const refined = refine(corners, sides.flat());
  if (!refined || refined.some((p, i) => Math.hypot(p[0] - corners[i][0], p[1] - corners[i][1]) > 6)) return null;
  return refined;
}

/** Deteção pela maior região clara (papel com contraste em relação ao fundo). */
function regionDetection(image: Pixels): Detection {
  const { width, height, data } = image;
  const total = width * height;
  // Usa memória partilhada que o mapa de arestas reescreve a seguir: nada daqui é devolvido por referência.
  const { a: smooth, tmp, label, stack, mask: bright } = buffers(total);
  for (let i = 0, q = 0; i < total; i++, q += 4) {
    const r = data[q], g = data[q + 1], b = data[q + 2];
    const max = r > g ? (r > b ? r : b) : (g > b ? g : b), min = r < g ? (r < b ? r : b) : (g < b ? g : b);
    const value = 0.299 * r + 0.587 * g + 0.114 * b - 0.6 * (max - min);
    smooth[i] = value > 0 ? value : 0;
  }
  blur3(smooth, smooth, tmp, width, height);
  blur3(smooth, smooth, tmp, width, height);
  const { threshold, separation } = otsu(smooth, total);
  if (separation < 24) {
    // Sem contraste: imagem toda clara (papel demasiado perto ou papel sobre fundo branco) ou nada encontrado.
    let sum = 0;
    for (let i = 0; i < total; i++) sum += smooth[i];
    return { corners: null, reason: sum / total > 170 ? "contrast" : "none", area: 0 };
  }
  for (let i = 0; i < total; i++) bright[i] = smooth[i] > threshold ? 1 : 0;

  // Maior região clara ligada; uma região que ocupa grande parte da moldura é fundo ou papel demasiado perto.
  label.fill(-1);
  const perimeter = 2 * (width + height);
  let best = -1, bestSize = 0, largestTouching = 0;
  for (let start = 0; start < total; start++) {
    if (!bright[start] || label[start] !== -1) continue;
    let top = 0, size = 0, touches = 0;
    stack[top++] = start; label[start] = start;
    while (top) {
      const p = stack[--top]; size++;
      const x = p % width, y = (p - x) / width;
      if (x === 0 || y === 0 || x === width - 1 || y === height - 1) touches++;
      if (x > 0 && bright[p - 1] && label[p - 1] === -1) { label[p - 1] = start; stack[top++] = p - 1; }
      if (x < width - 1 && bright[p + 1] && label[p + 1] === -1) { label[p + 1] = start; stack[top++] = p + 1; }
      if (y > 0 && bright[p - width] && label[p - width] === -1) { label[p - width] = start; stack[top++] = p - width; }
      if (y < height - 1 && bright[p + width] && label[p + width] === -1) { label[p + width] = start; stack[top++] = p + width; }
    }
    if (touches >= perimeter * 0.5) { largestTouching = Math.max(largestTouching, size); continue; }
    if (size > bestSize) { bestSize = size; best = start; }
  }
  if (best < 0) return { corners: null, reason: largestTouching > total * 0.3 ? "touching" : "none", area: 0 };
  if (bestSize < total * 0.02) return { corners: null, reason: largestTouching > total * 0.3 ? "touching" : "none", area: 0 };

  // Contorno da região escolhida → envolvente convexa → 4 vértices.
  const boundary: Point[] = [];
  for (let p = 0; p < total; p++) {
    if (label[p] !== best) continue;
    const x = p % width, y = (p - x) / width;
    if (x === 0 || y === 0 || x === width - 1 || y === height - 1
      || label[p - 1] !== best || label[p + 1] !== best || label[p - width] !== best || label[p + width] !== best) boundary.push([x, y]);
  }
  const hull = convexHull(boundary);
  if (hull.length < 4) return { corners: null, reason: "irregular", area: 0 };
  const rough = ordered(reduceToQuad(hull));
  const quad = refine(rough, boundary) || rough;
  const quadArea = polygonArea(quad);
  let corners = ordered(quad).map(([x, y]) => [Math.min(1, Math.max(0, x / (width - 1))), Math.min(1, Math.max(0, y / (height - 1)))] as Point) as Corners;
  if (!validCorners(corners)) corners = rough.map(([x, y]) => [x / (width - 1), y / (height - 1)] as Point) as Corners;
  const area = polygonArea(corners);
  let fill = bestSize / Math.max(1, quadArea);
  if (fill < 0.9) {
    // Zonas escuras rodeadas pelo papel (logótipos, tabelas, carimbos) contam como papel.
    const outside = new Uint8Array(total);
    let head = 0;
    const visit = (q: number) => { if (!outside[q] && label[q] !== best) { outside[q] = 1; stack[head++] = q; } };
    for (let x = 0; x < width; x++) { visit(x); visit((height - 1) * width + x); }
    for (let y = 0; y < height; y++) { visit(y * width); visit(y * width + width - 1); }
    while (head) {
      const p = stack[--head], x = p % width;
      if (x > 0) visit(p - 1);
      if (x < width - 1) visit(p + 1);
      if (p >= width) visit(p - width);
      if (p < total - width) visit(p + width);
    }
    let filled = 0;
    for (let p = 0; p < total; p++) if (!outside[p]) filled++;
    fill = filled / Math.max(1, quadArea);
  }
  if (!validCorners(corners) || !plausibleAngles(corners) || fill < 0.9 || fill > 1.2) return { corners: null, reason: "irregular", area };
  const inside = corners.every(([x, y]) => x > BORDER && y > BORDER && x < 1 - BORDER && y < 1 - BORDER);
  if (!inside) return { corners, reason: "touching", area };
  if (area < MIN_AREA) return { corners, reason: "small", area };
  return { corners, reason: "ok", area };
}

/**
 * Deteta o documento numa imagem reduzida (≈256–320 px no lado maior), combinando a região clara
 * (rápida e exata com contraste) e as arestas retas (branco sobre branco). Fica o melhor candidato.
 */
export function detectDocument(image: Pixels): Detection {
  const region = regionDetection(image);
  const edges = edgeMap(image);
  const { width, height } = image;
  const candidates = lineCandidates(edges, houghLines(edges));
  if (region.corners && region.reason !== "irregular") {
    const pixels = region.corners.map(([x, y]) => [x * (width - 1), y * (height - 1)] as Point);
    const rated = rate(edges, pixels, pixels.map((p, i) => sideOnSegment(edges, p, pixels[(i + 1) % 4])), "region");
    // A região clara já foi validada pela forma e pelo preenchimento: em empate, é a mais exata.
    if (rated) candidates.push({ ...rated, score: rated.score * 1.05 });
  }
  const best = candidates.sort((a, b) => b.score - a.score)[0];
  if (!best || best.score < MIN_SCORE) {
    // Folha cortada pela moldura ou longe: o lado na margem da imagem não tem aresta, mas o contorno da
    // região clara orienta o utilizador («afaste», «aproxime»). Nunca é «ok», logo nunca captura sozinho.
    if (region.corners && (region.reason === "touching" || region.reason === "small")) return { ...region, score: 0, method: "region" };
    const reason = region.reason === "ok" || region.reason === "small" || region.reason === "touching" ? "irregular" : region.reason;
    return { corners: null, reason, area: 0, score: best?.score || 0, method: "" };
  }
  const pixels = refineWithEdges(edges, best.corners) || best.corners;
  const normal = (points: Point[]) => ordered(points).map(([x, y]) => [Math.min(1, Math.max(0, x / (width - 1))), Math.min(1, Math.max(0, y / (height - 1)))] as Point) as Corners;
  let corners = normal(pixels);
  if (!validCorners(corners)) corners = normal(best.corners);
  if (!validCorners(corners) || !plausibleAngles(corners)) return { corners: null, reason: "irregular", area: 0, score: best.score, method: best.method };
  const area = polygonArea(corners);
  const inside = corners.every(([x, y]) => x > BORDER && y > BORDER && x < 1 - BORDER && y < 1 - BORDER);
  const reason: DetectionReason = !inside ? "touching" : area < MIN_AREA ? "small" : "ok";
  return { corners, reason, area, score: best.score, method: best.method };
}

/** Cantos para uma fotografia já tirada (aceita documento pequeno ou a tocar a margem, para ajuste manual). */
export function detectCorners(image: Pixels): Corners | null {
  const result = detectDocument(image);
  return result.corners && result.reason !== "irregular" && result.area >= MIN_STILL_AREA ? result.corners : null;
}

/**
 * Desenha a imagem (ou a imagem atual do vídeo) numa tela reduzida e devolve os píxeis.
 * As dimensões são explícitas: num `<video>`, `width`/`height` são atributos, não o tamanho da imagem.
 */
export function analyse(source: CanvasImageSource, sourceWidth: number, sourceHeight: number, canvas?: HTMLCanvasElement, side = 320) {
  if (!sourceWidth || !sourceHeight) return null;
  const scale = side / Math.max(sourceWidth, sourceHeight);
  const target = canvas || document.createElement("canvas");
  const width = Math.max(16, Math.round(sourceWidth * scale)), height = Math.max(16, Math.round(sourceHeight * scale));
  if (target.width !== width || target.height !== height) { target.width = width; target.height = height; }
  const context = target.getContext("2d", { willReadFrequently: true });
  if (!context) return null;
  context.drawImage(source, 0, 0, width, height);
  return context.getImageData(0, 0, width, height);
}

/** Reduz a imagem e deteta os cantos; nunca falha (devolve `null` para ajuste manual). */
export async function findDocument(source: CanvasImageSource & { width: number; height: number }): Promise<Corners | null> {
  try {
    const pixels = analyse(source, source.width, source.height);
    return pixels ? detectCorners(pixels) : null;
  } catch {
    return null;
  }
}
