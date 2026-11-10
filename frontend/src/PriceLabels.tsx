import { useEffect, useMemo, useState } from "react";
import { ImagePlus, Printer, Settings2, Tag, Trash2 } from "lucide-react";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import { salePriceNet } from "./ReceivingInsights";
import "./PriceLabels.css";

/**
 * Etiquetas de preço de uma receção: quantidades por artigo (por omissão, as recebidas), posição inicial
 * na folha, pré-visualização e impressão. As medidas são em centímetros, como na Wintouch; a impressão
 * usa uma página própria (iframe), sem o resto da aplicação.
 */

type Row = Record<string, any>;
type Settings = Row & { revision: number };
type Item = { key: string; code: string; name: string; gross: number | null; quantity: number };

const NUMBER_FIELDS: [string, string, string][] = [
  ["margin_top", "Topo", "Margens da página (cm)"], ["margin_left", "Esquerda", "Margens da página (cm)"],
  ["columns", "Horizontal", "Número de etiquetas"], ["rows", "Vertical", "Número de etiquetas"],
  ["label_width", "Largura", "Dimensão da etiqueta (cm)"], ["label_height", "Altura", "Dimensão da etiqueta (cm)"],
  ["gap_horizontal", "Horizontal", "Distância entre etiquetas (cm)"], ["gap_vertical", "Vertical", "Distância entre etiquetas (cm)"],
  ["page_width", "Largura", "Folha (cm)"], ["page_height", "Altura", "Folha (cm)"],
];
const euro = (value: number) => new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(value);
const escape = (value: string) => value.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));

/** PVP com IVA atual do artigo: o com IVA do ERP quando é essa a base; senão, o sem IVA mais a taxa de venda. */
export function salePriceGross(article: Row | undefined): number | null {
  if (!article) return null;
  const erp = article.origin === "ERP" || !!article.erp_id;
  if (erp && article.erp_price_basis === "Com IVA" && Number(article.erp_price) > 0) return Number(article.erp_price);
  const net = salePriceNet(article);
  const vat = erp ? article.erp_vat_rate : article.vat_rate;
  return net !== null && vat != null && vat !== "" && Number.isFinite(Number(vat)) ? Math.round(net * (1 + Number(vat) / 100) * 100) / 100 : null;
}

/** Artigos de mercadoria da receção, com as unidades recebidas (bónus incluído) arredondadas para cima. */
export function receiptItems(doc: Row, articles: Row[]): Item[] {
  const byId = new Map(articles.map(a => [a.id, a]));
  const items = new Map<string, Item>();
  for (const line of doc.lines || []) {
    if (line.nature && line.nature !== "goods") continue;
    const article = line.article_id ? byId.get(line.article_id) : undefined;
    if (article?.service) continue;
    const key = line.article_id || line.description;
    const current = items.get(key) || { key, code: article?.code || line.supplier_reference || "", name: article?.name || line.description || "",
      gross: salePriceGross(article), quantity: 0 };
    current.quantity += Number(line.quantity || 0);
    items.set(key, current);
  }
  return [...items.values()].map(item => ({ ...item, quantity: Math.max(0, Math.ceil(item.quantity - 1e-9)) }));
}

function labelHtml(item: Item, s: Settings) {
  const name = s.uppercase_name ? item.name.toLocaleUpperCase("pt-PT") : item.name;
  return `<div class="l">${s.show_logo && s.logo ? `<div class="logo"><img src="${s.logo}" alt=""></div>` : ""}`
    + `${s.show_name ? `<div class="name">${escape(name)}</div>` : ""}`
    + `<div class="mid">${s.show_price ? `<div class="price">${item.gross === null ? "" : escape(euro(item.gross))}</div>` : "<div></div>"}`
    + `<div class="right">${s.show_code ? `<div class="code">${escape(item.code)}</div>` : ""}${s.price_note ? `<div class="note">${escape(s.price_note)}</div>` : ""}</div></div>`
    + `${s.footer ? `<div class="footer">${escape(s.footer)}</div>` : ""}</div>`;
}

