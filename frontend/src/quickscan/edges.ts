/**
 * Deteção local das margens de um documento fotografado (sem serviços externos).
 *
 * Luminância → desfocagem → limiar de Otsu → maior região clara ligada → quatro cantos pelos
 * extremos de x+y e x−y. Devolve `null` quando a deteção não é fiável; nesse caso a interface
 * propõe a imagem inteira para ajuste manual. O utilizador revê sempre o recorte.
 */
export type Point = [number, number];
export type Corners = [Point, Point, Point, Point]; // sup. esq., sup. dir., inf. dir., inf. esq.

export const FULL_FRAME: Corners = [[0.03, 0.03], [0.97, 0.03], [0.97, 0.97], [0.03, 0.97]];

/** Quadrilátero convexo, sem cruzamentos, pela ordem dos ponteiros do relógio (mesma regra do servidor). */
export function validCorners(corners: Corners) {
  for (let i = 0; i < 4; i++) {
    const [a, b, c] = [corners[i], corners[(i + 1) % 4], corners[(i + 2) % 4]];
    const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    if (cross <= 0.01) return false;
  }
  return corners.every(([x, y]) => x >= 0 && x <= 1 && y >= 0 && y <= 1);
}

function area(c: Corners) {
  let total = 0;
  for (let i = 0; i < 4; i++) total += c[i][0] * c[(i + 1) % 4][1] - c[(i + 1) % 4][0] * c[i][1];
  return Math.abs(total) / 2;
}

function blur(values: Float32Array, width: number, height: number, radius: number) {
  const out = new Float32Array(values.length), tmp = new Float32Array(values.length);
  for (let y = 0; y < height; y++) {
    let sum = 0;
    for (let x = -radius; x <= radius; x++) sum += values[y * width + Math.min(width - 1, Math.max(0, x))];
    for (let x = 0; x < width; x++) {
      tmp[y * width + x] = sum / (2 * radius + 1);
      sum += values[y * width + Math.min(width - 1, x + radius + 1)] - values[y * width + Math.max(0, x - radius)];
    }
  }
  for (let x = 0; x < width; x++) {
    let sum = 0;
    for (let y = -radius; y <= radius; y++) sum += tmp[Math.min(height - 1, Math.max(0, y)) * width + x];
    for (let y = 0; y < height; y++) {
      out[y * width + x] = sum / (2 * radius + 1);
      sum += tmp[Math.min(height - 1, y + radius + 1) * width + x] - tmp[Math.max(0, y - radius) * width + x];
    }
  }
  return out;
}

function otsu(values: Float32Array) {
  const histogram = new Array(256).fill(0);
  for (const v of values) histogram[Math.max(0, Math.min(255, Math.round(v)))]++;
  const total = values.length;
  let sum = 0;
  for (let i = 0; i < 256; i++) sum += i * histogram[i];
  let background = 0, weight = 0, best = 0, threshold = 127;
  for (let i = 0; i < 256; i++) {
    weight += histogram[i];
    if (!weight || weight === total) continue;
    background += i * histogram[i];
    const meanLow = background / weight, meanHigh = (sum - background) / (total - weight);
    const between = weight * (total - weight) * (meanLow - meanHigh) ** 2;
    if (between > best) { best = between; threshold = i; }
  }
  return threshold;
}

/** Deteta os cantos numa imagem já reduzida (≈320 px no lado maior). */
export function detectCorners(image: ImageData): Corners | null {
  const { width, height, data } = image;
  const luminance = new Float32Array(width * height);
  for (let i = 0; i < width * height; i++) luminance[i] = 0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2];
  const smooth = blur(luminance, width, height, 2);
  const threshold = otsu(smooth);
  const bright = new Uint8Array(width * height);
  for (let i = 0; i < bright.length; i++) bright[i] = smooth[i] > threshold ? 1 : 0;
  // Maior região clara ligada (4-vizinhança); o papel costuma ser mais claro do que a mesa.
  const label = new Int32Array(width * height).fill(-1);
  const stack = new Int32Array(width * height);
  let best: number[] = [], bestSize = 0;
  for (let start = 0; start < bright.length; start++) {
    if (!bright[start] || label[start] !== -1) continue;
    let top = 0, size = 0;
    stack[top++] = start; label[start] = start;
    let tl = start, tr = start, br = start, bl = start;
    let tlv = Infinity, trv = -Infinity, brv = -Infinity, blv = Infinity;
    let touches = 0;
    while (top) {
      const p = stack[--top]; size++;
      const x = p % width, y = (p - x) / width;
      if (x === 0 || y === 0 || x === width - 1 || y === height - 1) touches++;
      if (x + y < tlv) { tlv = x + y; tl = p; }
      if (x + y > brv) { brv = x + y; br = p; }
      if (x - y > trv) { trv = x - y; tr = p; }
      if (x - y < blv) { blv = x - y; bl = p; }
      for (const q of [x > 0 ? p - 1 : -1, x < width - 1 ? p + 1 : -1, y > 0 ? p - width : -1, y < height - 1 ? p + width : -1]) {
        if (q >= 0 && bright[q] && label[q] === -1) { label[q] = start; stack[top++] = q; }
      }
    }
    // Uma região que ocupa grande parte da moldura da imagem é fundo, não papel.
    if (size > bestSize && touches < 2 * (width + height) * 0.6) { bestSize = size; best = [tl, tr, br, bl]; }
  }
  if (!best.length) return null;
  const corners = best.map(p => [(p % width) / (width - 1), Math.floor(p / width) / (height - 1)] as Point) as Corners;
  const covered = bestSize / (width * height);
  if (covered < 0.12 || !validCorners(corners) || area(corners) < 0.15) return null;
  // A região tem de preencher razoavelmente o quadrilátero (papel, não uma mancha irregular).
  if (covered / area(corners) < 0.75) return null;
  return corners;
}

/** Reduz a imagem e deteta os cantos; nunca falha (devolve `null` para ajuste manual). */
export async function findDocument(source: CanvasImageSource & { width: number; height: number }): Promise<Corners | null> {
  try {
    const scale = 320 / Math.max(source.width, source.height);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(16, Math.round(source.width * scale));
    canvas.height = Math.max(16, Math.round(source.height * scale));
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    context.drawImage(source, 0, 0, canvas.width, canvas.height);
    return detectCorners(context.getImageData(0, 0, canvas.width, canvas.height));
  } catch {
    return null;
  }
}
