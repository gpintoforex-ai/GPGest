import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Camera, Check, Send, Sparkles, Tag, X } from "lucide-react";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { BarList, ChartFrame, ColumnsChart, Legend } from "./DashboardCharts";
import { api } from "./lib/api";
import { formatEuro } from "./utils/format";
import "./Assistant.css";

/** Ações propostas pelo assistente: o servidor valida-as e devolve o pedido que o ecrã correspondente faria. */
type ArticleRef = { article_id: string; code: string; name: string };
type PriceAction = ArticleRef & {
  type: "price"; origin: "erp" | "local"; vat_rate: string; before_gross: string | null; after_gross: string;
  channels: { connection: string; own_price: boolean }[]; request: { path: string; body: unknown };
};
type ImageAction = ArticleRef & {
  type: "image"; role: "photo" | "label"; role_label: string; source: "anexo" | "url"; url?: string; site?: string;
  width: number; height: number; preview: string; request: { path: string; body?: unknown; upload?: "anexo" };
};
type ContentFields = { description: string; web_summary: string; web_highlights: string[]; seo_keyword: string; seo_title: string; seo_description: string };
type ContentAction = ArticleRef & {
  type: "web_content"; fields: ContentFields; needs_review: string[]; missing_information: string[]; images_used: boolean;
  request: { path: string; revision: number; discard: string };
};
type PublishAction = ArticleRef & {
  type: "publish"; shop: string; sku: string; gross: string; warnings: string[]; mode: "nova" | "repor";
  request: { path: string; body: unknown };
};
type Action = PriceAction | ImageAction | ContentAction | PublishAction;
type ActionState = { status: "pending" | "busy" | "done" | "cancelled" | "failed"; message?: string };
/** Gráfico pedido pelo assistente com dados já consultados: euros em cêntimos inteiros, restantes em texto decimal. */
type Chart = {
  title: string;
  kind: "colunas" | "linha" | "barras";
  unit: "euros" | "quantidade" | "percentagem";
  categories: string[];
  series: { name: string; values: (number | string | null)[] }[];
  note: string;
};
type Attachment = { blob: Blob; url: string };
type Turn = {
  role: "user" | "assistant"; content: string; image?: Attachment;
  tools?: { name: string }[]; actions?: Action[]; states?: ActionState[]; charts?: Chart[];
};

const PHOTO_SUGGESTION = "Tire uma fotografia ao produto e peça: «Cria este artigo para o site».";

/** Perguntas sugeridas: as consultas que o assistente sabe fazer. */
const SUGGESTIONS = [
  "Que artigos estão a terminar a validade nos próximos 60 dias?",
  "Que produtos é necessário encomendar?",
  "Compara as vendas deste mês com o mesmo mês do ano passado.",
  "Quais foram os 10 artigos mais vendidos nos últimos 30 dias?",
  "Que produtos o cliente … já comprou?",
  "Que clientes já não compram há mais de 90 dias?",
  "Qual é a previsão do IVA a pagar neste período?",
  "Que documentos estão em falta para a contabilidade?",
  "Mostra num gráfico a faturação dos últimos 12 meses comparada com o ano anterior.",
  "Altera o preço do artigo … para … € (com IVA).",
  PHOTO_SUGGESTION,
];

const TOOL_LABELS: Record<string, string> = {
  artigos_a_terminar_validade: "Lotes e validades",
  produtos_a_encomendar: "Reposição de stock",
  comparativo_vendas: "Faturação",
  artigos_mais_vendidos: "Vendas por artigo",
  compras_do_cliente: "Compras do cliente",
  clientes_sem_compras: "Clientes sem compras",
  previsao_iva: "Mapa de IVA",
  documentos_em_falta_contabilidade: "Conferência documental",
  procurar_artigos: "Artigos",
  propor_alteracao_preco: "Proposta de preço",
  mostrar_grafico: "Gráfico",
  propor_imagem: "Proposta de imagem",
  imagens_da_pagina: "Página do produto",
  gerar_descritivo: "Descritivo do site",
  propor_publicacao: "Loja online",
  web_search: "Pesquisa na internet",
};