/** Documento de impressão: uma página por folha, etiquetas posicionadas em centímetros. */
export function printDocument(items: Item[], s: Settings, start: number) {
  const n = (k: string) => Number(s[k]);
  const perPage = n("columns") * n("rows");
  const labels: Item[] = items.flatMap(item => Array.from({ length: item.quantity }, () => item));
  const slots: (Item | null)[] = [...Array.from({ length: Math.max(0, start - 1) }, () => null), ...labels];
  const pages: string[] = [];
  for (let page = 0; page * perPage < slots.length; page++) {
    const cells = slots.slice(page * perPage, (page + 1) * perPage).map((item, index) => {
      if (!item) return "";
      const column = index % n("columns"), row = Math.floor(index / n("columns"));
      const left = n("margin_left") + column * (n("label_width") + n("gap_horizontal"));
      const top = n("margin_top") + row * (n("label_height") + n("gap_vertical"));
      return `<div class="slot" style="left:${left}cm;top:${top}cm">${labelHtml(item, s)}</div>`;
    }).join("");
    pages.push(`<section class="page">${cells}</section>`);
  }
  const k = n("label_height") / 2.2;  // As letras acompanham a altura da etiqueta (referência 2,20 cm).
  return `<!doctype html><html lang="pt-PT"><head><meta charset="utf-8"><title>Etiquetas</title><style>
@page { size: ${n("page_width")}cm ${n("page_height")}cm; margin: 0; }
* { box-sizing: border-box; } html, body { margin: 0; padding: 0; background: #fff; color: #000; font-family: Arial, Helvetica, sans-serif; }
.page { position: relative; width: ${n("page_width")}cm; height: ${n("page_height")}cm; overflow: hidden; page-break-after: always; break-after: page; }
.page:last-child { page-break-after: auto; break-after: auto; }
.slot { position: absolute; width: ${n("label_width")}cm; height: ${n("label_height")}cm; overflow: hidden; }
.l { height: 100%; padding: ${0.06 * k}cm ${0.08 * k}cm; display: flex; flex-direction: column; justify-content: space-between; gap: ${0.02 * k}cm; }
.logo { text-align: center; line-height: 0; } .logo img { max-width: 100%; max-height: ${0.62 * k}cm; object-fit: contain; }
.name { font-weight: 700; font-size: ${7 * k}pt; text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.1; }
.mid { display: flex; align-items: center; justify-content: space-between; gap: ${0.05 * k}cm; }
.price { font-weight: 800; font-size: ${12.5 * k}pt; white-space: nowrap; line-height: 1; }
.right { text-align: right; min-width: 0; } .code { font-size: ${6.5 * k}pt; font-weight: 700; line-height: 1.1; }
.note { font-size: ${4.2 * k}pt; line-height: 1.1; max-width: ${1.3 * k}cm; margin-left: auto; }
.footer { font-size: ${6 * k}pt; font-weight: 700; white-space: nowrap; overflow: hidden; line-height: 1.1; }
</style></head><body>${pages.join("")}</body></html>`;
}

function printHtml(html: string) {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
  document.body.appendChild(frame);
  const target = frame.contentWindow!;
  target.document.open();
  target.document.write(html);
  target.document.close();
  const images = [...target.document.images];
  Promise.all(images.map(img => img.complete ? Promise.resolve() : new Promise(done => { img.onload = img.onerror = () => done(undefined); }))).then(() => {
    target.focus();
    target.print();
    window.setTimeout(() => frame.remove(), 60_000);
  });
}

