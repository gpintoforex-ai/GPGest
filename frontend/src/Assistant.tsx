import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { Check, Send, Sparkles, Tag, X } from "lucide-react";
import { ActionButton, AppDialog, Notice } from "./components/InteractionUI";
import { api } from "./lib/api";
import "./Assistant.css";

/** Ação proposta pelo assistente: o servidor valida-a e devolve o pedido que a ficha do artigo faria. */
type Action = {
  type: "price";
  article_id: string;
  code: string;
  name: string;
  origin: "erp" | "local";
  vat_rate: string;
  before_gross: string | null;
  after_gross: string;
  channels: { connection: string; own_price: boolean }[];
  request: { path: string; body: unknown };
};
type ActionState = { status: "pending" | "busy" | "done" | "cancelled" | "failed"; message?: string };
type Turn = { role: "user" | "assistant"; content: string; tools?: { name: string }[]; actions?: Action[]; states?: ActionState[] };

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
  "Altera o preço do artigo … para … € (com IVA).",
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
};

const euros = (value: string | null) => value === null ? "sem preço"
  : new Intl.NumberFormat("pt-PT", { style: "currency", currency: "EUR" }).format(Number(value));

/** Estado das ações já decididas, acrescentado à resposta no histórico: o modelo sabe o que foi gravado. */
function withOutcome(turn: Turn) {
  if (!turn.actions?.length) return turn.content;
  const lines = turn.actions.map((a, i) => {
    const s = turn.states?.[i]?.status;
    const what = `preço de ${a.name} (${a.code}) para ${euros(a.after_gross)}`;
    return s === "done" ? `- Confirmado pelo utilizador e gravado: ${what}.`
      : s === "cancelled" ? `- Cancelado pelo utilizador: ${what}.`
      : s === "failed" ? `- Falhou ao gravar: ${what} (${turn.states?.[i]?.message || "erro"}).`
      : `- Ainda por confirmar: ${what}.`;
  });
  return `${turn.content}\n\n[Estado das propostas]\n${lines.join("\n")}`;
}