function ChartView({ chart }: { chart: Chart }) {
  const decimal = new Intl.NumberFormat("pt-PT", { maximumFractionDigits: 2 });
  const format = chart.unit === "euros" ? formatEuro
    : chart.unit === "percentagem" ? (v: number) => `${decimal.format(v)} %` : (v: number) => decimal.format(v);
  const values = chart.series.map(s => s.values.map(v => v === null ? null : Number(v)));
  const cell = (v: number | null) => v === null ? "—" : format(v);
  const table = { columns: ["", ...chart.series.map(s => s.name)], rows: chart.categories.map((c, i) => [c, ...values.map(v => cell(v[i]))]) };
  const series = chart.series.map((s, i) => ({ key: String(i), label: s.name, form: (chart.kind === "linha" ? "line" : "bar") as "bar" | "line" }));
  return <div className="assistant-chart">
    <ChartFrame title={chart.title} legend={chart.series.length > 1 ? <Legend series={series} /> : undefined} table={table} footnote={chart.note || undefined}>
      {chart.kind === "barras"
        ? <BarList ariaLabel={chart.title} format={format} rows={chart.categories.map((c, i) => ({ label: c, value: values[0][i] ?? 0 }))} />
        : <ColumnsChart ariaLabel={chart.title} labels={chart.categories} series={series} values={values} formatValue={chart.unit === "euros" ? undefined : format} />}
    </ChartFrame>
  </div>;
}

const euros = (value: string | null) => value === null ? "sem preço"
  : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(value));

function describe(a: Action) {
  if (a.type === "price") return `preço de ${a.name} (${a.code}) para ${euros(a.after_gross)}`;
  if (a.type === "image") return `${a.role_label.toLowerCase()} de ${a.name} (${a.code}), ${a.source === "anexo" ? "a fotografia anexada" : `imagem de ${a.site}`}`;
  if (a.type === "web_content") return `descritivo do site de ${a.name} (${a.code})`;
  return `publicação de ${a.name} (${a.code}) na loja online ${a.shop}`;
}

/** Estado das ações já decididas, acrescentado à resposta no histórico: o modelo sabe o que foi gravado. */
function withOutcome(turn: Turn) {
  if (!turn.actions?.length) return turn.content;
  const lines = turn.actions.map((a, i) => {
    const s = turn.states?.[i]?.status;
    const what = describe(a);
    return s === "done" ? `- Confirmado pelo utilizador e gravado: ${what}.`
      : s === "cancelled" ? `- Cancelado pelo utilizador: ${what}.`
      : s === "failed" ? `- Falhou ao gravar: ${what} (${turn.states?.[i]?.message || "erro"}).`
      : `- Ainda por confirmar: ${what}.`;
  });
  return `${turn.content}\n\n[Estado das propostas]\n${lines.join("\n")}`;
}

/** Fotografia anexada, enviada pelo mesmo pedido da ficha do artigo (multipart). */
async function uploadAttachment(path: string, blob: Blob) {
  const body = new FormData();
  body.append("file", blob, "fotografia.jpg");
  let response: Response;
  try {
    response = await fetch("/api" + path, { method: "PUT", body, credentials: "same-origin", signal: AbortSignal.timeout(60_000) });
  } catch {
    throw new Error("Não foi possível contactar o servidor. Verifique a ligação e volte a tentar.");
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) window.dispatchEvent(new Event("gpgest-expired"));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Não foi possível guardar a fotografia.");
  return data;
}