export default function PriceLabels({ doc, articles, canConfigure, onClose }: { doc: Row; articles: Row[]; canConfigure: boolean; onClose: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [items, setItems] = useState<Item[]>(() => receiptItems(doc, articles));
  const [start, setStart] = useState(1);
  const [configuring, setConfiguring] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { api("/stock/label-settings").then((s: Settings) => { setSettings(s); setDraft(s); }).catch(e => setError((e as Error).message)); }, []);
  const active = configuring && draft ? draft : settings;
  const total = items.reduce((sum, item) => sum + item.quantity, 0);
  const perPage = active ? Number(active.columns) * Number(active.rows) : 0;
  const sheets = perPage ? Math.ceil((start - 1 + total) / perPage) : 0;
  const missingPrice = items.filter(item => item.quantity > 0 && item.gross === null);
  const sample = useMemo(() => items.find(item => item.quantity > 0) || items[0], [items]);

  async function save() {
    if (!draft) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const saved = await api("/stock/label-settings", draft, { preserveDraft: true });
      setSettings(saved); setDraft(saved); setNotice("Definições das etiquetas guardadas."); setConfiguring(false);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function logo(file: File | undefined) {
    if (!file || !draft) return;
    if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) { setError("Escolha uma imagem PNG, JPEG, WebP ou GIF."); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const value = String(reader.result || "");
      if (value.length > 400_000) { setError("O logótipo é demasiado grande (máximo ≈ 300 KB). Reduza a imagem."); return; }
      setError(""); setDraft({ ...draft, logo: value, show_logo: true });
    };
    reader.readAsDataURL(file);
  }

  return <AppDialog className="price-labels-dialog" aria-label="Imprimir etiquetas" busy={busy} guardChanges={false} onRequestClose={onClose}>
    <div className="price-labels-head"><Tag size={26} aria-hidden="true" /><div><h2>Imprimir etiquetas</h2>
      <p>Receção {doc.document || ""} · {doc.supplier_name}. Preço de venda atual com IVA de cada artigo.</p></div></div>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && <Notice tone="success">{notice}</Notice>}
    {!settings || !active ? !error && <p role="status">A carregar as definições das etiquetas…</p> : <>
      <div className="price-labels-layout">
        <section aria-label="Artigos e quantidades">
          <div className="table-wrap"><table className="price-labels-items">
            <thead><tr><th>Código</th><th>Descrição</th><th className="numeric">PVP com IVA</th><th className="numeric">Etiquetas</th></tr></thead>
            <tbody>{items.map(item => <tr key={item.key}><td>{item.code || "—"}</td><td>{item.name}</td>
              <td className="numeric">{item.gross === null ? <span className="price-labels-missing">Sem PVP</span> : euro(item.gross)}</td>
              <td className="numeric"><input type="number" min="0" max="999" step="1" aria-label={`Etiquetas de ${item.name}`} value={item.quantity}
                onChange={e => { const value = Math.max(0, Math.min(999, Math.floor(Number(e.target.value) || 0))); setItems(list => list.map(i => i.key === item.key ? { ...i, quantity: value } : i)); }} /></td></tr>)}</tbody>
          </table></div>
          <div className="price-labels-totals">
            <label>Começar na etiqueta<input type="number" min="1" max={perPage} value={start} onChange={e => setStart(Math.max(1, Math.min(perPage, Math.floor(Number(e.target.value) || 1))))} /></label>
            <span><b>{total}</b> etiqueta(s) · {sheets} folha(s) de {perPage}</span>
            <ActionButton type="button" onClick={() => setItems(receiptItems(doc, articles))}>Repor quantidades da receção</ActionButton>
          </div>
          {!!missingPrice.length && <Notice tone="warning">Sem PVP na ficha: {missingPrice.map(i => i.code || i.name).join(", ")}. A etiqueta sai sem preço.</Notice>}
        </section>
        <section className="price-labels-preview" aria-label="Pré-visualização da etiqueta">
          <h3>Pré-visualização</h3>
          {sample ? <iframe title="Pré-visualização da etiqueta" className="price-labels-frame"
            style={{ width: `${Number(active.label_width) * 2}cm`, height: `${Number(active.label_height) * 2}cm` }}
            srcDoc={printDocument([{ ...sample, quantity: 1 }], { ...active, page_width: active.label_width, page_height: active.label_height, margin_top: 0, margin_left: 0 }, 1)
              .replace("<body>", "<body style=\"zoom:2\">")} /> : <p className="dash-muted">Sem artigos de mercadoria.</p>}
          <small>Tamanho real: {String(active.label_width).replace(".", ",")} × {String(active.label_height).replace(".", ",")} cm (ampliado para leitura).</small>
        </section>
      </div>
      {configuring && draft && <section className="price-labels-settings" aria-label="Definições das etiquetas">
        <div className="price-labels-groups">
          {[...new Set(NUMBER_FIELDS.map(([, , group]) => group))].map(group => <fieldset key={group}><legend>{group}</legend>
            {NUMBER_FIELDS.filter(([, , g]) => g === group).map(([key, label]) => <label key={key}>{label}
              <input type="number" min="0" step={["columns", "rows"].includes(key) ? 1 : 0.01} value={draft[key]} onChange={e => setDraft({ ...draft, [key]: ["columns", "rows"].includes(key) ? Math.floor(Number(e.target.value) || 1) : e.target.value })} /></label>)}
          </fieldset>)}
          <fieldset><legend>Conteúdo</legend>
            {([["show_logo", "Logótipo"], ["show_name", "Descrição"], ["uppercase_name", "Descrição em maiúsculas"], ["show_price", "Preço com IVA"], ["show_code", "Código do artigo"]] as const).map(([key, label]) =>
              <label key={key} className="checkbox"><input type="checkbox" checked={!!draft[key]} onChange={e => setDraft({ ...draft, [key]: e.target.checked })} />{label}</label>)}
            <label>Nota do preço<input value={draft.price_note} maxLength={80} onChange={e => setDraft({ ...draft, price_note: e.target.value })} /></label>
            <label>Rodapé (por exemplo, telefone)<input value={draft.footer} maxLength={80} placeholder="Tel: 236 023 218" onChange={e => setDraft({ ...draft, footer: e.target.value })} /></label>
          </fieldset>
          <fieldset><legend>Logótipo</legend>
            {draft.logo ? <img className="price-labels-logo" src={draft.logo} alt="Logótipo das etiquetas" /> : <p className="dash-muted">Sem logótipo.</p>}
            <label className="ui-action price-labels-upload"><ImagePlus size={16} aria-hidden="true" />Escolher imagem
              <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={e => logo(e.target.files?.[0])} /></label>
            {draft.logo && <ActionButton type="button" onClick={() => setDraft({ ...draft, logo: "" })}><Trash2 size={16} aria-hidden="true" />Retirar logótipo</ActionButton>}
          </fieldset>
        </div>
        <div className="ui-dialog-actions"><ActionButton type="button" onClick={() => { setDraft(settings); setConfiguring(false); }}>Cancelar alterações</ActionButton>
          <ActionButton type="button" className="primary" onClick={() => void save()}>Guardar definições</ActionButton></div>
      </section>}
      <div className="ui-dialog-actions">
        {canConfigure && !configuring && <ActionButton type="button" onClick={() => setConfiguring(true)}><Settings2 size={16} aria-hidden="true" />Definições da folha e do conteúdo</ActionButton>}
        <ActionButton type="button" onClick={onClose}>Fechar</ActionButton>
        <ActionButton type="button" className="primary" disabled={!total || configuring} title={configuring ? "Guarde ou cancele as definições antes de imprimir" : undefined}
          onClick={() => printHtml(printDocument(items, settings, start))}><Printer size={16} aria-hidden="true" />Imprimir {total} etiqueta(s)</ActionButton>
      </div>
    </>}
  </AppDialog>;
}