/** Cartão de confirmação: nada é gravado até carregar em «Confirmar»; usa o mesmo pedido da ficha do artigo. */
function ActionCard({ action, state, onChange }: { action: Action; state: ActionState; onChange: (s: ActionState) => void }) {
  async function confirm() {
    onChange({ status: "busy" });
    try {
      const result = await api(action.request.path, action.request.body, { timeoutMs: 60_000 });
      if (action.origin === "erp" && result.state !== "confirmed")
        onChange({ status: "failed", message: result.state === "uncertain"
          ? "A Wintouch não confirmou a alteração. Verifique o preço na ficha do artigo antes de repetir."
          : result.error || "A Wintouch recusou o preço." });
      else onChange({ status: "done" });
    } catch (e) {
      onChange({ status: "failed", message: (e as Error).message });
    }
  }
  const published = action.channels.filter(c => !c.own_price).map(c => c.connection);
  return <div className={`assistant-action is-${state.status}`} role="group" aria-label={`Alterar preço de ${action.name}`}>
    <p className="assistant-action-title"><Tag size={16} aria-hidden="true" />Alterar preço de venda</p>
    <p><strong>{action.name}</strong> <span className="assistant-action-code">{action.code}</span></p>
    <p className="assistant-action-prices">
      <span>{euros(action.before_gross)}</span> → <strong>{euros(action.after_gross)}</strong> <small>com IVA ({Number(action.vat_rate).toLocaleString("pt-PT")} %)</small>
    </p>
    <small className="assistant-action-note">{action.origin === "erp"
      ? `Enviado à Wintouch e só gravado depois de confirmado por leitura.${published.length ? ` A loja online (${published.join(", ")}) recebe o preço na sincronização seguinte.` : ""}`
      : "Artigo local: o preço sem IVA é gravado na ficha do artigo."}</small>
    {state.status === "pending" || state.status === "busy" ? <div className="assistant-action-buttons">
      <ActionButton type="button" variant="primary" disabled={state.status === "busy"} onClick={() => void confirm()}>
        <Check size={16} aria-hidden="true" />{state.status === "busy" ? "A gravar…" : "Confirmar"}
      </ActionButton>
      <ActionButton type="button" variant="quiet" disabled={state.status === "busy"} onClick={() => onChange({ status: "cancelled" })}>
        <X size={16} aria-hidden="true" />Cancelar
      </ActionButton>
    </div>
      : state.status === "done" ? <Notice tone="success">Preço gravado.</Notice>
      : state.status === "cancelled" ? <p className="assistant-action-note">Cancelado: nada foi alterado.</p>
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
 * documentos), respondidas com a Claude API no servidor. As alterações (preços) chegam como propostas e só são
 * gravadas com «Confirmar», pelo mesmo pedido da ficha do artigo. A conversa vive nesta janela.
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

  useEffect(() => {
    if (!open || state) return;
    api("/assistente/estado").then(setState).catch(e => setState({ available: false, message: (e as Error).message }));
  }, [open, state]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [turns, busy]);

  async function send(question: string) {
    const content = question.trim();
    if (!content || busy) return;
    if (content.includes("…")) {
      setText(content);
      requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(content.indexOf("…"), content.indexOf("…") + 1); });
      return;
    }
    const history: Turn[] = [...turns, { role: "user", content }];
    setTurns(history); setText(""); setError(""); setBusy(true);
    try {
      const result = await api("/assistente/mensagens", {
        messages: history.map(t => ({ role: t.role, content: t.role === "assistant" ? withOutcome(t) : t.content })),
        store_id: store || "",
      }, { timeoutMs: 180_000 });
      const actions: Action[] = result.actions || [];
      setTurns([...history, { role: "assistant", content: result.reply, tools: result.tools, actions, states: actions.map(() => ({ status: "pending" })) }]);
    } catch (e) {
      setError((e as Error).message);
      setTurns(turns);
      setText(content);
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
        {turns.length > 0 && <ActionButton type="button" disabled={busy} onClick={() => { setTurns([]); setError(""); }}>Nova conversa</ActionButton>}
      </div>
      <p className="assistant-privacy">Responde a partir dos dados da empresa a que tem acesso. Pode propor alterações de preço, que só são gravadas depois de carregar em «Confirmar». A pergunta e os dados consultados são enviados à Anthropic (Claude) para gerar a resposta.</p>
      {state && !state.available && <Notice tone="warning">{state.message}</Notice>}
      <div className="assistant-log" aria-live="polite">
        {!turns.length && <div className="assistant-suggestions" role="group" aria-label="Perguntas sugeridas">
          {SUGGESTIONS.map(s => <button type="button" key={s} disabled={busy || !state?.available} onClick={() => void send(s)}>{s}</button>)}
        </div>}
        {turns.map((turn, i) => <div key={i} className={`assistant-turn is-${turn.role}`}>
          {turn.role === "user" ? <p>{turn.content}</p> : <Reply text={turn.content} />}
          {turn.actions?.map((action, j) => <ActionCard key={j} action={action} state={turn.states?.[j] || { status: "pending" }}
            onChange={next => setTurns(all => all.map((t, k) => k !== i ? t : { ...t, states: (t.states || []).map((s, m) => m === j ? next : s) }))} />)}
          {!!turn.tools?.length && <small className="assistant-sources">Consultou: {[...new Set(turn.tools.map(t => TOOL_LABELS[t.name] || t.name))].join(" · ")}</small>}
        </div>)}
        {busy && <p className="assistant-turn is-assistant assistant-thinking" role="status">A consultar os dados…</p>}
        <div ref={end} />
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <form className="assistant-form" onSubmit={e => { e.preventDefault(); void send(text); }}>
        <textarea ref={input} aria-label="Pergunta ao assistente" rows={2} maxLength={2000} value={text} disabled={!state?.available}
          placeholder="Escreva a pergunta (por exemplo: artigos mais vendidos em outubro)"
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } }} />
        <ActionButton type="submit" variant="primary" aria-label="Enviar pergunta" disabled={busy || !text.trim() || !state?.available}><Send size={18} aria-hidden="true" /></ActionButton>
      </form>
    </AppDialog>}
  </>;
}
