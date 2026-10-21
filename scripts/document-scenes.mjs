// Cenas sintéticas realistas para testar a deteção de margens (scripts/test-edge-detection.mjs): papel branco sobre mesa branca, sombras
// finas de contacto, texto em coordenadas do papel (com margens), perspetiva, luz desigual, desfocagem,
// ruído e objetos à volta. Renderização com sobreamostragem 3×3 (arestas sub-píxel).
export function makeRandom(seed) {
  let s = (Math.imul(seed + 1, 2654435761) >>> 0) || 1;
  return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
}

function insideQuad(px, py, q) {
  let sign = 0;
  for (let i = 0; i < q.length; i++) {
    const [ax, ay] = q[i], [bx, by] = q[(i + 1) % q.length];
    const c = (bx - ax) * (py - ay) - (by - ay) * (px - ax);
    if (c === 0) continue;
    const s = c > 0 ? 1 : -1;
    if (!sign) sign = s; else if (s !== sign) return false;
  }
  return true;
}

function invBilinear(px, py, q) {
  const [a, b, c, d] = q;
  const ex = b[0] - a[0], ey = b[1] - a[1], fx = d[0] - a[0], fy = d[1] - a[1];
  const gx = a[0] - b[0] + c[0] - d[0], gy = a[1] - b[1] + c[1] - d[1], hx = px - a[0], hy = py - a[1];
  const cross = (ux, uy, vx, vy) => ux * vy - uy * vx;
  const k2 = cross(gx, gy, fx, fy), k1 = cross(ex, ey, fx, fy) + cross(hx, hy, gx, gy), k0 = cross(hx, hy, ex, ey);
  let v;
  if (Math.abs(k2) < 1e-6) v = -k0 / k1;
  else {
    const w = Math.sqrt(Math.max(0, k1 * k1 - 4 * k0 * k2));
    v = (-k1 - w) / (2 * k2);
    if (v < -0.01 || v > 1.01) v = (-k1 + w) / (2 * k2);
  }
  const dx = ex + gx * v, dy = ey + gy * v;
  const u = Math.abs(dx) > Math.abs(dy) ? (hx - fx * v) / dx : (hy - fy * v) / dy;
  return [u, v];
}

function segmentDistance(px, py, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(px - a[0] - t * dx, py - a[1] - t * dy);
}

const clockwise = q => {
  let area = 0;
  for (let i = 0; i < 4; i++) area += q[i][0] * q[(i + 1) % 4][1] - q[(i + 1) % 4][0] * q[i][1];
  return area > 0; // eixo y para baixo: área positiva = sentido dos ponteiros
};

/**
 * Gera uma cena. `kind`: "white" (mesa branca), "wood", "dark", "grey", "paper" (folha sobre folha).
 * Devolve { width, height, data, quad (verdade, em píxeis), expect: "ok"|"touching"|"none", meta }.
 */