/** Fotografia escolhida reduzida no navegador (lado maior até 2000 px, JPEG), como a ficha a guarda. */
async function shrink(file: File): Promise<Blob> {
  if (!file.type.startsWith("image/")) throw new Error("Escolha uma fotografia (JPG, PNG ou WebP).");
  if (file.size > 25 * 1024 * 1024) throw new Error("A fotografia excede 25 MB.");
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" }).catch(() => {
    throw new Error("Não foi possível ler a fotografia. Use JPG, PNG ou WebP.");
  });
  const scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const context = canvas.getContext("2d")!;
  context.fillStyle = "#fff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return new Promise((resolve, reject) => canvas.toBlob(b => b ? resolve(b) : reject(new Error("Não foi possível preparar a fotografia.")), "image/jpeg", 0.88));
}

function base64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(new Error("Não foi possível ler a fotografia."));
    reader.readAsDataURL(blob);
  });
}

const TITLES: Record<Action["type"], string> = {
  price: "Alterar preço de venda", image: "Guardar imagem na ficha", web_content: "Descritivo para o site", publish: "Publicar na loja online",
};
const DONE: Record<Action["type"], string> = {
  price: "Preço gravado.", image: "Imagem guardada na ficha do artigo.", web_content: "Descritivo aprovado e gravado na ficha.",
  publish: "Publicado: a loja online recebe o artigo na sincronização seguinte.",
};

function ContentEditor({ fields, onChange, disabled }: { fields: ContentFields; onChange: (f: ContentFields) => void; disabled: boolean }) {
  const set = (key: keyof ContentFields) => (e: { target: { value: string } }) =>
    onChange({ ...fields, [key]: key === "web_highlights" ? e.target.value.split("\n").map(l => l.trim()).filter(Boolean).slice(0, 8) : e.target.value });
  return <div className="assistant-content-fields">
    <label>Descrição<textarea rows={7} maxLength={4000} disabled={disabled} value={fields.description} onChange={set("description")} /></label>
    <label>Resumo<input maxLength={300} disabled={disabled} value={fields.web_summary} onChange={set("web_summary")} /></label>
    <label>Pontos-chave (um por linha)<textarea rows={3} disabled={disabled} defaultValue={fields.web_highlights.join("\n")} onBlur={set("web_highlights")} /></label>
    <label>Palavra-chave principal<input maxLength={60} disabled={disabled} value={fields.seo_keyword} onChange={set("seo_keyword")} /></label>
    <label>Título SEO<input maxLength={70} disabled={disabled} value={fields.seo_title} onChange={set("seo_title")} /></label>
    <label>Meta-descrição<textarea rows={2} maxLength={170} disabled={disabled} value={fields.seo_description} onChange={set("seo_description")} /></label>
  </div>;
}

