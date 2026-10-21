/**
 * Deteção local das margens de um documento (fotografias e imagem da câmara em direto), sem
 * serviços externos nem bibliotecas.
 *
 * 1. «Brancura» de cada píxel: luminância menos saturação. O papel é claro e pouco saturado;
 *    mesas de madeira, tecidos e fundos coloridos ficam escuros nesta medida.
 * 2. Desfocagem leve e limiar de Otsu → região clara principal (4-vizinhança).
 * 3. Contorno da região → envolvente convexa → redução aos 4 vértices que melhor a descrevem
 *    (funciona com o papel rodado ou em perspetiva).
 * 4. Validação: quadrilátero convexo, ângulos plausíveis e região que preenche o quadrilátero.
 *
 * Devolve sempre o motivo, para orientar o utilizador em direto («aproxime», «afaste»…). O
 * utilizador revê sempre o recorte; sem deteção fiável propõe-se a imagem inteira.
 */
export type Point = [number, number];
export type Corners = [Point, Point, Point, Point]; // sup. esq., sup. dir., inf. dir., inf. esq.
/**
 * ok: pronto · small: demasiado longe · touching: corta a margem da imagem · irregular: forma pouco
 * fiável · contrast: imagem clara sem contraste (papel demasiado perto ou fundo branco) · none: nada encontrado
 */
export type DetectionReason = "ok" | "small" | "touching" | "irregular" | "contrast" | "none";
export type Detection = { corners: Corners | null; reason: DetectionReason; area: number };
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

function blur(values: Float32Array, width: number, height: number, radius: number) {
  const out = new Float32Array(values.length), tmp = new Float32Array(values.length);
  const size = 2 * radius + 1;
  for (let y = 0; y < height; y++) {
    let sum = 0;
    for (let x = -radius; x <= radius; x++) sum += values[y * width + Math.min(width - 1, Math.max(0, x))];
    for (let x = 0; x < width; x++) {
      tmp[y * width + x] = sum / size;
      sum += values[y * width + Math.min(width - 1, x + radius + 1)] - values[y * width + Math.max(0, x - radius)];
    }
  }
  for (let x = 0; x < width; x++) {
    let sum = 0;
    for (let y = -radius; y <= radius; y++) sum += tmp[Math.min(height - 1, Math.max(0, y)) * width + x];
    for (let y = 0; y < height; y++) {
      out[y * width + x] = sum / size;
      sum += tmp[Math.min(height - 1, y + radius + 1) * width + x] - tmp[Math.max(0, y - radius) * width + x];
    }
  }
  return out;
}

/** Limiar de Otsu e separação entre as médias das duas classes (contraste disponível). */
function otsu(values: Float32Array) {
  const histogram = new Array(256).fill(0);
  for (const v of values) histogram[Math.max(0, Math.min(255, Math.round(v)))]++;
  const total = values.length;
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

/** Deteta o documento numa imagem reduzida (≈256–320 px no lado maior). */
export function detectDocument(image: Pixels): Detection {
  const { width, height, data } = image;
  const total = width * height;
  const whiteness = new Float32Array(total);
  for (let i = 0; i < total; i++) {
    const r = data[i * 4], g = data[i * 4 + 1], b = data[i * 4 + 2];
    const chroma = Math.max(r, g, b) - Math.min(r, g, b);
    whiteness[i] = Math.max(0, 0.299 * r + 0.587 * g + 0.114 * b - 0.6 * chroma);
  }
  const smooth = blur(whiteness, width, height, 2);
  const { threshold, separation } = otsu(smooth);
  if (separation < 24) {
    // Sem contraste: imagem toda clara (papel demasiado perto ou papel sobre fundo branco) ou nada encontrado.
    const mean = smooth.reduce((sum, value) => sum + value, 0) / total;
    return { corners: null, reason: mean > 170 ? "contrast" : "none", area: 0 };
  }
  const bright = new Uint8Array(total);
  for (let i = 0; i < total; i++) bright[i] = smooth[i] > threshold ? 1 : 0;

  // Maior região clara ligada; uma região que ocupa grande parte da moldura é fundo ou papel demasiado perto.
  const label = new Int32Array(total).fill(-1);
  const stack = new Int32Array(total);
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