export function makeScene(seed, overrides = {}) {
  const random = makeRandom(seed);
  const pick = list => list[Math.floor(random() * list.length)];
  const range = (a, b) => a + (b - a) * random();
  const portrait = overrides.portrait ?? random() < 0.6;
  const width = overrides.width ?? (portrait ? 192 : 256), height = overrides.height ?? (portrait ? 256 : 192);
  const kind = overrides.kind ?? pick(["white", "white", "white", "white", "paper", "grey", "wood", "dark"]);
  const docType = overrides.docType ?? pick(["a4", "a4", "a4", "receipt"]);
  const expect = overrides.expect ?? "ok";

  // --- Papel: tamanho, rotação, perspetiva -----------------------------------------------------
  let quad = null;
  if (expect !== "none") {
    for (let attempt = 0; attempt < 200; attempt++) {
      const aspect = docType === "receipt" ? range(2.4, 3.6) : range(1.38, 1.44);
      const areaFraction = overrides.area ?? (docType === "receipt" ? range(0.12, 0.3) : range(0.22, 0.7));
      const area = areaFraction * width * height;
      const w = Math.sqrt(area / aspect), h = w * aspect;
      const angle = (overrides.angle ?? (random() < 0.85 ? range(-18, 18) : range(-40, 40))) * Math.PI / 180;
      const cx = range(0.3, 0.7) * width, cy = range(0.3, 0.7) * height;
      const persp = overrides.perspective ?? range(0, 0.08);
      const base = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
      // Perspetiva: o lado de cima mais estreito (telemóvel inclinado) + ruído nos cantos.
      const narrow = range(0, persp);
      const q = base.map(([x, y], i) => {
        const shrink = i < 2 ? 1 - narrow : 1 + narrow * 0.3;
        const jx = range(-persp, persp) * w * 0.5, jy = range(-persp, persp) * h * 0.3;
        const X = x * shrink + jx, Y = y + jy;
        return [cx + X * Math.cos(angle) - Y * Math.sin(angle), cy + X * Math.sin(angle) + Y * Math.cos(angle)];
      });
      if (!clockwise(q)) continue;
      const margin = 0.03;
      const insideFrame = q.every(([x, y]) => x > margin * width && y > margin * height && x < (1 - margin) * width && y < (1 - margin) * height);
      if (expect === "ok" && !insideFrame) continue;
      // «A tocar»: pelo menos um canto fora da moldura (cortado), não apenas perto da margem.
      if (expect === "touching" && !q.some(([x, y]) => x < -0.02 * width || y < -0.02 * height || x > 1.02 * width || y > 1.02 * height)) continue;
      quad = q;
      break;
    }
    if (!quad) return null;
  }

  // --- Materiais -------------------------------------------------------------------------------
  const tableTint = pick([[1.0, 0.99, 0.955], [1, 1, 1], [0.985, 0.99, 1.0], [1.0, 0.985, 0.97]]);
  let table, grain = 0;
  if (kind === "white") table = range(0.82, 0.93);
  else if (kind === "paper") table = range(0.86, 0.94);
  else if (kind === "grey") table = range(0.45, 0.6);
  else if (kind === "wood") { table = range(0.5, 0.7); grain = range(0.04, 0.1); }
  else { table = range(0.12, 0.25); grain = range(0, 0.06); }
  const tableColor = kind === "wood" ? [table, table * 0.78, table * 0.55] : kind === "dark" ? [table, table * 0.9, table * 0.85] : tableTint.map(t => table * t);
  // Papel: um pouco mais claro do que a mesa branca (0 a +7 %), por vezes mais escuro; tom frio (branqueadores).
  const delta = overrides.delta ?? (kind === "white" || kind === "paper" ? range(-0.025, 0.07) : range(0.0, 0.08));
  const paperBase = Math.min(0.97, (kind === "white" || kind === "paper" ? table : 0.9) + delta);
  const paperTint = docType === "receipt" ? [1, 0.995, 0.975] : pick([[0.985, 0.995, 1.0], [1, 1, 1], [0.99, 0.995, 1.0]]);
  const paperColor = paperTint.map(t => paperBase * t);
  const grainAngle = range(0, Math.PI), grainFreq = range(0.15, 0.4);

  // --- Luz ----------------------------------------------------------------------------------------
  const gx = range(-0.18, 0.18), gy = range(-0.18, 0.18), vignette = range(0, 0.3);
  const blob = random() < 0.3 ? { x: range(0, width), y: range(0, height), r: range(0.2, 0.5) * Math.max(width, height), s: range(0.08, 0.22) } : null;
  const lightAngle = range(0, 2 * Math.PI), lx = Math.cos(lightAngle), ly = Math.sin(lightAngle);
  const castShadow = overrides.shadow ?? range(0, 0.14), castWidth = range(0.6, 3.5), contact = range(0, 0.07);
  const highlight = range(0, 0.04);

  // --- Conteúdo do documento ---------------------------------------------------------------------
  const pw = quad ? Math.hypot(quad[1][0] - quad[0][0], quad[1][1] - quad[0][1]) : 0;
  const ph = quad ? Math.hypot(quad[3][0] - quad[0][0], quad[3][1] - quad[0][1]) : 0;
  const receipt = docType === "receipt";
  const marginX = (overrides.textMargin ?? (receipt ? range(0.04, 0.08) : range(0.06, 0.12))) * pw;
  const marginTop = (overrides.textMargin ?? (receipt ? range(0.03, 0.06) : range(0.04, 0.09))) * ph;
  const marginBottom = range(0.06, 0.3) * ph;
  const pitch = range(2.2, 5.5), inkHeight = range(0.4, 0.6) * pitch;
  const inkLevel = range(0.08, 0.3);
  const rows = new Map();
  const rowInfo = r => {
    if (!rows.has(r)) {
      const blank = random() < 0.15;
      const length = (receipt ? range(0.4, 1) : range(0.35, 1)) * (pw - 2 * marginX);
      const words = [];
      let x = 0;
      while (x < length) { const w = range(2.5, 12); words.push([x, Math.min(length, x + w)]); x += w + range(1, 2.4); }
      rows.set(r, { blank, words });
    }
    return rows.get(r);
  };
  const hasLogo = !receipt && random() < 0.5;
  const logo = hasLogo ? { x0: marginX, y0: marginTop, x1: marginX + range(0.15, 0.35) * pw, y1: marginTop + range(0.04, 0.09) * ph, color: pick([[0.15, 0.2, 0.5], [0.6, 0.1, 0.1], [0.1, 0.1, 0.1], [0.2, 0.45, 0.25]]) } : null;
  const rules = !receipt && random() < 0.5 ? [range(0.3, 0.5), range(0.55, 0.75)].map(v => v * ph) : [];
  const textStart = logo ? logo.y1 + 3 : marginTop;
  const ink = (X, Y) => {
    if (logo && X >= logo.x0 && X <= logo.x1 && Y >= logo.y0 && Y <= logo.y1) return logo.color;
    for (const r of rules) if (Math.abs(Y - r) < 0.45 && X > marginX && X < pw - marginX) return [0.25, 0.25, 0.27];
    if (X < marginX || X > pw - marginX || Y < textStart || Y > ph - marginBottom) return null;
    const row = Math.floor((Y - textStart) / pitch);
    if ((Y - textStart) - row * pitch > inkHeight) return null;
    const info = rowInfo(row);
    if (info.blank) return null;
    const x = X - marginX;
    for (const [a, b] of info.words) if (x >= a && x <= b) return ((x * 1.7 + row * 0.37) % 1) < 0.55 ? [inkLevel, inkLevel, inkLevel * 1.1] : null;
    return null;
  };

  // --- Distratores -----------------------------------------------------------------------------
  const objects = [];
  const freeRect = (w, h) => {
    for (let attempt = 0; attempt < 40; attempt++) {
      const x0 = range(-0.3 * w, width - 0.7 * w), y0 = range(-0.3 * h, height - 0.7 * h);
      const r = [[x0, y0], [x0 + w, y0], [x0 + w, y0 + h], [x0, y0 + h]];
      if (!quad) return r;
      const clear = r.every(([x, y]) => !insideQuad(x, y, quad)) && quad.every(([x, y]) => !insideQuad(x, y, r))
        && Math.min(...r.flatMap(p => quad.map((a, i) => segmentDistance(p[0], p[1], a, quad[(i + 1) % 4])))) > 6
        && Math.min(...quad.flatMap(p => r.map((a, i) => segmentDistance(p[0], p[1], a, r[(i + 1) % 4])))) > 6;
      if (clear) return r;
    }
    return null;
  };
  const distractors = overrides.distractors ?? (random() < 0.45 ? 1 + Math.floor(random() * 2) : 0);
  for (let i = 0; i < distractors; i++) {
    const type = pick(["keyboard", "phone", "pen", "edge", "paper"]);
    if (type === "keyboard") { const r = freeRect(range(60, 140), range(30, 60)); if (r) objects.push({ quad: r, color: [0.12, 0.12, 0.13] }); }
    else if (type === "phone") { const r = freeRect(range(25, 40), range(50, 75)); if (r) objects.push({ quad: r, color: [0.05, 0.05, 0.06] }); }
    else if (type === "pen") {
      const r = freeRect(range(40, 80), 2.5);
      if (r) objects.push({ quad: r, color: pick([[0.1, 0.15, 0.5], [0.05, 0.05, 0.05], [0.6, 0.1, 0.1]]) });
    } else if (type === "paper") {
      // Outra folha, sempre cortada pela moldura (uma folha inteira visível também seria um documento válido).
      const r = freeRect(range(50, 90), range(70, 120));
      if (r && r.some(([x, y]) => x < 0 || y < 0 || x > width || y > height)) objects.push({ quad: r, color: paperColor.map(c => c * range(0.97, 1.02)) });
    } else {
      // Fim da mesa: tudo o que fica para lá de uma reta, fora do papel.
      const side = pick(["top", "bottom", "left", "right"]);
      const minY = quad ? Math.min(...quad.map(p => p[1])) : height / 2, maxY = quad ? Math.max(...quad.map(p => p[1])) : height / 2;
      const minX = quad ? Math.min(...quad.map(p => p[0])) : width / 2, maxX = quad ? Math.max(...quad.map(p => p[0])) : width / 2;
      const slope = range(-0.08, 0.08);
      if (side === "top" && minY > 14) objects.push({ edge: { axis: "y", at: range(4, minY - 8), slope, below: false }, color: [0.3, 0.27, 0.25] });
      if (side === "bottom" && height - maxY > 14) objects.push({ edge: { axis: "y", at: range(maxY + 8, height - 4), slope, below: true }, color: [0.3, 0.27, 0.25] });
      if (side === "left" && minX > 14) objects.push({ edge: { axis: "x", at: range(4, minX - 8), slope, below: false }, color: [0.3, 0.27, 0.25] });
      if (side === "right" && width - maxX > 14) objects.push({ edge: { axis: "x", at: range(maxX + 8, width - 4), slope, below: true }, color: [0.3, 0.27, 0.25] });
    }
  }

  // --- Renderização (3×3 subamostras) --------------------------------------------------------------
  const S = 3, radiance = new Float32Array(width * height * 3);
  const normals = quad ? quad.map((a, i) => { const b = quad[(i + 1) % 4], dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy); return [dy / l, -dx / l]; }) : [];
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      let r = 0, g = 0, b = 0;
      for (let sy = 0; sy < S; sy++) {
        for (let sx = 0; sx < S; sx++) {
          const px = x + (sx + 0.5) / S, py = y + (sy + 0.5) / S;
          let color = tableColor, shade = 1;
          if (grain) {
            const t = (px * Math.cos(grainAngle) + py * Math.sin(grainAngle)) * grainFreq;
            shade *= 1 + grain * (Math.sin(t) * 0.6 + Math.sin(t * 2.7 + 1.3) * 0.4);
          }
          const onPaper = quad && insideQuad(px, py, quad);
          if (onPaper) {
            color = paperColor;
            const [u, v] = invBilinear(px, py, quad);
            const c = ink(u * pw, v * ph);
            if (c) color = c;
            if (highlight) {
              for (let i = 0; i < 4; i++) {
                if (normals[i][0] * lx + normals[i][1] * ly >= 0) continue;
                const d = segmentDistance(px, py, quad[i], quad[(i + 1) % 4]);
                if (d < 1) shade *= 1 + highlight * (1 - d);
              }
            }
          } else if (quad) {
            for (let i = 0; i < 4; i++) {
              const d = segmentDistance(px, py, quad[i], quad[(i + 1) % 4]);
              if (d > 12) continue;
              const facing = Math.max(0, normals[i][0] * lx + normals[i][1] * ly);
              shade *= 1 - contact * Math.exp(-d / 0.7) - castShadow * facing * Math.exp(-d / castWidth);
            }
          }
          for (const object of objects) {
            if (object.quad && insideQuad(px, py, object.quad)) color = object.color;
            if (object.edge) {
              const { axis, at, slope, below } = object.edge;
              const beyond = axis === "y" ? (below ? py > at + slope * px : py < at + slope * px) : (below ? px > at + slope * py : px < at + slope * py);
              if (beyond) color = object.color;
            }
          }
          let light = 1 + gx * (px / width - 0.5) + gy * (py / height - 0.5);
          light -= vignette * 2 * ((px / width - 0.5) ** 2 + (py / height - 0.5) ** 2);
          if (blob) { const d = Math.hypot(px - blob.x, py - blob.y) / blob.r; if (d < 1.6) light *= 1 - blob.s * Math.exp(-d * d * 1.5); }
          r += color[0] * shade * light; g += color[1] * shade * light; b += color[2] * shade * light;
        }
      }
      const p = (y * width + x) * 3;
      radiance[p] = r / (S * S); radiance[p + 1] = g / (S * S); radiance[p + 2] = b / (S * S);
    }
  }
  // Exposição automática: o mais claro (percentil 99) fica em ≈ 240–250.
  const sample = [];
  for (let i = 0; i < radiance.length; i += 7) sample.push(radiance[i]);
  sample.sort((a, b) => a - b);
  const exposure = range(232, 250) / Math.max(1e-3, sample[Math.floor(sample.length * 0.99)]);
  // Desfocagem gaussiana separável (σ 0,3–1,0 px) e ruído.
  const sigma = overrides.blur ?? range(0.3, 1.0), radius = Math.ceil(sigma * 2.5);
  const kernel = [];
  for (let i = -radius; i <= radius; i++) kernel.push(Math.exp(-(i * i) / (2 * sigma * sigma)));
  const sum = kernel.reduce((a, b) => a + b, 0);
  for (let i = 0; i < kernel.length; i++) kernel[i] /= sum;
  const tmp = new Float32Array(radiance.length), out = new Float32Array(radiance.length);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) for (let c = 0; c < 3; c++) {
    let v = 0;
    for (let k = -radius; k <= radius; k++) v += kernel[k + radius] * radiance[(y * width + Math.min(width - 1, Math.max(0, x + k))) * 3 + c];
    tmp[(y * width + x) * 3 + c] = v;
  }
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) for (let c = 0; c < 3; c++) {
    let v = 0;
    for (let k = -radius; k <= radius; k++) v += kernel[k + radius] * tmp[(Math.min(height - 1, Math.max(0, y + k)) * width + x) * 3 + c];
    out[(y * width + x) * 3 + c] = v;
  }
  const noise = overrides.noise ?? range(0.5, 3.2);
  const gauss = () => { let u = 0, v = 0; while (!u) u = random(); while (!v) v = random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    const common = gauss() * noise * 0.7;
    for (let c = 0; c < 3; c++) data[i * 4 + c] = Math.round(out[i * 3 + c] * exposure + common + gauss() * noise * 0.5);
    data[i * 4 + 3] = 255;
  }
  return { width, height, data, quad, expect, meta: { kind, docType, delta: +delta.toFixed(3), shadow: +castShadow.toFixed(3), contact: +contact.toFixed(3), sigma: +sigma.toFixed(2), noise: +noise.toFixed(1), distractors: objects.length } };
}

/** Erro máximo entre cantos (coordenadas normalizadas), aceitando qualquer canto inicial. */
export function cornerError(found, truth, width, height) {
  const normal = truth.map(([x, y]) => [x / (width - 1), y / (height - 1)]);
  let best = Infinity;
  for (let shift = 0; shift < 4; shift++) best = Math.min(best, Math.max(...found.map((p, i) => Math.hypot(p[0] - normal[(i + shift) % 4][0], p[1] - normal[(i + shift) % 4][1]))));
  return best;
}