/** Cartão de confirmação: nada é gravado até carregar em «Confirmar»; usa o mesmo pedido do ecrã correspondente. */
function ActionCard({ action, state, attachment, onChange }: { action: Action; state: ActionState; attachment?: Blob; onChange: (s: ActionState) => void }) {
  const [fields, setFields] = useState<ContentFields | null>(action.type === "web_content" ? action.fields : null);
  async function confirm() {
    onChange({ status: "busy" });
    try {
      if (action.type === "image" && action.request.upload === "anexo") {
        if (!attachment) throw new Error("A fotografia anexada já não está disponível nesta janela. Anexe-a de novo.");
        await uploadAttachment(action.request.path, attachment);
      } else if (action.type === "web_content") {
        await api(action.request.path, { ...fields, revision: action.request.revision }, { timeoutMs: 60_000 });
      } else {
        const result = await api(action.request.path, action.request.body, { timeoutMs: 60_000 });
        if (action.type === "price" && action.origin === "erp" && result.state !== "confirmed") {
          onChange({ status: "failed", message: result.state === "uncertain"
            ? "A Wintouch não confirmou a alteração. Verifique o preço na ficha do artigo antes de repetir."
            : result.error || "A Wintouch recusou o preço." });
          return;
        }
      }
      onChange({ status: "done" });
    } catch (e) {
      onChange({ status: "failed", message: (e as Error).message });
    }
  }
  async function cancel() {
    if (action.type !== "web_content") return onChange({ status: "cancelled" });
    onChange({ status: "busy" });
    try {
      await api(action.request.discard, {}, { timeoutMs: 30_000 });
      onChange({ status: "cancelled" });
    } catch (e) {
      onChange({ status: "failed", message: (e as Error).message });
    }
  }
  const open = state.status === "pending" || state.status === "busy";
  const busy = state.status === "busy";
  return <div className={`assistant-action is-${state.status}`} role="group" aria-label={`${TITLES[action.type]}: ${action.name}`}>
    <p className="assistant-action-title"><Tag size={16} aria-hidden="true" />{TITLES[action.type]}</p>
    <p><strong>{action.name}</strong> <span className="assistant-action-code">{action.code}</span></p>
    {action.type === "price" && <>
      <p className="assistant-action-prices">
        <span>{euros(action.before_gross)}</span> → <strong>{euros(action.after_gross)}</strong> <small>com IVA ({Number(action.vat_rate).toLocaleString("pt-PT")} %)</small>
      </p>
      <small className="assistant-action-note">{action.origin === "erp"
        ? `Enviado à Wintouch e só gravado depois de confirmado por leitura.${action.channels.some(c => !c.own_price) ? ` A loja online (${action.channels.filter(c => !c.own_price).map(c => c.connection).join(", ")}) recebe o preço na sincronização seguinte.` : ""}`
        : "Artigo local: o preço sem IVA é gravado na ficha do artigo."}</small>
    </>}
    {action.type === "image" && <>
      <img className="assistant-action-image" src={action.preview} alt={`${action.role_label} proposta para ${action.name}`} />
      <small className="assistant-action-note">{action.role_label} · {action.width} × {action.height} px · {action.source === "anexo" ? "fotografia anexada" : <>origem: <a href={action.url} target="_blank" rel="noopener noreferrer">{action.site}</a></>}.
        {" "}Substitui a imagem atual deste tipo.{action.source === "url" ? " Confirme que pode usar esta imagem (de preferência do fabricante ou do fornecedor)." : ""}
        {action.role === "label" ? " O rótulo fica interno (só a IA o lê) até o publicar na ficha." : ""}</small>
    </>}
    {action.type === "web_content" && fields && <>
      {!!action.needs_review.length && <Notice tone="warning"><strong>Confirme antes de publicar:</strong> {action.needs_review.join(" · ")}</Notice>}
      {!!action.missing_information.length && <small className="assistant-action-note">Informação em falta: {action.missing_information.join(" · ")}</small>}
      <ContentEditor fields={fields} onChange={setFields} disabled={!open || busy} />
      <small className="assistant-action-note">Pode editar o texto antes de aprovar. Fica gravado na ficha e segue para a loja online na sincronização seguinte.</small>
    </>}
    {action.type === "publish" && <>
      <p>Loja <strong>{action.shop}</strong> · SKU {action.sku} · <strong>{euros(action.gross)}</strong> com IVA</p>
      {!!action.warnings.length && <Notice tone="warning">{action.warnings.join(" ")}</Notice>}
      <small className="assistant-action-note">{action.mode === "repor" ? "Repõe a publicação retirada." : "Cria a publicação; a loja recebe o produto na sincronização seguinte."}</small>
    </>}
    {open ? <div className="assistant-action-buttons">
      <ActionButton type="button" variant="primary" disabled={busy} onClick={() => void confirm()}>
        <Check size={16} aria-hidden="true" />{busy ? "A gravar…" : action.type === "web_content" ? "Aprovar e gravar" : "Confirmar"}
      </ActionButton>
      <ActionButton type="button" variant="quiet" disabled={busy} onClick={() => void cancel()}>
        <X size={16} aria-hidden="true" />{action.type === "web_content" ? "Descartar" : "Cancelar"}
      </ActionButton>
    </div>
      : state.status === "done" ? <Notice tone="success">{DONE[action.type]}</Notice>
      : state.status === "cancelled" ? <p className="assistant-action-note">{action.type === "web_content" ? "Proposta descartada: a ficha não mudou." : "Cancelado: nada foi alterado."}</p>
      : <Notice tone="error">{state.message}</Notice>}
  </div>;
}

/** Negrito (**texto**) numa linha, sem HTML vindo da resposta. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4 ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>);
}

/** Texto simples da resposta: parágrafos e listas com «- ». */
function Reply({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let items: string[] = [];
  const flush = () => {
    if (items.length) blocks.push(<ul key={`l${blocks.length}`}>{items.map((item, i) => <li key={i}>{inline(item)}</li>)}</ul>);
    items = [];
  };
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (/^[-•*] /.test(trimmed)) items.push(trimmed.slice(2));
    else {
      flush();
      if (trimmed) blocks.push(<p key={`p${blocks.length}`}>{inline(trimmed)}</p>);
    }
  }
  flush();
  return <>{blocks}</>;
}

/**
 * Assistente IAZELA: perguntas sobre os dados da empresa (validades, reposição, vendas, clientes, IVA,
 * documentos), respondidas com a Claude API no servidor. As alterações (preços, imagens, descritivos, publicação na
 * loja online) chegam como propostas e só são gravadas com «Confirmar», pelo mesmo pedido do ecrã correspondente.
 * Pode anexar uma fotografia (câmara ou ficheiro), reduzida no navegador. A conversa vive nesta janela.
 */
export default function Assistant({ store }: { store: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<{ available: boolean; message: string } | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [attachment, setAttachment] = useState<Attachment | null>(null);

  async function attach(file: File | undefined) {
    if (!file) return;
    setError("");
    try {
      const blob = await shrink(file);
      if (attachment) URL.revokeObjectURL(attachment.url);
      setAttachment({ blob, url: URL.createObjectURL(blob) });
      input.current?.focus();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function reset() {
    turns.forEach(t => t.image && URL.revokeObjectURL(t.image.url));
    if (attachment) URL.revokeObjectURL(attachment.url);
    setTurns([]); setAttachment(null); setError("");
  }

  useEffect(() => {
    if (!open || state) return;
    api("/assistente/estado").then(setState).catch(e => setState({ available: false, message: (e as Error).message }));
  }, [open, state]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [turns, busy]);

  async function send(question: string) {
    const content = question.trim();
    if (!content || busy) return;
    if (content === PHOTO_SUGGESTION) {
      setText("Cria este artigo para o site");
      picker.current?.click();
      return;
    }
    if (content.includes("…")) {
      setText(content);
      requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(content.indexOf("…"), content.indexOf("…") + 1); });
      return;
    }
    const image = attachment;
    const history: Turn[] = [...turns, { role: "user", content, ...(image ? { image } : {}) }];
    setTurns(history); setText(""); setError(""); setBusy(true); setAttachment(null);
    try {
      const result = await api("/assistente/mensagens", {
        messages: history.map((t, i) => ({ role: t.role, content: t.role === "assistant" ? withOutcome(t)
          : t.image && i < history.length - 1 ? `${t.content}\n[Fotografia anexada a esta mensagem.]` : t.content })),
        store_id: store || "",
        ...(image ? { image: await base64(image.blob) } : {}),
      }, { timeoutMs: 240_000 });
      const actions: Action[] = result.actions || [];
      setTurns([...history, { role: "assistant", content: result.reply, tools: result.tools, actions, states: actions.map(() => ({ status: "pending" })), charts: result.charts || [] }]);
    } catch (e) {
      setError((e as Error).message);
      setTurns(turns);
      setText(content);
      setAttachment(image);
    } finally {
      setBusy(false);
    }
  }

  return <>
    <ActionButton type="button" className="assistant-trigger" aria-label="Assistente IAZELA" title="Perguntar ao assistente" onClick={() => setOpen(true)}>
      <Sparkles size={18} aria-hidden="true" />
    </ActionButton>
    {open && <AppDialog className="assistant-dialog" aria-labelledby="assistant-title" busy={busy} onRequestClose={() => setOpen(false)}>
      <div className="assistant-head">
        <h2 id="assistant-title"><Sparkles size={20} aria-hidden="true" />Assistente IAZELA</h2>
        {turns.length > 0 && <ActionButton type="button" disabled={busy} onClick={reset}>Nova conversa</ActionButton>}
      </div>
      <p className="assistant-privacy">Responde a partir dos dados da empresa a que tem acesso. Pode propor alterações (preços, imagens, descritivos, publicação no site), que só são gravadas depois de carregar em «Confirmar». A pergunta e os dados consultados são enviados à Anthropic (Claude) para gerar a resposta.</p>
      {state && !state.available && <Notice tone="warning">{state.message}</Notice>}
      <div className="assistant-log" aria-live="polite">
        {!turns.length && <div className="assistant-suggestions" role="group" aria-label="Perguntas sugeridas">
          {SUGGESTIONS.map(s => <button type="button" key={s} disabled={busy || !state?.available} onClick={() => void send(s)}>{s}</button>)}
        </div>}
        {turns.map((turn, i) => <div key={i} className={`assistant-turn is-${turn.role}`}>
          {turn.image && <img className="assistant-photo" src={turn.image.url} alt="Fotografia anexada" />}
          {turn.role === "user" ? <p>{turn.content}</p> : <Reply text={turn.content} />}
          {turn.charts?.map((chart, j) => <ChartView key={`g${j}`} chart={chart} />)}
          {turn.actions?.map((action, j) => <ActionCard key={j} action={action} state={turn.states?.[j] || { status: "pending" }} attachment={turns[i - 1]?.image?.blob}
            onChange={next => setTurns(all => all.map((t, k) => k !== i ? t : { ...t, states: (t.states || []).map((s, m) => m === j ? next : s) }))} />)}
          {!!turn.tools?.length && <small className="assistant-sources">Consultou: {[...new Set(turn.tools.map(t => TOOL_LABELS[t.name] || t.name))].join(" · ")}</small>}
        </div>)}
        {busy && <p className="assistant-turn is-assistant assistant-thinking" role="status">A consultar os dados…</p>}
        <div ref={end} />
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {attachment && <div className="assistant-attachment">
        <img src={attachment.url} alt="Fotografia a enviar" />
        <span>Fotografia anexada</span>
        <ActionButton type="button" variant="quiet" aria-label="Retirar fotografia" onClick={() => { URL.revokeObjectURL(attachment.url); setAttachment(null); }}><X size={16} aria-hidden="true" /></ActionButton>
      </div>}
      <form className="assistant-form" onSubmit={e => { e.preventDefault(); void send(text); }}>
        <input ref={picker} type="file" accept="image/*" hidden aria-label="Fotografia do produto"
          onChange={e => { void attach(e.target.files?.[0]); e.target.value = ""; }} />
        <ActionButton type="button" className="assistant-camera" aria-label="Anexar fotografia" title="Anexar fotografia (câmara ou ficheiro)"
          disabled={busy || !state?.available} onClick={() => picker.current?.click()}><Camera size={18} aria-hidden="true" /></ActionButton>
        <textarea ref={input} aria-label="Pergunta ao assistente" rows={2} maxLength={2000} value={text} disabled={!state?.available}
          placeholder="Escreva a pergunta (por exemplo: artigos mais vendidos em outubro)"
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } }} />
        <ActionButton type="submit" variant="primary" aria-label="Enviar pergunta" disabled={busy || !text.trim() || !state?.available}><Send size={18} aria-hidden="true" /></ActionButton>
      </form>
    </AppDialog>}
  </>;
}
